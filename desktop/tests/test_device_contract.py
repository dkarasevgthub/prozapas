"""Assess the real service process from outside its code and its self-check."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from PyQt6.QtTest import QTest

from wire_client import DESKTOP, ServiceProcess, qt_app, wait_for


class DeviceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.service = ServiceProcess()
        self.addCleanup(self.service.close)
        self.client = self.service.client()

    def ok(self, client, command, **fields):
        result = client.request(command, **fields)
        self.assertTrue(result["ok"], result)
        return result

    def test_protocol_negotiation_and_unknown_commands(self):
        hello = self.ok(self.client, "hello", protocol=1)
        self.assertEqual(hello["service"], "devices")
        self.assertIn(1, hello["protocol"])
        for cmd, fields, code in [("hello", {"protocol": 999}, "incompatible"),
                                  ("unknown", {}, "unknown_command"),
                                  ("subscribe", {"events": "scan"}, "bad_request"),
                                  ("", {}, "bad_request")]:
            with self.subTest(command=cmd):
                result = self.client.request(cmd, **fields)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], code)
        self.ok(self.client, "devices")

    def test_fragmented_batched_and_malformed_frames_keep_connection_usable(self):
        self.client.write(b'not-json\n[]\n{"id":81,"cmd":"hel')
        QTest.qWait(30)
        self.client.write(b'lo","protocol":1}\n{"id":82,"cmd":"devices"}\r\n')
        for identifier in (81, 82):
            self.assertTrue(self.client.take(lambda f: f.get("id") == identifier)["ok"])
        self.ok(self.client, "hello")

    def test_offline_devices_and_unowned_injection(self):
        self.assertEqual(self.ok(self.client, "devices")["devices"],
                         dict.fromkeys(("scanner", "scale", "printer"), "offline"))
        result = self.client.request("emit", event="scan", code="UNOWNED")
        self.assertEqual(result["error"]["code"], "not_attached")
        result = self.client.request("scale.read", timeout_ms=50)
        self.assertFalse(result["ok"])

    def test_device_ownership_detach_and_disconnect(self):
        owner = self.service.client()
        self.ok(owner, "attach", devices=["scanner", "scale", "printer"])
        self.assertTrue(all(state == "online" for state in self.ok(self.client, "devices")["devices"].values()))
        self.assertEqual(self.client.request("attach", devices=["scanner"])["error"]["code"], "busy")
        for device in ("scanner", "scale", "printer"):
            self.ok(owner, "detach", devices=[device])
            self.assertEqual(self.ok(self.client, "devices")["devices"][device], "offline")
            self.ok(owner, "attach", devices=[device])
        owner.close()
        wait_for(lambda: all(state == "offline" for state in self.ok(self.client, "devices")["devices"].values()),
                 message="released devices after owner disconnect")
        self.ok(self.client, "attach", devices=["scanner"])

    def test_scan_subscription_filtering_and_unsubscribe(self):
        owner = self.service.client()
        observer = self.service.client()
        self.ok(owner, "attach", devices=["scanner"])
        self.ok(self.client, "subscribe", events=["scan"])
        self.ok(owner, "emit", event="scan", code="КОД-123")
        self.assertEqual(self.client.event("scan")["code"], "КОД-123")
        observer.request("hello")
        observer.drain()
        self.assertFalse(any(f.get("event") == "scan" for f in observer.frames))
        self.ok(self.client, "unsubscribe", events=["scan"])
        self.ok(owner, "emit", event="scan", code="SECOND")
        self.client.request("hello")
        self.client.drain()
        self.assertFalse(any(f.get("event") == "scan" for f in self.client.frames))

    def test_weights_zero_stability_cached_subscription_and_read(self):
        owner = self.service.client()
        self.ok(owner, "attach", devices=["scale"])
        self.ok(self.client, "subscribe", events=["weight"])
        for grams, stable in ((500, False), (500, True), (0, True), (1234.5, True)):
            self.ok(owner, "emit", event="weight", value=grams, stable=stable)
            self.client.event("weight", value=grams, stable=stable, unit="g")
        late = self.service.client()
        self.ok(late, "subscribe", events=["weight"])
        late.event("weight", value=1234.5, stable=True)
        # The simulator exposes the weight stream; synchronous scale.read is
        # tested through the real serial driver in the Linux suite.

    def test_simulated_print_payload_and_external_ack(self):
        owner = self.service.client()
        self.ok(owner, "attach", devices=["printer"])
        self.ok(owner, "subscribe", events=["print.job"])
        self.ok(self.client, "subscribe", events=["job"])
        reply = self.ok(self.client, "print", key="label-1", format="zpl", payload="^XA^FD123^FS^XZ", copies=1)
        frame = owner.event("print.job", job=reply["job"])
        self.assertEqual(frame["payload"], "^XA^FD123^FS^XZ")
        self.ok(owner, "emit", event="job", job=reply["job"], state="done")
        self.client.event("job", job=reply["job"], state="done", error="")

    def test_second_instance_cannot_replace_live_service(self):
        process = subprocess.run([sys.executable, "-m", "devices", "--simulator-only"],
                                 cwd=DESKTOP, env=self.service.environment,
                                 capture_output=True, timeout=10,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(process.returncode, 2)
        self.ok(self.client, "hello")

    def test_shutdown_and_reconnect_to_restarted_service(self):
        self.ok(self.client, "shutdown")
        self.service.process.wait(timeout=5)
        self.assertEqual(self.service.process.returncode, 0)
        restarted = ServiceProcess(pipe=self.service.pipe)
        self.addCleanup(restarted.close)
        self.ok(restarted.client(), "hello")


class PrinterTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.service = ServiceProcess(simulated=False)
        self.addCleanup(self.service.close)
        self.client = self.service.client()

    def test_queue_copies_payload_idempotency_and_independent_output(self):
        payload = "^XA^FDЭтикетка-123^FS^XZ"
        first = self.client.request("print", key="same-label", format="zpl", payload=payload, copies=2)
        self.assertTrue(first["ok"], first)
        second = self.service.client().request("print", key="same-label", format="zpl", payload=payload, copies=2)
        self.assertEqual(first["job"], second["job"])
        wait_for(lambda: self.client.request("print.status", job=first["job"]).get("state") == "done")
        printed = list((self.service.root / "printed").glob("*.zpl"))
        self.assertEqual(len(printed), 1)
        self.assertEqual(printed[0].read_bytes(), payload.encode("cp866") * 2)
        queue = self.client.request("print.queue")["jobs"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["state"], "done")

    def test_bad_format_copies_and_unknown_job_are_rejected(self):
        for command, arguments in (("print", {"format": "pdf", "payload": "x"}),
                                   ("print", {"format": "zpl", "copies": "bad"}),
                                   ("print.status", {"job": "missing"}),
                                   ("print.retry", {"job": "missing"})):
            with self.subTest(command=command, arguments=arguments):
                reply = self.client.request(command, **arguments)
                self.assertFalse(reply["ok"])
                self.assertEqual(reply["error"]["code"], "bad_request")


if __name__ == "__main__":
    unittest.main()
