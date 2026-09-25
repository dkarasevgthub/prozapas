"""Label printer driver for devices.

A print job is **always accepted** — even with the printer offline —
and placed on a queue (status ``queued``). A dedicated worker thread
drains the queue one job at a time, because talking to the spooler
(``win32print``) is blocking and must not stall the Qt event loop.

Features
--------
* **Idempotency**: a second :meth:`submit` with the same ``key`` returns
  the existing job instead of creating a duplicate.
* **Retries**: a failed print is retried with pauses of 2, 5, 10
  seconds; after three failures the job is ``failed`` (and can be
  re-queued with :meth:`retry`).
* **Two transports**: a file-writing *stub* (default, for development
  and tests) and real ``win32print`` RAW printing for production.
* A :pyattr:`job_status_changed` signal fires on every state change.
"""

from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from ..config import list_printers
from ..protocol import (
    JOB_DONE,
    JOB_FAILED,
    JOB_PRINTING,
    JOB_QUEUED,
    STATE_OFFLINE,
    STATE_ONLINE,
)
from .base import DeviceDriver

logger = logging.getLogger(__name__)

#: Pause (seconds) before each retry attempt.
DEFAULT_RETRY_DELAYS: list[int] = [2, 5, 10]


@dataclass
class Job:
    """A single print job."""

    job_id: str
    key: str
    format: str
    payload: str
    copies: int = 1
    state: str = JOB_QUEUED
    error_message: str = ""
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the flat dict used in protocol responses."""
        return {
            "job": self.job_id,
            "key": self.key,
            "format": self.format,
            "copies": self.copies,
            "state": self.state,
            "error": self.error_message,
            "retries": self.retry_count,
        }


class PrinterDriver(DeviceDriver):
    """Driver for a label printer with a job queue.

    Signals
    -------
    job_status_changed(str, str, str)
        ``(job_id, state, error_message)`` — emitted on every job state
        transition, including retry attempts.
    """

    job_status_changed = pyqtSignal(str, str, str)

    def __init__(
        self,
        name: str = "",
        encoding: str = "cp1251",
        output_file: str = "",
        retry_delays: list[int] | None = None,
        stub: bool | None = None,
        parent: QObject | None = None,
    ) -> None:
        # Включаем авто-переподключение, чтобы драйвер периодически искал принтер
        super().__init__(device_id="printer", auto_reconnect=True, parent=parent)
        self._printer_name: str = name or ""
        self._encoding: str = encoding
        self._output_file: str = output_file or ""

        # ``stub`` forces the file-writing path. By default we stub when
        # an output file is configured.
        self._stub: bool = stub if stub is not None else bool(self._output_file)
        self._retry_delays: list[int] = (
            list(retry_delays) if retry_delays is not None else list(DEFAULT_RETRY_DELAYS)
        )

        self._lock = threading.RLock()
        self._jobs: list[Job] = []
        self._by_key: dict[str, Job] = {}
        self._job_counter: int = 0

        self._process_queue: queue.Queue[Job | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None

    # --- Lifecycle -------------------------------------------------------

    def _do_open(self) -> None:
        """Open the printer or search for one if not configured."""
        # Если задан output_file, мы работаем как stub (всегда online)
        if self._stub:
            logger.info("PrinterDriver: opening in stub mode (output_file=%s)", self._output_file)
            self._start_worker()
            self._set_state(STATE_ONLINE, "ready")
            return

        # Если принтер не задан в конфиге, пытаемся найти первый попавшийся
        if not self._printer_name:
            printers = list_printers()
            if printers:
                self._printer_name = printers[0]
                logger.info("PrinterDriver: auto-found printer %s", self._printer_name)
            else:
                logger.debug("PrinterDriver: no printer found, searching...")
                self._set_state(STATE_OFFLINE, "searching")
                return

        # Проверяем, доступен ли заданный (или найденный) принтер в системе
        printers = list_printers()
        if self._printer_name not in printers:
            logger.warning("PrinterDriver: printer '%s' not found in system", self._printer_name)
            # Сбрасываем имя, чтобы в следующий раз снова сработал автопоиск
            self._printer_name = ""
            self._set_state(STATE_OFFLINE, "searching")
            return

        logger.info("PrinterDriver: opening %s", self._printer_name)
        self._start_worker()
        self._set_state(STATE_ONLINE, "ready")

    def _do_close(self) -> None:
        """Stop the worker, finishing the in-flight job if possible."""
        self._stop_worker()

        with self._lock:
            remaining = [j for j in self._jobs if j.state in (JOB_QUEUED, JOB_PRINTING)]
        if remaining:
            for job in remaining:
                logger.warning("PrinterDriver: unfinished job on close: %s", job.to_dict())

        self._set_state(STATE_OFFLINE, "closed")

    # --- Worker thread ---------------------------------------------------

    def _start_worker(self) -> None:
        """Create and start the processing thread if not running."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._worker_loop, name="printer-worker", daemon=True
        )
        self._worker.start()

    def _stop_worker(self) -> None:
        """Signal the worker to stop and wait briefly for it."""
        self._stop_event.set()
        self._process_queue.put(None)  # wake the worker
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=5)
        self._worker = None

    def _worker_loop(self) -> None:
        """Pull jobs from the queue and print them sequentially."""
        while not self._stop_event.is_set():
            try:
                job = self._process_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                self._print_job(job)
            except Exception:
                logger.error("PrinterDriver: worker crashed on job %s", job.job_id, exc_info=True)
                self._set_job_state(job, JOB_FAILED, "internal error")

    # --- Job processing --------------------------------------------------

    def _print_job(self, job: Job) -> None:
        """Attempt to print *job*, retrying on failure."""
        self._set_job_state(job, JOB_PRINTING)

        attempt = 0
        while True:
            if self._stop_event.is_set():
                return
            try:
                self._do_print(job)
                job.error_message = ""
                self._set_job_state(job, JOB_DONE)
                logger.info("PrinterDriver: job %s done", job.job_id)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PrinterDriver: job %s failed (attempt %d): %s",
                    job.job_id,
                    attempt + 1,
                    exc,
                )
                if attempt < len(self._retry_delays):
                    delay = self._retry_delays[attempt]
                    job.retry_count = attempt + 1
                    job.error_message = str(exc)
                    self._set_job_state(job, JOB_QUEUED, str(exc))
                    if self._stop_event.wait(delay):
                        return
                    self._set_job_state(job, JOB_PRINTING, "")
                    attempt += 1
                    continue
                job.retry_count = attempt + 1
                job.error_message = str(exc)
                self._set_job_state(job, JOB_FAILED, str(exc))
                logger.error("PrinterDriver: job %s failed permanently", job.job_id)
                return

    def _do_print(self, job: Job) -> None:
        """Send one job's payload to the chosen transport."""
        if self._stub:
            self._print_to_file(job)
        else:
            self._print_via_win32(job)

    def _print_to_file(self, job: Job) -> None:
        """Stub transport: write the payload to a temp file."""
        tmp_dir = self._output_file or tempfile.gettempdir()
        os.makedirs(tmp_dir, exist_ok=True)
        path = os.path.join(tmp_dir, f"print_{job.job_id}.{job.format}")
        data = job.payload.encode(self._encoding, errors="replace")
        with open(path, "wb") as fh:
            fh.writelines(data for _ in range(max(1, job.copies)))
        logger.debug("PrinterDriver: wrote job %s -> %s", job.job_id, path)

    def _print_via_win32(self, job: Job) -> None:
        """Real transport: send RAW data through ``win32print``."""
        import win32print  # type: ignore[import-untyped]

        data = job.payload.encode(self._encoding, errors="replace") * max(1, job.copies)
        handle = win32print.OpenPrinter(self._printer_name)
        try:
            job_info = win32print.StartDocPrinter(
                handle, 1, ("devices " + job.job_id, None, "RAW")
            )
            try:
                win32print.StartPagePrinter(handle)
                try:
                    win32print.WritePrinter(handle, data)
                finally:
                    win32print.EndPagePrinter(handle)
            finally:
                win32print.EndDocPrinter(handle)
        finally:
            win32print.ClosePrinter(handle)

    # --- State helpers ---------------------------------------------------

    def _set_job_state(self, job: Job, state: str, error: str = "") -> None:
        """Update a job's state and emit :pyattr:`job_status_changed`."""
        if state == job.state and error == job.error_message and state != JOB_QUEUED:
            return
        job.state = state
        if state != JOB_QUEUED or error:
            job.error_message = error
        logger.info(
            "PrinterDriver: job %s -> %s%s",
            job.job_id,
            state,
            f" ({error})" if error else "",
        )
        self.job_status_changed.emit(job.job_id, state, job.error_message)

    def _next_job_id(self) -> str:
        """Return a unique job id ``j-<n>``."""
        with self._lock:
            self._job_counter += 1
            return f"j-{self._job_counter}"

    # --- Public API ------------------------------------------------------

    def submit(
        self, key: str, fmt: str, payload: str, copies: int = 1
    ) -> tuple[str, str]:
        """Queue a print job."""
        key = key or ""
        with self._lock:
            existing = self._by_key.get(key) if key else None
            if existing is not None:
                logger.info("PrinterDriver: idempotent hit for key=%s", key)
                return existing.job_id, existing.state

            job = Job(
                job_id=self._next_job_id(),
                key=key,
                format=fmt,
                payload=payload,
                copies=max(1, int(copies)),
            )
            self._jobs.append(job)
            if key:
                self._by_key[key] = job

        logger.info("PrinterDriver: job %s queued (key=%s)", job.job_id, key)
        self._process_queue.put(job)
        return job.job_id, job.state

    def get_status(self, job_id: str) -> str | None:
        """Return the state of *job_id*, or ``None`` if unknown."""
        with self._lock:
            for job in self._jobs:
                if job.job_id == job_id:
                    return job.state
        return None

    def get_queue(self) -> list[dict[str, Any]]:
        """Return all jobs as a list of flat dicts (newest last)."""
        with self._lock:
            return [job.to_dict() for job in self._jobs]

    def retry(self, job_id: str) -> str | None:
        """Re-queue a ``failed`` job. Returns its new state or ``None``."""
        with self._lock:
            job = None
            for j in self._jobs:
                if j.job_id == job_id:
                    job = j
                    break
            if job is None:
                return None
            if job.state != JOB_FAILED:
                return job.state
            job.retry_count = 0
            job.error_message = ""

        logger.info("PrinterDriver: retrying job %s", job_id)
        self._set_job_state(job, JOB_QUEUED)
        self._process_queue.put(job)
        return job.state

    # --- Test/debug helpers ---------------------------------------------

    @property
    def stub(self) -> bool:
        """Whether the driver uses the file-writing transport."""
        return self._stub

    def set_state_for_test(self, state: str, reason: str = "") -> None:
        """Force a device state (used by the debug console / tests)."""
        self._set_state(state, reason)