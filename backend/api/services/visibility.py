"""Видимость заказов по своему складу.

Единственное место, где это правило записано. Оно должно попасть в каждую
выборку заказов, отгрузок и приёмок — и не должно приниматься параметром
запроса, иначе склад №4 подсмотрит переписку между №1 и №3.
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, or_

from database.models import Order


def mine(warehouse_id: int | None) -> ColumnElement[bool]:
    """`from_warehouse_id = мой OR to_warehouse_id = мой`."""
    if warehouse_id is None:
        # Сотрудник без склада не участвует в перемещениях и не видит ничего.
        # Пустой фильтр здесь был бы «видит всё» — ровно наоборот.
        return Order.id.is_(None)
    return or_(Order.from_warehouse_id == warehouse_id,
               Order.to_warehouse_id == warehouse_id)


def outgoing(warehouse_id: int | None) -> ColumnElement[bool]:
    """Мы заказали, товар придёт к нам."""
    return Order.to_warehouse_id == warehouse_id if warehouse_id else Order.id.is_(None)


def incoming(warehouse_id: int | None) -> ColumnElement[bool]:
    """У нас заказали, мы отгружаем."""
    return Order.from_warehouse_id == warehouse_id if warehouse_id else Order.id.is_(None)
