"""Обмен с 1С: что вернуть после загрузки файла.

Импорт не падает целиком из-за одной плохой строки, но и не молчит о ней:
счётчики говорят, что применилось, `issues` — что именно пропущено и почему.
Без второго кладовщик видел бы «загружено 118 из 120» и не знал, каких двух
позиций не хватает.
"""
from __future__ import annotations

from pydantic import Field

from .common import Schema


class ExchangeIssue(Schema):
    """Позиция файла, потребовавшая внимания: пропущенная или применённая
    не целиком."""

    index: int = Field(description="Номер позиции в файле, считая с первой")
    ref: str = Field(description="Как позиция названа в файле: код 1С или артикул")
    reason: str = Field(description="Что именно не так")


class ImportResult(Schema):
    """Итог загрузки. Сумма четырёх счётчиков равна числу позиций в файле.

    Для остатков «создано» — позиции, у которых на этом складе ещё не было
    строки остатка.
    """

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    issues: list[ExchangeIssue] = Field(default_factory=list)
