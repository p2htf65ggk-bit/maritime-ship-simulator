# SPDX-License-Identifier: MPL-2.0
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from maritime_simulator.application.ais import AISRepository, AISTarget
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import SetPosition, SetRoute
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.domain.state import RouteWaypoint
from maritime_simulator.transport.live_ais import LiveAISClient
from maritime_simulator.ui.advanced_sensors import SensorNoisePanel
from maritime_simulator.ui.controls import EngineTelegraphControl
import maritime_simulator.ui.live_ais as live_ais_ui
from maritime_simulator.ui.live_ais import (
    AISControlPanel,
    AISSettingsDialog,
    EncounterDialog,
    SyntheticTrafficPanel,
)
from maritime_simulator.ui.main_window import V2MainWindow
from maritime_simulator.ui.mode_panels import CompactModeControls
from maritime_simulator.ui.performance_panel import PerformancePanel
from maritime_simulator.ui.protocol_output_dialog import OutputEvent, ProtocolOutputDialog
from maritime_simulator.ui.route_planner import RoutePlannerPanel


def app():
    return QApplication.instance() or QApplication([])


def test_live_ais_diagnostics_distinguish_frames_valid_reports_and_provider_errors():
    app()
    service = SimulatorService()
    repository = AISRepository()
    client = LiveAISClient(repository, service.snapshot)
    client._message(json.dumps({
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 211000001, "latitude": 52.1, "longitude": 3.2},
        "Message": {"PositionReport": {"Sog": 8.5, "Cog": 91.0, "TrueHeading": 90}},
    }))
    diagnostics = client.diagnostics()
    assert diagnostics["frames"] == 1
    assert diagnostics["valid_reports"] == 1
    assert diagnostics["targets"] == 1

    client._message(json.dumps({"error": "invalid API key"}))
    assert client.diagnostics()["server_error"] == "invalid API key"
    assert "rejected" in client.status.lower()
    client.deleteLater()
    service.close()


def test_live_ais_prune_tolerates_slow_provider_reports_for_six_minutes(monkeypatch):
    app()
    service = SimulatorService()
    repository = AISRepository()
    snapshot = service.snapshot()
    repository.ingest_live((
        AISTarget(211000001, snapshot.latitude_deg, snapshot.longitude_deg, 5.0, 90.0, 90.0),
        AISTarget(211000002, snapshot.latitude_deg, snapshot.longitude_deg, 5.0, 90.0, 90.0),
    ))
    client = LiveAISClient(repository, service.snapshot)
    client._last_seen = {211000001: 641.0, 211000002: 639.0}
    monkeypatch.setattr("maritime_simulator.transport.live_ais.time.monotonic", lambda: 1000.0)

    client._prune()

    assert {target.mmsi for target in repository.all_targets()} == {211000001}
    client.deleteLater()
    service.close()


def test_ais_output_filter_ui_disables_threshold_for_all_and_round_trips(tmp_path: Path):
    app()
    service = SimulatorService()
    repository = AISRepository()
    panel = AISControlPanel(repository, service.snapshot, tmp_path / "live_ais.json")

    assert not panel.output_filter_switch.isChecked()
    assert not panel.output_min_sog.isEnabled()
    assert panel.output_min_sog.value() == pytest.approx(0.5)

    panel.output_filter_switch.click()
    panel.output_min_sog.setValue(1.7)
    assert panel.output_min_sog.isEnabled()
    assert repository.output_filter_mode == "sog_dependent"
    assert repository.output_min_sog_kn == pytest.approx(1.7)

    state = panel.export_state()
    restored_repository = AISRepository()
    restored = AISControlPanel(restored_repository, service.snapshot, tmp_path / "restored.json")
    restored.apply_state(state)
    assert restored.output_filter_switch.isChecked()
    assert restored.output_min_sog.isEnabled()
    assert restored.output_min_sog.value() == pytest.approx(1.7)
    assert restored_repository.output_filter_mode == "sog_dependent"

    restored.close()
    panel.close()
    service.close()


def test_ais_avoid_land_control_restores_offline_setting_and_legacy_default(tmp_path: Path):
    app()
    service = SimulatorService()
    repository = AISRepository(water_checker=lambda *_: None)
    panel = AISControlPanel(repository, service.snapshot, tmp_path / "live_ais.json")
    assert panel.avoid_land.isChecked()
    panel.avoid_land.click()
    assert not repository.avoid_land
    assert len(repository.all_targets()) == repository.generated_target_count
    state = panel.export_state()
    state["mode"] = "synthetic"
    panel.apply_state(state)
    assert not panel.avoid_land.isChecked()
    assert panel.avoid_land.isEnabled()
    assert not repository.avoid_land
    state.pop("avoid_land")
    panel.apply_state(state)
    assert panel.avoid_land.isChecked()
    assert repository.avoid_land
    panel.close()
    service.close()


def test_generated_ais_shows_regional_preparation_without_overwriting_live_status(tmp_path):
    app()
    service = SimulatorService()
    repository = AISRepository(generated_target_count=4, water_checker=lambda *_: None,
                               water_preparer=lambda *_: False)
    status = ['Preparing AIS water area: 3/100 tiles']
    panel = AISControlPanel(repository, service.snapshot, tmp_path / 'ais.json',
                            water_status=lambda: status[0])
    try:
        repository.use_generated(service.snapshot())
        panel.update_snapshot()
        assert panel.status.text() == 'Generated AIS: 0/4 targets\n' + status[0]
        status[0] = 'Water mask ready'
        panel.update_snapshot()
        assert panel.status.text().endswith(status[0])
        repository.ingest_live((), replace_all=True)
        panel.status.setText('Live provider connected')
        panel.update_snapshot()
        assert panel.status.text() == 'Live provider connected'
    finally:
        panel.close()
        service.close()


def test_last_saved_configuration_restores_synthetic_traffic_mode(tmp_path: Path):
    application = app()
    first = V2MainWindow(config_dir=tmp_path)
    first.timer.stop()
    try:
        snapshot = first.service.snapshot()
        first.ais_controls.avoid_land.setChecked(False)
        first.ais.add_synthetic_target(
            snapshot,
            bearing_deg=35.0,
            range_nm=1.5,
            speed_kn=9.0,
            validate_water=False,
        )
        first.ais.use_synthetic(snapshot)
        first._persist_configuration_payload(first._configuration_payload())
    finally:
        first.close()
        first.deleteLater()
        application.processEvents()

    restored = V2MainWindow(config_dir=tmp_path)
    restored.timer.stop()
    try:
        assert restored.ais.mode == "synthetic"
        assert not restored.ais.avoid_land
        assert not restored.ais_controls.avoid_land.isChecked()
        assert len(restored.ais.synthetic_targets()) == 1
    finally:
        restored.close()
        restored.deleteLater()
        application.processEvents()


def test_synthetic_target_and_encounter_dialogs_accept_without_crashing(monkeypatch):
    app()
    service = SimulatorService()
    repository = AISRepository()
    panel = SyntheticTrafficPanel(repository, service.snapshot)
    proposed_targets = []
    proposed_encounters = []

    class AcceptedTargetDialog:
        def __init__(self, **kwargs):
            proposed_targets.append(dict(kwargs.get("values", {})))

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {
                "ship_type": "Cargo",
                "bearing_deg": 25.0,
                "range_nm": 2.0,
                "course_deg": 210.0,
                "heading_deg": 210.0,
                "speed_kn": 12.0,
                "name": "SYNTHETIC TEST",
            }

    class AcceptedEncounterDialog:
        def __init__(self, **kwargs):
            proposed_encounters.append(dict(kwargs))

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {
                "encounter": "head_on",
                "ship_type": "Cargo",
                "cpa_nm": 0.2,
                "tcpa_min": 10.0,
                "target_speed_kn": 12.0,
            }

    monkeypatch.setattr(live_ais_ui, "SyntheticTargetDialog", AcceptedTargetDialog)
    panel.add_target()
    assert len(repository.synthetic_targets()) == 1
    assert proposed_targets[0]["bearing_deg"] == pytest.approx(30.0)
    assert proposed_targets[0]["range_nm"] == pytest.approx(2.0)

    panel.add_target_at(53.5403, 9.9536)
    targets = repository.synthetic_targets()
    assert len(targets) == 2
    assert targets[-1].latitude_deg == pytest.approx(53.5403)
    assert targets[-1].longitude_deg == pytest.approx(9.9536)

    monkeypatch.setattr(live_ais_ui, "EncounterDialog", AcceptedEncounterDialog)
    panel.add_encounter()
    assert len(repository.synthetic_targets()) == 3
    assert proposed_encounters[0]["suggested_tcpa_min"] == pytest.approx(18.0)

    panel.close()
    service.close()


def test_encounter_dialog_suggests_semantically_valid_overtaking_speeds():
    app()
    dialog = EncounterDialog(own_speed_kn=10.0)

    dialog.encounter.setCurrentIndex(dialog.encounter.findData("own_ship_overtakes"))
    assert dialog.speed.value() == pytest.approx(8.0)

    dialog.encounter.setCurrentIndex(dialog.encounter.findData("overtaking_own_ship"))
    assert dialog.speed.value() == pytest.approx(12.0)

    dialog.close()
    dialog.deleteLater()


def test_synthetic_creation_waits_nonmodally_for_water_then_adds_exactly_once(monkeypatch):
    from PySide6.QtTest import QTest
    app()
    service = SimulatorService()
    readiness = [None]
    repository = AISRepository(water_checker=lambda *_args: readiness[0])
    repository.use_synthetic(service.snapshot())
    panel = SyntheticTrafficPanel(repository, service.snapshot)
    warnings = []
    monkeypatch.setattr(live_ais_ui, 'show_warning', lambda *args: warnings.append(args))
    try:
        panel._create_target(dict(bearing_deg=30, range_nm=1, speed_kn=10))
        assert len(panel._pending_creations) == 1
        assert not repository.synthetic_targets()
        assert panel.creation_timer.isActive()
        assert not warnings
        readiness[0] = True
        QTest.qWait(200)
        assert len(repository.synthetic_targets()) == 1
        assert not panel._pending_creations
        assert not panel.creation_timer.isActive()
        QTest.qWait(200)
        assert len(repository.synthetic_targets()) == 1
        assert not warnings
    finally:
        panel.creation_timer.stop()
        panel.deleteLater()
        service.close()


@pytest.mark.parametrize('resolution', ['land', 'timeout', 'mode', 'clear'])
def test_pending_synthetic_requests_fail_or_cancel_without_phantom_targets(monkeypatch, resolution):
    app()
    service = SimulatorService()
    readiness = [None]
    repository = AISRepository(water_checker=lambda *_args: readiness[0])
    repository.use_synthetic(service.snapshot())
    panel = SyntheticTrafficPanel(repository, service.snapshot)
    warnings = []
    monkeypatch.setattr(live_ais_ui, 'show_warning', lambda *args: warnings.append(args))
    try:
        panel._create_target(dict(bearing_deg=30, range_nm=1, speed_kn=10))
        assert len(panel._pending_creations) == 1
        if resolution == 'land':
            readiness[0] = False
        elif resolution == 'timeout':
            callback, _deadline, mode = panel._pending_creations[0]
            panel._pending_creations[0] = callback, 0, mode
        elif resolution == 'mode':
            repository.ingest_live(())
        else:
            panel.clear_all()
        panel._retry_creations()
        assert not repository.synthetic_targets()
        assert not panel._pending_creations
        assert not panel.creation_timer.isActive()
        assert not warnings
    finally:
        panel.creation_timer.stop()
        panel.deleteLater()
        service.close()


def test_protocol_monitor_freeze_preserves_display_without_pausing_output():
    app()
    events = [OutputEvent(1, 1.0, "UDP 1", "GGA", "$GPGGA,first")]
    dialog = ProtocolOutputDialog(
        output_running=lambda: True,
        configured_ports=lambda: ("UDP 1",),
        output_events=lambda: tuple(events),
        clear_events=events.clear,
        serial_load=lambda _port: None,
    )
    dialog.refresh(force=True)
    frozen_text = dialog.monitor.toPlainText()
    dialog.freeze.setChecked(True)
    events.append(OutputEvent(2, 2.0, "UDP 1", "RMC", "$GPRMC,second"))
    dialog.refresh()
    assert dialog.monitor.toPlainText() == frozen_text
    assert dialog._last_sequence == 1
    assert "output running" in dialog.status.text().lower()
    dialog.freeze.setChecked(False)
    assert "$GPRMC,second" in dialog.monitor.toPlainText()
    dialog.deleteLater()


def test_aisstream_subscription_confirmation_reports_compression_without_discarding_frame():
    app()
    service = SimulatorService()
    client = LiveAISClient(AISRepository(), service.snapshot)
    client._message(json.dumps({
        "MessageType": "SubscriptionConfirmation",
        "Message": {"SubscriptionConfirmation": {"Success": True, "CompressionEnabled": True}},
    }))
    diagnostics = client.diagnostics()
    assert diagnostics["frames"] == 1
    assert diagnostics["discarded_frames"] == 0
    assert diagnostics["compression"] == "Enabled"
    assert diagnostics["subscription"] == "Sent"
    client.deleteLater()
    service.close()


def test_live_ais_static_report_is_merged_into_later_position_output():
    app()
    service = SimulatorService()
    repository = AISRepository()
    client = LiveAISClient(repository, service.snapshot)
    client._message(json.dumps({
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 211000001},
        "Message": {"ShipStaticData": {
            "UserID": 211000001,
            "Name": "MERGED VESSEL",
            "CallSign": "DTEST",
            "Type": 70,
            "Dimension": {"A": 70, "B": 30, "C": 6, "D": 6},
            "MaximumStaticDraught": 65,
            "Destination": "KIEL",
        }},
    }))
    client._message(json.dumps({
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 211000001, "latitude": 52.1, "longitude": 3.2},
        "Message": {"PositionReport": {"Sog": 8.5, "Cog": 91.0, "TrueHeading": 90}},
    }))
    target = repository.target(211000001)
    assert target is not None
    assert (target.name, target.callsign, target.destination) == ("MERGED VESSEL", "DTEST", "KIEL")
    assert (target.length_m, target.beam_m, target.draught_m) == (100.0, 12.0, 6.5)
    client.deleteLater()
    service.close()


def test_live_ais_provider_parser_accepts_official_barentswatch_shape():
    app()
    service = SimulatorService()
    client = LiveAISClient(AISRepository(), service.snapshot)
    barentswatch = client._target_from_barentswatch({
        "mmsi": 257011940,
        "latitude": 69.543985,
        "longitude": 17.769428,
        "speedOverGround": 4.2,
        "courseOverGround": 356.7,
        "trueHeading": 355,
        "name": "BALDER",
    })
    assert barentswatch is not None
    assert barentswatch.name == "BALDER"
    assert barentswatch.heading_deg == 355.0
    client.deleteLater()
    service.close()


def test_explicit_own_ship_position_change_requests_live_ais_scope_refresh(tmp_path, monkeypatch):
    application = app()
    window = V2MainWindow(config_dir=tmp_path)
    calls = []
    try:
        window.timer.stop()
        window.ais.ingest_live((), replace_all=True)
        monkeypatch.setattr(
            window.ais_controls.client,
            "own_ship_position_changed",
            lambda: calls.append(window.service.snapshot()),
        )
        window._submit_ui_command(SetPosition(54.0, 10.0))
        assert len(calls) == 1
        assert calls[0].latitude_deg == pytest.approx(54.0)
        assert calls[0].longitude_deg == pytest.approx(10.0)
    finally:
        window.close()
        window.deleteLater()
        application.processEvents()


def test_live_ais_scope_boxes_are_recalculated_around_current_own_ship():
    app()
    service = SimulatorService()
    client = LiveAISClient(AISRepository(), service.snapshot)
    try:
        first = client._subscription_boxes(service.snapshot())
        service.submit(SetPosition(60.0, 5.0))
        service.advance_wall_time(0.0)
        second = client._subscription_boxes(service.snapshot())
        assert first != second
        assert second[0][0][0] < 60.0 < second[0][1][0]
        assert second[0][0][1] < 5.0 < second[0][1][1]
    finally:
        client.deleteLater()
        service.close()


def test_live_ais_scope_refresh_is_debounced_after_explicit_position_change():
    app()
    service = SimulatorService()
    repository = AISRepository()
    repository.ingest_live((), replace_all=True)
    client = LiveAISClient(repository, service.snapshot)
    try:
        client.own_ship_position_changed()
        assert client.scope_refresh_timer.isActive()
        client.own_ship_position_changed()
        assert client.scope_refresh_timer.isActive()
    finally:
        client.scope_refresh_timer.stop()
        client.deleteLater()
        service.close()


def test_aisstream_scope_refresh_updates_subscription_without_reconnecting():
    app()
    service = SimulatorService()
    repository = AISRepository()
    repository.ingest_live((), replace_all=True)
    client = LiveAISClient(repository, service.snapshot)
    subscriptions = []
    try:
        client.provider = LiveAISClient.AISSTREAM
        client.api_key = "test-key"
        client._transport_state = "Connected"
        client.subscription_sent = True
        client._aisstream.update_subscription = subscriptions.append
        service.submit(SetPosition(60.0, 5.0))
        service.advance_wall_time(0.0)
        client._refresh_scope_now()
        assert len(subscriptions) == 1
        assert subscriptions[0].payload()["BoundingBoxes"] == client._last_boxes
        assert client._transport_state == "Connected"
    finally:
        client.deleteLater()
        service.close()


def test_live_ais_scope_recenters_automatically_after_meaningful_own_ship_motion():
    app()
    service = SimulatorService()
    repository = AISRepository()
    repository.ingest_live((), replace_all=True)
    client = LiveAISClient(repository, service.snapshot)
    try:
        original = service.snapshot()
        client.subscription_range_nm = 5.0
        client._scope_center = (original.latitude_deg, original.longitude_deg)
        service.submit(SetPosition(original.latitude_deg + 0.05, original.longitude_deg))
        service.advance_wall_time(0.0)
        client._schedule_scope_refresh_if_needed()
        assert client.scope_refresh_timer.isActive()
    finally:
        client.scope_refresh_timer.stop()
        client.deleteLater()
        service.close()


def test_live_ais_settings_use_provider_specific_scope_language(tmp_path):
    app()
    service = SimulatorService()
    client = LiveAISClient(AISRepository(), service.snapshot)
    dialog = AISSettingsDialog(client, tmp_path / "live_ais.json")
    assert all(dialog.provider.itemData(index) != "fintraffic" for index in range(dialog.provider.count()))
    assert "aisstream.io/account" in dialog.api_key_row.findChild(QLabel).text()

    dialog.provider.setCurrentIndex(dialog.provider.findData(LiveAISClient.BARENTSWATCH))
    assert dialog.subscription_range_label.text() == "Local display range"
    assert dialog.worldwide.text() == "Show all Norwegian coverage"
    assert "barentswatch.no/minside" in dialog.client_secret_row.findChild(QLabel).text()
    assert "not a worldwide" in dialog.worldwide.toolTip().lower()
    client.provider = LiveAISClient.BARENTSWATCH
    client.worldwide_debug = True
    assert client.diagnostics()["scope"] == "All Norwegian coverage"

    dialog.provider.setCurrentIndex(dialog.provider.findData(LiveAISClient.AISSTREAM))
    assert dialog.subscription_range_label.text() == "Subscription range"
    assert dialog.worldwide.text() == "Worldwide subscription (high load)"
    client.provider = LiveAISClient.AISSTREAM
    assert client.diagnostics()["scope"] == "Worldwide subscription"
    dialog.deleteLater()
    client.deleteLater()
    service.close()


def test_map_ownship_uses_direct_rotation_and_high_contrast_non_ais_colour():
    html = Path("ui/web/map.html").read_text(encoding="utf-8")
    assert "function updateShipMarkerIcon" in html
    assert "svg.style.transform = `rotate(${normalized}deg)`" in html
    assert 'fill="#ffc94f"' in html
    assert 'stroke="#0a1720"' in html
    assert "currentHeading - heading" not in html


def test_map_widget_routes_trackpad_scroll_to_pan_without_hijacking_mouse_wheel():
    from ui.widgets.map_widget import _map_wheel_zoom_steps, _trackpad_pan_pixels

    class Device:
        def __init__(self, device_type):
            self._device_type = device_type

        def type(self):
            return self._device_type

    class WheelEvent:
        def __init__(self, *, pixel, angle, phase, device_type, modifiers=Qt.NoModifier):
            self._pixel = QPoint(*pixel)
            self._angle = QPoint(*angle)
            self._phase = phase
            self._device = Device(device_type)
            self._modifiers = modifiers

        def pixelDelta(self):
            return self._pixel

        def angleDelta(self):
            return self._angle

        def phase(self):
            return self._phase

        def device(self):
            return self._device

        def modifiers(self):
            return self._modifiers

    from PySide6.QtGui import QInputDevice

    trackpad = WheelEvent(
        pixel=(14, -22),
        angle=(0, 0),
        phase=Qt.ScrollPhase.ScrollUpdate,
        device_type=QInputDevice.DeviceType.TouchPad,
    )
    mouse = WheelEvent(
        pixel=(0, 0),
        angle=(0, 120),
        phase=Qt.ScrollPhase.NoScrollPhase,
        device_type=QInputDevice.DeviceType.Mouse,
    )
    pinch = WheelEvent(
        pixel=(0, 18),
        angle=(0, 0),
        phase=Qt.ScrollPhase.ScrollUpdate,
        device_type=QInputDevice.DeviceType.TouchPad,
        modifiers=Qt.ControlModifier,
    )

    assert _trackpad_pan_pixels(trackpad) == (-14, 22)
    assert _trackpad_pan_pixels(mouse) is None
    assert _trackpad_pan_pixels(pinch) is None
    assert _map_wheel_zoom_steps(pinch) == pytest.approx(0.15)

    source = Path("ui/widgets/map_widget.py").read_text(encoding="utf-8")
    html = Path("ui/web/map.html").read_text(encoding="utf-8")
    assert "class _MapInputFilter" in source
    assert "NativePinchZoom" in source
    assert "Qt.NativeGestureType.ZoomNativeGesture" in Path("ui/web_input.py").read_text(encoding="utf-8")
    assert "window.zoomMapByNativeGesture" in source
    assert "window.zoomMapByNativeGesture = function" in html
    assert "function animateSmoothMapZoom(timestamp)" in html
    assert "window.requestAnimationFrame(animateSmoothMapZoom)" in html
    assert "Math.max(-0.24, Math.min(0.24, interpolatedStep))" in html
    assert "map._move(center, nextZoom, {pinch: true, round: false})" in html
    assert "window.setTimeout(applyCoalescedMapZoom, 40)" not in html
    assert "zoomSnap: 0" in html
    assert "touchZoom: true" in html
    assert "map.options.touchZoom = followOwnShip ? 'center' : true" in html
    assert "followOwnShip && ownShipLatLng ? L.latLng(ownShipLatLng)" in html
    assert "event.stopImmediatePropagation()" in html
    assert "time.monotonic() - self._last_native_zoom_at < 0.16" in source
    assert "_event_position_in_view(watched, event, self._view)" in source
    assert "function beginSyntheticDrag()" in html
    assert "function endSyntheticDrag()" in html


def test_heading_line_uses_viewport_ray_instead_of_globe_spanning_distance():
    html = Path("ui/web/map.html").read_text(encoding="utf-8")
    assert "function headingLineCoordinates" in html
    assert "map.latLngToLayerPoint([lat, lon])" in html
    assert "map.layerPointToLatLng(endpoint)" in html
    assert "map.on('zoomend moveend resize', refreshHeadingLine)" in html
    assert "headingLineDistanceNm" not in html


def test_ais_popup_temporarily_suspends_follow_recentering():
    html = Path("ui/web/map.html").read_text(encoding="utf-8")
    assert "let aisPopupOpen = false" in html
    assert "(center || followOwnShip) && !aisPopupOpen" in html
    assert "m.on('popupopen'" in html
    assert "m.on('popupclose'" in html


def test_ais_symbol_uses_heading_while_motion_vector_uses_cog():
    html = Path("ui/web/map.html").read_text(encoding="utf-8")
    assert "const hasReportedHeading" in html
    assert "let orientation =" in html
    assert "createAISIconStyled(orientation, color, width)" in html
    assert "updateAISMarkerIcon(m, orientation, color, width)" in html
    assert "destinationPoint(lat, lon, course, vectorDistance)" in html


def test_ais_popup_separates_real_provider_age_from_dead_reckoning():
    html = Path("ui/web/map.html").read_text(encoding="utf-8")
    assert '"Real report age"' in html
    assert '"Last real interval"' in html
    assert '"Position source"' in html
    assert 'class="ais-nowrap">Dead reckoning ${formatAISAgeSeconds' in html
    assert "Dead reckoning (" not in html
    assert "Math.round(seconds)" in html
    assert ".ais-popup .ais-nowrap" in html
    assert "white-space: nowrap" in html


def test_open_ais_popup_is_refreshed_from_latest_live_target_payload():
    html = Path("ui/web/map.html").read_text(encoding="utf-8")
    assert "m.latestAisTarget = {...t, lat, lon, course, speed, heading: orientation}" in html
    assert "else if (m.isPopupOpen())" in html
    assert "m.setPopupContent(aisPopupHtml(m.latestAisTarget))" in html


def test_synthetic_map_drag_keeps_local_edit_until_model_confirms_it():
    html = Path("ui/web/map.html").read_text(encoding="utf-8")
    assert "m.syntheticDragging = true" in html
    assert "if (!m.syntheticDragging) m.setLatLng([lat, lon])" in html
    assert "m.syntheticPendingPosition" in html
    assert "handle.syntheticPendingVector" in html
    assert "expiresAt: Date.now() + 3000" in html


def test_live_ais_missing_credentials_remain_empty_and_are_not_silently_bundled(tmp_path, monkeypatch):
    app()
    import maritime_simulator.ui.live_ais as live_ais_ui

    monkeypatch.setattr(live_ais_ui, "DEFAULT_AISSTREAM_API_KEY", "")
    monkeypatch.setattr(live_ais_ui, "DEFAULT_BARENTSWATCH_CLIENT_ID", "")
    monkeypatch.setattr(live_ais_ui, "DEFAULT_BARENTSWATCH_CLIENT_SECRET", "")
    service = SimulatorService()
    client = LiveAISClient(AISRepository(), service.snapshot)
    settings_path = tmp_path / "live_ais_v2.json"
    settings_path.write_text(
        json.dumps({"barentswatch_client_id": "", "barentswatch_client_secret": ""}),
        encoding="utf-8",
    )
    dialog = AISSettingsDialog(client, settings_path)
    assert dialog.api_key.text() == ""
    assert dialog.client_id.text() == ""
    assert dialog.client_secret.text() == ""
    assert client.barentswatch_client_id == ""
    assert client.barentswatch_client_secret == ""
    dialog.deleteLater()
    client.deleteLater()
    service.close()


def test_compact_control_slider_axis_is_fixed_and_track_peer_value_is_derived():
    app()
    service = SimulatorService()
    panel = CompactModeControls(service)
    control = panel.sensor_sog
    positions = []
    for value in (-9.0, 9.0, 19.0):
        control.set_value(value)
        positions.append(control.slider.mapTo(control, QPoint(0, 0)).x())
    assert len(set(positions)) == 1

    snapshot = replace(
        service.snapshot(),
        control_mode=ControlMode.TRACK,
        use_planned_turns=False,
        control_turn_mode="fixed_rot",
        control_turn_rate_deg_min=10.0,
        control_turn_radius_nm=9.0,
        sog_kn=12.0,
        speed_order_kn=12.0,
    )
    panel.update_snapshot(snapshot)
    assert panel.track_rot.title.text() == "RATE"
    assert panel.track_radius.current_value() < 9.0
    derived_radius = panel.track_radius.current_value()

    panel.update_snapshot(replace(snapshot, control_turn_mode="fixed_radius", control_turn_radius_nm=derived_radius))
    assert panel.track_rot.current_value() == pytest.approx(10.0, abs=0.2)
    panel.deleteLater()
    service.close()


def test_noise_amplitude_emits_immediately_and_xte_lives_in_noise_panel():
    app()
    panel = SensorNoisePanel()
    commands = []
    panel.command_submitted.connect(commands.append)
    enabled, amplitude = panel.noise_fields["xte"]
    enabled.setChecked(True)
    amplitude.setValue(0.125)
    assert commands[-1].name == "xte"
    assert commands[-1].amplitude == 0.125
    period, period_label = panel.noise_period_fields["xte"]
    period.setValue(25)
    assert commands[-1].name == "xte"
    assert commands[-1].period_s == pytest.approx(2.5)
    assert period_label.text() == "2.5 s"


def test_continuous_telegraph_bolds_both_bracketing_detents():
    app()
    control = EngineTelegraphControl()
    steps = (("Full Ahead", 20.0), ("Half Ahead", 12.0), ("Stop", 0.0), ("Full Astern", -6.0))
    control.set_steps(steps, 6.0)
    assert not control._buttons[0].font().bold()
    assert control._buttons[1].font().bold()
    assert control._buttons[2].font().bold()
    assert not control._buttons[3].font().bold()
    assert control.currentData() == 6.0


def test_route_defaults_are_numeric_and_collapsed_arrow_points_up():
    app()
    service = SimulatorService()
    route = V2MainWindow._normalize_route_for_planner(
        (RouteWaypoint("A", 52.0, 3.0), RouteWaypoint("B", 52.1, 3.0))
    )
    assert all(waypoint.speed_kn == 10.0 for waypoint in route)
    assert all(waypoint.turn_rate_deg_min == 10.0 for waypoint in route)

    panel = RoutePlannerPanel()
    panel.set_route(route)
    assert "Manual" not in panel.table.item(0, panel.SPEED).text()
    panel._toggle_collapsed()
    assert panel.collapse_button.arrowType() == Qt.UpArrow
    service.close()


def test_performance_panel_exposes_operator_rows_and_keeps_optional_raw_details():
    app()
    service = SimulatorService()
    panel = PerformancePanel(service.performance_monitor)
    panel.enabled.setChecked(True)
    service.performance_monitor.increment("network_send_errors")
    panel.refresh()
    assert set(panel.metric_bars) == {"ui", "simulation", "track", "output", "serial", "errors", "ais", "memory"}
    assert panel.metric_bars["errors"].load() >= 70.0
    assert '"network_send_errors": 1' in panel.output.toPlainText()
    service.close()


def test_performance_panel_refresh_timer_runs_at_operator_rate():
    app()
    service = SimulatorService()
    panel = PerformancePanel(service.performance_monitor)
    panel.show()
    QApplication.processEvents()
    assert panel.timer.isActive()
    assert panel.timer.interval() >= 1000
    panel.hide()
    QApplication.processEvents()
    assert not panel.timer.isActive()
    service.close()


def test_performance_technical_details_preserve_manual_scroll_position():
    app()
    service = SimulatorService()
    panel = PerformancePanel(service.performance_monitor)
    panel.timer.stop()
    panel.enabled.setChecked(True)
    panel.details_toggle.setChecked(True)
    panel.output.setFixedHeight(80)
    panel.show()
    for index in range(80):
        service.performance_monitor.increment(f"diagnostic_{index}", index + 1)
    panel.refresh()
    QApplication.processEvents()
    scrollbar = panel.output.verticalScrollBar()
    assert scrollbar.maximum() > 0
    target = max(1, scrollbar.maximum() // 2)
    scrollbar.setValue(target)
    service.performance_monitor.increment("new_live_value")
    panel.refresh()
    QApplication.processEvents()
    assert abs(scrollbar.value() - target) <= 1
    panel.hide()
    panel.deleteLater()
    service.close()


def test_opening_protocol_monitor_does_not_restart_or_stop_output(tmp_path):
    application = app()
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        window.output_runtime.start()
        active_service = window.output_runtime._service
        assert active_service is not None
        assert window.output_runtime.timer.isActive()

        window._open_protocol_output()
        application.processEvents()

        assert window.output_runtime._service is active_service
        assert window.output_runtime.timer.isActive()
        assert window.protocol_dialog is not None
        assert "monitor" in window.protocol_dialog.status.text().lower()
    finally:
        window.output_runtime.close()
        window.input_runtime.close()
        window.service.close()
        window.deleteLater()
        application.processEvents()
