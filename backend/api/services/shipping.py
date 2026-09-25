"""Отгрузка: список, карточка, коробки. Штрихкод выдаёт сервер (api.md §8.1).

Коробка создаётся и удаляется под FOR UPDATE на строку отгрузки: два рабочих
места не упакуют вместе больше заказанного, и нумерация `-NN` внутри артикула
не задвоится. Отметки о печати нет: коробка существует — значит напечатана.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from database.models import (CatalogItem, DocStatus, Order, OrderPosition,
                             OrderStatus, Shipment, ShipmentBox, UserAccount)

from ..errors import Conflict, InvalidTransition, NotFound, Unprocessable


def list_shipments(session: Session, *, wh: int, status: str | None,
                   q: str | None, weight_min: float | None,
                   weight_max: float | None, created_from: date | None,
                   created_to: date | None, shipped_from: date | None,
                   shipped_to: date | None, limit: int,
                   offset: int) -> tuple[list[tuple], int]:
    where = [Order.from_warehouse_id == wh]          # мы отправитель
    if status:
        where.append(Shipment.status == status)
    if created_from:
        where.append(Order.created_at >= created_from)
    if created_to:
        where.append(Order.created_at < created_to + timedelta(days=1))
    if shipped_from:
        where.append(Shipment.shipped_at >= shipped_from)
    if shipped_to:
        where.append(Shipment.shipped_at < shipped_to + timedelta(days=1))
    if q and q.strip():
        where.append(Order.number.startswith(q.strip()))

    # Ожидаемый вес и число позиций: unit_weight × qty по позициям заказа.
    expected = (select(OrderPosition.order_id.label("order_id"),
                       func.coalesce(
                           func.sum(OrderPosition.qty * CatalogItem.unit_weight),
                           0).label("weight"),
                       func.count().label("total"))
                .join(CatalogItem, CatalogItem.id == OrderPosition.item_id)
                .group_by(OrderPosition.order_id).subquery())
    # Факт: вес и число коробок.
    packed = (select(ShipmentBox.shipment_id.label("sid"),
                     func.coalesce(func.sum(ShipmentBox.weight), 0).label("weight"))
              .group_by(ShipmentBox.shipment_id).subquery())
    # Позиции, собранные полностью (прогресс «1 из 2» на экране).
    by_position = (select(ShipmentBox.shipment_id.label("sid"),
                          OrderPosition.id.label("pos_id"),
                          OrderPosition.qty.label("ordered"),
                          func.sum(ShipmentBox.qty).label("packed"))
                   .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
                   .join(Order, Order.id == Shipment.order_id)
                   .join(OrderPosition,
                         (OrderPosition.order_id == Order.id)
                         & (OrderPosition.item_id == ShipmentBox.item_id))
                   .group_by(ShipmentBox.shipment_id, OrderPosition.id).subquery())
    done = (select(by_position.c.sid.label("sid"), func.count().label("done"))
            .where(by_position.c.packed >= by_position.c.ordered)
            .group_by(by_position.c.sid).subquery())

    if weight_min is not None:
        where.append(expected.c.weight >= weight_min)
    if weight_max is not None:
        where.append(expected.c.weight <= weight_max)
    # Индекса под весовой фильтр нет — это осознанно (api.md §6.3).

    base = (select(Shipment, Order,
                   expected.c.weight, expected.c.total,
                   func.coalesce(packed.c.weight, 0), func.coalesce(done.c.done, 0))
            .join(Order, Order.id == Shipment.order_id)
            .outerjoin(expected, expected.c.order_id == Order.id)
            .outerjoin(packed, packed.c.sid == Shipment.id)
            .outerjoin(done, done.c.sid == Shipment.id)
            .where(*where))
    total = session.scalar(
        select(func.count()).select_from(base.subquery())) or 0
    rows = list(session.execute(
        base.order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit).offset(offset)))
    return rows, total


def get_shipment(session: Session, order_id: int, wh: int) -> Shipment:
    ship = session.scalar(
        select(Shipment)
        .join(Order, Order.id == Shipment.order_id)
        .options(selectinload(Shipment.boxes),
                 selectinload(Shipment.order)
                 .selectinload(Order.positions)
                 .selectinload(OrderPosition.item))
        .where(Shipment.order_id == order_id, Order.from_warehouse_id == wh))
    if ship is None:
        raise NotFound("Отгрузка не найдена")
    return ship


def _locked_shipment(session: Session, order_id: int, wh: int) -> Shipment:
    """Строка отгрузки под блокировкой: лимит упаковки и номер коробки
    обязаны считаться по одному в каждый момент."""
    ship = session.scalar(
        select(Shipment)
        .join(Order, Order.id == Shipment.order_id)
        .where(Shipment.order_id == order_id, Order.from_warehouse_id == wh)
        .with_for_update(of=Shipment))
    if ship is None:
        raise NotFound("Отгрузка не найдена")
    return ship


def box_barcode(warehouse_id: int, number: str, article: str, seq: int) -> str:
    """WH{id:3}{номер}{артикул}[-NN]. NN — внутри артикула; первая без
    суффикса, чтобы не переклеивать наклеенную этикетку (api.md §8.1)."""
    base = f"WH{warehouse_id:03d}{number}{article}"
    return base if seq == 0 else f"{base}-{seq + 1:02d}"


def create_box(session: Session, order_id: int, user: UserAccount, *,
               article: str, qty: float, weight: float) -> ShipmentBox:
    ship = _locked_shipment(session, order_id, user.warehouse_id)
    if ship.status == DocStatus.DONE:
        raise InvalidTransition("Отгрузка уже проведена, упаковка закрыта")
    order = ship.order
    if order.status != OrderStatus.PROCESSING:
        raise InvalidTransition("Заказ не в сборке")

    position = session.scalar(
        select(OrderPosition)
        .options(joinedload(OrderPosition.item))
        .where(OrderPosition.order_id == order.id,
               OrderPosition.item_id.in_(
                   select(CatalogItem.id).where(CatalogItem.article == article))))
    if position is None:
        raise Unprocessable(f"Позиции «{article}» нет в заказе")

    qty_d, weight_d = Decimal(str(qty)), Decimal(str(weight))
    packed = Decimal(str(session.scalar(
        select(func.coalesce(func.sum(ShipmentBox.qty), 0))
        .where(ShipmentBox.shipment_id == ship.id,
               ShipmentBox.item_id == position.item_id))))
    # Допуск 1e-9 — на округление дробных количеств на клиенте.
    if packed + qty_d > position.qty + Decimal("1e-9"):
        raise Unprocessable(
            f"Больше заказанного: заказано {position.qty}, уже упаковано {packed}")

    seq = session.scalar(
        select(func.count()).select_from(ShipmentBox)
        .where(ShipmentBox.shipment_id == ship.id,
               ShipmentBox.item_id == position.item_id)) or 0
    box = ShipmentBox(shipment_id=ship.id,
                      barcode=box_barcode(order.to_warehouse_id, order.number,
                                          article, int(seq)),
                      item_id=position.item_id, qty=qty_d, weight=weight_d,
                      user_id=user.id)
    # Первый, кто тронул сборку, и остаётся ответственным.
    if ship.responsible_user_id is None:
        ship.responsible_user_id = user.id
    session.add(box)
    try:
        session.commit()
    except IntegrityError as exc:       # страховка; блокировка делает гонку невозможной
        session.rollback()
        raise Conflict("Коробка не создана: конфликт при записи") from exc
    return box


def delete_box(session: Session, order_id: int, box_id: int,
               user: UserAccount) -> None:
    ship = _locked_shipment(session, order_id, user.warehouse_id)
    if ship.status == DocStatus.DONE:
        raise InvalidTransition("Отгрузка уже проведена, коробку удалить нельзя")
    box = session.scalar(select(ShipmentBox).where(
        ShipmentBox.id == box_id, ShipmentBox.shipment_id == ship.id))
    if box is None:
        raise NotFound("Коробка не найдена")
    session.delete(box)
    session.commit()