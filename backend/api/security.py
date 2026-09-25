"""Пароли и токены.

Access — JWT на 15 минут, внутри `sub` и склад. Refresh — 32 случайных байта,
в базе только его SHA-256: утечка таблицы не даёт войти. Ротация при каждом
обновлении, предъявление отозванного обрушивает всю цепочку пользователя.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from database.models import RefreshToken, UserAccount

from .errors import SessionExpired, Unauthorized
from .settings import settings

_hasher = PasswordHasher()


# ── пароли ────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    """Параметры argon2 со временем ужесточаются — пересчитываем при входе."""
    try:
        return _hasher.check_needs_rehash(stored)
    except InvalidHashError:
        return False


# ── access ────────────────────────────────────────────────────
def issue_access(user: UserAccount) -> str:
    cfg = settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        # Склад в токене, чтобы фильтр видимости не требовал лишнего запроса.
        # Права здесь не носим: их меняют на ходу, и токен бы отставал.
        "wh": user.warehouse_id,
        "iat": now,
        "exp": now + timedelta(minutes=cfg.access_ttl_minutes),
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def read_access(token: str) -> dict:
    cfg = settings()
    try:
        return jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        # Ровно этот 401 клиент ловит, чтобы молча сходить за refresh.
        raise SessionExpired("Срок действия токена истёк") from None
    except jwt.InvalidTokenError:
        raise Unauthorized("Токен не разобран") from None


# ── refresh ───────────────────────────────────────────────────
def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_refresh(session: Session, user: UserAccount) -> str:
    token = secrets.token_urlsafe(32)
    session.add(RefreshToken(
        user_id=user.id,
        token_hash=_digest(token),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings().refresh_ttl_days),
    ))
    return token


def refresh_owner_id(session: Session, token: str) -> int | None:
    """Чей это refresh, не погашая его. Нужен выходу."""
    return session.scalar(
        select(RefreshToken.user_id)
        .where(RefreshToken.token_hash == _digest(token)))


def revoke_chain(session: Session, user_id: int) -> None:
    """Отозвать все живые refresh пользователя."""
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id,
               RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc)))


def consume_refresh(session: Session, token: str) -> UserAccount:
    """Проверить refresh и погасить его. Возвращает владельца.

    Предъявление уже отозванного — признак кражи: законный владелец получил бы
    ротированный. Поэтому рубим всю цепочку, а не только предъявленный.

    Отзывы фиксируются здесь же, до `raise`. Иначе исключение уносит транзакцию
    в откат вместе с отзывом, и следующий токен цепочки остаётся живым — то есть
    защита от кражи не срабатывает ровно в тот момент, когда нужна.
    """
    row = session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _digest(token)))
    if row is None:
        raise SessionExpired("Токен не найден")
    now = datetime.now(timezone.utc)
    if row.revoked_at is not None:
        revoke_chain(session, row.user_id)
        session.commit()
        raise SessionExpired("Токен уже был использован, сессии сброшены")
    if row.expires_at <= now:
        row.revoked_at = now
        session.commit()
        raise SessionExpired("Срок действия истёк")

    row.revoked_at = now
    user = session.get(UserAccount, row.user_id)
    if user is None or user.deleted_at is not None:
        session.commit()      # погашение предъявленного оставляем в силе
        raise SessionExpired("Учётная запись недоступна")
    return user
