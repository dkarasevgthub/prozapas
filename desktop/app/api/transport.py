"""Транспорт до сервера: запросы, токены, ошибки.

Сделан на `urllib` из стандартной библиотеки — по тем же соображениям, что и
служба устройств: приложение собирается в один exe, и каждая лишняя зависимость
усложняет сборку. Для JSON-API этого хватает.

Запросы синхронные: сервер стоит на том же складе, ответ приходит за единицы
миллисекунд, и экраны читают данные прямой строкой, без колбэков. Если сервер
окажется далеко, отсюда же вырастет фоновое выполнение — менять придётся только
этот файл.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .errors import ApiError, NetworkError, Unauthorized, from_status

#: Умолчания. Рабочее место настраивается через `app.config` — переменной
#: окружения или `.env` рядом с программой.
DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"
DEFAULT_TIMEOUT = 10.0


class Transport:
    """Один разговор с сервером: адрес, токены, разбор ответов."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *,
                 timeout: float = DEFAULT_TIMEOUT,
                 login_timeout: float = 5.0, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Ожидание входа короче обычного: человек смотрит на экран и ждёт.
        self.login_timeout = login_timeout
        self._access: str | None = None
        self._refresh: str | None = None
        self._ssl: ssl.SSLContext | None = None
        self.set_verify_tls(verify_tls)

    def set_verify_tls(self, verify: bool) -> None:
        """Сертификат склада может быть не из общего доверия — внутренний CA.

        `None` означает «проверять по системному хранилищу»: urllib подставит
        контекст по умолчанию сам.
        """
        self._ssl = None if verify else ssl._create_unverified_context()

    # ── токены ────────────────────────────────────────────────
    @property
    def authorized(self) -> bool:
        return self._access is not None

    @property
    def refresh_token(self) -> str | None:
        """Нужен, чтобы сохранить сессию между запусками при «запомнить меня»."""
        return self._refresh

    def set_tokens(self, access: str | None, refresh: str | None) -> None:
        self._access = access
        self._refresh = refresh

    def clear(self) -> None:
        self._access = self._refresh = None

    # ── запросы ───────────────────────────────────────────────
    def get(self, path: str, **params):
        return self.request("GET", path, params=params)

    def post(self, path: str, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def patch(self, path: str, body=None, **kw):
        return self.request("PATCH", path, body=body, **kw)

    def put(self, path: str, body=None, **kw):
        return self.request("PUT", path, body=body, **kw)

    def delete(self, path: str, **kw):
        return self.request("DELETE", path, **kw)

    def request(self, method: str, path: str, *, params: dict | None = None,
                body=None, if_match: int | str | None = None,
                idempotency_key: str | None = None, timeout: float | None = None,
                _retry: bool = True):
        """Выполнить запрос. Возвращает разобранный JSON или None на 204.

        При 401 один раз пробует обновить пару токенов и повторить: истёкший
        access — обычное дело раз в пятнадцать минут, и пользователь не должен
        об этом знать.
        """
        url = self._url(path, params)
        headers = {"Accept": "application/json"}
        if self._access:
            headers["Authorization"] = f"Bearer {self._access}"
        if if_match is not None:
            headers["If-Match"] = f'"{if_match}"'
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout,
                                        context=self._ssl) as resp:
                return self._decode(resp.status, resp.read(), resp.headers)
        except urllib.error.HTTPError as exc:
            payload = self._problem(exc)
            if exc.code == 401 and _retry and self._refresh:
                if self._try_refresh():
                    return self.request(method, path, params=params, body=body,
                                        if_match=if_match,
                                        idempotency_key=idempotency_key,
                                        timeout=timeout, _retry=False)
            raise from_status(exc.code, payload) from None
        except urllib.error.URLError as exc:
            raise NetworkError(f"сервер недоступен: {exc.reason}") from None
        except TimeoutError:
            raise NetworkError("сервер не ответил вовремя") from None

    # ── вход ──────────────────────────────────────────────────
    def login(self, login: str, password: str) -> dict:
        payload = self.request("POST", "/auth/login",
                               body={"login": login, "password": password},
                               timeout=self.login_timeout, _retry=False)
        self.set_tokens(payload.get("access"), payload.get("refresh"))
        return payload

    def logout(self) -> None:
        try:
            if self._refresh:
                self.request("POST", "/auth/logout", body={"refresh": self._refresh},
                             _retry=False)
        except ApiError:
            pass          # сервер недоступен — локально всё равно выходим
        finally:
            self.clear()

    def resume(self, refresh: str) -> bool:
        """Поднять сессию по сохранённому refresh — «запомнить меня»."""
        self.set_tokens(None, refresh)
        return self._try_refresh()

    def _try_refresh(self) -> bool:
        try:
            pair = self.request("POST", "/auth/refresh",
                                body={"refresh": self._refresh}, _retry=False)
        except ApiError:
            self.clear()
            return False
        self.set_tokens(pair.get("access"), pair.get("refresh"))
        return True

    # ── внутреннее ────────────────────────────────────────────
    def _url(self, path: str, params: dict | None) -> str:
        url = f"{self.base_url}{path}"
        if not params:
            return url
        pairs = []
        for key, value in params.items():
            if value is None or value == "":
                continue
            if isinstance(value, bool):
                value = "true" if value else "false"
            if isinstance(value, (list, tuple, set)):
                pairs.extend((key, str(v)) for v in value)   # ?status=a&status=b
            else:
                pairs.append((key, str(value)))
        return f"{url}?{urllib.parse.urlencode(pairs)}" if pairs else url

    @staticmethod
    def _decode(status: int, raw: bytes, headers):
        if status == 204 or not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ApiError(f"сервер вернул не JSON (код {status})", status=status) from None

    @staticmethod
    def _problem(exc: urllib.error.HTTPError) -> dict:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 — тело может быть пустым или не JSON
            return {}


def new_idempotency_key() -> str:
    """Ключ для создания заказа: повтор из-за таймаута не создаст второй."""
    return str(uuid.uuid4())
