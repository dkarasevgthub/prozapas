"""Переходы статусов заказа. Три транзакции — в одном файле намеренно:
резерв ставится здесь и снимается здесь же, читать надо рядом.

Блокировка строки документа (FOR UPDATE) до чтения полей: переходы по одному
заказу сериализуются, версия и статус под проверкой всегда актуальные.
Каждый переход пишет событие статуса и строку audit_log в той же транзакции.
Провал в середине не фиксируется — get_session откатывает.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database.models import (DocStatus, MovementType, Order, OrderPosition,
                             OrderStatus, OrderStatusEvent, Receipt, Shipment,
                             ShipmentBox, UserAccount)

from ..errors import Conflict, InvalidTransition, NotFound
from . import audit, inventory, visibility


def _locked_order(session: Session, order_id: int, wh: int | None) -> Order:
    # of=Order: joined relationships add LEFT OUTER JOINs, and PostgreSQL
    # refuses FOR UPDATE on the nullable side of an outer join.
    order = session.scalar(
        select(Order).where(Order.id == order_id, visibility.mine(wh))
        .with_for_update(of=Order))
    if order is None:
        raise NotFound("Заказ не найден")
    return order


def _positions(session: Session, order: Order) -> list[OrderPosition]:
    return list(session.scalars(
        select(OrderPosition).where(OrderPosition.order_id == order.id)
        .options(joinedload(OrderPosition.item))))


def _check_version(doc, expected: int) -> None:
    if doc.version != expected:
        raise Conflict(f"Версия документа устарела: прислали {expected}, "
                       f"в базе {doc.version}")


def _event(session: Session, order: Order, user: UserAccount,
           reason: str | None = None) -> None:
    session.add(OrderStatusEvent(order_id=order.id, status=order.status,
                                 reason=reason, user_id=user.id))


def accept(session: Session, order_id: int, user: UserAccount,
           expected: int) -> Order:
    """Транзакция №1: статус processing, резерв, пустые отгрузка и приёмка."""
    order = _locked_order(session, order_id, user.warehouse_id)
    if order.from_warehouse_id != user.warehouse_id:
        raise InvalidTransition("Принять заказ может только склад-отправитель")
    if order.status != OrderStatus.CREATED:
        raise InvalidTransition(
            f"Заказ в статусе «{order.status}», принять можно только новый")
    _check_version(order, expected)

    lines = [(p.item_id, p.qty, p.item.article)
             for p in _positions(session, order)]
    inventory.reserve(session, order.from_warehouse_id, lines,
                      user_id=user.id, order_id=order.id)
    order.status = OrderStatus.PROCESSING
    order.version += 1
    _event(session, order, user)
    session.add_all([
        Shipment(order_id=order.id, status=DocStatus.PROGRESS, version=1),
        Receipt(order_id=order.id, warehouse_id=order.to_warehouse_id,
                status=DocStatus.PROGRESS, version=1),
    ])
    audit.record(session, entity="order", entity_id=order.id,
                 action="accepted", user_id=user.id)
    session.commit()
    return order


def decline(session: Session, order_id: int, user: UserAccount,
            expected: int, reason: str | None) -> Order:
    order = _locked_order(session, order_id, user.warehouse_id)
    if order.from_warehouse_id != user.warehouse_id:
        raise InvalidTransition("Отклонить заказ может только склад-отправитель")
    if order.status != OrderStatus.CREATED:
        raise InvalidTransition(
            f"Заказ в статусе «{order.status}», отклонить можно только новый")
    _check_version(order, expected)

    order.status = OrderStatus.DECLINED
    order.reason = reason
    order.version += 1
    _event(session, order, user, reason)
    audit.record(session, entity="order", entity_id=order.id,
                 action="declined", user_id=user.id,
                 after={"reason": reason})
    session.commit()
    return order


def cancel(session: Session, order_id: int, user: UserAccount,
           expected: int, reason: str | None) -> Order:
    order = _locked_order(session, order_id, user.warehouse_id)
    if order.to_warehouse_id != user.warehouse_id:
        raise InvalidTransition("Отменить заказ может только склад-заказчик")
    if order.status not in (OrderStatus.CREATED, OrderStatus.PROCESSING):
        raise InvalidTransition(
            f"Заказ в статусе «{order.status}», отмена невозможна")
    has_boxes = session.scalar(
        select(ShipmentBox.id)
        .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
        .where(Shipment.order_id == order.id).limit(1)) is not None
    if has_boxes:
        raise InvalidTransition("По заказу уже упакованы коробки, отмена невозможна")
    _check_version(order, expected)

    if order.status == OrderStatus.PROCESSING:
        lines = [(p.item_id, p.qty, p.item.article)
                 for p in _positions(session, order)]
        inventory.release(session, order.from_warehouse_id, lines,
                          user_id=user.id, order_id=order.id)
        # Пустые документы ничего не значат без заказа — не оставляем сирот,
        # иначе они всплывут в списках отгрузки и приёмки.
        for doc in session.scalars(select(Shipment)
                                   .where(Shipment.order_id == order.id)):
            session.delete(doc)
        for doc in session.scalars(select(Receipt)
                                   .where(Receipt.order_id == order.id)):
            session.delete(doc)
    order.status = OrderStatus.CANCELLED
    order.reason = reason
    order.version += 1
    _event(session, order, user, reason)
    audit.record(session, entity="order", entity_id=order.id,
                 action="cancelled", user_id=user.id,
                 after={"reason": reason})
    session.commit()
    return order


def ship(session: Session, order_id: int, user: UserAccount,
         expected: int) -> tuple[Order, Shipment, list[dict]]:
    """Транзакция №2. Резерв снимается весь — по всем позициям заказа,
    списывается только упакованное; разница возвращается в свободный остаток
    (api.md §7.2). Ответ несёт недостачу для подтверждения частичной отгрузки.
    """
    ship_doc = session.scalar(
        select(Shipment).where(Shipment.order_id == order_id)
        .with_for_update(of=Shipment))
    order = session.scalar(
        select(Order)
        .where(Order.id == order_id,
               Order.from_warehouse_id == user.warehouse_id)
        .with_for_update(of=Order))
    if ship_doc is None or order is None:
        raise NotFound("Отгрузка не найдена")
    if ship_doc.status == DocStatus.DONE:
        raise InvalidTransition("Отгрузка уже проведена")
    if order.status != OrderStatus.PROCESSING:
        raise InvalidTransition(
            f"Заказ в статусе «{order.status}», отгрузка невозможна")
    _check_version(ship_doc, expected)

    boxes = list(session.scalars(
        select(ShipmentBox).where(ShipmentBox.shipment_id == ship_doc.id)))
    if not boxes:
        raise InvalidTransition("Не упаковано ни одной коробки")
    positions = list(session.scalars(
        select(OrderPosition).where(OrderPosition.order_id == order.id)
        .options(joinedload(OrderPosition.item))))

    packed_by_item: dict[int, Decimal] = {}
    for b in boxes:
        packed_by_item[b.item_id] = (packed_by_item.get(b.item_id, Decimal("0"))
                                     + b.qty)

    balances = inventory.locked_balances(
        session, order.from_warehouse_id, [p.item_id for p in positions])
    now = datetime.now(timezone.utc)
    for p in positions:
        bal = balances.get(p.item_id)
        if bal is None:
            raise Conflict(f"Нет остатка по «{p.item.article}»")
        packed = packed_by_item.get(p.item_id, Decimal("0"))
        if bal.qty < packed:
            # Остаток уменьшили мимо резерва (ручная корректировка).
            raise Conflict(
                f"Остаток «{p.item.article}» изменился, списать нечего")
        bal.qty -= packed
        bal.reserved -= p.qty        # весь резерв, не только упакованное
        bal.version += 1
        inventory.movement(session, item_id=p.item_id,
                           warehouse_id=order.from_warehouse_id,
                           type=MovementType.SHIPMENT, delta=-packed,
                           balance_after=bal.qty, order_id=order.id,
                           user_id=user.id)

    ship_doc.status = DocStatus.DONE
    ship_doc.shipped_at = now
    ship_doc.version += 1
    order.status = OrderStatus.SHIPPED
    order.shipped_at = now
    order.version += 1
    _event(session, order, user)
    audit.record(session, entity="order", entity_id=order.id,
                 action="shipped", user_id=user.id)
    session.commit()

    shortage = [{"article": p.item.article, "name": p.item.name,
                 "ordered": float(p.qty),
                 "packed": float(packed_by_item.get(p.item_id, Decimal("0"))),
                 "remaining": float(p.qty
                                    - packed_by_item.get(p.item_id,
                                                         Decimal("0")))}
                for p in positions
                if packed_by_item.get(p.item_id, Decimal("0")) < p.qty]
    return order, ship_doc, shortage


def complete(session: Session, order_id: int, user: UserAccount,
             expected: int) -> tuple[Order, Receipt, list[ShipmentBox]]:
    """Транзакция №3. Приходуется принятое, а не отгруженное: непринятые
    коробки остаются без received_at — это и есть недостача. Заказ
    принимается и с расхождением (api.md §7.3)."""
    receipt = session.scalar(
        select(Receipt)
        .join(Order, Order.id == Receipt.order_id)
        .where(Receipt.order_id == order_id,
               Order.to_warehouse_id == user.warehouse_id)
        .with_for_update(of=Receipt))
    if receipt is None:
        raise NotFound("Приёмка не найдена")
    if receipt.status == DocStatus.DONE:
        raise InvalidTransition("Приёмка уже завершена")
    order = receipt.order
    if order.status != OrderStatus.SHIPPED:
        raise InvalidTransition(
            f"Заказ в статусе «{order.status}», завершить приёмку нельзя")
    _check_version(receipt, expected)

    boxes = list(session.scalars(
        select(ShipmentBox)
        .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
        .where(Shipment.order_id == order.id)))

    accepted: dict[int, Decimal] = {}
    for b in boxes:
        if b.received_at is not None:
            accepted[b.item_id] = (accepted.get(b.item_id, Decimal("0"))
                                   + b.qty)

    now = datetime.now(timezone.utc)
    for item_id, qty in sorted(accepted.items()):
        if qty <= 0:
            continue
        bal = inventory.ensure_balance(session, item_id, receipt.warehouse_id)
        bal.qty += qty
        bal.version += 1
        inventory.movement(session, item_id=item_id,
                           warehouse_id=receipt.warehouse_id,
                           type=MovementType.RECEIPT, delta=qty,
                           balance_after=bal.qty, order_id=order.id,
                           user_id=user.id)

    receipt.status = DocStatus.DONE
    receipt.accepted_at = now
    receipt.version += 1
    order.status = OrderStatus.RECEIVED
    order.accepted_at = now
    order.version += 1
    _event(session, order, user)
    audit.record(session, entity="order", entity_id=order.id,
                 action="received", user_id=user.id)
    session.commit()

    missing = [b for b in boxes if b.received_at is None]
    return order, receipt, missing