"""Справочник — item catalogue."""
from PyQt6.QtWidgets import QHBoxLayout

from .. import api, fmt, theme
from ..api.errors import ApiError
from ..widgets.common import breadcrumb, button
from ..widgets.dialog import form_dialog
from ..widgets.table import TableSection
from ._ui import header_row, labeled_field, search_field
from .base import Page


class CatalogPage(Page):
    def build(self):
        self._search = ""

        self.add_block(breadcrumb("ProЗапас / Справочник"))

        add = button("+ Добавить позицию", "primary")
        add.clicked.connect(self._open_add)
        self.add_block(header_row(
            "Справочник",
            "Номенклатура завода · материалы, комплектующие, готовая продукция",
            add))

        row = QHBoxLayout()
        row.setSpacing(theme.SP4)
        field = search_field("Код, артикул или наименование", self._on_search)
        row.addWidget(labeled_field("Поиск", field, 360), 1)
        row.addStretch(1)
        self.add_block(row)

        self._table = TableSection(
            headers=["Наименование", "Артикул", "Артикул 1С", "Ед. изм.", "Вес единицы"],
            widths=[0, 150, 160, 120, 150],
            rows=[], on_row_click=self._open_item,
            page_size=14, auto_rows=True,
            on_page_change=self._refresh,       # страницу подгружает сервер
        )
        self.add_block(self._table)
        self.col.addStretch(1)

        self._refresh()

    def _open_item(self, item_id):
        self.nav.go("product", id=item_id, from_="catalog")

    def _on_search(self, text):
        self._search = text
        self._refresh()

    def _refresh(self, page: int = 1):
        size = self._table.page_size()
        try:
            payload = api.client.catalog(q=self._search.strip() or None,
                                         limit=size, offset=(page - 1) * size)
        except ApiError as exc:
            self._table.set_empty_text(exc.title)
            self._table.set_rows([], total=0, keep_page=True)
            return

        self._table.set_empty_text("Ничего не найдено по этим условиям")
        rows = [([c["name"], ("m", c["article"]), ("m", c.get("code1c") or ""),
                  # qty, а не weight: шайба весит 0.005 кг, до копеек не округлить
                  c["unit"], fmt.qty(c["unit_weight"], "кг")], c["id"])
                for c in payload["items"]]
        self._table.set_rows(rows, total=payload["total"], keep_page=True)

    def _open_add(self):
        fields = [
            ("name", "Наименование", "text", ""),
            ("article", "Артикул", "text", ""),
            ("code1c", "Артикул 1С", "text", ""),
            ("unit", "Единица измерения", "text", "шт."),
            ("unit_weight", "Вес единицы, кг", "text", ""),
        ]

        def on_save(values):
            if not values["name"].strip() or not values["article"].strip():
                return "Заполните наименование и артикул."
            try:
                weight = float(values["unit_weight"].replace(",", "."))
                if weight < 0:
                    raise ValueError
            except ValueError:
                return "Введите корректный вес единицы."
            try:
                api.client.create_item(
                    values["article"].strip(), values["name"].strip(),
                    values["unit"].strip() or "шт.",
                    code1c=values["code1c"].strip(), unit_weight=weight)
            except ApiError as exc:
                # Занятый артикул сервер отдаёт как 409 — текст показываем в самой
                # форме, чтобы человек поправил артикул и отправил снова.
                return exc.title
            self._refresh()
            return None

        form_dialog(self, "Добавить позицию", fields, on_save, submit_label="Добавить")
