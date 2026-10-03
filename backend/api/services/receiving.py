"""Приёмка: список, карточка, сканы коробок.

Коробки настоящие — те, что упаковал отправитель; приёмка ничего не
синтезирует. Ключ операции — штрихкод, он же естественный ключ
идемпотентности: повторный скан возвращает ту же коробку с 200.
Строка коробки берётся под FOR UPDATE: два сканера не задвоят отметку.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from database.models import (DocStatus, Order, OrderPosition, OrderStatus,
                             Receipt, Shipment, ShipmentBox, UserAccount)

from ..errors import InvalidTransition, NotFound


def list_receipts(session: Session, *, wh: int, status: str | None,
                  q: str | None, weight_min: float | None,
                  weight_max: float | None, created_from: date | None,
                  created_to: date | None, shipped_from: date | None,
                  shipped_to: date | None, limit: int,
                  offset: int) -> tuple[list[tuple], int]:
    where = [Order.to_warehouse_id == wh,
             Order.status.in_([OrderStatus.SHIPPED, OrderStatus.RECEIVED])]
    if status:
        where.append(Receipt.status == status)
    if created_from:
        where.append(Order.created_at >= created_from)
    if created_to:
        where.append(Order.created_at < created_to + timedelta(days=1))
    if shipped_from:
        where.append(Order.shipped_at >= shipped_from)
    if shipped_to:
        where.append(Order.shipped_at < shipped_to + timedelta(days=1))
    if q and q.strip():
        where.append(Order.number.startswith(q.strip()))

    # Ожидаемый вес — задекларированный отправителем (сумма весов коробок),
    # фактический — по принятым. Считает SQL, клиент не складывает.
    boxes = (select(Shipment.order_id.label("order_id"),
                    func.count().label("total"),
                    func.count(ShipmentBox.received_at).label("received"),
                    func.coalesce(func.sum(ShipmentBox.weight), 0)
                    .label("expected"),
                    func.coalesce(func.sum(ShipmentBox.actual_weight), 0)
                    .label("actual"))
             .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
             .group_by(Shipment.order_id).subquery())

    if weight_min is not None:
        where.append(boxes.c.expected >= weight_min)
    if weight_max is not None:
        where.append(boxes.c.expected <= weight_max)

    base = (select(Receipt, Order,
                   func.coalesce(boxes.c.total, 0),
                   func.coalesce(boxes.c.received, 0),
                   func.coalesce(boxes.c.expected, 0),
                   func.coalesce(boxes.c.actual, 0))
            .join(Order, Order.id == Receipt.order_id)
            .outerjoin(boxes, boxes.c.order_id == Order.id)
            .where(*where))
    total = session.scalar(
        select(func.count()).select_from(base.subquery())) or 0
    rows = list(session.execute(
        base.order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit).offset(offset)))
    return rows, total


def get_receipt(session: Session, order_id: int, wh: int
                ) -> tuple[Receipt, Order, list[ShipmentBox]]:
    receipt, order = session.execute(
        select(Receipt, Order)
        .join(Order, Order.id == Receipt.order_id)
        .options(selectinload(Order.positions).selectinload(OrderPosition.item))
        .where(Receipt.order_id == order_id,
               Order.to_warehouse_id == wh,
               Order.status.in_([OrderStatus.SHIPPED, OrderStatus.RECEIVED]))
    ).one_or_none() or (None, None)
    if receipt is None:
        raise NotFound("Приёмка не найдена")
    boxes = _order_boxes(session, order_id)
    return receipt, order, boxes


def _order_boxes(session: Session, order_id: int) -> list[ShipmentBox]:
    return list(session.scalars(
        select(ShipmentBox)
        .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
        .where(Shipment.order_id == order_id)
        .order_by(ShipmentBox.created_at, ShipmentBox.id)))


def _locked_receipt(session: Session, order_id: int, wh: int) -> Receipt:
    receipt = session.scalar(
        select(Receipt)
        .join(Order, Order.id == Receipt.order_id)
        .where(Receipt.order_id == order_id, Order.to_warehouse_id == wh)
        .with_for_update(of=Receipt))
    if receipt is None:
        raise NotFound("Приёмка не найдена")
    return receipt


def _check_active(session: Session, receipt: Receipt) -> Order:
    """Приёмка открыта, заказ в пути. Завершённая приёмка сканов не принимает."""
    order = receipt.order
    if receipt.status == DocStatus.DONE:
        raise InvalidTransition("Приёмка уже завершена")
    if order.status != OrderStatus.SHIPPED:
        raise InvalidTransition(
            f"Заказ в статусе «{order.status}», скан невозможен")
    return order


def receive(session: Session, order_id: int, barcode: str,
            user: UserAccount, actual_weight: float) -> ShipmentBox:
    receipt = _locked_receipt(session, order_id, user.warehouse_id)
    _check_active(session, receipt)

    box = session.scalar(
        select(ShipmentBox)
        .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
        .where(Shipment.order_id == order_id, ShipmentBox.barcode == barcode)
        .with_for_update(of=ShipmentBox))
    if box is None:
        raise NotFound("Штрихкод не относится к этой приёмке")
    if box.received_at is not None:
        return box                      # повторный скан — та же коробка, 200

    box.received_at = datetime.now(timezone.utc)
    box.actual_weight = Decimal(str(actual_weight))
    box.received_by_id = user.id
    if receipt.responsible_user_id is None:
        receipt.responsible_user_id = user.id   # первый, кто принял, и ответственный
    session.commit()
    return box


def cancel_receive(session: Session, order_id: int, barcode: str,
                   user: UserAccount) -> None:
    receipt = _locked_receipt(session, order_id, user.warehouse_id)
    _check_active(session, receipt)

    box = session.scalar(
        select(ShipmentBox)
        .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
        .where(Shipment.order_id == order_id, ShipmentBox.barcode == barcode)
        .with_for_update(of=ShipmentBox))
    if box is None:
        raise NotFound("Штрихкод не относится к этой приёмке")
    if box.received_at is not None:
        box.received_at = None
        box.actual_weight = None
        box.received_by_id = None
        session.commit()
    # Не был принят — 204 без изменений: отмена идемпотентна.
