"""Test the built executable through an external wire client, without self-check."""
import os
import unittest

from wire_client import ServiceProcess, qt_app, wait_for


@unittest.skipUnless(os.environ.get("PROZAPAS_TEST_EXE"), "Frozen executable tests run after CI build")
class FrozenExecutableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.service = ServiceProcess(simulated=False, executable=os.environ["PROZAPAS_TEST_EXE"])
        self.addCleanup(self.service.close)
        self.client = self.service.client()

    def test_executable_boots_and_exposes_the_expected_protocol(self):
        reply = self.client.request("hello", protocol=1)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["service"], "devices")
        self.assertEqual(self.client.request("devices")["devices"]["printer"], "online")

    def test_packaged_drivers_send_scans_weights_and_print_bytes(self):
        self.assertTrue(self.client.request("attach", devices=["scanner", "scale"])["ok"])
        observer = self.service.client()
        observer.request("subscribe", events=["scan", "weight"])
        self.client.request("emit", event="scan", code="EXE-123")
        observer.event("scan", code="EXE-123")
        self.client.request("emit", event="weight", value=500, stable=True)
        observer.event("weight", value=500, unit="g")
        job = observer.request("print", key="exe-label", format="zpl", payload="^XA^FD123^FS^XZ")["job"]
        wait_for(lambda: observer.request("print.status", job=job).get("state") == "done")
        self.assertEqual((self.service.root / "printed" / f"print_{job}.zpl").read_bytes(), b"^XA^FD123^FS^XZ")
