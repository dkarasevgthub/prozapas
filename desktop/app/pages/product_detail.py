"""Товар — product card: details, stock, movement history, operations."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import api, fmt, theme
from ..api.errors import ApiError
from ..session import session
from ..widgets.blueprint import BlueprintFrame
from ..widgets.common import button, h1, h4, icon_button
from ..widgets.dialog import form_dialog
from ..widgets.table import TableSection
from ._ui import stat_grid
from .base import Page

#: Тип движения → как это называется на экране. Порядок задаёт фильтр.
MOVEMENT_LABELS = [
    ("receipt", "Приемка"), ("shipment", "Отгрузка"), ("writeoff", "Списание"),
    ("recount", "Корректировка"), ("reserve", "Резерв"), ("unreserve", "Снятие резерва"),
]
LABEL_OF = dict(MOVEMENT_LABELS)

#: Ручные операции. Резерв и снятие резерва вручную не проводят — их ставит заказ.
MANUAL_TYPES = [("receipt", "Приемка"), ("shipment", "Отгрузка"),
                ("writeoff", "Списание"),
                ("recount", "Корректировка (пересчёт остатка)")]


class ProductDetailPage(Page):
    def build(self):
        self._item_id = self.params.get("id")
        self._from = self.params.get("from_", "catalog")
        self._type = "all"

        try:
            item = api.client.item(self._item_id)
        except ApiError:
            item = None

        back = "Остатки" if self._from == "stock" else "Справочник"
        crumb = QLabel(
            f'<a href="#home" style="color:{theme.NEUTRAL[600]};text-decoration:none;">ProЗапас</a> / '
            f'<a href="#back" style="color:{theme.NEUTRAL[600]};text-decoration:none;">{back}</a> / '
            + (item["article"] if item else "—"))
        crumb.setObjectName("breadcrumb")
        crumb.setTextFormat(Qt.TextFormat.RichText)
        crumb.linkActivated.connect(
            lambda href: self.nav.go(self._from) if href == "#back" else self.nav.go("home"))
        self.add_block(crumb)

        if not item:
            self._not_found()
            return
        self._item = item
        self._render(item)

    def _not_found(self):
        frame = BlueprintFrame(padding=theme.SP8)
        fl = frame.content_layout(); fl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel("Товар не найден")
        t.setStyleSheet(f"font-family:{theme.font_heading()};font-size:25px;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d = QLabel("Проверьте ссылку или вернитесь в справочник.")
        d.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};")
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fl.addWidget(t); fl.addWidget(d)
        self.add_block(frame); self.col.addStretch(1)

    def _render(self, item):
        head = QHBoxLayout()
        head.setSpacing(theme.SP3)
        left = QVBoxLayout(); left.setSpacing(4)
        left.addWidget(h1(item["name"]))
        sub = QLabel("Карточка товара"); sub.setObjectName("muted")
        left.addWidget(sub)
        head.addLayout(left); head.addStretch(1)
        edit = button("Редактировать товар", "secondary")
        edit.clicked.connect(self._edit_item)
        op = button("Провести операцию", "primary")
        op.clicked.connect(self._operation)
        head.addWidget(edit, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(op, 0, Qt.AlignmentFlag.AlignTop)
        self.add_block(head)

        info = BlueprintFrame(padding=theme.SP6)
        irow = QHBoxLayout(); irow.setSpacing(theme.SP8)
        for k, v in [("Артикул", item["article"]),
                     ("Артикул 1С", item.get("code1c") or "—"),
                     ("Единица измерения", item["unit"]),
                     ("Вес единицы", fmt.qty(item["unit_weight"], "кг"))]:
            irow.addWidget(self._cell(k, v))
        irow.addStretch(1)
        info.content_layout().addLayout(irow)
        self.add_block(info)

        self._render_stock(item)
        self._render_history(item)
        self.col.addStretch(1)

    def _render_stock(self, item):
        """Остаток на своём складе — цифрами, по остальным — строкой.

        `/stock/{id}` отдаёт разбивку по всем складам, а карточка про наш: чужой
        остаток здесь справочный, распоряжаться им нельзя.
        """
        try:
            shares = api.client.stock_by_warehouse(item["id"])
        except ApiError as exc:
            self.add_block(h4("Остатки на складе"))
            note = QLabel(exc.title); note.setObjectName("muted")
            self.add_block(note)
            return

        mine = next((s for s in shares
                     if s["warehouse"]["id"] == session.warehouse_id), None)
        unit = item["unit"]
        self.add_block(h4("Остатки на складе"))
        self.add_block(stat_grid([
            ("В наличии", fmt.qty(mine["qty"]) if mine else "0", unit),
            ("Свободно", fmt.qty(mine["free"]) if mine else "0", "доступно к отгрузке"),
            ("Резерв", fmt.qty(mine["reserved"]) if mine else "0", "под заказы клиентов"),
        ], columns=3))

        others = [s for s in shares
                  if s["warehouse"]["id"] != session.warehouse_id and s["qty"] > 0]
        if others:
            text = " · ".join(f'{s["warehouse"]["name"]}: {fmt.qty(s["qty"], unit)}'
                              for s in others)
            note = QLabel("На других складах — " + text)
            note.setObjectName("muted")
            note.setWordWrap(True)
            self.add_block(note)

    def _render_history(self, item):
        frame = BlueprintFrame(padding=theme.SP4)
        hl = frame.content_layout()
        hl.addWidget(h4("История движения"))

        filters = QHBoxLayout(); filters.setSpacing(theme.SP4)
        self._type_input = QComboBox()
        self._type_input.addItem("Все операции", "all")
        for code, label in MOVEMENT_LABELS:
            self._type_input.addItem(label, code)
        self._type_input.currentIndexChanged.connect(self._on_type)
        fbox = QWidget(); fb = QVBoxLayout(fbox)
        fb.setContentsMargins(0, 0, 0, 0); fb.setSpacing(5)
        flabel = QLabel("Операция")
        flabel.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[700]};")
        fb.addWidget(flabel); fb.addWidget(self._type_input)
        fbox.setFixedWidth(200)
        filters.addWidget(fbox)
        reset = icon_button("reset", "Сбросить фильтры")
        reset.clicked.connect(self._reset)
        filters.addWidget(reset, 0, Qt.AlignmentFlag.AlignBottom)
        filters.addStretch(1)
        hl.addLayout(filters)

        self._table = TableSection(
            headers=["Дата", "Операция", "Документ", "Количество", "Остаток после"],
            widths=[160, 130, 0, 120, 130], rows=[], page_size=10,
            auto_rows=True, framed=False,
            on_page_change=self._refresh_history,   # страницу подгружает сервер
        )
        hl.addWidget(self._table)
        self.add_block(frame)
        self._refresh_history()

    def _cell(self, k, v):
        box = QWidget(); lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        kk = QLabel(k); kk.setObjectName("kicker")
        vv = QLabel(str(v))
        vv.setStyleSheet(f"font-family:{theme.font_heading()};font-size:18px;")
        lay.addWidget(kk); lay.addWidget(vv)
        return box

    # ── история ──
    def _doc_label(self, m):
        """У списания и корректировки документа нет — показываем комментарий."""
        if m.get("doc_type") == "order":
            return f'Заказ №{m["doc_id"]}'
        if m.get("doc_type"):
            return f'{m["doc_type"]} №{m["doc_id"]}'
        return m.get("comment") or "—"

    def _refresh_history(self, page: int = 1):
        size = self._table.page_size()
        try:
            payload = api.client.movements(
                self._item["id"], type=None if self._type == "all" else self._type,
                limit=size, offset=(page - 1) * size)
        except ApiError as exc:
            self._table.set_empty_text(exc.title)
            self._table.set_rows([], total=0, keep_page=True)
            return

        self._table.set_empty_text("Движений по этим условиям нет")
        unit = self._item["unit"]
        rows = []
        for m in payload["items"]:
            delta = m["delta"]
            sign = "+" if delta >= 0 else "−"
            rows.append(([
                ("m", fmt.datetime_(m["created_at"])),
                LABEL_OF.get(m["type"], m["type"]),
                ("m", self._doc_label(m)),
                f'{sign}{fmt.qty(abs(delta), unit)}',
                fmt.qty(m["balance_after"], unit),
            ], None))
        self._table.set_rows(rows, total=payload["total"], keep_page=True)

    def _on_type(self, _):
        self._type = self._type_input.currentData()
        self._refresh_history()

    def _reset(self):
        self._type = "all"
        self._type_input.blockSignals(True)
        self._type_input.setCurrentIndex(0)
        self._type_input.blockSignals(False)
        self._refresh_history()

    # ── операции ──
    def _operation(self):
        item = self._item
        fields = [
            ("type", "Тип операции", "select", "writeoff", MANUAL_TYPES),
            ("qty", "Количество", "text", ""),
            ("comment", "Комментарий", "text", ""),
        ]

        def on_save(v):
            try:
                qty = float(v["qty"].replace(",", "."))
                if qty < 0:
                    raise ValueError
            except ValueError:
                return "Введите корректное количество."
            try:
                # Пересчёт остатка и проверку «не списать больше, чем есть»
                # делает сервер: только он знает остаток на момент операции.
                api.client.stock_operation(item["article"], session.warehouse_id,
                                           v["type"], qty, v["comment"].strip())
            except ApiError as exc:
                return exc.title
            return None

        if form_dialog(self, "Провести операцию", fields, on_save,
                       submit_label="Провести"):
            self.nav.go("product", id=item["id"], from_=self._from)

    def _edit_item(self):
        item = self._item
        fields = [
            ("name", "Наименование", "text", item["name"]),
            ("article", "Артикул", "text", item["article"]),
            ("code1c", "Артикул 1С", "text", item.get("code1c") or ""),
            ("unit", "Единица измерения", "text", item["unit"]),
            ("unit_weight", "Вес единицы, кг", "text", str(item["unit_weight"])),
        ]

        def on_save(v):
            if not v["name"].strip() or not v["article"].strip():
                return "Заполните наименование и артикул."
            try:
                weight = float(v["unit_weight"].replace(",", "."))
                if weight < 0:
                    raise ValueError
            except ValueError:
                return "Введите корректный вес единицы."
            try:
                api.client.update_item(
                    item["id"], name=v["name"].strip(), article=v["article"].strip(),
                    code1c=v["code1c"].strip(), unit=v["unit"].strip(),
                    unit_weight=weight)
            except ApiError as exc:
                return exc.title
            return None

        if form_dialog(self, "Редактировать товар", fields, on_save):
            self.nav.go("product", id=item["id"], from_=self._from)
