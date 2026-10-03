"""Отгрузка — 5 операций, раздел 6.3 api.md."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from database.models import Section, UserAccount

from ..deps import IfMatch, PageParams, SessionDep, etag, require
from ..schemas.common import Page
from ..schemas.shipping import (Box, BoxCreate, ShipmentDetail,
                                ShipmentListItem, ShipResult, ShortageLine)
from ..services import shipping, transitions
from ..schemas.orders import Order as OrderOut
router = APIRouter(tags=["Отгрузка"])


@router.get("/shipments", response_model=Page[ShipmentListItem],
            summary="Список отгрузок")
def list_shipments(session: SessionDep,
                   user: Annotated[UserAccount, require(Section.SHIPPING)],
                   page: PageParams,
                   status: Annotated[Literal["waiting", "progress", "done"]
                                     | None, Query()] = None,
                   q: Annotated[str | None, Query(max_length=64)] = None,
                   weight_min: Annotated[float | None, Query()] = None,
                   weight_max: Annotated[float | None, Query()] = None,
                   created_from: Annotated[date | None, Query()] = None,
                   created_to: Annotated[date | None, Query()] = None,
                   shipped_from: Annotated[date | None, Query()] = None,
                   shipped_to: Annotated[date | None, Query()] = None,
                   ) -> Page[ShipmentListItem]:
    limit, offset = page
    rows, total = shipping.list_shipments(
        session, wh=user.warehouse_id, status=status, q=q,
        weight_min=weight_min, weight_max=weight_max,
        created_from=created_from, created_to=created_to,
        shipped_from=shipped_from, shipped_to=shipped_to,
        limit=limit, offset=offset)
    items = []
    for shipment, order, _expected, positions_total, packed_weight, done in rows:
        item = ShipmentListItem.of(shipment, order, float(_expected),
                                   float(packed_weight), int(done))
        item.positions_total = int(positions_total)
        items.append(item)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/shipments/{order_id}", response_model=ShipmentDetail,
            summary="Карточка отгрузки")
def get_shipment(session: SessionDep,
                 user: Annotated[UserAccount, require(Section.SHIPPING)],
                 order_id: int, response: Response) -> ShipmentDetail:
    ship = shipping.get_shipment(session, order_id, user.warehouse_id)
    response.headers["ETag"] = etag(ship.version)
    return ShipmentDetail.of(ship)


@router.post("/shipments/{order_id}/boxes", response_model=Box, status_code=201,
             summary="Упаковать коробку")
def create_box(session: SessionDep,
               user: Annotated[UserAccount,
                               require(Section.SHIPPING, edit=True)],
               order_id: int, body: BoxCreate) -> Box:
    box = shipping.create_box(session, order_id, user, article=body.article,
                              qty=body.qty, weight=body.weight)
    return Box.of(box)


@router.delete("/shipments/{order_id}/boxes/{box_id}", status_code=204,
               response_model=None, summary="Удалить коробку")
def delete_box(session: SessionDep,
               user: Annotated[UserAccount,
                               require(Section.SHIPPING, edit=True)],
               order_id: int, box_id: int) -> None:
    shipping.delete_box(session, order_id, box_id, user)


@router.post("/shipments/{order_id}/ship", response_model=ShipResult,
             summary="Отгрузить")
def ship(session: SessionDep,
         user: Annotated[UserAccount, require(Section.SHIPPING, edit=True)],
         order_id: int, version: IfMatch) -> ShipResult:
    order, _ship_doc, shortage = transitions.ship(session, order_id, user,
                                                  version)
    return ShipResult(order=OrderOut.of(order),
                      shortage=[ShortageLine(**s) for s in shortage])
