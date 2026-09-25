"""MainView — sidebar + routed, scrollable content area."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QWidget

from . import devices, theme
from .sidebar import Sidebar

# which sidebar section lights up for a given page key
SECTION_OF = {
    "home": "home",
    "orders": "orders", "new_order": "orders", "order": "orders",
    "shipping": "shipping", "receiving": "receiving",
    "catalog": "catalog", "stock": "stock",
    "users": "users", "user": "users",
    "product": "catalog",
}


def _build_page(key, nav, **params):
    # imported lazily to avoid circular imports at module load
    from .pages import (
        catalog,
        home,
        new_order,
        order_detail,
        orders,
        product_detail,
        receiving,
        shipping,
        stock,
        user_detail,
        users,
    )
    builders = {
        "home": home.HomePage,
        "orders": orders.OrdersPage,
        "new_order": new_order.NewOrderPage,
        "order": order_detail.OrderDetailPage,
        "shipping": shipping.ShippingPage,
        "receiving": receiving.ReceivingPage,
        "catalog": catalog.CatalogPage,
        "stock": stock.StockPage,
        "users": users.UsersPage,
        "user": user_detail.UserDetailPage,
        "product": product_detail.ProductDetailPage,
    }
    return builders[key](nav, **params)


class MainView(QWidget):
    logout = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(0)
        self._sidebar = None
        self._scroll = None
        # browser-style history: [(key, params)] with a cursor into it
        self._history = []
        self._index = -1
        for keys, handler in ((("Alt+Left", "Backspace"), self.back),
                              (("Alt+Right",), self.forward)):
            for key in keys:
                QShortcut(QKeySequence(key), self, activated=handler)
        # сканер шлёт код всему приложению, а не в поле ввода: отдаём его
        # текущей странице, если она умеет его принять
        devices.client.scanned.connect(self._route_scan)
        self.go("home")

    def _route_scan(self, code):
        page = self._scroll.widget() if self._scroll is not None else None
        handler = getattr(page, "on_scan", None)
        if callable(handler):
            handler(code)

    def mousePressEvent(self, event):
        """Side buttons of the mouse, as in a browser."""
        if event.button() == Qt.MouseButton.BackButton:
            self.back()
        elif event.button() == Qt.MouseButton.ForwardButton:
            self.forward()
        else:
            super().mousePressEvent(event)

    # ── history ───────────────────────────────────────────────
    def can_back(self):
        return self._index > 0

    def can_forward(self):
        return -1 < self._index < len(self._history) - 1

    def back(self):
        if self.can_back():
            self._index -= 1
            self._show(*self._history[self._index])

    def forward(self):
        if self.can_forward():
            self._index += 1
            self._show(*self._history[self._index])

    def go(self, key, **params):
        entry = (key, params)
        if self._index >= 0 and self._history[self._index] == entry:
            self._show(*entry)          # same screen again — do not stack it
            return
        # a new step drops whatever was ahead, as in a browser
        del self._history[self._index + 1:]
        self._history.append(entry)
        self._index = len(self._history) - 1
        self._show(*entry)

    def _show(self, key, params):
        if key == "product":
            section = params.get("from_", "catalog")
        else:
            section = SECTION_OF.get(key, "home")

        # rebuild sidebar (cheap) so the active item updates
        new_sidebar = Sidebar(active=section)
        new_sidebar.navigate.connect(self.go)
        new_sidebar.open_user.connect(lambda uid: self.go("user", id=uid))
        new_sidebar.logout.connect(self.logout.emit)
        if self._sidebar is not None:
            self._row.replaceWidget(self._sidebar, new_sidebar)
            self._sidebar.setParent(None)
            self._sidebar.deleteLater()
        else:
            self._row.addWidget(new_sidebar)
        self._sidebar = new_sidebar

        # build the page inside a vertical scroll area
        page = _build_page(key, self, **params)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # содержимое, которое не сжимается дальше, должно прокручиваться,
        # а не уезжать за край окна
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea{{background:{theme.BG};border:none;}}")
        scroll.setWidget(page)

        if self._scroll is not None:
            self._row.replaceWidget(self._scroll, scroll)
            self._scroll.deleteLater()
        else:
            self._row.addWidget(scroll, 1)
        self._scroll = scroll
