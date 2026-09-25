"""Protocol constants and framing helpers for devices.

This module is the single source of truth shared between the device
service and the ProZapas application: command names, event names,
error codes, device/job states and the protocol version must match on
both sides. It also provides the JSON-over-line framing helpers used
to build and parse frames on the wire.

Wire format
-----------
One JSON object per line, UTF-8, separated by ``\\n``. Three kinds of
messages::

    request  {"id": 17, "cmd": "print", ...}     from client, always with id
    response {"id": 17, "ok": true, ...}         from service, same id
    event    {"event": "scan", ...}              from service, no id

Responses are **flat**: the fields of the reply live at the top level
alongside ``id`` and ``ok`` (see devices-service.md, section 4).
"""

from __future__ import annotations

import json
from typing import Any

# --- Protocol version ---------------------------------------------------

#: The protocol version this service speaks.
PROTOCOL_VERSION: int = 1

#: All protocol versions the service can talk to. A client whose
#: ``hello`` version is not in this list is told it is incompatible.
SUPPORTED_PROTOCOL_VERSIONS: list[int] = [1]

# --- Commands (sent by clients) ----------------------------------------

CMD_HELLO: str = "hello"
CMD_DEVICES: str = "devices"
CMD_SUBSCRIBE: str = "subscribe"
CMD_UNSUBSCRIBE: str = "unsubscribe"
CMD_PRINT: str = "print"
CMD_PRINT_STATUS: str = "print.status"
CMD_PRINT_QUEUE: str = "print.queue"
CMD_PRINT_RETRY: str = "print.retry"
CMD_SCALE_READ: str = "scale.read"
CMD_SCALE_TARE: str = "scale.tare"
CMD_SHUTDOWN: str = "shutdown"

#: Simulator commands for role claiming and event injection.
CMD_ATTACH: str = "attach"
CMD_DETACH: str = "detach"
CMD_EMIT: str = "emit"

# --- Events (sent by the service on its own initiative) ----------------

EVENT_SCAN: str = "scan"
EVENT_WEIGHT: str = "weight"
EVENT_DEVICE: str = "device"
EVENT_JOB: str = "job"
EVENT_PRINT_JOB: str = "print.job"

# --- Error codes --------------------------------------------------------

ERR_BAD_REQUEST: str = "bad_request"
ERR_UNKNOWN_COMMAND: str = "unknown_command"
ERR_NO_DEVICE: str = "no_device"
ERR_PRINTER_OFFLINE: str = "printer_offline"
ERR_SCALE_TIMEOUT: str = "scale_timeout"
ERR_BUSY: str = "busy"
ERR_NOT_ATTACHED: str = "not_attached"
ERR_INTERNAL: str = "internal"
ERR_INCOMPATIBLE: str = "incompatible"

# --- Device states ------------------------------------------------------

STATE_ONLINE: str = "online"
STATE_OFFLINE: str = "offline"
STATE_ERROR: str = "error"

#: Valid device states.
DEVICE_STATES: set[str] = {STATE_ONLINE, STATE_OFFLINE, STATE_ERROR}

# --- Job states ---------------------------------------------------------

JOB_QUEUED: str = "queued"
JOB_PRINTING: str = "printing"
JOB_DONE: str = "done"
JOB_FAILED: str = "failed"

#: Valid job states, in lifecycle order.
JOB_STATES: list[str] = [JOB_QUEUED, JOB_PRINTING, JOB_DONE, JOB_FAILED]

# --- Supported label formats -------------------------------------------

#: Print formats accepted by the ``print`` command.
PRINT_FORMATS: set[str] = {"zpl", "tspl", "raw"}

# --- Aggregates ---------------------------------------------------------

#: Commands that may legally appear in a client request.
ALL_COMMANDS: set[str] = {
    CMD_HELLO,
    CMD_DEVICES,
    CMD_SUBSCRIBE,
    CMD_UNSUBSCRIBE,
    CMD_PRINT,
    CMD_PRINT_STATUS,
    CMD_PRINT_QUEUE,
    CMD_PRINT_RETRY,
    CMD_SCALE_READ,
    CMD_SCALE_TARE,
    CMD_SHUTDOWN,
    CMD_ATTACH,
    CMD_DETACH,
    CMD_EMIT,
}

#: All event types a client may subscribe to.
ALL_EVENTS: set[str] = {
    EVENT_SCAN,
    EVENT_WEIGHT,
    EVENT_DEVICE,
    EVENT_JOB,
    # Задание на печать, развёрнутое в сторону симулятора. Без него `subscribe`
    # молча отбрасывал подписку, и задание не доезжало до подменного принтера.
    EVENT_PRINT_JOB,
}


# --- Framing helpers ----------------------------------------------------


def parse_frame(line: str | bytes) -> dict[str, Any] | None:
    """Parse one wire frame (a JSON object) from *line*.

    Returns the decoded dict, or ``None`` if *line* is not valid JSON
    (blank lines and non-JSON bytes are ignored, not raised on).
    """
    if isinstance(line, (bytes, bytearray)):
        line = bytes(line).decode("utf-8", errors="replace")
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def make_response(req_id: int | str, ok: bool, **fields: Any) -> dict[str, Any]:
    """Build a flat response frame.

    Extra keyword arguments are placed at the top level alongside
    ``id`` and ``ok``. For error replies prefer :func:`make_error`.

    IMPORTANT: Do not pass 'ok' in **fields, it's already a positional parameter.
    """
    # Убеждаемся, что ok не передан в fields
    if "ok" in fields:
        raise ValueError("'ok' should not be passed as a keyword argument, use positional parameter")

    frame: dict[str, Any] = {"id": req_id, "ok": ok}
    frame.update(fields)
    return frame

def make_error(
    req_id: int | str, code: str, message: str, **extra: Any
) -> dict[str, Any]:
    """Build an error response frame.

    The error is reported as ``{"error": {"code", "message"}}`` so the
    application can branch on the code while showing the message to the
    user.
    """
    error: dict[str, Any] = {"code": code, "message": message}
    error.update(extra)
    # Используем make_response, но ok передаём как позиционный аргумент
    return make_response(req_id, False, error=error)


def make_event(name: str, **fields: Any) -> dict[str, Any]:
    """Build an event frame (no ``id``)."""
    frame: dict[str, Any] = {"event": name}
    frame.update(fields)
    return frame


def encode_frame(frame: dict[str, Any]) -> bytes:
    """Serialize *frame* to a UTF-8 line terminated by ``\\n``."""
    return (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")