"""BlueprintFrame — a wireframe panel with registration corner marks.

Reproduces the `.blueprint` component: a square, transparent, hairline-bordered
box whose four corners carry a small cross-hair that sits *outside* the border.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QVBoxLayout

from .. import theme

MARK = _MARK = 6   # how far a corner mark reaches past the border
_ARM = 5           # half-length of each cross-hair arm


class BlueprintFrame(QFrame):
    clicked = pyqtSignal()

    def __init__(self, padding: int = theme.SP4, clickable: bool = False,
                 hover_accent: bool = False, parent=None):
        super().__init__(parent)
        self._pad = padding
        self._clickable = clickable
        self._hover_accent = hover_accent
        self._hover = False
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setMouseTracking(True)
        lay = QVBoxLayout(self)
        # leave room for the corner marks plus the requested inner padding
        m = _MARK + padding
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(theme.SP3)
        self._layout = lay

    def content_layout(self) -> QVBoxLayout:
        return self._layout

    # ── interaction ───────────────────────────────────────────
    def enterEvent(self, event):
        if self._hover_accent:
            self._hover = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._hover_accent:
            self._hover = False
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # border rectangle, inset so the marks have room to overhang
        r = self.rect().adjusted(_MARK, _MARK, -_MARK - 1, -_MARK - 1)
        border = QColor(29, 31, 32)
        border.setAlphaF(0.16)
        if self._hover and self._hover_accent:
            border = QColor(theme.ACCENT)
        p.setPen(QPen(border, 1))
        p.drawRect(r)

        # corner cross-hairs, centred on each border corner
        mark = QColor(29, 31, 32)
        mark.setAlphaF(0.55)
        p.setPen(QPen(mark, 1))
        for cx, cy in (
            (r.left(), r.top()), (r.right() + 1, r.top()),
            (r.left(), r.bottom() + 1), (r.right() + 1, r.bottom() + 1),
        ):
            p.drawLine(cx - _ARM, cy, cx + _ARM, cy)
            p.drawLine(cx, cy - _ARM, cx, cy + _ARM)
        p.end()
