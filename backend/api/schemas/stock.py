"""Остатки. Имена классов и полей — components.schemas openapi.json."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import Schema, UserBrief, WarehouseBrief


class StockShare(Schema):
    """Строка разбивки по складу. Название склада клиент берёт из /bootstrap."""
    warehouse_id: int
    qty: float
    free: float


class StockRow(Schema):
    item_id: int
    article: str
    code1c: str | None
    name: str
    unit: str
    qty: float
    free: float
    reserved: float
    min_qty: float
    by_warehouse: list[StockShare]


class StockByWarehouse(Schema):
    """Ответ GET /stock/{item_id}: карточка товара, свой склад первым."""
    warehouse: WarehouseBrief
    qty: float
    free: float
    reserved: float
    min_qty: float


class StockSummary(Schema):
    positions: int
    below_min: int
    reserved: float
    free: float


class Movement(Schema):
    id: int
    type: str
    delta: float
    balance_after: float
    doc_type: str | None
    doc_id: int | None
    comment: str | None
    user: UserBrief | None
    created_at: datetime

    @classmethod
    def of(cls, movement, user) -> "Movement":
        return cls(id=movement.id, type=movement.type,
                   delta=float(movement.delta),
                   balance_after=float(movement.balance_after),
                   doc_type=movement.doc_type, doc_id=movement.doc_id,
                   comment=movement.comment, user=UserBrief.of(user),
                   created_at=movement.created_at)


class StockOperation(Schema):
    article: str = Field(min_length=1, max_length=100)
    warehouse_id: int
    type: Literal["receipt", "shipment", "writeoff", "recount"]
    qty: float = Field(ge=0)        # recount разрешает 0 — «обнулили остаток»
    comment: str | None = None