"""Клиент серверного API.

    from app import api

    api.configure("http://sklad:8000/api/v1")
    api.client.login("i.kuznetsov", "пароль")
    orders = api.client.orders("outgoing", status=["created", "processing"])

Адрес, тайм-ауты и проверка сертификата берутся из настроек рабочего места
(`app.config`): переменная окружения, потом `.env` рядом с программой. Так склад
настраивается без пересборки.
"""
from __future__ import annotations

from .. import config
from .errors import (
    ApiError,
    BadRequest,
    Conflict,
    Forbidden,
    NetworkError,
    NotFound,
    PreconditionRequired,
    ServiceUnavailable,
    Unauthorized,
    Unprocessable,
)
from .resources import Resources
from .transport import DEFAULT_BASE_URL, Transport

__all__ = [
    "ApiError", "BadRequest", "Conflict", "Forbidden", "NetworkError",
    "NotFound", "PreconditionRequired", "ServiceUnavailable", "Unauthorized",
    "Unprocessable", "Resources", "Transport",
    "client", "transport", "configure", "available",
]

BASE_URL_ENV = "PROZAPAS_API"

transport = Transport(
    config.get(BASE_URL_ENV, DEFAULT_BASE_URL),
    timeout=config.number("PROZAPAS_API_TIMEOUT"),
    login_timeout=config.number("PROZAPAS_API_LOGIN_TIMEOUT"),
    verify_tls=config.flag("PROZAPAS_TLS_VERIFY"),
)
client = Resources(transport)


def configure(base_url: str, *, timeout: float | None = None,
              verify_tls: bool = True) -> None:
    """Переставить адрес сервера, сохранив текущую сессию."""
    transport.base_url = base_url.rstrip("/")
    if timeout is not None:
        transport.timeout = timeout
    transport.set_verify_tls(verify_tls)


def available() -> bool:
    """Отвечает ли сервер. Для экрана входа и индикатора связи."""
    try:
        client.health()
        return True
    except ApiError:
        return False
