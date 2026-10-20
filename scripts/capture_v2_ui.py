#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.ui.main_window import V2MainWindow
from ui.style import apply_global_ui_style


MODES = {
    "sensor": ControlMode.SENSOR,
    "ship": ControlMode.SHIP,
    "track": ControlMode.TRACK,
    "external": ControlMode.EXTERNAL_RUDDER,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a deterministic V2 UI screenshot")
    parser.add_argument("--mode", choices=tuple(MODES), default="sensor")
    parser.add_argument("--width", type=int, default=1366)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument(
        "--dialog",
        choices=("main", "route", "settings", "output", "advanced", "instruments", "about"),
        default="main",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    service = SimulatorService()
    temporary = tempfile.TemporaryDirectory(prefix="maritime-v2-ui-qa-")
    window = V2MainWindow(service, config_dir=Path(temporary.name))
    window.timer.stop()
    window.resize(args.width, args.height)
    window._request_mode(MODES[args.mode])
    service.advance_wall_time(0.0)
    window._refresh(force=True)
    window.show()
    app.processEvents()
    target = window.centralWidget()
    if args.dialog == "route":
        window._request_mode(ControlMode.TRACK)
        target = window.route_panel
    elif args.dialog != "main":
        open_dialog = {
            "settings": window._open_settings,
            "output": window._open_protocol_output,
            "advanced": window._open_advanced,
            "instruments": window._open_instruments,
            "about": window._open_about,
        }[args.dialog]
        open_dialog()
        target = {
            "settings": window.settings_dialog,
            "output": window.protocol_dialog,
            "advanced": window.advanced_dialog,
            "instruments": window.instrument_dialog,
            "about": window.about_dialog,
        }[args.dialog]
    QTest.qWait(80)
    app.processEvents()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Render the widget directly; the offscreen backing store may contain stale
    # top-level regions immediately after a hidden panel changes visibility.
    if target is None:
        raise SystemExit("Screenshot target was not created")
    image = QImage(target.size(), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    target.render(image)
    if not image.save(str(args.output)):
        raise SystemExit(f"Could not save screenshot: {args.output}")
    window.close()
    app.processEvents()
    temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
