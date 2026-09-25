"""Client session handling for devices."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .drivers.printer import PrinterDriver
from .drivers.scale import ScaleDriver, ScaleTimeoutError
from .protocol import (
    ALL_EVENTS,
    CMD_ATTACH,
    CMD_DETACH,
    CMD_DEVICES,
    CMD_EMIT,
    CMD_HELLO,
    CMD_PRINT,
    CMD_PRINT_QUEUE,
    CMD_PRINT_RETRY,
    CMD_PRINT_STATUS,
    CMD_SCALE_READ,
    CMD_SCALE_TARE,
    CMD_SHUTDOWN,
    CMD_SUBSCRIBE,
    CMD_UNSUBSCRIBE,
    DEVICE_STATES,
    ERR_BAD_REQUEST,
    ERR_BUSY,
    ERR_INCOMPATIBLE,
    ERR_INTERNAL,
    ERR_NO_DEVICE,
    ERR_NOT_ATTACHED,
    ERR_PRINTER_OFFLINE,
    ERR_SCALE_TIMEOUT,
    ERR_UNKNOWN_COMMAND,
    EVENT_JOB,
    EVENT_WEIGHT,
    JOB_DONE,
    JOB_QUEUED,
    PRINT_FORMATS,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    make_error,
    make_event,
    make_response,
    parse_frame,
)
from .slot import DeviceSlot

logger = logging.getLogger(__name__)


class ClientSession(QObject):
    """One connected client: framing, dispatch, subscription filtering."""

    shutdown_requested = pyqtSignal()
    print_routed = pyqtSignal(str, str)  # job_id, payload

    def __init__(
        self,
        socket: Any,
        server: Any,
        scanner_slot: DeviceSlot,
        scale_slot: DeviceSlot,
        printer_slot: DeviceSlot,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._socket = socket
        self._server = server
        self._scanner_slot = scanner_slot
        self._scale_slot = scale_slot
        self._printer_slot = printer_slot
        self._subscriptions: set[str] = set()
        self._buffer = bytearray()
        self._owned_devices: set[str] = set()
        self._pending_jobs: set[str] = set()

        self._connect_socket()

    @property
    def socket(self) -> Any:
        return self._socket

    @property
    def subscriptions(self) -> set[str]:
        return set(self._subscriptions)

    @property
    def owned_devices(self) -> set[str]:
        return set(self._owned_devices)

    def _connect_socket(self) -> None:
        try:
            self._socket.readyRead.connect(self._on_ready_read)
        except AttributeError:
            logger.warning("Socket has no readyRead signal")
        try:
            self._socket.disconnected.connect(self._on_disconnected)
        except AttributeError:
            pass

    # --- Inbound framing -------------------------------------------------

    def _on_ready_read(self) -> None:
        try:
            data = bytes(self._socket.readAll())
        except Exception:
            logger.error("Session: error reading socket", exc_info=True)
            return
        self._buffer.extend(data)
        while True:
            idx_lf = self._buffer.find(0x0A)
            idx_cr = self._buffer.find(0x0D)
            candidates = [i for i in (idx_lf, idx_cr) if i >= 0]
            if not candidates:
                break
            idx = min(candidates)
            line = bytes(self._buffer[:idx])
            del self._buffer[: idx + 1]
            self._dispatch_line(line)

    def _dispatch_line(self, line: bytes) -> None:
        request = parse_frame(line)
        if request is None:
            logger.debug("Session: ignored malformed frame: %r", line)
            return
        req_id = request.get("id", 0)
        cmd = request.get("cmd", "")
        logger.debug("Session: <- %s", request)
        if not cmd:
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, "missing 'cmd'"))
            return
        self.handle_request(request)

    def handle_request(self, request: dict[str, Any]) -> None:
        req_id = request.get("id", 0)
        cmd = request.get("cmd", "")
        try:
            handler = self._handlers().get(cmd)
            if handler is None:
                self._reply(
                    req_id,
                    make_error(req_id, ERR_UNKNOWN_COMMAND, f"unknown command: {cmd}"),
                )
                return
            handler(req_id, request)
        except ScaleTimeoutError:
            self._reply(
                req_id, make_error(req_id, ERR_SCALE_TIMEOUT, "scale did not stabilise")
            )
        except Exception as exc:
            logger.error("Session: handler for %r failed", cmd, exc_info=True)
            self._reply(req_id, make_error(req_id, ERR_INTERNAL, str(exc)))

    # --- Command handlers ------------------------------------------------

    def _handlers(self) -> dict[str, Any]:
        return {
            CMD_HELLO: self._cmd_hello,
            CMD_DEVICES: self._cmd_devices,
            CMD_SUBSCRIBE: self._cmd_subscribe,
            CMD_UNSUBSCRIBE: self._cmd_unsubscribe,
            CMD_PRINT: self._cmd_print,
            CMD_PRINT_STATUS: self._cmd_print_status,
            CMD_PRINT_QUEUE: self._cmd_print_queue,
            CMD_PRINT_RETRY: self._cmd_print_retry,
            CMD_SCALE_READ: self._cmd_scale_read,
            CMD_SCALE_TARE: self._cmd_scale_tare,
            CMD_SHUTDOWN: self._cmd_shutdown,
            CMD_ATTACH: self._cmd_attach,
            CMD_DETACH: self._cmd_detach,
            CMD_EMIT: self._cmd_emit,
        }

    def _cmd_hello(self, req_id: Any, request: dict[str, Any]) -> None:
        client_proto = request.get("protocol", PROTOCOL_VERSION)
        if client_proto not in SUPPORTED_PROTOCOL_VERSIONS:
            self._reply(
                req_id,
                make_error(req_id, ERR_INCOMPATIBLE, f"protocol {client_proto} not supported"),
            )
            return
        self._reply(req_id, make_response(req_id, True, service="devices", protocol=SUPPORTED_PROTOCOL_VERSIONS))

    def _cmd_devices(self, req_id: Any, request: dict[str, Any]) -> None:
        states = {
            "scanner": self._scanner_slot.active_driver().state,
            "scale": self._scale_slot.active_driver().state,
            "printer": self._printer_slot.active_driver().state,
        }
        self._reply(req_id, make_response(req_id, True, devices=states))

    def _cmd_subscribe(self, req_id: Any, request: dict[str, Any]) -> None:
        events = request.get("events", []) or []
        if not isinstance(events, list):
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, "'events' must be a list"))
            return
        fresh_weight = EVENT_WEIGHT in events and EVENT_WEIGHT not in self._subscriptions
        for evt in events:
            if evt in ALL_EVENTS:
                self._subscriptions.add(evt)
        self._reply(req_id, make_response(req_id, True, subscribed=sorted(self._subscriptions)))
        if fresh_weight:
            self._push_current_weight()

    def _push_current_weight(self) -> None:
        """Отдать последнее показание сразу после подписки.

        Драйвер шлёт событие только при изменении, поэтому на неподвижных весах
        новый подписчик не услышал бы ничего и показывал бы прочерк вместо нуля.
        """
        scale = self._scale_slot.active_driver()
        reading = getattr(scale, "last_reading", lambda: None)()
        if reading is None:
            return
        value, unit, stable = reading
        self.send_event(EVENT_WEIGHT, {"value": value, "unit": unit, "stable": stable})

    def _cmd_unsubscribe(self, req_id: Any, request: dict[str, Any]) -> None:
        events = request.get("events", []) or []
        if isinstance(events, list):
            for evt in events:
                self._subscriptions.discard(evt)
        self._reply(req_id, make_response(req_id, True, subscribed=sorted(self._subscriptions)))

    def _cmd_print(self, req_id: Any, request: dict[str, Any]) -> None:
        printer_slot = self._printer_slot
        if printer_slot.is_simulated():
            # Reverse flow: route to simulator
            try:
                payload = str(request.get("payload", ""))
            except (TypeError, ValueError):
                self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, "invalid print arguments"))
                return
            job_id = f"j-{uuid.uuid4().hex[:8]}"
            self._reply(req_id, make_response(req_id, True, job=job_id, state=JOB_QUEUED))
            self._pending_jobs.add(job_id)
            self.print_routed.emit(job_id, payload)
            QTimer.singleShot(2000, lambda: self._auto_ack_job(job_id))
            return

        printer = printer_slot.active_driver()
        if not isinstance(printer, PrinterDriver):
            self._reply(req_id, make_error(req_id, ERR_PRINTER_OFFLINE, "no printer driver"))
            return
        try:
            key = str(request.get("key", ""))
            fmt = str(request.get("format", ""))
            payload = str(request.get("payload", ""))
            copies = int(request.get("copies", 1))
        except (TypeError, ValueError):
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, "invalid print arguments"))
            return
        if fmt not in PRINT_FORMATS:
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, f"unknown format: {fmt}"))
            return
        job_id, state = printer.submit(key, fmt, payload, copies)
        self._reply(req_id, make_response(req_id, True, job=job_id, state=state))

    def _auto_ack_job(self, job_id: str) -> None:
        if job_id in self._pending_jobs:
            self._pending_jobs.discard(job_id)
            self._server.broadcast(EVENT_JOB, {"job": job_id, "state": JOB_DONE, "error": "auto-ack"})

    def _cmd_print_status(self, req_id: Any, request: dict[str, Any]) -> None:
        printer = self._printer_slot.active_driver()
        if not isinstance(printer, PrinterDriver):
            self._reply(req_id, make_error(req_id, ERR_NO_DEVICE, "no printer driver"))
            return
        job_id = str(request.get("job", ""))
        if not job_id:
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, "missing job id"))
            return
        state = printer.get_status(job_id)
        if state is None:
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, f"unknown job: {job_id}"))
            return
        self._reply(req_id, make_response(req_id, True, job=job_id, state=state))

    def _cmd_print_queue(self, req_id: Any, request: dict[str, Any]) -> None:
        printer = self._printer_slot.active_driver()
        if not isinstance(printer, PrinterDriver):
            self._reply(req_id, make_error(req_id, ERR_NO_DEVICE, "no printer driver"))
            return
        jobs = printer.get_queue()
        self._reply(req_id, make_response(req_id, True, jobs=jobs))

    def _cmd_print_retry(self, req_id: Any, request: dict[str, Any]) -> None:
        printer = self._printer_slot.active_driver()
        if not isinstance(printer, PrinterDriver):
            self._reply(req_id, make_error(req_id, ERR_NO_DEVICE, "no printer driver"))
            return
        job_id = str(request.get("job", ""))
        if not job_id:
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, "missing job id"))
            return
        state = printer.retry(job_id)
        if state is None:
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, f"unknown job: {job_id}"))
            return
        self._reply(req_id, make_response(req_id, True, job=job_id, state=state))

    def _cmd_scale_read(self, req_id: Any, request: dict[str, Any]) -> None:
        scale = self._scale_slot.active_driver()
        if not isinstance(scale, ScaleDriver):
            self._reply(req_id, make_error(req_id, ERR_NO_DEVICE, "no scale driver"))
            return
        timeout_ms = int(request.get("timeout_ms", 3000))
        want_stable = bool(request.get("stable", True))
        try:
            value = scale.read_stable(timeout_ms)
        except ScaleTimeoutError:
            self._reply(req_id, make_error(req_id, ERR_SCALE_TIMEOUT, "scale did not stabilise"))
            return
        self._reply(req_id, make_response(req_id, True, value=value, unit="g", stable=True))

    def _cmd_scale_tare(self, req_id: Any, request: dict[str, Any]) -> None:
        scale = self._scale_slot.active_driver()
        if not isinstance(scale, ScaleDriver):
            self._reply(req_id, make_error(req_id, ERR_NO_DEVICE, "no scale driver"))
            return
        if not scale.is_open():
            self._reply(req_id, make_error(req_id, ERR_NO_DEVICE, "scale is not online"))
            return
        if not scale.tare():
            self._reply(req_id, make_error(req_id, ERR_INTERNAL, "tare command failed"))
            return
        self._reply(req_id, make_response(req_id, True))

    def _cmd_shutdown(self, req_id: Any, request: dict[str, Any]) -> None:
        self._reply(req_id, make_response(req_id, True))
        self.shutdown_requested.emit()

    def _cmd_attach(self, req_id: Any, request: dict[str, Any]) -> None:
        devices = request.get("devices", []) or []
        if not isinstance(devices, list):
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, "'devices' must be a list"))
            return

        attached = []
        for dev in devices:
            slot = self._slot_by_id(dev)
            if slot is None:
                self._reply(req_id, make_error(req_id, ERR_NO_DEVICE, f"unknown device: {dev}"))
                return
            if slot.attach():
                self._owned_devices.add(dev)
                attached.append(dev)
            else:
                self._reply(req_id, make_error(req_id, ERR_BUSY, f"{dev} already attached"))
                return
        self._reply(req_id, make_response(req_id, True, attached=attached))

    def _cmd_detach(self, req_id: Any, request: dict[str, Any]) -> None:
        devices = request.get("devices", []) or []
        if not isinstance(devices, list):
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, "'devices' must be a list"))
            return

        still_attached = list(self._owned_devices)
        for dev in devices:
            if dev in self._owned_devices:
                slot = self._slot_by_id(dev)
                if slot:
                    slot.detach()
                self._owned_devices.discard(dev)
                still_attached.remove(dev)
        self._reply(req_id, make_response(req_id, True, attached=still_attached))

    def _cmd_emit(self, req_id: Any, request: dict[str, Any]) -> None:
        event = str(request.get("event", ""))

        if event == "scan":
            if "scanner" not in self._owned_devices:
                self._reply(req_id, make_error(req_id, ERR_NOT_ATTACHED, "scanner not attached"))
                return
            code = str(request.get("code", ""))
            self._scanner_slot.active_driver().emit_scan(code)
            self._reply(req_id, make_response(req_id, True))

        elif event == "weight":
            if "scale" not in self._owned_devices:
                self._reply(req_id, make_error(req_id, ERR_NOT_ATTACHED, "scale not attached"))
                return
            value = float(request.get("value", 0.0))
            stable = bool(request.get("stable", True))
            self._scale_slot.active_driver().emit_weight(value, "g", stable)
            self._reply(req_id, make_response(req_id, True))

        elif event == "device":
            dev = str(request.get("device", ""))
            if dev not in self._owned_devices:
                self._reply(req_id, make_error(req_id, ERR_NOT_ATTACHED, f"{dev} not attached"))
                return
            state = str(request.get("state", ""))
            reason = str(request.get("reason", ""))
            if state not in DEVICE_STATES:
                self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, f"bad state: {state}"))
                return
            slot = self._slot_by_id(dev)
            if slot:
                slot.active_driver()._set_state(state, reason)
            self._reply(req_id, make_response(req_id, True))

        elif event == "job":
            if "printer" not in self._owned_devices:
                self._reply(req_id, make_error(req_id, ERR_NOT_ATTACHED, "printer not attached"))
                return
            job_id = str(request.get("job", ""))
            state = str(request.get("state", JOB_DONE))
            if job_id in self._pending_jobs:
                self._pending_jobs.discard(job_id)
            self._server.broadcast(EVENT_JOB, {"job": job_id, "state": state, "error": ""})
            self._reply(req_id, make_response(req_id, True))

        else:
            self._reply(req_id, make_error(req_id, ERR_BAD_REQUEST, f"unknown emit event: {event}"))

    # --- Outbound --------------------------------------------------------

    def send_event(self, event_name: str, data: dict[str, Any]) -> None:
        if event_name not in self._subscriptions:
            return
        frame = make_event(event_name, **data)
        logger.debug("Session: -> event %s", frame)
        self._write(frame)

    def _reply(self, req_id: Any, frame: dict[str, Any]) -> None:
        logger.info(f"Session: replying to {req_id} with {frame}")
        self._write(frame)

    def _write(self, frame: dict[str, Any]) -> None:
        from .protocol import encode_frame
        try:
            data = encode_frame(frame)
            logger.info(f"Session: writing {len(data)} bytes")
            self._socket.write(data)
            self._socket.flush()
        except Exception as e:
            logger.error(f"Session: failed to write to socket: {e}", exc_info=True)

    # --- Disconnect ------------------------------------------------------

    def _on_disconnected(self) -> None:
        logger.info("Session: client disconnected")
        # Auto-detach owned devices
        for dev in list(self._owned_devices):
            slot = self._slot_by_id(dev)
            if slot:
                slot.detach()
        self._owned_devices.clear()
        self._subscriptions.clear()
        self._buffer.clear()

    # --- Helpers ---------------------------------------------------------

    def _slot_by_id(self, device_id: str) -> DeviceSlot | None:
        if device_id == "scanner":
            return self._scanner_slot
        if device_id == "scale":
            return self._scale_slot
        if device_id == "printer":
            return self._printer_slot
        return None