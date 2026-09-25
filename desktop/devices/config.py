"""Configuration management for devices.

No filesystem config files are used. Configuration is passed via CLI
arguments or environment variables. Empty values trigger auto-search.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def get_default_config() -> dict:
    """Return default configuration dictionary (auto-search mode)."""
    return {
        "pipe": {
            "name": os.environ.get("PROZAPAS_PIPE_NAME", "prozapas-devices"),
            "idle_timeout_sec": float(os.environ.get("PROZAPAS_IDLE_TIMEOUT", 30)),
        },
        "scanner": {
            "port": os.environ.get("PROZAPAS_SCANNER_PORT", ""),
            "baud": int(os.environ.get("PROZAPAS_SCANNER_BAUD", 115200)),
        },
        "scale": {
            "port": os.environ.get("PROZAPAS_SCALE_PORT", ""),
            "baud": int(os.environ.get("PROZAPAS_SCALE_BAUD", 115200)),
            "step_g": float(os.environ.get("PROZAPAS_SCALE_STEP_G", 10)),
            "command_tare": "TARE",
            "command_calib": "CALIB",
        },
        "printer": {
            "name": os.environ.get("PROZAPAS_PRINTER_NAME", ""),
            "encoding": "cp866",
            "output_file": os.environ.get("PROZAPAS_PRINTER_OUTPUT_FILE", ""),
        },
        "log": {
            "level": os.environ.get("PROZAPAS_LOG_LEVEL", "info"),
            "path": "",  # Empty means stdout
        },
    }


def list_ports() -> list[str]:
    """Serial ports with enough detail to tell one device from another."""
    try:
        import serial.tools.list_ports  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("serial.tools.list_ports not available")
        return []

    ports = []
    for port in sorted(serial.tools.list_ports.comports(), key=lambda p: p.device):
        line = port.device
        if port.description and port.description != "n/a":
            line += f" — {port.description}"
        if port.vid is not None:
            line += f" ({port.vid:04X}:{port.pid:04X})"
        ports.append(line)
    return ports


def list_printers() -> list[str]:
    """Return a list of printer names using ``win32print.EnumPrinters``."""
    try:
        import win32print  # type: ignore[import-untyped]

        printers = [
            p[2]
            for p in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS,
            )
        ]
        return printers
    except Exception:  # noqa: BLE001
        logger.debug("win32print not available")
        return []