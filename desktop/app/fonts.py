"""Font resolution.

The design's stack is `Barlow / Barlow Condensed, system-ui, sans-serif`, but
Barlow ships only latin / latin-ext / vietnamese — it has no Cyrillic. So in
the original browser render every Russian string already fell through to
`system-ui` (Segoe UI on Windows); only "Pro", the login and digits were
actually Barlow. We therefore resolve both roles to the system UI face and get
the heading weight from `font-weight: 600` (which Windows maps to Segoe UI
Semibold), exactly reproducing what the render showed.

Qt style sheets don't reliably walk a comma-separated family list, so we pick
one concrete, existing family for body and heading and bake those names into
the stylesheet.
"""
import glob
import os

from PyQt6.QtGui import QFont, QFontDatabase

from . import theme

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")

# Deliberately no Barlow here: it would render Latin in Barlow and Cyrillic in
# a fallback face, mixing two typefaces inside the same label.
_BODY_CANDIDATES = ["Segoe UI", "Verdana", "sans-serif"]
_HEADING_CANDIDATES = ["Segoe UI", "Verdana", "sans-serif"]


def _first_available(candidates, families):
    for c in candidates:
        if c in families:
            return c
    return candidates[-1]


def setup_fonts(app):
    # load bundled TTFs, if any (e.g. a Cyrillic-capable face dropped in later)
    if os.path.isdir(_FONT_DIR):
        for path in glob.glob(os.path.join(_FONT_DIR, "*.ttf")):
            QFontDatabase.addApplicationFont(path)

    families = set(QFontDatabase.families())
    body = _first_available(_BODY_CANDIDATES, families)
    heading = _first_available(_HEADING_CANDIDATES, families)

    theme.BODY_FAMILY = body
    theme.HEADING_FAMILY = heading

    app.setFont(QFont(body, 10))
    return body, heading
