"""Окно журнала обмена со службой устройств.

Отдельное окно верхнего уровня, а не диалог: приложением надо пользоваться, пока
журнал открыт — сканировать, взвешивать, ходить по экранам и сразу видеть, что
уходит в канал. Без родителя окно попадает в переключатель задач наравне с
главным, и между ними можно свободно переключаться.

Нужно при подключении настоящего железа: по журналу видно, шлёт ли устройство
хоть что-нибудь и в каком виде. Молчащий COM-порт от неверно разобранного
протокола по индикатору «Недоступен» не отличить.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import devices, theme
from .common import button

#: Открытое окно. Ссылку держим сами: у окна нет родителя, и без неё сборщик
#: мусора закроет его сразу после показа.
_window = None


class DeviceLogWindow(QWidget):
    """Живой журнал кадров протокола в обе стороны."""

    def __init__(self):
        super().__init__()          # без родителя — самостоятельное окно
        self.setWindowTitle("ProЗапас — обмен со службой устройств")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # Журнал не должен держать приложение живым: выход происходит по
        # закрытию последнего окна, и иначе закрытие главного оставило бы
        # висеть и процесс, и дочернюю службу устройств.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setStyleSheet(
            f"QWidget{{background:{theme.SURFACE};}}"
            f"QLabel{{background:transparent;}}"
        )
        self.resize(780, 540)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SP4, theme.SP4, theme.SP4, theme.SP4)
        lay.setSpacing(theme.SP3)

        self._status = QLabel()
        self._status.setStyleSheet(
            f"font-size:12px;color:{theme.NEUTRAL[600]};")
        lay.addWidget(self._status)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(devices.LOG_LIMIT + 50)
        # Шрифт берём у системы: имя вроде «Consolas» на другой платформе
        # отсутствует, и Qt тратит сотни миллисекунд на перебор синонимов.
        self._view.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._view.setStyleSheet(
            f"QPlainTextEdit{{background:{theme.SURFACE};color:{theme.TEXT};"
            f"border:1px solid {theme.DIVIDER};font-size:12px;}}")
        lay.addWidget(self._view, 1)

        self._show_weight = QCheckBox("Показывать поток весов")
        self._show_weight.setChecked(True)
        self._show_weight.setStyleSheet("background:transparent;font-size:13px;")
        self._show_weight.toggled.connect(self._refill)
        lay.addWidget(self._show_weight)

        actions = QHBoxLayout()
        copy = button("Скопировать", "secondary")
        copy.clicked.connect(self._copy)
        clear = button("Очистить", "secondary")
        clear.clicked.connect(self._clear)
        actions.addWidget(copy)
        actions.addWidget(clear)
        actions.addStretch(1)
        close = button("Закрыть", "primary")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        lay.addLayout(actions)

        # Слоты — методы этого QWidget, а не замыкания: Qt снимает такие
        # соединения сам, когда объект уничтожен. С обычной функцией она
        # пережила бы окно и обратилась к удалённым виджетам на первом же
        # кадре с весов.
        devices.client.logged.connect(self._append)
        devices.bus.changed.connect(self._refresh_status)

        self._refill()

    # ── наполнение ────────────────────────────────────────────
    @staticmethod
    def _line(entry) -> str:
        return f'{entry["at"]}  {entry["dir"]}  {entry["text"]}'

    def _wanted(self, entry) -> bool:
        return self._show_weight.isChecked() or entry.get("kind") != "weight"

    def _refresh_status(self):
        states = ", ".join(
            f"{name} — {devices.STATE_LABELS.get(devices.states().get(key), '?')}"
            for key, name in devices.DEVICES
        )
        link = "служба на связи" if devices.service_connected() else "службы нет"
        self._status.setText(f"{link}. {states}")

    def _refill(self):
        self._refresh_status()
        self._view.setPlainText(
            "\n".join(self._line(e) for e in devices.log_entries()
                      if self._wanted(e)))
        self._to_bottom()

    def _append(self, entry):
        if not self._wanted(entry):
            return
        bar = self._view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self._view.appendPlainText(self._line(entry))
        if at_bottom:      # не дёргаем прокрутку, если человек листает вверх
            self._to_bottom()

    def _to_bottom(self):
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ── действия ──────────────────────────────────────────────
    def _copy(self):
        QGuiApplication.clipboard().setText(self._view.toPlainText())

    def _clear(self):
        devices.clear_log()
        self._refill()

    def closeEvent(self, event):
        global _window
        _window = None
        super().closeEvent(event)


def show_device_log():
    """Открыть журнал или поднять уже открытый."""
    global _window
    if _window is None:
        _window = DeviceLogWindow()
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
