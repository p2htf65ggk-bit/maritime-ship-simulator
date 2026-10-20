#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import json
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

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from maritime_simulator.domain.state import SimulationState
from maritime_simulator.ui.forward_view import ForwardViewDialog
from maritime_simulator.visual.geodata import OverpassGeodataProvider
from maritime_simulator.visual.models import GeoLine, GeoObject, GeoPolygon, GeographicScene
from ui.style import apply_global_ui_style


def main() -> int:
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in {"offscreen", "minimal"}:
        raise SystemExit("This smoke test requires an interactive Qt platform with WebGL support.")

    output = Path(os.environ.get(
        "FORWARD_VIEW_QT_SCREENSHOT",
        Path(tempfile.gettempdir()) / "ship-simulator-forward-view-qt.png",
    ))
    cache_dir = Path(tempfile.mkdtemp(prefix="ship-simulator-visual-cache-"))
    state = SimulationState()
    state.position.latitude_deg = 51.9440
    state.position.longitude_deg = 4.1450
    state.motion.heading_deg = 12.0
    state.motion.cog_deg = 13.0
    state.motion.sog_kn = 11.5
    state.environment.true_wind_from_deg = 245.0
    state.environment.true_wind_speed_kn = 16.0
    snapshot = state.snapshot()

    provider = OverpassGeodataProvider(cache_dir)
    scene_id = provider.area_key(snapshot.latitude_deg, snapshot.longitude_deg)
    lat = snapshot.latitude_deg
    lon = snapshot.longitude_deg
    scene = GeographicScene(
        scene_id=scene_id,
        reference_latitude_deg=lat,
        reference_longitude_deg=lon,
        polygons=(
            GeoPolygon(
                "land-1",
                "land",
                (
                    (lat + 0.004, lon - 0.006),
                    (lat + 0.012, lon - 0.006),
                    (lat + 0.012, lon - 0.001),
                    (lat + 0.004, lon - 0.001),
                    (lat + 0.004, lon - 0.006),
                ),
                0.8,
                "OpenStreetMap",
                "Qt validation shore",
            ),
            GeoPolygon(
                "building-1",
                "building",
                (
                    (lat + 0.006, lon - 0.005),
                    (lat + 0.0075, lon - 0.005),
                    (lat + 0.0075, lon - 0.003),
                    (lat + 0.006, lon - 0.003),
                    (lat + 0.006, lon - 0.005),
                ),
                28.0,
                "OpenStreetMap",
                "Qt validation terminal",
            ),
        ),
        lines=(
            GeoLine(
                "quay-1",
                "quay",
                ((lat + 0.0035, lon - 0.004), (lat + 0.010, lon - 0.004)),
                8.0,
                2.0,
                "OpenStreetMap",
                "Qt validation quay",
            ),
        ),
        objects=(
            GeoObject(
                "buoy-1",
                "buoy-lateral",
                lat + 0.003,
                lon + 0.001,
                "OpenSeaMap",
                "Qt validation buoy",
                "green",
                "conical",
                "Fl.G.4s",
            ),
        ),
        source_status="Cached Qt validation fixture",
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{scene_id}.json").write_text(
        json.dumps(scene.to_dict()),
        encoding="utf-8",
    )
    target = {
        "mmsi": 244000111,
        "name": "QT TEST TARGET",
        "ship_type": "Container",
        "lat": lat + 0.006,
        "lon": lon + 0.0015,
        "heading": 190.0,
        "course": 188.0,
        "speed": 8.5,
        "rot": -1.2,
        "length": 145.0,
        "beam": 23.0,
    }

    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    dialog = ForwardViewDialog(lambda: snapshot, lambda _snapshot: (target,), cache_dir)
    dialog.resize(1180, 720)
    dialog.show()
    result: dict = {"diagnostics": None, "screenshot": str(output), "saved": False}

    def resized() -> None:
        dialog.resize(900, 560)

    def collect() -> None:
        dialog.request_diagnostics(finish)

    def finish(diagnostics) -> None:
        result["diagnostics"] = diagnostics
        result["manager_status"] = dialog.manager.status
        result["manager_scene"] = (
            None if dialog.manager.scene is None else dialog.manager.scene.scene_id
        )
        result["expected_scene"] = scene_id
        result["cache_files"] = sorted(path.name for path in cache_dir.iterdir())
        result["saved"] = dialog.grab().save(str(output))
        dialog.shutdown()
        dialog.close()
        app.quit()

    def timeout() -> None:
        if result["diagnostics"] is None:
            result["error"] = "QtWebEngine diagnostics timed out"
            finish(None)

    QTimer.singleShot(2200, resized)
    QTimer.singleShot(4500, collect)
    QTimer.singleShot(12_000, timeout)
    app.exec()
    print(json.dumps(result, indent=2))
    diagnostics = result.get("diagnostics")
    if not result["saved"] or not isinstance(diagnostics, dict):
        return 1
    if not diagnostics.get("ready") or diagnostics.get("frames", 0) < 3:
        return 1
    if diagnostics.get("aisObjects") != 1 or diagnostics.get("geographicObjects", 0) < 2:
        return 1
    if diagnostics.get("assetModelsLoaded", 0) < 2 or diagnostics.get("assetModelsFailed", 0) != 0:
        return 1
    if diagnostics.get("uniqueSampleColours", 0) < 2:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
