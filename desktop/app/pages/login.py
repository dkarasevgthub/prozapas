"""Login screen."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..api.errors import ApiError, Forbidden, NetworkError, Unauthorized
from ..session import session
from ..widgets.blueprint import BlueprintFrame
from ..widgets.common import button


class LoginPage(QWidget):
    logged_in = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.addStretch(1)

        center = QVBoxLayout()
        center.setSpacing(0)
        center.addStretch(1)

        # brand
        brand = QLabel(f'Pro<span style="color:{theme.ACCENT_RAMP[700]}">Запас</span>')
        brand.setTextFormat(Qt.TextFormat.RichText)
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setStyleSheet(
            f"font-family:{theme.font_heading()};font-weight:600;font-size:34px;"
            f"letter-spacing:-0.3px;"
        )
        sub = QLabel("учёт и логистика")
        sub.setObjectName("kicker")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(brand)
        center.addSpacing(2)
        center.addWidget(sub)
        center.addSpacing(theme.SP6)

        # card
        card = BlueprintFrame(padding=theme.SP6)
        card.setFixedWidth(400)
        cl = card.content_layout()
        cl.setSpacing(theme.SP4)

        title = QLabel("Вход в систему")
        title.setObjectName("h4")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-family:{theme.font_heading()};font-size:22px;")
        cl.addWidget(title)
        cl.addSpacing(theme.SP2)

        cl.addWidget(self._field_label("Логин"))
        self.login = QLineEdit()
        self.login.setPlaceholderText("i.ivanov")
        cl.addWidget(self.login)

        cl.addWidget(self._field_label("Пароль"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("••••••••")
        cl.addWidget(self.password)

        self.remember = QCheckBox("Запомнить меня")
        self.remember.setChecked(True)
        cl.addSpacing(theme.SP2)
        cl.addWidget(self.remember)

        self.error = QLabel("")
        self.error.setWordWrap(True)      # длинные сообщения не влезают в карточку
        self.error.setStyleSheet(f"font-size:13px;color:{theme.DANGER};")
        self.error.setVisible(False)
        cl.addWidget(self.error)

        submit = button("Войти", "primary")
        submit.clicked.connect(self._submit)
        cl.addSpacing(theme.SP2)
        cl.addWidget(submit)

        hint = QLabel("Забыли пароль? Обратитесь к администратору системы.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[600]};")
        cl.addSpacing(theme.SP2)
        cl.addWidget(hint)

        center.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        center.addStretch(1)

        outer.addLayout(center)
        outer.addStretch(1)

        self.password.returnPressed.connect(self._submit)
        self.login.returnPressed.connect(self._submit)

    def reset(self):
        """Очистить форму — вызывается при выходе из системы."""
        self.password.clear()
        self.error.setVisible(False)
        self.login.setFocus()

    def _field_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size:12px;color:{theme.NEUTRAL[700]};")
        return lbl

    def _show_error(self, msg):
        self.error.setText(msg)
        self.error.setVisible(True)

    def _submit(self):
        login = self.login.text().strip()
        password = self.password.text()
        if not login or not password:
            self._show_error("Введите логин и пароль.")
            return

        try:
            session.login(login, password, remember=self.remember.isChecked())
        except NetworkError:
            self._show_error("Сервер недоступен. Проверьте связь и попробуйте снова.")
            return
        except Forbidden:
            self._show_error("Учётная запись заблокирована. Обратитесь к администратору.")
            self.password.clear()
            return
        except Unauthorized:
            # Намеренно одна формулировка на «нет такого логина» и «неверный
            # пароль»: иначе по ответу можно перебирать учётные записи. Сервер
            # по той же причине не уточняет, что именно неверно.
            self._show_error("Неверный логин или пароль.")
            self.password.clear()
            self.password.setFocus()
            return
        except ApiError as exc:
            self._show_error(exc.title)
            self.password.clear()
            return

        self.error.setVisible(False)
        self.password.clear()
        self.logged_in.emit()
