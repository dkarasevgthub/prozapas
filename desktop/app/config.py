"""Настройки рабочего места.

Всё, что отличается от машины к машине: адрес сервера, тайм-ауты, имя канала
службы устройств, геометрия этикетки. В коде остаются только значения по
умолчанию, годные для разработки на своей машине.

Порядок старшинства:

    переменная окружения  →  файл .env  →  значение по умолчанию

Именно так, а не наоборот: `.env` лежит рядом с программой и задаёт настройку
склада, а окружение позволяет перебить её на одной машине или в одном запуске,
ничего не правя в файле.

Читаем сами, без python-dotenv: приложение собирается в один exe, и лишняя
зависимость усложняет сборку. Формат нужен минимальный — `КЛЮЧ=значение`,
`#` в начале строки комментарий.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: Имя файла рядом с программой. Путь можно задать явно через PROZAPAS_ENV_FILE.
ENV_FILE = ".env"
ENV_FILE_VAR = "PROZAPAS_ENV_FILE"

DEFAULTS: dict[str, str] = {
    # ── сервер ──
    "PROZAPAS_API": "http://127.0.0.1:8000/api/v1",
    "PROZAPAS_API_TIMEOUT": "10",
    "PROZAPAS_API_LOGIN_TIMEOUT": "5",
    "PROZAPAS_TLS_VERIFY": "true",
    # ── служба устройств ──
    "PROZAPAS_PIPE_NAME": "prozapas-devices",
    "PROZAPAS_IDLE_TIMEOUT": "30",
    "PROZAPAS_DEVICES_SERVICE": "",
    # ── этикетка коробки ──
    "PROZAPAS_LABEL_WIDTH_MM": "58",
    "PROZAPAS_LABEL_HEIGHT_MM": "40",
    "PROZAPAS_LABEL_DPI": "203",
}

_file_values: dict[str, str] = {}


def app_dir() -> Path:
    """Каталог программы: рядом с exe после сборки, рядом с пакетом при запуске из кода."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def env_path() -> Path:
    override = os.environ.get(ENV_FILE_VAR, "").strip()
    return Path(override) if override else app_dir() / ENV_FILE


def load(path: Path | None = None) -> dict[str, str]:
    """Прочитать .env. Отсутствие файла — не ошибка: годятся умолчания."""
    global _file_values
    target = path or env_path()
    values: dict[str, str] = {}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        _file_values = {}
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Кавычки вокруг значения снимаем: их ставят по привычке из shell.
        values[key.strip()] = value.strip().strip('"').strip("'")
    _file_values = values
    return values


def get(key: str, default: str | None = None) -> str:
    from_env = os.environ.get(key)
    if from_env is not None and from_env != "":
        return from_env
    if key in _file_values and _file_values[key] != "":
        return _file_values[key]
    if default is not None:
        return default
    return DEFAULTS.get(key, "")


def number(key: str, default: float | None = None) -> float:
    """Число из настройки. Мусор в файле не должен ронять запуск."""
    raw = get(key)
    try:
        return float(raw)
    except (TypeError, ValueError):
        fallback = default if default is not None else DEFAULTS.get(key, "0")
        return float(fallback)


def flag(key: str) -> bool:
    return get(key).strip().lower() in ("1", "true", "yes", "on", "да")


def summary() -> str:
    """Строка для журнала запуска: куда пойдём и откуда это взяли."""
    where = env_path()
    origin = str(where) if _file_values else "умолчания"
    return f"сервер {get('PROZAPAS_API')} · канал {get('PROZAPAS_PIPE_NAME')} · {origin}"


def describe() -> list[tuple[str, str, str]]:
    """(ключ, значение, откуда) — для окна диагностики и журнала запуска.

    Значения секретов здесь не бывает: пароли приложение не хранит, а токены
    живут в памяти и в сессионном файле.
    """
    rows = []
    for key in DEFAULTS:
        if os.environ.get(key):
            source = "окружение"
        elif _file_values.get(key):
            source = str(env_path())
        else:
            source = "по умолчанию"
        rows.append((key, get(key), source))
    return rows


# Читаем файл при импорте, а не из main(): `app.api` и `app.devices` берут
# настройки на уровне модуля, и при отложенной загрузке результат зависел бы от
# порядка импортов. Отсутствие файла — не ошибка.
load()
