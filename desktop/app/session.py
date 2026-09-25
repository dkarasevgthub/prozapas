"""Кто вошёл, на каком складе и что ему можно.

«Текущий пользователь» приходит с сервера вместе с токенами и живёт только в
памяти процесса. На диск уходит один refresh — и только если попросили запомнить.

Склад пользователя здесь же. Он нигде не передаётся в запросах — сервер берёт
его из токена, — но интерфейсу знать его надо: боковая панель показывает, а
«Новый заказ» не должен предлагать заказ у самого себя.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import api
from .api.errors import ApiError

VIEW = "view"
EDIT = "edit"


def _state_dir() -> Path:
    """Каталог под данные пользователя — не рядом с кодом: приложение ставится
    в Program Files, куда писать нельзя."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / "ProZapas"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Session:
    def __init__(self):
        self.user: dict | None = None
        self.warehouse: dict | None = None
        self._perms: dict[str, str] = {}
        self._file = _state_dir() / "session.json"

    # ── состояние ─────────────────────────────────────────────
    @property
    def authorized(self) -> bool:
        return self.user is not None

    @property
    def user_id(self) -> int | None:
        return self.user["id"] if self.user else None

    @property
    def warehouse_id(self) -> int | None:
        return self.warehouse["id"] if self.warehouse else None

    def display_name(self) -> str:
        """«Кузнецов Игорь Александрович» → «Кузнецов И.А.» — как в панели."""
        if not self.user:
            return "—"
        parts = self.user.get("full_name", "").split()
        if not parts:
            return "—"
        tail = "".join(f"{p[0]}." for p in parts[1:3] if p)
        return f"{parts[0]} {tail}".strip()

    def can(self, section: str, access: str = VIEW) -> bool:
        """Права приходят в токене, поэтому проверка ничего не спрашивает у сервера."""
        level = self._perms.get(section)
        if level is None:
            return False
        return True if access == VIEW else level == EDIT

    # ── вход и выход ──────────────────────────────────────────
    def login(self, login: str, password: str, remember: bool = False) -> None:
        """Поднять сессию. Ошибки пробрасываются: их разбирает экран входа."""
        payload = api.client.login(login, password)
        self._apply(payload)
        self._save(remember)

    def resume(self) -> bool:
        """Продолжить сессию по сохранённому refresh — «запомнить меня»."""
        saved = self._load()
        token = saved.get("refresh")
        if not token:
            return False
        if not api.transport.resume(token):
            self.forget()
            return False
        try:
            self._apply(api.client.me())
        except ApiError:
            self.forget()
            return False
        self._save(True)          # refresh был заменён ротацией
        return True

    def logout(self) -> None:
        try:
            api.client.logout()
        finally:
            self.user = self.warehouse = None
            self._perms = {}
            self.forget()

    def forget(self) -> None:
        self._file.unlink(missing_ok=True)

    # ── внутреннее ────────────────────────────────────────────
    def _apply(self, payload: dict) -> None:
        self.user = payload.get("user")
        self.warehouse = payload.get("warehouse")
        self._perms = {p["section"]: (EDIT if p["can_edit"] else VIEW)
                       for p in payload.get("permissions", [])
                       if p.get("can_view") or p.get("can_edit")}

    def _save(self, remember: bool) -> None:
        if not remember:
            self.forget()
            return
        # Refresh живёт тридцать дней и лежит открытым текстом. На общем
        # рабочем месте «запомнить меня» лучше не включать.
        self._file.write_text(
            json.dumps({"refresh": api.transport.refresh_token}), encoding="utf-8")

    def _load(self) -> dict:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


session = Session()
