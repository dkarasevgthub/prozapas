"""Заказы: список, карточка, история, создание. Переходы — в transitions.py.

Идемпотентность создания — по заголовку Idempotency-Key: тот же ключ и то же
тело → сохранённый ответ; тот же ключ с другим телом → 409. Гонка двух
одновременных запросов закрывается PRIMARY KEY на idempotency_key.key:
проигравший откатывается целиком и возвращает ответ победителя.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Callable, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from database.models import (CatalogItem, IdempotencyKey, Order, OrderPosition,
                             OrderStatus, OrderStatusEvent, UserAccount,
                             Warehouse, order_number_seq)

from ..errors import BadRequest, Conflict, NotFound, Unprocessable
from . import audit, visibility


class Position(NamedTuple):
    article: str
    qty: float


def list_orders(session: Session, *, wh: int | None, tab: str,
                statuses: list[OrderStatus] | None,
                warehouse_id: int | None, responsible_id: int | None,
                created_from: date | None, created_to: date | None,
                q: str | None, limit: int,
                offset: int) -> tuple[list[tuple[Order, int]], int]:
    # tab=outgoing — мы заказали, второй склад отправитель; incoming — наоборот.
    where = [visibility.incoming(wh) if tab == "incoming" else visibility.outgoing(wh)]
    if statuses:
        where.append(Order.status.in_(statuses))
    if warehouse_id:
        where.append(Order.from_warehouse_id == warehouse_id if tab == "outgoing"
                     else Order.to_warehouse_id == warehouse_id)
    if responsible_id:
        where.append(Order.responsible_user_id == responsible_id)
    if created_from:
        where.append(Order.created_at >= created_from)
    if created_to:
        where.append(Order.created_at < created_to + timedelta(days=1))
    if q and q.strip():
        where.append(Order.number.startswith(q.strip()))

    counts = (select(OrderPosition.order_id.label("order_id"),
                     func.count().label("cnt"))
              .group_by(OrderPosition.order_id).subquery())
    total = session.scalar(select(func.count()).select_from(Order).where(*where)) or 0
    rows = list(session.execute(
        select(Order, func.coalesce(counts.c.cnt, 0))
        .outerjoin(counts, counts.c.order_id == Order.id)
        .where(*where)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit).offset(offset)))
    return rows, total


def get_order(session: Session, order_id: int, wh: int | None) -> Order:
    order = session.scalar(
        select(Order).options(selectinload(Order.positions))
        .where(Order.id == order_id, visibility.mine(wh)))
    if order is None:
        raise NotFound("Заказ не найден")
    return order


def history(session: Session, order_id: int, wh: int | None
            ) -> list[tuple[OrderStatusEvent, UserAccount | None]]:
    if session.scalar(select(Order.id).where(Order.id == order_id,
                                             visibility.mine(wh))) is None:
        raise NotFound("Заказ не найден")
    return list(session.execute(
        select(OrderStatusEvent, UserAccount)
        .outerjoin(UserAccount, UserAccount.id == OrderStatusEvent.user_id)
        .where(OrderStatusEvent.order_id == order_id)
        .order_by(OrderStatusEvent.occurred_at, OrderStatusEvent.id)))


def create(session: Session, *, user: UserAccount, idem_key: str | None,
           from_warehouse_id: int, comment: str | None,
           positions: list[Position],
           serialize: Callable[[Order], dict]) -> dict:
    """Возвращает тело ответа. serialize передаёт роутер — сервис не знает
    схем и не собирает ответ клиента сам (api-architecture §2)."""
    if not idem_key or not idem_key.strip():
        raise BadRequest("Нет заголовка Idempotency-Key")
    # Scoped to the user: the same key from another client must not replay
    # a stored order of a warehouse it cannot see (api.md §3.1).
    idem_key = f"{user.id}:{idem_key.strip()}"
    request_hash = _request_hash(from_warehouse_id, comment, positions)

    stored = session.get(IdempotencyKey, idem_key)
    if stored is not None:
        _same_or_conflict(stored, request_hash)
        return dict(stored.response)

    if user.warehouse_id is None:
        raise Unprocessable("За вами не закреплён склад")
    sender = session.get(Warehouse, from_warehouse_id)
    if sender is None or sender.deleted_at is not None:
        raise NotFound("Склад-отправитель не найден")
    if not sender.is_active:
        raise Unprocessable("Склад-отправитель не активен")
    if from_warehouse_id == user.warehouse_id:
        raise Unprocessable("Нельзя заказать у своего склада")

    if len({p.article for p in positions}) != len(positions):
        raise Unprocessable("В заказе повторяется артикул")
    items = {i.article: i for i in session.scalars(
        select(CatalogItem).where(
            CatalogItem.article.in_([p.article for p in positions])))}
    bad = [p.article for p in positions
           if p.article not in items
           or items[p.article].is_archived
           or items[p.article].deleted_at is not None]
    if bad:
        raise Unprocessable(f"Позиции не найдены или в архиве: {', '.join(bad)}")

    number = str(session.scalar(select(order_number_seq.next_value())))
    order = Order(number=number, status=OrderStatus.CREATED,
                  from_warehouse_id=from_warehouse_id,
                  to_warehouse_id=user.warehouse_id,
                  responsible_user_id=user.id, comment=comment, version=1)
    session.add(order)
    session.flush()                    # id заказа — для позиций, события и ключа
    session.add_all(OrderPosition(order_id=order.id,
                                  item_id=items[p.article].id, qty=p.qty)
                    for p in positions)
    session.add(OrderStatusEvent(order_id=order.id, status=OrderStatus.CREATED,
                                 user_id=user.id))
    audit.record(session, entity="order", entity_id=order.id, action="created",
                 user_id=user.id)
    payload = serialize(order)         # lazy-связи работают: транзакция открыта
    session.add(IdempotencyKey(key=idem_key, request_hash=request_hash,
                               response=payload, status_code=201))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        stored = session.get(IdempotencyKey, idem_key)
        if stored is not None:
            _same_or_conflict(stored, request_hash)
            return dict(stored.response)   # параллельный дубль: победил другой
        raise Conflict("Заказ не создан: конфликт при записи")
    return payload


def _same_or_conflict(stored: IdempotencyKey, request_hash: str) -> None:
    if stored.request_hash != request_hash:
        raise Conflict("Ключ идемпотентности уже использован с другим телом заказа")


def _request_hash(from_warehouse_id: int, comment: str | None,
                  positions: list[Position]) -> str:
    body = json.dumps({"from_warehouse_id": from_warehouse_id,
                       "comment": comment,
                       "positions": [[p.article, p.qty] for p in positions]},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()