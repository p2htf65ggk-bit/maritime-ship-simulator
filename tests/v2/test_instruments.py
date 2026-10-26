# SPDX-License-Identifier: MPL-2.0
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.ui.instruments import SnapshotInstrumentCluster
from ui.instrument_cluster import ACCENT, BowThrusterInstrument


def test_instrument_cluster_is_a_pure_snapshot_consumer():
    app = QApplication.instance() or QApplication([])
    cluster = SnapshotInstrumentCluster()
    service = SimulatorService()
    before = service.snapshot()
    cluster.update_snapshot(before)
    after = service.snapshot()
    assert before == after
    assert "ORD" in cluster._values["rudder"].text()
    assert "dual_rudder" in cluster.instruments
    assert "docking_speed" in cluster.instruments
    cluster.remove_instrument("docking_speed")
    assert "docking_speed" not in cluster.items
    cluster.add_instrument("docking_speed")
    assert "docking_speed" in cluster.items
    cluster.timer.stop()
    cluster.deleteLater()
    app.processEvents()


def test_bow_instrument_uses_manual_sensor_values_in_sensor_mode():
    app = QApplication.instance() or QApplication([])
    cluster = SnapshotInstrumentCluster()
    service = SimulatorService()
    snapshot = replace(service.snapshot(), thruster_rpm_demand_percent=40, thruster_rpm_response_percent=25)
    cluster.update_snapshot(snapshot)
    cluster.refresh()
    assert cluster.instruments["bow_thruster"].order == 40
    assert cluster.instruments["bow_thruster"].response == 25
    cluster.remove_instrument("bow_thruster")
    state = cluster.export_state()
    cluster.apply_state(state)
    assert "bow_thruster" not in cluster.items
    cluster.apply_state({"items": ["hdg", "engine"]})
    assert cluster.items == ["hdg", "engine", "bow_thruster"]
    cluster.apply_state({"items": ["hdg", "engine", "thruster", "thruster_response"]})
    assert cluster.items == ["hdg", "engine", "bow_thruster"]
    assert "thruster" not in cluster.tiles
    assert "thruster_response" not in cluster.tiles
    for key, instrument in cluster.instruments.items():
        assert not instrument.title.isupper(), key
    cluster.timer.stop()
    service.close()
    cluster.deleteLater()
    app.processEvents()


def test_bow_instrument_fill_is_blue_in_both_directions():
    app = QApplication.instance() or QApplication([])
    instrument = BowThrusterInstrument()
    instrument.resize(240, 200)
    instrument.show()
    try:
        for response in (-100, -50, 0, 50, 100):
            instrument.set_values(response, response, 1.0)
            app.processEvents()
            image = instrument.grab().toImage()
            ratio = image.devicePixelRatio()

            def pixel(x, y):
                return image.pixelColor(round(x * ratio), round(y * ratio)).name()

            sign = -1 if response < 0 else 1
            if response:
                assert pixel(120 + sign * 20, 104) == ACCENT.name()
                assert pixel(120 - sign * 20, 104) == "#0b151e"
            else:
                assert pixel(100, 104) == pixel(140, 104) == "#0b151e"
            order_x = 120 + 96 * response / 100
            assert pixel(order_x, 87) == "#ffcc66"
    finally:
        instrument.close()
        instrument.deleteLater()
        app.processEvents()
