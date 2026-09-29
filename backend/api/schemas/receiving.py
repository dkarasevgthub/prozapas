"""Приёмка. Имена классов и полей — components.schemas openapi.json."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import Schema, UserBrief, WarehouseBrief
from .orders import Order


class ReceiveBody(Schema):
    actual_weight: float = Field(gt=0)


class ReceivedBox(Schema):
    barcode: str
    article: str
    name: str
    qty: float
    weight: float
    received_at: datetime | None
    actual_weight: float | None
    diff_kg: float | None
    diff_percent: float | None

    @classmethod
    def of(cls, box) -> "ReceivedBox":
        """Расхождение считается здесь и не хранится (api.md §8.4)."""
        diff_kg = diff_pct = None
        if box.actual_weight is not None:
            diff_kg = float(box.actual_weight) - float(box.weight)
            diff_pct = diff_kg / float(box.weight) * 100
        return cls(barcode=box.barcode, article=box.item.article,
                   name=box.item.name, qty=float(box.qty),
                   weight=float(box.weight), received_at=box.received_at,
                   actual_weight=(float(box.actual_weight)
                                  if box.actual_weight is not None else None),
                   diff_kg=diff_kg, diff_percent=diff_pct)


class ReceiptListItem(Schema):
    order_id: int
    number: str
    status: str
    from_warehouse: WarehouseBrief
    boxes_total: int
    boxes_received: int
    weight_expected: float
    weight_actual: float
    responsible: UserBrief | None
    created_at: datetime
    shipped_at: datetime | None


class ReceiptDetail(Schema):
    order: Order
    status: str
    responsible: UserBrief | None
    boxes: list[ReceivedBox]
    version: int


class ReceiptResult(Schema):
    order: Order
    missing: list[ReceivedBox]
