"""SearchableComboBox — a combo whose popup carries its own search box.

Qt's stock popup is a bare list: with a long list of warehouses or people you
can only scroll. This subclass keeps the whole QComboBox API (addItem,
currentData, currentIndexChanged …) and only replaces the popup with a small
panel holding a filter field above a scrollable list.
"""
from PyQt6.QtCore import QEvent, QPoint, QSize, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)


class SearchableComboBox(QComboBox):
    ROW_HEIGHT = 30

    def __init__(self, placeholder="Поиск", max_visible=9, parent=None):
        super().__init__(parent)
        self._placeholder = placeholder
        self._max_visible = max_visible
        self._popup = None
        self._search = None
        self._list = None

    # ── popup lifecycle ───────────────────────────────────────
    def showPopup(self):
        popup = self._ensure_popup()
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._fill()
        popup.setFixedWidth(max(self.width(), 200))
        popup.adjustSize()
        popup.move(self.mapToGlobal(QPoint(0, self.height())))
        popup.show()
        self._search.setFocus()

    def hidePopup(self):
        if self._popup is not None:
            self._popup.hide()
        super().hidePopup()

    def _ensure_popup(self):
        if self._popup is not None:
            return self._popup
        popup = QFrame(self, Qt.WindowType.Popup)
        popup.setObjectName("comboPopup")
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText(self._placeholder)
        self._search.textChanged.connect(lambda text: self._fill(text))
        self._search.installEventFilter(self)
        lay.addWidget(self._search)

        self._list = QListWidget()
        self._list.setObjectName("comboList")
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.itemClicked.connect(self._choose)
        lay.addWidget(self._list)

        self._popup = popup
        return popup

    # ── contents ──────────────────────────────────────────────
    def _fill(self, text=""):
        query = (text or "").strip().lower()
        self._list.clear()
        for index in range(self.count()):
            label = self.itemText(index)
            if query and query not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setSizeHint(QSize(0, self.ROW_HEIGHT))
            self._list.addItem(item)
            if index == self.currentIndex():
                self._list.setCurrentItem(item)

        if self._list.count() == 0:
            empty = QListWidgetItem("Ничего не найдено")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setSizeHint(QSize(0, self.ROW_HEIGHT))
            self._list.addItem(empty)
        elif self._list.currentRow() < 0:
            self._list.setCurrentRow(0)

        # show at most `max_visible` rows — the rest scrolls
        rows = min(self._list.count(), self._max_visible)
        self._list.setFixedHeight(rows * self.ROW_HEIGHT + 4)
        # the panel must follow the list, otherwise filtering leaves a gap
        if self._popup is not None and self._popup.isVisible():
            self._popup.adjustSize()

    def _choose(self, item):
        self._popup.hide()
        self.setCurrentIndex(item.data(Qt.ItemDataRole.UserRole))

    # ── keyboard: stay in the search field, drive the list from there ──
    def eventFilter(self, obj, event):
        if obj is self._search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if self._list.count():
                    row = self._list.currentRow()
                    row += 1 if key == Qt.Key.Key_Down else -1
                    self._list.setCurrentRow(max(0, min(row, self._list.count() - 1)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self._list.currentItem()
                if item is not None:
                    self._choose(item)
                return True
            if key == Qt.Key.Key_Escape:
                self._popup.hide()
                return True
        return super().eventFilter(obj, event)
