"""Device slots for dynamic source switching."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

from .drivers.base import DeviceDriver

logger = logging.getLogger(__name__)


class DeviceSlot(QObject):
    """Manages a real and a fake driver for a single device role."""

    state_changed = pyqtSignal(str, str)

    def __init__(
        self,
        device_id: str,
        real_driver: DeviceDriver,
        fake_driver: DeviceDriver,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.device_id: str = device_id
        self._real: DeviceDriver = real_driver
        self._fake: DeviceDriver = fake_driver
        self._active: DeviceDriver = self._real

        self._real.state_changed.connect(self._on_state_changed)
        self._fake.state_changed.connect(self._on_state_changed)

    def _on_state_changed(self, state: str, reason: str) -> None:
        sender = self.sender()
        if sender == self._active:
            # Если событие пришло от фейка во время симуляции, исправляем reason
            if sender == self._fake and reason == "fake":
                reason = "simulated"
            self.state_changed.emit(state, reason)

    def attach(self) -> bool:
        """Switch to the fake driver."""
        if self._active == self._real:
            self._active = self._fake
            # Блокируем сигналы фейка, чтобы он не слал 'fake', мы сами отправим 'simulated'
            self._fake.blockSignals(True)
            self._fake.open()
            self._fake.blockSignals(False)
            self.state_changed.emit(self._fake.state, "simulated")
            logger.info("Slot %s: attached simulator", self.device_id)
            return True
        return False

    def detach(self) -> bool:
        """Switch back to the real driver."""
        if self._active == self._fake:
            self._fake.blockSignals(True)
            self._fake.close()
            self._fake.blockSignals(False)
            self._active = self._real
            self.state_changed.emit(self._real.state, "real")
            logger.info("Slot %s: detached simulator", self.device_id)
            return True
        return False

    def active_driver(self) -> DeviceDriver:
        return self._active

    def is_simulated(self) -> bool:
        return self._active == self._fake