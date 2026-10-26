# SPDX-License-Identifier: MPL-2.0
from concurrent.futures import Future
from contextlib import closing
import json
import os
import time
import math
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkReply
from shapely import to_wkb
from shapely.geometry import Point, box

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import (
    SelectVesselProfile, SetChartDepth, SetDepthSource, SetEnvironment, SetSensorNoise,
)
from maritime_simulator.protocols.sensors import SensorProjector
from maritime_simulator.transport.chart_depth import ChartDepthClient
from maritime_simulator.visual.bathymetry import (
    S57DepthProvider, gebco_url, grid_corners, grid_depth, grid_position, parse_gebco,
)
from maritime_simulator.visual.s57 import S57Store


def apply(service, *commands):
    for command in commands:
        service.submit(command)
    service.advance_wall_time(0)
    return service.snapshot()


def test_chart_depth_subtracts_draft_once_and_holds_last_value_on_fallback():
    service = SimulatorService()
    apply(service, SetEnvironment(depth_m=99), SetDepthSource("chart"))
    snapshot = apply(service, SetChartDepth(20, "gebco"))
    assert snapshot.depth_m == pytest.approx(18.2)
    assert snapshot.manual_depth_m == pytest.approx(18.2)
    snapshot = apply(service, SetChartDepth(None))
    assert snapshot.depth_m == pytest.approx(18.2)
    assert snapshot.manual_depth_m == snapshot.depth_m
    assert snapshot.depth_source == "chart" and snapshot.active_depth_source == "manual"
    snapshot = apply(service, SetChartDepth(30, "s57"))
    assert snapshot.depth_m == pytest.approx(28.2)
    snapshot = apply(service, SelectVesselProfile("vlcc"))
    assert snapshot.depth_m == pytest.approx(9.5)
    snapshot = apply(service, SetDepthSource("manual"), SetChartDepth(100, "gebco"))
    assert snapshot.depth_m == pytest.approx(9.5)
    assert snapshot.manual_depth_m == snapshot.depth_m


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), 13000])
def test_invalid_chart_depth_holds_previous_value_without_crash(value):
    service = SimulatorService()
    apply(service, SetDepthSource("chart"), SetChartDepth(2, "s57"))
    snapshot = apply(service, SetChartDepth(value, "s57"))
    assert snapshot.depth_m == pytest.approx(.2)
    assert snapshot.active_depth_source == "manual"


def test_noise_is_applied_after_chart_depth_without_feedback_to_fallback():
    service = SimulatorService()
    snapshot = apply(service, SetDepthSource("chart"), SetChartDepth(20, "s57"),
                     SetSensorNoise("depth", enabled=True, amplitude=2))
    projector = SensorProjector()
    projected = projector.project(snapshot)
    assert abs(projected.depth_m-snapshot.depth_m) <= 2
    fallback = apply(service, SetChartDepth(None))
    assert fallback.manual_depth_m == pytest.approx(18.2)


def test_dbt_and_dpt_share_depth_below_keel_without_second_draft_subtraction():
    from maritime_simulator.protocols.iec61162 import encode_protocol
    service = SimulatorService()
    sensor = SensorProjector().project(apply(service, SetDepthSource("chart"), SetChartDepth(20, "s57")))
    assert "$SDDPT,18.2,0.0*" in encode_protocol("DPT", sensor)
    assert ",18.2,M," in encode_protocol("DBT", sensor)


@pytest.mark.parametrize("latitude,longitude", [(40.7, -74.0), (90, 180), (-90, -180), (0, 179.999)])
def test_gebco_grid_is_bounded_and_weights_sum_to_one(latitude, longitude):
    corners = grid_corners(latitude, longitude)
    assert sum(weight for _, weight in corners) == pytest.approx(1)
    assert all(0 <= x < 86400 and 0 <= y < 43200 for (x, y), _ in corners)
    assert grid_depth(corners, {key: -20 for key, _ in corners}) == pytest.approx(20)
    assert grid_depth(corners, {key: 10 for key, _ in corners}) is None


def test_gebco_parser_uses_numeric_values_and_checks_position_and_nodata():
    key = grid_corners(40.7, -74)[0][0]
    lat, lon = grid_position(key)
    payload = f"<root><x>{lon}</x><y>{lat}</y><value_list>-16</value_list></root>".encode()
    assert parse_gebco(payload, key) == 16 * -1
    assert "GetFeatureInfo" in gebco_url(key) and "api_key" not in gebco_url(key)
    query = parse_qs(urlsplit(gebco_url(key)).query)
    assert query["WIDTH"] == query["HEIGHT"] == ["3"]
    assert query["I"] == query["J"] == ["1"]
    with pytest.raises(ValueError):
        parse_gebco(payload.replace(b"-16", b"-32768"), key)
    with pytest.raises(ValueError):
        parse_gebco(payload, (0, 0))


def add_feature(store, code, scale, layer, geometry, **properties):
    with closing(store.connect(write=True)) as db, db:
        db.execute("INSERT OR IGNORE INTO charts VALUES(?,?,?,?,?,?,?,?,1)",
                   (code, code, "test", "1", "0", "20261026", scale,
                    json.dumps((-74.02, 40.68, -73.98, 40.72))))
        cursor = db.execute("INSERT INTO features(chart,layer,geometry,properties) VALUES(?,?,?,?)",
                            (code, layer, to_wkb(geometry), json.dumps(properties)))
        west, south, east, north = geometry.bounds
        db.execute("INSERT INTO extent VALUES(?,?,?,?,?)", (cursor.lastrowid, west, east, south, north))


def test_s57_prefers_detail_cells_and_interpolates_soundings_without_portrayal(tmp_path):
    store = S57Store(tmp_path)
    area = box(-74.02, 40.68, -73.98, 40.72)
    add_feature(store, "COARSE", 100000, "DEPARE", area, DRVAL1=2)
    add_feature(store, "FINE", 10000, "DEPARE", area, DRVAL1=10)
    add_feature(store, "FINE", 10000, "SOUNDG", Point(-74.001, 40.7, 12), SCAMIN=1)
    add_feature(store, "FINE", 10000, "SOUNDG", Point(-73.999, 40.7), DEPTH=16, SCAMIN=1)
    provider = S57DepthProvider(store)
    assert provider.depth_at(40.7, -74) == pytest.approx(14)
    assert provider.depth_at(40.7, -74.001) == pytest.approx(12)
    add_feature(store, "FINE", 10000, "LNDARE", box(-74.0001, 40.6999, -73.9999, 40.7001))
    assert provider.depth_at(40.7, -74) is None
    assert provider.depth_at(0, 0) is None


def test_s57_dredged_area_is_water_and_sparse_data_use_area_depth(tmp_path):
    store = S57Store(tmp_path)
    add_feature(store, "FINE", 10000, "DRGARE", box(-74.02, 40.68, -73.98, 40.72), DRVAL1=13)
    assert S57DepthProvider(store).depth_at(40.7, -74) == 13


def test_oversized_s57_geometry_is_rejected_before_loading_its_blob(tmp_path):
    store = S57Store(tmp_path)
    add_feature(store, "FINE", 10000, "DRGARE", box(-74.02, 40.68, -73.98, 40.72), DRVAL1=13)
    with closing(store.connect(write=True)) as db, db:
        db.execute("INSERT INTO chart_coverage VALUES('FINE',zeroblob(9000000))")
    with pytest.raises(ValueError, match="byte budget"):
        S57DepthProvider(store).depth_at(40.7, -74)


def test_client_interpolates_cached_gebco_with_no_network_in_offline_mode(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = ChartDepthClient(S57Store(tmp_path), allow_network=False)
    readings = []
    client.reading_received.connect(readings.append)
    try:
        client.update_position(40.7, -74)
        client.set_enabled(True)
        for key, _ in grid_corners(40.7, -74):
            client._cache[key] = (-16, time.monotonic())
        client.refresh()
        assert readings[-1].water_depth_m == pytest.approx(16)
        assert client._reply is None
        client.set_offline(True)
        assert readings[-1].water_depth_m is None
        assert client._reply is None
        client.update_position(41, -74)
        for _ in range(10):
            client.refresh()
        assert client._future is None  # No local database: no worker or network work.
    finally:
        client.close()


def test_client_discards_old_worker_after_position_jump(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = ChartDepthClient(S57Store(tmp_path), allow_network=False)
    readings = []
    client.reading_received.connect(readings.append)
    try:
        client.update_position(40.7, -74)
        client.offline = True
        client.enabled = True
        future = Future()
        client._future = future
        client._future_info = (client._generation, client._position, (client._position, client._store.cache_revision()), future)
        future.set_result(25)
        client.update_position(53.5, 10)
        client.refresh()
        assert all(item.water_depth_m is None for item in readings)
    finally:
        client.close()


def test_s57_busy_or_invalid_database_is_retried_with_backoff(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = ChartDepthClient(S57Store(tmp_path), allow_network=False)
    try:
        client.update_position(40.7, -74)
        client.offline = client.enabled = True
        future = Future()
        client._future = future
        client._future_info = (client._generation, client._position, (client._position, client._store.cache_revision()), future)
        future.set_exception(ValueError("Work budget exceeded"))
        client.refresh()
        assert client._future is None
        assert client._local_retry_at >= time.monotonic()+29
        client.update_position(40.71, -74)
        client.refresh()
        assert client._future is None
    finally:
        client.close()


def test_environment_ui_and_configuration_keep_source_draft_and_latest_fallback(tmp_path):
    from maritime_simulator.ui.main_window import V2MainWindow
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        window._submit_ui_command(SetDepthSource("chart"))
        snapshot = apply(window.service, SetChartDepth(20, "s57"))
        panel = window.environment_controls
        panel.update_snapshot(snapshot)
        assert panel.depth_source.currentData() == "chart"
        assert not panel.depth.isEnabled() and not panel.depth_slider.isEnabled()
        assert "S57" in panel.depth_status.text()
        payload = window._configuration_payload()
        saved = payload["app_state"]["simulation"]["environment"]
        assert saved["depth_source"] == "chart"
        assert saved["depth_m"] == pytest.approx(18.2)
        fragment = window.startup_profile.group_fragment("environment", payload)
        assert fragment["app_state"]["simulation"]["environment"]["depth_source"] == "chart"
        assert payload["app_state"]["simulation"]["sensor_values"]["depth_m"] == pytest.approx(18.2)
        window._apply_configuration_payload(payload)
        snapshot = window.service.snapshot()
        assert snapshot.depth_source == "chart" and snapshot.depth_m == pytest.approx(18.2)
        assert window.chart_depth.enabled
        panel.update_snapshot(snapshot)
        assert panel.depth.isEnabled()
        assert "fallback" in panel.depth_status.text()
        payload["app_state"]["simulation"]["environment"]["depth_m"] = 25
        payload["app_state"]["simulation"]["sensor_values"]["depth_m"] = 3
        window._apply_configuration_payload(payload)
        assert window.service.snapshot().depth_m == 25
        window._submit_ui_command(SetDepthSource("manual"))
        assert not window.chart_depth.timer.isActive()
        window._vessel_profiles_changed(snapshot.vessel_profile_id)
        assert window.service.snapshot().depth_m == 25
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


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

    def abort(self):
        self.failed = True
        self.finished.emit()

    def complete(self, key, value=-20):
        lat, lon = grid_position(key)
        self.payload = f"<root><x>{lon}</x><y>{lat}</y><value_list>{value}</value_list></root>".encode()
        self.readyRead.emit()
        self.finished.emit()


def fake_network(client):
    from types import SimpleNamespace
    requests = []

    def get(request):
        reply = FakeReply()
        requests.append((request, reply))
        return reply

    client.network = SimpleNamespace(get=get)
    return requests


class DeferredExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, _function, *_args):
        future = Future()
        self.jobs.append(future)
        return future

    def complete(self, value=None, *, error=None):
        future = next(job for job in self.jobs if not job.done())
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(value)

    def shutdown(self, **_kwargs):
        for future in self.jobs:
            future.cancel()


def local_client(tmp_path):
    store = S57Store(tmp_path)
    with closing(store.connect(write=True)):
        pass
    client = ChartDepthClient(store)
    client._executor.shutdown(wait=True)
    executor = DeferredExecutor()
    client._executor = executor
    requests = fake_network(client)
    client.update_position(40.7, -74)
    return client, executor, requests


@pytest.mark.parametrize("offline", [False, True])
def test_s57_is_first_source_and_suppresses_network_requests(tmp_path, offline):
    app = QApplication.instance() or QApplication([])
    client, executor, requests = local_client(tmp_path)
    try:
        client.set_offline(offline)
        client.set_enabled(True)
        assert len(executor.jobs) == 1
        assert not requests
        executor.complete(12)
        client.refresh()
        assert client._last_reading.source == "s57"
        assert client._last_reading.water_depth_m == 12
        for _ in range(20):
            client.refresh()
        assert not requests and len(executor.jobs) == 1
    finally:
        client.close()


def test_s57_missing_depth_falls_back_to_gebco_and_returns_to_s57(tmp_path):
    app = QApplication.instance() or QApplication([])
    client, executor, requests = local_client(tmp_path)
    try:
        client.set_enabled(True)
        executor.complete(None)
        client.refresh()
        assert len(requests) == 1
        for index, (key, _) in enumerate(grid_corners(40.7, -74)):
            requests[index][1].complete(key)
        assert client._last_reading.source == "gebco"
        assert client._last_reading.water_depth_m == pytest.approx(20)
        client._local_read_at -= 31
        client._local_start_at -= 31
        client.refresh()
        assert len(executor.jobs) == 2
        assert client._last_reading.source == "gebco"
        executor.complete(14)
        client.refresh()
        assert client._last_reading.source == "s57"
        assert client._last_reading.water_depth_m == 14
        assert len(requests) == 4
    finally:
        client.close()


def test_s57_recovery_aborts_pending_gebco_and_late_reply_cannot_override_it(tmp_path):
    app = QApplication.instance() or QApplication([])
    client, executor, requests = local_client(tmp_path)
    try:
        client.set_enabled(True)
        executor.complete(None)
        client.refresh()
        reply = requests[-1][1]
        client._local_read_at -= 31
        client._local_start_at -= 31
        client.refresh()
        executor.complete(14)
        client.refresh()
        assert reply.failed
        assert client._reply is None
        assert client._last_reading.source == "s57"
        reply.complete(grid_corners(40.7, -74)[0][0])
        assert client._last_reading.source == "s57"
        assert not client._cache
    finally:
        client.close()


def test_s57_error_allows_gebco_while_local_database_retry_is_paused(tmp_path):
    app = QApplication.instance() or QApplication([])
    client, executor, requests = local_client(tmp_path)
    try:
        client.set_enabled(True)
        executor.complete(error=ValueError("Database busy"))
        client.refresh()
        assert client._local_retry_at > time.monotonic()+29
        assert len(requests) == 1
        assert len(executor.jobs) == 1
    finally:
        client.close()


def test_offline_without_s57_depth_never_uses_even_cached_gebco(tmp_path):
    app = QApplication.instance() or QApplication([])
    client, executor, requests = local_client(tmp_path)
    try:
        for key, _ in grid_corners(40.7, -74):
            client._cache[key] = (-20, time.monotonic())
        client.set_offline(True)
        client.set_enabled(True)
        executor.complete(None)
        client.refresh()
        assert client._last_reading.water_depth_m is None
        assert client._last_reading.source == "manual"
        assert not requests
    finally:
        client.close()


def test_network_queries_are_serial_cache_is_bounded_and_errors_back_off(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = ChartDepthClient(S57Store(tmp_path))
    requests = fake_network(client)
    client.MAX_CACHE_POINTS = 4
    try:
        client.update_position(40.7, -74)
        client.set_enabled(True)
        for _ in range(20):
            client.refresh()
        assert len(requests) == 1
        for index, (key, _) in enumerate(grid_corners(40.7, -74)):
            requests[index][1].complete(key)
        assert client._last_reading.water_depth_m == pytest.approx(20)
        assert len(requests) == 4
        client.update_position(53.5, 10, reset=True)
        client.refresh()
        requests[-1][1].complete(grid_corners(53.5, 10)[0][0])
        assert len(client._cache) == 4
        requests[-1][1].abort()
        assert client._retry_at > time.monotonic()+29
        count = len(requests)
        for _ in range(20):
            client.refresh()
        assert len(requests) == count
        client.set_offline(True)
        assert client._reply is None
        assert len(requests) == count
    finally:
        client.close()


def test_reselecting_chart_source_replays_cached_reading_after_config_import(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = ChartDepthClient(S57Store(tmp_path), allow_network=False)
    readings = []
    client.reading_received.connect(readings.append)
    try:
        client.update_position(40.7, -74)
        for key, _ in grid_corners(40.7, -74):
            client._cache[key] = (-20, time.monotonic())
        client.set_enabled(True)
        assert readings[-1].water_depth_m == pytest.approx(20)
        before = len(readings)
        client.set_enabled(True, force=True)
        assert len(readings) > before
        assert readings[-1].water_depth_m == pytest.approx(20)
    finally:
        client.close()


def test_repeated_unavailable_depth_does_not_emit_commands_for_every_position(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = ChartDepthClient(S57Store(tmp_path), allow_network=False)
    readings = []
    client.reading_received.connect(readings.append)
    try:
        client.update_position(40.7, -74)
        client.set_enabled(True)
        count = len(readings)
        for index in range(100):
            client.update_position(40.7+index*.00001, -74)
            client.refresh()
        assert len(readings) == count
    finally:
        client.close()


def depth_clock(monkeypatch):
    from maritime_simulator.transport import chart_depth
    clock = [1000.0]
    monkeypatch.setattr(chart_depth, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


@pytest.mark.parametrize("scale,interval", [(1, 1), (10, 2), (30, 3), (120, 5)])
def test_depth_sampling_at_accelerated_speed_keeps_up_without_queue_growth(tmp_path, monkeypatch, scale, interval):
    app = QApplication.instance() or QApplication([])
    clock = depth_clock(monkeypatch)
    client, executor, requests = local_client(tmp_path)
    readings = []
    client.reading_received.connect(readings.append)
    try:
        client.update_position(40.7, -74, time_scale=scale, sog_kn=10)
        client.set_offline(True)
        client.set_enabled(True)
        assert client.sample_interval_s == interval
        for tick in range(1, 121):
            elapsed = tick*.25
            clock[0] = 1000+elapsed
            distance = 10*.514444*scale*elapsed
            client.update_position(40.7+distance/111320, -74, time_scale=scale, sog_kn=10)
            if client._future is not None and not client._future.done():
                requested_position = client._future_info[1]
                executor.complete(12+(requested_position[0]-40.7)*111320/1000)
            client.refresh()
            assert client._last_reading.source == "s57"
            assert sum(not job.done() for job in executor.jobs) <= 1
        good = [reading for reading in readings if reading.source == "s57"]
        assert len(good) >= int(30/interval)-1
        assert len(executor.jobs) <= math.ceil(30/interval)+1
        assert good[-1].water_depth_m > good[0].water_depth_m
        assert not requests
    finally:
        client.close()


def test_30x_result_is_not_discarded_after_travelling_more_than_25_metres(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    clock = depth_clock(monkeypatch)
    client, executor, requests = local_client(tmp_path)
    try:
        client.update_position(40.7, -74, time_scale=30, sog_kn=10)
        client.set_enabled(True)
        clock[0] += .25
        client.update_position(40.7+38.6/111320, -74)
        executor.complete(12)
        client.refresh()
        assert client._last_reading.source == "s57"
        assert client._last_reading.water_depth_m == 12
        assert client._last_reading.latitude == 40.7  # Metadata identifies the actual sample point.
        assert not requests
    finally:
        client.close()


def test_accelerated_teleport_invalidates_even_a_nearby_pending_depth_result(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    clock = depth_clock(monkeypatch)
    client, executor, _ = local_client(tmp_path)
    try:
        client.update_position(40.7, -74, time_scale=120, sog_kn=20)
        client.set_enabled(True)
        clock[0] += .25
        client.update_position(40.701, -74, reset=True)
        executor.complete(12)
        client.refresh()
        assert client._last_reading.water_depth_m is None
        assert len(executor.jobs) == 2
        clock[0] += .25
        executor.complete(18)
        client.refresh()
        assert client._last_reading.water_depth_m == 18
    finally:
        client.close()


def test_high_speed_does_not_allow_indefinitely_old_depth_results(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    clock = depth_clock(monkeypatch)
    client, executor, _ = local_client(tmp_path)
    try:
        client.update_position(40.7, -74, time_scale=30, sog_kn=10)
        client.set_enabled(True)
        clock[0] += 6
        client.update_position(40.7+10*.514444*30*6/111320, -74)
        executor.complete(12)
        client.refresh()
        assert client._last_reading.water_depth_m is None
    finally:
        client.close()


def test_gebco_finishes_one_sample_even_when_fast_travel_crosses_several_grid_cells(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    clock = depth_clock(monkeypatch)
    client = ChartDepthClient(S57Store(tmp_path))
    requests = fake_network(client)
    try:
        client.update_position(40.7, -74, time_scale=120, sog_kn=20)
        client.set_enabled(True)
        corners = grid_corners(40.7, -74)
        for index, (key, _) in enumerate(corners):
            elapsed = (index+1)*.3
            clock[0] = 1000+elapsed
            distance = 20*.514444*120*elapsed
            client.update_position(40.7+distance/111320, -74)
            requests[index][1].complete(key)
        assert distance > 1000
        assert client._last_reading.source == "gebco"
        assert client._last_reading.water_depth_m == pytest.approx(20)
        assert client._last_reading.latitude == 40.7
        client.refresh()
        assert len(requests) == 4  # Next batch respects the real-time sampling limit.
        assert client._last_reading.source == "gebco"
        clock[0] = 1005
        latitude = 40.7+20*.514444*120*5/111320
        client.update_position(latitude, -74)
        client.refresh()
        for index, (key, _) in enumerate(grid_corners(latitude, -74)):
            elapsed = 5+(index+1)*.3
            clock[0] = 1000+elapsed
            client.update_position(40.7+20*.514444*120*elapsed/111320, -74)
            requests[4+index][1].complete(key, value=-30)
        assert len(requests) == 8
        assert client._last_reading.water_depth_m == pytest.approx(30)
        assert client._last_reading.latitude == latitude
    finally:
        client.close()


def test_depth_receives_motion_context_and_resets_for_ui_position_changes(tmp_path):
    from maritime_simulator.ui.main_window import V2MainWindow
    from maritime_simulator.domain.commands import SetTimeScale, SetSensorValues, SetPosition
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        window._submit_ui_command(SetSensorValues(sog_kn=10))
        window._submit_ui_command(SetTimeScale(30))
        window._submit_ui_command(SetDepthSource("chart"))
        assert window.chart_depth.sample_interval_s == 3
        assert window.chart_depth._speed_mps == pytest.approx(10*.514444*30)
        before = window.chart_depth._generation
        window._submit_ui_command(SetPosition(40.7, -74))
        assert window.chart_depth._generation > before
        before = window.chart_depth._generation
        window._submit_ui_command(SetSensorValues(latitude_deg=40.701))
        assert window.chart_depth._generation > before
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
