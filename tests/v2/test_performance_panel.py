# SPDX-License-Identifier: MPL-2.0
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from maritime_simulator.application.metrics import PerformanceMonitor
from maritime_simulator.ui.performance_panel import PerformancePanel


def test_performance_panel_enables_profiling_and_renders_snapshot():
    app = QApplication.instance() or QApplication([])
    monitor = PerformanceMonitor(enabled=False)
    panel = PerformancePanel(monitor)
    panel.timer.stop()
    panel.enabled.setChecked(True)
    monitor.increment("test")
    panel.refresh()
    assert monitor.enabled
    assert '"test": 1' in panel.output.toPlainText()
    panel.deleteLater()
    app.processEvents()
