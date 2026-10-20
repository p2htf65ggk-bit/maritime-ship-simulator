# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import ssl
import time

from maritime_simulator.application.ais import AISRepository
from maritime_simulator.domain.state import SimulationState
from maritime_simulator.ui.forward_view import _enforce_web_view_zoom, _wheel_zoom_steps
from maritime_simulator.visual.coordinates import LocalFrame, local_offset_m, range_bearing_m
from maritime_simulator.visual.geodata import (
    OverpassGeodataProvider,
    parse_overpass_scene,
    verified_ssl_context,
)
from maritime_simulator.visual.models import GeographicScene, VisualSettings
from maritime_simulator.visual.world import VisualWorldManager


def test_forward_view_zoom_guard_restores_browser_scale_without_touching_map_state():
    class FakeView:
        factor = 1.6

        def zoomFactor(self):
            return self.factor

        def setZoomFactor(self, factor):
            self.factor = factor

    view = FakeView()
    _enforce_web_view_zoom(view)
    assert view.factor == 1.0

    source = (
        Path(__file__).resolve().parents[2]
        / "src/maritime_simulator/ui/forward_view.py"
    ).read_text(encoding="utf-8")
    assert "QApplication.instance().installEventFilter" not in source
    assert "class _ForwardViewInputFilter" in source
    assert "root.findChildren(QWidget)" in source


def test_forward_view_native_pinch_is_routed_to_camera_zoom_locally():
    from PySide6.QtCore import QPoint

    class WheelEvent:
        def __init__(self, pixel_y=0, angle_y=0):
            self._pixel = QPoint(0, pixel_y)
            self._angle = QPoint(0, angle_y)

        def pixelDelta(self):
            return self._pixel

        def angleDelta(self):
            return self._angle

    assert _wheel_zoom_steps(WheelEvent(pixel_y=60)) == 0.5
    assert _wheel_zoom_steps(WheelEvent(angle_y=-120)) == -1.0

    root = Path(__file__).resolve().parents[2]
    dialog = (root / "src/maritime_simulator/ui/forward_view.py").read_text(encoding="utf-8")
    google = (root / "ui/web/forward_view/google_maps_3d.js").read_text(encoding="utf-8")
    cesium = (root / "ui/web/forward_view/cesium_simple_view.js").read_text(encoding="utf-8")
    assert "class _ForwardViewGestureMixin" in dialog
    assert "QEvent.Type.NativeGesture" in dialog
    assert "Qt.NativeGestureType.ZoomNativeGesture" in dialog
    assert "window.applyNativeCameraZoom" in dialog
    assert "window.applyNativeCameraZoom" in google
    assert "window.applyNativeCameraZoom" in cesium


def test_local_frame_uses_east_and_north_metres_without_absolute_earth_coordinates():
    east, north = local_offset_m(52.0, 3.0, 52.01, 3.01)
    assert 675.0 < east < 690.0
    assert 1110.0 < north < 1120.0
    frame = LocalFrame(52.0, 3.0)
    assert frame.offset_m(52.01, 3.01) == (east, north)
    distance, bearing = range_bearing_m(52.0, 3.0, 52.01, 3.01)
    assert distance == math.hypot(east, north)
    assert 30.0 < bearing < 35.0


def test_overpass_parser_maps_osm_and_openseamap_objects_to_normalized_scene():
    payload = {
        "elements": [
            {
                "type": "way", "id": 1,
                "tags": {"building": "yes", "building:levels": "4", "name": "Warehouse"},
                "geometry": [
                    {"lat": 52.0, "lon": 3.0}, {"lat": 52.0, "lon": 3.001},
                    {"lat": 52.001, "lon": 3.001}, {"lat": 52.001, "lon": 3.0},
                    {"lat": 52.0, "lon": 3.0},
                ],
            },
            {
                "type": "way", "id": 2,
                "tags": {"natural": "coastline"},
                "geometry": [{"lat": 52.0, "lon": 3.0}, {"lat": 52.002, "lon": 3.0}],
            },
            {
                "type": "node", "id": 3, "lat": 52.001, "lon": 3.002,
                "tags": {
                    "seamark:type": "buoy_lateral",
                    "seamark:buoy_lateral:colour": "red",
                    "seamark:light:character": "Fl",
                    "seamark:light:period": "4",
                },
            },
            {
                "type": "way", "id": 4,
                "tags": {"man_made": "storage_tank", "height": "18 m"},
                "geometry": [
                    {"lat": 52.0, "lon": 3.002}, {"lat": 52.0, "lon": 3.003},
                    {"lat": 52.001, "lon": 3.003}, {"lat": 52.001, "lon": 3.002},
                    {"lat": 52.0, "lon": 3.002},
                ],
            },
            {
                "type": "way", "id": 5,
                "tags": {"water": "basin"},
                "geometry": [
                    {"lat": 52.002, "lon": 3.002}, {"lat": 52.002, "lon": 3.003},
                    {"lat": 52.003, "lon": 3.003}, {"lat": 52.003, "lon": 3.002},
                    {"lat": 52.002, "lon": 3.002},
                ],
            },
            {
                "type": "way", "id": 6,
                "tags": {"landuse": "forest", "name": "Harbour Trees"},
                "geometry": [
                    {"lat": 52.003, "lon": 3.0}, {"lat": 52.003, "lon": 3.001},
                    {"lat": 52.004, "lon": 3.001}, {"lat": 52.004, "lon": 3.0},
                    {"lat": 52.003, "lon": 3.0},
                ],
            },
            {
                "type": "way", "id": 7,
                "tags": {"highway": "primary", "lanes": "2"},
                "geometry": [{"lat": 52.0, "lon": 3.004}, {"lat": 52.002, "lon": 3.004}],
            },
            {
                "type": "way", "id": 8,
                "tags": {"highway": "secondary", "bridge": "yes"},
                "geometry": [{"lat": 52.0, "lon": 3.005}, {"lat": 52.002, "lon": 3.005}],
            },
            {
                "type": "way", "id": 9,
                "tags": {"natural": "water", "name": "River"},
                "geometry": [
                    {"lat": 52.004, "lon": 3.002}, {"lat": 52.004, "lon": 3.003},
                    {"lat": 52.005, "lon": 3.003}, {"lat": 52.005, "lon": 3.002},
                    {"lat": 52.004, "lon": 3.002},
                ],
            },
        ],
    }
    scene = parse_overpass_scene(
        payload,
        scene_id="test-area",
        reference_latitude_deg=52.0,
        reference_longitude_deg=3.0,
    )
    assert scene.polygons[0].kind == "building"
    assert scene.polygons[0].height_m == 12.8
    assert scene.polygons[1].kind == "storage-tank"
    assert scene.polygons[1].height_m == 18.0
    assert scene.polygons[2].kind == "harbour-basin"
    assert scene.polygons[3].kind == "forest"
    assert scene.polygons[4].kind == "water-area"
    assert [line.kind for line in scene.lines] == ["coastline", "road", "bridge"]
    assert scene.lines[1].width_m == 6.4
    assert scene.objects[0].source == "OpenSeaMap"
    assert scene.objects[0].colour == "red"
    assert scene.objects[0].light_character == "Fl 4s"


def test_overpass_parser_stitches_water_relation_into_surface_mask():
    payload = {
        "elements": [{
            "type": "relation",
            "id": 91,
            "tags": {"natural": "water", "name": "Test River"},
            "members": [
                {
                    "type": "way",
                    "role": "outer",
                    "geometry": [
                        {"lat": 52.0, "lon": 3.0},
                        {"lat": 52.0, "lon": 3.01},
                        {"lat": 52.01, "lon": 3.01},
                    ],
                },
                {
                    "type": "way",
                    "role": "outer",
                    "geometry": [
                        {"lat": 52.0, "lon": 3.0},
                        {"lat": 52.01, "lon": 3.0},
                        {"lat": 52.01, "lon": 3.01},
                    ],
                },
            ],
        }],
    }
    scene = parse_overpass_scene(
        payload,
        scene_id="water-relation",
        reference_latitude_deg=52.0,
        reference_longitude_deg=3.0,
    )
    assert len(scene.polygons) == 1
    assert scene.polygons[0].kind == "water-area"
    assert scene.polygons[0].name == "Test River"
    assert scene.polygons[0].coordinates[0] == scene.polygons[0].coordinates[-1]


def test_geodata_provider_reuses_processed_disk_cache(tmp_path: Path):
    provider = OverpassGeodataProvider(tmp_path)
    key = provider.area_key(52.0, 3.0)
    cached = GeographicScene(key, 52.0, 3.0, source_status="Original")
    (tmp_path / f"{key}.json").write_text(json.dumps(cached.to_dict()), encoding="utf-8")
    provider._fetch = lambda _query: (_ for _ in ()).throw(AssertionError("network must not be used"))
    loaded = provider.load(52.0, 3.0)
    assert loaded.scene_id == key
    assert loaded.source_status == "Cached OSM/OpenSeaMap data"


def test_visual_geodata_tls_context_keeps_certificate_verification_enabled():
    context = verified_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_visual_geodata_uses_a_second_endpoint_after_transient_failure(tmp_path: Path):
    provider = OverpassGeodataProvider(tmp_path)
    calls = []

    def fetch(endpoint, _query):
        calls.append(endpoint)
        if len(calls) == 1:
            raise TimeoutError("busy")
        return {"elements": []}

    provider._fetch_endpoint = fetch
    assert provider._fetch("test") == {"elements": []}
    assert calls == list(provider.endpoints[:2])


def test_visual_geodata_temporarily_skips_a_timed_out_endpoint(tmp_path: Path):
    provider = OverpassGeodataProvider(tmp_path)
    calls = []

    def fetch(endpoint, _query):
        calls.append(endpoint)
        if endpoint == provider.endpoints[0]:
            raise TimeoutError("busy")
        return {"elements": []}

    provider._fetch_endpoint = fetch
    assert provider._fetch("first") == {"elements": []}
    assert provider._fetch("second") == {"elements": []}
    assert calls == [provider.endpoints[0], provider.endpoints[1], provider.endpoints[1]]


def test_visual_geodata_keeps_successful_layers_when_one_layer_fails(tmp_path: Path):
    provider = OverpassGeodataProvider(tmp_path)

    def fetch(query):
        if '"natural"="coastline"' in query:
            raise OSError("terrain response too large")
        if '"seamark:type"' in query:
            return {
                "elements": [{
                    "type": "node",
                    "id": 42,
                    "lat": 52.0,
                    "lon": 3.0,
                    "tags": {"seamark:type": "buoy_lateral"},
                }]
            }
        return {"elements": []}

    provider._fetch = fetch
    scene = provider.load(52.0, 3.0, radius_nm=12.0)
    assert [item.feature_id for item in scene.objects] == ["node-42"]
    assert "terrain unavailable" in scene.source_status
    assert "Water-only fallback" not in scene.source_status


def test_visual_geodata_retries_oversized_terrain_with_local_bounds(tmp_path: Path):
    provider = OverpassGeodataProvider(tmp_path)
    terrain_queries = []

    def fetch(query):
        if '"natural"="coastline"' not in query:
            return {"elements": []}
        terrain_queries.append(query)
        if len(terrain_queries) == 1:
            raise ValueError("response exceeded 20 MB")
        return {
            "elements": [{
                "type": "way",
                "id": 7,
                "tags": {"natural": "land"},
                "geometry": [
                    {"lat": 52.0, "lon": 3.0},
                    {"lat": 52.0, "lon": 3.001},
                    {"lat": 52.001, "lon": 3.001},
                    {"lat": 52.0, "lon": 3.0},
                ],
            }]
        }

    provider._fetch = fetch
    scene = provider.load(52.0, 3.0, radius_nm=12.0)
    assert len(terrain_queries) == 2
    assert [area.kind for area in scene.polygons] == ["land"]
    assert "terrain limited to local detail area" in scene.source_status


def test_visual_world_does_not_overwrite_completed_area_before_poll(tmp_path: Path):
    snapshot = SimulationState().snapshot()
    provider = OverpassGeodataProvider(tmp_path)
    scene_id = provider.area_key(snapshot.latitude_deg, snapshot.longitude_deg)
    cached = GeographicScene(scene_id, snapshot.latitude_deg, snapshot.longitude_deg)
    (tmp_path / f"{scene_id}.json").write_text(json.dumps(cached.to_dict()), encoding="utf-8")
    manager = VisualWorldManager(provider)
    assert manager.request_area(snapshot)
    for _ in range(100):
        if not manager.request_area(snapshot) and manager.poll_area():
            break
        time.sleep(0.005)
    else:
        raise AssertionError("cached visual area was not published")
    assert manager.scene is not None
    assert manager.scene.scene_id == scene_id
    manager.close()


def test_visual_world_payload_culls_distant_ais_and_derives_environment(tmp_path: Path):
    state = SimulationState()
    state.position.latitude_deg = 52.0
    state.position.longitude_deg = 3.0
    state.environment.true_wind_speed_kn = 20.0
    snapshot = state.snapshot()
    manager = VisualWorldManager(
        OverpassGeodataProvider(tmp_path),
        now=lambda: datetime(2026, 9, 12, 12, 0, 0),
    )
    near = {"mmsi": 1, "lat": 52.01, "lon": 3.0, "heading": 15, "course": 16, "speed": 8}
    far = {"mmsi": 2, "lat": 53.0, "lon": 3.0, "heading": 20, "course": 20, "speed": 8}
    payload = manager.state_payload(snapshot, (near, far), VisualSettings(draw_distance_nm=10))
    manager.close()
    assert [target["mmsi"] for target in payload["ais_targets"]] == [1]
    assert payload["environment"]["sea_state"] == 4.0
    assert payload["camera"]["height_m"] == 15.0
    assert payload["own_ship"]["heading"] == snapshot.heading_deg


def test_visual_world_caps_payload_to_renderer_target_limit(tmp_path: Path):
    snapshot = SimulationState().snapshot()
    targets = (
        {
            "mmsi": index + 1,
            "lat": snapshot.latitude_deg + index * 0.000001,
            "lon": snapshot.longitude_deg,
            "heading": 10,
            "course": 10,
            "speed": 5,
        }
        for index in range(120)
    )
    manager = VisualWorldManager(OverpassGeodataProvider(tmp_path))
    payload = manager.state_payload(snapshot, targets)
    manager.close()
    assert len(payload["ais_targets"]) == 80


def test_visual_world_payload_exposes_complete_georeferenced_camera_configuration(tmp_path: Path):
    snapshot = SimulationState().snapshot()
    settings = VisualSettings(
        camera_height_m=20.0,
        camera_longitudinal_m=12.5,
        camera_lateral_m=-2.0,
        camera_pitch_deg=1.5,
        camera_roll_deg=-0.5,
        waterline_height_m=41.2,
    )
    manager = VisualWorldManager(OverpassGeodataProvider(tmp_path))
    payload = manager.state_payload(snapshot, (), settings)
    manager.close()
    assert payload["camera"] == {
        "height_m": 20.0,
        "longitudinal_m": 12.5,
        "lateral_m": -2.0,
        "pitch_deg": 1.5,
        "roll_deg": -0.5,
        "horizontal_fov_deg": 75.0,
        "show_bow": True,
    }
    assert payload["settings"]["waterline_height_m"] == 41.2


def test_visual_world_includes_vessel_dimensions_for_own_ship_model(tmp_path: Path):
    snapshot = SimulationState().snapshot()

    class Profile:
        display_name = "Test Vessel"
        length_m = 123.0
        beam_m = 21.0

    manager = VisualWorldManager(OverpassGeodataProvider(tmp_path))
    own_ship = manager.state_payload(snapshot, (), vessel_profile=Profile())["own_ship"]
    manager.close()
    assert own_ship["name"] == "Test Vessel"
    assert own_ship["length"] == 123.0
    assert own_ship["beam"] == 21.0


def test_visual_world_uses_cog_for_unavailable_ais_heading_and_projects_rot(tmp_path: Path):
    snapshot = SimulationState().snapshot()
    target = {
        "mmsi": 1,
        "lat": snapshot.latitude_deg + 0.001,
        "lon": snapshot.longitude_deg,
        "heading": 511,
        "course": 45.0,
        "speed": 8.0,
        "rot": 6.0,
        "dead_reckoning_age_s": 10.0,
    }
    manager = VisualWorldManager(OverpassGeodataProvider(tmp_path))
    rendered = manager.state_payload(snapshot, (target,))["ais_targets"][0]
    manager.close()
    assert rendered["cog"] == 45.0
    assert rendered["heading"] == 46.0
    assert rendered["prediction_age_s"] == 10.0
    assert rendered["dimensions_known"] is False


def test_visual_world_marks_reported_ais_dimensions_as_known(tmp_path: Path):
    snapshot = SimulationState().snapshot()
    target = {
        "mmsi": 2,
        "lat": snapshot.latitude_deg + 0.001,
        "lon": snapshot.longitude_deg,
        "length": 184.0,
        "beam": 29.0,
    }
    manager = VisualWorldManager(OverpassGeodataProvider(tmp_path))
    rendered = manager.state_payload(snapshot, (target,))["ais_targets"][0]
    manager.close()
    assert rendered["dimensions_known"] is True
    assert rendered["length"] == 184.0
    assert rendered["beam"] == 29.0
    assert rendered["output_active"] is True


def test_visual_payload_accepts_real_generated_ais_repository(tmp_path: Path):
    snapshot = SimulationState().snapshot()
    repository = AISRepository(generated_target_count=4, active_range_nm=5.0, random_seed=7)
    repository.use_generated(snapshot)
    manager = VisualWorldManager(OverpassGeodataProvider(tmp_path))
    payload = manager.state_payload(snapshot, repository.map_targets(snapshot))
    manager.close()
    assert len(payload["ais_targets"]) == 4
    assert all(target["source"] == "AIS" for target in payload["ais_targets"])
    assert all(target["output_active"] for target in payload["ais_targets"])


def test_forward_view_dialog_is_safe_in_headless_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SHIP_SIMULATOR_HEADLESS_MAP", "1")
    from PySide6.QtWidgets import QApplication
    from maritime_simulator.ui.forward_view import ForwardViewDialog

    app = QApplication.instance() or QApplication([])
    snapshot = SimulationState().snapshot()
    dialog = ForwardViewDialog(lambda: snapshot, lambda _snapshot: (), tmp_path)
    dialog.show()
    app.processEvents()
    dialog.refresh()
    assert dialog.view is None
    assert dialog.timer.isActive()
    dialog.hide()
    assert not dialog.timer.isActive()
    dialog.shutdown()


def test_forward_view_prefers_persisted_google_key_over_environment(tmp_path: Path, monkeypatch):
    from maritime_simulator.ui.forward_view import _google_tiles_api_key

    settings_path = tmp_path / "visual_settings_v2.json"
    settings_path.write_text('{"google_tiles_api_key": "stored-key"}', encoding="utf-8")
    monkeypatch.setenv("MARITIME_SIM_GOOGLE_3D_TILES_API_KEY", "environment-key")
    assert _google_tiles_api_key(settings_path) == "stored-key"


def test_forward_view_api_bridge_can_save_the_first_key_and_reload(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SHIP_SIMULATOR_HEADLESS_MAP", "1")
    monkeypatch.delenv("MARITIME_SIM_GOOGLE_3D_TILES_API_KEY", raising=False)
    from PySide6.QtWidgets import QApplication, QDialog
    from maritime_simulator.ui import forward_view

    app = QApplication.instance() or QApplication([])
    settings_path = tmp_path / "visual_settings_v2.json"
    dialog = forward_view.ForwardViewDialog(
        lambda: SimulationState().snapshot(), lambda _snapshot: (),
        tmp_path / "cache", settings_path=settings_path,
    )
    reloads = []
    monkeypatch.setattr(dialog, "_load_renderer_page", lambda: reloads.append(True))

    def enter_first_key(settings_dialog):
        assert settings_dialog.api_key.text() == ""
        settings_dialog.api_key.setText("first-key-test")
        settings_dialog.renderer_mode.setCurrentIndex(
            settings_dialog.renderer_mode.findData("google_maps_3d")
        )
        return QDialog.Accepted

    monkeypatch.setattr(forward_view._GoogleTilesSettingsDialog, "exec", enter_first_key)
    bridge = forward_view._VisualBridge(dialog)
    bridge.api_key_settings_requested.connect(dialog._open_api_key_settings)
    try:
        bridge.requestApiKeySettings()
        assert dialog.google_tiles_api_key == "first-key-test"
        assert dialog.renderer_mode == "google_maps_3d"
        assert forward_view.visual_settings_payload(settings_path)["google_tiles_api_key"] == "first-key-test"
        assert reloads == [True]
    finally:
        dialog.shutdown()
        app.processEvents()


def test_forward_view_restores_persisted_visual_quality_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SHIP_SIMULATOR_HEADLESS_MAP", "1")
    from PySide6.QtWidgets import QApplication
    from maritime_simulator.application.system_resources import SystemMemoryStatus
    from maritime_simulator.ui import forward_view

    settings_path = tmp_path / "visual_settings_v2.json"
    settings_path.write_text('{"visual_quality_mode": "low_memory"}', encoding="utf-8")
    monkeypatch.setattr(
        forward_view,
        "cached_memory_status",
        lambda: SystemMemoryStatus(16_384.0, 12_288.0, "test"),
    )

    app = QApplication.instance() or QApplication([])
    dialog = forward_view.ForwardViewDialog(
        lambda: SimulationState().snapshot(),
        lambda _snapshot: (),
        tmp_path / "cache",
        settings_path=settings_path,
    )
    assert dialog.visual_quality_mode == "low_memory"
    assert dialog._visual_quality_payload["name"] == "low_memory"
    assert dialog._visual_quality_payload["tile_cache_mb"] == 128
    dialog.shutdown()
    app.processEvents()


def test_renderer_probe_cancels_timeout_before_reply_deletion(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SHIP_SIMULATOR_HEADLESS_MAP", "1")
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from maritime_simulator.ui import forward_view

    class FakeReply(QObject):
        finished = Signal()

        def __init__(self):
            super().__init__()
            self.deleted = False
            self.is_running_calls = 0

        def attribute(self, _attribute):
            return 200

        def readAll(self):
            return b"{}"

        def isRunning(self):
            self.is_running_calls += 1
            if self.deleted:
                raise RuntimeError("reply accessed after deleteLater")
            return True

        def abort(self):
            self.finished.emit()

        def deleteLater(self):
            self.deleted = True

    class FakeNetworkManager:
        def __init__(self, reply):
            self.reply = reply

        def get(self, _request):
            return self.reply

    app = QApplication.instance() or QApplication([])
    dialog = forward_view.ForwardViewDialog(
        lambda: SimulationState().snapshot(),
        lambda _snapshot: (),
        tmp_path / "cache",
    )
    reply = FakeReply()
    dialog.google_tiles_api_key = "test-key"
    dialog._renderer_network_manager = FakeNetworkManager(reply)
    activated = []
    dialog._activate_renderer = lambda renderer, reason: activated.append((renderer, reason))
    monkeypatch.setattr(forward_view, "RENDERER_PROBE_TIMEOUT_MS", 1)

    dialog._probe_renderer()
    timeout_timer = dialog._renderer_probe_timer
    assert timeout_timer is not None and timeout_timer.isActive()
    reply.finished.emit()
    app.processEvents()
    QTest.qWait(10)
    app.processEvents()

    assert activated == [("photorealistic_tiles", "direct-tiles-available")]
    assert dialog._renderer_probe_reply is None
    assert dialog._renderer_probe_timer is None
    assert not timeout_timer.isActive()
    assert reply.deleted is True
    assert reply.is_running_calls == 0
    dialog.shutdown()


def test_forward_view_render_signature_ignores_clock_only_updates():
    from maritime_simulator.ui.forward_view import _visual_payload_signature

    first = {
        "revision": 1,
        "simulation_time_s": 10.0,
        "own_ship": {"lat": 1.0, "lon": 2.0},
        "environment": {"time_of_day_s": 10.0, "wind_speed_kn": 4.0},
    }
    later = {
        **first,
        "revision": 2,
        "simulation_time_s": 10.2,
        "environment": {"time_of_day_s": 10.2, "wind_speed_kn": 4.0},
    }
    assert _visual_payload_signature(first) == _visual_payload_signature(later)
    later["own_ship"] = {"lat": 1.0001, "lon": 2.0}
    assert _visual_payload_signature(first) != _visual_payload_signature(later)


def test_forward_view_render_signature_detects_late_ais_name_and_vector_updates():
    from maritime_simulator.ui.forward_view import _visual_payload_signature

    first = {
        "ais_targets": [{
            "mmsi": 211000001,
            "name": "211000001",
            "lat": 53.5,
            "lon": 9.9,
            "cog": 20.0,
            "sog": 4.0,
        }],
    }
    with_name = json.loads(json.dumps(first))
    with_name["ais_targets"][0]["name"] = "LATE STATIC NAME"
    with_vector_update = json.loads(json.dumps(with_name))
    with_vector_update["ais_targets"][0]["cog"] = 35.0

    assert _visual_payload_signature(first) != _visual_payload_signature(with_name)
    assert _visual_payload_signature(with_name) != _visual_payload_signature(with_vector_update)


def test_forward_view_skips_duplicate_render_payloads(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SHIP_SIMULATOR_HEADLESS_MAP", "1")
    from PySide6.QtWidgets import QApplication
    from maritime_simulator.ui.forward_view import ForwardViewDialog

    app = QApplication.instance() or QApplication([])
    snapshot = SimulationState().snapshot()
    dialog = ForwardViewDialog(lambda: snapshot, lambda _snapshot: (), tmp_path)
    sent = []
    dialog.view = object()
    dialog.loaded = True
    dialog._send_script = lambda function_name, payload: sent.append((function_name, payload))
    dialog.refresh()
    dialog.refresh()
    assert len(sent) == 1
    assert dialog.visual_updates_sent == 1
    assert dialog.visual_updates_skipped == 1
    dialog.view = None
    dialog.shutdown()
    app.processEvents()


def test_forward_view_switches_webengine_lifecycle_when_hidden(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SHIP_SIMULATOR_HEADLESS_MAP", "1")
    from PySide6.QtWidgets import QApplication
    from maritime_simulator.ui.forward_view import ForwardViewDialog

    class FakePage:
        def __init__(self):
            self.states = []

        def setLifecycleState(self, state):
            self.states.append(state.name)

    class FakeView:
        def __init__(self):
            self.fake_page = FakePage()

        def page(self):
            return self.fake_page

    app = QApplication.instance() or QApplication([])
    dialog = ForwardViewDialog(lambda: SimulationState().snapshot(), lambda _snapshot: (), tmp_path)
    fake_view = FakeView()
    dialog.view = fake_view
    dialog._set_page_active(False)
    dialog._set_page_active(True)
    assert fake_view.fake_page.states == ["Frozen", "Active"]
    dialog.view = None
    dialog.shutdown()
    app.processEvents()


def test_map_widget_freezes_hidden_page_and_flushes_latest_state(monkeypatch):
    monkeypatch.setenv("SHIP_SIMULATOR_HEADLESS_MAP", "1")
    from PySide6.QtWidgets import QApplication
    from ui.widgets.map_widget import MapWidget

    class FakePage:
        def __init__(self):
            self.states = []
            self.scripts = []

        def setLifecycleState(self, state):
            self.states.append(state.name)

        def runJavaScript(self, script, callback=None):
            self.scripts.append(script)

    class FakeView:
        def __init__(self):
            self.fake_page = FakePage()

        def page(self):
            return self.fake_page

    app = QApplication.instance() or QApplication([])
    widget = MapWidget()
    fake_view = FakeView()
    widget.view = fake_view
    widget.loaded = True

    widget._set_page_active(False)
    app.processEvents()
    widget.update_ship(53.0, 9.0, 20.0)
    widget.update_ship(53.1, 9.1, 25.0)
    widget.update_route([{"name": "WP1", "lat": 53.1, "lon": 9.1}])
    assert not widget.ais_update_due(force=True)
    assert fake_view.fake_page.scripts == []
    assert set(widget._pending_scripts) == {"ship", "route"}

    widget._set_page_active(True)
    assert fake_view.fake_page.states == ["Frozen", "Active"]
    assert len(fake_view.fake_page.scripts) == 2
    assert "53.1, 9.1, 25.0" in fake_view.fake_page.scripts[-1]
    assert widget._pending_scripts == {}
    widget.deleteLater()
    app.processEvents()


def test_windows_webengine_environment_prevents_false_occlusion_throttling(monkeypatch):
    import run_v2

    monkeypatch.setenv(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-features=ExistingFeature --remote-debugging-port=0",
    )
    run_v2._configure_webengine_environment("win32")
    flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
    assert "--disable-features=ExistingFeature,CalculateNativeWinOcclusion" in flags
    assert "--disable-backgrounding-occluded-windows" in flags
    assert "--remote-debugging-port=0" in flags

    run_v2._configure_webengine_environment("win32")
    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] == flags


def test_forward_view_uses_only_the_active_provider_renderers():
    root = Path(__file__).resolve().parents[2]
    dialog = (root / "src/maritime_simulator/ui/forward_view.py").read_text(encoding="utf-8")
    assert '"google_maps_3d.html"' in dialog
    assert '"cesium.html"' in dialog
    assert '"index.html"' not in dialog
    assert not (root / "ui/web/forward_view/assets").exists()
    assert not (root / "ui/web/forward_view/vendor").exists()
    assert "LocalContentCanAccessRemoteUrls, True" in dialog
    assert "self.timer.setInterval(250)" in dialog
    assert "self._pending_scripts" in dialog
    assert "runJavaScript(script, completed)" in dialog


def test_forward_view_uses_photorealistic_renderer_and_keeps_key_external(monkeypatch):
    from maritime_simulator.ui.forward_view import (
        _google_tiles_api_key,
        auto_renderer_from_response,
        normalize_visual_renderer_mode,
    )

    monkeypatch.delenv("MARITIME_SIM_GOOGLE_3D_TILES_API_KEY", raising=False)
    assert _google_tiles_api_key() == ""

    monkeypatch.setenv("MARITIME_SIM_GOOGLE_3D_TILES_API_KEY", "test-only-key")
    assert _google_tiles_api_key() == "test-only-key"
    assert normalize_visual_renderer_mode("GOOGLE_MAPS_3D") == "google_maps_3d"
    assert normalize_visual_renderer_mode("unknown") == "auto"
    assert auto_renderer_from_response(200, "{}")[0] == "photorealistic_tiles"
    assert auto_renderer_from_response(
        403,
        "3D tiles are not available for your account and region",
    ) == ("google_maps_3d", "region-restricted")
    assert auto_renderer_from_response(429, "quota exceeded") == (
        "google_maps_3d",
        "direct-tiles-unavailable",
    )


def test_google_maps_3d_renderer_keeps_navigation_and_overlays_available():
    root = Path(__file__).resolve().parents[2]
    page = (root / "ui/web/forward_view/google_maps_3d.html").read_text(encoding="utf-8")
    script = (root / "ui/web/forward_view/google_maps_3d.js").read_text(encoding="utf-8")
    style = (root / "ui/web/forward_view/google_maps_3d.css").read_text(encoding="utf-8")

    assert "google_maps_3d.js" in page
    assert "maps.googleapis.com/maps/api/js" in script
    assert 'importLibrary("maps3d")' in script
    assert "Map3DElement" in script
    assert 'id="navigationOverlay"' in page
    assert "drawNavigationOverlay" in script
    assert "projectedVesselGeometry" in script
    assert "visibleVesselSideFaces" in script
    assert "strokeClosedContour" in script
    assert "strokeVerticalContours" in script
    assert "if (!previousFace && !nextFace) continue" in script
    assert "convexHull" in script
    assert "OWN_SHIP_FILL" in script
    assert "AIS_SHIP_FILL" in script
    assert "const MAX_LABELLED_AIS = MAX_VISIBLE_AIS" in script
    assert "const MAX_AIS_VECTORS = MAX_VISIBLE_AIS" in script
    assert "GOOGLE_OVERLAY_QUALITY_LIMITS" in script
    assert "critical: Object.freeze({ visibleAIS: 24, labelledAIS: 12, aisVectors: 12 })" in script
    assert "Math.min(window.devicePixelRatio || 1, visualQuality.renderPixelRatio)" in script
    assert "window.configureVisualQuality = (configuration)" in script
    assert "applyVisualQualityConfiguration(configuration || {})" in script
    assert "function displayTargetName(target)" in script
    assert 'return name === mmsi ? "" : name' in script
    assert "function targetsByIncreasingRange(targets)" in script
    assert ".slice(0, overlayQualityLimits().visibleAIS)" in script
    assert "sort((a, b) => safeNumber(a.data.range_m" in script
    assert 'overlayContext.shadowColor = "transparent"' in script
    assert 'overlayContext.filter = "none"' in script
    assert 'nativeMapOverlayObjects:' in script
    assert 'screenOverlayActive:' in script
    assert "Polygon3DElement" not in script
    assert "Polyline3DElement" not in script
    assert "Marker3DElement" not in script
    assert "createVesselOutline" not in script
    assert "ownOutline" not in script
    assert ".coordinates =" not in script
    assert "VECTOR_HORIZON_S = 3 * 60" in script
    assert "AIS_INACTIVE_FILL" in script
    assert 'aisColourMode: "output-status"' in script
    assert 'id="namesButton"' in page
    assert "vesselHullRing" in script
    assert "updateAISTargets" in script
    assert "updateCamera" in script
    assert 'gestureHandling: "GREEDY"' in script
    assert 'gestureSurface.className = "google-gesture-surface"' in script
    assert "bindPointerNavigation" in script
    assert "applyTwoPointerGesture" in script
    assert "wheelIsTrackpadPan" in script
    assert "zoomByVerticalDelta" in script
    assert 'document.addEventListener("wheel"' in script
    assert "!event.ctrlKey || surface.contains(event.target)" in script
    assert 'event.pointerType === "mouse" && (event.buttons & 3) === 3' in script
    assert "const WATERLINE_HEIGHT_M = 1.5" in script
    assert "panRightM" in script
    assert "panForwardM" in script
    assert "function worldPanOffset()" in script
    assert "requestApplicationFullscreen" not in script
    assert 'event.deltaMode !== 0' in script
    assert "inputPointers.size > 0" in script
    for event_name in (
        "gmp-headingchange",
        "gmp-tiltchange",
        "gmp-rangechange",
        "gmp-centerchange",
    ):
        assert event_name in script
    assert 'renderer: "google-maps-javascript-3d"' in script
    assert 'id="fullscreenButton"' not in page
    assert "gmp-map-3d" in style


def test_google_and_cesium_views_expose_the_same_operator_controls():
    root = Path(__file__).resolve().parents[2]
    google_page = (root / "ui/web/forward_view/google_maps_3d.html").read_text(encoding="utf-8")
    cesium_page = (root / "ui/web/forward_view/cesium.html").read_text(encoding="utf-8")
    google_script = (root / "ui/web/forward_view/google_maps_3d.js").read_text(encoding="utf-8")
    cesium_script = (root / "ui/web/forward_view/cesium_simple_view.js").read_text(encoding="utf-8")

    for control_id in (
        "forwardButton",
        "orbitButton",
        "topButton",
        "hudButton",
        "namesButton",
        "controlsButton",
        "bearingControl",
        "elevationControl",
        "rangeControl",
        "navigationOverlay",
        "targetRows",
    ):
        assert f'id="{control_id}"' in google_page
        assert f'id="{control_id}"' in cesium_page
    assert 'id="fullscreenButton"' not in google_page
    assert 'id="fullscreenButton"' not in cesium_page
    for google_capability, cesium_capability in (
        ("updateAISTargets", "updateAISTargets"),
        ("updateHUD", "updateHUD"),
        ("predictedPath", "predictedPositions"),
        ("drawNavigationOverlay", "drawNavigationOverlay"),
        ("applyViewPreset", "applyViewPreset"),
        ("bindPointerNavigation", "bindPointerNavigation"),
    ):
        assert google_capability in google_script
        assert cesium_capability in cesium_script


def test_cesium_view_uses_simple_dimensioned_overlays_and_cached_camera_navigation():
    root = Path(__file__).resolve().parents[2]
    page = (root / "ui/web/forward_view/cesium.html").read_text(encoding="utf-8")
    script = (root / "ui/web/forward_view/cesium_simple_view.js").read_text(encoding="utf-8")
    style = (root / "ui/web/forward_view/cesium_simple_view.css").read_text(encoding="utf-8")
    dialog = (root / "src/maritime_simulator/ui/forward_view.py").read_text(encoding="utf-8")

    assert "cesium_simple_view.js" in page
    assert "1.145" in page
    assert "Google Photorealistic 3D Tiles" in page
    assert "showCreditsOnScreen: true" in script
    assert "tile.googleapis.com/v1/3dtiles/root.json?key=" in script
    assert "MARITIME_SIM_GOOGLE_3D_TILES_API_KEY" not in page
    assert "Model.fromGltfAsync" not in script
    assert "vesselHullPositions" in script
    assert "vesselVisualHeightM" in script
    assert 'aisColourMode: "output-status"' in script
    assert "safeNumber(data.length" in script
    assert "safeNumber(data.beam" in script
    assert "updateAISTargets" in script
    assert "MAX_VISIBLE_AIS = 80" in script
    assert "MAX_LABELLED_AIS = 12" in script
    assert "VECTOR_HORIZON_S = 3 * 60" in script
    assert "drawArrow" in script
    assert "overlayContext.setLineDash" in script
    assert "sampleHeightMostDetailed" in script
    assert "showRenderLoopErrors: false" in script
    assert "requestRenderMode: true" in script
    assert "useBrowserRecommendedResolution: false" in script
    assert "DEFAULT_RENDER_PIXEL_RATIO = 1.25" in script
    assert "OWN_SHIP_INTERPOLATION_MS" in script
    assert "DEFAULT_TARGET_FPS = 30" in script
    assert "visualAnimationFrameMs" in script
    assert "tileRenderMinIntervalMs" in script
    assert "transitionOwnShip" in script
    assert "cameraInteractionActive" in script
    assert "maximumScreenSpaceError: visualQuality.forwardScreenSpaceError" in script
    assert "dynamicScreenSpaceError: false" in script
    assert "progressiveResolutionHeightFraction: 0.4" in script
    assert "cullRequestsWhileMoving = cameraInteractionActive" in script
    assert "foveatedTimeDelay = cameraInteractionActive ? 0.1 : 0.0" in script
    assert "requestTileLoadRender" in script
    assert "scheduleTileSettleFrames" in script
    assert "TILE_SETTLE_FRAME_COUNT = 4" in script
    assert "cacheBytes: visualQuality.tileCacheMB * 1024 * 1024" in script
    assert "maximumCacheOverflowBytes: visualQuality.tileCacheOverflowMB * 1024 * 1024" in script
    assert "window.configureVisualQuality" in script
    assert "systemAvailableMemoryMB" in script
    assert "skipLevelOfDetail: false" in script
    assert "trimLoadedTiles" not in script
    assert "RELOCATION_DEBOUNCE_MS" in script
    assert "loadProgress" in script
    assert "LOADING_NOTICE_MS" in script
    assert "POSITION_JUMP_THRESHOLD_M" in script
    assert "new Cesium.HeadingPitchRange" in script
    assert "rotatePanOffsetToHeading" in script
    assert "panReferenceHeadingDeg" in script
    assert "worldToWindowCoordinates" in script
    assert "drawNavigationOverlay" in script
    assert "projectedSegments" in script
    assert "window.visualBridge?.requestApiKeySettings()" in script
    assert "requestFullscreen" not in script
    assert "vesselGeometry" in script
    assert "drawVessel(ownVisual.data, true, false, ownVisual.geometry)" in script
    assert "cachedAISGeometries" in script
    assert "aisVectorObjects" in script
    assert "screenOverlayActive" in script
    assert 'id="navigationOverlay"' in page
    assert 'id="skyBackdrop"' in page
    assert 'id="skyCloudCanvas"' in page
    assert 'id="apiSettingsButton"' in page
    assert 'id="fullscreenButton"' not in page
    assert "qtwebchannel/qwebchannel.js" in page
    assert "pointers.size >= 2" in script
    assert "twoFingerGesture.distance / gesture.distance" in script
    assert 'surface.addEventListener("wheel"' in script
    assert 'document.addEventListener("wheel"' in script
    assert "!event.ctrlKey || surface.contains(event.target)" in script
    assert 'surface.addEventListener("dblclick"' in script
    for control_id in ("bearingControl", "elevationControl", "rangeControl"):
        assert control_id in page
    assert "panEastControl" not in page
    assert "panNorthControl" not in page
    assert '"google_maps_3d.html"' in dialog
    assert '"cesium.html"' in dialog
    assert 'bundled_resource_path("ui", "web", "forward_view", page_name)' in dialog
    assert 'registerObject("visualBridge"' in dialog
    assert "visual_settings_v2.json" in dialog
    assert 'setBackgroundColor(QColor("#75bfdc"))' in dialog
    assert "request_area(" not in dialog
    assert "#cesiumContainer" in style
    assert "backdrop-filter: none" in style
    assert "cloud-drift" not in style
    assert "renderDirectionalSkyClouds" in script
    assert "bearing - cameraHeading" in script
    assert "skyCloudWorldAnchored" in script
    assert 'alpha: false' in script
    assert 'powerPreference: "high-performance"' in script
    assert "self._set_page_active(False)" in dialog
    assert "_visual_payload_signature" in dialog


def test_map_view_bounds_history_and_reuses_ais_vectors():
    root = Path(__file__).resolve().parents[2]
    script = (root / "ui/web/map.html").read_text(encoding="utf-8")
    verifier = (root / "scripts/verify_forward_view_web.mjs").read_text(encoding="utf-8")
    widget = (root / "ui/widgets/map_widget.py").read_text(encoding="utf-8")

    assert "MAX_OWN_SHIP_HISTORY_POINTS = 4096" in script
    assert "ownShipHistorySpacingM * 2" in script
    assert "let aisVectors = new Map()" in script
    assert "vector.setLatLngs(vectorCoordinates)" in script
    assert "aisVectorLayer.clearLayers()" not in script
    assert "window.getMapDiagnostics" in script
    assert "window.panMapByTrackpad" in script
    assert "installTrackpadPan" in script
    assert "event.stopImmediatePropagation()" in script
    assert "function setAISEditMode(active)" in script
    for action in ("synthetic_add", "synthetic_move", "synthetic_vector"):
        assert action in script
    assert "map_command = Signal(dict)" in widget
    assert "setAISEditMode" in widget
    assert "verifyMapEndurance" in verifier
    assert "ownShipHistoryPoints > diagnostics.ownShipHistoryLimit" in verifier


def test_webengine_http_cache_is_persistent_and_bounded():
    from PySide6.QtWebEngineCore import QWebEngineProfile

    from ui.webengine_profile import WEBENGINE_HTTP_CACHE_BYTES, configure_webengine_profile

    class Profile:
        cache_type = None
        maximum_size = None

        def setHttpCacheType(self, value):
            self.cache_type = value

        def setHttpCacheMaximumSize(self, value):
            self.maximum_size = value

    class Page:
        profile_value = Profile()

        def profile(self):
            return self.profile_value

    page = Page()
    configure_webengine_profile(page)
    assert page.profile_value.cache_type == QWebEngineProfile.DiskHttpCache
    assert page.profile_value.maximum_size == 1024 * 1024 * 1024
    assert WEBENGINE_HTTP_CACHE_BYTES == page.profile_value.maximum_size
