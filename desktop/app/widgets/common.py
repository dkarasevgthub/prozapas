"""Small shared UI helpers: headings, tags, buttons and line icons."""
from PyQt6.QtCore import QByteArray, QPointF, QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy

from .. import theme

# ── lucide-style line icons (paths lifted from the mockup) ─────────────────
ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-6h6v6"/>',
    "orders": '<rect x="6" y="3" width="12" height="18" rx="1"/><path d="M9 3v3h6V3M9 10h6M9 14h6M9 18h3"/>',
    "shipping": '<rect x="1" y="7" width="13" height="10"/><path d="M14 10h4l3 3v4h-7z"/><circle cx="6" cy="19" r="1.8"/><circle cx="17" cy="19" r="1.8"/>',
    "receiving": '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/><path d="M8 5.5 16 10"/>',
    "catalog": '<path d="M5 4h11a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z"/><path d="M5 4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2M18 16H7"/>',
    "stock": '<path d="M12 3 3 7.5 12 12l9-4.5z"/><path d="M3 7.5v9L12 21l9-4.5v-9M12 12v9"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/>',
    "chevron": '<path d="m9 18 6-6-6-6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "reset": '<path d="M3 2v6h6"/><path d="M3.51 9a9 9 0 1 0 2.13-3.36L3 8"/>',
    "calendar": '<rect x="3" y="4.5" width="18" height="17" rx="2"/>'
                '<path d="M16 2.5v4M8 2.5v4M3 10.5h18"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    "printer": '<path d="M6 9V2h12v7"/>'
               '<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>'
               '<rect x="6" y="14" width="12" height="8"/>',
    "trash": '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>',
    "book": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
}


def svg_pixmap(name: str, color: str, size: int = 18, dpr: int = 2) -> QPixmap:
    """`size` is the logical size; the pixmap is rendered at `size * dpr`
    device pixels. Pass dpr=1 when the result is saved to a file for a Qt
    style sheet — QSS draws image files at their pixel size and knows nothing
    about device pixel ratios."""
    body = ICONS.get(name, "")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # render into the *logical* rect — painter already carries the DPR scale,
    # so passing no rect would use the device-pixel viewport and clip the icon
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pm


def svg_icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(svg_pixmap(name, color, size))


# ── typography ─────────────────────────────────────────────────────────────
def label(text: str, object_name: str = "", parent=None) -> QLabel:
    lbl = QLabel(text, parent)
    if object_name:
        lbl.setObjectName(object_name)
    return lbl


def h1(text):        return label(text, "h1")
def h4(text):        return label(text, "h4")
def breadcrumb(text): return label(text, "breadcrumb")
def muted(text):     return label(text, "muted")
def kicker(text):    l = label(text, "kicker"); return l


# ── tag pill ───────────────────────────────────────────────────────────────
class Tag(QLabel):
    def __init__(self, text: str, color: str, bg: str, parent=None):
        super().__init__(text, parent)
        bg_rule = "" if bg == "transparent" else f"background:{bg};"
        self.setStyleSheet(
            f"QLabel{{{bg_rule}color:{color};font-size:11px;font-weight:700;"
            f"padding:3px 10px;}}"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)


# ── buttons ────────────────────────────────────────────────────────────────
def button(text: str, variant: str = "secondary", parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    if variant and variant != "secondary":
        btn.setProperty("variant", variant)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class IconButton(QPushButton):
    """Icon-only button that draws its icon exactly in the centre.

    `QPushButton.setIcon` lays the icon out together with the (empty) label and
    leaves it a couple of pixels left of centre — visible once the button has a
    hover fill around it. Painting the pixmap ourselves avoids that; the frame
    and the fill still come from the stylesheet.
    """

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._pixmap = pixmap

    def paintEvent(self, event):
        super().paintEvent(event)               # background and border from QSS
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        ratio = self._pixmap.devicePixelRatio() or 1
        w = self._pixmap.width() / ratio
        h = self._pixmap.height() / ratio
        # snap to the device pixel grid: on a scaled screen a half-pixel offset
        # makes the icon look shifted and smears its strokes
        screen = self.devicePixelRatioF() or 1
        x = round((self.width() - w) / 2 * screen) / screen
        y = round((self.height() - h) / 2 * screen) / screen
        painter.drawPixmap(QPointF(x, y), self._pixmap)


def icon_button(name: str, tooltip: str, color: str = None,
                size: int = 18, box: int = 36, parent=None,
                ghost: bool = False, soft: bool = False) -> QPushButton:
    """Square, icon-only button — `box` matches the height of an .input so it
    lines up with the fields it sits next to.

    `ghost` drops the border, for buttons inside a table row; `soft` fills the
    button, for controls that must stand out on their own.
    """
    btn = IconButton(svg_pixmap(name, color or theme.ACCENT, size), parent)
    btn.setFixedSize(box, box)
    btn.setProperty("variant", "icon-soft" if soft else ("icon-ghost" if ghost else "icon"))
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"background:{theme.DIVIDER}; max-height:1px; border:none;")
    line.setFixedHeight(1)
    return line
