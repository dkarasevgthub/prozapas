"""Administrator access, persisted selection and service startup integration."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QPushButton

from app import equipment
from app.service_host import ServiceHost
from app.session import session
from app.sidebar import Sidebar
from app.widgets.printer_settings import PrinterSettingsDialog, show_printer_settings


class PrinterSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "equipment.json"
        self.saved_user = session.user
        self.addCleanup(setattr, session, "user", self.saved_user)
        session.user = {"id": 1, "full_name": "Администратор", "role": "admin"}
        path_patch = patch("app.equipment._settings_path", return_value=self.path)
        path_patch.start()
        self.addCleanup(path_patch.stop)
        printer_patch = patch("app.equipment.list_printers", return_value=["Принтер этикеток", "Other"])
        self.printer_list = printer_patch.start()
        self.addCleanup(printer_patch.stop)

    def test_only_admin_can_open_or_save(self):
        for role in (None, "manager", "stockman"):
            with self.subTest(role=role):
                session.user = {"role": role} if role else None
                with self.assertRaises(PermissionError):
                    PrinterSettingsDialog()
                with self.assertRaises(PermissionError):
                    equipment.save_printer("Принтер этикеток")
                self.assertFalse(self.path.exists())
                self.assertIsNone(show_printer_settings())

    def test_sidebar_entry_only_for_admin(self):
        for role in ("admin", "manager", "stockman"):
            with self.subTest(role=role):
                session.user["role"] = role
                sidebar = Sidebar()
                self.assertEqual(
                    sidebar.findChild(QPushButton, "printerSettingsButton") is not None,
                    role == "admin",
                )
                self.assertEqual(
                    sidebar.findChild(QPushButton, "deviceLogsButton") is not None,
                    role == "admin",
                )
                sidebar.deleteLater()
                self.app.processEvents()

    def test_selection_persists_for_other_app_accounts(self):
        equipment.save_printer("Принтер этикеток")
        session.user = {"role": "stockman"}
        self.assertEqual(equipment.selected_printer(), "Принтер этикеток")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"printer_name": "Принтер этикеток"})

    def test_missing_printer_does_not_replace_saved_selection(self):
        equipment.save_printer("Принтер этикеток")
        with self.assertRaises(ValueError):
            equipment.save_printer("Removed printer")
        self.assertEqual(equipment.selected_printer(), "Принтер этикеток")

    def test_corrupt_settings_fall_back_to_environment_config(self):
        self.path.write_text("invalid json", encoding="utf-8")
        with patch("app.equipment.config.get", return_value="Configured printer"):
            self.assertEqual(equipment.selected_printer(), "Configured printer")

    def test_saved_selection_is_passed_to_service_over_env(self):
        equipment.save_printer("Принтер этикеток")
        with patch("app.service_host.QProcess") as process_class:
            process = process_class.return_value
            process.waitForStarted.return_value = True
            self.assertTrue(ServiceHost()._spawn())
            environment = process.setProcessEnvironment.call_args.args[0]
            self.assertEqual(environment.value("PROZAPAS_PRINTER_NAME"), "Принтер этикеток")

    def test_dialog_save_refresh_and_changed_role(self):
        dialog = PrinterSettingsDialog()
        self.addCleanup(dialog.deleteLater)
        dialog.printers.setCurrentIndex(dialog.printers.findData("Принтер этикеток"))
        dialog.save_button.click()
        self.assertEqual(equipment.selected_printer(), "Принтер этикеток")
        self.assertIn("Перезапустите", dialog.status.text())
        dialog.refresh()
        self.assertEqual(dialog.printers.currentData(), "Принтер этикеток")
        session.user["role"] = "manager"
        dialog.printers.setCurrentIndex(dialog.printers.findData("Other"))
        dialog.save_button.click()
        self.assertIn("только администратору", dialog.status.text())
        self.assertEqual(equipment.selected_printer(), "Принтер этикеток")

    def test_no_printers_and_removed_printer(self):
        self.printer_list.return_value = []
        dialog = PrinterSettingsDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIn("не найдены", dialog.hint.text())
        self.printer_list.return_value = ["Принтер этикеток"]
        dialog.refresh()
        dialog.printers.setCurrentIndex(1)
        self.printer_list.return_value = []
        dialog.save_button.click()
        self.assertFalse(self.path.exists())
        self.assertIn("не найден", dialog.status.text())


if __name__ == "__main__":
    unittest.main()
