"""FlowLayout — wraps children onto the next line when the row runs out of
width, i.e. the Qt equivalent of CSS `flex-wrap: wrap` used by the mockup's
filter rows. Qt ships no such layout, so this is the classic implementation.
"""
from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent=None, h_spacing=10, v_spacing=10):
        super().__init__(parent)
        self._items = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    # ── QLayout plumbing ──────────────────────────────────────
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        # QWidget::hasHeightForWidth() defers to its layout, so answering True
        # here would let a parent QVBoxLayout ask for the height at its own
        # narrow preferred width — where every field wraps onto its own line.
        # FlowRow reports the height for its real width via sizeHint instead.
        return False

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    # ── the actual flow ───────────────────────────────────────
    def _layout(self, rect, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width()
            if next_x > area.right() + 1 and line_height > 0:
                # doesn't fit — start a new line
                x = area.x()
                y += line_height + self._v
                next_x = x + hint.width()
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x + self._h
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


class FlowRow(QWidget):
    """A widget hosting a FlowLayout, sized for the width it actually has.

    height-for-width is deliberately *not* advertised: a parent QVBoxLayout
    would then evaluate it at the layout's own (narrow) preferred width, where
    every field wraps onto its own line. That inflates the page's size hint by
    a few hundred pixels, which a QScrollArea turns into a scrollbar plus a
    band of empty space. Reporting the height for the current width — and
    re-reporting it whenever the width changes — keeps the hint honest.
    """

    def __init__(self, h_spacing=10, v_spacing=10, parent=None):
        super().__init__(parent)
        self._flow = FlowLayout(self, h_spacing=h_spacing, v_spacing=v_spacing)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def add(self, widget):
        self._flow.addWidget(widget)
        return widget

    def sizeHint(self):
        return QSize(self._flow.sizeHint().width(),
                     self._flow.heightForWidth(max(1, self.width())))

    def minimumSizeHint(self):
        return QSize(0, self._flow.heightForWidth(max(1, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()       # a re-wrap changes our height
