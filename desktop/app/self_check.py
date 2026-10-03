"""End-to-end diagnostic for both source and frozen desktop builds."""
from pathlib import Path
import json
import os
import time

from PyQt6.QtCore import Qt, QTimer

from . import api, config, devices
from .resources import APP_ICON
from .service_host import host


def run_check(app, window, simulator, report_path):
    report = Path(report_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    checks = {}
    observed = {"scan": False, "weight": False, "print": False}
    phase = ["windows"]
    detach_roles = [("printer", simulator.printer_sim_cb),
                    ("scanner", simulator.scan_sim_cb),
                    ("scale", simulator.scale_sim_cb)] if simulator else []
    detach_index = [0]
    service_process = host._process
    print_key = f"self-check-{os.getpid()}"
    timer = QTimer(app)
    timer.setInterval(100)

    def record_scan(code):
        observed["scan"] = code == "SMOKE-123"

    def record_weight(value, stable):
        observed["weight"] = value == 0.5 and stable

    def record_frame(entry):
        frame = json.loads(entry["text"])
        if frame.get("event") == "job" and frame.get("state") == "done":
            observed["print"] = True

    devices.client.scanned.connect(record_scan)
    devices.client.weight_read.connect(record_weight)
    devices.client.logged.connect(record_frame)

    def finish(error=None):
        timer.stop()
        checks["error"] = error
        window.close()

    def on_exit():
        checks["service_stopped"] = (service_process is not None
                                      and not host._alive())
        checks["ok"] = checks.get("error") is None and checks["service_stopped"]
        report.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")

    # Runs after main.py's normal service shutdown handlers.
    app.aboutToQuit.connect(on_exit)

    def tick():
        try:
            if time.monotonic() - started > 45:
                raise TimeoutError(f"Desktop self-check timed out at {phase[0]}")
            if simulator is None:
                raise AssertionError("Simulator did not open")
            if phase[0] == "windows":
                assert simulator.parentWidget() is None
                assert simulator.windowModality() == Qt.WindowModality.NonModal
                if os.name == "nt":
                    import ctypes
                    from ctypes import wintypes
                    get_owner = ctypes.windll.user32.GetWindow
                    get_owner.argtypes = [wintypes.HWND, wintypes.UINT]
                    get_owner.restype = wintypes.HWND
                    assert not get_owner(int(simulator.winId()), 4)  # GW_OWNER
                window.showMinimized()
                phase[0] = "minimized"
            elif phase[0] == "minimized":
                assert window.isMinimized()
                assert simulator.isVisible() and not simulator.isMinimized()
                window.showNormal()
                from .widgets.dialog import confirm_dialog
                dialog_checks = {}

                def inspect_dialog():
                    try:
                        dialog = app.activeModalWidget()
                        assert dialog is not None
                        assert dialog.windowModality() == Qt.WindowModality.WindowModal
                        if os.name == "nt":
                            import ctypes
                            from ctypes import wintypes
                            is_enabled = ctypes.windll.user32.IsWindowEnabled
                            is_enabled.argtypes = [wintypes.HWND]
                            is_enabled.restype = wintypes.BOOL
                            assert is_enabled(int(simulator.winId()))
                            assert not is_enabled(int(window.winId()))
                        dialog_checks["ok"] = True
                    except Exception as exc:
                        dialog_checks["error"] = f"{type(exc).__name__}: {exc}"
                    finally:
                        if app.activeModalWidget() is not None:
                            app.activeModalWidget().reject()

                QTimer.singleShot(100, inspect_dialog)
                confirm_dialog(window, "Проверка окон", "Симулятор остаётся доступен.")
                assert dialog_checks.get("ok"), dialog_checks.get("error")
                checks["simulator_independent_window"] = True
                checks["simulator_available_during_dialog"] = True
                phase[0] = "connect"
            elif phase[0] == "connect":
                if simulator.attached_devices != {"scanner", "scale", "printer"}:
                    return
                if not devices.client.connected or any(v != "online" for v in devices.states().values()):
                    return
                checks["simulator_auto_connected"] = True
                assert simulator.status_label.text() == "Подключено"
                detach_roles[detach_index[0]][1].setChecked(False)
                phase[0] = "detach"
            elif phase[0] == "detach":
                role, checkbox = detach_roles[detach_index[0]]
                if simulator.attached_devices != {"scanner", "scale", "printer"} - {role}:
                    return
                assert devices.states()[role] == devices.OFFLINE, (
                    f"{role} still available after detach: {devices.states()}"
                )
                checks[f"{role}_offline_after_detach"] = True
                checkbox.setChecked(True)
                phase[0] = "reattach"
            elif phase[0] == "reattach":
                if simulator.attached_devices != {"scanner", "scale", "printer"}:
                    return
                if any(v != "online" for v in devices.states().values()):
                    return
                detach_index[0] += 1
                if detach_index[0] < len(detach_roles):
                    detach_roles[detach_index[0]][1].setChecked(False)
                    phase[0] = "detach"
                    return
                checks["toggle_emulation"] = True
                checks["devices"] = devices.states()
                checks["service_owned"] = host.owned
                checks["api"] = api.transport.base_url
                assert config.flag("PROZAPAS_TLS_VERIFY")
                assert Path(APP_ICON).exists()
                assert api.client.health()["status"] == "ok"
                checks["https"] = True
                login = os.environ.get("PROZAPAS_SELF_CHECK_LOGIN")
                if login:
                    from .session import session
                    session.login(login, os.environ["PROZAPAS_SELF_CHECK_PASSWORD"])
                    window._enter_app()
                    checks["login"] = session.user["login"]
                devices.client.subscribe_weight(True)
                simulator.scan_code_edit.setText("SMOKE-123")
                simulator.send_scan()
                simulator.weight_edit.setText("500")
                simulator.stable_cb.setChecked(True)
                simulator.send_weight()
                assert devices.client.print_label(
                    print_key, "^XA^FO10,10^FDПроверка этикетки^FS^XZ"
                )
                phase[0] = "events"
            elif phase[0] == "events" and all(observed.values()):
                timer.stop()
                assert "Проверка этикетки" in simulator.zpl_text.toPlainText()
                checks.update(observed)
                from .session import session
                if session.authorized:
                    from PyQt6.QtWidgets import QPushButton
                    from .widgets.printer_settings import PrinterSettingsDialog
                    settings_button = window.findChild(QPushButton, "printerSettingsButton")
                    logs_button = window.findChild(QPushButton, "deviceLogsButton")
                    assert (settings_button is not None) == session.is_admin
                    assert (logs_button is not None) == session.is_admin
                    if session.is_admin:
                        from .widgets import devicelog
                        logs_button.click()
                        assert devicelog._window is not None and devicelog._window.isVisible()
                        devicelog.close_device_log()
                        dialog_checks = {}

                        def inspect_settings():
                            dialog = app.activeModalWidget()
                            try:
                                assert isinstance(dialog, PrinterSettingsDialog)
                                assert dialog.printers.count() >= 1
                                dialog.grab().save(str(report.parent / "printer-settings.png"))
                                dialog_checks["ok"] = True
                            except Exception as exc:
                                dialog_checks["error"] = str(exc)
                            finally:
                                if dialog is not None:
                                    dialog.reject()

                        QTimer.singleShot(200, inspect_settings)
                        settings_button.click()
                        assert dialog_checks.get("ok"), dialog_checks.get("error")
                    checks["printer_settings_role_access"] = True
                    checks["device_logs_role_access"] = True
                window.grab().save(str(report.parent / "application.png"))
                simulator.grab().save(str(report.parent / "simulator.png"))
                if session.authorized:
                    session.logout()
                finish()
        except Exception as exc:
            finish(f"{type(exc).__name__}: {exc}")

    timer.timeout.connect(tick)
    timer.start()
