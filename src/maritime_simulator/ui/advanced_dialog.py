# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from .network_help import LOCAL_BIND_HELP, REMOTE_TCP_HELP

from collections.abc import Callable
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from .dialogs import OpaqueDialog, show_warning

from maritime_simulator.protocols.advanced import (
    ADVANCED_PROTOCOLS,
    XDR_PRESETS,
    encode_advanced_protocol,
    supports_auto,
)
from maritime_simulator.protocols.iec61162 import IEC61162Codec
from maritime_simulator.protocols.sensors import SensorProjector


def _pulse(button: QPushButton) -> None:
    if not isValid(button):
        return
    button.setProperty("feedback", True)
    button.style().unpolish(button)
    button.style().polish(button)

    def clear() -> None:
        if not isValid(button):
            return
        button.setProperty("feedback", False)
        button.style().unpolish(button)
        button.style().polish(button)

    # Bind the callback to the Qt object lifetime. Replacing an Advanced row
    # must cancel its pending visual feedback instead of touching a deleted C++ widget.
    QTimer.singleShot(170, button, clear)


_CHOICE_LABELS = {
    "A": "A - Valid / Active",
    "V": "V - Invalid",
}
_XDR_TYPE_LABELS = {
    "A": "A - Angular displacement",
    "B": "B - Absolute humidity",
    "C": "C - Temperature",
    "D": "D - Linear displacement",
    "E": "E - Fluid level",
    "F": "F - Frequency",
    "G": "G - Generic",
    "H": "H - Relative humidity",
    "I": "I - Electrical current",
    "L": "L - Salinity",
    "N": "N - Force",
    "P": "P - Pressure",
    "R": "R - Flow rate",
    "S": "S - Switch / valve",
    "T": "T - Tachometer",
    "U": "U - Voltage",
    "V": "V - Volume",
    "W": "W - Dew point",
}
_XDR_UNITS = {
    "A": (("D", "D - Degrees"), ("P", "P - Percent of full range")),
    "B": (("K", "K - Kilograms per cubic metre"),),
    "C": (("C", "C - Degrees Celsius"), ("K", "K - Degrees Kelvin")),
    "D": (("M", "M - Metres"), ("P", "P - Percent of full range")),
    "E": (("M", "M - Cubic metres"), ("P", "P - Percent of full range")),
    "F": (("H", "H - Hertz"),),
    "G": (("", "(null field)"), ("P", "P - Percent (legacy control)")),
    "H": (("P", "P - Percent"),),
    "I": (("A", "A - Amperes"),),
    "L": (("S", "S - Parts per thousand"),),
    "N": (("N", "N - Newtons"),),
    "P": (("B", "B - Bar"), ("P", "P - Pascals")),
    "R": (("I", "I - Litres per second (legacy)"), ("L", "L - Litres per second"), ("H", "H - Litres per hour")),
    "S": (("b", "b - Binary"), ("p", "p - Percent of full range")),
    "T": (("R", "R - Revolutions per minute"),),
    "U": (("V", "V - Volts"),),
    "V": (("M", "M - Cubic metres"), ("P", "P - Percent of full range")),
    "W": (("C", "C - Degrees Celsius"),),
}


class AdvancedOutputPortDialog(OpaqueDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Advanced Output Port")
        self.resize(500, 360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.transport = QComboBox()
        self.transport.addItems(("UDP", "TCP", "Serial"))
        self.transport.currentTextChanged.connect(self._update_fields)
        form.addRow("Transport", self.transport)
        self.host_label = QLabel("Destination host")
        self.host = QLineEdit("127.0.0.1")
        self.host.textChanged.connect(self._validate)
        form.addRow(self.host_label, self.host)
        self.port_label = QLabel("Port")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(4096)
        form.addRow(self.port_label, self.port)
        self.tcp_mode_label = QLabel("TCP mode")
        self.tcp_mode = QComboBox()
        self.tcp_mode.addItem("Client - connect to remote server", False)
        self.tcp_mode.addItem("Server - accept incoming clients", True)
        self.tcp_mode.currentIndexChanged.connect(self._update_fields)
        form.addRow(self.tcp_mode_label, self.tcp_mode)
        self.udp_mode_label = QLabel("UDP mode")
        self.udp_mode = QComboBox()
        for label, value in (
            ("AUTO - derive from destination", "auto"),
            ("Unicast", "unicast"), ("Broadcast", "broadcast"), ("Multicast", "multicast"),
        ):
            self.udp_mode.addItem(label, value)
        form.addRow(self.udp_mode_label, self.udp_mode)
        self.ttl_label = QLabel("Multicast TTL")
        self.ttl = QSpinBox()
        self.ttl.setRange(0, 255)
        self.ttl.setValue(1)
        form.addRow(self.ttl_label, self.ttl)
        self.interface_label = QLabel("Interface IP")
        self.interface = QLineEdit()
        self.interface.setPlaceholderText("Optional, for multicast routing")
        form.addRow(self.interface_label, self.interface)
        self.device_label = QLabel("Serial device")
        self.device = QLineEdit()
        self.device.setPlaceholderText("COM3 or /dev/ttyUSB0")
        self.device.textChanged.connect(self._validate)
        form.addRow(self.device_label, self.device)
        self.baud_label = QLabel("Baud")
        self.baud = QComboBox()
        self.baud.addItems(("4800", "9600", "19200", "38400", "57600", "115200"))
        form.addRow(self.baud_label, self.baud)
        self.parity_label = QLabel("Parity")
        self.parity = QComboBox()
        self.parity.addItems(("N", "E", "O"))
        form.addRow(self.parity_label, self.parity)
        self.stop_label = QLabel("Stop bits")
        self.stop_bits = QComboBox()
        self.stop_bits.addItems(("1", "2"))
        form.addRow(self.stop_label, self.stop_bits)
        layout.addLayout(form)
        self.feedback = QLabel()
        self.feedback.setObjectName("secondaryText")
        layout.addWidget(self.feedback)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_fields()

    def settings(self) -> dict:
        if self.transport.currentText().upper() == "SERIAL":
            return {
                "transport": "Serial", "device": self.device.text().strip(),
                "baud": int(self.baud.currentText()), "parity": self.parity.currentText(),
                "stop_bits": int(self.stop_bits.currentText()),
            }
        values = {
            "transport": self.transport.currentText().upper(),
            "host": self.host.text().strip(), "port": self.port.value(),
        }
        if values["transport"] == "UDP":
            values.update({
                "mode": self.udp_mode.currentData(), "multicast_ttl": self.ttl.value(),
                "interface_ip": self.interface.text().strip(),
            })
        elif values["transport"] == "TCP":
            values["server"] = bool(self.tcp_mode.currentData())
        return values

    def _update_fields(self, *_args) -> None:
        transport = self.transport.currentText().upper()
        network = transport in {"UDP", "TCP"}
        udp = transport == "UDP"
        tcp = transport == "TCP"
        serial = transport == "SERIAL"
        self.host_label.setText(
            "Bind address" if tcp and bool(self.tcp_mode.currentData()) else "Destination host"
        )
        if tcp:
            self.host.setToolTip(LOCAL_BIND_HELP if self.tcp_mode.currentData() else REMOTE_TCP_HELP)
        else:
            self.host.setToolTip("Address of the receiving computer or UDP multicast group.")
        self.host_label.setToolTip(self.host.toolTip())
        for widget in (self.host_label, self.host, self.port_label, self.port):
            widget.setVisible(network)
        for widget in (self.tcp_mode_label, self.tcp_mode):
            widget.setVisible(tcp)
        for widget in (
            self.udp_mode_label, self.udp_mode, self.ttl_label, self.ttl,
            self.interface_label, self.interface,
        ):
            widget.setVisible(udp)
        for widget in (
            self.device_label, self.device, self.baud_label, self.baud,
            self.parity_label, self.parity, self.stop_label, self.stop_bits,
        ):
            widget.setVisible(serial)
        self._validate()

    def _validate(self, *_args) -> None:
        serial = self.transport.currentText().upper() == "SERIAL"
        active_editor = self.device if serial else self.host
        inactive_editor = self.host if serial else self.device
        valid = bool(active_editor.text().strip())
        active_editor.setProperty("validationState", "valid" if valid else "invalid")
        inactive_editor.setProperty("validationState", "normal")
        for editor in (active_editor, inactive_editor):
            editor.style().unpolish(editor)
            editor.style().polish(editor)
        self.feedback.setText("Ready" if valid else "Enter the required destination/device.")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(valid)


class _PortAwareRow(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        self.port = QComboBox()
        self.port.setMinimumWidth(190)
        self._saved_port = ""

    def set_ports(self, ports: tuple[str, ...]) -> None:
        current = self.port.currentText() or self._saved_port
        self.port.blockSignals(True)
        self.port.clear()
        self.port.addItems(ports)
        index = self.port.findText(current)
        self.port.setCurrentIndex(max(0, index))
        if self.port.currentText():
            self._saved_port = self.port.currentText()
        self.port.blockSignals(False)
        resume = getattr(self, "_resume_cycle", None)
        if callable(resume):
            resume()


class AdvancedProtocolRow(_PortAwareRow):
    def __init__(self, owner: "AdvancedProtocolDialog"):
        super().__init__()
        self.owner = owner
        self._fields: dict[str, QLineEdit | QComboBox] = {}
        self._xdr_preset: QComboBox | None = None
        self._applying_xdr_preset = False
        self._cycle_requested = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        header = QGridLayout()
        header.setColumnStretch(6, 1)
        self.enabled = QCheckBox()
        self.enabled.setChecked(True)
        self.talker = QLineEdit("GP")
        self.talker.setMaxLength(2)
        self.talker.setMaximumWidth(58)
        self.protocol = QComboBox()
        self.protocol.setMinimumWidth(190)
        self.protocol.addItem("Select protocol", "")
        for formatter, definition in sorted(ADVANCED_PROTOCOLS.items()):
            self.protocol.addItem(f"{formatter} - {definition.name}", formatter)
        self.protocol.currentIndexChanged.connect(self._rebuild_fields)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.1, 50.0)
        self.rate.setValue(1.0)
        self.rate.setSuffix(" Hz")
        self.rate.valueChanged.connect(self._rate_changed)
        self.once = QPushButton("Send Once")
        self.once.clicked.connect(self.send_once)
        self.cycle = QPushButton("Start Cycle")
        self.cycle.setCheckable(True)
        self.cycle.toggled.connect(self._toggle_cycle)
        remove = QPushButton("X")
        remove.setToolTip("Remove protocol")
        remove.setFixedWidth(30)
        remove.clicked.connect(lambda: owner.remove_protocol(self))
        header.addWidget(self.enabled, 0, 0)
        header.addWidget(QLabel("Protocol"), 0, 1)
        header.addWidget(self.protocol, 0, 2, 1, 2)
        header.addWidget(QLabel("Talker"), 0, 4)
        header.addWidget(self.talker, 0, 5)
        header.addWidget(QLabel("Output"), 1, 1)
        header.addWidget(self.port, 1, 2, 1, 2)
        header.addWidget(QLabel("Rate"), 1, 4)
        header.addWidget(self.rate, 1, 5)
        header.addWidget(self.once, 1, 7)
        header.addWidget(self.cycle, 1, 8)
        header.addWidget(remove, 1, 9)
        layout.addLayout(header)
        self.form_widget = QWidget()
        self.form = QFormLayout(self.form_widget)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addWidget(self.form_widget)
        self.feedback = QLabel("Select a protocol")
        self.feedback.setObjectName("secondaryText")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.talker.textChanged.connect(self._validate_fields)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.send_once)

    def _rebuild_fields(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._fields.clear()
        self._xdr_preset = None
        formatter = str(self.protocol.currentData() or "")
        definition = ADVANCED_PROTOCOLS.get(formatter)
        if definition is None:
            self.feedback.setText("Select a protocol")
            return
        sensor = self.owner.projected_sensor()
        if formatter == "XDR":
            self._xdr_preset = QComboBox()
            self._xdr_preset.setMinimumContentsLength(34)
            self._xdr_preset.addItem("Custom XDR measurement", "")
            previous_group = ""
            for preset in XDR_PRESETS:
                if previous_group and preset.group != previous_group:
                    self._xdr_preset.insertSeparator(self._xdr_preset.count())
                suffix = " - live AUTO" if preset.model_linked else ""
                self._xdr_preset.addItem(
                    f"{preset.group} - {preset.label}{suffix}", preset.key
                )
                previous_group = preset.group
            self._xdr_preset.setToolTip(
                "Recommended IEC 61162-1:2024 XDR identifiers. Live AUTO presets use the "
                "current projected sensor or environment value."
            )
            self.form.addRow("Recommended preset", self._xdr_preset)
        for field in definition.fields:
            choices = field.field_type.split(":", 1)[1].split(",") if field.field_type.startswith("choice:") else None
            auto = supports_auto(formatter, field.name, sensor)
            if formatter == "XDR" and field.name == "unit":
                editor = QComboBox()
            elif choices is not None:
                editor = QComboBox()
                if auto:
                    editor.addItem("AUTO - Model-derived value", "AUTO")
                for choice in choices:
                    label = (
                        _XDR_TYPE_LABELS.get(choice, choice)
                        if formatter == "XDR" and field.name == "type"
                        else _CHOICE_LABELS.get(choice, choice or "(null field)")
                    )
                    editor.addItem(label, choice)
            else:
                editor = QLineEdit()
                if auto:
                    editor.setText("AUTO")
                editor.setPlaceholderText(field.description)
            tooltip = field.description
            if formatter == "XDR" and field.name == "name":
                tooltip = (
                    "Transducer ID. In recommended IEC identifiers, #X is the data-instance "
                    "number from 0 to 9999. Presets start with #0; use #1, #2, etc. for "
                    "additional transducers of the same type."
                )
            editor.setToolTip(tooltip)
            self.form.addRow(field.name.replace("_", " ").title(), editor)
            self._fields[field.name] = editor
            if isinstance(editor, QComboBox):
                editor.currentIndexChanged.connect(self._field_changed)
            else:
                editor.textChanged.connect(self._field_changed)
        if formatter == "XDR":
            type_editor = self._fields.get("type")
            if isinstance(type_editor, QComboBox):
                type_editor.currentIndexChanged.connect(self._update_xdr_units)
                self._update_xdr_units()
            if self._xdr_preset is not None:
                self._xdr_preset.currentIndexChanged.connect(self._apply_xdr_preset)
        self._validate_fields()

    def _values(self) -> dict[str, str]:
        return {
            name: str(editor.currentData() if isinstance(editor, QComboBox) else editor.text())
            for name, editor in self._fields.items()
        }

    def _field_changed(self, *_args) -> None:
        if not self._applying_xdr_preset:
            self._sync_xdr_preset()
        self._validate_fields()

    def _apply_xdr_preset(self, *_args) -> None:
        if self._xdr_preset is None:
            return
        key = str(self._xdr_preset.currentData() or "")
        preset = next((item for item in XDR_PRESETS if item.key == key), None)
        if preset is None:
            return
        type_editor = self._fields.get("type")
        value_editor = self._fields.get("value")
        unit_editor = self._fields.get("unit")
        name_editor = self._fields.get("name")
        if not (
            isinstance(type_editor, QComboBox)
            and isinstance(value_editor, QLineEdit)
            and isinstance(unit_editor, QComboBox)
            and isinstance(name_editor, QLineEdit)
        ):
            return
        self._applying_xdr_preset = True
        try:
            type_index = type_editor.findData(preset.transducer_type)
            if type_index >= 0:
                type_editor.setCurrentIndex(type_index)
            self._update_xdr_units()
            unit_index = unit_editor.findData(preset.unit)
            if unit_index >= 0:
                unit_editor.setCurrentIndex(unit_index)
            name_editor.setText(preset.identifier)
            value_editor.setText("AUTO" if preset.model_linked else "")
        finally:
            self._applying_xdr_preset = False
        self._validate_fields()

    def _sync_xdr_preset(self) -> None:
        if self._xdr_preset is None:
            return
        values = self._values()
        match = next(
            (
                preset.key
                for preset in XDR_PRESETS
                if values.get("type") == preset.transducer_type
                and values.get("unit") == preset.unit
                and values.get("name") == preset.identifier
            ),
            "",
        )
        index = self._xdr_preset.findData(match)
        self._xdr_preset.blockSignals(True)
        self._xdr_preset.setCurrentIndex(max(0, index))
        self._xdr_preset.blockSignals(False)

    def _update_xdr_units(self, *_args) -> None:
        type_editor = self._fields.get("type")
        unit_editor = self._fields.get("unit")
        if not isinstance(type_editor, QComboBox) or not isinstance(unit_editor, QComboBox):
            return
        previous = unit_editor.currentData()
        unit_editor.blockSignals(True)
        unit_editor.clear()
        for value, label in _XDR_UNITS.get(str(type_editor.currentData() or ""), (("", "(null field)"),)):
            unit_editor.addItem(label, value)
        match = unit_editor.findData(previous)
        unit_editor.setCurrentIndex(max(0, match))
        unit_editor.blockSignals(False)
        self._validate_fields()

    def _validate_fields(self) -> bool:
        formatter = str(self.protocol.currentData() or "")
        if not formatter:
            return False
        try:
            encode_advanced_protocol(
                self.talker.text().strip(),
                formatter,
                self._values(),
                sensor=self.owner.projected_sensor(),
            )
        except (TypeError, ValueError) as error:
            message = str(error)
            self.feedback.setText(f"Invalid: {message}")
            self.talker.setProperty(
                "validationState",
                "invalid" if "talker" in message.lower() else "normal",
            )
            self.talker.style().unpolish(self.talker)
            self.talker.style().polish(self.talker)
            for name, editor in self._fields.items():
                invalid = name.lower() in message.lower() or formatter == "XDR" and name in {"type", "unit"} and "XDR unit" in message
                editor.setProperty("validationState", "invalid" if invalid else "normal")
                editor.style().unpolish(editor)
                editor.style().polish(editor)
            return False
        self.talker.setProperty("validationState", "valid")
        self.talker.style().unpolish(self.talker)
        self.talker.style().polish(self.talker)
        for editor in self._fields.values():
            editor.setProperty("validationState", "valid")
            editor.style().unpolish(editor)
            editor.style().polish(editor)
        self.feedback.setText(f"{ADVANCED_PROTOCOLS[formatter].name} ready")
        return True

    def send_once(self) -> None:
        if not self.enabled.isChecked():
            return
        formatter = str(self.protocol.currentData() or "")
        port = self.port.currentText()
        if not formatter or not port:
            self.feedback.setText("Select a protocol and configured output port")
            return
        try:
            sentence = encode_advanced_protocol(
                self.talker.text().strip(),
                formatter,
                self._values(),
                sensor=self.owner.projected_sensor(),
            )
            self.owner.send(port, sentence)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.feedback.setText(str(error))
            if self.timer.isActive():
                self.cycle.setChecked(False)
            return
        self.feedback.setText(f"Sent {formatter}: {sentence}")
        _pulse(self.once)

    def _rate_changed(self) -> None:
        if self.timer.isActive():
            self.timer.setInterval(max(20, round(1000.0 / self.rate.value())))

    def _toggle_cycle(self, active: bool) -> None:
        self._cycle_requested = bool(active)
        if active:
            if not self.protocol.currentData() or not self.port.currentText():
                self.cycle.blockSignals(True)
                self.cycle.setChecked(False)
                self.cycle.blockSignals(False)
                self.feedback.setText("Select a protocol and configured output port")
                return
            self.timer.start(max(20, round(1000.0 / self.rate.value())))
            self.cycle.setText("Cycle Active")
            self.send_once()
        else:
            self.timer.stop()
            self.cycle.setText("Start Cycle")

    def stop(self) -> None:
        self.timer.stop()

    def _resume_cycle(self) -> None:
        if self._cycle_requested and not self.cycle.isChecked():
            self.cycle.setChecked(True)

    def export_state(self) -> dict:
        return {
            "enabled": self.enabled.isChecked(),
            "talker": self.talker.text(),
            "protocol": str(self.protocol.currentData() or ""),
            "port": self.port.currentText(),
            "rate_hz": self.rate.value(),
            "values": self._values(),
            "cycle_active": self.cycle.isChecked() and self.timer.isActive(),
        }

    def apply_state(self, state: dict) -> None:
        self.enabled.setChecked(bool(state.get("enabled", True)))
        self.talker.setText(str(state.get("talker", "GP")))
        index = self.protocol.findData(str(state.get("protocol", "")))
        self.protocol.setCurrentIndex(max(0, index))
        self.rate.setValue(float(state.get("rate_hz", 1.0)))
        port = str(state.get("port", ""))
        self._saved_port = port
        port_index = self.port.findText(port)
        if port_index >= 0:
            self.port.setCurrentIndex(port_index)
        values = state.get("values", {})
        if isinstance(values, dict):
            for name, value in values.items():
                editor = self._fields.get(name)
                if isinstance(editor, QComboBox):
                    match = editor.findData(str(value))
                    if match >= 0:
                        editor.setCurrentIndex(match)
                elif isinstance(editor, QLineEdit):
                    editor.setText(str(value))
        self._sync_xdr_preset()
        self._cycle_requested = bool(state.get("cycle_active", False))
        self._resume_cycle()


class FreeTextRow(_PortAwareRow):
    def __init__(self, owner: "AdvancedProtocolDialog"):
        super().__init__()
        self.owner = owner
        self._sentences: list[str] = []
        self._index = 0
        self._remaining: int | None = None
        self._cycle_requested = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        header = QGridLayout()
        header.setColumnStretch(3, 1)
        self.enabled = QCheckBox()
        self.enabled.setChecked(True)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.1, 50.0)
        self.rate.setValue(2.0)
        self.rate.setSuffix(" lines/s")
        self.rate.valueChanged.connect(self._rate_changed)
        load = QPushButton("Load File")
        load.clicked.connect(self._load_file)
        self.once = QPushButton("Send Once")
        self.once.clicked.connect(self.send_once)
        self.cycle = QPushButton("Start Cycle")
        self.cycle.setCheckable(True)
        self.cycle.toggled.connect(self._toggle_cycle)
        remove = QPushButton("X")
        remove.setToolTip("Remove free text playback")
        remove.setFixedWidth(30)
        remove.clicked.connect(lambda: owner.remove_free_text(self))
        header.addWidget(self.enabled, 0, 0)
        header.addWidget(QLabel("Output"), 0, 1)
        header.addWidget(self.port, 0, 2)
        header.addWidget(QLabel("Rate"), 1, 1)
        header.addWidget(self.rate, 1, 2)
        header.addWidget(load, 1, 4)
        header.addWidget(self.once, 1, 5)
        header.addWidget(self.cycle, 1, 6)
        header.addWidget(remove, 1, 7)
        layout.addLayout(header)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("$GPHDT,123.4,T  (checksum may be omitted)")
        self.editor.setMaximumHeight(110)
        layout.addWidget(self.editor)
        self.feedback = QLabel("Enter one IEC sentence per line")
        self.feedback.setObjectName("secondaryText")
        layout.addWidget(self.feedback)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._send_next)

    def _prepare(self) -> bool:
        try:
            self._sentences = [
                self._normalize(line)
                for line in self.editor.toPlainText().splitlines()
                if line.strip()
            ]
        except (TypeError, ValueError) as error:
            self.feedback.setText(str(error))
            return False
        if not self._sentences or not self.port.currentText():
            self.feedback.setText("Enter at least one sentence and select an output port")
            return False
        self._index = 0
        return True

    @staticmethod
    def _normalize(line: str) -> str:
        sentence = line.strip()
        positions = [position for position in (sentence.find("$"), sentence.find("!")) if position >= 0]
        if positions:
            sentence = sentence[min(positions):]
        if not sentence.startswith(("$", "!")):
            sentence = "$" + sentence
        if "*" in sentence:
            IEC61162Codec.decode(sentence)
            return sentence
        return IEC61162Codec.frame(sentence[1:], delimiter=sentence[0])

    def send_once(self) -> None:
        if not self.enabled.isChecked() or not self._prepare():
            return
        self.cycle.blockSignals(True)
        self.cycle.setChecked(False)
        self.cycle.blockSignals(False)
        self.cycle.setText("Start Cycle")
        self._remaining = len(self._sentences)
        self.timer.start(max(20, round(1000.0 / self.rate.value())))
        self._send_next()
        _pulse(self.once)

    def _toggle_cycle(self, active: bool) -> None:
        self._cycle_requested = bool(active)
        if active:
            if not self._prepare():
                self.cycle.blockSignals(True)
                self.cycle.setChecked(False)
                self.cycle.blockSignals(False)
                return
            self._remaining = None
            self.timer.start(max(20, round(1000.0 / self.rate.value())))
            self.cycle.setText("Cycle Active")
            self._send_next()
        else:
            self.timer.stop()
            self._remaining = None
            self.cycle.setText("Start Cycle")

    def _send_next(self) -> None:
        if not self._sentences:
            self.timer.stop()
            return
        try:
            self.owner.send(self.port.currentText(), self._sentences[self._index])
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.feedback.setText(str(error))
            self.cycle.setChecked(False)
            self.timer.stop()
            return
        self.feedback.setText(f"Sent line {self._index + 1}/{len(self._sentences)}")
        self._index = (self._index + 1) % len(self._sentences)
        if self._remaining is not None:
            self._remaining -= 1
            if self._remaining <= 0:
                self.timer.stop()

    def _rate_changed(self) -> None:
        if self.timer.isActive():
            self.timer.setInterval(max(20, round(1000.0 / self.rate.value())))

    def _load_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Load IEC text playback", "", "Text files (*.txt *.log *.nmea);;All files (*)"
        )
        if not path:
            return
        try:
            self.editor.setPlainText(Path(path).read_text(encoding="utf-8", errors="ignore"))
        except OSError as error:
            show_warning(self, "Playback file could not be loaded", str(error))

    def stop(self) -> None:
        self.timer.stop()

    def _resume_cycle(self) -> None:
        if self._cycle_requested and not self.cycle.isChecked():
            self.cycle.setChecked(True)

    def export_state(self) -> dict:
        return {
            "enabled": self.enabled.isChecked(),
            "port": self.port.currentText(),
            "rate_hz": self.rate.value(),
            "text": self.editor.toPlainText(),
            "cycle_active": self.cycle.isChecked() and self.timer.isActive() and self._remaining is None,
        }

    def apply_state(self, state: dict) -> None:
        self.enabled.setChecked(bool(state.get("enabled", True)))
        self.rate.setValue(float(state.get("rate_hz", 2.0)))
        self.editor.setPlainText(str(state.get("text", "")))
        port_index = self.port.findText(str(state.get("port", "")))
        self._saved_port = str(state.get("port", ""))
        if port_index >= 0:
            self.port.setCurrentIndex(port_index)
        self._cycle_requested = bool(state.get("cycle_active", False))
        self._resume_cycle()


class AdvancedProtocolDialog(OpaqueDialog):
    """Multi-row V2 advanced protocol and free-text workspace."""

    def __init__(
        self,
        *,
        snapshot,
        send_sentence: Callable[[str, str], None],
        add_output_port: Callable[[dict], str] | None = None,
        remove_output_port: Callable[[str], None] | None = None,
        state_path: Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._snapshot = snapshot
        self._send_sentence = send_sentence
        self._add_output_port = add_output_port
        self._remove_output_port = remove_output_port
        self._projector = SensorProjector()
        self._state_path = state_path
        self._ports: tuple[str, ...] = ()
        self.protocol_rows: list[AdvancedProtocolRow] = []
        self.free_text_rows: list[FreeTextRow] = []
        self.setWindowTitle("Maritime Ship Simulator V2 - Advanced Protocols")
        self.resize(1180, 760)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        heading = QLabel("Advanced IEC 61162-1 Output")
        heading.setObjectName("dialogTitle")
        top.addWidget(heading)
        top.addStretch(1)
        add_port = QPushButton("+ Output Port")
        add_port.setToolTip("Define a persistent UDP, TCP or Serial endpoint for Advanced output.")
        add_port.setEnabled(self._add_output_port is not None)
        add_port.clicked.connect(self._create_output_port)
        top.addWidget(add_port)
        add_protocol = QPushButton("+ Protocol")
        add_protocol.clicked.connect(self.add_protocol)
        top.addWidget(add_protocol)
        add_free = QPushButton("+ Free Text Playback")
        add_free.clicked.connect(self.add_free_text)
        top.addWidget(add_free)
        layout.addLayout(top)
        note = QLabel(
            "Configure multiple independent sentences and custom text playbacks. AUTO is offered only where "
            "the current model has a reliable value; generated sentences are validated and checksummed."
        )
        note.setObjectName("secondaryText")
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.rows_layout = QVBoxLayout(self.container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.addStretch(1)
        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)
        self.add_protocol()
        self._load_state()

    def projected_sensor(self):
        return self._projector.project(self._snapshot())

    def send(self, port: str, sentence: str) -> None:
        self._send_sentence(port, sentence)

    def _create_output_port(self) -> None:
        if self._add_output_port is None:
            return
        dialog = AdvancedOutputPortDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            label = self._add_output_port(dialog.settings())
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Output port could not be added", str(error))
            return
        self.set_ports(tuple(dict.fromkeys((*self._ports, label))))
        for row in (*self.protocol_rows, *self.free_text_rows):
            row.port.setCurrentText(label)

    def set_ports(self, ports: tuple[str, ...]) -> None:
        self._ports = tuple(ports)
        for row in (*self.protocol_rows, *self.free_text_rows):
            row.set_ports(self._ports)

    def add_protocol(self) -> AdvancedProtocolRow:
        row = AdvancedProtocolRow(self)
        row.set_ports(self._ports)
        self.protocol_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        return row

    def remove_protocol(self, row: AdvancedProtocolRow) -> None:
        if row not in self.protocol_rows:
            return
        row.stop()
        self.protocol_rows.remove(row)
        row.deleteLater()

    def add_free_text(self) -> FreeTextRow:
        row = FreeTextRow(self)
        row.set_ports(self._ports)
        self.free_text_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        return row

    def remove_free_text(self, row: FreeTextRow) -> None:
        if row not in self.free_text_rows:
            return
        row.stop()
        self.free_text_rows.remove(row)
        row.deleteLater()

    def export_state(self) -> dict:
        return {
            "protocols": [row.export_state() for row in self.protocol_rows],
            "free_text": [row.export_state() for row in self.free_text_rows],
        }

    def apply_state(self, state: dict) -> None:
        for row in (*self.protocol_rows, *self.free_text_rows):
            row.stop()
            row.deleteLater()
        self.protocol_rows.clear()
        self.free_text_rows.clear()
        for row_state in state.get("protocols", []) if isinstance(state, dict) else []:
            self.add_protocol().apply_state(row_state)
        for row_state in state.get("free_text", []) if isinstance(state, dict) else []:
            self.add_free_text().apply_state(row_state)

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.apply_state(state)

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.export_state(), indent=2) + "\n", encoding="utf-8")
        temporary.replace(self._state_path)

    def closeEvent(self, event) -> None:
        self._save_state()
        super().closeEvent(event)
