"""Exercise the real print worker with only the Windows OS API substituted."""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from devices.drivers.printer import PrinterDriver
from wire_client import qt_app, wait_for


class WindowsSpoolerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.spooler = MagicMock()
        self.spooler.OpenPrinter.return_value = "handle"
        for context in (patch.dict("sys.modules", {"win32print": self.spooler}),
                        patch("devices.drivers.printer.list_printers", return_value=["Labels"])):
            context.start()
            self.addCleanup(context.stop)
        self.driver = PrinterDriver(name="Labels", retry_delays=[0, 0], stub=False)
        self.driver.open()
        self.addCleanup(self.driver.close)

    def done(self, job, state):
        wait_for(lambda: self.driver.get_status(job) == state, message="Windows spooler job result")

    def test_raw_print_encoding_copies_and_os_handle_cleanup(self):
        job, _ = self.driver.submit("one", "zpl", "Этикетка", copies=2)
        self.done(job, "done")
        self.spooler.OpenPrinter.assert_called_once_with("Labels")
        self.spooler.WritePrinter.assert_called_once_with("handle", "Этикетка".encode("cp1251") * 2)
        self.spooler.EndPagePrinter.assert_called_once_with("handle")
        self.spooler.EndDocPrinter.assert_called_once_with("handle")
        self.spooler.ClosePrinter.assert_called_once_with("handle")

    def test_transient_spooler_failures_retry_and_close_every_handle(self):
        self.spooler.WritePrinter.side_effect = [OSError("offline"), OSError("busy"), 1]
        job, _ = self.driver.submit("retry", "zpl", "label")
        self.done(job, "done")
        self.assertEqual(self.spooler.WritePrinter.call_count, 3)
        self.assertEqual(self.spooler.ClosePrinter.call_count, 3)

    def test_failed_job_can_be_retried_without_creating_another_job(self):
        self.spooler.WritePrinter.side_effect = OSError("printer unplugged")
        job, _ = self.driver.submit("failed", "zpl", "label")
        self.done(job, "failed")
        self.assertEqual(len(self.driver.get_queue()), 1)
        self.spooler.WritePrinter.side_effect = None
        self.driver.retry(job)
        self.done(job, "done")
        self.assertEqual(len(self.driver.get_queue()), 1)

    def test_same_print_key_is_not_sent_twice_to_os(self):
        job, _ = self.driver.submit("same", "zpl", "label")
        self.done(job, "done")
        other, _ = self.driver.submit("same", "zpl", "label")
        self.assertEqual(job, other)
        self.spooler.WritePrinter.assert_called_once()
