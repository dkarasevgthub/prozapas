"""Зависимости: кто пришёл, что ему можно и какая версия документа у него в руках.

Здесь только HTTP-механика: разбор заголовков и проверка прав.
Бизнес-правила — в services/.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Role, RolePermission, Section, UserAccount, UserStatus

from .db import get_session
from .errors import (AccountBlocked, BadRequest, Forbidden,
                     PreconditionRequired, Unauthorized)
from .security import read_access

SessionDep = Annotated[Session, Depends(get_session)]


# --- Кто пришёл ---------------------------------------------------------------

def current_user(request: Request, session: SessionDep) -> UserAccount:
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("Нет заголовка Authorization: Bearer")

    claims = read_access(token)
    user = session.get(UserAccount, int(claims["sub"]))
    if user is None or user.deleted_at is not None:
        raise Unauthorized("Учётная запись недоступна")
    if user.status == UserStatus.BLOCKED:
        # Блокировка действует сразу, не дожидаясь истечения access-токена.
        raise AccountBlocked()
    return user


UserDep = Annotated[UserAccount, Depends(current_user)]


def permissions_of(session: Session, role_id: int) -> list[RolePermission]:
    return list(session.scalars(
        select(RolePermission).where(RolePermission.role_id == role_id)))


def role_code(session: Session, role_id: int) -> str:
    role = session.get(Role, role_id)
    return role.code if role else ""


def require(section: Section, *, edit: bool = False):
    """Проверка прав по матрице роли.

    Права не в токене: администратор снимает галочку, и это должно действовать
    на следующем запросе, а не через пятнадцать минут.
    """
    def guard(user: UserDep, session: SessionDep) -> UserAccount:
        row = session.get(RolePermission, (user.role_id, section.value))
        allowed = row is not None and (row.can_edit if edit else row.can_view)
        if not allowed:
            raise Forbidden(
                f"Роль не имеет доступа к разделу «{section.value}»"
                + (" на изменение" if edit else ""))
        return user
    return Depends(guard)


# --- Версия документа: ETag и If-Match ----------------------------------------

def etag(version: int) -> str:
    """Заголовок ETag для ответа: версия в кавычках, как в api.md §3.4."""
    return f'"{version}"'


def parse_if_match(header: str | None) -> int:
    """If-Match «"7"» → 7. Кавычки снимаются здесь, а не в каждом роутере.

    Заголовка нет → 428 (это контракт, а не ошибка разбора).
    Есть, но не номер → 400. Устарел → 409 — это решает сервис,
    сравнив с версией из базы.
    """
    if header is None or not header.strip():
        raise PreconditionRequired()
    raw = header.strip()
    if raw.startswith("W/"):                    # слабый сравнитель допускаем
        raw = raw[2:].strip()
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    if not raw.isdigit():
        raise BadRequest("If-Match должен быть номером версии из ETag")
    return int(raw)


def if_match(header: Annotated[str | None,
                                Header(alias="If-Match")] = None) -> int:
    return parse_if_match(header)


#: В изменяющих операциях: ``def accept(order_id: int, version: IfMatch, ...)``
IfMatch = Annotated[int, Depends(if_match)]


# --- Пагинация -----------------------------------------------------------------

def pagination(limit: Annotated[int, Query(ge=1, le=200)] = 50,
               offset: Annotated[int, Query(ge=0)] = 0) -> tuple[int, int]:
    """Умолчания из api.md §3.2: limit 50, максимум 200."""
    return limit, offset


#: В списках: ``limit, offset = page``.
PageParams = Annotated[tuple[int, int], Depends(pagination)]