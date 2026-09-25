"""Схемы, общие для разделов. Имена полей — как в docs/openapi.json."""
from __future__ import annotations

from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(Schema, Generic[T]):
    """Списочный ответ. Ровно эти четыре поля читает клиент."""
    items: list[T]
    total: int
    limit: int
    offset: int


class UserBrief(Schema):
    """Подпись под документом: «Кузнецов И.А.»"""
    id: int
    name: str

    @classmethod
    def of(cls, user) -> UserBrief | None:
        if user is None:
            return None
        return cls(id=user.id, name=short_name(user.full_name))


class Person(Schema):
    """Сотрудник для выпадающего фильтра: имя целиком и склад."""
    id: int
    name: str
    warehouse_id: int | None


class WarehouseBrief(Schema):
    id: int
    code: str
    name: str
    owner: str | None
    responsible: UserBrief | None

    @classmethod
    def of(cls, warehouse) -> WarehouseBrief | None:
        if warehouse is None:
            return None
        return cls(id=warehouse.id, code=warehouse.code, name=warehouse.name,
                   owner=warehouse.owner,
                   responsible=UserBrief.of(warehouse.responsible))


class User(Schema):
    id: int
    full_name: str
    login: str
    email: str
    role: str
    warehouse: WarehouseBrief | None
    position: str | None
    phone: str | None
    status: str
    hire_date: date | None
    last_login_at: datetime | None

    @classmethod
    def of(cls, user) -> User:
        return cls(id=user.id, full_name=user.full_name, login=user.login,
                   email=user.email, role=user.role.code,
                   warehouse=WarehouseBrief.of(user.warehouse),
                   position=user.position, phone=user.phone,
                   status=user.status, hire_date=user.hire_date,
                   last_login_at=user.last_login_at)


class Permission(Schema):
    role: str
    section: str
    can_view: bool
    can_edit: bool


def short_name(full_name: str | None) -> str:
    """«Кузнецов Игорь Александрович» → «Кузнецов И.А.»

    Сокращает сервер, а не клиент: подпись под документом должна выглядеть
    одинаково в приложении, в выгрузке и в письме.
    """
    parts = (full_name or "").strip().split()
    if not parts:
        return "—"
    tail = "".join(f"{p[0]}." for p in parts[1:3] if p)
    return f"{parts[0]} {tail}".strip()
