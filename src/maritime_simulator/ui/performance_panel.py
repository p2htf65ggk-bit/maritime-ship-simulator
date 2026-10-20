# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import json

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from maritime_simulator.application.metrics import PerformanceMonitor


class OperatorLoadBar(QWidget):
    """Compact traffic-light scale; 0 is healthy and 100 is critical."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._load = 0.0
        self._informational = False
        self.setFixedHeight(18)
        self.setMinimumWidth(120)

    def set_load(self, value: float) -> None:
        bounded = max(0.0, min(100.0, float(value)))
        if abs(bounded - self._load) > 0.05:
            self._load = bounded
            self.update()

    def load(self) -> float:
        return self._load

    def set_informational(self, informational: bool) -> None:
        self._informational = informational
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(1.0, 3.0, max(1.0, self.width() - 2.0), max(1.0, self.height() - 6.0))
        segments = ((0.0, 1.0, QColor("#34404d")),) if self._informational else (
            (0.00, 0.70, QColor("#37a875")),
            (0.70, 0.90, QColor("#d3a635")),
            (0.90, 1.00, QColor("#c74d55")),
        )
        for start, end, color in segments:
            painter.fillRect(
                QRectF(rect.left() + rect.width() * start, rect.top(), rect.width() * (end - start), rect.height()),
                color,
            )
        painter.setPen(QPen(QColor("#dce9ef"), 2.0))
        marker_x = rect.left() + rect.width() * self._load / 100.0
        painter.drawLine(marker_x, 1.0, marker_x, self.height() - 1.0)
        painter.setPen(QPen(QColor("#34404d"), 1.0))
        painter.drawRoundedRect(rect, 2.0, 2.0)


class PerformancePanel(QWidget):
    """Operator-facing health view with optional raw diagnostics."""

    _ROWS = (
        ("ui", "UI response", "How reliably the visible controls are refreshed."),
        ("simulation", "Simulation tick", "Average and worst simulation processing time."),
        ("track", "Track controller", "Worst Track Control calculation time."),
        ("output", "Protocol output", "Actual output-worker processing time. Sentence count alone is not a bandwidth limit for UDP or TCP."),
        ("serial", "Serial bandwidth", "Measured bytes per second compared with each active serial port's baud/framing capacity."),
        ("errors", "Network errors", "Recent transport errors; lifetime totals remain in technical details."),
        ("ais", "AIS targets", "Informational count, not a load or bandwidth warning."),
        ("memory", "App memory", "Current resident memory of the app and its child processes, including WebEngine. Shared pages may be counted more than once. Sampled every 5 seconds. If unavailable, the main-process peak is shown instead."),
    )

    def __init__(self, monitor: PerformanceMonitor, parent=None, *, serial_loads=None):
        super().__init__(parent)
        self.monitor = monitor
        self.serial_loads = serial_loads
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("Enable performance monitoring")
        self.enabled.setToolTip("Collect lightweight measurements until monitoring is disabled. Closing this window only stops display refresh.")
        self.enabled.setChecked(monitor.enabled)
        self.enabled.toggled.connect(self.monitor.set_enabled)
        layout.addWidget(self.enabled)

        self.overall_status = QLabel("Monitoring is off")
        self.overall_status.setObjectName("dataReadout")
        self.overall_status.setWordWrap(True)
        layout.addWidget(self.overall_status)
        explanation = QLabel(
            "Health uses the latest 10 seconds. Green is normal; yellow needs observation; red may affect "
            "operation. Grey rows are information, not capacity limits."
        )
        explanation.setObjectName("secondaryText")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.metric_labels: dict[str, QLabel] = {}
        self.metric_bars: dict[str, OperatorLoadBar] = {}
        for row, (key, title, tooltip) in enumerate(self._ROWS):
            title_label = QLabel(title)
            title_label.setToolTip(tooltip)
            value = QLabel("--")
            value.setMinimumWidth(150)
            value.setWordWrap(True)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            bar = OperatorLoadBar()
            bar.setToolTip(tooltip)
            grid.addWidget(title_label, row, 0)
            grid.addWidget(bar, row, 1)
            grid.addWidget(value, row, 2)
            self.metric_labels[key] = value
            self.metric_bars[key] = bar
            if key in {"ais", "memory"}:
                bar.set_informational(True)
        layout.addLayout(grid)

        controls = QGridLayout()
        self.details_toggle = QCheckBox("Show technical details")
        self.details_toggle.toggled.connect(lambda active: self.output.setVisible(active))
        reset = QPushButton("Reset measurements")
        reset.clicked.connect(self._reset)
        controls.addWidget(self.details_toggle, 0, 0)
        controls.setColumnStretch(1, 1)
        controls.addWidget(reset, 0, 2)
        layout.addLayout(controls)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.hide()
        layout.addWidget(self.output, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.timer.start()
        self.refresh()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def _reset(self) -> None:
        self.monitor.reset()
        self.refresh()

    def refresh(self) -> None:
        snapshot = self.monitor.snapshot()
        self._update_technical_output(snapshot)
        if not snapshot["enabled"]:
            self.overall_status.setText("Monitoring is off. Enable it to assess current operating load.")
            for key in self.metric_labels:
                self.metric_labels[key].setText("--")
                self.metric_bars[key].set_load(0.0)
            return

        recent = snapshot.get("recent", snapshot)
        durations = recent.get("durations", {})
        counters = recent.get("counters", {})
        rates = recent.get("rates", {})
        gauges = snapshot.get("gauges", {})

        ui = durations.get("ui_refresh", {})
        ui_rate = float(ui.get("rate_hz", 0.0))
        ui_max = float(ui.get("max_ms", 0.0))
        ui_load = max(ui_max / 50.0 * 100.0, 20.0 if ui_rate >= 15.0 else 75.0 if ui_rate >= 8.0 else 95.0) if ui.get("count", 0) else 0
        self._set_metric("ui", ui_load, f"{ui_rate:.1f}\u00a0Hz | peak {ui_max:.1f}\u00a0ms")

        simulation = durations.get("simulation_tick", {})
        sim_avg = float(simulation.get("avg_ms", 0.0))
        sim_max = float(simulation.get("max_ms", 0.0))
        self._set_metric("simulation", sim_max, f"avg {sim_avg:.1f}\u00a0ms | peak {sim_max:.1f}\u00a0ms")

        track = durations.get("track_controller", {})
        track_max = float(track.get("max_ms", 0.0))
        self._set_metric("track", track_max, f"peak {track_max:.1f}\u00a0ms")

        sentence_rate = float(rates.get("nmea_sentences", 0.0))
        pump = durations.get("output_pump", {})
        busy = float(pump.get("busy_percent", 0))
        # A 50-ms wake-up is normal at low output rates, not a data outage.
        pump_peak = float(pump.get("max_ms", 0))
        self._set_metric(
            "output",
            max(busy, pump_peak / 100.0 * 100),
            f"{sentence_rate:.0f}\u00a0sentences/s | busy\u00a0{busy:.0f}%",
        )

        loads = self.serial_loads(snapshot) if self.serial_loads is not None else {}
        serial_max = max(loads.values(), default=0)
        self.metric_bars["serial"].set_informational(not loads)
        self._set_metric("serial", serial_max, f"busiest {serial_max:.0f}%" if loads else "No active serial output")
        self.metric_bars["serial"].setToolTip("\n".join(f"{label}: {load:.1f}%" for label, load in loads.items()) or "UDP and TCP do not use a serial baud-rate limit.")

        errors = int(counters.get("network_send_errors", 0))
        self._set_metric("errors", 0.0 if errors == 0 else min(100.0, 70.0 + errors * 5.0), f"{errors} errors")

        targets = int(gauges.get("ais_output_targets", 0) or 0)
        self._set_metric("ais", 0, f"{targets} targets")

        rss = gauges.get("rss_mb")
        current_rss = gauges.get("memory.current_rss_mb")
        memory_text = (f"{float(current_rss):.0f} MB current" if current_rss is not None
                       else "Unavailable" if rss is None else f"{float(rss):.0f} MB peak")
        self._set_metric("memory", 0, memory_text)

        worst = max((bar.load() for bar in self.metric_bars.values()), default=0.0)
        if worst >= 90.0:
            self.overall_status.setText("Attention required: at least one measured value is in the red range.")
        elif worst >= 70.0:
            self.overall_status.setText("Operational, with one or more values in the observation range.")
        else:
            self.overall_status.setText("System load is within the normal operating range.")

    def _update_technical_output(self, snapshot: dict) -> None:
        text = json.dumps(snapshot, indent=2, sort_keys=True)
        if text == self.output.toPlainText():
            return

        vertical = self.output.verticalScrollBar()
        horizontal = self.output.horizontalScrollBar()
        vertical_value = vertical.value()
        horizontal_value = horizontal.value()
        was_at_bottom = vertical_value >= max(0, vertical.maximum() - 2)
        cursor = self.output.textCursor()
        cursor_position = cursor.position()
        cursor_anchor = cursor.anchor()

        self.output.setPlainText(text)

        cursor = self.output.textCursor()
        text_length = len(text)
        cursor.setPosition(min(cursor_anchor, text_length))
        cursor.setPosition(min(cursor_position, text_length), QTextCursor.KeepAnchor)
        self.output.setTextCursor(cursor)
        horizontal.setValue(min(horizontal_value, horizontal.maximum()))
        vertical.setValue(
            vertical.maximum() if was_at_bottom else min(vertical_value, vertical.maximum())
        )

    def _set_metric(self, key: str, load: float, text: str) -> None:
        self.metric_bars[key].set_load(load)
        self.metric_labels[key].setText(text)
