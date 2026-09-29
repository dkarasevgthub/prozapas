"""Писатель в audit_log.

Запись делает тот сервис, который меняет данные, и в той же транзакции:
сбой посередине не оставляет ни действия, ни строки журнала.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from database.models import AuditLog


def record(session: Session, *, entity: str, entity_id: int, action: str,
           user_id: int | None = None,
           before: dict[str, Any] | None = None,
           after: dict[str, Any] | None = None) -> None:
    """Без собственного коммита — фиксирует основной commit() сервиса."""
    session.add(AuditLog(entity=entity, entity_id=entity_id, action=action,
                         user_id=user_id, before=before, after=after))
