"""Служба устройств живёт ровно столько, сколько приложение.

Кладовщику нечего знать про отдельный процесс: он запускает ProЗапас, и вместе
с ним поднимается `devices`, а при закрытии окна гаснет. Чужую службу
приложение не трогает — если канал уже кто-то обслуживает (её запустили руками
или работает вторая копия приложения), мы просто подключаемся и при выходе
оставляем её жить.

Останов идёт по-хорошему: команда `shutdown` даёт службе закрыть порты и
дописать журнал, и только если она не ушла за отведённое время, процесс
снимается принудительно.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess

from . import config, devices

#: Путь к службе можно задать снаружи — например, когда её ставят отдельно.
SERVICE_DIR_ENV = "PROZAPAS_DEVICES_SERVICE"

_READY_TIMEOUT_MS = 6000    # столько ждём, пока служба откроет канал
_STOP_TIMEOUT_MS = 3000     # столько ждём добровольного завершения
_POLL_MS = 100
#: Столько служба живёт без клиентов — страховка от осиротевшего процесса.
_IDLE_TIMEOUT_SEC = config.number("PROZAPAS_IDLE_TIMEOUT")

OWN = "own"                 # службу подняли мы — нам её и гасить
EXTERNAL = "external"       # служба была до нас
ABSENT = "absent"           # запустить не удалось: работаем в симуляции


def service_dir() -> Path | None:
    """Каталог со службой или None, если он не найден."""
    override = config.get(SERVICE_DIR_ENV).strip()
    candidates = [Path(override)] if override else []
    candidates.append(Path(__file__).resolve().parents[1])   # каталог desktop
    for path in candidates:
        if (path / "devices" / "__main__.py").exists():
            return path
    return None


def _python() -> str:
    """Интерпретатор для дочернего процесса — без консольного окна."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    if sys.platform == "win32" and windowless.exists():
        return str(windowless)
    return str(exe)


class ServiceHost(QObject):
    """Запускает службу устройств и следит за её жизнью."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._state = ABSENT

    @property
    def state(self) -> str:
        return self._state

    @property
    def owned(self) -> bool:
        return self._state == OWN and self._alive()

    def start(self) -> str:
        """Поднять службу, если её ещё нет. Возвращает OWN/EXTERNAL/ABSENT."""
        if devices.pipe_answers():
            self._state = EXTERNAL
            return self._state
        if not self._spawn():
            self._state = ABSENT
            return self._state
        self._state = OWN if self._wait_ready() else ABSENT
        if self._state == ABSENT:
            self._kill()
        return self._state

    def ensure_running(self) -> bool:
        """Перезапустить свою службу, если она успела умереть."""
        if devices.pipe_answers():
            return True
        if self._state == EXTERNAL:
            return False        # чужую службу не поднимаем
        return self.start() == OWN

    def stop(self) -> None:
        """Погасить службу, если её запускали мы."""
        if not self._alive():
            self._process = None
            return
        devices.shutdown_service()          # даём закрыться самой
        if self._process.waitForFinished(_STOP_TIMEOUT_MS):
            self._process = None
            return
        self._kill()

    # ── внутреннее ────────────────────────────────────────────
    def _alive(self) -> bool:
        return (self._process is not None
                and self._process.state() != QProcess.ProcessState.NotRunning)

    def _spawn(self) -> bool:
        path = service_dir()
        if path is None:
            return False
        process = QProcess(self)
        process.setWorkingDirectory(str(path))
        # свой журнал служба пишет в %ProgramData%; её консольный вывод нам не
        # нужен, а непрочитанный канал со временем переполняется и вешает её
        process.setStandardOutputFile(QProcess.nullDevice())
        process.setStandardErrorFile(QProcess.nullDevice())
        process.setProgram(_python())
        # Тайм-аут простоя оставляем: если приложение упадёт, служба не
        # переживёт его надолго. Пока мы подключены, отсчёт не идёт, а после
        # симуляции её при необходимости поднимет ensure_running().
        process.setArguments(
            ["-m", "devices", "--idle-timeout", str(_IDLE_TIMEOUT_SEC)])
        process.start()
        if not process.waitForStarted(3000):
            return False
        self._process = process
        return True

    def _wait_ready(self) -> bool:
        """Ждём, пока служба откроет канал: до этого команды слать некому.

        Ждём обычным сном, не прокручивая события: проба канала и цикл событий
        друг другу мешают, а окно на этот момент ещё не показано.
        """
        deadline = time.monotonic() + _READY_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            if not self._alive():
                return False                # упала на старте — код 2 или 3
            if devices.pipe_answers():
                return True
            time.sleep(_POLL_MS / 1000)
        return False

    def _kill(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.waitForFinished(1000)
            self._process = None


host = ServiceHost()
