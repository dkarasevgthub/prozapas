"""RootWindow — switches between the login screen and the main application."""
from PyQt6.QtWidgets import QMainWindow, QStackedWidget

from . import reference
from .pages.login import LoginPage
from .session import session
from .shell import MainView


class RootWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ProЗапас — учёт и логистика")
        self.resize(1280, 820)
        # 1200 — измеренная ширина, при которой самая широкая страница
        # (главная: три карточки и две панели) помещается без прокрутки
        self.setMinimumSize(1220, 700)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._login = LoginPage()
        self._login.logged_in.connect(self._enter_app)
        self._stack.addWidget(self._login)

        self._main = None
        # «Запомнить меня» хранит refresh-токен: сессия поднимается без пароля,
        # но только если сервер его ещё принимает.
        if session.resume():
            self._enter_app()
        else:
            self._show_login()

    def _show_login(self):
        self._stack.setCurrentWidget(self._login)

    def _enter_app(self):
        # Справочники — один запрос на всю сессию: их спрашивает почти каждый
        # экран, и ходить за ними на каждую перерисовку незачем.
        reference.load(force=True)
        if self._main is not None:
            self._stack.removeWidget(self._main)
            self._main.deleteLater()
        self._main = MainView()
        self._main.logout.connect(self._do_logout)
        self._stack.addWidget(self._main)
        self._stack.setCurrentWidget(self._main)

    def _do_logout(self):
        session.logout()
        reference.clear()
        self._login.reset()
        self._show_login()
