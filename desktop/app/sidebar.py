"""Left navigation rail — the shared Sidebar component.

Stylesheets here are always scoped with an object-name selector: a rule set on
a parent widget without a selector also applies to every child, which would
draw the block's border under each label inside it.
"""
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import devices, theme
from .session import session
from .widgets.common import button, svg_pixmap
from .widgets.devicelog import show_device_log

NAV_ITEMS = [
    ("home", "Главная", "home"),
    ("orders", "Заказы", "orders"),
    ("shipping", "Отгрузка", "shipping"),
    ("receiving", "Приемка", "receiving"),
    ("catalog", "Справочник", "catalog"),
    ("stock", "Остатки", "stock"),
    ("users", "Пользователи", "users"),
]

NAV_ROW_HEIGHT = 45
ICON_SIZE = theme.ICON_SIZE


class NavRow(QWidget):
    """One navigation entry: icon + label, hairline active marker on the left."""

    clicked = pyqtSignal()

    def __init__(self, title, icon, is_active, parent=None):
        super().__init__(parent)
        self.setObjectName("navRowActive" if is_active else "navRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(NAV_ROW_HEIGHT)

        color = theme.ACCENT_RAMP[800] if is_active else theme.TEXT
        marker = theme.ACCENT if is_active else "transparent"
        bg = theme.BG if is_active else "transparent"
        hover_bg = theme.BG if is_active else theme.ACCENT_RAMP[100]
        self.setStyleSheet(
            f"QWidget#{self.objectName()}{{background:{bg};"
            f"border-left:2px solid {marker};}}"
            f"QWidget#{self.objectName()}:hover{{background:{hover_bg};}}"
            f"QWidget#{self.objectName()} QLabel{{background:transparent;border:none;}}"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.SP4 - 2, 0, theme.SP4, 0)
        lay.setSpacing(theme.SP3)

        icon_label = QLabel()
        icon_label.setPixmap(svg_pixmap(icon, color, ICON_SIZE))
        icon_label.setFixedSize(ICON_SIZE, ICON_SIZE)
        lay.addWidget(icon_label)

        text = QLabel(title)
        text.setStyleSheet(
            f"font-family:{theme.font_heading()};font-size:16px;color:{color};"
        )
        lay.addWidget(text)
        lay.addStretch(1)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class DeviceRow(QWidget):
    """Name of a device on the left, its state on the right, dot in front."""

    _DOT = {devices.ONLINE: theme.ACCENT, devices.ERROR: theme.DANGER,
            devices.OFFLINE: theme.NEUTRAL[400]}
    _SIM_DOT_COLOR = theme.ACCENT2_RAMP[700] if hasattr(theme, 'ACCENT2_RAMP') else "#FFA500"

    def __init__(self, title, state, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceRow")
        self.setStyleSheet("QWidget#deviceRow QLabel{background:transparent;border:none;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._dot = QLabel("●")
        self._dot.setFixedWidth(9)
        self._name = QLabel(title)
        self._name.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[700]};")
        self._state = QLabel()
        self._state.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lay.addWidget(self._dot)
        lay.addWidget(self._name)
        lay.addStretch(1)
        lay.addWidget(self._state)
        self.set_state(state)

    def set_state(self, state, simulated=False):
        if simulated:
            color = self._SIM_DOT_COLOR
        else:
            color = self._DOT.get(state, theme.NEUTRAL[400])
        self._dot.setStyleSheet(f"font-size:9px;color:{color};")

        state_text = devices.STATE_LABELS.get(state, state)
        if simulated:
            state_text += " (эмуляция)"

        self._state.setText(state_text)
        self._state.setStyleSheet(f"font-size:11px;color:{theme.NEUTRAL[600]};")
        self.setToolTip(f"{self._name.text()}: {state_text}")


class Sidebar(QWidget):
    navigate = pyqtSignal(str)
    open_user = pyqtSignal(int)
    logout = pyqtSignal()

    def __init__(self, active: str = "home", parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(200)
        self.setStyleSheet(
            f"QWidget#sidebar{{background:{theme.SURFACE};"
            f"border-right:1px solid {theme.DIVIDER};}}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._brand())
        root.addWidget(self._user_row())
        root.addLayout(self._nav(active), 1)
        root.addWidget(self._footer())

    def _brand(self):
        box = QWidget()
        box.setObjectName("brandBox")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(
            f"QWidget#brandBox{{background:transparent;"
            f"border-bottom:1px solid {theme.DIVIDER};}}"
            f"QWidget#brandBox QLabel{{border:none;background:transparent;}}"
        )
        lay = QVBoxLayout(box)
        lay.setContentsMargins(theme.SP4, theme.SP6, theme.SP4, theme.SP6)
        lay.setSpacing(2)

        brand = QLabel(f'Pro<span style="color:{theme.ACCENT_RAMP[700]}">Запас</span>')
        brand.setTextFormat(Qt.TextFormat.RichText)
        brand.setStyleSheet(
            f"font-family:{theme.font_heading()};font-weight:600;"
            f"font-size:24px;letter-spacing:-0.3px;"
        )
        sub = QLabel("учёт и логистика")
        sub.setObjectName("kicker")
        lay.addWidget(brand)
        lay.addWidget(sub)
        return box

    def _user_row(self):
        box = QWidget()
        box.setObjectName("userRow")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(
            f"QWidget#userRow{{background:transparent;"
            f"border-bottom:1px solid {theme.DIVIDER};}}"
            f"QWidget#userRow QLabel{{border:none;background:transparent;}}"
            f"QWidget#userLink{{background:transparent;border:none;}}"
            f"QWidget#userLink:hover{{background:{theme.ACCENT_RAMP[100]};}}"
            f"QPushButton#logoutBtn{{border:none;border-left:1px solid {theme.DIVIDER};"
            f"background:transparent;padding:0;}}"
            f"QPushButton#logoutBtn:hover{{background:{theme.ACCENT_RAMP[100]};}}"
        )
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        me = session.user
        name, position, uid = "—", "", None
        if me:
            name = session.display_name()
            position = me.get("position") or ""
            uid = me["id"]

        info = QWidget()
        info.setObjectName("userLink")
        info.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        info.setCursor(Qt.CursorShape.PointingHandCursor)
        if uid is not None:
            info.setToolTip("Открыть профиль")
            info.mouseReleaseEvent = lambda e, i=uid: (
                self.open_user.emit(i) if e.button() == Qt.MouseButton.LeftButton else None
            )
        il = QVBoxLayout(info)
        il.setContentsMargins(theme.SP4, theme.SP4, theme.SP4, theme.SP4)
        il.setSpacing(1)
        n = QLabel(name)
        n.setStyleSheet("font-size:14px;font-weight:500;")
        p = QLabel(position)
        p.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[600]};")
        p.setWordWrap(False)
        il.addWidget(n)
        il.addWidget(p)

        out = QPushButton()
        out.setObjectName("logoutBtn")
        out.setIcon(QIcon(svg_pixmap("logout", theme.NEUTRAL[600], ICON_SIZE)))
        out.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        out.setFixedWidth(46)
        out.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        out.setCursor(Qt.CursorShape.PointingHandCursor)
        out.setToolTip("Выйти")
        out.clicked.connect(self.logout.emit)

        lay.addWidget(info, 1)
        lay.addWidget(out)
        return box

    def _nav(self, active):
        nav = QVBoxLayout()
        nav.setContentsMargins(0, theme.SP3, 0, theme.SP3)
        nav.setSpacing(2)
        for key, title, icon in NAV_ITEMS:
            row = NavRow(title, icon, active == key)
            row.clicked.connect(lambda k=key: self.navigate.emit(k))
            nav.addWidget(row)
        nav.addStretch(1)
        return nav

    def _footer(self):
        box = QWidget()
        box.setObjectName("footerBox")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(
            f"QWidget#footerBox{{background:transparent;"
            f"border-top:1px solid {theme.DIVIDER};}}"
            f"QWidget#footerBox QLabel{{border:none;background:transparent;}}"
        )
        lay = QVBoxLayout(box)
        lay.setContentsMargins(theme.SP4, theme.SP4, theme.SP4, theme.SP4)
        lay.setSpacing(theme.SP2)

        kicker = QLabel("Оборудование")
        kicker.setObjectName("kicker")
        lay.addWidget(kicker)

        self._device_rows = {}
        for key, title in devices.DEVICES:
            row = DeviceRow(title, devices.OFFLINE)
            self._device_rows[key] = row
            lay.addWidget(row)

        devices.bus.changed.connect(self._refresh_devices)
        self._refresh_devices()

        # Журнал обмена: единственный способ увидеть из приложения, шлёт ли
        # подключённое железо хоть что-нибудь. Открывается отдельным окном,
        # чтобы приложением можно было пользоваться с ним параллельно.
        logs = button("Логи", "ghost")
        logs.clicked.connect(lambda: show_device_log())
        lay.addWidget(logs)

        lay.addSpacing(theme.SP2)
        v = QLabel("Версия 1.0")
        v.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[600]};")
        lay.addWidget(v)
        return box

    def _refresh_devices(self):
        """Pull the current state of every device into the footer."""
        current = devices.states()
        # Предполагаем, что у devices может быть метод для получения флага симуляции
        simulated_devices = getattr(devices, 'simulated_states', lambda: {})()
        for key, row in getattr(self, "_device_rows", {}).items():
            state = current.get(key, devices.OFFLINE)
            simulated = simulated_devices.get(key, False)
            row.set_state(state, simulated)