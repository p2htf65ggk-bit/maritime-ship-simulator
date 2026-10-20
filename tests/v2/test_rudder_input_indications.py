# SPDX-License-Identifier: MPL-2.0
import os
from pathlib import Path
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtWidgets import QApplication

from maritime_simulator.protocols.iec61162 import IEC61162Codec
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.transport.input_manager import RORInputBridge
from maritime_simulator.ui.main_window import V2MainWindow
from maritime_simulator.ui.input_runtime import InputRuntime
from ui.instrument_cluster import ROTDotInstrument
from ui.style import apply_global_ui_style


def _window():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    temporary = tempfile.TemporaryDirectory()
    window = V2MainWindow(config_dir=Path(temporary.name))
    window.timer.stop()
    return app, window, temporary


def test_rot_ring_visual_phase_runs_four_times_faster(monkeypatch):
    app = QApplication.instance() or QApplication([])
    times = iter((100.0, 101.0))
    monkeypatch.setattr("ui.instrument_cluster.time.monotonic", lambda: next(times))
    instrument = ROTDotInstrument()

    instrument.set_value(10.0)

    assert instrument.value == 10.0
    assert instrument.phase == pytest.approx(14.0)
    instrument.deleteLater()
    app.processEvents()


def test_rudder_input_lamps_track_each_protocol_and_side_independently():
    app, window, temporary = _window()
    now = [100.0]
    try:
        window._open_settings()
        dialog = window.settings_dialog
        runtime = window.input_runtime
        assert dialog is not None
        runtime._health_timer.stop()
        runtime.stop()
        runtime._monotonic = lambda: now[0]
        runtime._bridge = RORInputBridge(window.service, input_type="ROR", monotonic=lambda: now[0])

        runtime._on_sentence("test", IEC61162Codec.frame("AGROR,12.0,A,-7.0,A,B,,,,"))
        statuses = {(item.input_type, item.side): item for item in runtime.channel_statuses()}
        assert statuses[("ROR", "PORT")].state == "ok"
        assert statuses[("ROR", "STBD")].state == "ok"
        assert statuses[("RSA", "PORT")].state == "alarm"
        assert statuses[("RSA", "STBD")].state == "alarm"

        now[0] = 105.0
        assert all(
            item.state == "ok"
            for item in runtime.channel_statuses()
            if item.input_type == "ROR"
        )
        now[0] = 105.01
        assert all(
            item.state == "delayed"
            for item in runtime.channel_statuses()
            if item.input_type == "ROR"
        )

        runtime._on_sentence("test", IEC61162Codec.frame("AGRSA,6.0,A,,"))
        dialog._refresh_input_status()
        assert dialog.input_lamps[("ROR", "PORT")].property("signalState") == "delayed"
        assert dialog.input_lamps[("ROR", "STBD")].property("signalState") == "delayed"
        assert dialog.input_lamps[("RSA", "PORT")].property("signalState") == "empty"
        assert dialog.input_lamps[("RSA", "STBD")].property("signalState") == "ok"

        now[0] = 106.0
        runtime._on_sentence("test", "$AGRSA,,V,-0.0,A*52")
        dialog._refresh_input_status()
        assert dialog.input_lamps[("RSA", "STBD")].property("signalState") == "empty"
        assert dialog.input_lamps[("RSA", "PORT")].property("signalState") == "ok"
        assert "field empty" in dialog.input_lamps[("RSA", "STBD")].toolTip()

        now[0] = 136.01
        dialog._refresh_input_status()
        assert all(lamp.property("signalState") == "alarm" for lamp in dialog.input_lamps.values())
    finally:
        window.close()
        window.deleteLater()
        temporary.cleanup()
        app.processEvents()


def test_ror_and_rsa_endpoints_are_independent_and_persisted(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    path = tmp_path / "rudder-input.json"
    runtime = InputRuntime(service, path)
    restored = None
    try:
        runtime._health_timer.stop()
        runtime.stop()
        runtime.update_endpoint("ROR", host="0.0.0.0", port=14002, multicast_group="239.192.192.0")
        runtime.update_endpoint("RSA", host="127.0.0.1", port=15002, multicast_group="")
        runtime.update(input_type="RSA")
        assert runtime.settings.ror.port == 14002
        assert runtime.settings.rsa.port == 15002

        restored = InputRuntime(service, path)
        restored._health_timer.stop()
        restored.stop()
        assert restored.settings.input_type == "RSA"
        assert restored.settings.ror.multicast_group == "239.192.192.0"
        assert restored.settings.rsa.host == "127.0.0.1"
    finally:
        runtime.close()
        if restored is not None:
            restored.close()
        service.close()


@pytest.mark.parametrize("policy", ("ROR", "RSA", "PRIO_RSA_ROR", "PRIO_ROR_RSA"))
def test_external_rudder_control_policy_is_persisted(tmp_path, policy):
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    path = tmp_path / f"rudder-input-{policy}.json"
    runtime = InputRuntime(service, path)
    restored = None
    try:
        runtime._health_timer.stop()
        runtime.stop()
        runtime.update(input_type=policy)
        restored = InputRuntime(service, path)
        restored._health_timer.stop()
        restored.stop()
        assert restored.settings.input_type == policy
    finally:
        runtime.close()
        if restored is not None:
            restored.close()
        service.close()
        app.processEvents()
