"""Reusable list table: a blueprint-framed QTableWidget with pagination.

Rows are ``(cells, payload)`` pairs. Each cell is one of:
    "text"                     – plain cell
    ("m", "text")              – muted (neutral-600) cell
    ("h", "text")              – heading-font cell (numbers)
    ("tag", text, color, bg)   – a coloured status/role pill
"""
from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from .blueprint import BlueprintFrame
from .common import Tag, button

_CELL_PAD = 24          # QSS item padding (7px a side) + elide margin
ELASTIC_MIN_WIDTH = 90  # narrowest an elastic column may be squeezed to

# measured off the live mockup (ProЗапас Заказы.dc.html)
ROW_HEIGHT = 34         # td: 6.8px padding + 17.5px line box + 1px hairline
HEADER_HEIGHT = 25      # th row: 24.17px
MIN_ROWS = 5            # mockup: Math.max(5, ...) — below this let it scroll
_VERIFY_DELAY_MS = 40   # long enough for Qt to settle the geometry we measure


class RowHoverDelegate(QStyledItemDelegate):
    """`.table tbody tr:hover` highlights the whole row; Qt's `::item:hover`
    would only tint the cell under the cursor, so the row is painted here."""

    _TINT = QColor(29, 31, 32, 10)      # var(--color-text) at 4%

    def __init__(self, table):
        super().__init__(table)
        self._table = table
        self._row = -1
        self._row_colors = {}           # row -> base background
        table.setMouseTracking(True)
        table.viewport().setMouseTracking(True)
        table.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseMove:
            index = self._table.indexAt(event.position().toPoint())
            row = index.row() if index.isValid() else -1
            if row != self._row:
                self._row = row
                self._table.viewport().update()
        elif event.type() == QEvent.Type.Leave and self._row != -1:
            self._row = -1
            self._table.viewport().update()
        return super().eventFilter(obj, event)

    def set_row_colors(self, colors):
        """Base background per row — selected/child rows in the order builder."""
        self._row_colors = colors or {}

    def paint(self, painter, option, index):
        base = self._row_colors.get(index.row())
        if base is not None:
            painter.fillRect(option.rect, QColor(base))
        if index.row() == self._row:
            painter.fillRect(option.rect, self._TINT)
        super().paint(painter, option, index)


def transparent_cell():
    """Container for a widget placed inside a table cell.

    Cell widgets are painted over the row, and the global `QWidget` rule gives
    them an opaque background — which would punch a light rectangle through the
    row-hover tint. The rule is scoped by object name so the child tag keeps
    its own background.
    """
    w = QWidget()
    w.setObjectName("cellwrap")
    w.setStyleSheet("QWidget#cellwrap { background: transparent; }")
    return w


def style_table(table, hover=True):
    """Apply the mockup's table geometry: left/bottom aligned headers, 34px
    rows and a whole-row hover tint."""
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    header.setFixedHeight(HEADER_HEIGHT)
    header.setHighlightSections(False)
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    if hover:
        table._row_hover = RowHoverDelegate(table)      # keep a reference alive
        table.setItemDelegate(table._row_hover)


def content_column_widths(table, floors=None):
    """Width each column needs so nothing is elided: the widest of its header
    and its cell contents. `floors` optionally raises a column's minimum."""
    cols = table.columnCount()
    floors = floors or [0] * cols
    header_fm = QFontMetrics(table.horizontalHeader().font())
    # QTableWidgetItem.font() reports the app default (10pt), not the 14px the
    # table actually paints with, so the table's own metrics are the baseline
    base_fm = QFontMetrics(table.font())
    widths = []
    for c in range(cols):
        head = table.horizontalHeaderItem(c)
        need = header_fm.horizontalAdvance(head.text() if head else "") + _CELL_PAD
        for r in range(table.rowCount()):
            item = table.item(r, c)
            if item is not None:
                text_w = max(base_fm.horizontalAdvance(item.text()),
                             QFontMetrics(item.font()).horizontalAdvance(item.text()))
                need = max(need, text_w + _CELL_PAD)
            else:
                w = table.cellWidget(r, c)
                if w is not None:
                    need = max(need, w.sizeHint().width() + 16)
        widths.append(max(need, floors[c] if c < len(floors) else 0))
    return widths


def autosize_columns(table, floors=None, elastic=None):
    """Fit columns to the table's width: every column keeps at least the width
    its content needs, and any leftover space is shared out evenly, so the
    columns grow uniformly instead of one column eating the slack.

    A column is never taken below the width its content needs, so the table
    scrolls rather than clipping text — except for columns listed in `elastic`,
    which give their width back first. That is what keeps a narrow panel from
    pushing its last column (usually the row actions) out of sight.
    """
    cols = table.columnCount()
    if cols == 0:
        return
    floors = list(floors or [0] * cols)
    floors += [0] * (cols - len(floors))
    widths = content_column_widths(table, floors)
    available = table.viewport().width()
    total = sum(widths)
    if total < available:
        extra, remainder = divmod(available - total, cols)
        widths = [w + extra for w in widths]
        widths[-1] += remainder
    elif elastic:
        need = total - available
        for c in sorted(elastic):
            if need <= 0:
                break
            if c >= cols:
                continue
            # the floor here already carries the measured content width, so an
            # elastic column is bounded only by ELASTIC_MIN_WIDTH
            room = max(0, widths[c] - ELASTIC_MIN_WIDTH)
            take = min(need, room)
            widths[c] -= take
            need -= take
    for c, w in enumerate(widths):
        table.setColumnWidth(c, w)


class _ColumnFitter(QObject):
    """Re-fits a table's columns whenever the table itself is resized."""

    def __init__(self, table, floors=None, elastic=None):
        super().__init__(table)
        self._table = table
        self._floors = floors
        self._elastic = elastic
        self._busy = False
        table.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize and not self._busy:
            self._busy = True
            try:
                autosize_columns(self._table, self._floors, self._elastic)
            finally:
                self._busy = False
        return super().eventFilter(obj, event)


def enable_auto_columns(table, floors=None, elastic=None):
    """Keep `table`'s columns fitted to its width from now on."""
    autosize_columns(table, floors, elastic)
    table._column_fitter = _ColumnFitter(table, floors, elastic)   # keep a reference alive


def _relayout(widget, page):
    """Re-run the layouts from `widget` up to `page`.

    Activating only the page's layout is not enough when the table sits inside
    a panel: the panel would keep its old height and every measurement taken
    right after a re-render would be stale.
    """
    node = widget
    while node is not None and node is not page:
        if node.layout() is not None:
            node.layout().activate()
        node = node.parentWidget()
    if page.layout() is not None:
        page.layout().activate()


def _content_bottom(page):
    """Lowest edge of the page's real content — spacers are skipped, they
    stretch to the bottom of the scroll area and would mask any overflow."""
    layout = page.layout()
    bottom = 0
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.spacerItem() is not None:
            continue
        widget = item.widget()
        if widget is not None:
            bottom = max(bottom, widget.y() + widget.height())
        elif item.layout() is not None:
            geo = item.geometry()
            bottom = max(bottom, geo.y() + geo.height())
    return bottom


def fit_table_height(table):
    """Size a QTableWidget to show all its rows (no inner vertical scroll)."""
    bar = table.horizontalScrollBar()
    bar.setValue(0)
    row_h = table.verticalHeader().defaultSectionSize()
    header_h = table.horizontalHeader().sizeHint().height()
    total = header_h + row_h * table.rowCount() + 2
    if bar.isVisible():        # the h-scrollbar eats vertical space too
        total += bar.sizeHint().height()
    table.setFixedHeight(max(total, header_h + row_h))


class TableSection(QWidget):
    def __init__(self, headers, widths, rows, on_row_click=None,
                 page_size=14, min_width=0, empty_text="Ничего не найдено по этим условиям",
                 auto_rows=False, framed=True, on_page_change=None,
                 elastic=None, parent=None):
        super().__init__(parent)
        self._headers = headers
        self._widths = widths
        self._rows = rows
        self._pinned = []
        self._on_click = on_row_click
        # panels that are rebuilt from scratch report the page back so the
        # caller can restore it (see ShippingPage._render)
        self._on_page_change = on_page_change
        self._page_size = max(1, page_size)
        self._page = 1
        # Серверный режим: страницу присылают готовой, а общее число строк
        # известно только серверу. None — считаем по своему списку, как раньше.
        self._server_total = None
        self._auto_rows = auto_rows
        self._auto_busy = False
        self._watching = False
        self._column_fitter = None
        # re-fitting changes the layout, which fires more resize events; the
        # timer coalesces those and _fitted_for stops us re-solving a size we
        # have already solved (otherwise the two can ping-pong forever)
        self._fitted_for = None
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._apply_auto_rows)
        self._verify_left = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # `framed=False` when the caller already provides a blueprint panel —
        # nesting one frame inside another would double the border and marks
        if framed:
            self._frame = BlueprintFrame(padding=theme.SP4)
            fl = self._frame.content_layout()
        else:
            self._frame = None
            fl = outer
        # gaps are set explicitly below (the pagination row carries the
        # mockup's margin-top: var(--space-4))
        fl.setSpacing(0)

        self._table = QTableWidget(0, len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setWordWrap(False)
        style_table(self._table)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        if min_width:
            self._table.setMinimumWidth(min_width)
        header = self._table.horizontalHeader()
        header.setHighlightSections(False)
        # widths are treated as per-column minimums; the real sizing is done
        # by autosize_columns(). `_floors` is updated in place from the data
        # (see _measure_floors) — the column fitter keeps this very list.
        self._declared = list(widths)
        self._floors = list(widths)
        # columns allowed to give width back when the table is cramped
        self._elastic = set(elastic or ())
        for i in range(len(headers)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
        if on_row_click:
            self._table.setCursor(Qt.CursorShape.PointingHandCursor)
            self._table.cellClicked.connect(self._row_clicked)
        fl.addWidget(self._table)

        self._empty = QLabel(empty_text)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(
            f"font-size:13px;color:{theme.NEUTRAL[600]};padding:{theme.SP8}px 0;"
        )
        fl.addWidget(self._empty)

        # pagination footer
        self._footer = QWidget()
        fr = QHBoxLayout(self._footer)
        fr.setContentsMargins(0, theme.SP4, 0, 0)
        self._range = QLabel("")
        self._range.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};")
        fr.addWidget(self._range)
        fr.addStretch(1)
        self._pager = QHBoxLayout()
        self._pager.setSpacing(theme.SP2)
        fr.addLayout(self._pager)
        fl.addWidget(self._footer)

        if self._frame is not None:
            outer.addWidget(self._frame)
        self.set_rows(rows)

    # ── public ────────────────────────────────────────────────
    def set_rows(self, rows, pinned=None, total=None, keep_page=False):
        """`pinned` rows are always shown above the paged ones and are not
        part of the pagination (the order builder keeps picked items on top).

        `total` включает серверный режим: `rows` — уже готовая страница, а
        сколько строк всего, знает только сервер. `keep_page` нужен там же:
        после перехода на страницу список подменяется, но номер сбрасывать
        нельзя, иначе выйдет петля.
        """
        self._rows = rows
        self._pinned = list(pinned or [])
        self._server_total = total
        if not keep_page:
            self._page = 1
        self._render()

    def page_size(self):
        """Сколько строк влезает — серверу это нужно как `limit`."""
        return self._page_size

    def set_empty_text(self, text):
        """Что показать вместо строк. Список, который не загрузился, объясняет
        это здесь — а не модальным окном поверх экрана."""
        self._empty.setText(text)

    # ── rows that fit the window ──────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        if self._column_fitter is None:
            # first fit happens here: during __init__ the table has no real
            # width yet, so the widths computed then are meaningless
            enable_auto_columns(self._table, self._floors, self._elastic)
            self._column_fitter = self._table._column_fitter
        if self._auto_rows and not self._watching:
            self._watching = True
            # the scroll viewport is what actually constrains us; the window
            # is watched too so a resize refits even before the viewport does
            area = self._scroll_area()
            if area is not None:
                area.viewport().installEventFilter(self)
            win = self.window()
            if win is not None:
                win.installEventFilter(self)
            # at show time the page has not been laid out yet and the viewport
            # still reports a placeholder height — refit once that has settled
            self._fit_timer.start()

    def eventFilter(self, obj, event):
        if self._auto_rows and event.type() == QEvent.Type.Resize:
            self._fit_timer.start()
        return super().eventFilter(obj, event)

    def _scroll_area(self):
        """The QScrollArea this table is rendered inside, if any."""
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QScrollArea):
                return w
            w = w.parentWidget()
        return None

    def _apply_auto_rows(self):
        """Size the page so the whole thing fits without a scrollbar.

        The height left for rows is *measured*, not derived from constants:
        what sits above the table (tabs, filters that wrap onto a second
        line, stat cards) changes height on its own, so only the laid-out
        page knows how much room is actually left.
        """
        if self._auto_busy:
            return
        area = self._scroll_area()
        page = area.widget() if area is not None else None
        if page is None or page.layout() is None or not self._table.isVisible():
            return
        row_h = self._table.verticalHeader().defaultSectionSize()
        if row_h <= 0:
            return

        size_key = (area.viewport().width(), area.viewport().height())
        if self._fitted_for == size_key:
            return                          # already solved for this viewport

        _relayout(self, page)
        if self.height() <= 0:
            return                          # not laid out yet

        # The table is the last thing on the page, so its top edge does not
        # move when the row count changes — one measurement is enough, no
        # need to iterate against Qt's asynchronous layout.
        top = self.mapTo(page, QPoint(0, 0)).y()
        shown = self._page_size + len(self._pinned)
        chrome = self.height() - shown * row_h              # header + footer + padding
        # anything drawn below us (e.g. the padding of a panel we sit in)
        below = max(0, _content_bottom(page) - self.mapTo(page, QPoint(0, self.height())).y())
        room = (area.viewport().height() - top
                - page.layout().contentsMargins().bottom() - chrome - below)
        fit = max(MIN_ROWS, room // row_h)

        self._fitted_for = size_key
        was_page_size = self._page_size
        self._auto_busy = True
        try:
            if fit != self._page_size:
                self._page_size = fit
                self._render()
            # verify against the real layout: a horizontal scrollbar inside the
            # table, or a filter row that re-wrapped, can still push us over —
            # shave rows until it fits (shrink-only, so this cannot oscillate)
            for _ in range(4):
                _relayout(self, page)
                overflow = (_content_bottom(page)
                            + page.layout().contentsMargins().bottom()
                            - area.viewport().height())
                if overflow <= 0 or self._page_size <= MIN_ROWS:
                    break
                self._page_size = max(MIN_ROWS,
                                      self._page_size - max(1, (overflow + row_h - 1) // row_h))
                self._render()
        finally:
            self._auto_busy = False

        # В серверном режиме размер страницы — это `limit` запроса. Окно
        # изменили, влезает больше строк — надо перезапросить, иначе таблица
        # покажет старую короткую страницу и решит, что дальше ничего нет.
        if self._server_total is not None and self._page_size != was_page_size:
            if self._on_page_change is not None:
                self._on_page_change(self._page)

        # Geometry can settle a pass later than we measure it (a table with
        # taller rows reports its old height here), so the fit above may still
        # be one row too many. Re-check once the event loop has caught up —
        # shrink-only and bounded, so it cannot ping-pong.
        self._verify_left = 2
        QTimer.singleShot(_VERIFY_DELAY_MS, self._verify_fit)

    def _verify_fit(self):
        if self._verify_left <= 0 or self._auto_busy or not self._auto_rows:
            return
        area = self._scroll_area()
        page = area.widget() if area is not None else None
        if page is None or page.layout() is None or not self._table.isVisible():
            return
        row_h = self._table.verticalHeader().defaultSectionSize()
        if row_h <= 0:
            return
        _relayout(self, page)
        overflow = (_content_bottom(page)
                    + page.layout().contentsMargins().bottom()
                    - area.viewport().height())
        if overflow <= 0 or self._page_size <= MIN_ROWS:
            return
        self._verify_left -= 1
        self._auto_busy = True
        try:
            self._page_size = max(MIN_ROWS,
                                  self._page_size - max(1, (overflow + row_h - 1) // row_h))
            self._render()
        finally:
            self._auto_busy = False
        QTimer.singleShot(_VERIFY_DELAY_MS, self._verify_fit)

    # ── internals ─────────────────────────────────────────────
    def _row_count(self):
        return self._server_total if self._server_total is not None else len(self._rows)

    def _total_pages(self):
        return max(1, (self._row_count() + self._page_size - 1) // self._page_size)

    def _row_clicked(self, r, _c):
        page_rows = self._current_page_rows()
        if 0 <= r < len(page_rows) and self._on_click:
            self._on_click(page_rows[r][1])

    def _current_page_rows(self):
        if self._server_total is not None:
            return self._pinned + self._rows     # страница уже пришла готовой
        start = (self._page - 1) * self._page_size
        return self._pinned + self._rows[start:start + self._page_size]

    def _make_item(self, cell):
        """Cell forms: plain text, ("m", text) muted, ("h", text[, color])
        heading font, ("c", text, color) coloured text."""
        if isinstance(cell, tuple):
            kind = cell[0]
            if kind == "m":
                item = QTableWidgetItem(str(cell[1]))
                item.setForeground(QColor(theme.NEUTRAL[600]))
                return item
            if kind in ("h", "c"):
                item = QTableWidgetItem(str(cell[1]))
                if kind == "h":
                    f = QFont(theme.HEADING_FAMILY)
                    f.setPixelSize(14)   # match the 14px of surrounding cells
                    item.setFont(f)
                if len(cell) > 2 and cell[2]:
                    item.setForeground(QColor(cell[2]))
                return item
        return QTableWidgetItem(str(cell))

    def _measure_floors(self):
        """Column minimums wide enough for the *whole* data set.

        Measuring only the rows on screen would make a long value on one page
        widen the columns there and nowhere else — the table would grow a
        horizontal scrollbar that comes and goes as you page through it.
        """
        table = self._table
        body_fm = QFontMetrics(table.font())
        heading = QFont(theme.HEADING_FAMILY)
        heading.setPixelSize(14)
        head_fm = QFontMetrics(heading)
        hdr_fm = QFontMetrics(table.horizontalHeader().font())
        floors = []
        for c in range(len(self._headers)):
            need = hdr_fm.horizontalAdvance(str(self._headers[c])) + _CELL_PAD
            for row in list(self._pinned) + list(self._rows):
                cells = row[0]
                if c >= len(cells):
                    continue
                cell = cells[c]
                kind = cell[0] if isinstance(cell, tuple) else None
                if kind == "w":
                    continue                # a widget: the declared width rules
                text = str(cell[1]) if isinstance(cell, tuple) else str(cell)
                fm = head_fm if kind == "h" else body_fm
                width = fm.horizontalAdvance(text) + _CELL_PAD
                if kind == "tag":
                    width += 20             # the pill's own 10px side padding
                need = max(need, width)
            floors.append(max(need, self._declared[c] if c < len(self._declared) else 0))
        return floors

    def _render(self):
        self._floors[:] = self._measure_floors()
        total_pages = self._total_pages()
        self._page = min(self._page, total_pages)
        page_rows = self._current_page_rows()

        has_rows = bool(self._rows) or bool(self._pinned)
        self._table.setVisible(has_rows)
        self._empty.setVisible(not has_rows)

        # drop the previous rows outright: setRowCount() alone keeps cell
        # widgets that no row asks for any more, and they then cover the plain
        # items rendered underneath them
        self._table.setRowCount(0)
        self._table.setRowCount(len(page_rows))
        row_colors = {}
        for r, row in enumerate(page_rows):
            cells = row[0]
            if len(row) > 2 and row[2]:
                row_colors[r] = row[2]      # optional per-row background
            for c, cell in enumerate(cells):
                if isinstance(cell, tuple) and cell[0] == "tag":
                    _, text, color, bg = cell
                    wrap = transparent_cell()
                    wl = QHBoxLayout(wrap)
                    wl.setContentsMargins(4, 0, 4, 0)
                    wl.addWidget(Tag(text, color, bg), 0, Qt.AlignmentFlag.AlignVCenter)
                    wl.addStretch(1)
                    self._table.setCellWidget(r, c, wrap)
                elif isinstance(cell, tuple) and cell[0] == "w":
                    widget = cell[1]()
                    if not widget.objectName():
                        # the container must not hide the row behind it
                        widget.setObjectName("cellwrap")
                        widget.setStyleSheet(
                            "QWidget#cellwrap { background: transparent; }")
                    self._table.setCellWidget(r, c, widget)
                else:
                    self._table.setItem(r, c, self._make_item(cell))

        delegate = getattr(self._table, "_row_hover", None)
        if delegate is not None:
            delegate.set_row_colors(row_colors)

        fit_table_height(self._table)
        autosize_columns(self._table, self._floors, self._elastic)

        # pagination footer
        show_pager = total_pages > 1
        self._footer.setVisible(show_pager and has_rows)
        if has_rows:
            count = self._row_count()
            start = (self._page - 1) * self._page_size + 1
            end = min(self._page * self._page_size, count)
            self._range.setText(f"{start}–{end} из {count}")
        self._rebuild_pager(total_pages)

    def _rebuild_pager(self, total_pages):
        while self._pager.count():
            item = self._pager.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if total_pages <= 1:
            return
        prev = button("‹", "icon" if self._page > 1 else "icon")
        prev.setProperty("variant", "icon")
        prev.setEnabled(self._page > 1)
        prev.clicked.connect(lambda: self._go(self._page - 1))
        self._pager.addWidget(prev)
        for n in range(1, total_pages + 1):
            b = button(str(n))
            b.setProperty("variant", "page-active" if n == self._page else "page")
            b.setFixedWidth(32)
            b.clicked.connect(lambda _=False, x=n: self._go(x))
            self._pager.addWidget(b)
        nxt = button("›", "icon")
        nxt.setEnabled(self._page < total_pages)
        nxt.clicked.connect(lambda: self._go(self._page + 1))
        self._pager.addWidget(nxt)

    def _go(self, n):
        self._page = max(1, min(n, self._total_pages()))
        # Сначала сообщаем: в серверном режиме обработчик успеет подгрузить
        # страницу и вызвать set_rows, и отрисуется уже она.
        if self._on_page_change is not None:
            self._on_page_change(self._page)
        self._render()

    def current_page(self):
        return self._page

    def set_page(self, n):
        """Jump to a page without reporting it back as a user action."""
        page = max(1, min(int(n), self._total_pages()))
        if page != self._page:
            self._page = page
            self._render()
