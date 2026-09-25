"""Design tokens and global stylesheet for the ProЗапас desktop app.

Ported from the Claude Design "Industry" design system (styles.css):
a blueprint / wireframe industrial look — square corners, hairline borders,
Barlow / Barlow Condensed type, a muted blue accent on light grey.
"""

# ── Colour tokens ──────────────────────────────────────────────────────────
BG          = "#f2f2f3"
SURFACE     = "#e9e9ea"
TEXT        = "#1d1f20"
ACCENT      = "#5980a6"
ACCENT_2    = "#728fab"

# divider = text @ 16 % over the ground
DIVIDER     = "rgba(29,31,32,0.16)"
DIVIDER_SOLID = "#c7c8c9"          # opaque approximation, for painters

NEUTRAL = {
    100: "#f5f5f8", 200: "#e7e7ea", 300: "#d4d4d7", 400: "#b7b7ba",
    500: "#98989b", 600: "#7a7a7d", 700: "#5d5d60", 800: "#424244", 900: "#2b2b2d",
}
ACCENT_RAMP = {
    100: "#eef6ff", 200: "#d6ebff", 300: "#b5d9fd", 400: "#94bce3",
    500: "#749dc4", 600: "#597ea3", 700: "#416180", 800: "#2c455d", 900: "#1d2d3d",
}
ACCENT2_RAMP = {
    100: "#eef6ff", 200: "#d6ebff", 300: "#bdd8f2", 400: "#9ebbd8",
    500: "#7e9cb8", 600: "#627d98", 700: "#486077", 800: "#314457", 900: "#1f2d3a",
}
DANGER = "#b3261e"

# ── Spacing (px) ───────────────────────────────────────────────────────────
SP1, SP2, SP3, SP4, SP6, SP8 = 3, 7, 10, 14, 20, 27

# ── Fonts ──────────────────────────────────────────────────────────────────
# Resolved at startup by fonts.setup_fonts() to concrete installed families
# (Barlow when bundled, otherwise the closest system face). QSS reads these.
BODY_FAMILY    = "Segoe UI"
HEADING_FAMILY = "Bahnschrift SemiCondensed"


def _q(family: str) -> str:
    return f'"{family}"'


# kept as functions so the resolved family is picked up after setup_fonts()
def font_body() -> str:
    return _q(BODY_FAMILY)


def font_heading() -> str:
    return _q(HEADING_FAMILY)


def status_meta(status: str):
    """(label, text-colour, bg-colour) for an order status tag."""
    return {
        "created":    ("Создан",      NEUTRAL[800],     NEUTRAL[200]),
        "processing": ("В обработке", ACCENT2_RAMP[700], ACCENT2_RAMP[100]),
        "shipped":    ("Отгружен",    ACCENT_RAMP[800],  ACCENT_RAMP[100]),
        "received":   ("Завершен",    ACCENT_RAMP[700],  "transparent"),
        "declined":   ("Отклонён",    NEUTRAL[600],      "transparent"),
        "cancelled":  ("Отменён",     NEUTRAL[600],      "transparent"),
    }.get(status, (status, NEUTRAL[800], NEUTRAL[200]))


def role_meta(role: str):
    return {
        "manager":  ("Менеджер",       ACCENT2_RAMP[700], ACCENT2_RAMP[100]),
        "stockman": ("Кладовщик",      ACCENT_RAMP[800],  ACCENT_RAMP[100]),
        "admin":    ("Администратор",  NEUTRAL[800],      NEUTRAL[200]),
    }.get(role, (role, NEUTRAL[800], NEUTRAL[200]))


def user_status_meta(status: str):
    return {
        "active":  ("Активен",       ACCENT2_RAMP[700], ACCENT2_RAMP[100]),
        "blocked": ("Заблокирован",  DANGER,            NEUTRAL[100]),
    }.get(status, (status, NEUTRAL[800], NEUTRAL[200]))


# size of the sidebar's line icons
ICON_SIZE = 18
# icons sitting inside a 34px input are scaled down to stay in proportion
FIELD_ICON_SIZE = 14

# width of a date field's calendar button — EmptyDateEdit uses it to tell a
# click on the button apart from a click in the text area
DATE_BUTTON_W = 24


def _calendar_image() -> str:
    """`image:` rule for the date field's calendar button, if it can be baked."""
    from .resources import icon_png  # deferred: resources imports widgets
    path = icon_png("calendar", NEUTRAL[600], FIELD_ICON_SIZE)
    return f"image: url({path});" if path else ""


def _chevron_image() -> str:
    """`image:` rule for the arrow of a select, if it can be baked."""
    from .resources import icon_png  # deferred: resources imports widgets
    path = icon_png("chevron-down", NEUTRAL[600], FIELD_ICON_SIZE)
    return f"image: url({path});" if path else ""


def build_qss() -> str:
    """Global application stylesheet — the desktop equivalent of styles.css."""
    return f"""
    * {{
        font-family: {font_body()};
        font-size: 15px;
        color: {TEXT};
    }}
    QWidget {{ background: {BG}; }}
    QToolTip {{
        background: {NEUTRAL[900]}; color: {BG};
        border: none; padding: 4px 8px;
    }}

    /* headings use the condensed face — set per-widget via objectName */
    QLabel#h1 {{
        font-family: {font_heading()}; font-size: 44px; font-weight: 600;
        letter-spacing: -0.66px;   /* -0.015em from the base heading rule */
    }}
    QLabel#h4 {{ font-family: {font_heading()}; font-size: 22px; font-weight: 600; }}
    QLabel#breadcrumb {{ font-size: 18px; color: {NEUTRAL[600]}; }}
    QLabel#muted {{ font-size: 14px; color: {NEUTRAL[600]}; }}
    /* .kicker in the mockup is uppercase with 0.08em tracking */
    QLabel#kicker {{
        font-size: 11px; letter-spacing: 1px; color: {NEUTRAL[600]};
        text-transform: uppercase;
    }}
    /* .stat .num: no font-weight in the mockup (→ regular) and line-height:1 */
    QLabel#statnum {{
        font-family: {font_heading()}; font-size: 40px;
        color: {ACCENT_RAMP[800]};
        min-height: 40px; max-height: 40px;
    }}

    /* ── inputs ─────────────────────────────────────────────── */
    QLineEdit, QComboBox, QDateEdit, QSpinBox {{
        background: {SURFACE};
        border: 1px solid {DIVIDER};
        border-radius: 0;
        min-height: 24px;
        padding: 4px 10px;
        font-size: 14px;
        selection-background-color: {ACCENT};
        selection-color: {BG};
    }}
    QLineEdit:hover, QComboBox:hover, QDateEdit:hover, QSpinBox:hover {{
        border: 1px solid rgba(29,31,32,0.45);
    }}
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {{
        border: 1px solid {ACCENT};
    }}
    /* a select needs a visible arrow, like the native <select> in the mockup */
    QComboBox {{ padding-right: {DATE_BUTTON_W + 4}px; }}
    QComboBox::drop-down {{
        subcontrol-origin: padding; subcontrol-position: center right;
        width: {DATE_BUTTON_W}px; border: none; background: transparent;
    }}
    QComboBox::down-arrow {{ {_chevron_image()} }}
    /* a date field needs a visible affordance for opening the calendar */
    QDateEdit::drop-down {{
        subcontrol-origin: padding; subcontrol-position: center right;
        width: {DATE_BUTTON_W}px; border: none; background: transparent;
        {_calendar_image()}
    }}
    QDateEdit::drop-down:hover {{ background: rgba(29,31,32,0.07); }}
    QDateEdit::down-arrow {{ image: none; width: 0; height: 0; }}
    QComboBox QAbstractItemView {{
        background: {SURFACE};
        border: 1px solid {DIVIDER};
        selection-background-color: {ACCENT_RAMP[100]};
        selection-color: {TEXT};
        outline: none;
    }}

    /* popup of SearchableComboBox: a search field above a scrolling list */
    QFrame#comboPopup {{
        background: {SURFACE};
        border: 1px solid {DIVIDER};
    }}
    QListWidget#comboList {{
        background: transparent; border: none; outline: none;
        font-size: 14px;
    }}
    QListWidget#comboList::item {{ padding: 0 8px; border: none; }}
    QListWidget#comboList::item:hover {{ background: {ACCENT_RAMP[100]}; }}
    QListWidget#comboList::item:selected {{
        background: {ACCENT_RAMP[100]}; color: {ACCENT_RAMP[800]};
    }}
    QListWidget#comboList::item:disabled {{
        color: {NEUTRAL[600]}; background: transparent;
    }}

    /* ── buttons ────────────────────────────────────────────── */
    QPushButton {{
        font-family: {font_heading()};
        font-weight: 600;
        font-size: 14px;
        color: {TEXT};
        background: transparent;
        border: 1px solid {DIVIDER};
        border-radius: 0;
        padding: 7px 15px;
    }}
    QPushButton:hover {{ background: rgba(29,31,32,0.07); }}
    QPushButton:pressed {{ background: rgba(29,31,32,0.14); }}
    QPushButton:disabled {{ color: {NEUTRAL[500]}; border-color: {DIVIDER}; }}

    QPushButton[variant="primary"] {{
        background: {ACCENT}; color: {BG}; border: 1px solid {ACCENT};
    }}
    QPushButton[variant="primary"]:hover  {{ background: {ACCENT_RAMP[600]}; }}
    QPushButton[variant="primary"]:pressed {{ background: {ACCENT_RAMP[700]}; }}
    QPushButton[variant="primary"]:disabled {{
        background: {NEUTRAL[300]}; color: {NEUTRAL[500]}; border-color: {NEUTRAL[300]};
    }}

    QPushButton[variant="ghost"] {{
        color: {ACCENT}; border: 1px solid transparent; padding: 7px 6px;
    }}
    QPushButton[variant="ghost"]:hover {{ background: rgba(89,128,166,0.12); }}

    QPushButton[variant="icon"] {{ padding: 0; min-width: 32px; min-height: 32px; }}

    /* borderless icon button — .btn-ghost.btn-icon in the mockup */
    /* borderless icon button; the size comes from the widget itself, so the
       hover fill covers exactly the button box and nothing around it */
    QPushButton[variant="icon-ghost"] {{
        padding: 0; border: 1px solid transparent; background: transparent;
    }}
    /* the same neutral tint every other button uses — an accent fill reads as
       «выбрано», not «под курсором» */
    QPushButton[variant="icon-ghost"]:hover {{ background: rgba(29,31,32,0.07); }}
    QPushButton[variant="icon-ghost"]:pressed {{ background: rgba(29,31,32,0.14); }}

    /* icon button with a fill — used by the history arrows, where a bare icon
       reads as decoration rather than a control */
    QPushButton[variant="icon-soft"] {{
        padding: 0; min-width: 26px; min-height: 26px;
        background: {SURFACE}; border: 1px solid {DIVIDER};
    }}
    QPushButton[variant="icon-soft"]:hover {{ background: rgba(29,31,32,0.10); }}
    QPushButton[variant="icon-soft"]:pressed {{ background: rgba(29,31,32,0.16); }}
    QPushButton[variant="icon-soft"]:disabled {{
        background: transparent; border-color: rgba(29,31,32,0.08);
    }}

    /* compact button for use inside table rows — the property must not be
       called "size": that is a real QWidget property and setting it would
       resize the button instead of tagging it for the stylesheet */
    QPushButton[compact="true"] {{ padding: 3px 10px; font-size: 13px; }}

    /* a field that has to fit inside a 34px table row: the default 4px
       vertical padding leaves too little room and clips the digits */
    QSpinBox[compact="true"] {{ padding: 2px 6px; min-height: 0; }}

    /* pagination: square buttons — padding must be 0 or the digit gets
       squeezed out of the fixed 32px width */
    QPushButton[variant="page"] {{ padding: 0; min-width: 32px; min-height: 32px; }}
    QPushButton[variant="page"]:hover {{ background: rgba(29,31,32,0.07); }}
    QPushButton[variant="page-active"] {{
        padding: 0; min-width: 32px; min-height: 32px;
        background: {ACCENT}; color: {BG}; border: 1px solid {ACCENT};
    }}
    QPushButton[variant="page-active"]:hover {{ background: {ACCENT_RAMP[600]}; }}

    /* ── checkboxes ─────────────────────────────────────────── */
    QCheckBox {{ font-size: 13px; color: {NEUTRAL[700]}; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px;
        border: 1px solid {DIVIDER}; background: {SURFACE};
    }}
    QCheckBox::indicator:checked {{
        background: {ACCENT}; border: 1px solid {ACCENT};
    }}

    /* ── tables ─────────────────────────────────────────────── */
    QTableWidget, QTableView {{
        background: transparent;
        border: none;
        gridline-color: transparent;
        font-size: 14px;
    }}
    /* .table td: padding var(--space-2), 1px hairline under every row.
       Row hover is painted by a delegate so the whole row lights up, not
       just the cell under the cursor. */
    QTableWidget::item, QTableView::item {{
        border-bottom: 1px solid rgba(29,31,32,0.08);
        padding: {SP2}px;
    }}
    QTableWidget::item:selected {{ background: {ACCENT_RAMP[100]}; color: {TEXT}; }}
    QHeaderView {{ background: transparent; }}
    /* .table th — 13px/600, left aligned, bottom aligned, no uppercase
       (every screen overrides the base uppercase rule) */
    QHeaderView::section {{
        background: transparent;
        color: rgba(29,31,32,0.6);
        border: none;
        border-bottom: 1px solid {DIVIDER};
        padding: {SP1}px {SP2}px;
        font-size: 13px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{ background: transparent; border: none; }}

    /* ── scrollbars ─────────────────────────────────────────── */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {NEUTRAL[400]}; min-height: 30px; border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {NEUTRAL[500]}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{
        background: {NEUTRAL[400]}; min-width: 30px; border-radius: 5px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """
