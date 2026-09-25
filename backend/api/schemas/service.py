"""Служебные ответы: живость, версия, справочники, сводка."""
from __future__ import annotations

from datetime import datetime

from .common import Permission, Person, Schema, User, UserBrief, WarehouseBrief


class Health(Schema):
    status: str


class Version(Schema):
    version: str
    schema_revision: str | None


class Role(Schema):
    code: str
    label: str


class Bootstrap(Schema):
    """Один поход вместо шести при старте приложения."""
    warehouses: list[WarehouseBrief]
    roles: list[Role]
    sections: list[str]
    permissions: list[Permission]
    user: User
    warehouse: WarehouseBrief | None
    #: Сотрудники для фильтра «Ответственный». Здесь, а не в /users: тот открыт
    #: только администратору, а фильтр нужен всем.
    users: list[Person]
    catalog_version: str | None = None


class StatusEvent(Schema):
    order_id: int
    number: str
    status: str
    reason: str | None
    user: UserBrief | None
    occurred_at: datetime


class Dashboard(Schema):
    in_work: int
    outgoing_in_work: int
    incoming_in_work: int
    to_ship: int
    to_receive: int
    events: list[StatusEvent]
