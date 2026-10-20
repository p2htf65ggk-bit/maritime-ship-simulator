# SPDX-License-Identifier: MPL-2.0
import json
import math
import os
from dataclasses import asdict
from pathlib import Path
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

import pytest
from PySide6.QtWidgets import QApplication

from maritime_simulator.domain.commands import SetRouteOptions
from maritime_simulator.simulation.route_geometry import RouteGeometry
from maritime_simulator.simulation.route_io import load_rtz
from maritime_simulator.ui.main_window import V2MainWindow


DEFAULT_ROUTE = Path(__file__).resolve().parents[2] / "routes" / "default_route.rtz"


@pytest.fixture
def window(tmp_path):
    application = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    window.timer.stop()
    yield window
    window.close()
    window.deleteLater()
    application.processEvents()


def test_bundled_route_is_new_york_to_baltimore_with_ten_knot_legs():
    root = ET.parse(DEFAULT_ROUTE).getroot()
    assert root.find("{*}routeInfo").get("routeName") == "USNYC-USBAL"
    route = load_rtz(DEFAULT_ROUTE)
    assert len(route) == 82
    assert (route[0].latitude_deg, route[0].longitude_deg) == (40.643759, -74.056721)
    assert (route[-1].latitude_deg, route[-1].longitude_deg) == (39.225307, -76.536957)
    assert all(waypoint.speed_kn == 10.0 for waypoint in route)
    assert all(-90 <= point.latitude_deg <= 90 and -180 <= point.longitude_deg <= 180 for point in route)
    geometry = RouteGeometry(route)
    assert all(math.isfinite(tick) for tick in geometry.ticks_nm)
    assert all(first < second for first, second in zip(geometry.ticks_nm, geometry.ticks_nm[1:]))
    assert any(point.turn_mode == "fixed_radius" and point.turn_radius_nm > 0 for point in route)


def test_fresh_start_uses_requested_default_route_options(window):
    snapshot = window.service.snapshot()
    assert snapshot.route_waypoints == load_rtz(DEFAULT_ROUTE)
    assert snapshot.use_planned_speed
    assert snapshot.use_planned_turns
    assert snapshot.route_end_action == "Reverse"
    assert window.route_planner.use_planned_speed.isChecked()
    assert window.route_planner.use_planned_turns.isChecked()
    assert window.route_planner.end_action.currentText() == "Reverse"
    assert window.auto_scroll.isChecked()


def test_load_default_restores_route_and_track_options(window):
    window._set_route(load_rtz(DEFAULT_ROUTE)[:3])
    window._submit_ui_command(SetRouteOptions(
        use_planned_speed=False, use_planned_turns=False, end_action="Stop",
    ))
    window.auto_scroll.setChecked(False)
    window._load_default_route()
    snapshot = window.service.snapshot()
    assert snapshot.route_waypoints == load_rtz(DEFAULT_ROUTE)
    assert snapshot.use_planned_speed and snapshot.use_planned_turns
    assert snapshot.route_end_action == "Reverse"
    assert window.auto_scroll.isChecked()


@pytest.mark.parametrize("custom_route", (False, True))
def test_explicit_saved_route_options_override_factory_defaults(tmp_path, custom_route):
    application = QApplication.instance() or QApplication([])
    saved = {"use_planned_speed": False, "use_planned_turns": False, "route_end_action": "Stop"}
    expected_route = load_rtz(DEFAULT_ROUTE)[:3] if custom_route else load_rtz(DEFAULT_ROUTE)
    if custom_route:
        saved["route"] = [asdict(point) for point in expected_route]
    path = tmp_path / "app_state_v2.json"
    original = json.dumps(saved)
    path.write_text(original, encoding="utf-8")
    window = V2MainWindow(config_dir=tmp_path)
    window.timer.stop()
    try:
        snapshot = window.service.snapshot()
        assert snapshot.route_waypoints == expected_route
        assert not snapshot.use_planned_speed
        assert not snapshot.use_planned_turns
        assert snapshot.route_end_action == "Stop"
        restored = json.loads(path.read_text(encoding="utf-8"))
        assert all(restored[key] == value for key, value in saved.items())
    finally:
        window.close()
        window.deleteLater()
        application.processEvents()
