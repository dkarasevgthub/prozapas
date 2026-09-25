"""Logging setup for devices.

Configures a RotatingFileHandler (5 files × 2 MB) writing to
%ProgramData%\\ProZapas\\devices.log (or the path specified in config).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


def setup_logging(config: dict[str, Any]) -> None:
    """Configure the root logger for devices.

    Parameters
    ----------
    config:
        The full application config dictionary.  The ``[log]`` section
        is used to determine the log level and file path.
    """
    log_cfg = config.get("log", {})
    level_name: str = log_cfg.get("level", "info").upper()
    log_path: str | None = log_cfg.get("path") or None

    # Resolve log path
    if not log_path:
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        log_dir = Path(program_data) / "ProZapas"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(log_dir / "devices.log")

    numeric_level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger("devices")
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers on repeated calls
    if root_logger.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Rotating file handler: 5 files, 2 MB each
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,  # 2 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Also log to stderr for development convenience
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(numeric_level)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    root_logger.info("Logging initialised at level=%s path=%s", level_name, log_path)
