"""Заказы — 7 операций, раздел 6.2 api.md."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Response

from database.models import OrderStatus, Section, UserAccount

from ..deps import IfMatch, PageParams, SessionDep, etag, require
from ..schemas.common import Page
from ..schemas.orders import Order as OrderOut
from ..schemas.orders import OrderCreate, OrderListItem, ReasonBody
from ..schemas.service import StatusEvent
from ..services import orders, transitions
from ..services.orders import Position

from ..schemas.common import UserBrief 

router = APIRouter(tags=["Заказы"])


@router.get("/orders", response_model=Page[OrderListItem],
            summary="Список заказов")
def list_orders(session: SessionDep,
                user: Annotated[UserAccount, require(Section.ORDERS)],
                page: PageParams,
                tab: Annotated[Literal["outgoing", "incoming"], Query()],
                status: Annotated[list[OrderStatus] | None, Query()] = None,
                warehouse_id: Annotated[int | None, Query()] = None,
                responsible_id: Annotated[int | None, Query()] = None,
                created_from: Annotated[date | None, Query()] = None,
                created_to: Annotated[date | None, Query()] = None,
                q: Annotated[str | None, Query(max_length=64)] = None,
                ) -> Page[OrderListItem]:
    limit, offset = page
    rows, total = orders.list_orders(
        session, wh=user.warehouse_id, tab=tab, statuses=status,
        warehouse_id=warehouse_id, responsible_id=responsible_id,
        created_from=created_from, created_to=created_to, q=q,
        limit=limit, offset=offset)
    items = [OrderListItem.of(order,
                              order.from_warehouse if tab == "outgoing"
                              else order.to_warehouse, count)
             for order, count in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/orders", response_model=OrderOut, status_code=201,
             summary="Создать заказ")
def create_order(session: SessionDep,
                 user: Annotated[UserAccount,
                                 require(Section.ORDERS, edit=True)],
                 body: OrderCreate,
                 idem_key: Annotated[str | None,
                                     Header(alias="Idempotency-Key")] = None,
                 ) -> OrderOut:
    return orders.create(
        session, user=user, idem_key=idem_key,
        from_warehouse_id=body.from_warehouse_id, comment=body.comment,
        positions=[Position(p.article, p.qty) for p in body.positions],
        serialize=lambda o: OrderOut.of(o).model_dump(mode="json"))


@router.get("/orders/{id}", response_model=OrderOut, summary="Карточка заказа")
def get_order(session: SessionDep,
              user: Annotated[UserAccount, require(Section.ORDERS)],
              id: int, response: Response) -> OrderOut:
    order = orders.get_order(session, id, user.warehouse_id)
    response.headers["ETag"] = etag(order.version)
    return OrderOut.of(order)


@router.get("/orders/{id}/history", response_model=list[StatusEvent],
            summary="История статусов")
def get_history(session: SessionDep,
                user: Annotated[UserAccount, require(Section.ORDERS)],
                id: int) -> list[StatusEvent]:
    rows = orders.history(session, id, user.warehouse_id)
    order = orders.get_order(session, id, user.warehouse_id)
    return [StatusEvent(order_id=event.order_id, number=order.number,
                        status=event.status, reason=event.reason,
                        user=UserBrief.of(person), occurred_at=event.occurred_at)
            for event, person in rows]

@router.post("/orders/{id}/accept", response_model=OrderOut,
             summary="Принять заказ")
def accept(session: SessionDep,
           user: Annotated[UserAccount, require(Section.ORDERS, edit=True)],
           id: int, version: IfMatch) -> OrderOut:
    return OrderOut.of(transitions.accept(session, id, user, version))


@router.post("/orders/{id}/decline", response_model=OrderOut,
             summary="Отклонить заказ")
def decline(session: SessionDep,
            user: Annotated[UserAccount, require(Section.ORDERS, edit=True)],
            id: int, body: ReasonBody, version: IfMatch) -> OrderOut:
    return OrderOut.of(transitions.decline(session, id, user, version,
                                           body.reason))


@router.post("/orders/{id}/cancel", response_model=OrderOut,
             summary="Отменить заказ")
def cancel(session: SessionDep,
           user: Annotated[UserAccount, require(Section.ORDERS, edit=True)],
           id: int, body: ReasonBody, version: IfMatch) -> OrderOut:
    return OrderOut.of(transitions.cancel(session, id, user, version,
                                          body.reason))