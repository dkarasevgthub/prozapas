"""Feed physical-driver byte streams via OS serial ports, outside the service."""
import os
import select
import threading
import unittest

from wire_client import ServiceProcess, qt_app, wait_for


@unittest.skipUnless(os.name == "posix", "PTY serial hardware fixture runs on Linux")
class SerialDriverContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        import pty
        self.scanner, scanner_slave = pty.openpty()
        self.scale, scale_slave = pty.openpty()
        self.addCleanup(os.close, self.scanner)
        self.addCleanup(os.close, self.scale)
        self.addCleanup(os.close, scanner_slave)
        self.addCleanup(os.close, scale_slave)
        self.service = ServiceProcess(simulated=False, extra_env={
            "PROZAPAS_SCANNER_PORT": os.ttyname(scanner_slave),
            "PROZAPAS_SCALE_PORT": os.ttyname(scale_slave)})
        self.addCleanup(self.service.close)
        self.client = self.service.client()
        states = self.client.request("devices")["devices"]
        self.assertEqual(states, dict.fromkeys(("scanner", "scale", "printer"), "online"))
        self.client.request("subscribe", events=["scan", "weight", "device"])

    def test_scanner_cr_lf_fragmentation_and_multiple_barcodes(self):
        os.write(self.scanner, b"BAR")
        os.write(self.scanner, b"CODE-123\r\nNEXT-456\n")
        self.client.event("scan", code="BARCODE-123")
        self.client.event("scan", code="NEXT-456")

    def test_scale_stream_stable_unstable_zero_and_synchronous_read(self):
        os.write(self.scale, b"garbage\n500 U\n500 S\n0 S\n")
        for value, stable in ((500, False), (500, True), (0, True)):
            self.client.event("weight", value=value, stable=stable, unit="g")
        result = self.client.request("scale.read", timeout_ms=100)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["value"], 0)

    def test_unstable_scale_times_out_without_stopping_other_commands(self):
        os.write(self.scale, b"100 U\n")
        self.client.event("weight", stable=False)
        reply = self.client.request("scale.read", timeout_ms=100)
        self.assertEqual(reply["error"]["code"], "scale_timeout")
        self.assertTrue(self.client.request("hello")["ok"])

    def test_tare_command_reaches_serial_port_and_ack_is_required(self):
        observed = []

        def scale_hardware():
            if select.select([self.scale], [], [], 3)[0]:
                observed.append(os.read(self.scale, 128))
                os.write(self.scale, b"OK\n")

        responder = threading.Thread(target=scale_hardware)
        responder.start()
        try:
            result = self.client.request("scale.tare")
        finally:
            responder.join(timeout=4)
        self.assertTrue(result["ok"], result)
        self.assertEqual(observed, [b"TARE\n"])
