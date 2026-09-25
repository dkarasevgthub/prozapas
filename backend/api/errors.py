"""Ошибки в формате RFC 9457 — `application/problem+json`.

Клиент разбирает ровно эти поля: `type`, `title`, `status`, `detail`
(desktop/app/api/errors.py). `type` — стабильный URI: по нему можно ветвиться в
коде, в отличие от `title`, который меняется вместе с формулировкой.

`title` попадает человеку на экран без перевода, поэтому он по-русски всегда —
включая ответы, которые сочиняет не наш код, а маршрутизатор.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

BASE = "https://prozapas/errors/"
CONTENT_TYPE = "application/problem+json"


class ApiProblem(Exception):
    """Отказ, о котором клиенту надо рассказать словами."""

    status = 500
    kind = "internal"
    title = "Внутренняя ошибка"

    def __init__(self, detail: str | None = None, *, title: str | None = None,
                 **extra):
        super().__init__(detail or self.title)
        self.detail = detail
        if title:
            self.title = title
        self.extra = extra

    def payload(self) -> dict:
        body = {"type": BASE + self.kind, "title": self.title,
                "status": self.status}
        if self.detail:
            body["detail"] = self.detail
        body.update(self.extra)
        return body


class BadRequest(ApiProblem):
    status, kind, title = 400, "bad-request", "Некорректный запрос"


class Unauthorized(ApiProblem):
    status, kind, title = 401, "unauthorized", "Требуется вход"


class InvalidCredentials(Unauthorized):
    kind = "invalid-credentials"
    # Одна формулировка на «нет такого логина» и «неверный пароль»: иначе по
    # ответу перебирают учётные записи. Клиент показывает её как есть.
    title = "Неверный логин или пароль"


class SessionExpired(Unauthorized):
    kind, title = "session-expired", "Сессия истекла, войдите снова"


class Forbidden(ApiProblem):
    status, kind, title = 403, "forbidden", "Недостаточно прав"


class AccountBlocked(Forbidden):
    kind, title = "account-blocked", "Учётная запись заблокирована"


class NotFound(ApiProblem):
    status, kind, title = 404, "not-found", "Не найдено"


class Conflict(ApiProblem):
    status, kind, title = 409, "conflict", "Данные уже изменили"

class InsufficientStock(Conflict):
    kind, title = "insufficient-stock", "Недостаточно свободного остатка"

class InvalidTransition(Conflict):
    kind, title = "invalid-transition", "Недопустимый переход статуса"

class Unprocessable(ApiProblem):
    status, kind, title = 422, "unprocessable", "Данные не прошли проверку"


class PreconditionRequired(ApiProblem):
    status, kind, title = 428, "precondition-required", "Нужен заголовок If-Match"


class Unavailable(ApiProblem):
    status, kind, title = 503, "unavailable", "Сервис недоступен"


#: Ответы, которые сочиняет не наш код: маршрутизатор, разбор заголовков.
#: Их англоязычный `detail` человеку показывать нельзя.
BY_STATUS: dict[int, type[ApiProblem]] = {
    400: BadRequest, 401: Unauthorized, 403: Forbidden, 404: NotFound,
    409: Conflict, 422: Unprocessable, 428: PreconditionRequired,
    503: Unavailable,
}

METHOD_NOT_ALLOWED = "Метод не поддерживается этим адресом"


def problem_response(problem: ApiProblem) -> JSONResponse:
    return JSONResponse(problem.payload(), status_code=problem.status,
                        media_type=CONTENT_TYPE)


def install(app) -> None:
    """Повесить обработчики так, чтобы наружу не уходил ни один голый JSON."""
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException

    @app.exception_handler(ApiProblem)
    async def _api_problem(_request: Request, exc: ApiProblem) -> JSONResponse:
        return problem_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request,
                          exc: RequestValidationError) -> JSONResponse:
        # Разбор по полям кладём в detail: клиент покажет title, а detail
        # попадёт в журнал и подскажет, что именно не сошлось.
        fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) or "тело"
                           for e in exc.errors())
        return problem_response(Unprocessable(f"Не приняты поля: {fields}"))

    @app.exception_handler(HTTPException)
    async def _http(_request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 405:
            problem = BadRequest(title=METHOD_NOT_ALLOWED)
            problem.status = 405
            problem.kind = "method-not-allowed"
        else:
            problem = BY_STATUS.get(exc.status_code, ApiProblem)()
            problem.status = exc.status_code
        response = problem_response(problem)
        for name, value in (getattr(exc, "headers", None) or {}).items():
            response.headers[name] = value
        return response
