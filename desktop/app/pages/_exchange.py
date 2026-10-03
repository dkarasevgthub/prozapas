"""Обмен с 1С в разделах «Справочник» и «Остатки».

Оба раздела обмениваются одинаково — выбрать файл, отправить, показать итог, —
поэтому диалоги и разбор ответа живут здесь, а не дважды на страницах.

Запросы синхронные, как и всё остальное в приложении, но файл на тысячу позиций
сервер разбирает секунды, а не миллисекунды. На это время кнопки гаснут: другого
способа показать занятость в приложении нет, а замершее окно без объяснения
читается как поломка.

Кнопка загрузки доступна только при праве на изменение раздела. Это не замена
проверке — решает сервер, — но человеку незачем выбирать файл, чтобы узнать
про отказ.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QWidget

from .. import theme
from ..api.errors import ApiError
from ..session import EDIT, session
from ..widgets.common import button
from ..widgets.dialog import confirm_dialog

#: 1С выгружает import.xml и offers.xml; каталог бывает и в архиве рядом с
#: картинками, но мы берём только сам файл.
FILTER = "Файлы обмена 1С (*.xml);;Все файлы (*)"

NO_RIGHTS = "Загрузка доступна тем, кто может изменять этот раздел"

#: Где человек в прошлый раз брал файл. На запуск не переживает: в настройках
#: рабочего места этому места нет, а в пределах смены каталог один и тот же.
_last_dir: Path | None = None

#: Сколько непринятых позиций показываем в отчёте. Полный список на тысячу
#: строк в модальном окне не читают.
_ISSUE_LIMIT = 12


def toolbar(page, *, section: str, on_import, on_export, extra=None) -> QWidget:
    """Кнопки обмена для заголовка раздела. `extra` — своя кнопка раздела."""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.SP3)

    load = button("Импорт из 1С")
    save = button("Экспорт для 1С")
    editable = session.can(section, EDIT)
    if not editable:
        load.setEnabled(False)
        load.setToolTip(NO_RIGHTS)

    def busy(action):
        """На время обмена гасим обе кнопки: повторное нажатие отправило бы
        файл второй раз."""
        def run():
            for btn in (load, save):
                btn.setEnabled(False)
            try:
                action()
            finally:
                save.setEnabled(True)
                load.setEnabled(editable)
        return run

    load.clicked.connect(busy(on_import))
    save.clicked.connect(busy(on_export))
    for btn in (load, save):
        row.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
    if extra is not None:
        row.addWidget(extra, 0, Qt.AlignmentFlag.AlignTop)
    return host


def load(page, send, after) -> None:
    """Выбрать файл из 1С, отправить его и показать итог.

    `send(raw)` возвращает ответ сервера, `after()` перечитывает экран.
    """
    path = _ask_open(page)
    if path is None:
        return
    try:
        raw = path.read_bytes()
    except OSError as exc:
        page.show_error(_problem(f"Файл не прочитан: {exc.strerror}"))
        return
    if not raw:
        page.show_error(_problem("Файл пустой"))
        return

    try:
        result = send(raw)
    except ApiError as exc:
        page.show_error(_explain(exc))
        return
    _report(page, path, result)
    after()


def save(page, fetch, default_name: str) -> None:
    """Забрать файл с сервера и положить туда, куда укажут."""
    try:
        raw = fetch()
    except ApiError as exc:
        page.show_error(_explain(exc))
        return
    path = _ask_save(page, default_name)
    if path is None:
        return
    try:
        path.write_bytes(raw)
    except OSError as exc:
        page.show_error(_problem(f"Файл не записан: {exc.strerror}"))
        return
    confirm_dialog(page, "Файл готов",
                   f"Сохранён {path.name}, {_size(len(raw))}.\n{path.parent}",
                   confirm_label="Понятно", cancel_label="")


def _ask_open(page) -> Path | None:
    global _last_dir
    start = str(_last_dir or Path.home())
    name, _ = QFileDialog.getOpenFileName(page, "Выберите файл из 1С", start, FILTER)
    if not name:
        return None
    path = Path(name)
    _last_dir = path.parent
    return path


def _ask_save(page, default_name: str) -> Path | None:
    global _last_dir
    start = str((_last_dir or Path.home()) / default_name)
    name, _ = QFileDialog.getSaveFileName(page, "Куда сохранить файл для 1С",
                                          start, FILTER)
    if not name:
        return None
    path = Path(name)
    _last_dir = path.parent
    return path


def _report(page, path: Path, result: dict) -> None:
    """Итог загрузки: счётчики и то, что не легло."""
    counts = [("Создано", result.get("created", 0)),
              ("Обновлено", result.get("updated", 0)),
              ("Без изменений", result.get("unchanged", 0)),
              ("Пропущено", result.get("skipped", 0))]
    lines = [path.name, ""]
    lines += [f"{label}: {value}" for label, value in counts]

    issues = result.get("issues") or []
    if issues:
        lines += ["", "Требуют внимания:"]
        for issue in issues[:_ISSUE_LIMIT]:
            lines.append(f"  {issue['index']}. {issue['ref']} — {issue['reason']}")
        if len(issues) > _ISSUE_LIMIT:
            lines.append(f"  … и ещё {len(issues) - _ISSUE_LIMIT}")

    applied = sum(value for label, value in counts if label != "Пропущено")
    title = "Загружено" if applied else "Ничего не загружено"
    confirm_dialog(page, title, "\n".join(lines),
                   confirm_label="Понятно", cancel_label="")


def _explain(exc: ApiError):
    """Причину отказа сервер кладёт в `detail`, а в `title` — только разряд
    ошибки. Показывать «Некорректный запрос» вместо «в файле нет товаров»
    значило бы прятать единственное, что человеку нужно.
    """
    return _problem(exc.detail("detail") or exc.title)


def _problem(text: str):
    """`show_error` читает `.title` — тот же приём, что в order_detail."""
    return type("_", (), {"title": text})()


def _size(count: int) -> str:
    return f"{count / 1024:.0f} КБ" if count >= 1024 else f"{count} Б"
