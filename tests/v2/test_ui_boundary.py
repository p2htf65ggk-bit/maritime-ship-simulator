# SPDX-License-Identifier: MPL-2.0
import os
from pathlib import Path
import socket
import tempfile
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QComboBox, QLabel, QMessageBox

from maritime_simulator.ui.main_window import V2MainWindow
from maritime_simulator.domain.commands import SetRouteOptions, SetSensorNoise, SetSpeedOrder
from maritime_simulator.domain.enums import ControlMode, TransportKind
from maritime_simulator.ui.controls import format_coordinate, parse_coordinate
import maritime_simulator.ui.settings_dialog as settings_dialog_module
from maritime_simulator.simulation.route_geometry import bearing_deg, radius_from_rot
from maritime_simulator.transport.live_weather import LiveWeatherReading
from ui.style import apply_global_ui_style


def _window():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    temporary = tempfile.TemporaryDirectory()
    window = V2MainWindow(config_dir=Path(temporary.name))
    # Keep the isolated configuration alive for the complete Qt object lifetime.
    window._test_config_directory = temporary
    window.timer.stop()
    return app, window


def _temporary_window():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    temporary = tempfile.TemporaryDirectory()
    window = V2MainWindow(config_dir=Path(temporary.name))
    window.timer.stop()
    return app, window, temporary


def test_sensor_editor_publishes_command_instead_of_mutating_state_directly():
    app, window = _window()
    try:
        window.sensor_fields["sog_kn"].setValue(-4.2)
        window._sensor_value_changed("sog_kn")
        window.service.advance_wall_time(0.0)
        assert window.service.snapshot().sog_kn == -4.2
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_marine_coordinate_editor_formats_and_accepts_degrees_minutes():
    assert format_coordinate(52.5, latitude=True) == "52° 30.000' N"
    assert format_coordinate(-3.25, latitude=False) == "003° 15.000' W"
    assert parse_coordinate("52° 30.000' N", latitude=True) == 52.5
    assert parse_coordinate("003 15.000 W", latitude=False) == -3.25


def test_unchanged_route_is_not_redispatched_to_map_every_refresh():
    app, window = _window()
    calls = []
    original = window.map.update_route
    window.map.update_route = lambda route: (calls.append(tuple(route)), original(route))[1]
    try:
        window._request_mode(ControlMode.TRACK)
        window.service.advance_wall_time(0.1)
        window._refresh()
        first_count = len(calls)
        window.service.advance_wall_time(0.1)
        window._refresh()
        assert first_count == 1
        assert len(calls) == first_count
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_map_route_signature_reuses_immutable_waypoint_snapshot_between_ticks():
    app, window = _window()
    try:
        window._request_mode(ControlMode.TRACK)
        source = window._map_route_source
        signature = window._map_route_signature_cache
        window.service.advance_wall_time(0.1)
        window._refresh()
        assert window._map_route_source is source
        assert window._map_route_signature_cache is signature
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_map_ais_projection_is_deferred_until_web_dispatch_is_due(monkeypatch):
    app, window = _window()
    calls = []
    monkeypatch.setattr(window.ais, "map_targets", lambda snapshot: calls.append(snapshot) or ())
    try:
        window.map._last_ais_emit = time.monotonic()
        window._last_ais_revision = -1
        window.service.advance_wall_time(0.05)
        window._refresh()
        assert calls == []

        window.map._last_ais_emit -= window.map._ais_emit_min_interval
        window._refresh()
        assert len(calls) == 1
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_route_is_rendered_only_in_track_mode_and_restored_when_reentered():
    app, window, temporary = _temporary_window()
    try:
        window.service.advance_wall_time(0.0)
        window._refresh(force=True)
        assert window.map._last_route_json.endswith('"turnArcs": []}')
        assert '"waypoints": []' in window.map._last_route_json
        window._request_mode(ControlMode.TRACK)
        assert '"waypoints": []' not in window.map._last_route_json
        window._request_mode(ControlMode.SHIP)
        assert '"waypoints": []' in window.map._last_route_json
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_map_route_actions_preserve_ship_and_resynchronize_route_progress():
    app, window, temporary = _temporary_window()
    try:
        window.service.advance_wall_time(0.0)
        window._request_mode(ControlMode.TRACK)
        before = window.service.snapshot()
        waypoint = before.route_waypoints[2]
        window._map_route_event(
            {
                "action": "move",
                "index": 2,
                "lat": waypoint.latitude_deg + 0.001,
                "lon": waypoint.longitude_deg + 0.001,
            }
        )
        moved = window.service.snapshot()
        assert moved.route_waypoints[2].latitude_deg == waypoint.latitude_deg + 0.001
        assert moved.latitude_deg == before.latitude_deg
        assert moved.longitude_deg == before.longitude_deg
        window._map_route_event(
            {"action": "set_ownship", "lat": waypoint.latitude_deg, "lon": waypoint.longitude_deg}
        )
        positioned = window.service.snapshot()
        assert positioned.latitude_deg == waypoint.latitude_deg
        assert positioned.route_progress_nm > 0.0
        assert positioned.next_waypoint_index >= 2
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_track_seek_slider_updates_ship_position_while_dragging():
    app, window, temporary = _temporary_window()
    try:
        window.service.advance_wall_time(0.0)
        window._request_mode(ControlMode.TRACK)
        window._refresh(force=True)
        map_centers = []
        window.map.update_ship = lambda *_args, **kwargs: map_centers.append(kwargs["center"])
        total = window.mode_controls._route_total_nm
        start = window.service.snapshot()
        window.mode_controls._track_seek.setValue(5000)
        moved = window.service.snapshot()
        assert abs(moved.route_progress_nm - total / 2.0) < 0.02
        assert (moved.latitude_deg, moved.longitude_deg) != (start.latitude_deg, start.longitude_deg)
        leg_end = moved.next_waypoint_index
        expected_heading = bearing_deg(
            moved.route_waypoints[leg_end - 1], moved.route_waypoints[leg_end]
        )
        assert moved.heading_deg == pytest.approx(expected_heading)
        assert moved.cog_deg == pytest.approx(expected_heading)
        assert map_centers[-1] is True
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_map_expand_command_detaches_and_restores_the_single_map_widget():
    app, window, temporary = _temporary_window()
    try:
        window.show()
        app.processEvents()
        window.map.map_command.emit({"action": "expand"})
        app.processEvents()
        assert window._map_detached
        assert window.map_dialog is not None
        assert window.map_dialog.isVisible()
        assert window.map.parentWidget() is window.map_dialog

        window.map_dialog.close()
        app.processEvents()
        assert not window._map_detached
        assert window.workspace.indexOf(window.navigation_panel) == 1
        assert window.navigation_stack.indexOf(window.map) >= 0
        assert window.navigation_stack.currentWidget() is window.map
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_navigation_area_reuses_one_forward_view_for_embedded_and_window_modes():
    app, window, temporary = _temporary_window()
    try:
        window.show()
        app.processEvents()
        window._show_navigation_view("3d")
        app.processEvents()
        view = window.forward_view_dialog
        assert view is not None
        assert window._forward_view_embedded
        assert window.navigation_stack.currentWidget() is view
        assert view.parentWidget() is window.navigation_stack
        assert window.navigation_view_buttons["3d"].isChecked()

        window._show_navigation_view("map")
        app.processEvents()
        assert window.navigation_stack.currentWidget() is window.map
        assert not view.timer.isActive()

        window._show_navigation_view("3d")
        window._open_forward_view()
        app.processEvents()
        assert window.forward_view_dialog is view
        assert not window._forward_view_embedded
        assert view.isWindow()
        assert view.isVisible()
        assert window.navigation_stack.currentWidget() is window.map
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_track_turn_slider_is_debounced_and_updates_control_turn_value():
    app, window, temporary = _temporary_window()
    try:
        window._request_mode(ControlMode.TRACK)
        window.service.advance_wall_time(0.0)
        window._refresh(force=True)
        index = window.service.snapshot().next_waypoint_index
        slider = window.mode_controls._track_rot_slider
        original = window.service.snapshot().route_waypoints[index]
        target = slider.minimum() if slider.value() != slider.minimum() else slider.maximum()

        slider.setValue(target)
        assert window.service.snapshot().route_waypoints[index] == original
        QTest.qWait(window.mode_controls._track_turn_commit_timer.interval() + 40)
        app.processEvents()

        snapshot = window.service.snapshot()
        assert snapshot.route_waypoints[index] == original
        assert snapshot.control_turn_mode == "fixed_rot"
        assert snapshot.control_turn_rate_deg_min == target / 10.0
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_track_radius_slider_coalesces_drag_updates_without_route_rebuild_storm():
    app, window, temporary = _temporary_window()
    try:
        window._request_mode(ControlMode.TRACK)
        window.service.advance_wall_time(0.0)
        window._refresh(force=True)
        index = window.service.snapshot().next_waypoint_index
        slider = window.mode_controls._track_radius_slider
        emitted = []
        window.mode_controls.command_submitted.connect(emitted.append)
        target = min(slider.maximum(), slider.minimum() + 12)

        for value in range(slider.minimum() + 1, target + 1):
            slider.setValue(value)
        assert not [command for command in emitted if command.__class__.__name__ == "UpdateRoute"]
        QTest.qWait(window.mode_controls._track_turn_commit_timer.interval() + 40)
        app.processEvents()
        option_updates = [command for command in emitted if command.__class__.__name__ == "SetRouteOptions"]
        assert len(option_updates) == 1
        snapshot = window.service.snapshot()
        assert snapshot.control_turn_mode == "fixed_radius"
        assert snapshot.control_turn_radius_nm == target / 100.0
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_track_controls_derive_inactive_peer_and_activate_on_use():
    app, window, temporary = _temporary_window()
    try:
        window._request_mode(ControlMode.TRACK)
        window._submit_ui_command(SetRouteOptions(
            use_planned_speed=False,
            use_planned_turns=False,
            control_turn_mode="fixed_rot",
            control_turn_rate_deg_min=10.0,
        ))
        window._submit_ui_command(SetSpeedOrder(20.0))
        window.service.advance_wall_time(0.0)
        window._refresh(force=True)

        assert not hasattr(window.mode_controls, "_fix_rot")
        assert not hasattr(window.mode_controls, "_fix_radius")
        assert window.mode_controls._track_rot.property("derived") is False
        assert window.mode_controls._track_radius.property("derived") is True
        assert window.mode_controls._track_radius.value() == pytest.approx(radius_from_rot(20.0, 10.0), abs=0.01)

        slider = window.mode_controls._track_radius_slider
        slider.setValue(min(slider.maximum(), max(slider.minimum(), round(1.2 * 100))))
        QTest.qWait(window.mode_controls._track_turn_commit_timer.interval() + 40)
        app.processEvents()
        assert window.service.snapshot().control_turn_mode == "fixed_radius"
        assert window.mode_controls._track_rot.property("derived") is True
        assert window.mode_controls._track_radius.property("derived") is False
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_accelerated_time_is_highlighted_and_compact_track_control_is_narrow():
    app, window, temporary = _temporary_window()
    try:
        window.time_scale.setCurrentIndex(window.time_scale.findData(5.0))
        assert window.time_scale.property("accelerated") is True
        window.time_scale.setCurrentIndex(window.time_scale.findData(1.0))
        assert window.time_scale.property("accelerated") is False

        window._request_mode(ControlMode.TRACK)
        window.show()
        window._toggle_left_panel()
        app.processEvents()
        assert window.left_panel.maximumWidth() <= 124
        assert window.compact_controls.secondary.slider.orientation() == Qt.Vertical
        assert window.compact_controls.secondary.title.text() in {"RATE", "RADIUS"}
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_sensor_page_omits_xte_and_environment_owns_depth():
    app, window, temporary = _temporary_window()
    try:
        assert "xte_nm" not in window.sensor_fields
        assert "depth_m" not in window.sensor_fields
        window.environment_controls.depth.setValue(42.5)
        window.environment_controls._depth_changed()
        window.service.advance_wall_time(0.0)
        assert window.service.snapshot().depth_m == 42.5
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_environment_uses_direction_dials_depth_limit_and_separate_tabs():
    app, window, temporary = _temporary_window()
    try:
        assert window.environment_controls.wind_direction.__class__.__name__ == "CompassDial"
        assert window.environment_controls.current_direction.__class__.__name__ == "CompassDial"
        assert window.environment_controls.depth.maximum() == 2000.0
        labels = [window.control_tabs.tabText(index) for index in range(window.control_tabs.count())]
        assert labels == ["Control", "Environment", "AIS", "Noise", "Other", "Fault Injection"]
        assert window.control_tabs.tabToolTip(3) == "Sensor Noise"
        assert window.control_tabs.tabToolTip(4) == "Other Sensors"
        assert not hasattr(window.environment_controls, "time_scale")
        assert window.time_scale.findData(30.0) >= 0
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_environment_can_switch_between_manual_and_position_based_live_weather():
    app, window, temporary = _temporary_window()
    requests = []
    window.live_weather.refresh = lambda force=False: requests.append(force)
    try:
        source = window.environment_controls.weather_source
        source.setCurrentIndex(source.findData("open-meteo"))
        assert window.live_weather.enabled is True
        assert requests == [True]
        assert window._app_state_payload()["simulation"]["environment"]["weather_source"] == "open-meteo"

        reading = LiveWeatherReading(
            latitude_deg=53.5,
            longitude_deg=9.9,
            air_temperature_c=18.2,
            relative_humidity_percent=71.0,
            dew_point_c=12.8,
            surface_pressure_bar=1.018,
            observation_time="2026-09-18T10:15",
        )
        window._apply_live_weather(reading)
        snapshot = window.service.snapshot()
        assert snapshot.xdr_air_temperature_c == 18.2
        assert snapshot.xdr_relative_humidity_percent == 71.0
        assert snapshot.xdr_dew_point_c == 12.8
        assert snapshot.xdr_surface_pressure_bar == 1.018
        assert snapshot.air_temperature_c == 15.0
        assert snapshot.barometric_pressure_bar == 1.013
        window._refresh(force=True)
        assert not window.environment_controls.xdr_fields["xdr_air_temperature_c"].isEnabled()
        assert window.advanced_sensors.meteo_fields["air_temperature_c"].isEnabled()

        source.setCurrentIndex(source.findData("manual"))
        window._refresh(force=True)
        assert window.live_weather.enabled is False
        assert window.environment_controls.xdr_fields["xdr_air_temperature_c"].isEnabled()
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_sensor_tab_does_not_duplicate_protocol_output_enable_controls():
    app, window, temporary = _temporary_window()
    try:
        assert not hasattr(window.advanced_sensors, "protocol_checks")
        assert window.advanced_sensors.dual_rudder.text() == "Second rudder active"
        assert "XDR Environment" in window.protocol_configuration.sensors
        window._open_settings()
        rows = window.settings_dialog._rows
        for protocol, identifier in (
            ("XDR_AIR_TEMPERATURE", "Air#0"),
            ("XDR_RELATIVE_HUMIDITY", "Air#1"),
            ("XDR_DEW_POINT", "Air#2"),
            ("XDR_SURFACE_PRESSURE", "Baro#0"),
        ):
            assert ("XDR Environment", protocol) in rows
            matches = window.settings_dialog.output_trees["udp"].findItems(
                identifier, Qt.MatchContains | Qt.MatchRecursive, 0
            )
            assert matches
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_configuration_state_writer_skips_unchanged_files(tmp_path):
    path = tmp_path / "state.json"
    payload = {"route": [1, 2], "mode": "track"}
    assert V2MainWindow._write_json_if_changed(path, payload) is True
    first_stat = path.stat()
    assert V2MainWindow._write_json_if_changed(path, payload) is False
    assert path.stat().st_mtime_ns == first_stat.st_mtime_ns
    assert V2MainWindow._write_json_if_changed(path, {"route": [1, 2, 3]}) is True


def test_disabled_sensor_parent_can_enable_all_protocols_in_one_click():
    app, window, temporary = _temporary_window()
    try:
        window.protocol_configuration.update_sensor("GNSS", enabled=False)
        window._open_settings()
        settings = window.settings_dialog
        settings.refresh()
        parent = settings._rows[("GNSS", None)].enabled
        assert parent.checkState() == Qt.Unchecked
        QTest.mouseClick(parent, Qt.LeftButton)
        assert all(
            window.protocol_configuration.sensors["GNSS"].get_protocol(protocol).enabled
            for protocol in window.protocol_configuration.sensors["GNSS"].protocols()
        )
        assert parent.checkState() == Qt.Checked
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_output_window_is_monitor_only_and_scheduler_runs_independently():
    app, window, temporary = _temporary_window()
    try:
        assert window.output_runtime.is_running()
        window._open_protocol_output()
        assert not hasattr(window.protocol_dialog, "run_button")
        assert "Monitor" in window.protocol_dialog.status.text()
        window.protocol_dialog.freeze.click()
        assert window.protocol_dialog.freeze.text() == "Resume"
        assert "display frozen" in window.protocol_dialog.status.text().lower()
        assert window.output_runtime.is_running()
        window.protocol_dialog.freeze.click()
        assert window.protocol_dialog.freeze.text() == "Freeze"
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_route_and_left_panels_collapse_without_removing_key_controls():
    app, window, temporary = _temporary_window()
    try:
        window.route_planner._toggle_collapsed()
        assert not window.route_planner.details.isVisible()
        assert window.route_planner.position_slider.isVisibleTo(window.route_planner)
        window._toggle_left_panel()
        assert window.compact_controls.isVisibleTo(window.left_panel)
        assert not window.control_tabs.isVisible()
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_complete_configuration_payload_round_trips_route_and_io():
    app, window, temporary = _temporary_window()
    try:
        visual_path = Path(temporary.name) / "visual_settings_v2.json"
        window._write_json(visual_path, {
            "google_tiles_api_key": "portable-google-test-key",
            "visual_quality_mode": "low_memory",
            "renderer_mode": "google_maps_3d",
        })
        window._submit_ui_command(SetSensorNoise(
            "heading",
            enabled=True,
            amplitude=2.5,
            period_s=3.2,
        ))
        window.service.advance_wall_time(0.0)
        payload = window._configuration_payload()
        assert payload["visual_settings"]["google_tiles_api_key"] == "portable-google-test-key"
        assert payload["app_state"]["simulation"]["sensor_noise"]["heading"] == [
            True,
            2.5,
            3.2,
        ]
        payload["app_state"]["route"][0]["name"] = "PROFILE START"
        payload["protocol_output"]["protocols"]["GGA"]["udp"]["port"] = 4999
        path = Path(temporary.name) / "complete-config.json"
        window._write_json(path, payload)
        loaded = __import__("json").loads(path.read_text(encoding="utf-8"))
        window._apply_configuration_payload(loaded)
        window.service.advance_wall_time(0.0)
        assert window.service.snapshot().route_waypoints[0].name == "PROFILE START"
        assert window.protocol_configuration.sensors["GNSS"].get_protocol("GGA").port == 4999
        assert dict(window.service.snapshot().sensor_noise)["heading"] == (
            True,
            2.5,
            3.2,
        )
        assert __import__("json").loads(visual_path.read_text(encoding="utf-8")) == {
            "google_tiles_api_key": "portable-google-test-key",
            "visual_quality_mode": "low_memory",
            "renderer_mode": "google_maps_3d",
        }
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_detached_map_and_3d_view_can_toggle_fullscreen():
    app, window, temporary = _temporary_window()
    try:
        window._detach_map()
        app.processEvents()
        assert window.map_dialog is not None
        window.map_dialog.toggle_fullscreen()
        app.processEvents()
        assert window.map_dialog.isFullScreen()
        window.map_dialog.leave_fullscreen()
        assert not window.map_dialog.isFullScreen()

        window._show_navigation_view("3d")
        dialog = window._ensure_forward_view()
        assert window._forward_view_embedded
        dialog._toggle_fullscreen()
        app.processEvents()
        assert not window._forward_view_embedded
        assert dialog.isFullScreen()
        dialog._leave_fullscreen()
        assert not dialog.isFullScreen()
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_last_complete_configuration_and_vessel_overrides_load_on_next_start():
    app, window, temporary = _temporary_window()
    config_dir = Path(temporary.name)
    try:
        window.service.profiles.update_profile(
            "crew-transfer-vessel",
            display_name="Configured CTV",
            max_speed_kn=32.5,
        )
        window.ais_controls.output_filter_switch.click()
        window.ais_controls.output_min_sog.setValue(1.4)
        payload = window._configuration_payload()
        payload["app_state"]["route"][0]["name"] = "LAST SESSION START"
        window._persist_configuration_payload(payload)
        window.close()
        window.deleteLater()
        app.processEvents()

        restored = V2MainWindow(config_dir=config_dir)
        restored.timer.stop()
        restored.service.advance_wall_time(0.0)
        assert restored.service.profiles.get("crew-transfer-vessel").display_name == "Configured CTV"
        assert restored.service.profiles.get("crew-transfer-vessel").max_speed_kn == pytest.approx(32.5)
        assert restored.service.snapshot().route_waypoints[0].name == "LAST SESSION START"
        assert restored.ais_controls.output_filter_switch.isChecked()
        assert restored.ais_controls.output_min_sog.value() == pytest.approx(1.4)
        assert restored.ais.output_filter_mode == "sog_dependent"
        assert (config_dir / "last_configuration_v2.json").exists()
        restored.close()
        restored.deleteLater()
    finally:
        temporary.cleanup()
        app.processEvents()


def test_track_manual_controls_take_ownership_from_route_plan_and_compact_modes_are_complete():
    app, window, temporary = _temporary_window()
    try:
        window._request_mode(ControlMode.TRACK)
        window._submit_ui_command(SetRouteOptions(use_planned_speed=True, use_planned_turns=True))
        snapshot = window.service.snapshot()
        window.mode_controls.update_snapshot(snapshot)
        window.compact_controls.update_snapshot(snapshot)
        assert window.mode_controls._engine_fields[ControlMode.TRACK].property("derived") is True
        assert window.mode_controls._track_rot.property("derived") is True
        assert window.mode_controls._track_radius.property("derived") is True
        assert window.compact_controls.engine.property("derived") is True
        assert window.compact_controls.track_rot.property("derived") is True
        assert window.compact_controls.track_radius.property("derived") is True

        window.mode_controls._engine_changed(ControlMode.TRACK, 14.0)
        window.service.advance_wall_time(0.0)
        assert window.service.snapshot().use_planned_speed is False
        window.mode_controls._track_radius.setValue(0.8)
        window.mode_controls._track_turn_value_changed("fixed_radius")
        assert window.service.snapshot().use_planned_turns is False
        assert window.service.snapshot().control_turn_mode == "fixed_radius"

        window._request_mode(ControlMode.SENSOR)
        window.compact_controls.update_snapshot(window.service.snapshot())
        assert all(not control.isHidden() for control in (
            window.compact_controls.sensor_hdg,
            window.compact_controls.sensor_rot,
            window.compact_controls.sensor_sog,
            window.compact_controls.sensor_stw,
        ))
        assert window.mode_controls.findChildren(QLabel, "dataReadout")
        assert not hasattr(window.mode_controls, "_track_phase")
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_model_owned_advanced_sensor_controls_disable_editor_and_slider():
    app, window, temporary = _temporary_window()
    try:
        window._request_mode(ControlMode.SHIP)
        window.advanced_sensors.update_snapshot(window.service.snapshot())
        response = window.advanced_sensors.rudder_fields["rudder_response_starboard_deg"]
        transverse = window.advanced_sensors.motion_fields["aft_transverse_ground_kn"]
        assert not response.isEnabled()
        assert not response._paired_slider.isEnabled()
        assert not transverse.isEnabled()
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_engine_telegraph_starts_with_full_ahead():
    app, window, temporary = _temporary_window()
    try:
        window.service.advance_wall_time(0.0)
        window._refresh(force=True)
        for combo in window.mode_controls._engine_fields.values():
            assert combo.itemText(0).startswith("Full Ahead")
            assert float(combo.itemData(0)) > 0.0
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_ship_keyboard_uses_window_shortcuts_without_global_event_filter():
    app, window, temporary = _temporary_window()
    try:
        window._request_mode(ControlMode.SHIP)
        window._set_keyboard_control(True)
        window.setFocus()
        app.processEvents()
        assert all(shortcut.parent() is window for shortcut in window._keyboard_shortcuts)
        assert all(shortcut.isEnabled() for shortcut in window._keyboard_shortcuts)

        window._keyboard_shortcuts[0].activated.emit()
        window.service.advance_wall_time(0.0)
        assert window.service.snapshot().speed_order_kn > 0.0
        window._keyboard_shortcuts[2].activated.emit()
        window.service.advance_wall_time(0.0)
        assert window.service.snapshot().rudder_order_starboard_deg == -1.0

        window._request_mode(ControlMode.TRACK)
        assert not any(shortcut.isEnabled() for shortcut in window._keyboard_shortcuts)
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_each_control_mode_has_its_own_command_surface():
    app, window, temporary = _temporary_window()
    try:
        expected_pages = {
            ControlMode.SENSOR: "Sensor Control",
            ControlMode.SHIP: "Ship Control",
            ControlMode.TRACK: "Track Control",
            ControlMode.EXTERNAL_RUDDER: "External Control",
        }
        for mode, heading in expected_pages.items():
            window._request_mode(mode)
            window.service.advance_wall_time(0.0)
            window._refresh(force=True)
            app.processEvents()
            visible_pages = [page for page in window.mode_controls._pages.values() if page.isVisibleTo(window)]
            assert len(visible_pages) == 1
            assert visible_pages[0].findChildren(QLabel)[0].text() == heading
            assert window.route_panel.isVisibleTo(window) is (mode is ControlMode.TRACK)
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_small_track_viewport_keeps_map_control_and_route_regions_separate():
    app, window, temporary = _temporary_window()
    try:
        window.resize(1280, 720)
        window._request_mode(ControlMode.TRACK)
        window.service.advance_wall_time(0.0)
        window._refresh(force=True)
        window.show()
        app.processEvents()
        left = window.control_tabs.mapTo(window.centralWidget(), window.control_tabs.rect().topRight())
        map_left = window.map.mapTo(window.centralWidget(), window.map.rect().topLeft())
        route_bottom = window.route_panel.mapTo(window.centralWidget(), window.route_panel.rect().bottomLeft())
        assert map_left.x() > left.x()
        assert route_bottom.y() <= window.centralWidget().height()
        assert window.mode_controls._pages[ControlMode.TRACK].isVisibleTo(window)
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_main_toolbar_opens_all_v2_tool_dialogs():
    app, window, temporary = _temporary_window()
    try:
        window.show()
        for callback, attribute in (
            (window._open_settings, "settings_dialog"),
            (window._open_protocol_output, "protocol_dialog"),
            (window._open_advanced, "advanced_dialog"),
            (window._open_instruments, "instrument_dialog"),
            (window._open_about, "about_dialog"),
        ):
            callback()
            app.processEvents()
            dialog = getattr(window, attribute)
            assert dialog is not None
            assert dialog.isVisible()
            dialog.hide()
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_settings_sensor_edit_pushes_to_children_and_child_difference_aggregates_blank():
    app, window, temporary = _temporary_window()
    try:
        window._open_settings()
        dialog = window.settings_dialog
        assert dialog is not None
        dialog._apply("GNSS", None, host="239.192.192.1", port=4097, rate_hz=2.0)
        gnss = window.protocol_configuration.sensors["GNSS"]
        assert all(gnss.get_protocol(name).host == "239.192.192.1" for name in gnss.protocols())
        assert all(gnss.get_protocol(name).port == 4097 for name in gnss.protocols())
        assert all(gnss.get_protocol(name).rate_hz == 2.0 for name in gnss.protocols())
        dialog._apply("GNSS", "GGA", host="239.192.192.2")
        assert gnss.aggregate("host") is None
        assert dialog._rows[("GNSS", None)].destination.text() == ""
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_settings_has_independent_transport_tabs_and_configures_each_sensor_level():
    app, window, temporary = _temporary_window()
    try:
        window._open_settings()
        dialog = window.settings_dialog
        assert dialog is not None
        assert [dialog.output_tabs.tabText(index) for index in range(dialog.output_tabs.count())] == [
            "UDP", "TCP", "Serial"
        ]
        dialog._apply(
            "GNSS",
            None,
            output_transport=TransportKind.TCP,
            enabled=True,
            host="0.0.0.0",
            port=5001,
            rate_hz=2.0,
            tcp_server=True,
        )
        assert all(
            window.protocol_configuration.sensors["GNSS"].get_protocol(name, TransportKind.TCP).enabled
            for name in window.protocol_configuration.sensors["GNSS"].protocols()
        )
        dialog._apply(
            "GNSS",
            None,
            output_transport=TransportKind.SERIAL,
            serial_device="COM9",
            baud=38400,
            parity="E",
            stop_bits=2,
        )
        dialog._apply("GNSS", None, output_transport=TransportKind.SERIAL, enabled=True)
        for name in window.protocol_configuration.sensors["GNSS"].protocols():
            settings = window.protocol_configuration.sensors["GNSS"].get_protocol(name, TransportKind.SERIAL)
            assert settings.transport is TransportKind.SERIAL
            assert (settings.serial_device, settings.baud, settings.parity, settings.stop_bits) == (
                "COM9", 38400, "E", 2
            )
            child = dialog._transport_rows[TransportKind.SERIAL][("GNSS", name)]
            assert child.destination.currentText() == "COM9"
            assert child.baud.currentData() == 38400
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_settings_adds_and_removes_exact_selected_output_tab(monkeypatch):
    app, window, temporary = _temporary_window()
    try:
        window._open_settings()
        dialog = window.settings_dialog
        assert dialog is not None
        original_payload = window.protocol_configuration.to_payload()

        dialog._add_output_path(TransportKind.UDP)
        second_udp = window.protocol_configuration.output_paths()[-1]
        assert (second_udp.path_id, second_udp.label, second_udp.removable) == ("udp-2", "UDP 2", True)
        assert dialog.output_tabs.tabText(dialog.output_tabs.currentIndex()) == "UDP 2"
        assert dialog.output_remove.isEnabled()

        dialog._apply(
            "AIS",
            "VDM",
            output_transport=TransportKind.UDP,
            output_path_id=second_udp.path_id,
            enabled=True,
            host="239.192.192.10",
            port=14100,
            rate_hz=2.0,
        )
        extra_vdm = window.protocol_configuration.sensors_for_path(second_udp.path_id)["AIS"].get_protocol(
            "VDM", TransportKind.UDP
        )
        primary_vdm = window.protocol_configuration.sensors["AIS"].get_protocol("VDM", TransportKind.UDP)
        assert (extra_vdm.host, extra_vdm.port, extra_vdm.rate_hz) == ("239.192.192.10", 14100, 2.0)
        assert (primary_vdm.host, primary_vdm.port, primary_vdm.rate_hz) == ("239.192.192.0", 4096, 1.0)

        dialog._add_output_path(TransportKind.TCP)
        assert [dialog.output_tabs.tabText(index) for index in range(dialog.output_tabs.count())] == [
            "UDP", "TCP", "Serial", "UDP 2", "TCP 2"
        ]
        assert dialog.output_tabs.tabText(dialog.output_tabs.currentIndex()) == "TCP 2"
        monkeypatch.setattr(
            settings_dialog_module,
            "ask_question",
            lambda *_args, **_kwargs: QMessageBox.Yes,
        )
        dialog._remove_current_output_path()
        assert [dialog.output_tabs.tabText(index) for index in range(dialog.output_tabs.count())] == [
            "UDP", "TCP", "Serial", "UDP 2"
        ]
        assert [path.path_id for path in window.protocol_configuration.output_paths()] == [
            "udp", "tcp", "serial", "udp-2"
        ]
        assert dialog.output_tabs.tabText(dialog.output_tabs.currentIndex()) == "TCP"

        dialog.output_tabs.setCurrentIndex(0)
        assert not dialog.output_remove.isEnabled()
        dialog._remove_current_output_path()
        assert [path.path_id for path in window.protocol_configuration.output_paths()] == [
            "udp", "tcp", "serial", "udp-2"
        ]

        window.protocol_configuration.apply_payload(original_payload)
        dialog.refresh()
        assert [dialog.output_tabs.tabText(index) for index in range(dialog.output_tabs.count())] == [
            "UDP", "TCP", "Serial"
        ]
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_output_settings_disable_row_selection_and_use_serial_port_dropdowns():
    app, window, temporary = _temporary_window()
    try:
        window._open_settings()
        dialog = window.settings_dialog
        assert dialog is not None
        assert all(
            tree.selectionMode() == QAbstractItemView.NoSelection
            for tree in dialog.output_trees.values()
        )
        serial = dialog._transport_rows[TransportKind.SERIAL][("GNSS", "GGA")].destination
        assert isinstance(serial, QComboBox)
        assert serial.isEditable()
        dialog._apply(
            "GNSS",
            "GGA",
            output_transport=TransportKind.SERIAL,
            serial_device="CUSTOM_PORT",
        )
        assert serial.currentText() == "CUSTOM_PORT"
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_external_rudder_input_autostarts_disables_irrelevant_fields_and_opens_monitor():
    app, window, temporary = _temporary_window()
    try:
        QTest.qWait(50)
        assert window.input_runtime.is_running()
        assert window.input_runtime.status() is not None
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        rsa_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rsa_probe.bind(("127.0.0.1", 0))
        rsa_port = rsa_probe.getsockname()[1]
        rsa_probe.close()
        window.input_runtime.stop()
        window.input_runtime.update(
            host="127.0.0.1",
            port=port,
            transport="UDP",
            rsa_host="127.0.0.1",
            rsa_port=rsa_port,
            rsa_transport="UDP",
            xdr_order={"endpoint": {"host": "127.0.0.1", "port": port}},
            xdr_response={"endpoint": {"host": "127.0.0.1", "port": port}},
            bow_thruster={"primary": {"host": "127.0.0.1", "port": port}, "xdr": {"host": "127.0.0.1", "port": port}},
            engine={"primary": {"host": "127.0.0.1", "port": port}, "xdr": {"host": "127.0.0.1", "port": port}},
        )
        assert window.input_runtime.ensure_started()
        QTest.qWait(50)
        assert window.input_runtime.is_listening()
        window._open_settings()
        dialog = window.settings_dialog
        assert dialog is not None
        assert not hasattr(dialog, "input_start")

        assert [dialog.input_endpoints.tabText(index) for index in range(4)] == [
            "ROR - Rudder Order", "RSA - Rudder Response", "XDR - Rudder Order", "XDR - Rudder Response",
        ]
        editor = dialog.input_editors["ROR"]
        assert [editor.tabs.tabText(index) for index in range(3)] == ["UDP", "TCP", "Serial"]
        udp = editor.fields["UDP"]
        assert not udp["interface_ip"].isEnabled()
        udp["multicast_group"].setText("239.192.192.1")
        assert udp["interface_ip"].isEnabled()
        assert udp["port"].value() == port
        assert dialog.input_editors["RSA"].fields["UDP"]["port"].value() == rsa_port
        # Merely viewing the other endpoint must not reconfigure either listener.
        dialog.input_endpoints.setCurrentIndex(1)
        assert window.input_runtime.settings.ror.port == port
        assert window.input_runtime.settings.rsa.port == rsa_port
        serial = editor.fields["Serial"]
        assert set(serial) == {"serial_device", "baud", "parity", "stop_bits"}
        assert serial["serial_device"].isEditable()

        QTest.mouseClick(dialog.input_view, Qt.LeftButton)
        assert dialog.input_monitor.isVisible()
        assert "External Input" in dialog.input_monitor.windowTitle()
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()
