"""Application ownership policy verified against separate real processes."""
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

from app import devices
from app.service_host import EXTERNAL, OWN, ServiceHost
from wire_client import ServiceProcess, WireClient, qt_app, wait_for


class ServiceLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipe = "prozapas-host-test-" + uuid.uuid4().hex
        for context in (patch.object(devices, "PIPE_NAME", self.pipe),
                        patch.dict(os.environ, {"PROGRAMDATA": self.directory.name,
                                               "PROZAPAS_SIMULATOR": "true"})):
            context.start()
            self.addCleanup(context.stop)
        self.host = ServiceHost()
        self.addCleanup(self.host.stop)

    def test_starts_and_stops_owned_service_using_normal_application_host(self):
        self.assertEqual(self.host.start(), OWN)
        self.assertTrue(self.host.owned)
        process = self.host._process
        client = WireClient(self.pipe)
        self.assertTrue(client.request("hello")["ok"])
        client.close()
        self.host.stop()
        self.assertEqual(process.exitCode(), 0)
        self.assertFalse(devices.pipe_answers())

    def test_leaves_preexisting_external_service_running(self):
        service = ServiceProcess(pipe=self.pipe)
        self.addCleanup(service.close)
        self.assertEqual(self.host.start(), EXTERNAL)
        self.assertFalse(self.host.owned)
        self.host.stop()
        self.assertIsNone(service.process.poll())
        self.assertTrue(service.client().request("hello")["ok"])

    def test_restarts_owned_service_after_unexpected_exit(self):
        self.assertEqual(self.host.start(), OWN)
        client = WireClient(self.pipe)
        client.request("shutdown")
        client.close()
        self.assertTrue(self.host._process.waitForFinished(5000))
        self.assertTrue(self.host.ensure_running())
        self.assertTrue(self.host.owned)
        self.assertTrue(devices.pipe_answers())
