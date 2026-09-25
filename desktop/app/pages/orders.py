"""Заказы — order list with incoming/outgoing tabs and filters."""
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit

from .. import api, fmt, reference, theme
from ..api.errors import ApiError
from ..widgets.combo import SearchableComboBox
from ..widgets.common import breadcrumb, button, icon_button
from ..widgets.flow import FlowRow
from ..widgets.table import TableSection
from ._ui import (
    clear_date,
    date_value,
    empty_date_edit,
    filter_action,
    header_row,
    labeled_field,
)
from .base import Page

#: Вкладка экрана → параметр API. «Входящие» — у нас заказали и мы отгружаем;
#: «исходящие» — заказали мы, товар придёт к нам.
TAB_TO_API = {"theirs": "incoming", "ours": "outgoing"}

STATUS_OPTIONS = [
    ("all", "Все статусы"), ("created", "Создан"), ("processing", "В обработке"),
    ("shipped", "Отгружен"), ("received", "Завершен"), ("declined", "Отклонён"),
    ("cancelled", "Отменён"),
]


class OrdersPage(Page):
    def build(self):
        self._tab = "theirs"
        self._search = ""
        self._status = "all"
        self._warehouse = "all"
        self._responsible = "all"

        self.add_block(breadcrumb("ProЗапас / Заказы"))

        action = button("+ Создать заказ", "primary")
        action.clicked.connect(lambda: self.nav.go("new_order"))
        # only outgoing orders can be created from here
        action.setVisible(self._tab == "ours")
        self._action = action
        self.add_block(header_row("Заказы", "Заказы на перемещение товара между складами", action))

        # tabs
        tabs = QHBoxLayout()
        tabs.setSpacing(0)          # вкладки стоят вплотную
        self._tab_in = button("Входящие", "primary")
        self._tab_out = button("Исходящие", "secondary")
        self._tab_in.clicked.connect(lambda: self._set_tab("theirs"))
        self._tab_out.clicked.connect(lambda: self._set_tab("ours"))
        tabs.addWidget(self._tab_in)
        tabs.addWidget(self._tab_out)
        tabs.addStretch(1)
        self.add_block(tabs)

        # filters
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Номер заказа")
        self._search_input.textChanged.connect(self._on_search)
        self._status_input = QComboBox()
        for v, t in STATUS_OPTIONS:
            self._status_input.addItem(t, v)
        self._status_input.currentIndexChanged.connect(self._on_filter)
        # these two grow with the data, so their popups get a search field
        self._warehouse_input = SearchableComboBox("Поиск склада")
        self._warehouse_input.currentIndexChanged.connect(self._on_warehouse)
        self._responsible_input = SearchableComboBox("Поиск сотрудника")
        self._responsible_input.currentIndexChanged.connect(self._on_filter)
        self._date_from = empty_date_edit()
        self._date_from.dateChanged.connect(self._on_filter)
        self._date_to = empty_date_edit()
        self._date_to.dateChanged.connect(self._on_filter)

        filters = FlowRow(h_spacing=theme.SP4, v_spacing=theme.SP3)
        # widths are tuned so the whole row — reset button included — still
        # fits on one line at the default 1280px window
        filters.add(labeled_field("Поиск", self._search_input, 150))
        filters.add(labeled_field("Статус", self._status_input, 140))
        filters.add(labeled_field("Склад", self._warehouse_input, 165))
        filters.add(labeled_field("Ответственный", self._responsible_input, 150))
        filters.add(labeled_field("Дата создания от", self._date_from, 130))
        filters.add(labeled_field("Дата создания до", self._date_to, 130))
        reset = icon_button("reset", "Сбросить фильтры")
        reset.clicked.connect(self._reset)
        filters.add(filter_action(reset))
        self.add_block(filters)

        self._table = TableSection(
            headers=self._headers(), widths=[72, 116, 0, 84, 0, 128, 128],
            rows=[], on_row_click=self._open_order, page_size=13, auto_rows=True,
            on_page_change=self._refresh,      # страницу подгружает сервер
        )
        self.add_block(self._table)
        self.col.addStretch(1)

        self._refresh_warehouse_options()
        self._refresh_responsible_options()
        self._refresh()

    # ── headers ──
    def _headers(self):
        last = "Отгружен" if self._tab == "theirs" else "Принят"
        wh = "Склад-получатель" if self._tab == "theirs" else "Склад-отправитель"
        return ["Номер", "Статус", wh, "Позиций", "Ответственный", "Создан", last]

    # ── tab / filters ──
    def _set_tab(self, tab):
        if tab == self._tab:
            return
        self._tab = tab
        self._clear_filter_widgets()
        self._tab_in.setProperty("variant", "primary" if tab == "theirs" else "secondary")
        self._tab_out.setProperty("variant", "primary" if tab == "ours" else "secondary")
        for b in (self._tab_in, self._tab_out):
            b.style().unpolish(b); b.style().polish(b)
        self._action.setVisible(tab == "ours")
        self._table._table.setHorizontalHeaderLabels(self._headers())
        self._refresh_warehouse_options()
        self._refresh_responsible_options()
        self._refresh()

    def _refresh_warehouse_options(self):
        """Склады берём из справочника, а не из выборки заказов: с серверной
        пагинацией полного списка заказов у экрана нет."""
        self._warehouse_input.blockSignals(True)
        self._warehouse_input.clear()
        self._warehouse_input.addItem("Все склады", "all")
        for w in reference.warehouses(exclude_own=True):
            self._warehouse_input.addItem(w["name"], w["id"])
        self._warehouse_input.blockSignals(False)

    def _refresh_responsible_options(self):
        """Ответственные — сотрудники склада-контрагента, а при «всех складах»
        все, кого видит справочник."""
        self._responsible_input.blockSignals(True)
        self._responsible_input.clear()
        self._responsible_input.addItem("Все", "all")
        wid = None if self._warehouse == "all" else self._warehouse
        for u in reference.people(warehouse_id=wid):
            self._responsible_input.addItem(fmt.short_name(u["name"]), u["id"])
        self._responsible_input.blockSignals(False)
        self._responsible = "all"

    def _clear_filter_widgets(self):
        self._search = ""
        self._status = "all"
        self._warehouse = "all"
        self._responsible = "all"
        self._search_input.blockSignals(True); self._search_input.clear(); self._search_input.blockSignals(False)
        for combo in (self._status_input, self._warehouse_input, self._responsible_input):
            combo.blockSignals(True); combo.setCurrentIndex(0); combo.blockSignals(False)
        clear_date(self._date_from)
        clear_date(self._date_to)

    def _on_search(self, text):
        self._search = text
        self._refresh()

    def _on_warehouse(self, _):
        self._warehouse = self._warehouse_input.currentData()
        self._refresh_responsible_options()
        self._refresh()

    def _on_filter(self, _=None):
        self._status = self._status_input.currentData()
        self._responsible = self._responsible_input.currentData()
        self._refresh()

    def _reset(self):
        self._clear_filter_widgets()
        self._refresh_responsible_options()
        self._refresh()

    def _open_order(self, oid):
        self.nav.go("order", id=oid)

    def _refresh(self, page: int = 1):
        """Фильтры и страницы считает сервер: своего полного списка у экрана нет."""
        size = self._table.page_size()
        try:
            payload = api.client.orders(
                TAB_TO_API[self._tab],
                status=None if self._status == "all" else self._status,
                warehouse_id=None if self._warehouse == "all" else self._warehouse,
                responsible_id=None if self._responsible == "all" else self._responsible,
                created_from=date_value(self._date_from),
                created_to=date_value(self._date_to),
                q=self._search.strip() or None,
                limit=size, offset=(page - 1) * size,
            )
        except ApiError as exc:
            # Список объясняет отказ на своём месте. Модальное окно тут нельзя:
            # обновление зовётся и при перелистывании, и при каждом фильтре —
            # недоступный сервер завалил бы экран диалогами.
            self._table.set_empty_text(exc.title)
            self._table.set_rows([], total=0, keep_page=True)
            return

        self._table.set_empty_text("Ничего не найдено по этим условиям")
        rows = []
        for o in payload["items"]:
            label, color, bg = theme.status_meta(o["status"])
            last = o["shipped_at"] if self._tab == "theirs" else o.get("accepted_at")
            rows.append((
                [("h", "№" + o["number"]),
                 ("tag", label, color, bg),
                 ("m", o["counterparty"]["name"]),
                 ("m", str(o["positions_count"])),
                 ("m", fmt.short_name((o.get("responsible") or {}).get("name", ""))),
                 ("m", fmt.datetime_(o["created_at"])),
                 ("m", fmt.datetime_(last))],
                o["id"],
            ))
        self._table.set_rows(rows, total=payload["total"], keep_page=page > 1)
