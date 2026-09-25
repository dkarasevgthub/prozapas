"""Device server for devices."""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .drivers.base import DeviceDriver
from .drivers.printer import PrinterDriver
from .drivers.scale import ScaleDriver
from .drivers.scanner import ScannerDriver
from .protocol import EVENT_DEVICE, EVENT_JOB, EVENT_SCAN, EVENT_WEIGHT
from .session import ClientSession
from .slot import DeviceSlot

logger = logging.getLogger(__name__)


class DeviceServer(QObject):
    """Named-pipe server managing sessions and slot event wiring."""

    client_connected = pyqtSignal()
    client_disconnected = pyqtSignal()
    shutdown_requested = pyqtSignal()

    def __init__(
        self,
        scanner_slot: DeviceSlot,
        scale_slot: DeviceSlot,
        printer_slot: DeviceSlot,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._sessions: list[ClientSession] = []
        self._scanner_slot = scanner_slot
        self._scale_slot = scale_slot
        self._printer_slot = printer_slot

        self._server.newConnection.connect(self._on_new_connection)
        self._wire_slots()

    @property
    def sessions(self) -> list[ClientSession]:
        return list(self._sessions)

    @property
    def has_clients(self) -> bool:
        return len(self._sessions) > 0

    def start(self, pipe_name: str) -> bool:
        if self._pipe_answers(pipe_name):
            logger.error("DeviceServer: pipe %s is already served by another instance", pipe_name)
            return False

        if self._server.listen(pipe_name):
            logger.info("DeviceServer: listening on %s", self._server.fullServerName())
            return True

        logger.warning("DeviceServer: removing stale pipe %s", pipe_name)
        QLocalServer.removeServer(pipe_name)
        ok = self._server.listen(pipe_name)
        if ok:
            logger.info("DeviceServer: listening on %s", self._server.fullServerName())
        else:
            logger.error("DeviceServer: failed to listen on %s – %s", pipe_name, self._server.serverError())
        return ok

    @staticmethod
    def _pipe_answers(pipe_name: str, timeout_ms: int = 300) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(pipe_name)
        alive = probe.waitForConnected(timeout_ms)
        probe.abort()
        return alive

    def stop(self) -> None:
        logger.info("DeviceServer: stopping")
        for session in self._sessions:
            self._close_session(session)
        self._sessions.clear()
        self._server.close()

    def broadcast(self, event_name: str, data: dict[str, Any]) -> None:
        """Send an event to every session subscribed to *event_name*."""
        for session in list(self._sessions):  # Iterate over copy
            session.send_event(event_name, data)

    # --- Slot wiring ---------------------------------------------------

    def _wire_slots(self) -> None:
        """Connect slot driver signals to broadcast slots."""
        scanner = self._scanner_slot
        if scanner:
            scanner._real.scanned.connect(self._on_scan)
            scanner._fake.scanned.connect(self._on_scan)
            scanner.state_changed.connect(self._on_slot_state_changed)

        scale = self._scale_slot
        if scale:
            scale._real.weight.connect(self._on_weight)
            scale._fake.weight.connect(self._on_weight)
            scale.state_changed.connect(self._on_slot_state_changed)

        printer = self._printer_slot
        if printer:
            printer._real.job_status_changed.connect(self._on_job)
            printer._fake.job_status_changed.connect(self._on_job)
            printer.state_changed.connect(self._on_slot_state_changed)

    # --- Event slots ---------------------------------------------------

    def _on_scan(self, code: str) -> None:
        """Broadcast a ``scan`` event from the scanner."""
        slot = self._scanner_slot
        if self.sender() != slot.active_driver():
            return
        self.broadcast(EVENT_SCAN, {"code": code, "device": slot.device_id, "id": slot.device_id})

    def _on_weight(self, value: float, unit: str, stable: bool) -> None:
        """Broadcast a ``weight`` event from the scale."""
        slot = self._scale_slot
        if self.sender() != slot.active_driver():
            return
        self.broadcast(EVENT_WEIGHT, {"value": value, "unit": unit, "stable": stable})

    def _on_job(self, job_id: str, state: str, error: str) -> None:
        """Broadcast a ``job`` event from the printer."""
        slot = self._printer_slot
        if self.sender() != slot.active_driver():
            return
        self.broadcast(EVENT_JOB, {"job": job_id, "state": state, "error": error})

    def _on_slot_state_changed(self, state: str, reason: str) -> None:
        """Broadcast a ``device`` event on a state change."""
        slot = self.sender()
        if isinstance(slot, DeviceSlot):
            # ВАЖНО: отправляем и 'id', и 'device' для обратной совместимости
            self.broadcast(
                EVENT_DEVICE,
                {"id": slot.device_id, "device": slot.device_id, "state": state, "reason": reason},
            )

    # --- Connection handling --------------------------------------------

    def _on_new_connection(self) -> None:
        socket: QLocalSocket | None = self._server.nextPendingConnection()
        if socket is None:
            return

        session = ClientSession(
            socket=socket,
            server=self,
            scanner_slot=self._scanner_slot,
            scale_slot=self._scale_slot,
            printer_slot=self._printer_slot,
            parent=self,
        )
        session.shutdown_requested.connect(self.shutdown_requested.emit)
        session.print_routed.connect(self._on_print_routed)
        self._sessions.append(session)
        logger.info("DeviceServer: client connected (total=%d)", len(self._sessions))
        self.client_connected.emit()

        socket.disconnected.connect(lambda: self._on_client_disconnected(session))

    def _on_print_routed(self, job_id: str, payload: str) -> None:
        """Route print job to the simulator session that owns the printer."""
        for session in self._sessions:
            if "printer" in session.owned_devices:
                session.send_event("print.job", {"job": job_id, "payload": payload})
                return

    def _on_client_disconnected(self, session: ClientSession) -> None:
        if session in self._sessions:
            self._sessions.remove(session)
            logger.info("DeviceServer: client disconnected (total=%d)", len(self._sessions))
        self._close_session(session)
        self.client_disconnected.emit()

    def _close_session(self, session: ClientSession) -> None:
        try:
            sock = session.socket
            if hasattr(sock, "close"):
                sock.close()
        except Exception:
            pass
        session.deleteLater()