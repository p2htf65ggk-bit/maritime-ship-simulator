# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .dialogs import OpaqueDialog


@dataclass(frozen=True, slots=True)
class OutputEvent:
    sequence: int
    monotonic_s: float
    port: str
    protocol: str
    sentence: str


class ProtocolOutputDialog(OpaqueDialog):
    """Monitor actual accepted output with only configured ports selectable."""

    def __init__(
        self,
        *,
        output_running: Callable[[], bool],
        configured_ports: Callable[[], Sequence[str]],
        output_events: Callable[[], Sequence[OutputEvent]],
        clear_events: Callable[[], None],
        serial_load: Callable[[str], float | None],
        parent=None,
    ):
        super().__init__(parent)
        self._output_running = output_running
        self._configured_ports = configured_ports
        self._output_events = output_events
        self._clear_events = clear_events
        self._serial_load = serial_load
        self._last_sequence = -1
        self._active_port = ""
        self._frozen = False
        self.setWindowTitle("Maritime Ship Simulator V2 - Protocol Output")
        self.resize(920, 620)

        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel("Protocol Output")
        title.setObjectName("dialogTitle")
        heading.addWidget(title)
        heading.addStretch(1)
        self.port = QComboBox()
        self.port.setMinimumWidth(300)
        self.port.currentTextChanged.connect(self._port_changed)
        heading.addWidget(QLabel("Port"))
        heading.addWidget(self.port)
        self.freeze = QPushButton("Freeze")
        self.freeze.setCheckable(True)
        self.freeze.setToolTip(
            "Freeze or resume this monitor display. Protocol output continues running."
        )
        self.freeze.toggled.connect(self._set_frozen)
        heading.addWidget(self.freeze)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        heading.addWidget(clear)
        layout.addLayout(heading)

        self.status = QLabel("Monitor shows sentences accepted by the configured output transports.")
        self.status.setObjectName("secondaryText")
        layout.addWidget(self.status)
        load_row = QHBoxLayout()
        load_row.addWidget(QLabel("Port load"))
        self.load = QProgressBar()
        self.load.setRange(0, 100)
        self.load.setTextVisible(True)
        load_row.addWidget(self.load, 1)
        layout.addLayout(load_row)

        self.monitor = QPlainTextEdit()
        self.monitor.setObjectName("protocolMonitor")
        self.monitor.setReadOnly(True)
        self.monitor.setMaximumBlockCount(2500)
        layout.addWidget(self.monitor, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.timer.start(200)
        self._refresh_ports()
        self.refresh(force=True)

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self, force: bool = False) -> None:
        self._refresh_ports()
        running = self._output_running()
        events = tuple(self._output_events())
        if self._frozen:
            state = "running" if running else "stopped"
            self.status.setText(
                f"Monitor display frozen | output {state} | press Resume to continue"
            )
            self._update_load(self.port.currentText())
            return
        if force or self._active_port != self.port.currentText():
            self._active_port = self.port.currentText()
            self.monitor.clear()
            self._last_sequence = -1
        selected = self._active_port
        new_events = [event for event in events if event.sequence > self._last_sequence]
        visible_events = [event for event in new_events if not selected or event.port == selected]
        # One document update avoids starving the 10 ms output scheduler while a
        # retained monitor history is first displayed.
        if visible_events:
            lines = []
            for event in visible_events[-2500:]:
                port = event.port if len(event.port) <= 34 else event.port[:31] + "..."
                protocol = event.protocol if len(event.protocol) <= 5 else event.protocol[:5]
                lines.append(f"{port:<34} {protocol:<5} {event.sentence}")
            self.monitor.appendPlainText("\n".join(lines))
        if events:
            self._last_sequence = events[-1].sequence
        visible_count = sum(1 for event in events if not selected or event.port == selected)
        state = "running" if running else "stopped"
        self.status.setText(f"Monitor | output {state} | {visible_count} captured lines")
        self._update_load(selected)

    def _update_load(self, selected: str) -> None:
        load = self._serial_load(selected) if selected else None
        if load is None:
            self.load.setValue(0)
            self.load.setFormat("N/A for this transport")
        else:
            percent = max(0.0, min(100.0, load))
            self.load.setValue(round(percent))
            self.load.setFormat(f"{percent:.1f}%")
            self.load.setProperty("loadLevel", "high" if percent >= 90.0 else "medium" if percent >= 70.0 else "normal")
            self.load.style().unpolish(self.load)
            self.load.style().polish(self.load)

    def _set_frozen(self, frozen: bool) -> None:
        self._frozen = bool(frozen)
        self.freeze.setText("Resume" if self._frozen else "Freeze")
        if self._frozen:
            self.refresh()
        else:
            self.refresh(force=True)

    def _refresh_ports(self) -> None:
        ports = tuple(self._configured_ports())
        current = self.port.currentText()
        existing = tuple(self.port.itemText(index) for index in range(self.port.count()))
        if ports == existing:
            return
        self.port.blockSignals(True)
        self.port.clear()
        self.port.addItems(ports)
        index = self.port.findText(current)
        self.port.setCurrentIndex(max(0, index))
        self.port.blockSignals(False)

    def _port_changed(self, _text: str) -> None:
        self.refresh(force=True)

    def _clear(self) -> None:
        self._clear_events()
        self.monitor.clear()
        self._last_sequence = -1
        self.refresh()
