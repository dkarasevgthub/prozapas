"""External test probe: JSON/IPC only, no imports from the device service."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from PyQt6.QtNetwork import QLocalSocket
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

DESKTOP = Path(__file__).resolve().parents[1]


def qt_app():
    return QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=5, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError(f"Timed out waiting for {message}")


class WireClient:
    def __init__(self, name):
        self.socket = QLocalSocket()
        self.socket.connectToServer(name)
        if not self.socket.waitForConnected(1000):
            raise ConnectionError(self.socket.errorString())
        self.frames = []
        self.buffer = b""
        self.sequence = 0

    def close(self):
        self.socket.abort()
        QApplication.processEvents()

    def write(self, data):
        self.socket.write(data)
        self.socket.flush()

    def drain(self):
        self.buffer += bytes(self.socket.readAll())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            self.frames.append(json.loads(line))

    def take(self, predicate, timeout=5):
        found = []

        def poll():
            self.drain()
            for index, frame in enumerate(self.frames):
                if predicate(frame):
                    found.append(self.frames.pop(index))
                    return True
            return False

        wait_for(poll, timeout, "wire response/event")
        return found[0]

    def request(self, cmd, **fields):
        self.sequence += 1
        identifier = self.sequence
        self.write((json.dumps({"id": identifier, "cmd": cmd, **fields}) + "\n").encode())
        return self.take(lambda frame: frame.get("id") == identifier and "ok" in frame)

    def event(self, name, **fields):
        return self.take(lambda frame: frame.get("event") == name
                         and all(frame.get(key) == value for key, value in fields.items()))


class ServiceProcess:
    def __init__(self, *, simulated=True, extra_env=None, pipe=None, idle=0):
        self.directory = tempfile.TemporaryDirectory(prefix="prozapas-devices-test-")
        self.root = Path(self.directory.name)
        self.pipe = pipe or "prozapas-test-" + uuid.uuid4().hex
        self.log = (self.root / "service.log").open("w", encoding="utf-8")
        self.environment = {**os.environ, "PROZAPAS_PIPE_NAME": self.pipe,
                            "PROGRAMDATA": str(self.root), "XDG_DATA_HOME": str(self.root),
                            "PROZAPAS_IDLE_TIMEOUT": str(idle),
                            "PROZAPAS_SCANNER_PORT": "COM99999" if os.name == "nt" else "/dev/prozapas-missing-scanner",
                            "PROZAPAS_SCALE_PORT": "COM99998" if os.name == "nt" else "/dev/prozapas-missing-scale",
                            "PROZAPAS_PRINTER_NAME": "prozapas-missing-printer",
                            "PROZAPAS_PRINTER_OUTPUT_FILE": str(self.root / "printed") if not simulated else "",
                            **(extra_env or {})}
        arguments = [sys.executable, "-m", "devices", "--idle-timeout", str(idle)]
        if simulated:
            arguments.append("--simulator-only")
        self.process = subprocess.Popen(arguments, cwd=DESKTOP, env=self.environment,
                                        stdout=self.log, stderr=self.log,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.clients = []
        try:
            wait_for(self._ready, message="separate device service startup")
        except Exception:
            self.log.flush()
            diagnostic = (self.root / "service.log").read_text(encoding="utf-8")[-4000:]
            self.close()
            raise AssertionError(diagnostic)

    def _ready(self):
        if self.process.poll() is not None:
            raise AssertionError(f"Service exited: {self.process.returncode}")
        probe = QLocalSocket()
        probe.connectToServer(self.pipe)
        ready = probe.waitForConnected(40)
        probe.abort()
        return ready

    def client(self):
        client = WireClient(self.pipe)
        self.clients.append(client)
        return client

    def close(self):
        if self.process.poll() is None:
            try:
                shutdown = WireClient(self.pipe)
                shutdown.request("shutdown")
                shutdown.close()
                self.process.wait(timeout=5)
            except (ConnectionError, AssertionError, subprocess.TimeoutExpired):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                                   capture_output=True)
                else:
                    self.process.kill()
                self.process.wait(timeout=5)
        for client in self.clients:
            client.close()
        self.log.close()
        self.directory.cleanup()
