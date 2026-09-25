"""Вход, обновление, выход, кто я."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from database.models import Role, UserAccount, UserStatus

from ..deps import SessionDep, UserDep, permissions_of
from ..errors import AccountBlocked, InvalidCredentials
from ..schemas.auth import LoginRequest, LoginResponse, RefreshRequest, TokenPair
from ..schemas.common import Permission, User, WarehouseBrief
from ..security import (
    consume_refresh,
    hash_password,
    issue_access,
    issue_refresh,
    needs_rehash,
    refresh_owner_id,
    revoke_chain,
    verify_password,
)

router = APIRouter(tags=["Вход"])

#: Сверяем пароль даже когда логина нет — иначе ответ приходит заметно быстрее,
#: и по времени отклика перебираются существующие учётные записи.
_DUMMY_HASH = hash_password("нет такого пользователя")


def _session_payload(session, user: UserAccount, *, access: str | None = None,
                     refresh: str | None = None) -> LoginResponse:
    roles = {r.id: r.code for r in session.scalars(select(Role))}
    return LoginResponse(
        access=access, refresh=refresh,
        user=User.of(user),
        warehouse=WarehouseBrief.of(user.warehouse),
        permissions=[Permission(role=roles.get(p.role_id, ""), section=p.section,
                                can_view=p.can_view, can_edit=p.can_edit)
                     for p in permissions_of(session, user.role_id)],
    )


@router.post("/auth/login", response_model=LoginResponse,
             summary="Вход по логину и паролю")
def login(body: LoginRequest, session: SessionDep) -> LoginResponse:
    user = session.scalar(
        select(UserAccount).where(func.lower(UserAccount.login) == body.login.lower(),
                                  UserAccount.deleted_at.is_(None)))
    stored = user.password_hash if user else _DUMMY_HASH
    if not verify_password(body.password, stored) or user is None:
        raise InvalidCredentials()
    if user.status == UserStatus.BLOCKED:
        raise AccountBlocked()

    if needs_rehash(user.password_hash):
        # Параметры argon2 ужесточились — пересчитываем, пока пароль в руках.
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(timezone.utc)
    refresh = issue_refresh(session, user)
    # Чтение и запись идут в одной неявной транзакции — её и фиксируем. Явный
    # session.begin() здесь нельзя: SELECT выше её уже открыл.
    session.commit()
    return _session_payload(session, user, access=issue_access(user),
                            refresh=refresh)


@router.post("/auth/refresh", response_model=TokenPair,
             summary="Обновить пару токенов")
def refresh_tokens(body: RefreshRequest, session: SessionDep) -> TokenPair:
    user = consume_refresh(session, body.refresh)
    if user.status == UserStatus.BLOCKED:
        revoke_chain(session, user.id)
        session.commit()
        raise AccountBlocked()
    issued = issue_refresh(session, user)
    session.commit()
    return TokenPair(access=issue_access(user), refresh=issued)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT,
             summary="Выход")
def logout(body: RefreshRequest, session: SessionDep) -> Response:
    """Выход по refresh, без access-токена.

    Намеренно: приложение простояло открытым ночь, access истёк, человек жмёт
    «Выйти» — и выход должен сработать, а не упасть в 401. Предъявленный refresh
    сам подтверждает, кто выходит.

    Гасим всю цепочку, а не один токен: рядом мог остаться ротированный.
    """
    owner_id = refresh_owner_id(session, body.refresh)
    if owner_id is not None:
        revoke_chain(session, owner_id)
        session.commit()
    # Неизвестный токен — тоже 204: «вышел» и так верно, а разное поведение
    # подсказывало бы, какой токен существует.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/me", response_model=LoginResponse,
            summary="Текущий пользователь, склад и права")
def me(session: SessionDep, user: UserDep) -> LoginResponse:
    # Токены не перевыпускаем: они у клиента уже есть, и в схеме необязательны.
    return _session_payload(session, user)
