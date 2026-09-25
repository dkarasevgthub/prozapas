"""Modal dialogs styled after the design's .dialog component."""
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from .combo import SearchableComboBox
from .common import button


def _style_dialog(dlg):
    # labels must be told to stay transparent: the app-wide `QWidget` rule
    # gives every widget an opaque background, which paints a band behind
    # each caption instead of letting the dialog show through
    dlg.setStyleSheet(
        f"QDialog{{background:{theme.SURFACE};border:1px solid {theme.DIVIDER};}}"
        f"QDialog QLabel{{background:transparent;}}"
    )


def confirm_dialog(parent, title, body, confirm_label="Подтвердить", cancel_label="Отмена"):
    dlg = QDialog(parent)
    dlg.setModal(True)
    dlg.setWindowTitle(title)
    _style_dialog(dlg)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(theme.SP4, theme.SP4, theme.SP4, theme.SP4)
    lay.setSpacing(theme.SP3)

    t = QLabel(title)
    t.setStyleSheet(f"background:transparent;font-family:{theme.font_heading()};"
                    f"font-weight:600;font-size:20px;")
    lay.addWidget(t)
    b = QLabel(body)
    b.setWordWrap(True)
    b.setStyleSheet("background:transparent;font-size:14px;")
    lay.addWidget(b)

    actions = QHBoxLayout()
    actions.addStretch(1)
    cancel = button(cancel_label, "secondary")
    cancel.clicked.connect(dlg.reject)
    ok = button(confirm_label, "primary")
    ok.clicked.connect(dlg.accept)
    actions.addWidget(cancel)
    actions.addWidget(ok)
    lay.addSpacing(theme.SP2)
    lay.addLayout(actions)

    dlg.setMinimumWidth(440)
    return dlg.exec() == QDialog.DialogCode.Accepted


def form_dialog(parent, title, fields, on_save, submit_label="Сохранить", columns=1, width=440):
    """fields: list of (key, label, kind, default[, options]).

    kind is 'text', 'select' or 'search-select' (a select whose popup carries a
    search box). on_save(values)->error_str|None. Returns True on save.
    """
    dlg = QDialog(parent)
    dlg.setModal(True)
    dlg.setWindowTitle(title)
    _style_dialog(dlg)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(theme.SP4, theme.SP4, theme.SP4, theme.SP4)
    lay.setSpacing(theme.SP3)

    t = QLabel(title)
    t.setStyleSheet(f"background:transparent;font-family:{theme.font_heading()};"
                    f"font-weight:600;font-size:20px;")
    lay.addWidget(t)

    grid = QGridLayout()
    grid.setHorizontalSpacing(theme.SP3)
    grid.setVerticalSpacing(theme.SP3)
    widgets = {}
    for i, field in enumerate(fields):
        key, label, kind, default = field[0], field[1], field[2], field[3]
        cell = QWidget()
        # the wrapper is only there to stack label over field: left opaque it
        # paints a band of page background behind the caption
        cell.setObjectName("formCell")
        cell.setStyleSheet("QWidget#formCell{background:transparent;}")
        v = QVBoxLayout(cell)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"background:transparent;font-size:12px;color:{theme.NEUTRAL[700]};")
        v.addWidget(lbl)
        if kind in ("select", "search-select"):
            w = SearchableComboBox() if kind == "search-select" else QComboBox()
            options = field[4]
            for value, text in options:
                w.addItem(text, value)
            idx = max(0, [o[0] for o in options].index(default) if default in [o[0] for o in options] else 0)
            w.setCurrentIndex(idx)
        else:
            w = QLineEdit(str(default))
        v.addWidget(w)
        widgets[key] = (w, kind)
        r, c = divmod(i, columns)
        grid.addWidget(cell, r, c)
    lay.addLayout(grid)

    error = QLabel("")
    error.setStyleSheet(f"background:transparent;font-size:12px;color:{theme.DANGER};")
    error.setVisible(False)
    lay.addWidget(error)

    actions = QHBoxLayout()
    actions.addStretch(1)
    cancel = button("Отмена", "secondary")
    cancel.clicked.connect(dlg.reject)
    save = button(submit_label, "primary")
    actions.addWidget(cancel)
    actions.addWidget(save)
    lay.addSpacing(theme.SP2)
    lay.addLayout(actions)

    def collect():
        out = {}
        for key, (w, kind) in widgets.items():
            out[key] = w.text() if kind == "text" else w.currentData()
        return out

    def try_save():
        err = on_save(collect())
        if err:
            error.setText(err)
            error.setVisible(True)
        else:
            dlg.accept()

    save.clicked.connect(try_save)
    dlg.setMinimumWidth(width)
    return dlg.exec() == QDialog.DialogCode.Accepted


def weight_dialog(parent, hint=None, title="Введите вес", value=None):
    """Manual weight entry — the scales are unavailable. Returns kg or None.

    `value` подставляется в поле: когда весы показывают, но показание не
    устоялось, оператору остаётся подтвердить цифру, а не набирать её.
    """
    result = {}

    def on_save(values):
        text = values["weight"].strip().replace(",", ".")
        try:
            weight = float(text)
        except ValueError:
            return "Введите вес в килограммах, например 12.5"
        if weight <= 0:
            return "Вес должен быть больше нуля."
        result["weight"] = weight
        return None

    label = "Вес, кг" + (f" ({hint})" if hint else "")
    preset = "" if value is None else f"{value:.2f}"
    if form_dialog(parent, title, [("weight", label, "text", preset)], on_save,
                   submit_label="Готово"):
        return result.get("weight")
    return None
