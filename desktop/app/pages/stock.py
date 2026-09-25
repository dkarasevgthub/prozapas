"""Остатки — stock balances."""
from .. import api, fmt
from ..api.errors import ApiError
from ..widgets.common import breadcrumb
from ..widgets.table import TableSection
from ._ui import header_row, search_field, stat_grid
from .base import Page


class StockPage(Page):
    def build(self):
        self._search = ""

        self.add_block(breadcrumb("ProЗапас / Остатки"))
        self.add_block(header_row("Остатки", "Складские остатки по номенклатуре"))

        self._stats = stat_grid(self._summary(), columns=4)
        self.add_block(self._stats)

        self.add_block(search_field("Поиск по артикулу или наименованию", self._on_search))

        self._table = TableSection(
            headers=["Наименование", "Артикул", "Артикул 1С", "Ед. изм.",
                     "В наличии", "Свободно", "Резерв"],
            widths=[0, 110, 120, 90, 110, 110, 100],
            rows=[], on_row_click=self._open_item,
            page_size=14, auto_rows=True,
            on_page_change=self._refresh,       # страницу подгружает сервер
        )
        self.add_block(self._table)
        self.col.addStretch(1)

        self._refresh()

    def _summary(self):
        """Четыре карточки считает сервер: своей полной выборки у экрана нет."""
        try:
            s = api.client.stock_summary()
        except ApiError:
            return [("Всего позиций", "—", "на складе"),
                    ("Ниже минимума", "—", "требуют дозаказа"),
                    ("В резерве", "—", "под заказы клиентов"),
                    ("Свободный остаток", "—", "доступно к отгрузке")]
        return [
            ("Всего позиций", str(s["positions"]), "на складе"),
            ("Ниже минимума", str(s["below_min"]), "требуют дозаказа"),
            ("В резерве", fmt.qty(s["reserved"]), "под заказы клиентов"),
            ("Свободный остаток", fmt.qty(s["free"]), "доступно к отгрузке"),
        ]

    def _open_item(self, item_id):
        self.nav.go("product", id=item_id, from_="stock")

    def _on_search(self, text):
        self._search = text
        self._refresh()

    def _refresh(self, page: int = 1):
        size = self._table.page_size()
        try:
            payload = api.client.stock(q=self._search.strip() or None,
                                       limit=size, offset=(page - 1) * size)
        except ApiError as exc:
            # Отказ объясняем на месте таблицы: обновление зовётся и при поиске,
            # и при перелистывании — модалка тут завалила бы экран.
            self._table.set_empty_text(exc.title)
            self._table.set_rows([], total=0, keep_page=True)
            return

        self._table.set_empty_text("Ничего не найдено по этим условиям")
        rows = [([r["name"], ("m", r["article"]), ("m", r.get("code1c") or ""),
                  r["unit"], fmt.qty(r["qty"]), fmt.qty(r["free"]),
                  ("m", fmt.qty(r["reserved"]))], r["item_id"])
                for r in payload["items"]]
        self._table.set_rows(rows, total=payload["total"], keep_page=True)
