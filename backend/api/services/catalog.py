"""Справочник: правила и запись.

Поиск — по search_vector (GIN, русский словарь), не LIKE: api.md §6.6.
Позиция не удаляется, а архивируется — история движений и старые заказы
остаются целыми. Действия пишутся в audit_log той же транзакцией.

Коммит здесь, а не `with session.begin()`: сессию уже начал current_user,
второй begin() падает. Отказ до коммита откатывает get_session — частичной
записи не остаётся.
"""
from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import CatalogItem

from ..errors import Conflict, NotFound, Unprocessable
from . import audit

_SANITIZE = re.compile(r"[^\w\s-]")


def list_items(session: Session, *, q: str | None, archived: bool,
               limit: int, offset: int) -> tuple[list[CatalogItem], int]:
    where = [CatalogItem.deleted_at.is_(None),
             CatalogItem.is_archived.is_(archived)]
    if q and q.strip():
        ts = _prefix_query(q)
        if ts:  # санитайзер выкинул всё — ищем без фильтра, а не пустой список
            where.append(CatalogItem.search_vector.op("@@")(
                func.to_tsquery("russian", ts)))
    total = session.scalar(
        select(func.count()).select_from(CatalogItem).where(*where)) or 0
    items = list(session.scalars(
        select(CatalogItem).where(*where)
        .order_by(CatalogItem.name, CatalogItem.id)
        .limit(limit).offset(offset)))
    return items, total


def get(session: Session, item_id: int) -> CatalogItem:
    item = session.get(CatalogItem, item_id)
    if item is None or item.deleted_at is not None:
        raise NotFound("Позиция справочника не найдена")
    return item


def create(session: Session, *, article: str, code1c: str | None, name: str,
           unit: str, unit_weight: float, user_id: int | None = None
           ) -> CatalogItem:
    _ensure_free(session, article=article, code1c=code1c)
    item = CatalogItem(article=article, code1c=code1c, name=name,
                       unit=unit, unit_weight=unit_weight)
    session.add(item)
    session.flush()
    audit.record(session, entity="catalog_item", entity_id=item.id,
                 action="created", user_id=user_id,
                 after={"article": article, "name": name})
    _commit(session, "Позиция с таким артикулом уже существует")
    return item


def update(session: Session, item_id: int, changes: dict,
           user_id: int | None = None) -> CatalogItem:
    item = get(session, item_id)
    if not changes:
        return item
    for field in ("name", "unit", "unit_weight"):
        if field in changes and changes[field] is None:
            raise Unprocessable(f"Поле «{field}» не может быть пустым")
    _ensure_free(session, article=item.article,
                 code1c=changes.get("code1c", item.code1c), exclude_id=item.id)
    for field, value in changes.items():
        setattr(item, field, value)
    audit.record(session, entity="catalog_item", entity_id=item.id,
                 action="updated", user_id=user_id, after=changes)
    _commit(session, "Код 1С уже занят другой позицией")
    return item


def archive(session: Session, item_id: int,
            user_id: int | None = None) -> CatalogItem:
    item = get(session, item_id)
    item.is_archived = True
    audit.record(session, entity="catalog_item", entity_id=item.id,
                 action="archived", user_id=user_id)
    _commit(session, "Не удалось архивировать позицию")
    return item


def _prefix_query(q: str) -> str | None:
    """«Труба стальная» → ``труба:* & стальная:*`` — префиксы по лексемам,
    чтобы находило и по началу артикула."""
    # Hyphens stay inside a token: the parser keeps "ТМЦ-00512" as a compound,
    # and a query without the hyphen never matches the 1C code.
    tokens = [t.strip("-") for t in _SANITIZE.sub(" ", q).split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return None
    return " & ".join(f"{t}:*" for t in tokens)


def _ensure_free(session: Session, *, article: str, code1c: str | None,
                 exclude_id: int | None = None) -> None:
    def taken(column, value) -> bool:
        stmt = select(CatalogItem.id).where(column == value)
        if exclude_id is not None:
            stmt = stmt.where(CatalogItem.id != exclude_id)
        return session.scalar(stmt) is not None

    if taken(CatalogItem.article, article):
        raise Conflict("Артикул уже занят другой позицией", title="Артикул занят")
    if code1c and taken(CatalogItem.code1c, code1c):
        raise Conflict("Код 1С уже занят другой позицией", title="Код 1С занят")


def _commit(session: Session, conflict_message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # Гонка двух одновременных созданий: проверка выше прошла, база — нет.
        raise Conflict(conflict_message) from exc
