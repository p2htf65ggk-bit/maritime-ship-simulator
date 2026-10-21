#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_local_environment(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding the host environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key.isidentifier():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_local_environment(ROOT / ".env")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")


def _configure_webengine_environment(platform: str | None = None) -> None:
    """Keep Chromium active when Windows briefly misreports an embedded view as occluded."""
    if not (platform or sys.platform).startswith("win"):
        return
    variable = "QTWEBENGINE_CHROMIUM_FLAGS"
    flags = os.environ.get(variable, "").strip()
    feature = "CalculateNativeWinOcclusion"
    if feature not in flags:
        match = re.search(r"--disable-features=([^\s]+)", flags)
        if match:
            existing = [item for item in match.group(1).split(",") if item]
            replacement = "--disable-features=" + ",".join([*existing, feature])
            flags = flags[:match.start()] + replacement + flags[match.end():]
        else:
            flags = f"{flags} --disable-features={feature}".strip()
    background_flag = "--disable-backgrounding-occluded-windows"
    if background_flag not in flags:
        flags = f"{flags} {background_flag}".strip()
    os.environ[variable] = flags


_configure_webengine_environment()

from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from maritime_simulator.ui import V2MainWindow
from maritime_simulator.application.paths import bundled_resource_path, user_config_dir
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.application.startup import (
    configure_startup_logging,
    install_exception_logging,
    show_startup_failure,
)
from maritime_simulator import __version__
from ui.app_metadata import APP_DISPLAY_NAME, APP_NAME
from ui.style import apply_global_ui_style


LINUX_DESKTOP_FILE_ID = "maritime-ship-simulator-v2"


def _validate_runtime_resources() -> None:
    required = (
        bundled_resource_path("FREEWARE_LICENSE.txt"),
        bundled_resource_path("assets", "app_icon.png"),
        bundled_resource_path("assets", "geodata", "osm-simplified-land-polygons-3857.zip"),
        bundled_resource_path("assets", "geodata", "README.txt"),
        bundled_resource_path("ui", "web", "map.html"),
        bundled_resource_path("ui", "web", "map_select.js"),
        bundled_resource_path("ui", "web", "leaflet", "leaflet.js"),
        bundled_resource_path("ui", "web", "forward_view", "cesium.html"),
        bundled_resource_path("ui", "web", "forward_view", "cesium_simple_view.css"),
        bundled_resource_path("ui", "web", "forward_view", "cesium_simple_view.js"),
        bundled_resource_path("ui", "web", "forward_view", "google_maps_3d.html"),
        bundled_resource_path("ui", "web", "forward_view", "google_maps_3d.css"),
        bundled_resource_path("ui", "web", "forward_view", "google_maps_3d.js"),
        bundled_resource_path("routes", "default_route.rtz"),
        bundled_resource_path("maritime_simulator", "resources", "factory_vessel_profiles.json"),
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing packaged application resources: " + ", ".join(missing))


def _validate_s57_smoke_cell(path: Path) -> tuple[str, int]:
    import tempfile
    from maritime_simulator.visual.s57 import S57Store
    with tempfile.TemporaryDirectory(prefix="maritime-s57-smoke-") as directory:
        store = S57Store(directory)
        codes = store.import_files([path])
        if len(codes) != 1:
            raise RuntimeError("S57 packaged smoke requires exactly one ENC cell")
        record, = store.records()
        if not store.features(record["bounds"], 14)["features"]:
            raise RuntimeError("Packaged S57 import produced no visible geometry")
        return record["code"], record["feature_count"]


def _schedule_dialog_smoke(app: QApplication, window: V2MainWindow) -> None:
    def open_ais_settings():
        dialog = window.ais_controls.settings_dialog
        dialog.show()
        return dialog

    def open_input_monitor():
        window._open_settings()
        dialog = window.settings_dialog.input_monitor
        dialog.show()
        return dialog

    steps = (
        lambda: (window._open_settings(), window.settings_dialog)[1],
        lambda: (window._open_protocol_output(), window.protocol_dialog)[1],
        lambda: (window._open_advanced(), window.advanced_dialog)[1],
        lambda: (window._open_instruments(), window.instrument_dialog)[1],
        lambda: (window._open_forward_view(), window.forward_view_dialog)[1],
        lambda: (window._open_performance(), window.performance_dialog)[1],
        lambda: (window._open_about(), window.about_dialog)[1],
        lambda: (window._open_user_guide(), window.user_guide_dialog)[1],
        open_ais_settings,
        open_input_monitor,
    )
    state = {"index": 0, "previous": None}

    def advance() -> None:
        previous = state["previous"]
        if previous is not None:
            previous.hide()
        if state["index"] >= len(steps):
            window.close()
            app.exit(0)
            return
        dialog = steps[state["index"]]()
        if dialog is None:
            raise RuntimeError(f"Dialog smoke step {state['index']} did not create a dialog")
        dialog.resize(max(480, dialog.width()), max(360, dialog.height()))
        dialog.show()
        dialog.raise_()
        state["previous"] = dialog
        state["index"] += 1
        QTimer.singleShot(160, advance)

    QTimer.singleShot(200, advance)


def main() -> int:
    config_dir = user_config_dir()
    diagnostics = configure_startup_logging(config_dir, __version__)
    app = None
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)
        install_exception_logging(diagnostics)
        app.setApplicationName(f"{APP_NAME} V2")
        app.setApplicationDisplayName(f"{APP_DISPLAY_NAME} V2")
        app.setApplicationVersion(__version__)
        if sys.platform.startswith("linux"):
            app.setDesktopFileName(LINUX_DESKTOP_FILE_ID)
        icon = bundled_resource_path("assets", "app_icon.png")
        if icon.exists():
            app_icon = QIcon(str(icon))
            if not app_icon.isNull():
                app.setWindowIcon(app_icon)
        apply_global_ui_style(app)
        _validate_runtime_resources()
        window = V2MainWindow(
            SimulatorService(profile_file=config_dir / "vessel_profiles.json"),
            config_dir=config_dir,
        )
        automated_smoke = "--smoke-test" in sys.argv or "--dialog-smoke-test" in sys.argv
        if automated_smoke:
            import pyogrio
            from maritime_simulator.visual.s57 import layer_descriptions
            if "S57" not in pyogrio.list_drivers() or "DEPARE" not in layer_descriptions():
                raise RuntimeError("Packaged S57 driver/data validation failed")
            smoke_cell = os.environ.get("SHIP_SIMULATOR_S57_SMOKE_CELL")
            if smoke_cell:
                code, features = _validate_s57_smoke_cell(Path(smoke_cell))
                diagnostics.logger.info("packaged S57 import verified: %s, %s features", code, features)
            # LaunchServices uses the native Cocoa platform, so a smoke close
            # would otherwise wait forever in the interactive confirmation.
            window._interactive_close_confirmation_enabled = lambda: False
        if not app.windowIcon().isNull():
            window.setWindowIcon(app.windowIcon())
        window.show()
        diagnostics.logger.info("main window initialized")
        if "--dialog-smoke-test" in sys.argv:
            _schedule_dialog_smoke(app, window)
        elif "--smoke-test" in sys.argv:
            def finish_smoke_test() -> None:
                window.close()
                app.exit(0)

            QTimer.singleShot(2500, finish_smoke_test)
        exit_code = app.exec()
        diagnostics.logger.info("shutdown exit_code=%s", exit_code)
        return exit_code
    except BaseException as error:
        show_startup_failure(diagnostics, error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
