"""Сборка приложения.

Префикс `/api/v1` — как в openapi.json и в клиенте. Меняется только вместе с
`servers` в контракте.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from . import errors
from .routers import (auth, catalog, orders, receiving, service, shipping,
                      stock, users)

PREFIX = "/api/v1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="ProЗапас API",
    version="1.0.0",
    description="Складской учёт ООО «Сталкер Групп». Контракт — docs/api.md.",
    docs_url=f"{PREFIX}/docs",
    openapi_url=f"{PREFIX}/openapi.json",
    redoc_url=None,
)

errors.install(app)

for module in (service, auth, catalog, orders, shipping, receiving, stock,
               users):
    app.include_router(module.router, prefix=PREFIX)


def openapi() -> dict:
    """Схема с `servers`, как в docs/openapi.json."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version,
                         description=app.description, routes=app.routes)
    schema["servers"] = [{"url": PREFIX}]
    schema["paths"] = {path.removeprefix(PREFIX): item
                       for path, item in schema["paths"].items()}
    app.openapi_schema = schema
    return schema


app.openapi = openapi                             # type: ignore[method-assign]