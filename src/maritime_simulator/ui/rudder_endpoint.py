# SPDX-License-Identifier: MPL-2.0
from dataclasses import asdict

from PySide6.QtCore import Signal
from PySide6.QtSerialPort import QSerialPortInfo
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QTabWidget, QVBoxLayout, QWidget
from .network_help import LOCAL_BIND_HELP, REMOTE_TCP_HELP
from .content_tabs import ContentSizedTabs


class RudderEndpointEditor(QWidget):
    """One active transport per endpoint; hidden transport values are retained."""

    changed = Signal()
    TRANSPORTS = ("UDP", "TCP", "Serial")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._values = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = ContentSizedTabs()
        self.fields = {}
        self.labels = {}
        for transport in self.TRANSPORTS:
            page = QWidget()
            form = QFormLayout(page)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            fields, labels = {}, {}
            def add(key, title, widget):
                label = QLabel(title)
                form.addRow(label, widget)
                fields[key], labels[key] = widget, label
                if isinstance(widget, QLineEdit) or isinstance(widget, QSpinBox):
                    widget.editingFinished.connect(self._commit)
                elif isinstance(widget, QCheckBox):
                    widget.toggled.connect(self._commit)
                elif widget.isEditable():
                    widget.lineEdit().editingFinished.connect(self._commit)
                    widget.activated.connect(self._commit)
                else:
                    widget.currentIndexChanged.connect(self._commit)
                return widget
            if transport in {"UDP", "TCP"}:
                host = add("host", "Bind address", QLineEdit())
                host.setToolTip(LOCAL_BIND_HELP)
                labels["host"].setToolTip(LOCAL_BIND_HELP)
                port = QSpinBox()
                port.setRange(1, 65535)
                add("port", "Port", port)
            if transport == "UDP":
                group = add("multicast_group", "Multicast group", QLineEdit())
                group.setPlaceholderText("Empty for unicast")
                group.textChanged.connect(self._availability)
                interface = add("interface_ip", "Interface IP", QLineEdit())
                interface.setPlaceholderText("0.0.0.0 - automatic")
                interface.setToolTip("Local receiving adapter for multicast; empty or 0.0.0.0 lets the OS select it.")
            elif transport == "TCP":
                add("tcp_server", "TCP mode", QCheckBox("Server: listen for incoming connections"))
            else:
                device = QComboBox()
                device.setEditable(True)
                device.addItems(sorted({port.systemLocation() for port in QSerialPortInfo.availablePorts()}))
                add("serial_device", "Serial port", device)
                baud = QComboBox()
                baud.setEditable(True)
                baud.addItems([str(value) for value in (4800, 9600, 19200, 38400, 57600, 115200)])
                add("baud", "Baud", baud)
                parity = QComboBox()
                for text, value in (("None", "N"), ("Even", "E"), ("Odd", "O")):
                    parity.addItem(text, value)
                add("parity", "Parity", parity)
                stop_bits = QComboBox()
                for value in (1, 2):
                    stop_bits.addItem(str(value), value)
                add("stop_bits", "Stop bits", stop_bits)
            self.fields[transport], self.labels[transport] = fields, labels
            self.tabs.addTab(page, transport)
        self.tabs.currentChanged.connect(self._commit)
        root.addWidget(self.tabs)

    def load(self, endpoint):
        self._loading = True
        try:
            self._values = asdict(endpoint)
            for fields in self.fields.values():
                for key, field in fields.items():
                    value = self._values[key]
                    if isinstance(field, QLineEdit):
                        field.setText(str(value))
                    elif isinstance(field, QSpinBox):
                        field.setValue(value)
                    elif isinstance(field, QCheckBox):
                        field.setChecked(value)
                    elif field.isEditable():
                        field.setCurrentText(str(value))
                    else:
                        field.setCurrentIndex(max(0, field.findData(value)))
            self.tabs.setCurrentIndex(next(index for index, value in enumerate(self.TRANSPORTS) if value.upper() == endpoint.transport.upper()))
            self._availability()
        finally:
            self._loading = False

    def values(self):
        transport = self.TRANSPORTS[self.tabs.currentIndex()]
        result = dict(self._values, transport=transport)
        for key, field in self.fields[transport].items():
            if isinstance(field, QLineEdit):
                value = field.text().strip()
            elif isinstance(field, QSpinBox):
                value = field.value()
            elif isinstance(field, QCheckBox):
                value = field.isChecked()
            elif field.isEditable():
                value = field.currentText().strip()
            else:
                value = field.currentData()
            result[key] = int(value) if key == "baud" else value
        result["interface_ip"] = result.get("interface_ip") or "0.0.0.0"
        return result

    def _commit(self, *_args):
        if not self._loading:
            self._availability()
            self.changed.emit()

    def _availability(self, *_args):
        if not self.fields:
            return
        udp = self.fields.get("UDP", {})
        if udp:
            enabled = bool(udp["multicast_group"].text().strip())
            udp["interface_ip"].setEnabled(enabled)
            self.labels["UDP"]["interface_ip"].setEnabled(enabled)
        tcp = self.fields.get("TCP", {})
        if tcp:
            server = tcp["tcp_server"].isChecked()
            label = self.labels["TCP"]["host"]
            label.setText("Bind address" if server else "Remote host")
            tcp["host"].setToolTip(LOCAL_BIND_HELP if server else REMOTE_TCP_HELP)
            label.setToolTip(tcp["host"].toolTip())
