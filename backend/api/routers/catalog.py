"""Справочник — 5 операций, раздел 6.6 api.md."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from database.models import Section, UserAccount

from ..deps import PageParams, SessionDep, require
from ..schemas.catalog import CatalogCreate, CatalogItem, CatalogUpdate
from ..schemas.common import Page
from ..services import catalog

router = APIRouter(tags=["Справочник"])


@router.get("/catalog", response_model=Page[CatalogItem],
            summary="Номенклатура")
def list_items(session: SessionDep,
               user: Annotated[UserAccount, require(Section.CATALOG)],
               page: PageParams,
               q: Annotated[str | None, Query(max_length=100)] = None,
               archived: Annotated[bool, Query()] = False,
               ) -> Page[CatalogItem]:
    limit, offset = page
    items, total = catalog.list_items(session, q=q, archived=archived,
                                      limit=limit, offset=offset)
    return Page(items=[CatalogItem.of(i) for i in items],
                total=total, limit=limit, offset=offset)


@router.post("/catalog", response_model=CatalogItem, status_code=201,
             summary="Добавить позицию")
def add_item(session: SessionDep,
             user: Annotated[UserAccount, require(Section.CATALOG, edit=True)],
             body: CatalogCreate) -> CatalogItem:
    item = catalog.create(session, article=body.article, code1c=body.code1c,
                          name=body.name, unit=body.unit,
                          unit_weight=body.unit_weight, user_id=user.id)
    return CatalogItem.of(item)


@router.get("/catalog/{id}", response_model=CatalogItem,
            summary="Карточка позиции")
def get_item(session: SessionDep,
             user: Annotated[UserAccount, require(Section.CATALOG)],
             id: int) -> CatalogItem:
    return CatalogItem.of(catalog.get(session, id))


@router.patch("/catalog/{id}", response_model=CatalogItem,
              summary="Изменить позицию")
def patch_item(session: SessionDep,
               user: Annotated[UserAccount, require(Section.CATALOG, edit=True)],
               id: int, body: CatalogUpdate) -> CatalogItem:
    item = catalog.update(session, id, body.model_dump(exclude_unset=True),
                          user_id=user.id)
    return CatalogItem.of(item)


@router.post("/catalog/{id}/archive", status_code=204, response_model=None,
             summary="Архивировать позицию")
def archive_item(session: SessionDep,
                 user: Annotated[UserAccount, require(Section.CATALOG, edit=True)],
                 id: int) -> None:
    catalog.archive(session, id, user_id=user.id)