"""Barcode scanner driver for devices.

The scanner is a USB device switched into virtual COM-port (USB-CDC)
mode. The driver reads the stream, accumulates bytes up to ``\\r`` or
``\\n`` (and splits on a ``0x1D`` GS separator if present), cleans the
string and emits a :pyattr:`scanned` signal — subject to a 500 ms
deduplication window, because scanners resend the same code while the
trigger is held.

If no port is configured or the port is not found, the driver enters
a search state: every few seconds it rescans available COM ports and
attempts to open the first suitable one.
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QByteArray, QObject, pyqtSignal
from PyQt6.QtSerialPort import QSerialPort

from ..config import list_ports
from ..protocol import STATE_ERROR, STATE_OFFLINE, STATE_ONLINE
from .base import DeviceDriver

logger = logging.getLogger(__name__)

# --- Terminators / separators ------------------------------------------

#: Carriage return — a common scanner suffix.
TERMINATOR_CR: int = 0x0D
#: Line feed — alternate terminator.
TERMINATOR_LF: int = 0x0A
#: Group Separator (0x1D) — some scanners use it as a field separator.
BYTE_GS = b'\x1d'

#: Byte values treated as line terminators.
_TERMINATORS = (TERMINATOR_CR, TERMINATOR_LF)

# --- Timing -------------------------------------------------------------

#: Window within which an identical code is treated as a duplicate.
DEDUP_INTERVAL_MS: int = 500

#: Ports claimed by drivers to avoid conflicts during auto-search.
_claimed_ports: set[str] = set()


class ScannerDriver(DeviceDriver):
    """Driver for a barcode scanner on a virtual COM port.

    Signals
    -------
    scanned(str)
        Emitted with a cleaned, upper-case barcode string when a scan
        passes deduplication.
    """

    scanned = pyqtSignal(str)

    def __init__(
        self,
        port_name: str = "",
        baud: int = 115200,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(device_id="scanner", parent=parent)
        self._port_name: str = port_name or ""
        self._baud: int = baud

        self._serial: QSerialPort | None = None
        self._buffer: bytearray = bytearray()
        self._last_code: str = ""
        self._last_code_monotonic_ms: int = 0

    # --- Lifecycle -------------------------------------------------------

    def _do_open(self) -> None:
        """Open the COM port, or search for one if not configured."""
        if not self._port_name or not self._port_name.upper().startswith("COM"):
            found_port = self._find_port()
            if found_port:
                logger.info("ScannerDriver: found port %s", found_port)
                self._port_name = found_port
            else:
                logger.debug("ScannerDriver: no port found, searching...")
                self._set_state(STATE_OFFLINE, "searching")
                return

        logger.info("ScannerDriver: opening %s @ %d baud", self._port_name, self._baud)
        self._open_serial()

    def _find_port(self) -> str:
        """Find a suitable COM port. If a hint is set, match it."""
        hint = self._port_name.lower()
        for p in list_ports():
            parts = p.split(" ", 1)
            device = parts[0]
            description = parts[1] if len(parts) > 1 else ""

            if device in _claimed_ports:
                continue
            if hint and hint in description.lower():
                _claimed_ports.add(device)
                return device
            if not hint:
                _claimed_ports.add(device)
                return device
        return ""

    def _open_serial(self) -> None:
        """Create and open the underlying ``QSerialPort``."""
        self._close_serial()
        serial = QSerialPort(self)
        serial.setPortName(self._port_name)
        serial.setBaudRate(self._baud)
        serial.setDataBits(QSerialPort.DataBits.Data8)
        serial.setParity(QSerialPort.Parity.NoParity)
        serial.setStopBits(QSerialPort.StopBits.OneStop)
        serial.setFlowControl(QSerialPort.FlowControl.NoFlowControl)

        if not serial.open(QSerialPort.OpenModeFlag.ReadOnly):
            error = serial.errorString()
            logger.error("ScannerDriver: failed to open %s – %s", self._port_name, error)
            _claimed_ports.discard(self._port_name)
            serial.deleteLater()
            # Уходим в поиск, а не в ошибку
            self._port_name = ""
            self._set_state(STATE_OFFLINE, "searching")
            return

        serial.readyRead.connect(self._on_ready_read)
        serial.errorOccurred.connect(self._on_error_occurred)
        self._serial = serial
        self._set_state(STATE_ONLINE, "port opened")

        serial.readyRead.connect(self._on_ready_read)
        serial.errorOccurred.connect(self._on_error_occurred)
        self._serial = serial
        self._set_state(STATE_ONLINE, "port opened")

    def _do_close(self) -> None:
        """Release the serial port and clear buffers."""
        self._close_serial()
        self._buffer.clear()
        self._last_code = ""

    def _close_serial(self) -> None:
        """Disconnect and dispose of the current ``QSerialPort``."""
        serial = self._serial
        self._serial = None
        if serial is None:
            return
        try:
            serial.readyRead.disconnect(self._on_ready_read)
        except (TypeError, RuntimeError):
            pass
        try:
            serial.errorOccurred.disconnect(self._on_error_occurred)
        except (TypeError, RuntimeError):
            pass
        if serial.isOpen():
            serial.close()
        _claimed_ports.discard(self._port_name)
        serial.deleteLater()

    # --- Slots -----------------------------------------------------------

    def _on_ready_read(self) -> None:
        """Read available bytes and feed them to the line assembler."""
        if self._serial is None:
            return
        try:
            data: QByteArray = self._serial.readAll()
            self._feed_bytes(bytes(data))
        except Exception:
            logger.error("ScannerDriver: error reading port", exc_info=True)

    def _on_error_occurred(self, error: QSerialPort.SerialPortError) -> None:
        """Handle a serial-port error (e.g. cable unplugged)."""
        if error == QSerialPort.SerialPortError.NoError:
            return
        msg = self._serial.errorString() if self._serial else str(error)
        logger.warning("ScannerDriver: port error (%s): %s", error, msg)
        if (
            error == QSerialPort.SerialPortError.ResourceError
            or error == QSerialPort.SerialPortError.DeviceNotFoundError
        ):
            self._close_serial()
            self._port_name = ""
            # Уходим в поиск, а не в ошибку
            self._set_state(STATE_OFFLINE, "searching")
    # --- Parsing ---------------------------------------------------------

    def _feed_bytes(self, data: bytes) -> None:
        """Accumulate *data*, extract complete lines and process them."""
        self._buffer.extend(data)
        while True:
            idx = self._next_terminator()
            if idx < 0:
                break
            raw_line = bytes(self._buffer[:idx])
            del self._buffer[: idx + 1]
            self._process_line(raw_line)

    def _next_terminator(self) -> int:
        """Return the index of the earliest terminator, or -1."""
        positions = [
            i
            for i in (self._buffer.find(b) for b in _TERMINATORS)
            if i >= 0
        ]
        return min(positions) if positions else -1

    def _process_line(self, raw_line: bytes) -> None:
        """Clean a raw line, split on GS, and emit each non-empty code."""
        codes = self._split_codes(raw_line)
        for code in codes:
            self._handle_code(code)

    @staticmethod
    def _split_codes(raw_line: bytes) -> list[str]:
        """Split *raw_line* on GS, clean and upper-case each piece."""
        out: list[str] = []
        for piece in raw_line.split(BYTE_GS):
            text = piece.decode("ascii", errors="replace").strip().upper()
            text = text.replace("\r", "").replace("\n", "")
            if text:
                out.append(text)
        return out

    def _handle_code(self, code: str) -> None:
        """Apply deduplication, then emit :pyattr:`scanned`."""
        now_ms = int(time.monotonic() * 1000)
        if (
            code == self._last_code
            and (now_ms - self._last_code_monotonic_ms) < DEDUP_INTERVAL_MS
        ):
            logger.debug("Scanner: duplicate scan ignored: %s", code)
            return
        self._last_code = code
        self._last_code_monotonic_ms = now_ms
        logger.info("Scanner: barcode=%s", code)
        self.scanned.emit(code)

    # --- Public emulation API (kept for FakeScanner) ---------------------

    def emit_scan(self, code: str) -> None:
        """Inject a barcode for emulation."""
        if not isinstance(code, str):
            code = str(code)
        codes = self._split_codes(code.encode("ascii", errors="replace"))
        for cleaned in codes:
            self._handle_code(cleaned)