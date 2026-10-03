"""Log access is checked on construction, opening and logout."""
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from app.session import session
from app.widgets import devicelog
from app.window import RootWindow


class DeviceLogAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        saved_user = session.user
        self.addCleanup(setattr, session, "user", saved_user)
        devicelog.close_device_log()
        self.addCleanup(devicelog.close_device_log)
        session.user = {"id": 1, "role": "admin"}

    def test_non_admin_cannot_create_or_open_log(self):
        for role in (None, "manager", "stockman"):
            with self.subTest(role=role):
                session.user = {"role": role} if role else None
                with self.assertRaises(PermissionError):
                    devicelog.DeviceLogWindow()
                self.assertIsNone(devicelog.show_device_log())
                self.assertIsNone(devicelog._window)

    def test_admin_can_open_and_reuse_log(self):
        log = devicelog.show_device_log()
        self.assertTrue(log.isVisible())
        self.assertIs(devicelog.show_device_log(), log)

    def test_non_admin_cannot_reopen_previous_admin_log(self):
        log = devicelog.show_device_log()
        session.user = {"role": "manager"}
        self.assertIsNone(devicelog.show_device_log())
        self.assertFalse(log.isVisible())
        self.assertIsNone(devicelog._window)

    def test_logout_closes_administrator_log(self):
        with patch.object(session, "resume", return_value=False):
            root = RootWindow()
        self.addCleanup(root.deleteLater)
        log = devicelog.show_device_log()
        with patch("app.session.api.client.logout"):
            root._do_logout()
        self.assertFalse(log.isVisible())
        self.assertIsNone(devicelog._window)


if __name__ == "__main__":
    unittest.main()
