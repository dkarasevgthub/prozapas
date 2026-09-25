"""Shared page-building helpers: header rows, search fields, stat cards."""
from PyQt6.QtCore import QDate, QEvent, QSize, Qt
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QDateEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..widgets.blueprint import BlueprintFrame
from ..widgets.common import h1


def header_row(title, subtitle, action=None):
    """Title + subtitle on the left, an optional action button on the right."""
    row = QHBoxLayout()
    left = QVBoxLayout()
    left.setSpacing(4)
    left.addWidget(h1(title))
    sub = QLabel(subtitle)
    sub.setObjectName("muted")
    left.addWidget(sub)
    row.addLayout(left)
    row.addStretch(1)
    if action is not None:
        row.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)
    return row


def search_field(placeholder, on_change, max_width=360):
    """The mockup's `.field {flex:1;max-width:360px}` — it takes the space it
    is given up to 360px instead of shrinking to the placeholder."""
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setMaximumWidth(max_width)
    field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    field.textChanged.connect(on_change)
    return field


_EMPTY_DATE = QDate(1900, 1, 1)
_input_height = None


def _plain_input_height():
    """Height of an ordinary .input, so date fields line up with the rest."""
    global _input_height
    if _input_height is None:
        _input_height = QLineEdit().sizeHint().height()
    return _input_height


class EmptyDateEdit(QDateEdit):
    """A date field that starts blank — QDateEdit has no null state, so the
    minimum date doubles as 'not set' and is shown as an empty string.

    Opening the calendar on a blank field lands on the current month instead
    of the sentinel year, and the field is trimmed to the height of a plain
    input (the drop-down button otherwise makes it a few pixels taller).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd.MM.yyyy")
        # without this a click in the text area is hit-tested as the spin box's
        # "step down" button and silently shifts the date back by one day
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setMinimumDate(_EMPTY_DATE)
        self.setSpecialValueText(" ")
        self.setDate(_EMPTY_DATE)
        self.setFixedHeight(_plain_input_height())
        calendar = self.calendarWidget()
        if calendar is not None:
            calendar.installEventFilter(self)

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Show
                and obj is self.calendarWidget()
                and self.date() == _EMPTY_DATE):
            today = QDate.currentDate()
            obj.setCurrentPage(today.year(), today.month())
        return super().eventFilter(obj, event)

    # ── typing into a blank field starts from today, not from the sentinel ──
    def _start_from_today(self):
        if self.date() == _EMPTY_DATE:
            self.setDate(QDate.currentDate())

    def mousePressEvent(self, event):
        # clicking the calendar button just opens the popup; clicking the text
        # means the user wants to type, so give them a sensible starting date
        if event.position().x() < self.width() - theme.DATE_BUTTON_W:
            self._start_from_today()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() not in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
                               Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._start_from_today()
        super().keyPressEvent(event)


def empty_date_edit():
    return EmptyDateEdit()


def date_value(edit):
    """Return a `datetime.date` or None when the field is blank."""
    if edit.date() == _EMPTY_DATE:
        return None
    return edit.date().toPyDate()


def clear_date(edit):
    edit.blockSignals(True)
    edit.setDate(_EMPTY_DATE)
    edit.blockSignals(False)


def number_field(on_change=None, width=90):
    """`input type="number"` from the mockup — blank means "no bound"."""
    field = QLineEdit()
    validator = QDoubleValidator(0.0, 1e9, 2)
    validator.setNotation(QDoubleValidator.Notation.StandardNotation)
    field.setValidator(validator)
    field.setMaximumWidth(width)
    if on_change is not None:
        field.textChanged.connect(on_change)
    return field


def number_value(edit):
    """The field's value as a float, or None while it is blank."""
    text = edit.text().strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def filter_action(widget, top_offset=21):
    """Wrap a bare button so it lines up with the inputs of labelled fields
    (their 12px label plus the 5px gap above the control)."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, top_offset, 0, 0)
    v.setSpacing(0)
    v.addWidget(widget)
    return box


def labeled_field(label_text, widget, min_width=170):
    box = QWidget()
    box.setMaximumWidth(max(min_width, 260) if min_width else 16777215)
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(5)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[700]};")
    v.addWidget(lbl)
    v.addWidget(widget)
    box.setMinimumWidth(min_width)
    return box


def stat_card(label, value, hint):
    card = BlueprintFrame(padding=theme.SP4)
    cl = card.content_layout()
    cl.setSpacing(0)
    k = QLabel(label); k.setObjectName("kicker")
    num = QLabel(value); num.setObjectName("statnum")
    h = QLabel(hint); h.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[600]};")
    cl.addWidget(k)
    cl.addSpacing(theme.SP2)
    cl.addWidget(num)
    cl.addSpacing(theme.SP1)
    cl.addWidget(h)
    return card


def stat_grid(stats, columns=4):
    grid = QGridLayout()
    grid.setSpacing(theme.SP6)
    for i, (label, value, hint) in enumerate(stats):
        grid.addWidget(stat_card(label, value, hint), 0, i)
        grid.setColumnStretch(i, 1)     # repeat(N, 1fr)
    return grid


class SplitRow(QWidget):
    """Левый блок и правый блок в одной строке.

    Пока ширины хватает, правый блок прижат к правому краю — как
    `justify-content: space-between` в макете. Когда не хватает, он переносится
    под левый, вместо того чтобы уезжать за край окна.
    """

    def __init__(self, left, right, spacing=None, v_spacing=None, parent=None):
        super().__init__(parent)
        self._left = left
        self._right = right
        left.setParent(self)
        right.setParent(self)
        self._h = theme.SP4 if spacing is None else spacing
        self._v = theme.SP3 if v_spacing is None else v_spacing
        self._last_width = -1
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    # ── геометрия ─────────────────────────────────────────────
    def _wrapped(self, width):
        need = (self._left.sizeHint().width() + self._h
                + self._right.sizeHint().width())
        return need > max(1, width)

    def sizeHint(self):
        left, right = self._left.sizeHint(), self._right.sizeHint()
        if self._wrapped(self.width()):
            return QSize(max(left.width(), right.width()),
                         left.height() + self._v + right.height())
        return QSize(left.width() + self._h + right.width(),
                     max(left.height(), right.height()))

    def minimumSizeHint(self):
        return QSize(0, self.sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()
        left, right = self._left.sizeHint(), self._right.sizeHint()
        if self._wrapped(width):
            self._left.setGeometry(0, 0, min(left.width(), width), left.height())
            self._right.setGeometry(0, left.height() + self._v,
                                    min(right.width(), width), right.height())
        else:
            self._left.setGeometry(0, 0, left.width(), left.height())
            self._right.setGeometry(width - right.width(), 0,
                                    right.width(), right.height())
        if width != self._last_width:
            self._last_width = width
            self.updateGeometry()       # перенос меняет нашу высоту
