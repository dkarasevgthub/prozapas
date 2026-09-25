"""Fake device drivers for testing and development without hardware.

Each fake driver immediately reports ``online`` and exposes methods to
inject events. They use the same typed signals as the real drivers so
the server does not need to distinguish them.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

from ..protocol import (
    STATE_OFFLINE,
    STATE_ONLINE,
    JOB_QUEUED,
    JOB_PRINTING,
    JOB_DONE,
)
from .base import DeviceDriver

logger = logging.getLogger(__name__)


class FakeScanner(DeviceDriver):
    """Fake barcode scanner.

    Signals
    -------
    scanned(str)
        Emitted by :meth:`emit_scan`.
    """

    scanned = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(device_id="scanner", auto_reconnect=False, parent=parent)

    def _do_open(self) -> None:
        self._set_state(STATE_ONLINE, "fake")

    def _do_close(self) -> None:
        self._set_state(STATE_OFFLINE, "closed")

    def emit_scan(self, code: str) -> None:
        """Emit a fake scan with the given barcode *code*."""
        code = code.strip().upper()
        if code:
            logger.info("FakeScanner: barcode=%s", code)
            self.scanned.emit(code)


class FakeScale(DeviceDriver):
    """Fake scale.

    Signals
    -------
    weight(float, str, bool)
        Emitted by :meth:`emit_weight`.
    """

    weight = pyqtSignal(float, str, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(device_id="scale", auto_reconnect=False, parent=parent)
        self._last: tuple[float, str, bool] | None = None

    def _do_open(self) -> None:
        self._set_state(STATE_ONLINE, "fake")

    def _do_close(self) -> None:
        self._last = None
        self._set_state(STATE_OFFLINE, "closed")

    def last_reading(self) -> tuple[float, str, bool] | None:
        """Последнее показание — как у настоящего драйвера, для новых подписчиков."""
        return self._last

    def emit_weight(self, value: float, unit: str = "g", stable: bool = True) -> None:
        """Emit a fake weight reading."""
        logger.info("FakeScale: value=%.2f %s stable=%s", value, unit, stable)
        self._last = (value, unit, stable)
        self.weight.emit(value, unit, stable)


class FakePrinter(DeviceDriver):
    """Fake printer that simulates queued → printing → done."""

    job_status_changed = pyqtSignal(str, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(device_id="printer", auto_reconnect=False, parent=parent)
        self._jobs: dict[str, dict] = {}

    def _do_open(self) -> None:
        self._set_state(STATE_ONLINE, "fake")

    def _do_close(self) -> None:
        self._set_state(STATE_OFFLINE, "closed")

    def submit(
        self, key: str, fmt: str, payload: str, copies: int = 1
    ) -> tuple[str, str]:
        """Queue a fake job and immediately finish it."""
        job_id = f"j-{len(self._jobs) + 1}"
        self._jobs[job_id] = {"key": key, "format": fmt, "copies": copies}
        self.job_status_changed.emit(job_id, JOB_QUEUED, "")
        self.job_status_changed.emit(job_id, JOB_PRINTING, "")
        self.job_status_changed.emit(job_id, JOB_DONE, "")
        return job_id, JOB_DONE

    def get_status(self, job_id: str) -> str | None:
        return JOB_DONE if job_id in self._jobs else None

    def get_queue(self) -> list[dict]:
        return [{"job": jid, "state": JOB_DONE} for jid in self._jobs]

    def retry(self, job_id: str) -> str | None:
        return JOB_DONE if job_id in self._jobs else None