"""Entry point for devices.

Creates the Qt application, loads configuration from CLI/ENV, initialises
drivers, starts the named-pipe server, and runs the event loop.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from PyQt6.QtCore import QCoreApplication, QTimer

from .config import get_default_config, list_ports, list_printers
from .drivers.fake import FakePrinter, FakeScale, FakeScanner
from .drivers.printer import PrinterDriver
from .drivers.scale import ScaleDriver
from .drivers.scanner import ScannerDriver
from .logging_setup import setup_logging
from .server import DeviceServer
from .slot import DeviceSlot

logger = logging.getLogger(__name__)

SERVICE_NAME = "devices"
SERVICE_VERSION = "0.1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=SERVICE_NAME)
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit.")
    parser.add_argument("--list-printers", action="store_true", help="List printers and exit.")
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=None,
        metavar="SEC",
        help="Override idle_timeout_sec; 0 disables the timeout.",
    )
    # Динамическая конфигурация оборудования
    parser.add_argument("--scanner-port", type=str, default=None, help="Override scanner COM port.")
    parser.add_argument("--scale-port", type=str, default=None, help="Override scale COM port.")
    parser.add_argument("--printer-name", type=str, default=None, help="Override printer name.")
    return parser.parse_args()


def _build_slots(config: dict, app: QCoreApplication) -> tuple[DeviceSlot, DeviceSlot, DeviceSlot]:
    """Create scanner/scale/printer slots with real and fake drivers."""
    scanner_cfg = config.get("scanner", {})
    real_scanner = ScannerDriver(
        port_name=scanner_cfg.get("port", ""),
        baud=int(scanner_cfg.get("baud", 115200)),
        parent=app,
    )
    fake_scanner = FakeScanner(parent=app)
    scanner_slot = DeviceSlot("scanner", real_scanner, fake_scanner, parent=app)

    scale_cfg = config.get("scale", {})
    real_scale = ScaleDriver(
        port=scale_cfg.get("port", ""),
        baud=int(scale_cfg.get("baud", 115200)),
        step_g=float(scale_cfg.get("step_g", 10)),
        command_tare=scale_cfg.get("command_tare", "TARE"),
        command_calib=scale_cfg.get("command_calib", "CALIB"),
        parent=app,
    )
    fake_scale = FakeScale(parent=app)
    scale_slot = DeviceSlot("scale", real_scale, fake_scale, parent=app)

    printer_cfg = config.get("printer", {})
    real_printer = PrinterDriver(
        name=printer_cfg.get("name", ""),
        encoding=printer_cfg.get("encoding", "cp866"),
        output_file=printer_cfg.get("output_file", ""),
        parent=app,
    )
    fake_printer = FakePrinter(parent=app)
    printer_slot = DeviceSlot("printer", real_printer, fake_printer, parent=app)

    return scanner_slot, scale_slot, printer_slot


def main() -> None:
    args = parse_args()

    if args.version:
        print(f"{SERVICE_NAME} v{SERVICE_VERSION}", file=sys.stderr)
        return

    if args.list_ports:
        for port in list_ports():
            print(port)
        return

    if args.list_printers:
        for printer_name in list_printers():
            print(printer_name)
        return

    # Получаем дефолтную конфигурацию (из ENV)
    config = get_default_config()

    # Перекрываем аргументами командной строки, если они переданы
    if args.scanner_port is not None:
        config["scanner"]["port"] = args.scanner_port
    if args.scale_port is not None:
        config["scale"]["port"] = args.scale_port
    if args.printer_name is not None:
        config["printer"]["name"] = args.printer_name
    if args.idle_timeout is not None:
        config["pipe"]["idle_timeout_sec"] = args.idle_timeout

    setup_logging(config)

    app = QCoreApplication(sys.argv)
    app.setApplicationName(SERVICE_NAME)
    app.setApplicationVersion(SERVICE_VERSION)

    scanner_slot, scale_slot, printer_slot = _build_slots(config, app)

    # Open real drivers
    for slot, label in (
        (scanner_slot, "scanner"),
        (scale_slot, "scale"),
        (printer_slot, "printer"),
    ):
        try:
            slot._real.open()
        except Exception as exc:
            logger.error("Failed to open %s: %s", label, exc)

    server = DeviceServer(
        scanner_slot=scanner_slot,
        scale_slot=scale_slot,
        printer_slot=printer_slot,
        parent=app,
    )

    pipe_cfg = config.get("pipe", {})
    pipe_name = pipe_cfg.get("name", "prozapas-devices")
    idle_timeout = float(pipe_cfg.get("idle_timeout_sec", 30))

    if not server.start(pipe_name):
        logger.error("Pipe %s already in use, exiting", pipe_name)
        sys.exit(2)

    idle_timer = QTimer(app)
    idle_timer.setSingleShot(True)
    idle_timer.setInterval(int(idle_timeout * 1000))
    idle_timer.timeout.connect(app.quit)
    if idle_timeout > 0:
        idle_timer.start()

    def _reset_idle() -> None:
        if server.has_clients:
            idle_timer.stop()
        elif idle_timeout > 0:
            idle_timer.start()

    server.client_connected.connect(_reset_idle)
    server.client_disconnected.connect(_reset_idle)
    server.shutdown_requested.connect(app.quit)

    def _on_about_to_quit() -> None:
        logger.info("devices shutting down")
        idle_timer.stop()
        scanner_slot._real.close()
        scale_slot._real.close()
        printer_slot._real.close()
        server.stop()

    app.aboutToQuit.connect(_on_about_to_quit)

    def sigint_handler(signum, frame):
        print("\n⏹ Получен сигнал остановки (Ctrl+C). Завершаем работу...")
        app.quit()

    signal.signal(signal.SIGINT, sigint_handler)

    print("ready", file=sys.stderr)
    logger.info("devices ready (pipe=%s, version=%s)", pipe_name, SERVICE_VERSION)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()