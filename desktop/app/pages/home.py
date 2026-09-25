"""Home dashboard."""
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import api, fmt, reference, theme
from ..api.errors import ApiError
from ..session import session
from ..widgets.blueprint import BlueprintFrame
from ..widgets.common import breadcrumb, h1, h4, svg_pixmap
from .base import Page

_MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]

EVENT_META = {
    "created":    (lambda n: f"Создан заказ №{n}", "Заказ", "tag-outline", "orders"),
    "processing": (lambda n: f"Заказ №{n} взят в обработку", "Заказ", "tag-outline", "orders"),
    "shipped":    (lambda n: f"Заказ №{n} отгружен", "Отгрузка", "tag-accent", "shipping"),
    "received":   (lambda n: f"Заказ №{n} принят", "Приёмка", "tag-neutral", "receiving"),
    "declined":   (lambda n: f"Заказ №{n} отклонён", "Заказ", "tag-outline", "orders"),
    "cancelled":  (lambda n: f"Заказ №{n} отменён", "Заказ", "tag-outline", "orders"),
}


class HomePage(Page):
    def build(self):
        user = session.user or {}
        role = user.get("role", "admin")
        parts = (user.get("full_name") or "").split()
        first_name = parts[1] if len(parts) > 1 else (parts[0] if parts else "коллега")
        now = datetime.now()
        today = f"{now.day} {_MONTHS[now.month]} {now.year}"
        owner = (session.warehouse or {}).get("owner", "ООО «Сталкер Групп»")

        # ── breadcrumb + greeting ──
        self.add_block(breadcrumb("ProЗапас / Главная"))
        greet = QVBoxLayout()
        greet.setSpacing(4)
        greet.addWidget(h1(f"Здравствуйте, {first_name}"))
        sub = QLabel(f"{today} · {reference.role_label(role)} · {owner}")
        sub.setObjectName("muted")
        greet.addWidget(sub)
        self.add_block(greet)

        try:
            board = api.client.dashboard()
        except ApiError as exc:
            self._offline(exc)
            return

        # ── stat cards ──
        stats = [
            ("Заказы в работе", board["in_work"],
             f'{board["outgoing_in_work"]} исходящих · {board["incoming_in_work"]} входящих',
             "orders"),
            ("К отгрузке", board["to_ship"], "ждут сборки", "shipping"),
            ("К приёмке", board["to_receive"], "наши заказы в пути", "receiving"),
        ]
        grid = QGridLayout()
        grid.setSpacing(theme.SP6)
        for i, (lbl, val, hint, dest) in enumerate(stats):
            grid.addWidget(self._stat_card(lbl, str(val), hint, dest), 0, i)
            grid.setColumnStretch(i, 1)     # repeat(3, 1fr)
        self.add_block(grid)

        # ── two panels ──
        # grid-template-columns:1.4fr 1fr; align-items:start — each panel is
        # only as tall as its own rows, so AlignTop must be per-widget
        panels = QHBoxLayout()
        panels.setSpacing(theme.SP6)
        panels.addWidget(self._tasks_panel(role), 14, Qt.AlignmentFlag.AlignTop)
        panels.addWidget(self._events_panel(board.get("events") or []), 10,
                         Qt.AlignmentFlag.AlignTop)
        self.add_block(panels)
        self.col.addStretch(1)

    def _offline(self, exc):
        frame = BlueprintFrame(padding=theme.SP8)
        fl = frame.content_layout()
        fl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel(exc.title)
        t.setStyleSheet(f"font-family:{theme.font_heading()};font-size:20px;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d = QLabel("Сводка появится, как только сервер ответит. "
                   "Разделы в меню слева работают независимо.")
        d.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};")
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.setWordWrap(True)
        fl.addWidget(t); fl.addWidget(d)
        self.add_block(frame)
        self.col.addStretch(1)

    # ── components ─────────────────────────────────────────────
    def _stat_card(self, lbl, val, hint, dest):
        card = BlueprintFrame(padding=theme.SP4, clickable=True, hover_accent=True)
        card.clicked.connect(lambda: self.nav.go(dest))
        cl = card.content_layout()
        # label ─SP2─ num ─SP1─ hint, per the mockup's margin-bottom/-top
        cl.setSpacing(0)
        k = QLabel(lbl); k.setObjectName("kicker")
        num = QLabel(val); num.setObjectName("statnum")
        h = QLabel(hint); h.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[600]};")
        cl.addWidget(k)
        cl.addSpacing(theme.SP2)
        cl.addWidget(num)
        cl.addSpacing(theme.SP1)
        cl.addWidget(h)
        return card

    def _panel(self, title, right_widget=None):
        panel = BlueprintFrame(padding=theme.SP6)
        cl = panel.content_layout()
        cl.setSpacing(theme.SP4)      # head has margin-bottom: var(--space-4)
        head = QHBoxLayout()
        t = h4(title)
        head.addWidget(t, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        if right_widget is not None:
            head.addWidget(right_widget, 0, Qt.AlignmentFlag.AlignBottom)
        cl.addLayout(head)
        rows = QVBoxLayout()
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(0)
        cl.addLayout(rows)
        return panel, cl, rows

    def _tag(self, text, cls):
        colors = {
            "tag-outline": ("transparent", theme.ACCENT, theme.ACCENT),
            "tag-accent": (theme.ACCENT_RAMP[100], theme.ACCENT_RAMP[800], None),
            "tag-neutral": (theme.NEUTRAL[100], theme.NEUTRAL[800], None),
        }[cls]
        bg, fg, border = colors
        style = f"background:{bg};color:{fg};font-size:11px;padding:3px 10px;"
        if border:
            style += f"border:1px solid {border};"
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFixedWidth(88)
        lbl.setStyleSheet(style)
        return lbl

    def _row(self, tag_widget, text, sub, on_click, chevron=True):
        row = QWidget()
        row.setObjectName("hrow")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setStyleSheet(
            f"QWidget#hrow{{border-bottom:1px solid {theme.DIVIDER};background:transparent;}}"
            f"QWidget#hrow:hover{{background:{theme.ACCENT_RAMP[100]};}}"
        )
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(theme.SP2, theme.SP3, theme.SP2, theme.SP3)
        rl.setSpacing(theme.SP3)
        rl.addWidget(tag_widget)
        txt = QLabel(text)
        txt.setStyleSheet("font-size:14px;background:transparent;")
        rl.addWidget(txt, 1)
        s = QLabel(sub)
        s.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[600]};background:transparent;")
        rl.addWidget(s)
        if chevron:
            chev = QLabel()
            chev.setPixmap(svg_pixmap("chevron", theme.NEUTRAL[500], 16))
            chev.setStyleSheet("background:transparent;")
            rl.addWidget(chev)
        row.mouseReleaseEvent = lambda e: on_click()
        return row

    def _tasks_panel(self, role):
        """Очереди — это те же списки заказов, только отфильтрованные.

        Отдельного эндпоинта под задачи нет и не нужно: два запроса дают все три
        очереди, а фильтр по статусу и складу сервер и так умеет.
        """
        try:
            incoming = api.client.orders("incoming",
                                         status=["created", "processing"],
                                         limit=8)["items"]
            outgoing = api.client.orders("outgoing",
                                         status=["processing", "shipped"],
                                         limit=8)["items"]
        except ApiError as exc:
            panel, _cl, rows = self._panel("Текущие задачи")
            fail = QLabel(exc.title)
            fail.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};"
                               f"padding:{theme.SP3}px 0;")
            rows.addWidget(fail)
            return panel

        def sub(o):
            return f'{o["positions_count"]} поз. · {o["counterparty"]["name"]}'

        to_process = [o for o in incoming if o["status"] == "created"]
        ship_queue = incoming
        receive_queue = [o for o in outgoing if o["status"] == "shipped"]

        manage = [("Заказ", "tag-outline", f'Обработать заказ №{o["number"]}',
                   sub(o), "order", {"id": o["id"]}) for o in to_process]
        ship = [("Отгрузка", "tag-accent", f'Собрать и отгрузить №{o["number"]}',
                 sub(o), "shipping", {}) for o in ship_queue]
        receive = [("Приёмка", "tag-neutral", f'Принять заказ №{o["number"]}',
                    sub(o), "receiving", {}) for o in receive_queue]

        if role == "manager":
            tasks = manage
        elif role == "stockman":
            tasks = ship + receive
        else:
            tasks = []
            seen = set()
            for task in manage + ship + receive:
                key = task[0] + str(task[5].get("id", task[2]))
                if key not in seen:
                    seen.add(key)
                    tasks.append(task)
        tasks = tasks[:8]

        count = QLabel(str(len(tasks)))
        count.setStyleSheet(f"font-family:{theme.font_heading()};font-size:16px;"
                            f"color:{theme.ACCENT_RAMP[800]};")
        panel, _cl, rows = self._panel("Текущие задачи", count)

        if not tasks:
            empty = QLabel("Активных задач нет.")
            empty.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};"
                                f"padding:{theme.SP3}px 0;")
            rows.addWidget(empty)
        for tag, cls, txt, hint, dest, params in tasks:
            rows.addWidget(self._row(self._tag(tag, cls), txt, hint,
                                     lambda d=dest, p=params: self.nav.go(d, **p)))
        return panel

    def _events_panel(self, events):
        link = QLabel(f'<a href="#" style="color:{theme.ACCENT_RAMP[700]};'
                      f'font-size:13px;text-decoration:none;">Все заказы</a>')
        link.setTextFormat(Qt.TextFormat.RichText)
        link.linkActivated.connect(lambda _: self.nav.go("orders"))
        panel, _cl, rows = self._panel("Последние действия", link)

        if not events:
            empty = QLabel("Пока нет зафиксированных действий.")
            empty.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};"
                                f"padding:{theme.SP3}px 0;")
            rows.addWidget(empty)
        for e in events[:6]:
            meta = EVENT_META.get(e["status"])
            if not meta:
                continue
            text_fn, tag, cls, dest = meta
            when = fmt.parse(e["occurred_at"])
            short = f"{when.day:02d}.{when.month:02d} {when.hour:02d}:{when.minute:02d}" if when else ""
            rows.addWidget(self._row(self._tag(tag, cls), text_fn(e["number"]),
                                     short, lambda d=dest: self.nav.go(d),
                                     chevron=False))
        return panel
