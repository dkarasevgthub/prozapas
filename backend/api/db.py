"""Движок и сессия. Единственное место, где создаётся Session."""
from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings


@lru_cache
def _factory() -> sessionmaker[Session]:
    cfg = settings()
    engine = create_engine(
        cfg.database_url,
        pool_size=cfg.pool_size,
        max_overflow=cfg.max_overflow,
        pool_pre_ping=True,        # соединение могло умереть, пока лежало в пуле
        future=True,
    )
    return sessionmaker(engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI: сессия на запрос.

    Транзакцию здесь не открываем. Её границу задаёт тот, кто пишет, — см.
    services/. Чтение обходится неявной транзакцией SQLAlchemy.
    """
    with _factory()() as session:
        yield session
