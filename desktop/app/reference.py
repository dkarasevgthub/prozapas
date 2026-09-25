"""Справочники, которые нужны почти каждому экрану.

Склады, роли, разделы и люди приходят одним запросом `/bootstrap` при входе и
живут в памяти до выхода. Меняются они редко, а спрашивать их на каждой
перерисовке — это лишний поход в сеть на каждое нажатие фильтра.
"""
from __future__ import annotations

from . import api
from .api.errors import ApiError
from .session import session

_data: dict = {}


def load(force: bool = False) -> bool:
    """Загрузить справочники. Зовётся после входа и при `Обновить`."""
    global _data
    if _data and not force:
        return True
    try:
        _data = api.client.bootstrap()
    except ApiError:
        _data = {}
        return False
    return True


def clear() -> None:
    global _data
    _data = {}


def _ensure() -> dict:
    if not _data:
        load()
    return _data


# ── склады ────────────────────────────────────────────────────
def warehouses(exclude_own: bool = False) -> list[dict]:
    items = _ensure().get("warehouses", [])
    if exclude_own and session.warehouse_id is not None:
        return [w for w in items if w["id"] != session.warehouse_id]
    return list(items)


def warehouse(warehouse_id: int) -> dict | None:
    for w in _ensure().get("warehouses", []):
        if w["id"] == warehouse_id:
            return w
    return None


def warehouse_name(warehouse_id: int, dash: str = "—") -> str:
    w = warehouse(warehouse_id)
    return w["name"] if w else dash


def responsible(warehouse_id: int) -> dict | None:
    """Ответственный за склад — карточка заказа показывает обе стороны."""
    w = warehouse(warehouse_id)
    return (w or {}).get("responsible")


# ── люди ──────────────────────────────────────────────────────
def people(warehouse_id: int | None = None) -> list[dict]:
    """Сотрудники для выпадающих фильтров: id и имя, без прав и почты.

    Список приходит в `/bootstrap`, а не из `/users`: тот доступен только
    администратору, а фильтр «Ответственный» нужен всем.
    """
    items = _ensure().get("users", [])
    if warehouse_id is not None:
        items = [u for u in items if u.get("warehouse_id") == warehouse_id]
    return sorted(items, key=lambda u: u.get("name", ""))


# ── роли и разделы ────────────────────────────────────────────
def roles() -> list[dict]:
    return list(_ensure().get("roles", []))


def role_label(code: str) -> str:
    # Подпись, а не данные: неизвестная роль показывается кодом, а не падает.
    for r in _ensure().get("roles", []):
        if r.get("code") == code:
            return r.get("label") or code
    return code


def sections() -> list[str]:
    return list(_ensure().get("sections", []))


def section_label(code: str) -> str:
    """Разделы подписаны в боковом меню — второй словарь названий не нужен."""
    from .sidebar import NAV_ITEMS
    for key, label, _icon in NAV_ITEMS:
        if key == code:
            return label
    return code
