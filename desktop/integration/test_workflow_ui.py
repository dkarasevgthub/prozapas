"""User interactions against the real HTTP API and a separate device process."""
import os
from pathlib import Path
import sys
import unittest
import uuid

os.environ["PROZAPAS_PIPE_NAME"] = "prozapas-ui-" + uuid.uuid4().hex

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QLabel,
                            QLineEdit, QPushButton, QTableWidget, QWidget)

from app import api, devices, reference, theme
from app.session import session
from app.sidebar import NavRow
from app.window import RootWindow
from wire_client import ServiceProcess, qt_app, wait_for

PASSWORD = "system-test-password"
ARTIFACTS = Path(os.environ["SYSTEM_TEST_ARTIFACTS"])


class WorkflowUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        cls.app.setStyleSheet(theme.build_qss())

    def setUp(self):
        session.logout()
        reference.clear()
        self.service = ServiceProcess(simulated=False, pipe=devices.PIPE_NAME)
        self.addCleanup(self.service.close)
        self.hardware = self.service.client()
        self.assertTrue(self.hardware.request("attach", devices=["scanner", "scale"])["ok"])
        self.assertTrue(devices.connect())
        self.addCleanup(devices.client.stop)
        wait_for(lambda: all(devices.available(kind) for kind in ("scanner", "scale", "printer")))
        self.errors = []
        old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, traceback: self.errors.append(str(value))
        self.addCleanup(setattr, sys, "excepthook", old_hook)
        self.window = RootWindow()
        self.window.show()
        self.addCleanup(self.dispose_window)
        self.expected_dialog = False
        self.guard = QTimer()
        self.guard.timeout.connect(self.dismiss_unexpected_dialog)
        self.guard.start(100)
        self.addCleanup(self.guard.stop)
        QTest.qWait(30)

    def dispose_window(self):
        self.window.grab().save(str(ARTIFACTS / (self._testMethodName + ".png")))
        session.logout()
        reference.clear()
        self.window.hide()
        self.window.deleteLater()
        QApplication.processEvents()

    def dismiss_unexpected_dialog(self):
        dialog = QApplication.activeModalWidget()
        if dialog and not self.expected_dialog:
            self.errors.append("Unexpected dialog: " + dialog.windowTitle() + " " +
                               " ".join(label.text() for label in dialog.findChildren(QLabel)))
            dialog.reject()

    @property
    def page(self):
        return self.window._main._scroll.widget()

    def click(self, widget):
        self.assertIsNotNone(widget)
        self.assertTrue(widget.isEnabled())
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertEqual(self.errors, [])

    def button(self, text, parent=None):
        parent = parent or self.page
        matches = [b for b in parent.findChildren(QPushButton) if b.text() == text and b.isVisible()]
        self.assertEqual(len(matches), 1, f"Expected visible button {text!r}: {len(matches)}")
        return matches[0]

    def navigate(self, route):
        rows = [row for row in self.window._main._sidebar.findChildren(NavRow)
                if row.property("route") == route]
        self.assertEqual(len(rows), 1)
        self.click(rows[0])

    def login(self, name):
        if session.authorized:
            self.click(self.window._main._sidebar.findChild(QPushButton, "logoutBtn"))
        form = self.window._login
        form.login.setText(name)
        form.password.setText(PASSWORD)
        form.remember.setChecked(False)
        self.click(self.button("Войти", form))
        self.assertTrue(session.authorized, form.error.text())
        self.assertEqual(session.user["login"], name)

    def select_row(self, text):
        for table in self.page.findChildren(QTableWidget):
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None and text in item.text():
                        table.scrollToItem(item)
                        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                                         pos=table.visualItemRect(item).center())
                        QApplication.processEvents()
                        self.assertEqual(self.errors, [])
                        return
        self.fail(f"No visible table row for {text!r}")

    def dialog(self, trigger, fields=None, confirm="Сохранить"):
        errors = []
        self.expected_dialog = True

        def fill():
            dlg = QApplication.activeModalWidget()
            try:
                self.assertIsNotNone(dlg)
                for key, value in (fields or {}).items():
                    widget = dlg.findChild(QWidget, f"form-{key}")
                    self.assertIsNotNone(widget, key)
                    if isinstance(widget, QComboBox):
                        index = widget.findData(value)
                        self.assertGreaterEqual(index, 0, key)
                        widget.setCurrentIndex(index)
                    else:
                        widget.setText(str(value))
                self.click(self.button(confirm, dlg))
                self.assertFalse(dlg.isVisible(), "Form validation prevented saving")
            except Exception as error:
                errors.append(str(error))
            finally:
                if dlg and dlg.isVisible():
                    dlg.reject()

        QTimer.singleShot(50, fill)
        try:
            self.click(trigger)
        finally:
            self.expected_dialog = False
        self.assertEqual(errors, [])

    def create_order(self, lines):
        self.login("o.egorova")
        self.navigate("orders")
        self.click(self.button("Исходящие"))
        self.click(self.button("+ Создать заказ"))
        warehouse = next(w for w in reference.warehouses() if w["code"] == "129")
        self.page._wh_input.setCurrentIndex(self.page._wh_input.findData(warehouse["id"]))
        for article, quantity in lines:
            self.page._search_input.setText(article)
            self.click(self.button("Добавить"))
            spin = self.button("Добавить").parentWidget().findChild(QDoubleSpinBox)
            self.assertIsNotNone(spin)
            spin.setValue(quantity)
            self.click(self.button("Добавить"))
        self.click(self.button("Отправить заказ"))
        return api.client.order(self.page.params["id"])

    def open_order(self, order):
        self.navigate("orders")
        self.page._search_input.setText(str(order["number"]))
        self.select_row(str(order["number"]))

    def open_document(self, section, order):
        self.navigate(section)
        self.page._search_input.setText(str(order["number"]))
        self.select_row(str(order["number"]))

    def send_weight(self, grams):
        result = self.hardware.request("emit", event="weight", value=grams, stable=True)
        self.assertTrue(result["ok"], result)
        wait_for(lambda: self.page._live is not None and self.page._live[0] == grams / 1000,
                 message="live weight on UI")

    def pack(self, article, grams):
        self.select_row(article)
        self.send_weight(grams)
        self.click(self.button("Считать вес"))
        self.click(self.button("Напечатать этикетку"))
        box = api.client.shipment(self.page._active_id)["boxes"][-1]
        wait_for(lambda: any(box["barcode"].encode("cp866") in p.read_bytes()
                             for p in (self.service.root / "printed").glob("*.zpl")),
                 message="label bytes delivered to independent print sink")
        return box

    def scan(self, code):
        self.assertTrue(self.hardware.request("emit", event="scan", code=code)["ok"])
        # Separate legitimate scans by the client's 500 ms duplicate filter.
        QTest.qWait(550)
        self.assertEqual(self.errors, [])

    def test_complete_transfer_through_widgets_print_scan_and_stock_balances(self):
        order = self.create_order([("100512", 2), ("100655", 4)])
        warehouses = {w["code"]: w["id"] for w in reference.warehouses()}
        initial = {}
        for article in ("100512", "100655"):
            item = api.client.catalog(q=article)["items"][0]
            initial[article] = {r["warehouse"]["id"]: r["qty"]
                                for r in api.client.stock_by_warehouse(item["id"])}
        self.login("e.morozova")
        self.open_order(order)
        self.click(self.button("Принять заказ"))
        self.assertEqual(api.client.order(order["id"])["status"], "processing")
        self.login("p.nikitin")
        self.open_document("shipping", order)
        boxes = [self.pack("100512", 3200), self.pack("100655", 20)]
        self.click(self.button("Отгрузить"))
        self.assertEqual(api.client.order(order["id"])["status"], "shipped")
        self.login("p.sokolov")
        self.open_document("receiving", order)
        self.scan("NOT-A-BOX")
        self.assertIn("не найден", self.page._scan_error)
        for box in boxes:
            self.scan(box["barcode"])
            self.assertEqual(self.page._active_box["barcode"], box["barcode"])
            self.send_weight(box["weight"] * 1000)
            self.scan(box["barcode"])
            self.assertIsNone(self.page._active_box)
        self.click(self.button("Завершить приемку"))
        self.assertEqual(api.client.order(order["id"])["status"], "received")
        self.assertEqual([event["status"] for event in api.client.order_history(order["id"])],
                         ["created", "processing", "shipped", "received"])
        for article, quantity in (("100512", 2), ("100655", 4)):
            item = api.client.catalog(q=article)["items"][0]
            after = {r["warehouse"]["id"]: r for r in api.client.stock_by_warehouse(item["id"])}
            self.assertEqual(after[warehouses["129"]]["qty"], initial[article][warehouses["129"]] - quantity)
            self.assertEqual(after[warehouses["128"]]["qty"], initial[article].get(warehouses["128"], 0) + quantity)

    def test_partial_shipment_and_missing_box_confirmations(self):
        order = self.create_order([("100512", 4)])
        self.login("e.morozova")
        self.open_order(order)
        self.click(self.button("Принять заказ"))
        self.login("p.nikitin")
        self.open_document("shipping", order)
        self.pack("100512", 3200)
        self.dialog(self.button("Отгрузить"), confirm="Отгрузить с недостачей")
        self.login("p.sokolov")
        self.open_document("receiving", order)
        self.dialog(self.button("Завершить приемку"), confirm="Завершить с недостачей")
        receipt = api.client.receipt(order["id"])
        self.assertEqual(receipt["status"], "done")
        self.assertTrue(all(box["received_at"] is None for box in receipt["boxes"]))

    def test_catalog_creation_edit_and_stock_operation_through_forms(self):
        self.login("admin")
        self.navigate("catalog")
        article = "UI-" + uuid.uuid4().hex[:8]
        self.dialog(self.button("+ Добавить позицию"), {
            "article": article, "name": "UI product", "unit": "шт.", "unit_weight": "0.25"}, confirm="Добавить")
        search = next(w for w in self.page.findChildren(QLineEdit) if w.placeholderText() == "Код, артикул или наименование")
        search.setText(article)
        self.select_row(article)
        self.dialog(self.button("Редактировать товар"), {"name": "UI product edited"})
        self.dialog(self.button("Провести операцию"), {"type": "receipt", "qty": "5"}, confirm="Провести")
        item = api.client.catalog(q=article)["items"][0]
        self.assertEqual(item["name"], "UI product edited")
        balance = next(r for r in api.client.stock_by_warehouse(item["id"])
                       if r["warehouse"]["id"] == session.warehouse_id)
        self.assertEqual(balance["qty"], 5)
        self.assertEqual(api.client.movements(item["id"])["items"][-1]["delta"], 5)

    def test_role_navigation_login_errors_and_logout(self):
        self.click(self.button("Войти", self.window._login))
        self.assertIn("Введите", self.window._login.error.text())
        self.window._login.login.setText("admin")
        self.window._login.password.setText("wrong-password")
        self.click(self.button("Войти", self.window._login))
        self.assertFalse(session.authorized)
        self.assertIn("Неверный", self.window._login.error.text())
        for login, admin in (("admin", True), ("o.egorova", False), ("p.sokolov", False)):
            self.login(login)
            for route in ("home", "orders", "catalog", "stock", "shipping", "receiving"):
                self.navigate(route)
                self.assertEqual(self.errors, [])
            sidebar = self.window._main._sidebar
            self.assertEqual(sidebar.findChild(QPushButton, "deviceLogsButton") is not None, admin)
            self.assertEqual(sidebar.findChild(QPushButton, "printerSettingsButton") is not None, admin)
        self.click(self.window._main._sidebar.findChild(QPushButton, "logoutBtn"))
        self.assertFalse(session.authorized)
        self.assertFalse(api.transport.authorized)

    def test_device_loss_blocks_packing_and_recovery_restores_ui(self):
        order = self.create_order([("100512", 1)])
        self.login("e.morozova")
        self.open_order(order)
        self.click(self.button("Принять заказ"))
        self.login("p.nikitin")
        self.open_document("shipping", order)
        self.assertTrue(self.hardware.request("attach", devices=["printer"])["ok"])
        self.assertTrue(self.hardware.request("emit", event="device", device="printer", state="offline")["ok"])
        wait_for(lambda: not devices.available("printer"))
        self.assertTrue(any("без принтера" in label.text() for label in self.page.findChildren(QLabel)))
        self.assertTrue(self.hardware.request("emit", event="device", device="printer", state="online")["ok"])
        wait_for(lambda: devices.available("printer"))
        self.hardware.request("detach", devices=["printer"])
        self.pack("100512", 1600)
