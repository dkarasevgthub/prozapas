"""Debug console / CLI client for devices.

Connects to the device service's named pipe as an ordinary client and
either runs a single command (when given arguments) or drops into an
interactive prompt that reads commands from stdin.

Commands map either to ordinary protocol requests or to the ``emit``
command used to inject events (scan / weight / device) for testing
scenarios that would otherwise need real hardware.

Вбрасывать события можно только в устройство, взятое себе командой
``attach``: служба проверяет владение и иначе отвечает ``not_attached``.
В однократном режиме это значит, что `attach` и `emit` надо слать в одном
сеансе — то есть из интерактивного режима::

    python -m devices.cli attach scale
    python -m devices.cli scan WH1281187100421
    python -m devices.cli weight 22.4 --stable
    python -m devices.cli device printer offline --reason unplugged
    python -m devices.cli printer send mykey zpl "^XA^FDhi^XZ"
    python -m devices.cli printer queue
    python -m devices.cli scale read
    python -m devices.cli scale tare
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import threading
from typing import Any

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalSocket

from .config import get_default_config
from .protocol import parse_frame

#: Default pipe name (kept in sync with the config default).
DEFAULT_PIPE_NAME = "prozapas-devices"


class DebugClient(QObject):
    """A thin QLocalSocket client wrapping the device-service protocol.

    Signals
    -------
    frame_received(dict)
        A complete inbound frame (response or event).
    disconnected_()
        The connection to the service was lost.
    """

    frame_received = pyqtSignal(dict)
    disconnected_ = pyqtSignal()

    def __init__(self, pipe_name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pipe_name = pipe_name
        self._socket = QLocalSocket(self)
        self._buffer = bytearray()
        self._socket.readyRead.connect(self._on_ready_read)
        self._socket.disconnected.connect(self._on_disconnected)

    # --- Connection ------------------------------------------------------

    def connect(self, timeout_ms: int = 3000) -> bool:
        """Connect to the named pipe. Returns ``True`` on success.

        Имя передаём короткое: префикс ``\\\\.\\pipe\\`` Qt подставляет сам, а с
        полным путём клиент сервера не находит (см. ``DeviceServer.start``).
        """
        self._socket.connectToServer(self._pipe_name)
        return self._socket.waitForConnected(timeout_ms)

    def is_connected(self) -> bool:
        """Whether the socket is currently connected."""
        return self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState

    def disconnect(self) -> None:
        """Disconnect from the service."""
        self._socket.disconnectFromServer()

    # --- I/O -------------------------------------------------------------

    def send(self, frame: dict[str, Any]) -> bool:
        """Write a request frame. Returns ``True`` on success."""
        if not self.is_connected():
            print("not connected", file=sys.stderr)
            return False
        raw = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
        self._socket.write(raw)
        self._socket.flush()
        return True

    def _on_ready_read(self) -> None:
        """Frame incoming bytes by newline and emit each frame."""
        self._buffer.extend(bytes(self._socket.readAll()))
        while True:
            idx_lf = self._buffer.find(0x0A)
            idx_cr = self._buffer.find(0x0D)
            candidates = [i for i in (idx_lf, idx_cr) if i >= 0]
            if not candidates:
                break
            idx = min(candidates)
            line = bytes(self._buffer[:idx])
            del self._buffer[: idx + 1]
            frame = parse_frame(line)
            if frame is not None:
                self.frame_received.emit(frame)

    def _on_disconnected(self) -> None:
        """Notify listeners of the disconnect."""
        self.disconnected_.emit()


# --- Command translation -----------------------------------------------


def build_request(cmd_line: str) -> tuple[dict[str, Any] | None, str]:
    """Translate a CLI command string into a protocol request.

    Returns ``(frame, error)`` — exactly one is non-empty. Returns
    ``(None, "")`` for ``quit``/``exit`` (handled by the caller).
    """
    try:
        tokens = shlex.split(cmd_line)
    except ValueError as exc:
        return None, f"parse error: {exc}"
    if not tokens:
        return None, ""
    if tokens[0] in ("quit", "exit"):
        return None, ""

    head = tokens[0]
    rest = tokens[1:]

    if head == "hello":
        return {"id": _next_id(), "cmd": "hello", "protocol": 1}, ""
    if head == "devices":
        return {"id": _next_id(), "cmd": "devices"}, ""
    if head == "subscribe":
        return {"id": _next_id(), "cmd": "subscribe", "events": rest}, ""
    if head == "unsubscribe":
        return {"id": _next_id(), "cmd": "unsubscribe", "events": rest}, ""
    if head == "shutdown":
        return {"id": _next_id(), "cmd": "shutdown"}, ""

    if head in ("attach", "detach"):
        devices = rest or ["scanner", "scale", "printer"]
        return {"id": _next_id(), "cmd": head, "devices": devices}, ""

    if head == "scan":
        if not rest:
            return None, "usage: scan <code>"
        return _emit({"event": "scan", "code": rest[0]}), ""

    if head == "weight":
        parser = argparse.ArgumentParser(prog="weight", exit_on_error=False)
        parser.add_argument("value", type=float)
        parser.add_argument("--stable", action="store_true", default=True)
        parser.add_argument("--unstable", dest="stable", action="store_false")
        try:
            ns = parser.parse_args(rest)
        except (SystemExit, argparse.ArgumentError) as exc:
            return None, f"usage: weight <value> [--stable|--unstable] ({exc})"
        return _emit({"event": "weight", "value": ns.value, "stable": ns.stable}), ""

    if head == "device":
        if len(rest) < 2:
            return None, "usage: device <id> <state> [--reason <text>]"
        dev, state = rest[0], rest[1]
        reason = ""
        if "--reason" in rest:
            i = rest.index("--reason")
            reason = rest[i + 1] if i + 1 < len(rest) else ""
        return _emit({"event": "device", "device": dev, "state": state, "reason": reason}), ""

    if head == "printer":
        return _printer_command(rest)

    if head == "scale":
        return _scale_command(rest)

    return None, f"unknown command: {head}"


def _printer_command(rest: list[str]) -> tuple[dict[str, Any] | None, str]:
    """Build a printer subcommand request."""
    if not rest:
        return None, "usage: printer <send|queue|status|retry> ..."
    sub = rest[0]
    args = rest[1:]
    if sub == "queue":
        return {"id": _next_id(), "cmd": "print.queue"}, ""
    if sub == "status":
        if not args:
            return None, "usage: printer status <job>"
        return {"id": _next_id(), "cmd": "print.status", "job": args[0]}, ""
    if sub == "retry":
        if not args:
            return None, "usage: printer retry <job>"
        return {"id": _next_id(), "cmd": "print.retry", "job": args[0]}, ""
    if sub == "send":
        if len(args) < 3:
            return None, "usage: printer send <key> <format> <payload> [copies]"
        copies = int(args[3]) if len(args) > 3 else 1
        return (
            {
                "id": _next_id(),
                "cmd": "print",
                "key": args[0],
                "format": args[1],
                "payload": args[2],
                "copies": copies,
            },
            "",
        )
    return None, f"unknown printer subcommand: {sub}"


def _scale_command(rest: list[str]) -> tuple[dict[str, Any] | None, str]:
    """Build a scale subcommand request."""
    if not rest:
        return None, "usage: scale <read|tare> ..."
    sub = rest[0]
    args = rest[1:]
    if sub == "tare":
        return {"id": _next_id(), "cmd": "scale.tare"}, ""
    if sub == "read":
        timeout = int(args[0]) if args else 3000
        return {"id": _next_id(), "cmd": "scale.read", "stable": True, "timeout_ms": timeout}, ""
    return None, f"unknown scale subcommand: {sub}"


_id_counter = 0


def _next_id() -> int:
    """Return a monotonically increasing request id for this process."""
    global _id_counter
    _id_counter += 1
    return _id_counter


def _emit(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap *payload* in an ``emit`` request."""
    payload["id"] = _next_id()
    payload["cmd"] = "emit"
    return payload


# --- Output ------------------------------------------------------------


def format_frame(frame: dict[str, Any]) -> str:
    """Render a frame for human reading."""
    if "event" in frame:
        return f"[event] {json.dumps(frame, ensure_ascii=False)}"
    if frame.get("ok"):
        return f"[ok]    {json.dumps(frame, ensure_ascii=False)}"
    return f"[error] {json.dumps(frame, ensure_ascii=False)}"


# --- Modes -------------------------------------------------------------


def run_one_shot(args: list[str], pipe_name: str) -> int:
    """Run a single command from *args* and exit."""
    app = QCoreApplication(sys.argv)
    client = DebugClient(pipe_name, parent=app)

    if not client.connect():
        print(f"cannot connect to {pipe_name}", file=sys.stderr)
        return 1

    frame, error = build_request(" ".join(shlex.quote(a) for a in args))
    if error:
        print(error, file=sys.stderr)
        return 1
    if frame is None:
        return 0

    done = threading.Event()

    def _on_frame(received: dict[str, Any]) -> None:
        print(format_frame(received))
        # Stop after the matching response arrives.
        if "event" not in received:
            done.set()

    client.frame_received.connect(_on_frame)
    if not client.send(frame):
        return 1

    # Allow a short grace period for follow-up events.
    timer = threading.Timer(0.5, done.set)
    timer.start()
    while not done.is_set():
        app.processEvents()
    timer.cancel()
    return 0


def run_interactive(pipe_name: str) -> int:
    """Run the interactive prompt reading from stdin."""
    app = QCoreApplication(sys.argv)
    client = DebugClient(pipe_name, parent=app)

    if not client.connect():
        print(f"cannot connect to {pipe_name}", file=sys.stderr)
        return 1

    client.frame_received.connect(lambda f: print(format_frame(f)))
    client.disconnected_.connect(lambda: print("--- disconnected ---"))

    # Read stdin on a daemon thread; marshal lines onto the Qt thread.
    prompter = _StdinPrompter()
    prompter.line_ready.connect(lambda line: _handle_line(client, line))
    prompter.start()

    print("connected; commands: attach/detach, scan, weight, device, printer, scale, "
          "subscribe, devices; quit to exit")
    print("вбросить событие можно только в устройство, взятое командой attach")
    return app.exec()


class _StdinPrompter(QObject):
    """Reads stdin on a background thread and signals each line.

    Signals
    -------
    line_ready(str)
        A line read from stdin (Qt-thread-safe).
    """

    line_ready = pyqtSignal(str)

    def start(self) -> None:
        """Start the background reader thread."""
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        """Blocking read loop feeding lines to the Qt thread."""
        while True:
            try:
                line = sys.stdin.readline()
            except (KeyboardInterrupt, EOFError):
                break
            if not line:
                break
            self.line_ready.emit(line.strip())


def _handle_line(client: DebugClient, line: str) -> None:
    """Translate and send one interactive command."""
    if not line:
        return
    if line in ("quit", "exit"):
        QCoreApplication.quit()
        return
    frame, error = build_request(line)
    if error:
        print(error, file=sys.stderr)
        return
    if frame is not None:
        client.send(frame)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="devices.cli")
    parser.add_argument("--pipe", default=None, help="named pipe to connect to")
    parser.add_argument("command", nargs="*", help="one-shot command (omit for interactive)")
    # ключи подкоманд (--reason, --stable) не наши: отдаём их команде как есть
    args, extra = parser.parse_known_args()
    args.command = args.command + extra

    pipe_name = args.pipe
    if pipe_name is None:
        try:
            pipe_name = get_default_config().get("pipe", {}).get("name", DEFAULT_PIPE_NAME)
        except Exception:  # noqa: BLE001
            pipe_name = DEFAULT_PIPE_NAME

    if args.command:
        sys.exit(run_one_shot(args.command, pipe_name))
    sys.exit(run_interactive(pipe_name))


if __name__ == "__main__":
    main()
