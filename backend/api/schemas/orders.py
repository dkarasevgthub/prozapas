"""Заказы. Имена классов и полей — components.schemas openapi.json."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import Schema, UserBrief, WarehouseBrief


class OrderPositionIn(Schema):
    article: str = Field(min_length=1, max_length=100)
    qty: float = Field(gt=0)


class OrderCreate(Schema):
    from_warehouse_id: int
    comment: str | None = None
    positions: list[OrderPositionIn] = Field(min_length=1)


class ReasonBody(Schema):
    reason: str | None = None


class OrderPosition(Schema):
    article: str
    name: str
    unit: str
    qty: float

    @classmethod
    def of(cls, position) -> "OrderPosition":
        return cls(article=position.item.article, name=position.item.name,
                   unit=position.item.unit, qty=float(position.qty))


class OrderListItem(Schema):
    id: int
    number: str
    status: str
    counterparty: WarehouseBrief
    positions_count: int
    responsible: UserBrief | None
    created_at: datetime
    shipped_at: datetime | None
    accepted_at: datetime | None

    @classmethod
    def of(cls, order, counterparty, positions_count: int) -> "OrderListItem":
        return cls(id=order.id, number=order.number, status=order.status,
                   counterparty=WarehouseBrief.of(counterparty),
                   positions_count=positions_count,
                   responsible=UserBrief.of(order.responsible),
                   created_at=order.created_at, shipped_at=order.shipped_at,
                   accepted_at=order.accepted_at)


class Order(Schema):
    id: int
    number: str
    status: str
    from_warehouse: WarehouseBrief
    to_warehouse: WarehouseBrief
    responsible: UserBrief | None
    comment: str | None
    reason: str | None
    positions: list[OrderPosition]
    created_at: datetime
    shipped_at: datetime | None
    accepted_at: datetime | None
    version: int

    @classmethod
    def of(cls, order) -> "Order":
        return cls(id=order.id, number=order.number, status=order.status,
                   from_warehouse=WarehouseBrief.of(order.from_warehouse),
                   to_warehouse=WarehouseBrief.of(order.to_warehouse),
                   responsible=UserBrief.of(order.responsible),
                   comment=order.comment, reason=order.reason,
                   positions=[OrderPosition.of(p) for p in order.positions],
                   created_at=order.created_at, shipped_at=order.shipped_at,
                   accepted_at=order.accepted_at, version=order.version)
