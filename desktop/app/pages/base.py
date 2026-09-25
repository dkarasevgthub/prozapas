"""Base class for content pages."""
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLayout,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..widgets.blueprint import MARK, BlueprintFrame
from ..widgets.common import icon_button


def _edge(item):
    """Invisible margin an item carries at its top and bottom edges.

    A blueprint frame reserves `MARK` px around itself for the corner marks.
    That reservation adds to the layout spacing, so two panels end up 12px
    further apart than the mockup unless it is subtracted again.
    """
    if isinstance(item, BlueprintFrame):
        return MARK
    if isinstance(item, QLayout):
        for i in range(item.count()):
            child = item.itemAt(i)
            if isinstance(child.widget(), BlueprintFrame):
                return MARK
            inner = child.layout()
            if inner is not None and _edge(inner):
                return MARK
        return 0
    if isinstance(item, QWidget) and item.layout() is not None:
        # e.g. a TableSection, which wraps a blueprint frame
        first = item.layout().itemAt(0)
        if first is not None and isinstance(first.widget(), BlueprintFrame):
            return MARK
    return 0


class BlockColumn(QVBoxLayout):
    """A column whose *visible* gaps are all `gap` px.

    Blueprint panels reserve room around themselves for the corner marks, so a
    plain layout spacing shows up as 32px between two panels where the mockup
    has 20px. Every gap here is a spacer that is recomputed from what actually
    sits next to it, and hidden blocks collapse instead of leaving a hole.
    """

    def __init__(self, parent=None, gap=None, page=None):
        super().__init__(parent)
        self.setSpacing(0)
        self._gap = theme.SP6 if gap is None else gap
        self._page = page                   # owner, needed for the history arrows
        self._blocks = []                   # [(item, spacer before it)]

    def add_block(self, item, visible=True):
        """Append a block. Pass ``visible=False`` for one that starts hidden —
        Qt only marks a freshly added widget visible on the next layout pass,
        so its own state cannot be trusted here."""
        if (self._page is not None and not self._blocks
                and isinstance(item, QWidget) and item.objectName() == "breadcrumb"):
            item = self._page.history_row(item)
        self._align_edges(item)
        spacer = None
        if self._blocks:
            spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum,
                                 QSizePolicy.Policy.Fixed)
            self.addItem(spacer)
        if isinstance(item, QLayout):
            self.addLayout(item)
        else:
            self.addWidget(item)
            item.setVisible(visible)
        self._blocks.append([item, spacer, visible])
        self.refresh_gaps()

    @staticmethod
    def _align_edges(item):
        """Line the block's left and right edges up with the panels'.

        The column is laid out `MARK` px wider than the mockup's content box so
        that the corner marks have somewhere to go; everything that is not a
        blueprint panel has to give that room back, or headings and text would
        start `MARK` px left of the panel borders.
        """
        inset = MARK - _edge(item)
        if inset <= 0 or not isinstance(item, (QLayout, QWidget)):
            return
        m = item.contentsMargins()
        item.setContentsMargins(m.left() + inset, m.top(),
                                m.right() + inset, m.bottom())

    def set_block_visible(self, item, visible):
        """Show or hide a block and close the gap it leaves behind."""
        for record in self._blocks:
            if record[0] is item:
                record[2] = visible
                break
        if isinstance(item, QWidget):
            item.setVisible(visible)
        self.refresh_gaps()

    def refresh_gaps(self):
        """Recompute the gap before every block.

        Call this after showing or hiding a block: a hidden one must not leave
        its spacing behind, and its neighbours have to close up instead.
        """
        previous = None
        for item, spacer, shown in self._blocks:
            if spacer is not None:
                gap = 0
                if shown and previous is not None:
                    gap = max(0, self._gap - _edge(previous) - _edge(item))
                spacer.changeSize(0, gap, QSizePolicy.Policy.Minimum,
                                  QSizePolicy.Policy.Fixed)
            if shown:
                previous = item
        self.invalidate()

    def reset_blocks(self):
        """Forget the blocks — for callers that empty the layout and rebuild."""
        self._blocks = []


class Page(QWidget):
    """A content page. Subclasses fill ``self.col`` in ``build()``.

    ``nav`` is the MainView; call ``self.nav.go(key, **params)`` to navigate.

    Blocks added with :meth:`add_block` are spaced so that the *visible* gap
    matches the mockup; pages that just use ``self.col`` keep a plain spacing.
    """

    def __init__(self, nav, **params):
        super().__init__()
        self.nav = nav
        self.params = params
        self.col = BlockColumn(self, page=self)
        # horizontal margins are short by MARK: blocks added with add_block()
        # give that back unless they are panels, whose corner marks live there
        self.col.setContentsMargins(theme.SP8 - MARK, theme.SP8,
                                    theme.SP8 - MARK, theme.SP6)
        self.col.setSpacing(theme.SP6)
        self._manual_gaps = False
        self.build()

    def can_go_back(self):
        """Pages with an inner view (list ⇄ card) override this."""
        return self.nav.can_back()

    def go_back(self):
        self.nav.back()

    def history_row(self, crumb):
        """Back/forward arrows in front of the breadcrumb, as in a browser."""
        row = QHBoxLayout()
        row.setSpacing(2)
        for icon, tip, handler, enabled in (
            ("chevron-left", "Назад (Alt+←)", self.go_back, self.can_go_back()),
            ("chevron", "Вперёд (Alt+→)", self.nav.forward, self.nav.can_forward()),
        ):
            # the icon is a pixmap, so a disabled button has to be drawn pale
            colour = theme.NEUTRAL[600] if enabled else theme.NEUTRAL[400]
            btn = icon_button(icon, tip, colour, size=15, box=26, soft=True)
            btn.setEnabled(enabled)
            btn.clicked.connect(lambda _=False, h=handler: h())
            row.addWidget(btn)
        row.addSpacing(theme.SP2)
        row.addWidget(crumb)
        row.addStretch(1)
        return row

    def add_block(self, item, visible=True):
        """Append a widget or layout, keeping a 20px gap between visible edges."""
        if not self._manual_gaps:
            self._manual_gaps = True
            self.col.setSpacing(0)          # gaps are managed by refresh_gaps()
        self.col.add_block(item, visible)

    def set_block_visible(self, item, visible):
        self.col.set_block_visible(item, visible)

    def show_error(self, error):
        """Сообщить об отказе сервера.

        Экраны зовут это из обработчиков: у ошибки API есть `title` для
        человека, а разбирать её причину — дело вызывающего.
        """
        from ..widgets.dialog import confirm_dialog
        title = getattr(error, "title", None) or str(error)
        confirm_dialog(self, "Не получилось", title,
                       confirm_label="Понятно", cancel_label="")

    def refresh_gaps(self):
        self.col.refresh_gaps()

    def build(self):  # override
        pass
