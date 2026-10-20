# SPDX-License-Identifier: MPL-2.0
"""Capture isolated operator UI; never load the operator's saved configuration."""
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SHIP_SIMULATOR_HEADLESS_MAP"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from maritime_simulator.domain.commands import SetControlMode
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.ui.main_window import V2MainWindow
from maritime_simulator.visual.water_mask import OSMNavigableWaterMask
from ui.style import apply_global_ui_style


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/operator-polish-ui")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_global_ui_style(app)
    initialize = OSMNavigableWaterMask.__init__
    def offline(self, *args, **kwargs):
        kwargs["allow_network"] = False
        initialize(self, *args, **kwargs)
    OSMNavigableWaterMask.__init__ = offline
    with tempfile.TemporaryDirectory(prefix="mss-polish-ui-") as work:
        window = V2MainWindow(config_dir=Path(work))
        try:
            window.timer.stop()
            window.output_runtime.stop()
            window.resize(1280, 800)
            window.show()
            for mode in (ControlMode.SENSOR, ControlMode.TRACK, ControlMode.SHIP):
                window.service.submit(SetControlMode(mode))
                window.service.advance_wall_time(0)
                window._refresh(force=True)
                QTest.qWait(250)
                window.status_strip.grab().save(str(output / f"status-{mode.value}.png"))
                assert window.status_strip.track_cell.isVisible() == (mode is ControlMode.TRACK)
                for name, label in window.status_fields.items():
                    if label.isVisible():
                        assert label.fontMetrics().horizontalAdvance(label.text()) <= label.width(), (name, label.width(), label.text())
            window._open_settings()
            dialog = window.settings_dialog
            dialog.resize(1100, 730)
            app.processEvents()
            dialog.grab().save(str(output / "startup.png"))
            dialog.tabs.setCurrentIndex(dialog._input_tab_index)
            for index, channel in enumerate(("ROR", "RSA")):
                dialog.input_endpoints.setCurrentIndex(index)
                app.processEvents()
                dialog.grab().save(str(output / f"input-{channel}.png"))
            for button in (dialog.input_view,):
                rect = button.rect().translated(button.mapTo(dialog, QPoint(0, 0)))
                assert dialog.rect().contains(rect)
            dialog.hide()
            window._open_performance()
            panel = window.performance_dialog.layout().itemAt(0).widget()
            panel.enabled.setChecked(True)
            for _ in range(30):
                window.service.performance_monitor.record_duration("ui_refresh", .001)
                window.service.performance_monitor.record_duration("output_pump", .001)
                QTest.qWait(50)
            panel.refresh()
            app.processEvents()
            window.performance_dialog.grab().save(str(output / "performance.png"))
            window.performance_dialog.hide()
            window._open_about()
            app.processEvents()
            window.about_dialog.grab().save(str(output / "about.png"))
            window.about_dialog.hide()
        finally:
            window._interactive_close_confirmation_enabled = lambda: False
            window.close()
            app.processEvents()
    print(f"Operator UI captured to {output}")


if __name__ == "__main__":
    main()
