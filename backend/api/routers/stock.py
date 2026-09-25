"""Остатки — 5 операций, раздел 6.5 api.md.

Фиксированные пути (/summary, /operations) объявлены раньше /stock/{item_id} —
иначе FastAPI разобрал бы «summary» как item_id и вернул 422.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from database.models import Section, UserAccount

from ..deps import PageParams, SessionDep, require
from ..schemas.common import Page, WarehouseBrief
from ..schemas.stock import (Movement, StockByWarehouse, StockOperation,
                             StockRow, StockShare, StockSummary)
from ..services import inventory
from ..services.catalog import get as get_catalog_item

router = APIRouter(tags=["Остатки"])


@router.get("/stock", response_model=Page[StockRow],
            summary="Остатки по номенклатуре")
def list_stock(session: SessionDep,
               user: Annotated[UserAccount, require(Section.STOCK)],
               page: PageParams,
               q: Annotated[str | None, Query(max_length=100)] = None,
               warehouse_id: Annotated[int | None, Query()] = None,
               below_min: Annotated[bool, Query()] = False,
               in_stock: Annotated[bool | None, Query()] = None,
               ) -> Page[StockRow]:
    limit, offset = page
    rows, total = inventory.list_stock(
        session, default_wh=user.warehouse_id, warehouse_id=warehouse_id,
        q=q, below_min=below_min, in_stock=in_stock,
        limit=limit, offset=offset)
    by_item = inventory.shares_by_items(session,
                                        [r[0].item_id for r in rows])
    items = [StockRow(
        item_id=bal.item_id, article=item.article, code1c=item.code1c,
        name=item.name, unit=item.unit, qty=float(bal.qty),
        free=float(bal.qty - bal.reserved), reserved=float(bal.reserved),
        min_qty=float(bal.min_qty),
        by_warehouse=[StockShare(warehouse_id=s.warehouse_id,
                                 qty=float(s.qty),
                                 free=float(s.qty - s.reserved))
                      for s in by_item.get(bal.item_id, [])])
        for bal, item in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/stock/summary", response_model=StockSummary,
            summary="Четыре карточки экрана")
def stock_summary(session: SessionDep,
                  user: Annotated[UserAccount, require(Section.STOCK)]
                  ) -> StockSummary:
    return StockSummary(**inventory.summary(session, user.warehouse_id))


@router.post("/stock/operations", response_model=Movement, status_code=201,
             summary="Ручная операция")
def operation(session: SessionDep,
              user: Annotated[UserAccount,
                              require(Section.STOCK, edit=True)],
              body: StockOperation) -> Movement:
    entry = inventory.operation(
        session, user, article=body.article, warehouse_id=body.warehouse_id,
        type=body.type, qty=body.qty, comment=body.comment)
    return Movement.of(entry, user)


@router.get("/stock/{item_id}", response_model=list[StockByWarehouse],
            summary="Остаток по складам")
def stock_by_item(session: SessionDep,
                  user: Annotated[UserAccount, require(Section.STOCK)],
                  item_id: int) -> list[StockByWarehouse]:
    get_catalog_item(session, item_id)          # нет товара → 404
    return [StockByWarehouse(warehouse=WarehouseBrief.of(w), qty=float(b.qty),
                             free=float(b.qty - b.reserved),
                             reserved=float(b.reserved),
                             min_qty=float(b.min_qty))
            for b, w in inventory.stock_by_item(session, item_id,
                                                user.warehouse_id)]


@router.get("/stock/{item_id}/movements", response_model=Page[Movement],
            summary="История движений")
def movements(session: SessionDep,
              user: Annotated[UserAccount, require(Section.STOCK)],
              item_id: int, page: PageParams,
              type: Annotated[Literal["receipt", "shipment", "writeoff",
                                      "recount", "reserve", "unreserve"]
                             | None, Query()] = None) -> Page[Movement]:
    limit, offset = page
    get_catalog_item(session, item_id)          # нет товара → 404
    rows, total = inventory.movements(session, item_id=item_id,
                                      wh=user.warehouse_id, type=type,
                                      limit=limit, offset=offset)
    return Page(items=[Movement.of(m, p) for m, p in rows],
                total=total, limit=limit, offset=offset)