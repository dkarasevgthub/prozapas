"""Приемка — сканирование коробок отправителя и приём заказа.

Партия приёмки — это наш исходящий заказ в пути. Сканируются те самые коробки,
которые упаковал склад-отправитель: их штрихкоды и ожидаемые веса приходят с
сервера, ничего не синтезируется. Результат скана пишется на коробку, поэтому
отдельного журнала сканов нет.
"""
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .. import api, devices, fmt, theme
from ..api.errors import ApiError, Conflict
from ..widgets.blueprint import BlueprintFrame
from ..widgets.common import Tag, button, h1, h4, icon_button
from ..widgets.dialog import confirm_dialog, weight_dialog
from ..widgets.flow import FlowRow
from ..widgets.table import TableSection
from ._ui import (
    clear_date,
    date_value,
    empty_date_edit,
    filter_action,
    labeled_field,
    number_field,
    number_value,
)
from .base import BlockColumn, Page
from .shipping import PANEL_PAGE_SIZE, _kicker_value

#: Статус приёмки на языке экрана.
STATUS_LABEL = {
    "waiting": ("Ожидает", theme.NEUTRAL[800], theme.NEUTRAL[200]),
    "progress": ("В работе", theme.ACCENT2_RAMP[700], theme.ACCENT2_RAMP[100]),
    "done": ("Принят", theme.ACCENT_RAMP[700], "transparent"),
}


class ReceivingPage(Page):
    def build(self):
        self._active_id = None
        self._active_box = None
        self._scan_error = ""
        self._last_weight = None
        self._live = None               # текущее показание весов, (кг, устойчиво)
        self._scale_value = None        # ярлык панели «Весы», пока она на экране
        self._search = ""
        self._status = "all"
        self._pending_page = 1
        self._scanned_page = 1
        # filters keep their value in the page: the list is rebuilt from
        # scratch every time the user comes back from a batch
        self._weight_min = ""
        self._weight_max = ""
        self._created_from = None
        self._created_to = None
        self._ship_from = None
        self._ship_to = None

        self._container = BlockColumn(page=self)
        self.col.addLayout(self._container)
        self.col.addStretch(1)
        # подключение и отвал оборудования меняют то, что экран может делать
        devices.bus.changed.connect(self._render)
        devices.client.weight_read.connect(self._on_live_weight)
        # экран уходит — поток веса службе больше не нужен
        self.destroyed.connect(lambda: devices.live_weight(False))
        self._render()

    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _render(self):
        self._clear_layout(self._container)
        self._container.reset_blocks()
        self._scale_value = None
        if self._active_id is None:
            self._build_list()
        else:
            self._build_detail()
        # поток нужен только на экране партии, где панель весов видна
        devices.live_weight(self._active_id is not None)

    def _on_live_weight(self, kg, stable):
        """Показание с весов — панель обновляется, не перерисовывая экран."""
        self._live = (kg, stable)
        if self._scale_value is None:
            return
        try:
            self._scale_value.setText(f"{kg:.1f} кг")
            self._scale_value.setStyleSheet(self._scale_style(stable))
        except RuntimeError:
            self._scale_value = None    # ярлык уже уничтожен перерисовкой

    @staticmethod
    def _scale_style(stable):
        color = theme.ACCENT_RAMP[700] if stable else theme.NEUTRAL[600]
        return f"font-family:{theme.font_heading()};font-size:36px;color:{color};"

    # ── list ──
    def _build_list(self):
        crumb = QLabel(f'<a href="#" style="color:{theme.NEUTRAL[600]};text-decoration:none;">ProЗапас</a> / Приемка')
        crumb.setObjectName("breadcrumb"); crumb.setTextFormat(Qt.TextFormat.RichText)
        self._container.add_block(crumb)

        title = QVBoxLayout(); title.setSpacing(4)
        title.addWidget(h1("Приемка"))
        sub = QLabel("Партии от поставщиков, которые нужно принять")
        sub.setObjectName("muted"); title.addWidget(sub)
        self._container.add_block(title)

        self._search_input = QLineEdit(); self._search_input.setPlaceholderText("Номер заказа")
        self._search_input.setText(self._search)
        self._search_input.textChanged.connect(self._on_search)
        self._status_input = QComboBox()
        for v, t in [("all", "Все статусы"), ("waiting", "Ожидает"), ("progress", "В работе"), ("done", "Принят")]:
            self._status_input.addItem(t, v)
        idx = self._status_input.findData(self._status)
        self._status_input.setCurrentIndex(max(idx, 0))
        self._status_input.currentIndexChanged.connect(self._on_status)
        self._weight_min_input = number_field(self._on_weight)
        self._weight_min_input.setText(self._weight_min)
        self._weight_max_input = number_field(self._on_weight)
        self._weight_max_input.setText(self._weight_max)
        self._created_from_input = empty_date_edit()
        self._created_to_input = empty_date_edit()
        self._ship_from_input = empty_date_edit()
        self._ship_to_input = empty_date_edit()
        for edit, value in ((self._created_from_input, self._created_from),
                            (self._created_to_input, self._created_to),
                            (self._ship_from_input, self._ship_from),
                            (self._ship_to_input, self._ship_to)):
            if value is not None:
                edit.setDate(QDate(value.year, value.month, value.day))
            edit.dateChanged.connect(self._on_dates)

        filters = FlowRow(h_spacing=theme.SP4, v_spacing=theme.SP3)
        filters.add(labeled_field("Поиск", self._search_input, 150))
        filters.add(labeled_field("Статус", self._status_input, 140))
        filters.add(labeled_field("Вес от, кг", self._weight_min_input, 90))
        filters.add(labeled_field("Вес до, кг", self._weight_max_input, 90))
        filters.add(labeled_field("Дата создания от", self._created_from_input, 130))
        filters.add(labeled_field("Дата создания до", self._created_to_input, 130))
        filters.add(labeled_field("Дата отгрузки от", self._ship_from_input, 130))
        filters.add(labeled_field("Дата отгрузки до", self._ship_to_input, 130))
        reset = icon_button("reset", "Сбросить фильтры"); reset.clicked.connect(self._reset)
        filters.add(filter_action(reset))
        self._container.add_block(filters)

        self._table = TableSection(
            headers=["Заказ", "Статус", "Прогресс", "Вес, кг", "Ответственный", "Создан", "Принят"],
            widths=[72, 116, 96, 96, 0, 124, 124], rows=[],
            on_row_click=self._open, page_size=13, auto_rows=True,
            on_page_change=self._list_rows,
        )
        self._container.add_block(self._table)
        self._list_rows()

    def _list_rows(self, page=1):
        """Заказы, где мы получатель. Фильтры и страницы считает сервер."""
        size = self._table.page_size()
        try:
            payload = api.client.receipts(
                status=None if self._status == "all" else self._status,
                q=self._search.strip() or None,
                weight_min=number_value(self._weight_min_input),
                weight_max=number_value(self._weight_max_input),
                created_from=self._created_from, created_to=self._created_to,
                shipped_from=self._ship_from, shipped_to=self._ship_to,
                limit=size, offset=(page - 1) * size,
            )
        except ApiError as exc:
            self._table.set_empty_text(exc.title)
            self._table.set_rows([], total=0, keep_page=True)
            return

        rows = []
        for it in payload["items"]:
            label, color, bg = STATUS_LABEL.get(it["status"], STATUS_LABEL["waiting"])
            who = it.get("responsible") or {}
            rows.append((
                [("h", "№" + it["number"]), ("tag", label, color, bg),
                 f'{it["boxes_received"]} из {it["boxes_total"]}',
                 ("m", f'{it["weight_expected"]:.1f} кг'),
                 ("m", fmt.short_name(who.get("name", "")) if who else "—"),
                 ("m", fmt.date(it["created_at"])),
                 ("m", fmt.date(it.get("accepted_at")))],
                it["order_id"],
            ))
        self._table.set_rows(rows, total=payload["total"], keep_page=page > 1)

    def _on_search(self, text):
        self._search = text
        self._list_rows()

    def _on_status(self, _):
        self._status = self._status_input.currentData()
        self._list_rows()

    def _on_weight(self, _=None):
        self._weight_min = self._weight_min_input.text()
        self._weight_max = self._weight_max_input.text()
        self._list_rows()

    def _on_dates(self, _=None):
        self._created_from = date_value(self._created_from_input)
        self._created_to = date_value(self._created_to_input)
        self._ship_from = date_value(self._ship_from_input)
        self._ship_to = date_value(self._ship_to_input)
        self._list_rows()

    def _reset(self):
        self._search = ""; self._status = "all"
        self._weight_min = ""; self._weight_max = ""
        self._created_from = self._created_to = None
        self._ship_from = self._ship_to = None
        for edit in (self._search_input, self._weight_min_input, self._weight_max_input):
            edit.blockSignals(True); edit.clear(); edit.blockSignals(False)
        self._status_input.blockSignals(True); self._status_input.setCurrentIndex(0); self._status_input.blockSignals(False)
        for edit in (self._created_from_input, self._created_to_input,
                     self._ship_from_input, self._ship_to_input):
            clear_date(edit)
        self._list_rows()

    def _open(self, bid):
        self._active_id = bid
        self._pending_page = 1
        self._scanned_page = 1
        self._active_box = None
        self._scan_error = ""
        self._last_weight = None
        self._render()

    def _back(self):
        self._active_id = None
        self._render()

    # the arrow first steps out of the open card, and only then leaves the page
    def can_go_back(self):
        return self._active_id is not None or self.nav.can_back()

    def go_back(self):
        if self._active_id is not None:
            self._back()
        else:
            self.nav.back()

    # ── detail ──
    def _build_detail(self):
        try:
            self._doc = api.client.receipt(self._active_id)
        except ApiError as exc:
            self._container.add_block(QLabel(exc.title))
            return
        bd = self._doc
        b = bd["order"]
        boxes = bd["boxes"]
        self._pending = [x for x in boxes if not x.get("received_at")]
        self._scanned = [x for x in boxes if x.get("received_at")]
        scanned, total = len(self._scanned), len(boxes)
        done = bd["status"] == "done"

        crumb = QLabel(
            f'<a href="#home" style="color:{theme.NEUTRAL[600]};text-decoration:none;">ProЗапас</a> / '
            f'<a href="#back" style="color:{theme.NEUTRAL[600]};text-decoration:none;">Приемка</a> / №{b["number"]}')
        crumb.setObjectName("breadcrumb"); crumb.setTextFormat(Qt.TextFormat.RichText)
        crumb.linkActivated.connect(lambda href: self._back() if href == "#back" else self.nav.go("home"))
        self._container.add_block(crumb)

        head = QHBoxLayout()
        left = QVBoxLayout(); left.setSpacing(4)
        left.addWidget(h1(f"Заказ №{b['number']}"))
        sub = QLabel(f'Приём коробок со склада-отправителя — {b["from_warehouse"]["name"]}')
        sub.setObjectName("muted"); left.addWidget(sub)
        head.addLayout(left); head.addStretch(1)
        complete = button("Завершить приемку", "primary")
        complete.clicked.connect(lambda: self._request_complete(b))
        head.addWidget(complete, 0, Qt.AlignmentFlag.AlignTop)
        self._container.add_block(head)

        label, color, bg = STATUS_LABEL.get(bd["status"], STATUS_LABEL["waiting"])
        who = bd.get("responsible") or {}
        info = BlueprintFrame(padding=theme.SP4)
        # `flex-wrap: wrap` in the mockup: the fields move to the next line
        # instead of being squeezed when the window narrows
        irow = FlowRow(h_spacing=theme.SP8, v_spacing=theme.SP4)
        stat = QWidget(); sv = QVBoxLayout(stat); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(2)
        sk = QLabel("Статус"); sk.setObjectName("kicker"); sv.addWidget(sk); sv.addWidget(Tag(label, color, bg))
        irow.add(stat)
        irow.add(_kicker_value("Ответственный",
                               fmt.short_name(who.get("name", "")) if who else "—"))
        irow.add(_kicker_value("Дата отгрузки", fmt.datetime_(b.get("shipped_at"))))
        irow.add(_kicker_value("Дата принятия", fmt.datetime_(b.get("accepted_at"))))
        irow.add(_kicker_value("Дата создания", fmt.datetime_(b["created_at"])))
        irow.add(_kicker_value("Позиций", len({x["article"] for x in boxes})))
        irow.add(_kicker_value("Коробок", total))
        irow.add(_kicker_value("Общий вес",
                               f'{sum(float(x["weight"]) for x in boxes):.1f} кг'))
        irow.add(_kicker_value("Прогресс", f"{scanned} из {total}"))
        info.content_layout().addWidget(irow)
        self._container.add_block(info)

        # scan + scale
        scan_row = QHBoxLayout(); scan_row.setSpacing(theme.SP6)
        scan_row.addWidget(self._scan_panel(bd), 2)
        scan_row.addWidget(self._scale_panel(), 1)
        self._container.add_block(scan_row)

        two = QHBoxLayout(); two.setSpacing(theme.SP6)
        two.addWidget(self._pending_panel(bd), 1)
        two.addWidget(self._scanned_panel(bd), 1)
        self._container.add_block(two)

    def _box_label(self, box):
        """«Коробка 2 из 3» — считается среди коробок того же товара.

        Отдельного поля для этого нет и не нужно: отправитель мог упаковать
        позицию во сколько угодно коробок, и порядок задаёт сам список.
        """
        same = [b for b in self._doc["boxes"] if b["article"] == box["article"]]
        try:
            n = same.index(box) + 1
        except ValueError:
            n = 1
        return f"Коробка {n} из {len(same)}"

    def _scan_panel(self, bd):
        frame = BlueprintFrame(padding=theme.SP4)
        fl = frame.content_layout()
        if self._active_box:
            ab = self._active_box
            top = QHBoxLayout()
            box = QVBoxLayout(); box.setSpacing(2)
            kick = QLabel("В работе"); kick.setStyleSheet(f"font-size:11px;color:{theme.ACCENT_RAMP[700]};text-transform:uppercase;")
            name = QLabel(f'{ab["name"]} · {self._box_label(ab)} · {ab["weight"]} кг')
            name.setStyleSheet(f"font-family:{theme.font_heading()};font-size:18px;")
            meta = QLabel("Поставьте коробку на весы и отсканируйте штрихкод ещё раз для подтверждения")
            meta.setWordWrap(True); meta.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};")
            box.addWidget(kick); box.addWidget(name); box.addWidget(meta)
            top.addLayout(box); top.addStretch(1)
            cancel = button("Отменить", "ghost"); cancel.clicked.connect(self._cancel_active)
            top.addWidget(cancel, 0, Qt.AlignmentFlag.AlignTop)
            fl.addLayout(top)
        else:
            hint = QLabel("Отсканируйте штрихкод коробки или введите его вручную")
            hint.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[600]};")
            fl.addWidget(hint)

        self._scan_input = QLineEdit()
        self._scan_input.setStyleSheet(f"font-family:{theme.font_heading()};")
        self._scan_input.returnPressed.connect(lambda: self._scan(self._scan_input.text()))
        self._scan_input.setPlaceholderText("Штрихкод коробки, например WH1281187201187-02")
        fl.addWidget(self._scan_input)
        if self._scan_error:
            err = QLabel(self._scan_error)
            err.setWordWrap(True); err.setStyleSheet(f"font-size:12px;color:{theme.ACCENT_RAMP[800]};")
            fl.addWidget(err)
        return frame

    def _scale_panel(self):
        frame = BlueprintFrame(padding=theme.SP4)
        fl = frame.content_layout()
        fl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        k = QLabel("Весы"); k.setObjectName("kicker"); k.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Панель всегда показывает текущий вес на весах, включая ноль. Прочерк
        # означает только одно: весов нет либо служба ещё не дала показания.
        stable = True
        if devices.available("scale") and self._live is not None:
            reading, stable = f"{self._live[0]:.1f} кг", self._live[1]
        else:
            reading = f"{self._last_weight} кг" if self._last_weight else "—"
        r = QLabel(reading); r.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r.setStyleSheet(self._scale_style(stable))
        self._scale_value = r
        fl.addWidget(k); fl.addWidget(r)
        return frame

    def _pending_panel(self, bd):
        frame = BlueprintFrame(padding=theme.SP4)
        fl = frame.content_layout()
        fl.addWidget(h4("К сканированию"))

        active = self._active_box["barcode"] if self._active_box else None
        # артикул не показываем: он уже внутри штрихкода, а панель узкая
        rows = [([("h", p["barcode"], theme.ACCENT_RAMP[700]),
                  p["name"],
                  f'{p["weight"]} кг'],
                 p["barcode"],
                 theme.ACCENT_RAMP[100] if p["barcode"] == active else None)
                for p in self._pending]

        section = TableSection(
            headers=["Штрихкод", "Наименование", "Вес по заказу"],
            widths=[90, 80, 80], rows=rows, elastic={1},
            page_size=PANEL_PAGE_SIZE, auto_rows=True, framed=False,
            empty_text="Все коробки отсканированы",
            on_page_change=lambda n: setattr(self, "_pending_page", n),
        )
        section.set_page(self._pending_page)
        fl.addWidget(section)
        # the two panels are stretched to the same height; the spare space
        # belongs at the bottom, not between the heading and the table
        fl.addStretch(1)
        return frame

    def _scanned_panel(self, bd):
        frame = BlueprintFrame(padding=theme.SP4)
        fl = frame.content_layout()
        fl.addWidget(h4("Отсканировано"))

        rows = []
        for box in self._scanned:
            # Расхождение считает сервер: хранить разность двух известных чисел
            # незачем, а расходиться она умеет.
            diff_pct = box.get("diff_percent")
            sign = "+" if (diff_pct or 0) >= 0 else "−"
            cells = [("h", box["barcode"], theme.NEUTRAL[600]),
                     box["name"], f'{box["actual_weight"]} кг',
                     ("m", f'{sign}{abs(diff_pct):.1f} %' if diff_pct is not None else "—")]
            # принятая партия — закрытый документ, из неё ничего не забирают
            cells.append("" if bd["status"] == "done"
                         else ("w", lambda bc=box["barcode"]: self._undo_cell(bc)))
            rows.append((cells, box["barcode"]))

        section = TableSection(
            # заголовки укорочены: панель занимает половину ширины, и с полными
            # подписями колонка с действием уезжала за край
            headers=["Штрихкод", "Наименование", "Вес", "Расхожд.", ""],
            widths=[80, 62, 56, 60, 34], rows=rows, elastic={1},
            page_size=PANEL_PAGE_SIZE, auto_rows=True, framed=False,
            empty_text="Пока ничего не отсканировано",
            on_page_change=lambda n: setattr(self, "_scanned_page", n),
        )
        section.set_page(self._scanned_page)
        fl.addWidget(section)
        # the two panels are stretched to the same height; the spare space
        # belongs at the bottom, not between the heading and the table
        fl.addStretch(1)
        return frame

    def _undo_cell(self, barcode):
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        btn = icon_button("trash", "Убрать из отсканированных", theme.ACCENT,
                          size=15, box=26, ghost=True)
        btn.clicked.connect(lambda _=False, bc=barcode: self._undo_scan(bc))
        lay.addWidget(btn)
        return wrap

    def _undo_scan(self, barcode):
        """Убрать коробку из принятых — она возвращается в очередь."""
        if self._doc["status"] == "done":
            return
        try:
            api.client.undo_receive(self._active_id, barcode)
        except ApiError as exc:
            self.show_error(exc)
            return
        if self._active_box and self._active_box["barcode"] == barcode:
            self._active_box = None
        self._scan_error = ""
        self._render()

    # ── scanning ──
    def on_scan(self, code):
        """Штрихкод от сканера — принимаем только в открытой партии."""
        if self._active_id is not None:
            self._scan(code)

    def _scan(self, barcode):
        code = (barcode or "").strip().upper()
        if not code:
            return
        if not self._active_box:
            item = next((p for p in self._pending if p["barcode"] == code), None)
            if not item:
                self._scan_error = "Штрихкод не найден в этой партии."
                self._render()
                return
            self._active_box = item
            self._scan_error = ""
            self._render()
            return
        if code != self._active_box["barcode"]:
            self._scan_error = "Это другая коробка. Поставьте на весы ту же коробку и отсканируйте ещё раз."
            self._render()
            return

        item = self._active_box
        expected = float(item["weight"])
        # Второй скан той же коробки фиксирует вес: коробка уже на весах, и
        # записывается ровно то число, которое кладовщик видит на панели.
        if devices.available("scale") and self._live is not None:
            actual = self._live[0]
        else:
            # Весов нет (или поток ещё ничего не дал) — спрашиваем вручную
            actual = weight_dialog(self, hint=f"по заказу {expected:.1f} кг")
            if actual is None:
                return
        try:
            # Идемпотентно: коробку уже приняли — сервер вернёт её как есть.
            # Расхождение считает он же, клиент шлёт только факт.
            api.client.receive_box(self._active_id, item["barcode"], actual)
        except ApiError as exc:
            self.show_error(exc)
            return
        self._last_weight = f"{actual:.1f}"
        self._active_box = None
        self._scan_error = ""
        self._render()

    def _cancel_active(self):
        self._active_box = None
        self._scan_error = ""
        self._render()

    # ── complete ──
    def _request_complete(self, b):
        if self._pending:
            lines = "\n".join(f'· {p["barcode"]} — {p["name"]}' for p in self._pending)
            if not confirm_dialog(
                    self, "Не все коробки отсканированы",
                    f"Осталось не отсканировано: {len(self._pending)}. "
                    f"Эти коробки будут отмечены как недостача.\n\n{lines}",
                    confirm_label="Завершить с недостачей"):
                return
        self._complete(b)

    def _complete(self, b):
        """Завершение — одна транзакция на сервере: оприходование и статусы.

        Коробки без отметки о приёмке становятся зафиксированной недостачей;
        заказ всё равно переходит в «Принят».
        """
        try:
            api.client.complete_receipt(self._active_id, self._doc["version"])
        except Conflict:
            self.show_error(type("_", (), {"title":
                "Приёмку уже изменили в другом месте. Открываю текущее состояние."})())
        except ApiError as exc:
            self.show_error(exc)
            return
        self._active_box = None
        self._render()
