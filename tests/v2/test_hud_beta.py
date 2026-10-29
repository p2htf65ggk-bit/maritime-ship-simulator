# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import QUrlQuery
from pathlib import Path

from maritime_simulator.domain.state import SimulationState
from maritime_simulator.application.paths import user_config_dir
from maritime_simulator.ui.forward_view import _renderer_page_url
from maritime_simulator.visual.geodata import OverpassGeodataProvider
from maritime_simulator.visual.world import VisualWorldManager


def test_final_hud_defaults_on_and_allows_original_hud(monkeypatch):
    for page in ("google_maps_3d.html", "cesium.html"):
        monkeypatch.delenv("MARITIME_SIMULATOR_3D_HUD_BETA", raising=False)
        assert QUrlQuery(_renderer_page_url(page)).queryItemValue("hud") == "beta"
        monkeypatch.setenv("MARITIME_SIMULATOR_3D_HUD_BETA", "0")
        original = _renderer_page_url(page)
        assert original.isLocalFile() and not original.hasQuery()
        monkeypatch.setenv("MARITIME_SIMULATOR_3D_HUD_BETA", "1")
        beta = _renderer_page_url(page)
        assert beta.toLocalFile() == original.toLocalFile()
        assert QUrlQuery(beta).queryItemValue("hud") == "beta"


def test_final_hud_uses_standard_operator_settings_and_respects_override(monkeypatch, tmp_path):
    monkeypatch.delenv("MARITIME_SIMULATOR_V2_CONFIG_DIR", raising=False)
    assert user_config_dir().parts[-2:] == ("Ship Simulator", "v2")
    monkeypatch.setenv("MARITIME_SIMULATOR_V2_CONFIG_DIR", str(tmp_path))
    assert user_config_dir() == tmp_path.resolve()


def test_hud_environment_uses_shared_snapshot_without_changing_direction_conventions(tmp_path):
    state = SimulationState()
    state.environment.true_wind_from_deg = 245.0
    state.environment.true_wind_speed_kn = 16.0
    state.environment.relative_wind_from_bow_deg = 312.0
    state.environment.relative_wind_speed_kn = 18.4
    state.environment.current_from_deg = 270.0
    state.environment.current_speed_kn = 1.3
    snapshot = state.snapshot()
    manager = VisualWorldManager(OverpassGeodataProvider(tmp_path))
    payload = manager.state_payload(snapshot, ())
    environment = payload["environment"]
    assert environment["wind_from_deg"] == 245.0
    assert environment["apparent_wind_from_bow_deg"] == 312.0
    assert environment["apparent_wind_speed_kn"] == 18.4
    assert environment["current_from_deg"] == 270.0
    assert environment["current_speed_kn"] == 1.3
    assert state.snapshot() == snapshot


def test_hud_speed_scale_follows_vessel_profile(tmp_path):
    class Profile:
        max_speed_kn = 35.0

    manager = VisualWorldManager(OverpassGeodataProvider(tmp_path))
    assert manager.state_payload(SimulationState().snapshot(), (), vessel_profile=Profile())["own_ship"]["max_speed_kn"] == 35.0


def test_both_renderers_start_with_camera_sliders_hidden():
    directory = Path(__file__).resolve().parents[2] / "ui/web/forward_view"
    for html, script in (("google_maps_3d.html", "google_maps_3d.js"), ("cesium.html", "cesium_simple_view.js")):
        markup = (directory / html).read_text()
        source = (directory / script).read_text()
        assert 'id="controlsButton" aria-pressed="false"' in markup
        assert 'id="cameraControls" class="glass-panel hidden"' in markup
        assert "let controlsVisible = false;" in source
        assert "controlsVisible = state.controlsVisible === true;" in source
