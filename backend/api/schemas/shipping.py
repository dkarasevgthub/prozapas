"""Отгрузка. Имена классов и полей — components.schemas openapi.json."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import Schema, UserBrief, WarehouseBrief
from .orders import Order


class BoxCreate(Schema):
    article: str = Field(min_length=1, max_length=100)
    qty: float = Field(gt=0)
    weight: float = Field(gt=0)


class Box(Schema):
    id: int
    barcode: str
    article: str
    name: str
    qty: float
    weight: float
    created_at: datetime

    @classmethod
    def of(cls, box) -> "Box":
        return cls(id=box.id, barcode=box.barcode, article=box.item.article,
                   name=box.item.name, qty=float(box.qty),
                   weight=float(box.weight), created_at=box.created_at)


class ToPackLine(Schema):
    article: str
    name: str
    unit: str
    unit_weight: float
    ordered: float
    packed: float
    remaining: float


def to_pack_lines(order, boxes) -> list[ToPackLine]:
    """Прогресс по позициям. Считает сервер: клиент не вычитает (api.md §6.3)."""
    packed: dict[int, float] = {}
    for b in boxes:
        packed[b.item_id] = packed.get(b.item_id, 0.0) + float(b.qty)
    lines = []
    for p in order.positions:
        ordered = float(p.qty)
        done = packed.get(p.item_id, 0.0)
        lines.append(ToPackLine(
            article=p.item.article, name=p.item.name, unit=p.item.unit,
            unit_weight=float(p.item.unit_weight), ordered=ordered,
            packed=done, remaining=max(ordered - done, 0.0)))
    return lines


class ShipmentListItem(Schema):
    order_id: int
    number: str
    status: str
    to_warehouse: WarehouseBrief
    positions_total: int
    positions_packed: int
    weight_expected: float
    weight_packed: float
    responsible: UserBrief | None
    created_at: datetime
    shipped_at: datetime | None

    @classmethod
    def of(cls, shipment, order, expected_weight: float, packed_weight: float,
           done_positions: int) -> "ShipmentListItem":
        return cls(order_id=order.id, number=order.number,
                   status=shipment.status,
                   to_warehouse=WarehouseBrief.of(order.to_warehouse),
                   positions_total=0,          # подставит роутер из подзапроса
                   positions_packed=done_positions,
                   weight_expected=expected_weight,
                   weight_packed=packed_weight,
                   responsible=UserBrief.of(shipment.responsible),
                   created_at=order.created_at, shipped_at=shipment.shipped_at)


class ShipmentDetail(Schema):
    order: Order
    status: str
    responsible: UserBrief | None
    to_pack: list[ToPackLine]
    boxes: list[Box]
    version: int

    @classmethod
    def of(cls, shipment) -> "ShipmentDetail":
        return cls(order=Order.of(shipment.order), status=shipment.status,
                   responsible=UserBrief.of(shipment.responsible),
                   to_pack=to_pack_lines(shipment.order, shipment.boxes),
                   boxes=[Box.of(b) for b in shipment.boxes],
                   version=shipment.version)


class ShortageLine(Schema):
    article: str
    name: str
    ordered: float
    packed: float
    remaining: float


class ShipResult(Schema):
    order: Order
    shortage: list[ShortageLine]