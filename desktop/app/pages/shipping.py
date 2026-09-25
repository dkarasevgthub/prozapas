"""Отгрузка — pick, weigh, box and ship outgoing (theirs) orders.

List ⇄ detail is handled inside the page so packing progress survives the
toggle. Boxes live on the server: the client posts qty and weight, the
barcode comes back.
"""
import time

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import api, config, devices, fmt, theme
from ..api.errors import ApiError, Conflict
from ..widgets.blueprint import BlueprintFrame
from ..widgets.common import Tag, button, h1, h4, icon_button
from ..widgets.dialog import confirm_dialog, weight_dialog
from ..widgets.flow import FlowRow
from ..widgets.table import (
    TableSection,
    style_table,
)
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

PANEL_PAGE_SIZE = 8      # rows per page in the two detail panels

#: Статус отгрузки на языке экрана.
STATUS_LABEL = {
    "waiting": ("Ожидает", theme.NEUTRAL[800], theme.NEUTRAL[200]),
    "progress": ("В сборке", theme.ACCENT2_RAMP[700], theme.ACCENT2_RAMP[100]),
    "done": ("Отгружен", theme.ACCENT_RAMP[700], "transparent"),
}


NO_PRINTER = "Принтер недоступен — упаковка и отгрузка заблокированы"


def _kicker_value(kicker, value):
    box = QWidget()
    v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
    k = QLabel(kicker); k.setObjectName("kicker")
    val = QLabel(str(value)); val.setStyleSheet(f"font-family:{theme.font_heading()};font-size:18px;")
    v.addWidget(k); v.addWidget(val)
    return box


def config_table(table, widths):
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    style_table(table)
    hdr = table.horizontalHeader()
    for i in range(table.columnCount()):
        hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
    # widths act as per-column minimums; the rest follows the table's width
    table._width_floors = list(widths)


def cell(table, r, c, text, color=None, heading=False):
    it = QTableWidgetItem(str(text))
    if color:
        it.setForeground(QColor(color))
    if heading:
        f = QFont(theme.HEADING_FAMILY); f.setPixelSize(14); it.setFont(f)
    table.setItem(r, c, it)


class ShippingPage(Page):
    def build(self):
        self._active_id = None
        self._active_article = None
        self._weighed = None
        self._live = None               # текущее показание весов, (кг, устойчиво)
        self._scale_value = None        # ярлык панели «Весы», пока она на экране
        self._print_msg = ""
        self._search = ""
        self._status = "all"
        self._topack_page = 1
        self._boxes_page = 1
        # filters keep their value in the page: the list is rebuilt from
        # scratch every time the user comes back from a shipment
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

    def _clear(self):
        self._clear_layout(self._container)
        self._container.reset_blocks()

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
        self._clear()
        self._scale_value = None
        if self._active_id is None:
            self._build_list()
        else:
            self._build_detail()
        # поток нужен только на экране заказа, где панель весов видна
        devices.live_weight(self._active_id is not None)

    def _on_live_weight(self, kg, stable):
        """Показание с весов — панель обновляется, не перерисовывая экран.

        Панель показывает то, что на весах прямо сейчас, всегда. Вес уже
        взвешенной коробки живёт отдельной метрикой и потоком не затирается.
        """
        self._live = (kg, stable)
        if self._scale_value is None:
            return
        try:
            self._scale_value.setText(f"{kg:.2f} кг")
            self._scale_value.setStyleSheet(self._scale_style(stable))
        except RuntimeError:
            self._scale_value = None    # ярлык уже уничтожен перерисовкой

    @staticmethod
    def _scale_style(stable):
        color = theme.ACCENT_RAMP[700] if stable else theme.NEUTRAL[600]
        return f"font-family:{theme.font_heading()};font-size:36px;color:{color};"


    # ── list ──
    def _build_list(self):
        crumb = QLabel(f'<a href="#" style="color:{theme.NEUTRAL[600]};text-decoration:none;">ProЗапас</a> / Отгрузка')
        crumb.setObjectName("breadcrumb"); crumb.setTextFormat(Qt.TextFormat.RichText)
        self._container.add_block(crumb)

        title = QVBoxLayout(); title.setSpacing(4)
        title.addWidget(h1("Отгрузка"))
        sub = QLabel("Заявки других складов, которые нужно собрать и отгрузить")
        sub.setObjectName("muted"); title.addWidget(sub)
        self._container.add_block(title)

        self._search_input = QLineEdit(); self._search_input.setPlaceholderText("Номер заказа")
        self._search_input.setText(self._search)
        self._search_input.textChanged.connect(self._on_search)
        self._status_input = QComboBox()
        for v, t in [("all", "Все статусы"), ("waiting", "Ожидает"), ("progress", "В сборке"), ("done", "Отгружен")]:
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
            headers=["Заказ", "Статус", "Прогресс", "Вес, кг", "Ответственный", "Создан", "Отгружен"],
            widths=[72, 116, 96, 96, 0, 128, 128], rows=[],
            on_row_click=self._open, page_size=13, auto_rows=True,
            on_page_change=self._list_rows,
        )
        self._container.add_block(self._table)
        self._list_rows()

    def _list_rows(self, page=1):
        """Фильтры и страницы считает сервер: видны только заказы, где мы
        отправитель."""
        size = self._table.page_size()
        try:
            payload = api.client.shipments(
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
                 f'{it["positions_packed"]} из {it["positions_total"]}',
                 ("m", f'{it["weight_expected"]:.1f} кг'),
                 ("m", fmt.short_name(who.get("name", "")) if who else "—"),
                 ("m", fmt.datetime_(it["created_at"])),
                 ("m", fmt.datetime_(it.get("shipped_at")))],
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

    def _open(self, oid):
        self._active_id = oid
        self._topack_page = 1
        self._boxes_page = 1
        self._active_article = None
        self._weighed = None
        self._live = None               # текущее показание весов, (кг, устойчиво)
        self._scale_value = None        # ярлык панели «Весы», пока она на экране
        self._print_msg = ""
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
            self._doc = api.client.shipment(self._active_id)
        except ApiError as exc:
            self._container.add_block(QLabel(exc.title))
            return
        o = self._doc["order"]
        pd = self._doc
        done = self._doc["status"] == "done"

        crumb = QLabel(
            f'<a href="#home" style="color:{theme.NEUTRAL[600]};text-decoration:none;">ProЗапас</a> / '
            f'<a href="#back" style="color:{theme.NEUTRAL[600]};text-decoration:none;">Отгрузка</a> / №{o["number"]}')
        crumb.setObjectName("breadcrumb"); crumb.setTextFormat(Qt.TextFormat.RichText)
        crumb.linkActivated.connect(lambda href: self._back() if href == "#back" else self.nav.go("home"))
        self._container.add_block(crumb)

        head = QHBoxLayout()
        left = QVBoxLayout(); left.setSpacing(4)
        left.addWidget(h1(f'Заказ №{o["number"]}'))
        sub = QLabel("Упаковка позиций и отгрузка на склад-получатель")
        sub.setObjectName("muted"); left.addWidget(sub)
        head.addLayout(left); head.addStretch(1)
        if not done:
            ship = button("Отгрузить", "primary"); ship.clicked.connect(lambda: self._request_ship(o))
            if not devices.available("printer"):
                ship.setEnabled(False)
                ship.setToolTip(NO_PRINTER)
            head.addWidget(ship, 0, Qt.AlignmentFlag.AlignTop)
        self._container.add_block(head)

        label, color, bg = STATUS_LABEL.get(pd["status"], STATUS_LABEL["waiting"])
        packed_weight = sum(float(b["weight"]) for b in pd["boxes"])
        fully = len([p for p in pd["to_pack"] if p["remaining"] <= 0])
        who = pd.get("responsible") or {}
        info = BlueprintFrame(padding=theme.SP4)
        # `flex-wrap: wrap` in the mockup: the fields move to the next line
        # instead of being squeezed when the window narrows
        irow = FlowRow(h_spacing=theme.SP8, v_spacing=theme.SP4)
        stat = QWidget(); sv = QVBoxLayout(stat); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(2)
        sk = QLabel("Статус"); sk.setObjectName("kicker"); sv.addWidget(sk); sv.addWidget(Tag(label, color, bg))
        irow.add(stat)
        irow.add(_kicker_value("Ответственный",
                               fmt.short_name(who.get("name", "")) if who else "—"))
        irow.add(_kicker_value("Дата создания", fmt.datetime_(o["created_at"])))
        irow.add(_kicker_value("Дата отгрузки", fmt.datetime_(o.get("shipped_at"))))
        irow.add(_kicker_value("Позиций", len(pd["to_pack"])))
        irow.add(_kicker_value("Коробок", len(pd["boxes"])))
        irow.add(_kicker_value("Упаковано, вес", f"{packed_weight:.1f} кг"))
        irow.add(_kicker_value("Прогресс", f"{fully} из {len(pd['to_pack'])}"))
        info.content_layout().addWidget(irow)
        self._container.add_block(info)

        if not done:
            pack_row = QHBoxLayout(); pack_row.setSpacing(theme.SP6)
            pack_row.addWidget(self._packing_panel(o, pd), 2)
            pack_row.addWidget(self._scale_panel(), 1)
            self._container.add_block(pack_row)

        two = QHBoxLayout(); two.setSpacing(theme.SP6)
        two.addWidget(self._topack_panel(o, pd, done), 1)
        two.addWidget(self._boxes_panel(o, pd, done), 1)
        self._container.add_block(two)

    def _packing_panel(self, o, pd):
        frame = BlueprintFrame(padding=theme.SP4)
        fl = frame.content_layout()
        if not devices.available("printer"):
            note = QLabel(
                "Каждая коробка маркируется этикеткой, поэтому без принтера "
                "упаковать позицию и отгрузить заказ нельзя. Подключите принтер.")
            note.setWordWrap(True)
            note.setStyleSheet(f"font-size:13px;color:{theme.DANGER};")
            fl.addWidget(note)
            return frame

        if self._active_article:
            pos = next(p for p in pd["to_pack"] if p["article"] == self._active_article)
            remaining = pos["remaining"]
            top = QHBoxLayout()
            box = QVBoxLayout();
            box.setSpacing(2)
            kick = QLabel("Упаковка позиции")
            kick.setStyleSheet(f"font-size:11px;color:{theme.ACCENT_RAMP[700]};text-transform:uppercase;")
            name = QLabel(pos["name"]);
            name.setStyleSheet(f"font-family:{theme.font_heading()};font-size:18px;")
            meta = QLabel(f'Артикул {pos["article"]} · осталось упаковать {remaining} {pos["unit"]}')
            meta.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};")
            box.addWidget(kick);
            box.addWidget(name);
            box.addWidget(meta)
            top.addLayout(box);
            top.addStretch(1)
            cancel = button("Отменить", "ghost");
            cancel.clicked.connect(self._cancel_active)
            top.addWidget(cancel, 0, Qt.AlignmentFlag.AlignTop)
            fl.addLayout(top)

            if self._weighed is None:
                hint = QLabel("Упакуйте позицию в коробку, поставьте её на весы и нажмите «Считать вес».")
                hint.setWordWrap(True);
                hint.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};")
                fl.addWidget(hint)
                label = "Считать вес" if devices.available("scale") else "Ввести вес"
                read = button(label, "secondary");
                read.clicked.connect(lambda: self._read_scale(o, pd))
                fl.addWidget(read, 0, Qt.AlignmentFlag.AlignLeft)
            else:
                res = QHBoxLayout();
                res.setSpacing(theme.SP6)
                res.addWidget(self._big_metric("Вес коробки", f'{self._weighed["weight"]} кг', theme.ACCENT_RAMP[700]))
                res.addWidget(self._big_metric("Количество (расчёт по весу)", f'{self._weighed["qty"]} {pos["unit"]}',
                                               theme.TEXT))
                res.addStretch(1)
                fl.addLayout(res)
                btns = QHBoxLayout()
                pr = button("Напечатать этикетку", "primary")
                pr.clicked.connect(lambda: self._print_label(o, pd))
                rw = button("Взвесить заново", "secondary");
                rw.clicked.connect(self._reweigh)
                btns.addWidget(pr);
                btns.addWidget(rw);
                btns.addStretch(1)
                fl.addLayout(btns)
        else:
            hint = QLabel("Выберите позицию в списке «К упаковке», чтобы начать упаковку коробки.")
            hint.setWordWrap(True);
            hint.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};")
            fl.addWidget(hint)

        if self._print_msg:
            pm = QLabel(self._print_msg)
            pm.setStyleSheet(f"font-size:12px;color:{theme.ACCENT_RAMP[800]};")
            fl.addWidget(pm)
        return frame

    def _big_metric(self, kicker, value, color):
        box = QWidget()
        v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
        k = QLabel(kicker); k.setObjectName("kicker")
        val = QLabel(str(value)); val.setStyleSheet(f"font-family:{theme.font_heading()};font-size:22px;color:{color};")
        v.addWidget(k); v.addWidget(val)
        return box

    def _scale_panel(self):
        frame = BlueprintFrame(padding=theme.SP4)
        fl = frame.content_layout()
        fl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        k = QLabel("Весы"); k.setObjectName("kicker"); k.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Панель всегда показывает текущий вес на весах, включая ноль. Прочерк
        # означает только одно: весов нет либо служба ещё не дала показания.
        stable = True
        if devices.available("scale") and self._live is not None:
            reading, stable = f"{self._live[0]:.2f} кг", self._live[1]
        else:
            reading = "—"
        r = QLabel(reading); r.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r.setStyleSheet(self._scale_style(stable))
        self._scale_value = r
        fl.addWidget(k); fl.addWidget(r)
        return frame

    def _topack_panel(self, o, pd, done):
        frame = BlueprintFrame(padding=theme.SP4)
        fl = frame.content_layout()
        fl.addWidget(h4("К упаковке"))

        # fully packed positions sink to the bottom, as in the mockup
        # Сервер уже посчитал заказано/упаковано/осталось по каждой позиции.
        positions = sorted(pd["to_pack"], key=lambda p: 1 if p["remaining"] <= 0 else 0)
        rows = []
        for p in positions:
            packed, remaining = p["packed"], p["remaining"]
            spent = remaining <= 0
            # a finished position is greyed out whole; the one being packed is
            # tinted through the row background
            muted = theme.NEUTRAL[500] if spent else None
            rows.append((
                [("c", p["article"], muted or theme.NEUTRAL[600]),
                 ("c", p["name"], muted),
                 ("c", fmt.qty(p["ordered"], p["unit"]), muted),
                 ("c", fmt.qty(packed, p["unit"]), muted),
                 ("c", "0" if spent else fmt.qty(remaining),
                  muted or (theme.ACCENT_RAMP[700] if spent else theme.TEXT))],
                p["article"],
                theme.ACCENT_RAMP[100] if (not spent and self._active_article == p["article"]) else None,
            ))

        def on_click(article):
            pos = next((p for p in pd["to_pack"] if p["article"] == article), None)
            if pos is not None and pos["remaining"] > 0:
                self._select_position(article)

        section = TableSection(
            headers=["Артикул", "Наименование", "Заказано", "Упаковано", "Осталось"],
            widths=[70, 80, 62, 62, 62], rows=rows, elastic={1},
            on_row_click=None if done else on_click,
            page_size=PANEL_PAGE_SIZE, auto_rows=True, framed=False,
            empty_text="В заказе нет позиций",
            on_page_change=lambda n: setattr(self, "_topack_page", n),
        )
        section.set_page(self._topack_page)
        fl.addWidget(section)
        # the two panels are stretched to the same height; the spare space
        # belongs at the bottom, not between the heading and the table
        fl.addStretch(1)
        return frame

    def _boxes_panel(self, o, pd, done):
        frame = BlueprintFrame(padding=theme.SP4)
        fl = frame.content_layout()
        fl.addWidget(h4(f"Упаковано ({len(pd['boxes'])})"))

        rows = []
        for b in pd["boxes"]:
            # артикул не показываем: он внутри штрихкода, а панель узкая —
            # иначе колонка с кнопками уезжает за край при небольшом окне
            cells = [("h", b["barcode"], theme.ACCENT_RAMP[700]),
                     fmt.qty(b["qty"], self._unit_of(b["article"])),
                     f'{b["weight"]} кг']
            cells.append("" if done else ("w", lambda box=b: self._box_actions(box)))
            rows.append((cells, b["barcode"]))

        section = TableSection(
            headers=["Штрихкод", "Кол-во", "Вес", ""],
            widths=[90, 60, 60, 62], rows=rows,
            page_size=PANEL_PAGE_SIZE, auto_rows=True, framed=False,
            empty_text="Ещё нет упакованных коробок",
            on_page_change=lambda n: setattr(self, "_boxes_page", n),
        )
        section.set_page(self._boxes_page)
        fl.addWidget(section)
        # the two panels are stretched to the same height; the spare space
        # belongs at the bottom, not between the heading and the table
        fl.addStretch(1)
        return frame

    def _unit_of(self, article):
        """Единица берётся из позиций заказа: коробка её не дублирует."""
        pos = next((p for p in self._doc["to_pack"] if p["article"] == article), None)
        return (pos or {}).get("unit", "")

    def _box_actions(self, box):
        """Reprint / delete — the mockup's last column."""
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addStretch(1)               # действия прижаты к правому краю строки
        has_printer = devices.available("printer")
        reprint = icon_button("printer",
                              "Перепечатать этикетку" if has_printer else NO_PRINTER,
                              theme.ACCENT, size=15, box=26, ghost=True)
        reprint.setEnabled(has_printer)
        reprint.clicked.connect(lambda _=False, b=box: self._reprint_box(b))
        remove = icon_button("trash", "Удалить коробку", theme.ACCENT,
                             size=15, box=26, ghost=True)
        remove.clicked.connect(lambda _=False, b=box: self._delete_box(b))
        lay.addWidget(reprint)
        lay.addWidget(remove)
        return wrap

    def _reprint_box(self, box):
        if not devices.available("printer"):
            return
        # Ключ обязан быть новым: по совпадающему служба считает задание
        # повтором и возвращает старое, ничего не печатая. Идемпотентность
        # нужна первой печати (защита от двойного клика), а перепечатка —
        # это осознанная просьба выдать ещё одну этикетку.
        devices.print_label(
            key=f'box-{box["barcode"]}#{time.time_ns()}',
            payload=self._label_zpl(box["barcode"], box.get("name", ""), box["qty"],
                                    self._unit_of(box["article"]), box["weight"]),
        )
        self._print_msg = f'Этикетка {box["barcode"]} отправлена на печать'
        self._render()

    def _select_position(self, article):
        self._active_article = article
        self._weighed = None
        self._render()

    def _cancel_active(self):
        self._active_article = None
        self._weighed = None
        self._render()

    def _read_scale(self, o, pd):
        pos = next(p for p in pd["to_pack"] if p["article"] == self._active_article)
        remaining = pos["remaining"]
        if remaining <= 0:
            return
        uw = pos["unit_weight"]
        # Весы на связи — записываем ровно то число, которое кладовщик видит на
        # панели. Ни запроса к службе, ни подтверждения: коробка уже на весах.
        if devices.available("scale") and self._live is not None:
            weight = self._live[0]
        else:
            # Весов нет (или поток ещё ничего не дал) — оператор читает табло сам
            weight = weight_dialog(self)
            if weight is None:
                return
        computed = max(1, round(weight / uw))
        self._weighed = {"weight": f"{weight:.2f}", "qty": min(computed, remaining)}
        self._render()

    def _reweigh(self):
        self._weighed = None
        self._render()

    @staticmethod
    def _label_zpl(barcode, name, qty, unit, weight):
        """Этикетка коробки. Размер и плотность печати — из настроек.

        Раньше 58×40 мм при 203 dpi стояли числами прямо в команде: на принтере
        с другой плотностью макет уезжал за край, и поправить это без пересборки
        было нельзя.

        Макет живёт здесь только до появления сервера: по плану шаблон хранится
        на нём, чтобы правка не требовала пересборки приложения.
        """
        dpi = config.number("PROZAPAS_LABEL_DPI")
        dots = lambda mm: round(mm / 25.4 * dpi)      # noqa: E731
        width = dots(config.number("PROZAPAS_LABEL_WIDTH_MM"))
        height = dots(config.number("PROZAPAS_LABEL_HEIGHT_MM"))
        pad = dots(3)
        title = name[:28]
        return (
            "^XA"
            "^CI28"                       # UTF-8, иначе кириллица уедет
            f"^PW{width}^LL{height}"
            f"^FO{pad},{pad}^A0N,28,28^FD{title}^FS"
            f"^FO{pad},{pad + dots(5)}^A0N,24,24^FD{qty} {unit} / {weight} кг^FS"
            f"^FO{pad},{pad + dots(11)}^BY2^BCN,{dots(11)},Y,N,N^FD{barcode}^FS"
            "^XZ"
        )

    def _print_label(self, o, pd):
        """Коробка создаётся на сервере, потом печатается этикетка.

        Порядок именно такой: зажевало ленту — коробка на месте, этикетку
        перепечатают. Не вышла вовсе — коробку удаляют, поэтому отметки о
        печати в базе и не нужно.
        """
        if not devices.available("printer"):
            return
        pos = next(p for p in pd["to_pack"] if p["article"] == self._active_article)
        try:
            box = api.client.pack_box(self._active_id, pos["article"],
                                      self._weighed["qty"], self._weighed["weight"])
        except ApiError as exc:
            self.show_error(exc)
            return

        devices.print_label(
            key=f'box-{box["barcode"]}',
            payload=self._label_zpl(box["barcode"], pos["name"], box["qty"],
                                    pos["unit"], box["weight"]),
        )
        self._weighed = None
        if pos["remaining"] - box["qty"] <= 0:
            self._active_article = None
        self._print_msg = f'Этикетка {box["barcode"]} отправлена на печать'
        self._render()

    def _delete_box(self, box):
        try:
            api.client.delete_box(self._active_id, box["id"])
        except ApiError as exc:
            self.show_error(exc)
            return
        self._print_msg = ""
        self._render()

    def _request_ship(self, o):
        shortage = [p for p in self._doc["to_pack"] if p["remaining"] > 0]
        if shortage:
            lines = "\n".join(f'· {p["name"]} — осталось {fmt.qty(p["remaining"], p["unit"])}'
                               for p in shortage)
            if not confirm_dialog(
                    self, "Упакованы не все позиции",
                    f"Осталось упаковать позиций: {len(shortage)}. "
                    f"Заказ будет отгружен с недостачей.\n\n{lines}",
                    confirm_label="Отгрузить с недостачей"):
                return
        self._ship_now(o)

    def _ship_now(self, o):
        """Отгрузка — одна транзакция на сервере: списание, статусы, движения."""
        try:
            api.client.ship(self._active_id, self._doc["version"])
        except Conflict:
            self.show_error(type("_", (), {"title":
                "Отгрузку уже изменили в другом месте. Открываю текущее состояние."})())
        except ApiError as exc:
            self.show_error(exc)
            return
        self._active_article = None
        self._weighed = None
        self._render()
