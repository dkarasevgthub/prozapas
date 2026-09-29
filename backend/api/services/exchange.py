"""Обмен с 1С: загрузка и выгрузка номенклатуры и остатков.

Разбор формата — в `api/commerceml.py`, здесь только база. Разделение не
формальное: разбор проверяется на настоящих выгрузках 1С без Postgres, а
здесь остаются правила, которые без базы не проверить.

Одна транзакция на файл. Битый файл не применяется вовсе — это `Malformed`
из разбора, роутер отдаёт 400. Разобранный файл применяется целиком, а
позиции, которые не легли, возвращаются списком: «загружено 118 из 120» без
указания, каких двух не хватает, заставило бы сверять файл глазами.

Сопоставление: сначала код 1С, потом артикул. Артикул в выгрузках 1С **не**
уникален — в демо-выгрузке один артикул носят два разных товара, а у вариантов
одного товара он общий, — поэтому ключ первой очереди именно код 1С.
Артикул при загрузке не меняется: он ключ для человека, и его правка на
чужих данных развалила бы ссылки в заказах. Расхождение попадает в `issues`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.models import (CatalogItem, MovementType, StockBalance,
                             StockMovement, UserAccount, Warehouse)

from .. import commerceml
from ..errors import BadRequest, Conflict, Unprocessable
from . import audit, inventory

#: Комментарий к движениям, которые оставляет загрузка. По нему движения
#: импорта отличаются от ручного пересчёта в журнале позиции.
COMMENT = "Импорт из 1С"


class Result:
    """Счётчики и замечания. В схему превращает роутер."""

    def __init__(self) -> None:
        self.created = 0
        self.updated = 0
        self.unchanged = 0
        self.skipped = 0
        self.issues: list[commerceml.Issue] = []

    def skip(self, index: int, ref: str, reason: str) -> None:
        """Позиция не применена."""
        self.skipped += 1
        self.issues.append(commerceml.Issue(index, ref, reason))

    def note(self, index: int, ref: str, reason: str) -> None:
        """Позиция применена, но не полностью. В счётчик пропусков не идёт:
        иначе одна строка попала бы и в «обновлено», и в «пропущено»."""
        self.issues.append(commerceml.Issue(index, ref, reason))


# --- Загрузка -------------------------------------------------------------

def import_catalog(session, user: UserAccount, raw: bytes) -> Result:
    """import.xml → справочник."""
    items, parse_issues = _parse(commerceml.parse_catalog, raw)
    result = Result()
    for issue in parse_issues:
        result.skip(*issue)

    by_code, by_article = _catalog_index(session)
    for item in items:
        ref = item.code1c or item.article or f"позиция {item.index}"
        existing = _match(item.code1c, item.article, by_code, by_article)

        if existing is None:
            if not item.article:
                result.skip(item.index, ref,
                            "новая позиция без артикула — в справочнике он обязателен")
                continue
            if item.article in by_article:
                result.skip(item.index, ref,
                            f"артикул {item.article} уже у другой позиции")
                continue
            row = CatalogItem(article=item.article, code1c=item.code1c,
                              name=item.name, unit=item.unit,
                              unit_weight=item.unit_weight or Decimal("0"))
            session.add(row)
            session.flush()
            by_article[row.article] = row
            if row.code1c:
                by_code[row.code1c] = row
            audit.record(session, entity="catalog_item", entity_id=row.id,
                         action="imported", user_id=user.id,
                         after={"article": row.article, "name": row.name})
            result.created += 1
            continue

        if _conflicting(existing, item.code1c):
            result.skip(item.index, ref,
                        f"артикул {item.article} занят позицией с другим кодом 1С "
                        f"({existing.code1c}) — это разные товары")
            continue
        if item.article and item.article != existing.article:
            result.note(item.index, ref,
                        f"в 1С артикул {item.article}, в справочнике "
                        f"{existing.article} — оставлен прежний")
        changes = _apply(existing, item, by_code)
        if changes:
            audit.record(session, entity="catalog_item", entity_id=existing.id,
                         action="imported", user_id=user.id, after=changes)
            result.updated += 1
        else:
            result.unchanged += 1

    _commit(session)
    return result


def import_stock(session, user: UserAccount, raw: bytes) -> Result:
    """offers.xml → остатки своего склада.

    Инвентаризация: количество из файла становится остатком, разница уходит
    движением типа `recount`. Резерв не трогаем — он принадлежит живым
    заказам, а файл из 1С о них не знает; поэтому остаток ниже резерва
    отклоняется, как и в ручной операции.
    """
    warehouse_id = user.warehouse_id
    if warehouse_id is None:
        raise Unprocessable("У учётной записи нет склада — загружать остатки некуда")

    offers, parse_issues = _parse(commerceml.parse_offers, raw)
    result = Result()
    for issue in parse_issues:
        result.skip(*issue)

    by_code, by_article = _catalog_index(session)
    # Варианты одного товара (размер, цвет) приходят отдельными предложениями
    # с составным Ид. В справочнике им соответствует одна позиция, поэтому
    # количества складываются: остаток товара — сумма остатков его вариантов.
    wanted: dict[int, tuple[Decimal, commerceml.Offer]] = {}
    for offer in offers:
        ref = offer.code1c or offer.article or f"предложение {offer.index}"
        item = _match(offer.code1c, offer.article, by_code, by_article)
        if item is None:
            result.skip(offer.index, ref,
                        "позиции нет в справочнике — сначала загрузите номенклатуру")
            continue
        if _conflicting(item, offer.code1c):
            result.skip(offer.index, ref,
                        f"артикул {offer.article} занят позицией с другим кодом 1С "
                        f"({item.code1c}) — это разные товары")
            continue
        seen = wanted.get(item.id)
        wanted[item.id] = ((seen[0] if seen else Decimal("0")) + offer.qty, offer)

    if wanted:
        # Порядок захвата строк тот же, что в inventory: сначала блокируем
        # существующие по возрастанию item_id — иначе две параллельные
        # загрузки встанут друг против друга.
        balances = inventory.locked_balances(session, warehouse_id, sorted(wanted))
    else:
        balances = {}

    for item_id in sorted(wanted):
        qty, offer = wanted[item_id]
        ref = offer.code1c or offer.article or f"предложение {offer.index}"
        # locked_balances отдаёт только существующие строки: чего в ней нет,
        # того на складе не было — такую позицию заводим и считаем созданной.
        fresh = item_id not in balances
        balance = (inventory.ensure_balance(session, item_id, warehouse_id)
                   if fresh else balances[item_id])

        if qty < balance.reserved:
            result.skip(offer.index, ref,
                        f"остаток {qty} меньше резерва {balance.reserved} "
                        "— позиция занята заказами")
            continue
        delta = qty - balance.qty
        if delta == 0:
            result.unchanged += 1
            continue

        balance.qty = qty
        balance.version += 1
        session.add(StockMovement(
            item_id=item_id, warehouse_id=warehouse_id,
            type=MovementType.RECOUNT, delta=delta, balance_after=qty,
            doc_type=None, doc_id=None, comment=COMMENT, user_id=user.id))
        audit.record(session, entity="stock_balance", entity_id=item_id,
                     action="imported", user_id=user.id,
                     after={"warehouse_id": warehouse_id, "delta": float(delta),
                            "qty": float(qty), "comment": COMMENT})
        if fresh:
            result.created += 1
        else:
            result.updated += 1

    _commit(session)
    return result


# --- Выгрузка -------------------------------------------------------------

def export_catalog(session, *, archived: bool = False) -> bytes:
    """Справочник → import.xml."""
    stmt = (select(CatalogItem)
            .where(CatalogItem.deleted_at.is_(None))
            .order_by(CatalogItem.name))
    if not archived:
        stmt = stmt.where(CatalogItem.is_archived.is_(False))
    rows = [commerceml.ExportItem(code1c=item.code1c, article=item.article,
                                  name=item.name, unit=item.unit,
                                  unit_weight=item.unit_weight)
            for item in session.scalars(stmt)]
    return commerceml.build_catalog(rows, now=_now())


def export_stock(session, warehouse_id: int) -> bytes:
    """Остатки одного склада → offers.xml."""
    warehouse = session.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise Unprocessable("Склад не найден")
    stmt = (select(StockBalance, CatalogItem)
            .join(CatalogItem, CatalogItem.id == StockBalance.item_id)
            .where(StockBalance.warehouse_id == warehouse_id,
                   CatalogItem.deleted_at.is_(None))
            .order_by(CatalogItem.name))
    rows = [commerceml.ExportStock(code1c=item.code1c, article=item.article,
                                   name=item.name, unit=item.unit,
                                   qty=balance.qty)
            for balance, item in session.execute(stmt)]
    return commerceml.build_offers(rows, warehouse_code=warehouse.code,
                                   warehouse_name=warehouse.name, now=_now())


# --- Внутреннее -----------------------------------------------------------

def _parse(reader, raw: bytes):
    """Разбор файла. Нечитаемый файл — отказ разбора, а не нарушение правила:
    по api.md §3.3 это 400, а не 422."""
    try:
        return reader(raw)
    except commerceml.Malformed as exc:
        raise BadRequest(str(exc)) from None


def _catalog_index(session) -> tuple[dict[str, CatalogItem], dict[str, CatalogItem]]:
    """Справочник целиком в память: файл сверяется с ним построчно, и запрос
    на каждую позицию превратил бы загрузку в тысячи обращений к базе."""
    by_code: dict[str, CatalogItem] = {}
    by_article: dict[str, CatalogItem] = {}
    for item in session.scalars(
            select(CatalogItem).where(CatalogItem.deleted_at.is_(None))):
        by_article[item.article] = item
        if item.code1c:
            by_code[item.code1c] = item
    return by_code, by_article


def _conflicting(item: CatalogItem, code1c: str | None) -> bool:
    """Позиция нашлась по артикулу, но носит другой код 1С.

    Артикул в выгрузках 1С не уникален: в демо-выгрузке один артикул носят
    два разных товара. Принять их за одну позицию значило бы слить два товара
    в один и потерять остаток одного из них.
    """
    if not code1c or not item.code1c:
        return False
    return item.code1c not in (code1c, commerceml.base_id(code1c))


def _match(code1c: str | None, article: str | None,
           by_code: dict, by_article: dict) -> CatalogItem | None:
    """Код 1С, затем его товарная часть, затем артикул."""
    if code1c:
        found = by_code.get(code1c)
        if found is not None:
            return found
        base = commerceml.base_id(code1c)
        if base and base in by_code:
            return by_code[base]
    if article:
        return by_article.get(article)
    return None


def _apply(item: CatalogItem, incoming: commerceml.Item,
           by_code: dict) -> dict:
    """Обновить поля позиции, вернуть изменённые.

    Чужой код 1С сюда не доходит: его отсекает `_conflicting`, а по артикулу
    позиция находится только тогда, когда кода из файла в справочнике нет.
    """
    changes: dict = {}
    if incoming.code1c and incoming.code1c != item.code1c:
        item.code1c = incoming.code1c
        by_code[incoming.code1c] = item
        changes["code1c"] = incoming.code1c
    if incoming.name != item.name:
        item.name = incoming.name
        changes["name"] = incoming.name
    if incoming.unit != item.unit:
        item.unit = incoming.unit
        changes["unit"] = incoming.unit
    if (incoming.unit_weight is not None
            and Decimal(str(incoming.unit_weight)) != Decimal(str(item.unit_weight))):
        item.unit_weight = incoming.unit_weight
        changes["unit_weight"] = float(incoming.unit_weight)
    return changes


def _commit(session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("Файл разошёлся со справочником: артикул или код 1С "
                       "уже занят") from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)
