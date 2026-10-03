"""Справочник. Имена классов — как в components.schemas openapi.json."""
from __future__ import annotations

from pydantic import Field

from .common import Schema


class CatalogCreate(Schema):
    article: str = Field(min_length=1, max_length=100)
    code1c: str | None = None
    name: str = Field(min_length=1, max_length=500)
    unit: str = Field(min_length=1, max_length=20)
    unit_weight: float = Field(default=0, ge=0)


class CatalogUpdate(Schema):
    code1c: str | None = None          # явный null — очистить код 1С
    name: str | None = Field(default=None, min_length=1, max_length=500)
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    unit_weight: float | None = Field(default=None, ge=0)


class CatalogItem(Schema):
    id: int
    article: str
    code1c: str | None
    name: str
    unit: str
    unit_weight: float
    is_archived: bool

    @classmethod
    def of(cls, item) -> "CatalogItem":
        return cls(id=item.id, article=item.article, code1c=item.code1c,
                   name=item.name, unit=item.unit,
                   unit_weight=float(item.unit_weight),
                   is_archived=item.is_archived)
