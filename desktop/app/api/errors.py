"""Ошибки API.

Сервер отвечает в формате RFC 9457 (`application/problem+json`), где машина
читает `type`, а человеку показывается `title`. Здесь это разворачивается в
исключения, по которым экран может ветвиться, не разбирая строки.
"""
from __future__ import annotations


class ApiError(Exception):
    """Основание для всех ошибок обмена с сервером."""

    def __init__(self, message: str, *, status: int = 0, problem: dict | None = None):
        super().__init__(message)
        self.status = status
        self.problem = problem or {}

    @property
    def type(self) -> str:
        """Машиночитаемый вид ошибки — по нему и надо ветвиться."""
        return self.problem.get("type", "")

    @property
    def title(self) -> str:
        """Текст для пользователя."""
        return self.problem.get("title") or str(self)

    def detail(self, key: str, default=None):
        """Дополнительное поле проблемы: список позиций, доступный остаток и т.п."""
        return self.problem.get(key, default)


class NetworkError(ApiError):
    """Сервер недоступен: нет сети, отказ соединения, истекло ожидание."""


class BadRequest(ApiError):
    """400 — тело или параметры не разобрались."""


class Unauthorized(ApiError):
    """401 — нет токена, истёк или отозван."""


class Forbidden(ApiError):
    """403 — нет прав на раздел либо запрещено правилом."""


class NotFound(ApiError):
    """404 — объекта нет, удалён или принадлежит чужому складу."""


class Conflict(ApiError):
    """409 — версия разошлась, недопустимый переход, нехватка остатка."""


class Unprocessable(ApiError):
    """422 — нарушено доменное правило."""


class PreconditionRequired(ApiError):
    """428 — изменение без If-Match."""


class ServiceUnavailable(ApiError):
    """503 — база недоступна."""


BY_STATUS = {
    400: BadRequest,
    401: Unauthorized,
    403: Forbidden,
    404: NotFound,
    409: Conflict,
    422: Unprocessable,
    428: PreconditionRequired,
    503: ServiceUnavailable,
}


def from_status(status: int, problem: dict) -> ApiError:
    cls = BY_STATUS.get(status, ApiError)
    title = problem.get("title") or f"Ошибка {status}"
    return cls(title, status=status, problem=problem)
