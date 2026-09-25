"""Пользователи — user list, add dialog, and the roles/permissions matrix."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .. import api, fmt, reference, theme
from ..api.errors import ApiError
from ..widgets.blueprint import BlueprintFrame
from ..widgets.combo import SearchableComboBox
from ..widgets.common import breadcrumb, button, icon_button
from ..widgets.dialog import form_dialog
from ..widgets.flow import FlowRow
from ..widgets.table import TableSection, transparent_cell
from ._ui import filter_action, header_row, labeled_field
from .base import Page

STATUS_OPTIONS = [("all", "Все статусы"), ("active", "Активен"),
                  ("blocked", "Заблокирован")]


def _initials(full_name):
    parts = (full_name or "").strip().split()
    return "".join(p[0].upper() for p in parts[:2]) or "—"


def _avatar(text, size=32, font=13):
    a = QLabel(text)
    a.setFixedSize(size, size)
    a.setAlignment(Qt.AlignmentFlag.AlignCenter)
    # background must stay transparent, otherwise it blanks out the row hover
    a.setStyleSheet(
        f"border:1px solid {theme.DIVIDER};background:transparent;"
        f"font-family:{theme.font_heading()};"
        f"font-size:{font}px;color:{theme.ACCENT_RAMP[700]};"
    )
    return a


def _employee_cell(full_name, email):
    w = transparent_cell()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setSpacing(theme.SP3)
    lay.addWidget(_avatar(_initials(full_name)))
    box = QVBoxLayout()
    box.setSpacing(0)
    n = QLabel(full_name)
    n.setStyleSheet("font-size:14px;background:transparent;")
    e = QLabel(email)
    e.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[600]};background:transparent;")
    box.addWidget(n)
    box.addWidget(e)
    lay.addLayout(box)
    lay.addStretch(1)
    return w


class UsersPage(Page):
    def build(self):
        self._tab = "users"
        self._search = ""
        self._role = "all"
        self._status = "all"
        self._warehouse = "all"

        self.add_block(breadcrumb("ProЗапас / Пользователи"))
        self._add_btn = button("+ Добавить пользователя", "primary")
        self._add_btn.clicked.connect(self._open_add)
        self.add_block(header_row("Пользователи",
                                  "Сотрудники, их роли и права доступа", self._add_btn))

        tabs = QHBoxLayout()
        tabs.setSpacing(theme.SP2)
        self._tab_users = button("Пользователи", "primary")
        self._tab_roles = button("Роли и права", "secondary")
        self._tab_users.clicked.connect(lambda: self._set_tab("users"))
        self._tab_roles.clicked.connect(lambda: self._set_tab("roles"))
        tabs.addWidget(self._tab_users)
        tabs.addWidget(self._tab_roles)
        tabs.addStretch(1)
        self.add_block(tabs)

        self._body = QVBoxLayout()
        self._body.setSpacing(theme.SP6)
        self.add_block(self._body)
        self.col.addStretch(1)
        self._render_body()

    def _role_options(self):
        return [("all", "Все роли")] + [(r["code"], r["label"])
                                        for r in reference.roles()]

    # ── tabs ──
    def _set_tab(self, tab):
        if tab == self._tab:
            return
        self._tab = tab
        self._tab_users.setProperty("variant", "primary" if tab == "users" else "secondary")
        self._tab_roles.setProperty("variant", "primary" if tab == "roles" else "secondary")
        for b in (self._tab_users, self._tab_roles):
            b.style().unpolish(b); b.style().polish(b)
        self._add_btn.setVisible(tab == "users")
        self._render_body()

    def _clear_body(self):
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _render_body(self):
        self._clear_body()
        if self._tab == "users":
            self._render_users()
        else:
            self._render_roles()

    # ── users tab ──
    def _render_users(self):
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("ФИО, телефон или email")
        self._search_input.textChanged.connect(self._on_search)
        self._role_input = self._combo(self._role_options())
        self._status_input = self._combo(STATUS_OPTIONS)
        self._warehouse_input = self._combo(
            [("all", "Все склады")] + [(w["id"], w["name"])
                                       for w in reference.warehouses()],
            searchable="Поиск склада")

        filters = FlowRow(h_spacing=theme.SP4, v_spacing=theme.SP3)
        filters.add(labeled_field("Поиск", self._search_input, 200))
        filters.add(labeled_field("Роль", self._role_input, 150))
        filters.add(labeled_field("Статус", self._status_input, 150))
        filters.add(labeled_field("Склад", self._warehouse_input, 160))
        reset = icon_button("reset", "Сбросить фильтры")
        reset.clicked.connect(self._reset)
        filters.add(filter_action(reset))
        self._body.addWidget(filters)

        self._table = TableSection(
            headers=["Сотрудник", "Роль", "Должность", "Склад", "Телефон",
                     "Статус", "Последний вход"],
            widths=[260, 120, 0, 150, 150, 116, 150],
            rows=[], on_row_click=lambda uid: self.nav.go("user", id=uid),
            page_size=12, auto_rows=True,
            on_page_change=self._refresh,       # страницу подгружает сервер
        )
        self._table._table.verticalHeader().setDefaultSectionSize(46)
        self._body.addWidget(self._table)
        self._refresh()

    def _combo(self, options, searchable=None):
        c = SearchableComboBox(searchable) if searchable else QComboBox()
        for v, t in options:
            c.addItem(t, v)
        c.currentIndexChanged.connect(self._on_filter)
        return c

    def _refresh(self, page: int = 1):
        size = self._table.page_size()
        try:
            payload = api.client.users(
                q=self._search.strip() or None,
                role=None if self._role == "all" else self._role,
                status=None if self._status == "all" else self._status,
                warehouse_id=None if self._warehouse == "all" else self._warehouse,
                limit=size, offset=(page - 1) * size)
        except ApiError as exc:
            self._table.set_empty_text(exc.title)
            self._table.set_rows([], total=0, keep_page=True)
            return

        self._table.set_empty_text("Ничего не найдено по этим условиям")
        rows = []
        for u in payload["items"]:
            rl, rc, rb = theme.role_meta(u["role"])
            sl, sc, sb = theme.user_status_meta(u["status"])
            fn, em = u["full_name"], u["email"]
            rows.append((
                [("w", lambda fn=fn, em=em: _employee_cell(fn, em)),
                 ("tag", rl, rc, rb),
                 ("m", u.get("position") or "—"),
                 ("m", (u.get("warehouse") or {}).get("name", "—")),
                 ("m", u.get("phone") or "—"),
                 ("tag", sl, sc, sb),
                 ("m", fmt.datetime_(u.get("last_login_at")))],
                u["id"],
            ))
        self._table.set_rows(rows, total=payload["total"], keep_page=True)

    def _on_search(self, text):
        self._search = text
        self._refresh()

    def _on_filter(self, _):
        self._role = self._role_input.currentData()
        self._status = self._status_input.currentData()
        self._warehouse = self._warehouse_input.currentData()
        self._refresh()

    def _reset(self):
        self._search = ""; self._role = "all"
        self._status = "all"; self._warehouse = "all"
        for c in (self._role_input, self._status_input, self._warehouse_input):
            c.blockSignals(True); c.setCurrentIndex(0); c.blockSignals(False)
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self._refresh()

    def _open_add(self):
        warehouses = reference.warehouses()
        fields = [
            ("full_name", "ФИО", "text", ""),
            ("position", "Должность", "text", ""),
            ("role", "Роль", "select", "manager",
             [(r["code"], r["label"]) for r in reference.roles()]),
            ("warehouse_id", "Склад", "search-select",
             warehouses[0]["id"] if warehouses else None,
             [(w["id"], w["name"]) for w in warehouses]),
            ("status", "Статус", "select", "active",
             [("active", "Активен"), ("blocked", "Заблокирован")]),
            ("phone", "Телефон", "text", ""),
            ("email", "Email", "text", ""),
            ("login", "Логин", "text", ""),
            ("password", "Пароль", "text", ""),
        ]

        def on_save(v):
            if not v["full_name"].strip():
                return "Укажите как минимум ФИО."
            login = v["login"].strip().lower() or v["email"].split("@")[0].strip().lower()
            if not login:
                return "Укажите логин или почту — по ним сотрудник входит в систему."
            if len(v["password"]) < 6:
                return "Пароль должен быть не короче 6 символов."
            try:
                # Занятость логина и почты проверяет сервер: только у него есть
                # весь список, включая тех, кого этот экран сейчас не показывает.
                api.client.create_user(
                    full_name=v["full_name"].strip(), login=login,
                    email=v["email"].strip(), password=v["password"],
                    role=v["role"], warehouse_id=v["warehouse_id"],
                    position=v["position"].strip(), phone=v["phone"].strip(),
                    status=v["status"])
            except ApiError as exc:
                return exc.title
            self._refresh()
            return None

        form_dialog(self, "Новый пользователь", fields, on_save,
                    submit_label="Добавить", columns=2, width=560)

    # ── roles tab ──
    def _render_roles(self):
        note = QLabel("Отметьте, что каждая роль может видеть и редактировать "
                      "по разделам. Изменения сохраняются автоматически.")
        note.setStyleSheet(f"font-size:13px;color:{theme.NEUTRAL[600]};")
        note.setWordWrap(True)
        self._body.addWidget(note)

        try:
            self._perms = api.client.permissions()
        except ApiError as exc:
            fail = QLabel(exc.title)
            fail.setStyleSheet(f"font-size:13px;color:{theme.DANGER};")
            self._body.addWidget(fail)
            return

        frame = BlueprintFrame(padding=theme.SP4)
        fl = frame.content_layout()
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.SP6)
        grid.setVerticalSpacing(theme.SP3)

        roles = reference.roles()
        for c, htext in enumerate(["Раздел"] + [r["label"] for r in roles]):
            h = QLabel(htext)
            h.setStyleSheet(f"font-size:11px;color:{theme.NEUTRAL[600]};"
                            f"text-transform:uppercase;font-weight:600;")
            grid.addWidget(h, 0, c)

        for r, code in enumerate(reference.sections(), start=1):
            sl = QLabel(reference.section_label(code))
            sl.setStyleSheet("font-weight:600;")
            grid.addWidget(sl, r, 0)
            for c, role in enumerate(roles, start=1):
                grid.addWidget(self._perm_cell(role["code"], code), r, c)
        fl.addLayout(grid)
        self._body.addWidget(frame)

    def _entry(self, role, section):
        for p in self._perms:
            if p["role"] == role and p["section"] == section:
                return p
        return {"role": role, "section": section,
                "can_view": False, "can_edit": False}

    def _perm_cell(self, role, section):
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.SP4)
        view = QCheckBox("Просмотр")
        edit = QCheckBox("Ред.")
        current = self._entry(role, section)
        view.setChecked(current["can_view"])
        edit.setChecked(current["can_edit"])

        def toggle(_=None):
            # Редактирование без просмотра бессмысленно — галочки тянут друг друга.
            if edit.isChecked() and not view.isChecked():
                view.setChecked(True)
                return                  # setChecked вызовет toggle заново
            if not view.isChecked() and edit.isChecked():
                edit.setChecked(False)
                return
            entry = self._entry(role, section)
            was = (entry["can_view"], entry["can_edit"])
            entry["can_view"] = view.isChecked()
            entry["can_edit"] = edit.isChecked()
            if entry not in self._perms:
                self._perms.append(entry)
            try:
                # Матрица уходит целиком: полтора десятка строк, а частичное
                # обновление потребовало бы отдельного ключа на каждую клетку.
                self._perms = api.client.save_permissions(self._perms)
            except ApiError as exc:
                entry["can_view"], entry["can_edit"] = was
                for box, value in ((view, was[0]), (edit, was[1])):
                    box.blockSignals(True); box.setChecked(value); box.blockSignals(False)
                self.show_error(exc)

        view.toggled.connect(toggle)
        edit.toggled.connect(toggle)
        lay.addWidget(view)
        lay.addWidget(edit)
        lay.addStretch(1)
        return cell
