"""Paths to files shipped with the application."""
import os

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
APP_ICON = os.path.join(ASSETS_DIR, "prozapas.ico")
_GENERATED_DIR = os.path.join(ASSETS_DIR, "generated")


def icon_png(name: str, color: str, size: int = 16):
    """Render one of the line icons to a PNG and return a QSS-usable path.

    Qt style sheets can only point `image:` at a file, so sub-control icons
    (the calendar button of a date field) need the SVG baked out once.
    Returns None if the file cannot be written — the style then just omits it.
    """
    from .widgets.common import svg_pixmap  # deferred: common imports theme

    stem = f"{name}-{size}-{color.lstrip('#')}"
    path = os.path.join(_GENERATED_DIR, f"{stem}.png")
    retina = os.path.join(_GENERATED_DIR, f"{stem}@2x.png")
    try:
        if not os.path.exists(path):
            os.makedirs(_GENERATED_DIR, exist_ok=True)
            # 1× file drives the drawn size; Qt picks the @2x one automatically
            # on high-DPI screens, so the icon stays crisp without growing
            if not svg_pixmap(name, color, size, dpr=1).save(path):
                return None
            svg_pixmap(name, color, size * 2, dpr=1).save(retina)
    except OSError:
        return None
    return path.replace("\\", "/")      # QSS urls want forward slashes
