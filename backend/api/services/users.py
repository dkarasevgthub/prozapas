"""Пользователи и права: CRUD, блокировка, пароли, матрица.

Раздел закрыт require(Section.USERS) на маршрутах. Правила, которым нужен
сам объект, живут здесь: нельзя заблокировать или удалить себя, изменить
свою роль, заблокировать или удалить последнего администратора.

Каждое действие пишет строку в audit_log той же транзакцией: /activity
без писателя возвращал бы вечную пустоту.
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database.models import (AuditLog, Role, RolePermission, RefreshToken,
                             Section, UserAccount, UserStatus, Warehouse)

from ..errors import Conflict, NotFound, Unprocessable
from ..security import hash_password
from . import audit


# --- Список и карточка ---------------------------------------------------------

def list_users(session: Session, *, q: str | None, role: str | None,
               status: str | None, warehouse_id: int | None,
               limit: int, offset: int) -> tuple[list[UserAccount], int]:
    where = [UserAccount.deleted_at.is_(None)]
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append(or_(UserAccount.full_name.ilike(like),
                         UserAccount.login.ilike(like)))
    if role:
        where.append(Role.code == role)
    if status:
        where.append(UserAccount.status == status)
    if warehouse_id is not None:
        where.append(UserAccount.warehouse_id == warehouse_id)

    base = (select(UserAccount)
            .join(Role, Role.id == UserAccount.role_id)
            .where(*where))
    total = session.scalar(
        select(func.count()).select_from(base.subquery())) or 0
    rows = list(session.scalars(
        base.order_by(UserAccount.full_name, UserAccount.id)
        .limit(limit).offset(offset)))
    return rows, total


def get_user(session: Session, user_id: int) -> UserAccount:
    user = session.get(UserAccount, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("Пользователь не найден")
    return user


# --- Правила -------------------------------------------------------------------

def _login_taken(session: Session, login: str,
                 exclude_id: int | None = None) -> bool:
    stmt = select(UserAccount.id).where(
        func.lower(UserAccount.login) == login.lower(),
        UserAccount.deleted_at.is_(None))
    if exclude_id is not None:
        stmt = stmt.where(UserAccount.id != exclude_id)
    return session.scalar(stmt) is not None


def _email_taken(session: Session, email: str,
                 exclude_id: int | None = None) -> bool:
    stmt = select(UserAccount.id).where(
        func.lower(UserAccount.email) == email.lower(),
        UserAccount.deleted_at.is_(None))
    if exclude_id is not None:
        stmt = stmt.where(UserAccount.id != exclude_id)
    return session.scalar(stmt) is not None


def _guard_last_admin(session: Session, user: UserAccount, verb: str) -> None:
    if user.role.code != "admin":
        return
    others = session.scalar(
        select(func.count()).select_from(UserAccount)
        .join(Role, Role.id == UserAccount.role_id)
        .where(Role.code == "admin",
               UserAccount.id != user.id,
               UserAccount.deleted_at.is_(None),
               UserAccount.status == UserStatus.ACTIVE)) or 0
    if others == 0:
        raise Conflict(f"Последнего администратора нельзя {verb}")


def _revoke_sessions(session: Session, user_id: int) -> None:
    """Гасит всю цепочку refresh. Access умирает сам: current_user проверяет
    статус при каждом запросе, не дожидаясь истечения токена."""
    session.execute(sa.update(RefreshToken)
                    .where(RefreshToken.user_id == user_id,
                           RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=datetime.now(timezone.utc)))


# --- Запись ---------------------------------------------------------------------

def create(session: Session, actor: UserAccount, *, full_name: str,
           login: str, email: str, password: str, role_code: str,
           warehouse_id: int, position: str | None, phone: str | None,
           hire_date) -> UserAccount:
    if _login_taken(session, login):
        raise Conflict("Логин уже занят другим сотрудником", title="Логин занят")
    if _email_taken(session, email):
        raise Conflict("Почта уже занята другим сотрудником", title="Почта занята")
    role = session.scalar(select(Role).where(Role.code == role_code))
    if role is None:
        raise Unprocessable(f"Роль «{role_code}» не существует")
    wh = session.get(Warehouse, warehouse_id)
    if wh is None or wh.deleted_at is not None:
        raise NotFound("Склад не найден")

    user = UserAccount(full_name=full_name, login=login, email=email,
                       password_hash=hash_password(password),
                       role_id=role.id, warehouse_id=warehouse_id,
                       position=position, phone=phone,
                       status=UserStatus.ACTIVE, hire_date=hire_date)
    session.add(user)
    session.flush()                     # id — для записи аудита
    audit.record(session, entity="user_account", entity_id=user.id,
                 action="created", user_id=actor.id,
                 after={"login": login, "role": role_code})
    session.commit()
    return user


def update(session: Session, actor: UserAccount, user_id: int,
           changes: dict) -> UserAccount:
    user = get_user(session, user_id)
    if changes.get("status") is not None and changes["status"] != user.status:
        raise Unprocessable("Статус меняется через block/unblock")
    for field in ("full_name", "email"):
        if field in changes and not changes[field]:
            raise Unprocessable(f"Поле «{field}» не может быть пустым")

    new_email = changes.get("email")
    if new_email and new_email.lower() != user.email.lower() \
            and _email_taken(session, new_email, exclude_id=user.id):
        raise Conflict("Почта уже занята другим сотрудником", title="Почта занята")

    new_role = changes.get("role")
    if new_role and new_role != user.role.code:
        if user.id == actor.id:
            raise Conflict("Нельзя изменить собственную роль")
        role = session.scalar(select(Role).where(Role.code == new_role))
        if role is None:
            raise Unprocessable(f"Роль «{new_role}» не существует")
        user.role_id = role.id

    if changes.get("warehouse_id") is not None \
            and changes["warehouse_id"] != user.warehouse_id:
        wh = session.get(Warehouse, changes["warehouse_id"])
        if wh is None or wh.deleted_at is not None:
            raise NotFound("Склад не найден")
        user.warehouse_id = changes["warehouse_id"]

    for field in ("full_name", "position", "phone"):
        if field in changes:
            setattr(user, field, changes[field])
    if new_email:
        user.email = new_email

    audit.record(session, entity="user_account", entity_id=user.id,
                 action="updated", user_id=actor.id,
                 after={k: v for k, v in changes.items() if k != "status"})
    session.commit()
    # Joined role/warehouse were loaded before the foreign keys changed.
    session.refresh(user)
    return user


def block(session: Session, actor: UserAccount, user_id: int) -> None:
    user = get_user(session, user_id)
    if user.id == actor.id:
        raise Conflict("Нельзя заблокировать себя")
    if user.status == UserStatus.BLOCKED:
        return                          # повторная блокировка — идемпотентна
    _guard_last_admin(session, user, "заблокировать")
    user.status = UserStatus.BLOCKED
    _revoke_sessions(session, user.id)
    audit.record(session, entity="user_account", entity_id=user.id,
                 action="blocked", user_id=actor.id)
    session.commit()


def unblock(session: Session, actor: UserAccount, user_id: int) -> None:
    user = get_user(session, user_id)
    if user.status == UserStatus.ACTIVE:
        return                          # идемпотентно
    user.status = UserStatus.ACTIVE
    audit.record(session, entity="user_account", entity_id=user.id,
                 action="unblocked", user_id=actor.id)
    session.commit()


def set_password(session: Session, actor: UserAccount, user_id: int,
                 password: str) -> None:
    user = get_user(session, user_id)
    user.password_hash = hash_password(password)
    _revoke_sessions(session, user.id)  # сменили пароль — сессии погашены
    audit.record(session, entity="user_account", entity_id=user.id,
                 action="password-changed", user_id=actor.id)
    session.commit()


def delete(session: Session, actor: UserAccount, user_id: int) -> None:
    """Мягкое удаление. Подписи под заказами и движениями остаются целыми."""
    user = get_user(session, user_id)
    if user.id == actor.id:
        raise Conflict("Нельзя удалить себя")
    _guard_last_admin(session, user, "удалить")
    user.deleted_at = datetime.now(timezone.utc)
    _revoke_sessions(session, user.id)
    audit.record(session, entity="user_account", entity_id=user.id,
                 action="deleted", user_id=actor.id)
    session.commit()


# --- Журнал действий -------------------------------------------------------------

def activity(session: Session, user_id: int, limit: int,
             offset: int) -> tuple[list[AuditLog], int]:
    get_user(session, user_id)          # несуществующий — 404, а не пустая страница
    where = [AuditLog.user_id == user_id]
    total = session.scalar(
        select(func.count()).select_from(AuditLog).where(*where)) or 0
    rows = list(session.scalars(
        select(AuditLog).where(*where)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit).offset(offset)))
    return rows, total


# --- Матрица прав -----------------------------------------------------------------

def matrix(session: Session) -> list[tuple[RolePermission, str]]:
    return list(session.execute(
        select(RolePermission, Role.code)
        .join(Role, Role.id == RolePermission.role_id)
        .order_by(RolePermission.role_id, RolePermission.section)))


def matrix_put(session: Session, actor: UserAccount, cells) -> None:
    """Матрица уходит и приходит целиком (api-sections §8): частичное
    обновление потребовало бы ключа на каждую клетку. can_edit без can_view
    уже отвергнут схемой (422)."""
    if not cells:
        raise Unprocessable("Матрица не может быть пустой")
    seen: set[tuple[str, str]] = set()
    sections = {s.value for s in Section}
    for c in cells:
        pair = (c.role, c.section)
        if pair in seen:
            raise Unprocessable(f"Повторяется строка {c.role}/{c.section}")
        seen.add(pair)
        if c.section not in sections:
            raise Unprocessable(f"Неизвестный раздел «{c.section}»")
    roles = {r.code: r for r in session.scalars(select(Role))}
    unknown = sorted({c.role for c in cells if c.role not in roles})
    if unknown:
        raise Unprocessable(f"Неизвестная роль: {', '.join(unknown)}")
    # Страховка самоблокировки: без этой строки админ после PUT не откроет
    # даже экран прав, чтобы вернуть как было.
    admin_cell = next((c for c in cells if c.role == "admin"
                       and c.section == Section.USERS.value), None)
    if admin_cell is None or not admin_cell.can_edit:
        raise Unprocessable("Нельзя лишить администратора права менять пользователей")

    session.execute(sa.delete(RolePermission))
    session.add_all(RolePermission(role_id=roles[c.role].id, section=c.section,
                                   can_view=c.can_view, can_edit=c.can_edit)
                    for c in cells)
    audit.record(session, entity="role_permission", entity_id=actor.role_id,
                 action="matrix-replaced", user_id=actor.id,
                 after={"cells": [[c.role, c.section, c.can_view, c.can_edit]
                                  for c in cells]})
    session.commit()