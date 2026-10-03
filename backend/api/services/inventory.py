"""Остатки: блокировка строк баланса, резерв, журнал движений, ручные операции.

Строки баланса берутся под SELECT ... FOR UPDATE: два одновременных accept
на одном складе сериализуются, условие «свободного >= заказанного»
проверяется под блокировкой и не может разъехаться. Ручная операция пишет
аудит той же транзакцией.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from database.models import (CatalogItem, MovementType, StockBalance,
                             StockMovement, UserAccount, Warehouse)

from ..errors import InsufficientStock, NotFound, Unprocessable
from . import audit


# --- Базовые операции с балансом (используются транзакциями заказов) ----------

def locked_balances(session: Session, warehouse_id: int,
                    item_ids: list[int]) -> dict[int, StockBalance]:
    rows = session.scalars(
        select(StockBalance)
        .where(StockBalance.warehouse_id == warehouse_id,
               StockBalance.item_id.in_(item_ids))
        .order_by(StockBalance.item_id)   # стабильный порядок захвата — без взаимных блокировок
        .with_for_update())
    return {b.item_id: b for b in rows}


def ensure_balance(session: Session, item_id: int,
                   warehouse_id: int) -> StockBalance:
    """Строка баланса под блокировкой, при отсутствии — создаётся.

    Строки может не быть: этот товар на склад ещё ни разу не приходовал.
    Гонку двух одновременных созданий закрывает ON CONFLICT DO NOTHING
    и первичный ключ (item_id, warehouse_id): второй ждёт первого,
    затем дочитывает уже существующую строку.
    """
    where = (StockBalance.item_id == item_id,
             StockBalance.warehouse_id == warehouse_id)
    bal = session.scalar(select(StockBalance).where(*where).with_for_update())
    if bal is not None:
        return bal
    session.execute(pg_insert(StockBalance)
                    .values(item_id=item_id, warehouse_id=warehouse_id,
                            qty=0, reserved=0, min_qty=0, version=1)
                    .on_conflict_do_nothing())
    return session.scalar(select(StockBalance).where(*where).with_for_update())


def movement(session: Session, *, item_id: int, warehouse_id: int,
             type: MovementType, delta: Decimal, balance_after: Decimal,
             order_id: int | None = None, comment: str | None = None,
             user_id: int | None = None) -> None:
    session.add(StockMovement(
        item_id=item_id, warehouse_id=warehouse_id, type=type, delta=delta,
        balance_after=balance_after,
        doc_type="order" if order_id else None, doc_id=order_id,
        comment=comment, user_id=user_id))


def reserve(session: Session, warehouse_id: int, lines, *,
            user_id: int, order_id: int) -> None:
    """lines — тройки (item_id, qty, article). Резервируется заказанное целиком."""
    balances = locked_balances(session, warehouse_id, [l[0] for l in lines])
    shortage = []
    for item_id, qty, article in lines:
        bal = balances.get(item_id)
        free = (bal.qty - bal.reserved) if bal else Decimal("0")
        if free < qty:
            shortage.append({"article": article, "requested": float(qty),
                             "available": float(free)})
    if shortage:
        raise InsufficientStock(
            f"Не хватает позиций: {', '.join(s['article'] for s in shortage)}",
            positions=shortage)
    for item_id, qty, _article in lines:
        bal = balances[item_id]
        bal.reserved += qty
        bal.version += 1
        # balance_after — количество: резерв его не меняет, меняет reserved.
        movement(session, item_id=item_id, warehouse_id=warehouse_id,
                 type=MovementType.RESERVE, delta=qty, balance_after=bal.qty,
                 order_id=order_id, user_id=user_id)


def release(session: Session, warehouse_id: int, lines, *,
            user_id: int, order_id: int) -> None:
    """Снять резерв целиком (отмена заказа в processing)."""
    balances = locked_balances(session, warehouse_id, [l[0] for l in lines])
    for item_id, qty, _article in lines:
        bal = balances[item_id]
        if bal.reserved < qty:
            raise InsufficientStock(
                f"Резерв по позиции {item_id} меньше заказанного")
        bal.reserved -= qty
        bal.version += 1
        movement(session, item_id=item_id, warehouse_id=warehouse_id,
                 type=MovementType.UNRESERVE, delta=-qty,
                 balance_after=bal.qty, order_id=order_id, user_id=user_id)


# --- Чтение: список, сводка, разбивка, журнал ---------------------------------

def list_stock(session: Session, *, default_wh: int, warehouse_id: int | None,
               q: str | None, below_min: bool, in_stock: bool | None,
               limit: int, offset: int
               ) -> tuple[list[tuple[StockBalance, CatalogItem]], int]:
    """in_stock=None — умолчание из контракта: True, но при below_min
    снимается: нулевой остаток при минимуме пятьдесят как раз и требует
    дозаказа (api.md §6.5)."""
    wh = warehouse_id if warehouse_id is not None else default_wh
    show_in_stock = (not below_min) if in_stock is None else in_stock

    where = [StockBalance.warehouse_id == wh,
             CatalogItem.deleted_at.is_(None)]
    if show_in_stock:
        where.append(StockBalance.qty > 0)
    if below_min:
        where.append(StockBalance.qty - StockBalance.reserved
                     < StockBalance.min_qty)
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append(or_(CatalogItem.article.ilike(like),
                         CatalogItem.name.ilike(like)))

    base = (select(StockBalance, CatalogItem)
            .join(CatalogItem, CatalogItem.id == StockBalance.item_id)
            .where(*where))
    total = session.scalar(
        select(func.count()).select_from(base.subquery())) or 0
    rows = list(session.execute(
        base.order_by(CatalogItem.name, StockBalance.item_id)
        .limit(limit).offset(offset)))
    return rows, total


def shares_by_items(session: Session, item_ids: list[int]
                    ) -> dict[int, list[StockBalance]]:
    """Разбивка по всем складам пакетом — один запрос на страницу, не N+1.
    Только ненулевые: режим «найти где есть» читает это списком кандидатов,
    наибольший остаток первым."""
    if not item_ids:
        return {}
    rows = session.scalars(
        select(StockBalance)
        .where(StockBalance.item_id.in_(item_ids), StockBalance.qty > 0)
        .order_by(StockBalance.item_id, StockBalance.qty.desc()))
    result: dict[int, list[StockBalance]] = {}
    for bal in rows:
        result.setdefault(bal.item_id, []).append(bal)
    return result


def stock_by_item(session: Session, item_id: int, own_wh: int | None
                  ) -> list[tuple[StockBalance, Warehouse]]:
    """Карточка товара: свой склад первым, остальные по возрастанию id."""
    rows = list(session.execute(
        select(StockBalance, Warehouse)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
        .where(StockBalance.item_id == item_id)
        .order_by(StockBalance.warehouse_id)))
    if own_wh is None:
        return rows
    return sorted(rows, key=lambda r: 0 if r[0].warehouse_id == own_wh else 1)


def summary(session: Session, wh: int) -> dict:
    """Четыре карточки экрана. Возвращает словарь — схему собирает роутер,
    сервис схем не знает."""
    row = session.execute(
        select(func.count().label("positions"),
               func.coalesce(func.sum(case(
                   (StockBalance.qty - StockBalance.reserved
                    < StockBalance.min_qty, 1), else_=0)), 0)
               .label("below_min"),
               func.coalesce(func.sum(StockBalance.reserved), 0)
               .label("reserved"),
               func.coalesce(func.sum(StockBalance.qty
                                      - StockBalance.reserved), 0)
               .label("free"))
        .where(StockBalance.warehouse_id == wh)).one()
    return {"positions": int(row.positions), "below_min": int(row.below_min),
            "reserved": float(row.reserved), "free": float(row.free)}


def movements(session: Session, *, item_id: int, wh: int,
              type: str | None, limit: int, offset: int
              ) -> tuple[list[tuple[StockMovement, UserAccount | None]], int]:
    """История по своему складу. Индекс ix_stock_movement_item под это."""
    where = [StockMovement.item_id == item_id,
             StockMovement.warehouse_id == wh]
    if type:
        where.append(StockMovement.type == type)
    total = session.scalar(
        select(func.count()).select_from(StockMovement).where(*where)) or 0
    rows = list(session.execute(
        select(StockMovement, UserAccount)
        .outerjoin(UserAccount, UserAccount.id == StockMovement.user_id)
        .where(*where)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .limit(limit).offset(offset)))
    return rows, total


# --- Ручная операция -----------------------------------------------------------

def operation(session: Session, user: UserAccount, *, article: str,
              warehouse_id: int, type: str, qty: float,
              comment: str | None) -> StockMovement:
    """Ручная операция из карточки товара.

    Склад — только свой: поле в теле есть по контракту, но сервер сверяет его
    со складом из токена. reserve/unreserve вручную не проводятся — их ставит
    заказ. recount: qty — новое значение остатка, не дельта. У списания и
    прихода документа нет: doc_type/doc_id пустые, историю объясняет comment.
    """
    if warehouse_id != user.warehouse_id:
        raise Unprocessable("Операции можно проводить только по своему складу")
    if type not in ("receipt", "shipment", "writeoff", "recount"):
        raise Unprocessable(f"Тип «{type}» вручную не проводится")
    if type != "recount" and qty <= 0:
        raise Unprocessable("Количество должно быть больше нуля")

    item = session.scalar(
        select(CatalogItem).where(CatalogItem.article == article))
    if item is None or item.deleted_at is not None:
        raise NotFound("Позиция справочника не найдена")
    if item.is_archived and type == "receipt":
        raise Unprocessable("Позиция в архиве, приход по ней невозможен")

    qty_d = Decimal(str(qty))
    bal = ensure_balance(session, item.id, warehouse_id)

    if type == "recount":
        if qty_d < bal.reserved:
            raise Unprocessable(
                f"Новый остаток меньше резерва: зарезервировано {bal.reserved}")
        delta = qty_d - bal.qty
        bal.qty = qty_d
    elif type == "receipt":
        delta = qty_d
        bal.qty += qty_d
    else:                                    # shipment | writeoff
        if bal.qty - bal.reserved < qty_d:
            raise InsufficientStock(
                "Недостаточно свободного остатка для списания",
                positions=[{"article": article, "requested": float(qty),
                            "available": float(bal.qty - bal.reserved)}])
        delta = -qty_d
        bal.qty -= qty_d

    bal.version += 1
    entry = StockMovement(
        item_id=item.id, warehouse_id=warehouse_id, type=MovementType(type),
        delta=delta, balance_after=bal.qty,
        doc_type=None, doc_id=None, comment=comment, user_id=user.id)
    session.add(entry)
    audit.record(session, entity="stock_balance", entity_id=item.id,
                 action=type, user_id=user.id,
                 after={"warehouse_id": warehouse_id, "delta": float(delta),
                        "qty": float(bal.qty), "comment": comment})
    session.commit()
    return entry
