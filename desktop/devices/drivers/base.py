"""Base device driver for devices.

Every concrete driver (scanner, scale, printer) inherits from
:class:`DeviceDriver`, which owns the shared lifecycle: opening and
closing the device, tracking its state, emitting ``state_changed``
and, on loss of the device, scheduling a reconnect every few seconds.

The base class implements the template methods :meth:`open` and
:meth:`close`; subclasses implement :meth:`_do_open` and
:meth:`_do_close` to talk to their specific hardware (or stub).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..protocol import STATE_ERROR, STATE_OFFLINE, STATE_ONLINE

logger = logging.getLogger(__name__)

#: Interval between reconnect attempts after the device goes away.
RECONNECT_INTERVAL_MS: int = 5000


class DeviceDriver(QObject):
    """Abstract base for all device drivers.

    Signals
    -------
    state_changed(str, str)
        Emitted when the device state changes: ``(new_state, reason)``.
        The :class:`~devices.server.DeviceServer` fills in the
        device ``id`` when relaying this as a ``device`` event.
    """

    state_changed = pyqtSignal(str, str)  # state, reason

    def __init__(
        self,
        device_id: str = "",
        auto_reconnect: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        #: Stable identifier used in ``device`` events (e.g. ``"scanner"``).
        self.device_id: str = device_id
        self._state: str = STATE_OFFLINE
        self._opened: bool = False
        self._closing: bool = False
        self._auto_reconnect: bool = auto_reconnect

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(RECONNECT_INTERVAL_MS)
        self._reconnect_timer.timeout.connect(self._on_reconnect)

    # --- Public lifecycle (template methods) ------------------------------

    def open(self) -> None:
        """Open the device.

        Guards against double-open; delegates the actual work to
        :meth:`_do_open`. Any exception is caught, logged and turns the
        device into ``error`` (never propagates to the caller).
        """
        if self._opened:
            logger.debug("%s: open() ignored, already opened", self._logname())
            return
        self._opened = True
        self._closing = False
        try:
            self._do_open()
        except Exception as exc:
            logger.error("%s: open failed: %s", self._logname(), exc, exc_info=True)
            self._set_state(STATE_ERROR, f"open error: {exc}")

    def close(self) -> None:
        """Close the device and release resources.

        Stops the reconnect timer, lets :meth:`_do_close` release the
        hardware, then marks the device ``offline``. Never raises.
        """
        self._closing = True
        self._reconnect_timer.stop()
        try:
            self._do_close()
        except Exception as exc:
            logger.error("%s: close failed: %s", self._logname(), exc, exc_info=True)
        self._opened = False
        self._set_state(STATE_OFFLINE, "closed")

    def is_open(self) -> bool:
        """Return ``True`` while the device is connected and online."""
        return self._state == STATE_ONLINE

    @property
    def state(self) -> str:
        """Current device state (``online`` / ``offline`` / ``error``)."""
        return self._state

    # --- Hooks for subclasses --------------------------------------------

    def _do_open(self) -> None:
        """Open the hardware. Subclasses implement this.

        On success they should set state to ``online`` via
        :meth:`_set_state`; on failure they may raise, which the base
        class turns into an ``error`` state.
        """
        raise NotImplementedError

    def _do_close(self) -> None:
        """Release the hardware. Subclasses implement this."""
        # Default: nothing to release.
        return

    # --- Protected helpers -----------------------------------------------

    def _set_state(self, state: str, reason: str = "") -> None:
        """Change state, log it, emit the signal and arm reconnect."""
        if self._state == state:
            return
        old = self._state
        self._state = state
        logger.info(
            "%s: state %s -> %s (%s)", self._logname(), old, state, reason
        )
        self.state_changed.emit(state, reason)
        # Reconnect only when we lose the device and not while closing.
        if (
            state in (STATE_OFFLINE, STATE_ERROR)
            and self._auto_reconnect
            and not self._closing
            and self._opened
        ):
            self._reconnect_timer.start()

    def _on_reconnect(self) -> None:
        """Timer slot: try to reopen the device after a loss."""
        if self._closing or not self._opened:
            return
        logger.info("%s: reconnecting...", self._logname())
        try:
            self._do_open()
        except Exception as exc:  # noqa: BLE001
            logger.error("%s: reconnect failed: %s", self._logname(), exc)
            self._reconnect_timer.start()

    def _logname(self) -> str:
        """A readable name for log lines."""
        return self.device_id or self.__class__.__name__
