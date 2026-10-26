# SPDX-License-Identifier: MPL-2.0
import json
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QNetworkReply

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import SetEnvironment, SetEnvironmentSource, SetOnlineEnvironment
from maritime_simulator.transport import live_weather
from maritime_simulator.transport.live_environment import (
    LiveWindClient, LiveCurrentClient, LiveVectorReading, wind_url, current_url, parse_wind, parse_current,
)
from maritime_simulator.transport.live_weather import validate_api_key


def apply(service, *commands):
    for command in commands:
        service.submit(command)
    service.advance_wall_time(0)
    return service.snapshot()


def vector_payload(*, marine=False, speed=18.52, direction=90, unit=None):
    speed_field = "ocean_current_velocity" if marine else "wind_speed_10m"
    direction_field = "ocean_current_direction" if marine else "wind_direction_10m"
    return {
        "current": {speed_field: speed, direction_field: direction, "time": "2026-10-26T12:00"},
        "current_units": {speed_field: unit or ("km/h" if marine else "kn")},
    }


@pytest.mark.parametrize("channel,maximum", [("wind", 150), ("current", 20)])
def test_vector_source_fallback_holds_last_values_and_returns_automatically(channel, maximum):
    service = SimulatorService()
    snapshot = apply(service, SetEnvironmentSource(channel, "open-meteo"), SetOnlineEnvironment(channel, 270, 5))
    assert getattr(snapshot, f"active_{channel}_source") == "open-meteo"
    snapshot = apply(service, SetEnvironment(**{f"{channel}_speed_kn": 9}))
    assert getattr(snapshot, f"{channel}_speed_kn") == 5  # Online controls cannot be overwritten accidentally.
    snapshot = apply(service, SetOnlineEnvironment(channel))
    assert getattr(snapshot, f"active_{channel}_source") == "manual"
    assert getattr(snapshot, f"{channel}_speed_kn") == 5
    assert getattr(snapshot, f"{channel}_from_deg") == 270
    snapshot = apply(service, SetEnvironment(**{f"{channel}_speed_kn": 6}))
    assert getattr(snapshot, f"{channel}_speed_kn") == 6
    snapshot = apply(service, SetOnlineEnvironment(channel, 180, 7))
    assert getattr(snapshot, f"{channel}_speed_kn") == 7
    snapshot = apply(service, SetEnvironmentSource(channel, "manual"), SetOnlineEnvironment(channel, 0, 2))
    assert getattr(snapshot, f"{channel}_speed_kn") == 7


@pytest.mark.parametrize("direction,speed", [(float("nan"), 5), (0, float("inf")), (361, 2), (90, -1), (None, 1), ("bad", 1), (True, 1)])
def test_invalid_online_vectors_never_replace_last_good_values(direction, speed):
    service = SimulatorService()
    snapshot = apply(service, SetEnvironmentSource("wind", "open-meteo"), SetOnlineEnvironment("wind", 10, 4),
                     SetOnlineEnvironment("wind", direction, speed))
    assert snapshot.wind_from_deg == 10 and snapshot.wind_speed_kn == 4
    assert snapshot.active_wind_source == "manual"


def test_wind_and_current_sources_are_independent():
    service = SimulatorService()
    snapshot = apply(service, SetEnvironmentSource("wind", "open-meteo"), SetOnlineEnvironment("wind", 40, 6),
                     SetEnvironmentSource("current", "open-meteo"), SetOnlineEnvironment("current", 180, 2),
                     SetOnlineEnvironment("current"))
    assert snapshot.active_wind_source == "open-meteo"
    assert snapshot.active_current_source == "manual"
    assert snapshot.wind_speed_kn == 6 and snapshot.current_speed_kn == 2


def test_request_units_commercial_host_and_current_direction_conversion():
    wind = wind_url(40.7, -74)
    query = parse_qs(urlsplit(wind).query)
    assert query["wind_speed_unit"] == ["kn"]
    assert "apikey" not in query
    assert urlsplit(current_url(40.7, -74, api_key="example-key")).hostname == "customer-marine-api.open-meteo.com"
    assert urlsplit(wind_url(40.7, -74, api_key="example-key")).hostname == "customer-api.open-meteo.com"
    reading = parse_current(vector_payload(marine=True), latitude_deg=40.7, longitude_deg=-74)
    assert reading.speed_kn == pytest.approx(10)
    assert reading.direction_from_deg == 270  # API flows east; simulator receives "from west".
    wind = parse_wind(vector_payload(speed=10), latitude_deg=40.7, longitude_deg=-74)
    assert wind.speed_kn == 10 and wind.direction_from_deg == 90


@pytest.mark.parametrize("parser,marine", [(parse_wind, False), (parse_current, True)])
@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), True, "bad"])
def test_invalid_provider_speeds_are_rejected(parser, marine, value):
    with pytest.raises((TypeError, ValueError)):
        parser(vector_payload(marine=marine, speed=value), latitude_deg=40.7, longitude_deg=-74)


def test_calm_unknown_direction_is_allowed_but_far_away_marine_cells_are_rejected():
    value = vector_payload(marine=True, speed=0, direction=None)
    assert parse_current(value, latitude_deg=40.7, longitude_deg=-74).speed_kn == 0
    value.update(latitude=41.7, longitude=-74)
    with pytest.raises(ValueError, match="too far"):
        parse_current(value, latitude_deg=40.7, longitude_deg=-74)


class FakeReply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.payload = b""
        self.failed = False

    def setReadBufferSize(self, _size):
        pass

    def readAll(self):
        payload, self.payload = self.payload, b""
        return payload

    def error(self):
        return QNetworkReply.OperationCanceledError if self.failed else QNetworkReply.NoError

    def errorString(self):
        return "Mock transport failure"

    def abort(self):
        self.failed = True
        self.finished.emit()

    def complete(self, value):
        self.payload = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.readyRead.emit()
        self.finished.emit()


def make_client(monkeypatch, client_class=LiveWindClient):
    app = QApplication.instance() or QApplication([])
    clock = [1000.0]
    monkeypatch.setattr(live_weather, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = client_class()
    requests = []

    def get(request):
        reply = FakeReply()
        requests.append((request, reply))
        return reply

    client.network = SimpleNamespace(get=get)
    return app, client, requests, clock


def test_client_holds_on_error_retries_and_recovers_without_request_storms(monkeypatch):
    app, client, requests, clock = make_client(monkeypatch)
    values, unavailable = [], []
    client.reading_received.connect(values.append)
    client.unavailable.connect(lambda: unavailable.append(True))
    try:
        client.set_enabled(True)
        client.update_position(40.7, -74)
        assert len(requests) == 1
        requests[0][1].complete(vector_payload(speed=5))
        assert values[-1].speed_kn == 5
        clock[0] += 601
        client.refresh()
        requests[-1][1].complete(vector_payload(speed=None))
        assert unavailable == [True]
        assert len(values) == 1
        count = len(requests)
        for index in range(100):
            client.update_position(41+index*.001, -74)
            client.refresh(force=True)
        assert len(requests) == count
        clock[0] += 61
        client.refresh()
        requests[-1][1].complete(vector_payload(speed=7))
        assert values[-1].speed_kn == 7
        assert client._available and client._failures == 0
    finally:
        client.close()


def test_cached_readings_are_reused_and_manual_or_close_aborts_pending_requests(monkeypatch):
    app, client, requests, clock = make_client(monkeypatch)
    values = []
    client.reading_received.connect(values.append)
    try:
        client.update_position(40.7, -74)
        client.set_enabled(True)
        requests[0][1].complete(vector_payload(speed=5))
        client.set_enabled(False)
        client.set_enabled(True)
        assert len(requests) == 1 and len(values) == 2
        clock[0] += 61
        client.update_position(53.5, 10)
        pending = requests[-1][1]
        assert len(requests) == 2
        client.set_enabled(False)
        assert pending.failed
        pending.complete(vector_payload(speed=20))
        assert len(values) == 2
    finally:
        client.close()
    assert not client.refresh_timer.isActive()
    assert not client.enabled


def test_returning_to_cached_position_does_not_toggle_sources_every_ui_tick(monkeypatch):
    app, client, requests, clock = make_client(monkeypatch)
    values = []
    client.reading_received.connect(values.append)
    try:
        client.update_position(40, -74)
        client.set_enabled(True)
        requests[-1][1].complete(vector_payload(speed=5))
        clock[0] += 61
        client.update_position(41, -74)
        requests[-1][1].complete(vector_payload(speed=6))
        client.update_position(40, -74)
        assert values[-1].speed_kn == 5
        count = len(values)
        for _ in range(100):
            client.update_position(40, -74)
        assert len(values) == count
        assert len(requests) == 2
    finally:
        client.close()


def test_key_validation_and_urls_do_not_leak_secrets_in_error_status(monkeypatch):
    app, client, requests, clock = make_client(monkeypatch)
    statuses = []
    client.status_changed.connect(statuses.append)
    try:
        client.set_api_key("example-key")
        client.update_position(40.7, -74)
        client.set_enabled(True)
        assert requests[0][0].url().host() == "customer-api.open-meteo.com"
        requests[0][1].abort()
        assert all("example-key" not in status for status in statuses)
        with pytest.raises(ValueError):
            validate_api_key("not a key")
    finally:
        client.close()


def test_relocation_cancels_old_reply_and_coalesces_to_latest_position(monkeypatch):
    app, client, requests, clock = make_client(monkeypatch)
    values = []
    client.reading_received.connect(values.append)
    try:
        client.update_position(40.7, -74)
        client.set_enabled(True)
        old = requests[0][1]
        client.update_position(53.5, 10)
        assert old.failed and len(requests) == 1
        old.complete(vector_payload(speed=10))
        assert not values
        client.update_position(54.0, 10)
        client.update_position(55.0, 10)
        clock[0] += 61
        client.refresh()
        assert len(requests) == 2
        assert "latitude=55.00000" in requests[-1][0].url().toString()
        requests[-1][1].complete(vector_payload(speed=8))
        assert values[-1].latitude_deg == 55
    finally:
        client.close()


def test_polling_cache_has_fixed_capacity_and_oversized_responses_fall_back(monkeypatch):
    app, client, requests, clock = make_client(monkeypatch)
    client.MAX_CACHE_READINGS = 4
    unavailable = []
    client.unavailable.connect(lambda: unavailable.append(True))
    try:
        client.update_position(40, -74)
        client.set_enabled(True)
        for index in range(6):
            if index:
                clock[0] += 601
                client.update_position(40+index*.2, -74)
            requests[-1][1].complete(vector_payload(speed=5))
        assert len(client._cache) == 4
        clock[0] += 601
        client.refresh()
        requests[-1][1].complete(b"x"*131073)
        assert not client._available and client._reply is None
        assert unavailable
        assert client._retry_at > clock[0]
    finally:
        client.close()


def test_online_current_drives_ground_vector_and_noise_does_not_change_fallback():
    from maritime_simulator.domain.commands import SetControlMode, SetSensorNoise
    from maritime_simulator.domain.enums import ControlMode
    from maritime_simulator.protocols.sensors import SensorProjector
    service = SimulatorService()
    apply(service, SetControlMode(ControlMode.SHIP), SetEnvironmentSource("current", "open-meteo"),
          SetOnlineEnvironment("current", 270, 3), SetEnvironmentSource("wind", "open-meteo"),
          SetOnlineEnvironment("wind", 0, 0))
    service.advance_wall_time(.1)
    snapshot = service.snapshot()
    assert snapshot.cog_deg == pytest.approx(90)
    assert abs(snapshot.sog_kn) == pytest.approx(3)
    snapshot = apply(service, SetOnlineEnvironment("wind", 30, 6), SetSensorNoise("wind_speed", enabled=True, amplitude=2))
    SensorProjector().project(snapshot)
    fallback = apply(service, SetOnlineEnvironment("wind"))
    assert fallback.wind_speed_kn == 6
    assert fallback.wind_from_deg == 30


def test_sensor_mode_recalculates_true_and_apparent_wind_without_advancing_time():
    from maritime_simulator.domain.commands import SetSensorValues
    from maritime_simulator.protocols.sensors import SensorProjector
    from maritime_simulator.protocols.iec61162 import encode_protocol
    service = SimulatorService()
    snapshot = apply(service, SetSensorValues(heading_deg=0, cog_deg=0, sog_kn=5),
                     SetEnvironmentSource("wind", "open-meteo"), SetOnlineEnvironment("wind", 0, 10))
    assert snapshot.true_wind_speed_kn == 10
    assert snapshot.relative_wind_speed_kn == pytest.approx(15)
    assert snapshot.relative_wind_from_bow_deg == pytest.approx(0)
    sensor = SensorProjector().project(snapshot)
    assert ",10.0,N," in encode_protocol("MWD", sensor)
    assert ",15.0,N," in encode_protocol("MWV", sensor)


def test_environment_ui_roundtrip_and_offline_restore_keep_sources_and_last_values(tmp_path):
    from maritime_simulator.ui.main_window import V2MainWindow
    from maritime_simulator.visual.offline import OfflineSettings
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        window._set_open_meteo_api_key("example-key")
        for channel in ("wind", "current"):
            source = getattr(window.environment_controls, f"{channel}_source")
            source.setCurrentIndex(source.findData("open-meteo"))
            window._apply_online_vector(channel, LiveVectorReading(40.7, -74, 270, 5, "2026-10-26T12:00"))
        window.service.advance_wall_time(0)
        snapshot = window.service.snapshot()
        window.environment_controls.update_snapshot(snapshot)
        assert not window.environment_controls.wind_speed.isEnabled()
        assert not window.environment_controls.current_direction.isEnabled()
        payload = window._configuration_payload()
        env = payload["app_state"]["simulation"]["environment"]
        assert env["wind_source"] == env["current_source"] == "open-meteo"
        assert env["wind_speed_kn"] == env["current_speed_kn"] == 5
        assert env["open_meteo_api_key"] == "example-key"
        fragment = window.startup_profile.group_fragment("environment", payload)
        assert fragment["app_state"]["simulation"]["environment"] == env
        window._apply_offline_settings(OfflineSettings(enabled=True))
        window.service.advance_wall_time(0)
        snapshot = window.service.snapshot()
        assert snapshot.wind_speed_kn == 5 and snapshot.active_wind_source == "manual"
        assert not window.live_wind.enabled and not window.live_current.enabled
        window.environment_controls.update_snapshot(snapshot)
        assert window.environment_controls.wind_speed.isEnabled()
        assert window.environment_controls.current_speed_slider.isEnabled()
        window._apply_configuration_payload(payload)
        assert window.live_wind.enabled and window.live_current.enabled
        assert window.service.snapshot().wind_source == "open-meteo"
        assert window.service.snapshot().wind_speed_kn == 5
        window._submit_ui_command(SetEnvironmentSource("wind", "manual"))
        assert not window.live_wind.enabled and window.live_current.enabled
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_open_meteo_key_is_hidden_in_startup_differences_and_rejected_by_release_audit(tmp_path):
    from maritime_simulator.ui.startup_profile_panel import _differences
    from scripts.check_v2_release_credentials import audit
    key_name = "open_meteo_" + "api_key"
    value = "temporary-private-value"
    assert value not in _differences({key_name: value}, {})
    file = tmp_path / "provider.txt"
    file.write_text(json.dumps({key_name: value}))
    findings = audit([file])
    assert any("named provider credential" in line for line in findings)
    assert all(value not in line for line in findings)


def test_environment_reflows_wind_and_current_in_scroll_area_without_horizontal_overflow():
    from PySide6.QtWidgets import QScrollArea
    from maritime_simulator.ui.mode_panels import EnvironmentPanel
    app = QApplication.instance() or QApplication([])
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    panel = EnvironmentPanel()
    scroll.setWidget(panel)
    scroll.show()
    try:
        service = SimulatorService()
        panel.command_submitted.connect(service.submit)
        snapshot = apply(service, SetEnvironment(wind_speed_kn=8, current_speed_kn=2),
                         SetEnvironmentSource("wind", "open-meteo"))
        panel.update_snapshot(snapshot)
        for width, columns in ((340, 2), (800, 2), (280, 1), (800, 2), (280, 1)):
            scroll.resize(width, 700)
            for _ in range(8):
                app.processEvents()
            assert panel._vector_columns == columns
            assert panel.vector_grid.count() == 2
            assert scroll.horizontalScrollBar().maximum() == 0
            wind = panel.wind_section.geometry()
            current = panel.current_section.geometry()
            if columns == 2:
                assert wind.top() == current.top()
                assert wind.right() < current.left()
                assert abs(wind.width()-current.width()) <= 1
            else:
                assert wind.bottom() < current.top()
            assert panel.wind_speed.width() == panel.current_speed.width()
            assert panel.wind_speed.value() == 8 and panel.current_speed.value() == 2
            assert panel.wind_source.currentData() == "open-meteo"
            assert panel.wind_status.text() == "Manual fallback"
            assert panel.wind_status.fontMetrics().horizontalAdvance(panel.wind_status.text()) <= panel.wind_status.width()
        panel.wind_speed_slider.setValue(90)
        service.advance_wall_time(0)
        assert service.snapshot().wind_speed_kn == 9
        service.close()
    finally:
        scroll.close()
        scroll.deleteLater()
        app.processEvents()


def test_wind_current_and_weather_share_the_same_source_choice_labels_and_ids():
    from maritime_simulator.ui.mode_panels import EnvironmentPanel
    app = QApplication.instance() or QApplication([])
    panel = EnvironmentPanel()
    try:
        for source in (panel.wind_source, panel.current_source, panel.weather_source):
            assert [(source.itemText(i), source.itemData(i)) for i in range(source.count())] == [
                ("Manual", "manual"), ("Online - Open-Meteo", "open-meteo"),
            ]
    finally:
        panel.deleteLater()
        app.processEvents()


def test_environment_compacts_inside_the_actual_main_window_sidebar(tmp_path):
    from maritime_simulator.ui.main_window import V2MainWindow
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        window.resize(1400, 900)
        window.control_tabs.setCurrentIndex(1)
        window.show()
        panel = window.environment_controls
        scroll = window.control_tabs.widget(1)
        heights = {}
        for width, height, columns in ((300, 700, 1), (340, 900, 2), (600, 900, 2), (300, 700, 1), (480, 900, 2)):
            window.resize(1400, height)
            window.workspace.setSizes([width, 1400-width])
            for _ in range(10):
                app.processEvents()
            assert panel._vector_columns == columns
            assert scroll.horizontalScrollBar().maximum() == 0
            heights[columns] = panel.minimumSizeHint().height()
            if columns == 2:
                assert scroll.verticalScrollBar().maximum() == 0
            assert panel.xdr_grid.count() == 4
            cells = [panel.xdr_grid.itemAtPosition(row, column).widget() for row in range(2) for column in range(2)]
            assert all(not a.geometry().intersects(b.geometry()) for index, a in enumerate(cells) for b in cells[index+1:])
            assert all(field.width() == panel.wind_speed.width() for field in panel.xdr_fields.values())
            assert panel.feedback.fontMetrics().horizontalAdvance(panel.feedback.text()) <= panel.feedback.width()
        assert heights[2] < heights[1]-180
        assert window.left_panel.maximumWidth() >= 600
        expanded_width = window.left_panel.width()
        window._toggle_left_panel()
        for _ in range(4):
            app.processEvents()
        window._toggle_left_panel()
        for _ in range(8):
            app.processEvents()
        assert abs(window.left_panel.width()-expanded_width) <= 1
        assert panel._vector_columns == 2
        panel.set_weather_source("open-meteo")
        message = "Open-Meteo 2026-10-26 14:00 UTC at 40.7000, -74.0200"
        panel.set_weather_status(message)
        assert panel.weather_status.text() == "Open-Meteo"
        assert panel.weather_status.toolTip() == message
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_compact_environment_dial_keeps_labels_clear_and_pointer_mapping_correct():
    from PySide6.QtCore import QPointF
    from maritime_simulator.ui.controls import CompassDial
    app = QApplication.instance() or QApplication([])
    compact = CompassDial("TRUE WIND FROM", compact=True)
    regular = CompassDial("HDG")
    try:
        compact.resize(132, 136)
        center, radius = compact._dial_geometry()
        assert center.y()-radius >= 22
        assert center.y()+radius <= compact.height()-28
        compact._set_from_position(QPointF(center.x()+radius*.7, center.y()))
        assert compact.value() == pytest.approx(90)
        compact._set_from_position(QPointF(center.x(), center.y()-radius*.7))
        assert compact.value() == pytest.approx(0)
        assert regular.minimumHeight() == 150
    finally:
        compact.deleteLater()
        regular.deleteLater()
        app.processEvents()


def test_narrow_environment_source_shows_a_short_caption_but_keeps_full_menu_labels():
    from maritime_simulator.ui.mode_panels import EnvironmentPanel
    app = QApplication.instance() or QApplication([])
    panel = EnvironmentPanel()
    try:
        source = panel.wind_source
        source.setCurrentIndex(source.findData("open-meteo"))
        source.resize(132, 34)
        assert source.display_text() == "Online"
        assert source.currentText() == "Online - Open-Meteo"
        assert source.itemText(1) == "Online - Open-Meteo"
        source.resize(320, 34)
        assert source.display_text() == "Online - Open-Meteo"
    finally:
        panel.deleteLater()
        app.processEvents()
