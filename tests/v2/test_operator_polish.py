# SPDX-License-Identifier: MPL-2.0
from dataclasses import replace
import math
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from maritime_simulator.application import metrics
from maritime_simulator.application.ais import AISRepository, _destination, _distance_nm
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.ui.offline_panel import OfflineAreaManager
from maritime_simulator.ui.offline_widgets import MapCellDelegate
from maritime_simulator.ui.performance_panel import PerformancePanel
from maritime_simulator.ui.rudder_endpoint import RudderEndpointEditor
from maritime_simulator.ui.input_runtime import RudderEndpointSettings
from maritime_simulator.ui.startup_profile_panel import _details, _differences
from maritime_simulator.ui.status_strip import StatusStrip


def app():
    return QApplication.instance() or QApplication([])


def test_recent_metrics_recover_and_high_network_sentence_rate_is_not_capacity(monkeypatch):
    app()
    now = [100.0]
    monkeypatch.setattr(metrics, "time", SimpleNamespace(perf_counter=lambda: now[0]))
    monitor = metrics.PerformanceMonitor(True)
    panel = PerformancePanel(monitor)
    for index in range(200):
        now[0] = 100.0 + (index + 1) / 20
        monitor.record_duration("ui_refresh", .001)
        monitor.record_duration("output_pump", .001)
        monitor.increment("nmea_sentences", 100)
    monitor.set_gauge("ais_output_targets", 5000)
    panel.refresh()
    assert monitor.snapshot()["recent"]["rates"]["nmea_sentences"] >= 1900
    assert panel.metric_bars["output"].load() < 70
    assert panel.metric_bars["ais"].load() == 0
    assert panel.metric_bars["memory"].load() == 0
    assert "normal" in panel.overall_status.text()
    monitor.record_duration("output_pump", .150)
    monitor.increment("network_send_errors")
    panel.refresh()
    assert panel.metric_bars["output"].load() == 100
    assert panel.metric_bars["errors"].load() >= 70
    now[0] += 12
    panel.refresh()
    assert panel.metric_bars["output"].load() == 0
    assert panel.metric_bars["errors"].load() == 0
    assert monitor.snapshot()["counters"]["network_send_errors"] == 1
    panel.deleteLater()


def test_serial_health_is_separate_from_udp_tcp_rate():
    app()
    monitor = metrics.PerformanceMonitor(True)
    monitor.increment("nmea_sentences", 2000)
    panel = PerformancePanel(monitor, serial_loads=lambda _snapshot: {"Serial fixture 4800": 96})
    panel.refresh()
    assert panel.metric_bars["serial"].load() == 96
    assert panel.metric_bars["output"].load() == 0
    assert "Serial fixture" in panel.metric_bars["serial"].toolTip()
    panel.deleteLater()


def test_high_frequency_metrics_keep_the_whole_recent_window_bounded(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(metrics, "time", SimpleNamespace(perf_counter=lambda: now[0]))
    monitor = metrics.PerformanceMonitor(True)
    for index in range(10000):
        now[0] = 100.0 + (index + 1) / 1000
        monitor.record_duration("output_pump", .0005)
    recent = monitor.snapshot()["recent"]["durations"]["output_pump"]
    assert recent["count"] == 10000
    assert math.isclose(recent["busy_percent"], 50)
    assert len(monitor._recent_durations["output_pump"]) <= 16


def test_rudder_transport_pages_retain_values_and_hide_irrelevant_fields():
    app()
    editor = RudderEndpointEditor()
    endpoint = RudderEndpointSettings(host="127.0.0.1", port=14002, baud=4800, serial_device="/dev/fixture")
    changes = []
    editor.changed.connect(lambda: changes.append(editor.values()))
    editor.load(endpoint)
    assert not changes
    editor.tabs.setCurrentIndex(1)
    assert changes[-1]["transport"] == "TCP"
    assert changes[-1]["port"] == 14002
    assert "0.0.0.0" in editor.fields["UDP"]["host"].toolTip()
    assert "simulator computer" in editor.fields["TCP"]["host"].toolTip()
    editor.fields["TCP"]["tcp_server"].setChecked(False)
    assert editor.labels["TCP"]["host"].text() == "Remote host"
    assert "server's network IP" in editor.fields["TCP"]["host"].toolTip()
    assert changes[-1]["serial_device"] == "/dev/fixture"
    editor.tabs.setCurrentIndex(2)
    assert changes[-1]["transport"] == "Serial"
    assert changes[-1]["baud"] == 4800
    assert set(editor.fields["Serial"]) == {"serial_device", "baud", "parity", "stop_bits"}
    editor.deleteLater()


def test_startup_tooltips_are_bounded_differences_hide_credentials():
    payload = {"app_state": {"simulation": {"sensor_values": {f"sensor_{i}": i for i in range(200)}}}}
    text = _details("sensors", payload)
    assert len(text.splitlines()) <= 9
    assert "other saved values" in text
    current = {"external_rudder_input": {"input_type": "RSA", "secret": "hidden", "api_key": "hidden"}}
    saved = {"external_rudder_input": {"input_type": "ROR", "secret": "different", "api_key": "different"}}
    differences = _differences(current, saved)
    assert "ROR → RSA" in differences
    assert "hidden" not in differences and "different" not in differences


def test_track_readout_animation_redistributes_without_overlap():
    app()
    strip = StatusStrip()
    strip.resize(1000, 64)
    cells = []
    for index in range(9):
        cell = QWidget()
        layout = QVBoxLayout(cell)
        layout.addWidget(QLabel("TRACK" if index == 7 else "POSITION"))
        strip.add_cell(cell, track_only=index == 7)
        cells.append(cell)
    strip.show()
    QTest.qWait(20)
    assert not cells[7].isVisible()
    before = cells[-1].geometry()
    strip.set_track_visible(True)
    QTest.qWait(80)
    assert 0 < strip._fraction < 1
    for a, b in zip(cells, cells[1:]):
        assert a.geometry().right() < b.geometry().left()
    QTest.qWait(160)
    assert cells[7].isVisible() and cells[-1].geometry() != before
    strip.set_track_visible(False)
    QTest.qWait(220)
    assert not cells[7].isVisible()
    assert cells[-1].geometry() == before
    strip.close()


def test_protomaps_uses_s57_delegate_and_real_click_targets_selected_row(tmp_path):
    app()
    manager = OfflineAreaManager(tmp_path)
    manager.queue_areas([{"bounds": [9.9,53.5,9.91,53.51], "max_zoom":13},
                         {"bounds": [10.0,53.6,10.01,53.61], "max_zoom":13}])
    assert isinstance(manager.table.itemDelegate(), MapCellDelegate)
    manager.resize(1400, 600)
    manager.show()
    manager.table.scrollToItem(manager.table.item(1, 7))
    app().processEvents()
    QTest.mouseClick(manager.table.viewport(), Qt.LeftButton,
                     pos=manager.table.visualItemRect(manager.table.item(1, 7)).center())
    assert len(manager.pending) == 1
    assert manager.pending[0]["bounds"][0] == 9.9
    assert manager.table.item(0,5).data(Qt.UserRole) == 0
    manager.shutdown()
    manager.close()


def test_generated_targets_do_not_pile_up_on_one_warm_water_sample():
    service = SimulatorService()
    own = service.snapshot()
    sample = _destination(own.latitude_deg, own.longitude_deg, 90, 1)
    repository = AISRepository(generated_target_count=20, water_checker=lambda *_: True,
                               water_sampler=lambda *_: sample, random_seed=42)
    repository.use_generated(own)
    targets = repository.all_targets()
    assert len(targets) == 20
    for index, target in enumerate(targets):
        for other in targets[index + 1:]:
            assert _distance_nm(target.latitude_deg,target.longitude_deg,other.latitude_deg,other.longitude_deg) >= max(.04,(target.length_m+other.length_m)/(2*1852))
    service.close()


def test_generated_hull_check_rejects_center_water_but_bow_on_land():
    service = SimulatorService()
    own = service.snapshot()
    repository = AISRepository(generated_target_count=1, avoid_land=False, random_seed=3)
    repository.use_generated(own)
    target = replace(repository.all_targets()[0], latitude_deg=0, longitude_deg=0, heading_deg=0, length_m=200)
    repository.configure(avoid_land=True)
    repository._water_checker = lambda latitude, _longitude: latitude < .0005
    assert repository._target_is_on_water(target) is True
    assert not repository._hull_is_on_water(target)
    service.close()
