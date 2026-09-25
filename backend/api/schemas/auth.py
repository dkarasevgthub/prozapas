"""Вход и токены."""
from __future__ import annotations

from pydantic import Field

from .common import Permission, Schema, User, WarehouseBrief


class LoginRequest(Schema):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(Schema):
    refresh: str = Field(min_length=1)


class TokenPair(Schema):
    access: str
    refresh: str


class LoginResponse(Schema):
    """Вход отдаёт всё, что нужно нарисовать окно: кто, где и что можно.

    Токены необязательны: `/auth/me` возвращает ту же форму, но выдавать при
    нём новую пару не за что — у клиента она уже есть.
    """
    access: str | None = None
    refresh: str | None = None
    user: User
    warehouse: WarehouseBrief | None
    permissions: list[Permission]
