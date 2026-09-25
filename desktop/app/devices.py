"""Connected equipment — scanner, label printer and scales.

The hardware itself is held by a separate process, `devices`, which the
application talks to over the named pipe `prozapas-devices`. Nothing here knows
about COM ports, printer spoolers or scale protocols: the client sends
``{"cmd": …}`` and receives ``{"event": …}``.

Состояния приходят от службы. Если её нет, все устройства `offline`: штрихкод
вводится вручную, вес — тоже, а упаковка и отгрузка блокируются, потому что
печатать этикетку нечем.

The public surface (`states`, `available`, `bus.changed`, `scanned`) is what the
screens use.
"""
from __future__ import annotations

import json
from collections import deque

from PyQt6.QtCore import QDateTime, QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QLocalSocket

from . import config

# ── protocol (mirrors devices/protocol.py) ─────────────────────────
PROTOCOL_VERSION = 1
#: Короткое имя: префикс канала Qt добавит сам. Читаем из настроек — служба
#: берёт его оттуда же, и раньше эти два значения могли разойтись: переменную
#: ставили, служба слушала новый канал, а приложение стучалось в старый.
PIPE_NAME = config.get("PROZAPAS_PIPE_NAME")

OFFLINE = "offline"
ONLINE = "online"
ERROR = "error"

STATE_LABELS = {OFFLINE: "Недоступен", ONLINE: "Доступен", ERROR: "Ошибка"}

DEVICES = [("scanner", "Сканер"), ("printer", "Принтер"), ("scale", "Весы")]

_RECONNECT_MS = 5000        # как часто пробовать поднять соединение заново
_SCAN_DEDUP_MS = 500        # сканеры повторяют код при удержании кнопки
#: Глубина журнала обмена. Весы дают десять кадров в секунду, так что окно
#: получается коротким по времени — но диагностируют по свежим событиям.
LOG_LIMIT = 500

OUT = "→"       # запрос от приложения
IN = "←"        # ответ или событие от службы
NOTE = "·"      # отметка самого клиента: соединение, обрыв


class _Bus(QObject):
    """Fires when the state of any device changes."""
    changed = pyqtSignal()


bus = _Bus()


class DeviceClient(QObject):
    """Клиент службы устройств: соединение, команды, события.

    Соединение поднимается само и переустанавливается после обрыва, поэтому
    приложение может стартовать раньше службы и переживать её перезапуск.
    """

    scanned = pyqtSignal(str)            # штрихкод
    weight_read = pyqtSignal(float, bool)  # килограммы, устойчиво ли
    logged = pyqtSignal(dict)            # новая запись журнала обмена

    def __init__(self, parent=None):
        super().__init__(parent)
        self._socket = QLocalSocket(self)
        self._buffer = bytearray()
        self._next_id = 0
        self._states = {key: OFFLINE for key, _ in DEVICES}
        self._connected = False
        self._last_scan = ("", 0)
        self._want_weight = False        # нужен ли поток веса: см. _on_connected
        self._enabled = False
        self._log = deque(maxlen=LOG_LIMIT)

        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.errorOccurred.connect(self._on_socket_error)
        self._socket.readyRead.connect(self._on_ready_read)

        self._retry = QTimer(self)
        self._retry.setInterval(_RECONNECT_MS)
        self._retry.timeout.connect(self._try_connect)

    # ── жизненный цикл ────────────────────────────────────────
    def start(self):
        self._enabled = True
        self._try_connect()
        self._retry.start()

    def start_sync(self, timeout_ms: int) -> bool:
        """Поднять соединение и сразу узнать результат — нужно при старте.

        Если службы нет, откатываемся в исходное состояние, чтобы приложение
        осталось в симуляции и не держало напрасных таймеров.
        """
        self._enabled = True
        self._retry.start()
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.connectToServer(PIPE_NAME)
        if self._socket.waitForConnected(timeout_ms):
            return True
        self.stop()
        return False

    def stop(self):
        self._enabled = False
        self._retry.stop()
        if self._socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.disconnectFromServer()
        self._set_all(OFFLINE)

    @property
    def connected(self) -> bool:
        return self._connected

    def states(self) -> dict[str, str]:
        return dict(self._states)

    def _try_connect(self):
        if not self._enabled or self._connected:
            return
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.connectToServer(PIPE_NAME)

    def _on_connected(self):
        self._connected = True
        self._buffer.clear()
        self._note("соединение со службой установлено")
        self._send("hello", protocol=PROTOCOL_VERSION, client="prozapas 1.0")
        self._send("devices")
        # Подписка — это состояние клиента, а не разовая команда: объявляем весь
        # набор целиком, иначе после перезапуска службы поток веса не вернётся, а
        # экран продолжит показывать последнее значение как живое.
        self._send("subscribe", events=self._subscriptions())

    def _subscriptions(self) -> list[str]:
        """Полный набор событий, который нужен приложению прямо сейчас."""
        events = ["scan", "device", "job"]
        if self._want_weight:
            events.append("weight")
        return events

    def _on_socket_error(self, error):
        """Ошибка сокета — ещё не обрыв связи.

        Истёкшее ожидание отдаёт `SocketTimeoutError`, хотя канал жив. Считать
        это обрывом нельзя: приложение уводило все устройства в offline после
        первой же печати, и следующая кнопка молча ничего не отправляла.
        """
        if self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._note(f"ошибка сокета: {error.name} (связь сохранена)")
            return
        self._on_disconnected()

    def _on_disconnected(self):
        if not self._connected and all(v == OFFLINE for v in self._states.values()):
            return
        self._note("соединение со службой потеряно")
        self._connected = False
        self._set_all(OFFLINE)

    def _set_all(self, state):
        changed = any(v != state for v in self._states.values())
        self._states = {key: state for key, _ in DEVICES}
        if changed:
            bus.changed.emit()

    # ── журнал обмена ─────────────────────────────────────────
    def _record(self, direction: str, frame: dict, kind: str = ""):
        """Положить кадр в журнал и разбудить открытое окно логов.

        Журнал нужен при подключении настоящего железа: по нему видно, шлёт ли
        устройство хоть что-нибудь и в каком виде, — иначе диагностировать
        молчащий COM-порт из интерфейса нечем.
        """
        entry = {
            "at": QDateTime.currentDateTime().toString("HH:mm:ss.zzz"),
            "dir": direction,
            "kind": kind or frame.get("event") or frame.get("cmd") or "",
            "text": json.dumps(frame, ensure_ascii=False),
        }
        self._log.append(entry)
        self.logged.emit(entry)

    def _note(self, text: str):
        """Отметка о состоянии соединения — её в канале нет, а понимать надо."""
        self._record(NOTE, {"note": text}, kind="note")

    def log_entries(self) -> list[dict]:
        return list(self._log)

    def clear_log(self):
        self._log.clear()

    # ── отправка и разбор ─────────────────────────────────────
    def _send(self, cmd, **fields) -> int:
        self._next_id += 1
        frame = dict(id=self._next_id, cmd=cmd, **fields)
        self._socket.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
        self._socket.flush()
        self._record(OUT, frame)
        return self._next_id

    def _on_ready_read(self):
        self._buffer.extend(bytes(self._socket.readAll()))
        while b"\n" in self._buffer:
            line, _, _rest = bytes(self._buffer).partition(b"\n")
            del self._buffer[: len(line) + 1]
            if not line.strip():
                continue
            try:
                frame = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue                     # мусор в канале не должен ронять клиент
            if isinstance(frame, dict):
                self._handle(frame)

    def _handle(self, frame: dict):
        self._record(IN, frame)
        if "event" in frame:
            self._handle_event(frame)
            return
        devices = frame.get("devices")
        if isinstance(devices, dict):        # ответ на `devices`
            self._states = {key: devices.get(key, OFFLINE) for key, _ in DEVICES}
            bus.changed.emit()

    def _handle_event(self, frame: dict):
        event = frame.get("event")
        if event == "scan":
            code = str(frame.get("code", "")).strip()
            now = _now_ms()
            if code and not (code == self._last_scan[0]
                             and now - self._last_scan[1] < _SCAN_DEDUP_MS):
                self._last_scan = (code, now)
                # экран в ответ на скан спрашивает весы, а синхронный запрос
                # изнутри readyRead не получит ответа: сокет не читается
                # рекурсивно. Отдаём скан следующим тактом цикла событий.
                QTimer.singleShot(0, lambda c=code: self.scanned.emit(c))
        elif event == "device":
            key = frame.get("id")
            state = frame.get("state")
            if key in self._states and state in STATE_LABELS:
                if self._states[key] != state:
                    self._states[key] = state
                    bus.changed.emit()
        elif event == "weight":
            self.weight_read.emit(_to_kg(frame), bool(frame.get("stable")))

    def print_label(self, key: str, payload: str, fmt: str = "zpl", copies: int = 1) -> bool:
        """Отправить этикетку в печать. Ответа не ждём.

        Раньше здесь крутился вложенный цикл событий: он ждал номер задания,
        который экрану не нужен, а по дороге ловил таймаут сокета и обрушивал
        связь. Итог печати всё равно приходит событием `job`.
        """
        if not self._connected:
            return False
        self._send("print", key=key, format=fmt, payload=payload, copies=copies)
        return True

    def send_shutdown(self):
        """Команда службе завершиться — ответа не ждём, она сразу уходит."""
        if self._connected:
            self._send("shutdown")
            self._socket.waitForBytesWritten(400)

    def subscribe_weight(self, on: bool):
        """Поток показаний весов нужен только на экранах, где он виден.

        Желаемое состояние запоминается: соединение может подняться позже, а
        служба — перезапуститься, и тогда подписку восстановит `_on_connected`.
        """
        self._want_weight = on
        if self._connected:
            self._send("subscribe" if on else "unsubscribe", events=["weight"])


def _now_ms() -> int:
    from PyQt6.QtCore import QDateTime
    return QDateTime.currentMSecsSinceEpoch()


def _to_kg(frame: dict) -> float:
    """Служба считает в граммах; в приложении вес везде в килограммах."""
    value = float(frame.get("value", 0.0) or 0.0)
    return value / 1000.0 if str(frame.get("unit", "g")) == "g" else value


client = DeviceClient()


def pipe_answers(timeout_ms: int = 400) -> bool:
    """Отвечает ли кто-нибудь на канале службы."""
    probe = QLocalSocket()
    probe.connectToServer(PIPE_NAME)
    alive = probe.waitForConnected(timeout_ms)
    probe.abort()
    return alive


def shutdown_service() -> bool:
    """Попросить службу завершиться. Только для того, кто её запускал."""
    if client.connected:
        client.send_shutdown()
        return True
    probe = QLocalSocket()
    probe.connectToServer(PIPE_NAME)
    if not probe.waitForConnected(400):
        return False
    probe.write(json.dumps({"id": 1, "cmd": "shutdown"}).encode("utf-8") + b"\n")
    probe.flush()
    probe.waitForBytesWritten(400)
    probe.disconnectFromServer()
    return True


def service_connected() -> bool:
    return client.connected


def connect(timeout_ms: int = 400) -> bool:
    """Подключиться к службе. Дальше клиент держит связь сам."""
    return client.start_sync(timeout_ms)


def states() -> dict[str, str]:
    """Current state of every device, keyed as in `DEVICES`."""
    return client.states()


def available(key: str) -> bool:
    return states().get(key) == ONLINE


def live_weight(on: bool) -> None:
    """Поток показаний весов включаем только там, где его видно."""
    client.subscribe_weight(on)


def log_entries() -> list[dict]:
    """Журнал обмена со службой — для окна «Логи»."""
    return client.log_entries()


def clear_log() -> None:
    client.clear_log()


def print_label(key: str, payload: str, copies: int = 1):
    """Отправить этикетку в печать. Возвращает номер задания или None."""
    return client.print_label(key, payload, copies=copies)
