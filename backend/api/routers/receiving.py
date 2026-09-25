"""Приёмка — 5 операций, раздел 6.4 api.md."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from database.models import Section, UserAccount

from ..deps import IfMatch, PageParams, SessionDep, etag, require
from ..schemas.common import Page
from ..schemas.orders import Order as OrderOut
from ..schemas.receiving import (ReceiptDetail, ReceiptListItem,
                                 ReceiptResult, ReceiveBody, ReceivedBox)
from ..services import receiving, transitions
from ..schemas.common import UserBrief, WarehouseBrief

router = APIRouter(tags=["Приёмка"])


@router.get("/receipts", response_model=Page[ReceiptListItem],
            summary="Список приёмок")
def list_receipts(session: SessionDep,
                  user: Annotated[UserAccount, require(Section.RECEIVING)],
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
                  ) -> Page[ReceiptListItem]:
    limit, offset = page
    rows, total = receiving.list_receipts(
        session, wh=user.warehouse_id, status=status, q=q,
        weight_min=weight_min, weight_max=weight_max,
        created_from=created_from, created_to=created_to,
        shipped_from=shipped_from, shipped_to=shipped_to,
        limit=limit, offset=offset)
    items = [ReceiptListItem(
        order_id=order.id, number=order.number, status=receipt.status,
        from_warehouse=WarehouseBrief.of(order.from_warehouse),
        boxes_total=int(boxes_total), boxes_received=int(boxes_received),
        weight_expected=float(expected_w), weight_actual=float(actual_w),
        responsible=UserBrief.of(receipt.responsible),
        created_at=order.created_at, shipped_at=order.shipped_at)
        for receipt, order, boxes_total, boxes_received,
            expected_w, actual_w in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/receipts/{order_id}", response_model=ReceiptDetail,
            summary="Карточка приёмки")
def get_receipt(session: SessionDep,
                user: Annotated[UserAccount, require(Section.RECEIVING)],
                order_id: int, response: Response) -> ReceiptDetail:
    receipt, order, boxes = receiving.get_receipt(session, order_id,
                                                  user.warehouse_id)
    response.headers["ETag"] = etag(receipt.version)
    return ReceiptDetail(order=OrderOut.of(order), status=receipt.status,
                         responsible=UserBrief.of(receipt.responsible),
                         boxes=[ReceivedBox.of(b) for b in boxes],
                         version=receipt.version)


@router.post("/receipts/{order_id}/boxes/{barcode}/receive",
             response_model=ReceivedBox,
             summary="Отметить коробку принятой")
def receive_box(session: SessionDep,
                user: Annotated[UserAccount,
                                require(Section.RECEIVING, edit=True)],
                order_id: int, barcode: str,
                body: ReceiveBody) -> ReceivedBox:
    box = receiving.receive(session, order_id, barcode, user,
                            body.actual_weight)
    return ReceivedBox.of(box)


@router.delete("/receipts/{order_id}/boxes/{barcode}/receive", status_code=204,
               response_model=None, summary="Отменить скан")
def cancel_receive(session: SessionDep,
                   user: Annotated[UserAccount,
                                   require(Section.RECEIVING, edit=True)],
                   order_id: int, barcode: str) -> None:
    receiving.cancel_receive(session, order_id, barcode, user)


@router.post("/receipts/{order_id}/complete", response_model=ReceiptResult,
             summary="Завершить приёмку")
def complete_receipt(session: SessionDep,
                     user: Annotated[UserAccount,
                                     require(Section.RECEIVING, edit=True)],
                     order_id: int, version: IfMatch) -> ReceiptResult:
    order, _receipt, missing = transitions.complete(session, order_id, user,
                                                    version)
    return ReceiptResult(order=OrderOut.of(order),
                         missing=[ReceivedBox.of(b) for b in missing])