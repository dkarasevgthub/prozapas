"""Printer selection for this Windows workstation, shared by app accounts."""
import json

from devices.config import list_printers

from . import config
from .session import _state_dir, session


def _settings_path():
    return _state_dir() / "equipment.json"


def selected_printer() -> str:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        name = data.get("printer_name") if isinstance(data, dict) else None
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, ValueError):
        pass
    return config.get("PROZAPAS_PRINTER_NAME").strip()


def save_printer(name: str) -> None:
    if not session.is_admin:
        raise PermissionError("Настройка принтера доступна только администратору.")
    name = name.strip()
    if not name or name not in list_printers():
        raise ValueError("Принтер не найден в Windows. Обновите список и выберите принтер.")
    path = _settings_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"printer_name": name}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
