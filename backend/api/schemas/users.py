"""Пользователи и права. Имена классов и полей — components.schemas."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import Field, model_validator

from .common import Schema

_EMAIL_PATTERN = r"[^@\s]+@[^@\s]+\.[^@\s]+"
_EMAIL = Field(max_length=254, pattern=_EMAIL_PATTERN)


class UserCreate(Schema):
    full_name: str = Field(min_length=1, max_length=200)
    login: str = Field(min_length=1, max_length=100)
    email: str = _EMAIL
    password: str = Field(min_length=6, max_length=200)
    role: str = Field(min_length=1, max_length=50)
    warehouse_id: int
    position: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    hire_date: date | None = None


class UserUpdate(Schema):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=254, pattern=_EMAIL_PATTERN)
    role: str | None = Field(default=None, max_length=50)
    warehouse_id: int | None = None
    position: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    status: str | None = None          # принимается, но отвергается сервисом


class PasswordBody(Schema):
    password: str = Field(min_length=6, max_length=200)


class AuditEntry(Schema):
    id: int
    entity: str
    entity_id: int
    action: str
    created_at: datetime

    @classmethod
    def of(cls, entry) -> "AuditEntry":
        return cls(id=entry.id, entity=entry.entity,
                   entity_id=entry.entity_id, action=entry.action,
                   created_at=entry.created_at)


class PermissionUpdate(Schema):
    """Строка матрицы на входе PUT /permissions. Валидация клетки здесь,
    чтобы отказ приходил как 422 до всякой базы."""

    role: str
    section: str
    can_view: bool
    can_edit: bool

    @model_validator(mode="after")
    def _edit_needs_view(self):
        if self.can_edit and not self.can_view:
            raise ValueError("can_edit без can_view невозможен")
        return self
