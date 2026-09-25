"""ProЗапас desktop — application entry point."""
import os
import sys

from app import config, devices, theme
from app.fonts import setup_fonts
from app.resources import APP_ICON
from app.service_host import ABSENT, EXTERNAL, host
from app.window import RootWindow
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication


def _use_own_taskbar_icon():
    """Windows groups windows by AppUserModelID and takes the taskbar icon
    from it — without this the taskbar shows the Python interpreter's icon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("stalker.prozapas.desktop")
    except Exception:
        pass        # cosmetic only — never block startup over it


def main():
    _use_own_taskbar_icon()
    app = QApplication(sys.argv)
    # set on the application so every window and dialog inherits it
    if os.path.exists(APP_ICON):
        app.setWindowIcon(QIcon(APP_ICON))
    setup_fonts(app)
    app.setStyleSheet(theme.build_qss())
    # Служба устройств поднимается вместе с приложением и гаснет вместе с ним;
    # запущенную до нас оставляем в покое, свою — закрываем сами. Делаем это
    # до окна: ожидание короткое, но показывать ради него пустую раму незачем.
    print(config.summary())
    state = host.start()
    if state == ABSENT:
        print("служба устройств не запустилась — оборудование недоступно")
    elif devices.connect():
        print("работаем с оборудованием"
              + (" (служба уже была запущена)" if state == EXTERNAL else ""))
    else:
        devices.client.start()          # свяжемся, как только служба ответит
    app.aboutToQuit.connect(devices.client.stop)
    app.aboutToQuit.connect(host.stop)

    win = RootWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
