"""Administrator-only selection of a Windows label printer."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QVBoxLayout

from .. import equipment, theme
from ..session import session
from .common import button
from .dialog import _style_dialog


class PrinterSettingsDialog(QDialog):
    def __init__(self, parent=None):
        if not session.is_admin:
            raise PermissionError("Настройка принтера доступна только администратору.")
        super().__init__(parent)
        self.setWindowTitle("Настройка принтера этикеток")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(520)
        _style_dialog(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SP4, theme.SP4, theme.SP4, theme.SP4)
        layout.setSpacing(theme.SP3)
        layout.addWidget(QLabel("Принтер этикеток в Windows"))
        self.printers = QComboBox()
        self.printers.setObjectName("windowsPrinterSelect")
        layout.addWidget(self.printers)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        note = QLabel("Принтер должен поддерживать ZPL. Выбор сохраняется для этого "
                      "рабочего места и применяется после перезапуска приложения.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        refresh = button("Обновить список", "secondary")
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        actions.addStretch(1)
        close = button("Закрыть", "secondary")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        self.save_button = button("Сохранить", "primary")
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self):
        selected = self.printers.currentData() or equipment.selected_printer()
        names = sorted(set(equipment.list_printers()), key=str.casefold)
        self.printers.clear()
        self.printers.addItem("Выберите принтер", None)
        for name in names:
            self.printers.addItem(name, name)
        index = self.printers.findData(selected)
        self.printers.setCurrentIndex(max(0, index))
        self.printers.setEnabled(bool(names))
        self.save_button.setEnabled(bool(names) and session.is_admin)
        if not names:
            self.hint.setText("Принтеры не найдены. Установите принтер в Windows "
                              "и нажмите «Обновить список».")
        elif selected and index < 0:
            self.hint.setText(f"Сохранённый принтер «{selected}» сейчас не установлен.")
        else:
            self.hint.setText("")
        self.hint.setVisible(bool(self.hint.text()))
        self.status.hide()

    def save(self):
        try:
            equipment.save_printer(self.printers.currentData() or "")
        except (OSError, ValueError, PermissionError) as exc:
            self.status.setText(str(exc))
            self.status.setStyleSheet(f"color:{theme.DANGER};")
        else:
            self.status.setText("Выбор сохранён. Перезапустите приложение, "
                                "чтобы применить настройку.")
            self.status.setStyleSheet(f"color:{theme.ACCENT};")
        self.status.show()


def show_printer_settings(parent=None):
    if not session.is_admin:
        return
    PrinterSettingsDialog(parent).exec()
