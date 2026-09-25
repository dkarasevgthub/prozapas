"""Пользователь — user detail with info grid, activity log and edit actions."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import api, fmt, reference, theme
from ..api.errors import ApiError
from ..widgets.blueprint import BlueprintFrame
from ..widgets.common import Tag, button, h1, h4
from ..widgets.dialog import confirm_dialog, form_dialog
from ..widgets.flow import FlowRow
from ..widgets.table import TableSection
from ._ui import SplitRow
from .base import Page

#: Сущность из audit_log → раздел приложения. Название раздела берётся из меню.
SECTION_OF_ENTITY = {
    "order": "orders", "shipment": "shipping", "receipt": "receiving",
    "catalog_item": "catalog", "stock_balance": "stock",
    "user_account": "users", "role_permission": "users",
}

#: Действие → как это читается. Ключ — (сущность, действие), потом просто действие.
ACTION_TEXT = {
    ("order", "created"): "Создал заказ №{id}",
    ("order", "accepted"): "Принял заказ №{id} к сборке",
    ("order", "declined"): "Отклонил заказ №{id}",
    ("order", "cancelled"): "Отменил заказ №{id}",
    ("order", "updated"): "Изменил заказ №{id}",
    ("shipment", "shipped"): "Отгрузил заказ №{id}",
    ("shipment", "boxed"): "Собрал коробку по заказу №{id}",
    ("receipt", "received"): "Завершил приёмку заказа №{id}",
    ("receipt", "boxed"): "Принял коробку по заказу №{id}",
    ("catalog_item", "created"): "Добавил позицию в справочник",
    ("catalog_item", "updated"): "Отредактировал позицию справочника",
    ("catalog_item", "archived"): "Архивировал позицию справочника",
    ("stock_balance", "receipt"): "Оприходовал товар вручную",
    ("stock_balance", "shipment"): "Отгрузил товар вручную",
    ("stock_balance", "writeoff"): "Списал товар",
    ("stock_balance", "recount"): "Скорректировал остаток",
    ("user_account", "created"): "Добавил пользователя",
    ("user_account", "updated"): "Изменил пользователя",
    ("user_account", "blocked"): "Заблокировал пользователя",
    ("user_account", "unblocked"): "Разблокировал пользователя",
    ("user_account", "deleted"): "Удалил пользователя",
    ("user_account", "password_changed"): "Сменил пароль",
    ("role_permission", "updated"): "Изменил права роли",
}
PLAIN_ACTION = {"login": "Вход в систему", "logout": "Выход из системы"}


class UserDetailPage(Page):
    def build(self):
        uid = self.params.get("id")
        try:
            user = api.client.user(uid)
        except ApiError:
            user = None

        crumb = QLabel(
            f'<a href="#" style="color:{theme.NEUTRAL[600]};text-decoration:none;">Пользователи</a>'
            + (f' / {user["full_name"]}' if user else "")
        )
        crumb.setObjectName("breadcrumb")
        crumb.setTextFormat(Qt.TextFormat.RichText)
        crumb.linkActivated.connect(lambda _: self.nav.go("users"))
        self.add_block(crumb)

        if not user:
            self._not_found()
            return
        self._user = user
        self._render(user)

    def _not_found(self):
        frame = BlueprintFrame(padding=theme.SP8)
        fl = frame.content_layout()
        fl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel("Пользователь не найден")
        t.setStyleSheet(f"font-family:{theme.font_heading()};font-size:25px;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        back = button("К списку пользователей", "secondary")
        back.clicked.connect(lambda: self.nav.go("users"))
        fl.addWidget(t)
        fl.addWidget(back, 0, Qt.AlignmentFlag.AlignCenter)
        self.add_block(frame)
        self.col.addStretch(1)

    def _render(self, user):
        rl, rc, rb = theme.role_meta(user["role"])
        sl, sc, sb = theme.user_status_meta(user["status"])

        # header: кнопки справа, при нехватке ширины — под именем
        who = QWidget()
        left = QHBoxLayout(who); left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(theme.SP4)
        parts = user["full_name"].split()
        avatar = QLabel("".join(p[0].upper() for p in parts[:2]) or "—")
        avatar.setFixedSize(72, 72)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            f"border:1px solid {theme.DIVIDER};font-family:{theme.font_heading()};"
            f"font-size:28px;color:{theme.ACCENT_RAMP[700]};"
        )
        left.addWidget(avatar)
        namebox = QVBoxLayout(); namebox.setSpacing(6)
        name = h1(user["full_name"])
        # ФИО целиком длиннее любого другого заголовка в приложении: на 44px
        # строка с кнопками не помещается в окно даже стандартной ширины
        name.setStyleSheet("font-size:36px;")
        namebox.addWidget(name)
        tags = QHBoxLayout(); tags.setSpacing(theme.SP2)
        tags.addWidget(Tag(rl, rc, rb)); tags.addWidget(Tag(sl, sc, sb)); tags.addStretch(1)
        namebox.addLayout(tags)
        left.addLayout(namebox)

        edit = button("Редактировать", "secondary")
        edit.clicked.connect(lambda: self._edit(user))
        passwd = button("Сменить пароль", "secondary")
        passwd.clicked.connect(lambda: self._change_password(user))
        blocked = user["status"] == "blocked"
        block = button("Разблокировать" if blocked else "Заблокировать", "secondary")
        block.clicked.connect(lambda: self._toggle_block(user))
        delete = button("Удалить", "ghost")
        delete.clicked.connect(lambda: self._delete(user))
        actions = QWidget()
        arow = QHBoxLayout(actions); arow.setContentsMargins(0, 0, 0, 0)
        arow.setSpacing(theme.SP3)
        for b in (edit, passwd, block, delete):
            arow.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        self.add_block(SplitRow(who, actions))

        # info grid
        info = BlueprintFrame(padding=theme.SP6)
        cells = [
            ("Должность", user.get("position") or "—"), ("Роль", rl),
            ("Склад", (user.get("warehouse") or {}).get("name", "—")), ("Статус", sl),
            ("Телефон", user.get("phone") or "—"), ("Email", user["email"]),
            ("Логин", user.get("login", "—")),
            ("Дата приёма", fmt.date(user.get("hire_date"))),
            ("Последний вход", fmt.datetime_(user.get("last_login_at"))),
        ]
        # те же реквизиты, что в карточках заказа и партии: строка с переносом
        grid = FlowRow(h_spacing=theme.SP8, v_spacing=theme.SP4)
        for k, val in cells:
            grid.add(self._info_cell(k, val))
        info.content_layout().addWidget(grid)
        self.add_block(info)

        # activity
        act = BlueprintFrame(padding=theme.SP4)
        act.content_layout().addWidget(h4("История действий"))
        self._table = TableSection(
            headers=["Дата и время", "Действие", "Раздел"],
            widths=[160, 0, 140], rows=[], page_size=8, auto_rows=True,
            framed=False,
            on_page_change=self._refresh_activity,   # страницу подгружает сервер
        )
        act.content_layout().addWidget(self._table)
        self.add_block(act)
        self.col.addStretch(1)
        self._refresh_activity()

    def _info_cell(self, k, v):
        box = QWidget()
        lay = QVBoxLayout(box); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        kk = QLabel(k); kk.setObjectName("kicker")
        vv = QLabel(str(v))
        vv.setStyleSheet(f"font-family:{theme.font_heading()};font-size:18px;")
        # без переноса: в строке с обтеканием ячейка получает ширину по своему
        # содержимому, и «Центральный склад» ломался на две строки
        lay.addWidget(kk); lay.addWidget(vv)
        return box

    # ── история ──
    def _action_text(self, entry):
        text = ACTION_TEXT.get((entry["entity"], entry["action"]))
        if text:
            return text.replace("{id}", str(entry["entity_id"]))
        return PLAIN_ACTION.get(entry["action"], entry["action"])

    def _refresh_activity(self, page: int = 1):
        size = self._table.page_size()
        try:
            payload = api.client.user_activity(
                self._user["id"], limit=size, offset=(page - 1) * size)
        except ApiError as exc:
            self._table.set_empty_text(exc.title)
            self._table.set_rows([], total=0, keep_page=True)
            return

        self._table.set_empty_text("Действий пока нет")
        rows = [([("m", fmt.datetime_(e["created_at"])),
                  self._action_text(e),
                  ("m", reference.section_label(
                      SECTION_OF_ENTITY.get(e["entity"], "")) or "—")], None)
                for e in payload["items"]]
        self._table.set_rows(rows, total=payload["total"], keep_page=True)

    # ── actions ──
    def _edit(self, user):
        fields = [
            ("full_name", "ФИО", "text", user["full_name"]),
            ("position", "Должность", "text", user.get("position") or ""),
            ("role", "Роль", "select", user["role"],
             [(r["code"], r["label"]) for r in reference.roles()]),
            ("warehouse_id", "Склад", "search-select",
             (user.get("warehouse") or {}).get("id"),
             [(w["id"], w["name"]) for w in reference.warehouses()]),
            ("status", "Статус", "select", user["status"],
             [("active", "Активен"), ("blocked", "Заблокирован")]),
            ("phone", "Телефон", "text", user.get("phone") or ""),
            ("email", "Email", "text", user["email"]),
        ]

        def on_save(v):
            if not v["full_name"].strip():
                return "Укажите как минимум ФИО."
            try:
                api.client.update_user(
                    user["id"], full_name=v["full_name"].strip(),
                    position=v["position"].strip(), role=v["role"],
                    warehouse_id=v["warehouse_id"], status=v["status"],
                    phone=v["phone"].strip(), email=v["email"].strip())
            except ApiError as exc:
                return exc.title
            return None

        if form_dialog(self, "Редактировать пользователя", fields, on_save,
                       columns=2, width=560):
            self.nav.go("user", id=user["id"])

    def _change_password(self, user):
        def on_save(v):
            if len(v["password"]) < 6:
                return "Пароль должен быть не короче 6 символов."
            if v["password"] != v["repeat"]:
                return "Пароли не совпадают."
            try:
                api.client.set_password(user["id"], v["password"])
            except ApiError as exc:
                return exc.title
            return None

        if form_dialog(self, f'Пароль — {fmt.short_name(user["full_name"])}',
                       [("password", "Новый пароль", "text", ""),
                        ("repeat", "Повторите пароль", "text", "")],
                       on_save, submit_label="Сохранить"):
            self.nav.go("user", id=user["id"])

    def _toggle_block(self, user):
        call = (api.client.unblock_user if user["status"] == "blocked"
                else api.client.block_user)
        try:
            call(user["id"])
        except ApiError as exc:
            self.show_error(exc)
            return
        self.nav.go("user", id=user["id"])

    def _delete(self, user):
        if not confirm_dialog(
                self, "Удалить пользователя?",
                f'Пользователь «{user["full_name"]}» потеряет доступ и исчезнет '
                f'из списков. Его подписи под заказами и движениями останутся.',
                confirm_label="Удалить"):
            return
        try:
            api.client.delete_user(user["id"])
        except ApiError as exc:
            self.show_error(exc)
            return
        self.nav.go("users")
