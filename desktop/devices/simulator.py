"""
Simulator GUI для ручного тестирования службы devices.
Использует PyQt6 и QLocalSocket для связи через именованный канал.
"""

import datetime
import json
import random
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtNetwork import QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# Симулятор запускают и модулем (python -m devices.simulator), и просто
# файлом из редактора — во втором случае пакета нет, и его нужно найти самому.
try:
    from . import protocol
except ImportError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from devices import protocol  # noqa: F401


class SimulatorWindow(QWidget):
    #: Сколько тактов груз «оседает», прежде чем весы объявят вес устойчивым.
    STREAM_SETTLE_TICKS = 12          # ≈1.2 секунды при 10 Гц

    def __init__(self):
        super().__init__()
        self.socket = None
        self.next_id = 1
        self.device_states = {
            "scanner": None,   # None, "online", "offline", "error"
            "scale": None,
            "printer": None,
        }
        # Отслеживание подключенных ролей
        self.attached_devices = set()

        # Поток показаний весов — настоящие весы шлют ~10 раз в секунду
        self._settling = 0
        self.stream_timer = QTimer(self)
        self.stream_timer.setInterval(100)
        self.stream_timer.timeout.connect(self._stream_tick)

        self.init_ui()
        self.setWindowTitle("Devices Service Simulator")
        self.resize(900, 850)
        self._update_ui_states()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # --- 1. Панель подключения ---
        conn_group = QGroupBox("Подключение к службе")
        conn_layout = QHBoxLayout()
        self.pipe_edit = QLineEdit("prozapas-devices")
        self.connect_btn = QPushButton("Подключиться")
        self.disconnect_btn = QPushButton("Отключиться")
        self.status_label = QLabel("Отключено")
        self.status_label.setStyleSheet("font-weight: bold; color: gray;")
        conn_layout.addWidget(QLabel("Канал:"))
        conn_layout.addWidget(self.pipe_edit)
        conn_layout.addWidget(self.connect_btn)
        conn_layout.addWidget(self.disconnect_btn)
        conn_layout.addWidget(self.status_label)
        conn_layout.addStretch()
        conn_group.setLayout(conn_layout)
        main_layout.addWidget(conn_group)

        # --- 2. Панели управления устройствами ---
        devices_container = QHBoxLayout()
        devices_container.addWidget(self._build_scanner_panel())
        devices_container.addWidget(self._build_scale_panel())
        devices_container.addWidget(self._build_printer_panel())
        main_layout.addLayout(devices_container)

        # --- 3. Индикаторы состояний устройств ---
        status_group = QGroupBox("Состояния в службе")
        status_layout = QHBoxLayout()
        self.ind_scan = QLabel("scanner: неизвестно")
        self.ind_weight = QLabel("scale: неизвестно")
        self.ind_printer = QLabel("printer: неизвестно")
        for lbl in (self.ind_scan, self.ind_weight, self.ind_printer):
            lbl.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Sunken)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setMinimumWidth(100)
            lbl.setStyleSheet("background-color: lightgray;")
            status_layout.addWidget(lbl)
        status_layout.addStretch()
        status_group.setLayout(status_layout)
        main_layout.addWidget(status_group)

        # --- 4. Панель команд протокола (отладка) ---
        proto_group = QGroupBox("Команды протокола (Отладка)")
        proto_layout = QHBoxLayout()
        self.hello_btn = QPushButton("Hello")
        self.devices_btn = QPushButton("Devices")
        self.subscribe_btn = QPushButton("Подписаться на все")
        self.shutdown_btn = QPushButton("Shutdown")
        proto_layout.addWidget(self.hello_btn)
        proto_layout.addWidget(self.devices_btn)
        proto_layout.addWidget(self.subscribe_btn)
        proto_layout.addWidget(self.shutdown_btn)
        proto_layout.addStretch()
        proto_group.setLayout(proto_layout)
        main_layout.addWidget(proto_group)

        # --- 5. Окно ZPL (входящие задания) ---
        zpl_group = QGroupBox("Входящие задания печати (Print Jobs)")
        zpl_layout = QVBoxLayout()
        self.zpl_text = QTextEdit()
        self.zpl_text.setFontFamily("monospace")
        self.zpl_text.setReadOnly(True)
        zpl_layout.addWidget(self.zpl_text)
        zpl_group.setLayout(zpl_layout)
        main_layout.addWidget(zpl_group)

        # --- 6. Лог-окно ---
        log_group = QGroupBox("Лог")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setFontFamily("monospace")
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # Подключение сигналов
        self.connect_btn.clicked.connect(self.connect_to_server)
        self.disconnect_btn.clicked.connect(self.disconnect_from_server)

        self.hello_btn.clicked.connect(lambda: self.send_request("hello", protocol=1, client="simulator"))
        self.devices_btn.clicked.connect(lambda: self.send_request("devices"))
        self.subscribe_btn.clicked.connect(lambda: self.send_request("subscribe", events=["scan", "weight", "device", "job", "print.job"]))
        self.shutdown_btn.clicked.connect(lambda: self.send_request("shutdown"))

    def _build_scanner_panel(self):
        group = QGroupBox("Сканер")
        layout = QVBoxLayout()

        self.scan_sim_cb = QCheckBox("Включить эмуляцию (Attach)")
        self.scan_sim_cb.toggled.connect(lambda c: self.handle_sim_toggled("scanner", c))
        layout.addWidget(self.scan_sim_cb)

        # Вброс скана
        scan_row = QHBoxLayout()
        self.scan_code_edit = QLineEdit()
        self.scan_code_edit.setPlaceholderText("Штрихкод")
        self.scan_btn = QPushButton("Сканировать")
        self.scan_btn.clicked.connect(self.send_scan)
        scan_row.addWidget(self.scan_code_edit)
        scan_row.addWidget(self.scan_btn)
        layout.addLayout(scan_row)

        # Смена состояния
        state_row = QHBoxLayout()
        self.scan_state_combo = QComboBox()
        self.scan_state_combo.addItems(["online", "offline", "error"])
        self.scan_state_btn = QPushButton("Сменить состояние")
        self.scan_state_btn.clicked.connect(lambda: self.send_device_state("scanner", self.scan_state_combo.currentText()))
        state_row.addWidget(self.scan_state_combo)
        state_row.addWidget(self.scan_state_btn)
        layout.addLayout(state_row)

        layout.addStretch()
        group.setLayout(layout)
        return group

    def _build_scale_panel(self):
        group = QGroupBox("Весы")
        layout = QVBoxLayout()

        self.scale_sim_cb = QCheckBox("Включить эмуляцию (Attach)")
        self.scale_sim_cb.toggled.connect(lambda c: self.handle_sim_toggled("scale", c))
        layout.addWidget(self.scale_sim_cb)

        # Вброс веса
        weight_row = QHBoxLayout()
        self.weight_edit = QLineEdit()
        self.weight_edit.setPlaceholderText("Вес (г)")
        self.stable_cb = QCheckBox("Стабильно")
        self.stable_cb.setChecked(True)
        self.weight_btn = QPushButton("Взвесить")
        self.weight_btn.clicked.connect(self.send_weight)
        weight_row.addWidget(self.weight_edit)
        weight_row.addWidget(self.stable_cb)
        weight_row.addWidget(self.weight_btn)
        layout.addLayout(weight_row)

        # Поток показаний: разовое «Взвесить» не воспроизводит живые весы —
        # там цифра дрожит и лишь потом замирает. Без потока не проверить ни
        # обновление панели, ни переход «неустойчиво → устойчиво».
        stream_row = QHBoxLayout()
        self.stream_cb = QCheckBox("Поток 10 Гц")
        self.stream_cb.toggled.connect(self.toggle_stream)
        self.settle_btn = QPushButton("Стабилизировать")
        self.settle_btn.clicked.connect(self.settle_stream)
        stream_row.addWidget(self.stream_cb)
        stream_row.addWidget(self.settle_btn)
        stream_row.addStretch(1)
        layout.addLayout(stream_row)

        # Смена состояния
        state_row = QHBoxLayout()
        self.scale_state_combo = QComboBox()
        self.scale_state_combo.addItems(["online", "offline", "error"])
        self.scale_state_btn = QPushButton("Сменить состояние")
        self.scale_state_btn.clicked.connect(lambda: self.send_device_state("scale", self.scale_state_combo.currentText()))
        state_row.addWidget(self.scale_state_combo)
        state_row.addWidget(self.scale_state_btn)
        layout.addLayout(state_row)

        # Команды весов
        cmd_row = QHBoxLayout()
        self.scale_read_btn = QPushButton("Прочитать вес")
        self.scale_tare_btn = QPushButton("Тара")
        self.scale_read_btn.clicked.connect(lambda: self.send_request("scale.read", stable=True, timeout_ms=5000))
        self.scale_tare_btn.clicked.connect(lambda: self.send_request("scale.tare"))
        cmd_row.addWidget(self.scale_read_btn)
        cmd_row.addWidget(self.scale_tare_btn)
        layout.addLayout(cmd_row)

        layout.addStretch()
        group.setLayout(layout)
        return group

    def _build_printer_panel(self):
        group = QGroupBox("Принтер")
        layout = QVBoxLayout()

        self.printer_sim_cb = QCheckBox("Включить эмуляцию (Attach)")
        self.printer_sim_cb.toggled.connect(lambda c: self.handle_sim_toggled("printer", c))
        layout.addWidget(self.printer_sim_cb)

        # Смена состояния
        state_row = QHBoxLayout()
        self.printer_state_combo = QComboBox()
        self.printer_state_combo.addItems(["online", "offline", "error"])
        self.printer_state_btn = QPushButton("Сменить состояние")
        self.printer_state_btn.clicked.connect(lambda: self.send_device_state("printer", self.printer_state_combo.currentText()))
        state_row.addWidget(self.printer_state_combo)
        state_row.addWidget(self.printer_state_btn)
        layout.addLayout(state_row)

        # Команды печати (отладка отправки)
        layout.addWidget(QLabel("Отправка тестовой печати:"))
        print_row = QHBoxLayout()
        self.print_key_edit = QLineEdit("test-box")
        self.print_btn = QPushButton("Напечатать")
        self.print_btn.clicked.connect(self.send_print)
        print_row.addWidget(self.print_key_edit)
        print_row.addWidget(self.print_btn)
        layout.addLayout(print_row)

        layout.addStretch()
        group.setLayout(layout)
        return group

    def _update_ui_states(self):
        """Блокировка кнопок в зависимости от статуса attach."""
        is_connected = self.socket is not None and self.socket.state() == QLocalSocket.LocalSocketState.ConnectedState

        widgets = [
            (self.scan_sim_cb, self.scan_code_edit, self.scan_btn, self.scan_state_combo, self.scan_state_btn),
            (self.scale_sim_cb, self.weight_edit, self.stable_cb, self.weight_btn,
             self.stream_cb, self.settle_btn,
             self.scale_state_combo, self.scale_state_btn, self.scale_read_btn, self.scale_tare_btn),
            (self.printer_sim_cb, self.printer_state_combo, self.printer_state_btn, self.print_key_edit, self.print_btn)
        ]

        for group in widgets:
            main_cb = group[0]
            others = group[1:]

            # Блокируем всё, если не подключены к серверу
            for w in group:
                w.setEnabled(is_connected)

            # Если подключены, блокируем доп. кнопки, если не стоит галочка attach
            if is_connected:
                for w in others:
                    w.setEnabled(main_cb.isChecked())

    # ---------- Логика Attach/Detach ----------
    def handle_sim_toggled(self, device, checked):
        if checked:
            self.send_request("attach", devices=[device])
        else:
            if device == "scale":
                self.stream_cb.setChecked(False)   # отдали весы — поток ни к чему
            self.send_request("detach", devices=[device])
        self._update_ui_states()

    # ---------- Сокет и отправка ----------
    def connect_to_server(self):
        if self.socket and self.socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self.log("Уже подключено", "info")
            return
        pipe_name = self.pipe_edit.text().strip() or "prozapas-devices"
        self.socket = QLocalSocket()
        self.socket.connected.connect(self.on_connected)
        self.socket.disconnected.connect(self.on_disconnected)
        self.socket.errorOccurred.connect(self.on_error)
        self.socket.readyRead.connect(self.on_ready_read)
        self.socket.connectToServer(pipe_name)
        self.status_label.setText("Подключение...")
        self.status_label.setStyleSheet("color: orange;")

    def disconnect_from_server(self):
        if self.socket:
            # Сбрасываем галочки при отключении
            self.scan_sim_cb.setChecked(False)
            self.scale_sim_cb.setChecked(False)
            self.printer_sim_cb.setChecked(False)
            self.socket.disconnectFromServer()
        else:
            self.status_label.setText("Отключено")
            self.status_label.setStyleSheet("color: gray;")
            self._update_ui_states()

    def on_connected(self):
        self.status_label.setText("Подключено")
        self.status_label.setStyleSheet("color: green;")
        self.log("Подключено к каналу", "info")
        self.send_request("devices")
        self.send_request("subscribe", events=["scan", "weight", "device", "job", "print.job"])
        self._update_ui_states()

    def on_disconnected(self):
        self.status_label.setText("Отключено")
        self.status_label.setStyleSheet("color: gray;")
        self.log("Отключено от канала", "info")
        self.stream_cb.setChecked(False)   # иначе поток будет писать в мёртвый сокет
        if self.socket:
            self.socket.deleteLater()
            self.socket = None
        self.attached_devices.clear()
        self._update_ui_states()

    def on_error(self, error):
        if error == QLocalSocket.LocalSocketError.ServerNotFoundError:
            msg = "канал не найден — служба не запущена (python -m desktop.devices)"
        else:
            msg = self.socket.errorString() if self.socket else str(error)
        self.status_label.setText("Ошибка")
        self.status_label.setStyleSheet("color: red;")
        self.log(f"Ошибка сокета: {msg}", "error")

    def on_ready_read(self):
        if not self.socket:
            return
        data = self.socket.readAll().data().decode('utf-8', errors='ignore')
        for line in data.split('\n'):
            line = line.strip()
            if line:
                self.process_response(line)

    def send_request(self, cmd, **kwargs):
        if not self.socket or self.socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            self.log("Не подключено к серверу", "error")
            return
        req = {"id": self.next_id, "cmd": cmd}
        req.update(kwargs)
        self.next_id += 1
        payload = json.dumps(req, ensure_ascii=False, separators=(',', ':')) + '\n'
        try:
            self.socket.write(payload.encode('utf-8'))
            self.socket.flush()
            self.log(f"{json.dumps(req, ensure_ascii=False)}", "sent")
        except Exception as e:
            self.log(f"Ошибка отправки: {e}", "error")

    # ---------- Обработка ответов ----------
    def process_response(self, line):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            self.log(f"← [невалидный JSON] {line}", "error")
            return

        if "event" in data:
            event = data["event"]
            if event == "device":
                self.handle_device_event(data)
            elif event == "print.job":
                self.handle_print_job(data)
            self.log(f"{json.dumps(data, ensure_ascii=False)}", "received")
            return

        # Обработка ответов на attach/detach
        req_id = data.get("id")
        if data.get("ok"):
            # Служба в ответе на attach/detach присылает актуальный список ролей —
            # берём его целиком, чтобы своё представление не разъезжалось с её.
            if "attached" in data:
                self.attached_devices = set(data["attached"])
        elif "error" in data:
            err_code = data["error"].get("code")
            if err_code == "busy":
                self.log("Устройство уже занято другим клиентом", "error")
                # Откатываем галочку
                # Определяем, какое устройство пытались занять
                # (упрощенный подход: просто снимаем все галочки, которые не подтверждены)
            elif err_code == "not_attached":
                self.log("Сначала включите эмуляцию устройства", "error")
            self.log(f"{json.dumps(data, ensure_ascii=False)}", "error")
            return

        devices = data.get("devices")
        if isinstance(devices, dict):
            for dev, state in devices.items():
                if dev in self.device_states:
                    self.device_states[dev] = state
            self.update_device_indicators()

        self.log(f"{json.dumps(data, ensure_ascii=False)}", "received")

    def handle_device_event(self, data):
        device = data.get("device") or data.get("id")
        status = data.get("state") or data.get("status")
        reason = data.get("reason", "")
        if device not in self.device_states:
            return
        self.device_states[device] = status
        self.update_device_indicators()
        log_msg = f"событие device: {device} -> {status}"
        if reason:
            log_msg += f" (причина: {reason})"
        self.log(log_msg, "received")

    def handle_print_job(self, data):
        job_id = data.get("job", "")
        payload = data.get("payload", "")
        self.zpl_text.append(f"--- Job: {job_id} ---\n{payload}\n")
        # Авто-подтверждение задания (Auto-ack)
        self.send_request("emit", event="job", job=job_id, state="done")

    def update_device_indicators(self):
        mapping = {
            "scanner": self.ind_scan,
            "scale": self.ind_weight,
            "printer": self.ind_printer,
        }
        for dev, lbl in mapping.items():
            state = self.device_states.get(dev)
            if state is None:
                lbl.setText(f"{dev}: неизвестно")
                lbl.setStyleSheet("background-color: lightgray;")
            elif state == "online":
                lbl.setText(f"{dev}: online")
                lbl.setStyleSheet("background-color: lightgreen;")
            elif state == "offline":
                lbl.setText(f"{dev}: offline")
                lbl.setStyleSheet("background-color: lightcoral;")
            elif state == "error":
                lbl.setText(f"{dev}: error")
                lbl.setStyleSheet("background-color: yellow;")
            else:
                lbl.setText(f"{dev}: {state}")
                lbl.setStyleSheet("background-color: lightgray;")

    # ---------- Логирование ----------
    def log(self, message, msg_type="info"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = ""
        if msg_type == "sent":
            prefix = "→"
        elif msg_type == "received":
            prefix = "←"
        elif msg_type == "error":
            prefix = "!"
        elif msg_type == "info":
            prefix = "i"
        full = f"[{timestamp}] {prefix} {message}"
        if msg_type == "error":
            full = f'<font color="red">{full}</font>'
        self.log_text.append(full)
        cursor = self.log_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    # ---------- Команды от кнопок ----------
    def send_scan(self):
        code = self.scan_code_edit.text().strip()
        if not code:
            self.log("Введите код скана", "error")
            return
        self.send_request("emit", event="scan", code=code)

    def send_weight(self):
        val_text = self.weight_edit.text().strip()
        if not val_text:
            self.log("Введите вес", "error")
            return
        try:
            value = float(val_text)
        except ValueError:
            self.log("Вес должен быть числом", "error")
            return
        stable = self.stable_cb.isChecked()
        self.send_request("emit", event="weight", value=value, unit="g", stable=stable)

    # ---------- Поток показаний ----------
    def toggle_stream(self, on):
        """Включить или выключить поток веса."""
        if not on:
            self.stream_timer.stop()
            self.log("Поток весов остановлен")
            return
        if not self.weight_edit.text().strip():
            self.log("Введите вес, вокруг которого дрожать", "error")
            self.stream_cb.setChecked(False)
            return
        self._settling = self.STREAM_SETTLE_TICKS
        self.stream_timer.start()
        self.log("Поток весов запущен: 10 раз в секунду")

    def settle_stream(self):
        """Прекратить дрожание — как будто груз успокоился на платформе."""
        self._settling = 0

    def _stream_tick(self):
        """Одно показание: пока груз «оседает» — дрожит и неустойчиво."""
        try:
            base = float(self.weight_edit.text().strip())
        except ValueError:
            self.stream_cb.setChecked(False)
            return
        if self._settling > 0:
            self._settling -= 1
            value, stable = base + random.uniform(-40.0, 40.0), False
        else:
            value, stable = base, True
        self.send_request("emit", event="weight",
                          value=round(value, 1), unit="g", stable=stable)

    def send_device_state(self, device, state):
        self.send_request("emit", event="device", device=device, state=state, reason="manual override")

    def send_print(self):
        key = self.print_key_edit.text().strip()
        if not key:
            self.log("Введите ключ", "error")
            return
        payload = "^XA^FO50,50^ADN,36,20^FDTest^FS^XZ"
        self.send_request("print", key=key, format="zpl", payload=payload, copies=1)

    def closeEvent(self, event):
        self.disconnect_from_server()
        event.accept()


def main():
    app = QApplication(sys.argv)
    win = SimulatorWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()