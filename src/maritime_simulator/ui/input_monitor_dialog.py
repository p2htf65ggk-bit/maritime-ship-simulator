# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .dialogs import OpaqueDialog

from .input_runtime import InputPortEvent


class ExternalInputMonitorDialog(OpaqueDialog):
    """Show the exact lines received by the configured external-rudder port."""

    def __init__(
        self,
        *,
        input_running: Callable[[], bool],
        endpoint_description: Callable[[], str],
        input_events: Callable[[], Sequence[InputPortEvent]],
        clear_events: Callable[[], None],
        parent=None,
    ):
        super().__init__(parent)
        self._input_running = input_running
        self._endpoint_description = endpoint_description
        self._input_events = input_events
        self._clear_events = clear_events
        self._last_sequence = -1
        self.setWindowTitle("Maritime Ship Simulator V2 - External Input")
        self.resize(920, 560)

        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel("External Input Ports")
        title.setObjectName("dialogTitle")
        heading.addWidget(title)
        heading.addStretch(1)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        heading.addWidget(clear)
        layout.addLayout(heading)

        self.status = QLabel()
        self.status.setObjectName("secondaryText")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.monitor = QPlainTextEdit()
        self.monitor.setObjectName("protocolMonitor")
        self.monitor.setReadOnly(True)
        self.monitor.setMaximumBlockCount(2500)
        layout.addWidget(self.monitor, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.refresh(force=True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.timer.start()
        self.refresh(force=True)

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self, force: bool = False) -> None:
        if force:
            self.monitor.clear()
            self._last_sequence = -1
        events = tuple(self._input_events())
        for event in events:
            if event.sequence <= self._last_sequence:
                continue
            control = "CONTROL" if event.accepted_for_control else "observe"
            self.monitor.appendPlainText(
                f"{event.source:<22} {event.formatter:<3} {event.topology:<6} {control:<7} {event.sentence}"
            )
        if events:
            self._last_sequence = events[-1].sequence
        state = "listening" if self._input_running() else "not listening"
        self.status.setText(
            f"{self._endpoint_description()} | {state} | {len(events)} captured lines\n"
            "CONTROL marks valid input eligible for a configured source. Arbitration selects one source per actuator."
        )

    def _clear(self) -> None:
        self._clear_events()
        self.monitor.clear()
        self._last_sequence = -1
        self.refresh()
