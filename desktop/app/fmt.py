"""Показ дат и чисел.

Сервер отдаёт время в ISO 8601 и UTC, экран показывает местное и по-русски.
Хранение и показ разделены: в данных дата остаётся датой, а «22.07.2026 09:00»
собирается только здесь.
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse(value) -> datetime | None:
    """ISO 8601 → местное время. `None` и прочерк возвращают None."""
    if not value or value == "—":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def date(value, dash: str = "—") -> str:
    """«22.07.2026»"""
    dt = parse(value)
    return f"{dt.day:02d}.{dt.month:02d}.{dt.year}" if dt else dash


def datetime_(value, dash: str = "—") -> str:
    """«22.07.2026 09:00»"""
    dt = parse(value)
    return f"{date(dt)} {dt.hour:02d}:{dt.minute:02d}" if dt else dash


def qty(value, unit: str = "") -> str:
    """Количества дробные, но целые показываются без хвоста: 12 и 12.5."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.0f}" if number == int(number) else f"{number:.3f}".rstrip("0").rstrip(".")
    return f"{text} {unit}".strip()


def weight(value, dash: str = "—") -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return dash


def short_name(full_name: str) -> str:
    """«Кузнецов Игорь Александрович» → «Кузнецов И.А.»"""
    parts = (full_name or "").strip().split()
    if not parts:
        return "—"
    tail = "".join(f"{p[0]}." for p in parts[1:3] if p)
    return f"{parts[0]} {tail}".strip()
