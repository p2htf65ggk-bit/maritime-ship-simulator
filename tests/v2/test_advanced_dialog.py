# SPDX-License-Identifier: MPL-2.0
import os
import socket
import sys
from threading import Event, RLock, Thread
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.application.ais import AISRepository
from maritime_simulator.application.configuration import ProtocolConfigurationStore
from maritime_simulator.domain.enums import TransportKind, UdpMode
from maritime_simulator.protocols.iec61162 import IEC61162Codec
from maritime_simulator.transport.config import TcpEndpoint, UdpEndpoint
from maritime_simulator.ui.advanced_dialog import AdvancedOutputPortDialog, AdvancedProtocolDialog
from maritime_simulator.ui.output_runtime import OutputRuntime


class _RecordingPumpService:
    def __init__(self, **_kwargs):
        self.pump_times = []
        self.pumped = Event()
        self.output_manager = self

    def register(self, _registration, *, now_s=None):
        return None

    def prepare(self, _endpoint):
        return None

    def release(self, _endpoint):
        return None

    def send(self, _endpoint, _sentence):
        return True

    def pump(self, _snapshot, *, now_s=None):
        self.pump_times.append(time.monotonic() if now_s is None else now_s)
        self.pumped.set()
        return 0

    def next_delay_s(self, *, now_s=None, default=0.05):
        return 0.02

    def close(self):
        return None


class _SlowDeadlinePumpService(_RecordingPumpService):
    def next_delay_s(self, *, now_s=None, default=0.05):
        return 1.0


def _wait_for_pumps(service, count, timeout=3.0):
    # Wait for worker progress without processing Qt events or assuming a VM CPU slice.
    deadline = time.monotonic() + timeout
    while len(service.pump_times) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        service.pumped.wait(remaining)
        service.pumped.clear()
    return tuple(service.pump_times)


def test_advanced_dialog_supports_multiple_protocols_and_free_text_playback():
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    sent = []
    dialog = AdvancedProtocolDialog(
        snapshot=service.snapshot,
        send_sentence=lambda port, sentence: sent.append((port, sentence)),
    )
    try:
        dialog.set_ports(("UDP  127.0.0.1:4096", "TCP  127.0.0.1:4097"))
        first = dialog.protocol_rows[0]
        first.protocol.setCurrentIndex(first.protocol.findData("HDT"))
        first.send_once()
        assert sent and sent[-1][0].startswith("UDP")
        assert sent[-1][1].startswith("$GPHDT,")

        second = dialog.add_protocol()
        second.protocol.setCurrentIndex(second.protocol.findData("ROT"))
        second.port.setCurrentIndex(1)
        assert len(dialog.protocol_rows) == 2

        playback = dialog.add_free_text()
        playback.editor.setPlainText("GPHDT,123.4,T")
        playback.send_once()
        assert sent[-1][1].startswith("$GPHDT,123.4,T*")
        assert len(dialog.free_text_rows) == 1
    finally:
        for row in (*dialog.protocol_rows, *dialog.free_text_rows):
            row.stop()
        service.close()
        dialog.deleteLater()
        app.processEvents()


def test_advanced_xdr_recommended_preset_populates_live_environment_fields():
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    sent = []
    dialog = AdvancedProtocolDialog(
        snapshot=service.snapshot,
        send_sentence=lambda port, sentence: sent.append((port, sentence)),
    )
    try:
        dialog.set_ports(("UDP  127.0.0.1:4096",))
        row = dialog.protocol_rows[0]
        row.protocol.setCurrentIndex(row.protocol.findData("XDR"))
        assert row._xdr_preset is not None
        preset_index = row._xdr_preset.findData("C:C:air")
        assert preset_index >= 0
        row._xdr_preset.setCurrentIndex(preset_index)
        assert row._values() == {
            "type": "C",
            "value": "AUTO",
            "unit": "C",
            "name": "Air#0",
        }
        row.send_once()
        assert sent
        decoded = IEC61162Codec.decode(sent[-1][1])
        assert decoded.formatter == "XDR"
        assert decoded.fields[0] == "C"
        assert decoded.fields[2:] == ("C", "Air#0")
    finally:
        for row in (*dialog.protocol_rows, *dialog.free_text_rows):
            row.stop()
        service.close()
        dialog.deleteLater()
        app.processEvents()


def test_advanced_output_port_supports_tcp_client_and_server_modes():
    app = QApplication.instance() or QApplication([])
    dialog = AdvancedOutputPortDialog()
    try:
        dialog.transport.setCurrentText("TCP")
        dialog.tcp_mode.setCurrentIndex(dialog.tcp_mode.findData(True))
        dialog.host.setText("0.0.0.0")
        dialog.port.setValue(14002)
        assert dialog.host_label.text() == "Bind address"
        assert dialog.settings() == {
            "transport": "TCP",
            "host": "0.0.0.0",
            "port": 14002,
            "server": True,
        }
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_advanced_xdr_generic_supports_null_and_percent_units():
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    sent = []
    dialog = AdvancedProtocolDialog(
        snapshot=service.snapshot,
        send_sentence=lambda port, sentence: sent.append((port, sentence)),
    )
    try:
        dialog.set_ports(("UDP  127.0.0.1:4096",))
        row = dialog.protocol_rows[0]
        row.protocol.setCurrentIndex(row.protocol.findData("XDR"))
        row._fields["type"].setCurrentIndex(row._fields["type"].findData("G"))
        unit = row._fields["unit"]
        assert not unit.isEditable()
        assert [unit.itemData(i) for i in range(unit.count())] == ["", "P"]
        unit.setCurrentIndex(unit.findData("P"))
        row._fields["name"].setText("BowThrust#0")
        row._fields["value"].setText("-25")
        row.send_once()
        assert IEC61162Codec.decode(sent[-1][1]).fields == ("G", "-25", "P", "BowThrust#0")
    finally:
        for row in (*dialog.protocol_rows, *dialog.free_text_rows):
            row.stop()
        service.close()
        dialog.deleteLater()
        app.processEvents()


def test_advanced_cycle_state_survives_dialog_close_and_restore():
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    sent = []
    dialog = AdvancedProtocolDialog(
        snapshot=service.snapshot,
        send_sentence=lambda port, sentence: sent.append((port, sentence)),
    )
    restored = AdvancedProtocolDialog(
        snapshot=service.snapshot,
        send_sentence=lambda port, sentence: sent.append((port, sentence)),
    )
    try:
        dialog.set_ports(("UDP  127.0.0.1:4096",))
        row = dialog.protocol_rows[0]
        row.protocol.setCurrentIndex(row.protocol.findData("HDT"))
        row.cycle.setChecked(True)
        state = dialog.export_state()
        dialog.close()
        assert row.timer.isActive()
        assert state["protocols"][0]["cycle_active"] is True

        restored.apply_state(state)
        restored.set_ports(("UDP  127.0.0.1:4096",))
        assert restored.protocol_rows[0].cycle.isChecked()
        assert restored.protocol_rows[0].timer.isActive()
    finally:
        for owner in (dialog, restored):
            for row in (*owner.protocol_rows, *owner.free_text_rows):
                row.stop()
            owner.deleteLater()
        service.close()
        app.processEvents()


def test_pending_send_feedback_is_safe_when_advanced_row_is_replaced():
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    caught = []
    original_hook = sys.excepthook
    sys.excepthook = lambda *error: caught.append(error)
    dialog = AdvancedProtocolDialog(
        snapshot=service.snapshot,
        send_sentence=lambda _port, _sentence: None,
    )
    try:
        dialog.set_ports(("UDP  127.0.0.1:4096",))
        row = dialog.protocol_rows[0]
        row.protocol.setCurrentIndex(row.protocol.findData("HDT"))
        row.send_once()
        assert row.once.property("feedback") is True

        dialog.apply_state({"protocols": [], "free_text": []})
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        wait = QEventLoop()
        QTimer.singleShot(220, wait.quit)
        wait.exec()
        assert caught == []
    finally:
        sys.excepthook = original_hook
        service.close()
        dialog.deleteLater()
        app.processEvents()


def test_advanced_output_ports_are_persistent_without_sensor_protocol_assignment(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    store = ProtocolConfigurationStore(tmp_path / "protocols.json")
    path = tmp_path / "advanced-ports.json"
    runtime = OutputRuntime(service, AISRepository(), store, path)
    restored = None
    try:
        label = runtime.add_advanced_port({
            "transport": "UDP", "host": "239.192.192.0", "port": 14002,
            "mode": "multicast", "multicast_ttl": 2, "interface_ip": "",
        })
        assert label in runtime.configured_ports()
        restored = OutputRuntime(service, AISRepository(), store, path)
        assert restored.configured_ports() == runtime.configured_ports()
        assert restored.advanced_ports_payload()[0]["port"] == 14002
    finally:
        runtime.close()
        if restored is not None:
            restored.close()
        service.close()
        app.processEvents()


def test_output_scheduler_continues_without_qt_event_processing(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    import maritime_simulator.ui.output_runtime as output_runtime_module

    monkeypatch.setattr(output_runtime_module, "ProtocolOutputService", _RecordingPumpService)
    simulation = SimulatorService()
    runtime = OutputRuntime(
        simulation,
        AISRepository(),
        ProtocolConfigurationStore(tmp_path / "protocols.json"),
    )
    try:
        runtime.start()
        runtime.timer.stop()
        worker_service = runtime._service
        assert isinstance(worker_service, _RecordingPumpService)
        pump_times = _wait_for_pumps(worker_service, 8)
        assert len(pump_times) >= 8
    finally:
        runtime.close()
        simulation.close()


def test_output_scheduler_watchdog_stays_responsive_for_slow_protocol_deadlines(
    tmp_path,
    monkeypatch,
):
    QApplication.instance() or QApplication([])
    import maritime_simulator.ui.output_runtime as output_runtime_module

    monkeypatch.setattr(output_runtime_module, "ProtocolOutputService", _SlowDeadlinePumpService)
    simulation = SimulatorService()
    runtime = OutputRuntime(
        simulation,
        AISRepository(),
        ProtocolConfigurationStore(tmp_path / "protocols.json"),
    )
    try:
        runtime.start()
        runtime.timer.stop()
        worker_service = runtime._service
        assert isinstance(worker_service, _SlowDeadlinePumpService)
        pump_times = _wait_for_pumps(worker_service, 4)
        assert len(pump_times) >= 4
    finally:
        runtime.close()
        simulation.close()


@pytest.mark.parametrize("requested_delay,expected_wait", [(0.02, 0.02), (1.0, 0.05), (-1.0, 0.001)])
def test_output_scheduler_waits_are_bounded_without_wall_clock_assumptions(
    requested_delay, expected_wait, monkeypatch,
):
    import maritime_simulator.ui.output_runtime as module

    simulation = SimulatorService()
    stop = Event()
    waits = []
    service = _RecordingPumpService()
    service.next_delay_s = lambda **_kwargs: requested_delay

    def record_wait(timeout):
        waits.append(timeout)
        if len(waits) == 3:
            stop.set()

    runtime = SimpleNamespace(
        simulation=simulation,
        _worker_stop=stop,
        _worker_wake=SimpleNamespace(wait=record_wait, clear=lambda: None),
        _service_lock=RLock(),
        _service=service,
        _last_pump_s=None,
        _maximum_pump_gap_s=0.0,
        latest_snapshot=simulation.snapshot,
    )
    monkeypatch.setattr(module, "_configure_output_worker_timing", module._WindowsWorkerTiming)
    try:
        OutputRuntime._pump_loop(runtime)
        assert waits == pytest.approx([expected_wait] * 3)
        assert len(service.pump_times) == 3
    finally:
        simulation.close()


def test_output_worker_timing_configuration_is_noop_off_windows():
    import maritime_simulator.ui.output_runtime as output_runtime_module

    timing = output_runtime_module._configure_output_worker_timing("darwin")
    assert timing.timer_period_active is False
    assert timing.priority_elevated is False


def test_output_scheduler_does_not_wait_for_simulation_snapshot_lock(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    import maritime_simulator.ui.output_runtime as output_runtime_module

    monkeypatch.setattr(output_runtime_module, "ProtocolOutputService", _RecordingPumpService)
    simulation = SimulatorService()
    runtime = OutputRuntime(
        simulation,
        AISRepository(),
        ProtocolConfigurationStore(tmp_path / "protocols.json"),
    )

    def unavailable_snapshot():
        raise AssertionError("output worker attempted to enter the simulation snapshot lock")

    monkeypatch.setattr(simulation, "snapshot", unavailable_snapshot)
    try:
        runtime.start()
        runtime.timer.stop()
        worker_service = runtime._service
        assert isinstance(worker_service, _RecordingPumpService)
        assert len(_wait_for_pumps(worker_service, 5)) >= 5
    finally:
        runtime.close()
        simulation.close()


def test_udp_output_timing_continues_while_qt_event_loop_is_blocked(tmp_path):
    QApplication.instance() or QApplication([])
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(0.05)
    receiver_port = receiver.getsockname()[1]
    received_at = []
    receiver_stop = Event()

    def receive_packets():
        while not receiver_stop.is_set():
            try:
                receiver.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            received_at.append(time.monotonic())

    receive_worker = Thread(target=receive_packets, name="udp-timing-receiver", daemon=True)
    receive_worker.start()
    simulation = SimulatorService()
    store = ProtocolConfigurationStore(tmp_path / "protocols.json")
    for sensor in store.sensors:
        store.update_sensor(sensor, output_transport=TransportKind.UDP, enabled=False)
    store.update_protocol(
        "HDT",
        output_transport=TransportKind.UDP,
        enabled=True,
        host="127.0.0.1",
        port=receiver_port,
        rate_hz=20.0,
        udp_mode=UdpMode.UNICAST,
    )
    runtime = OutputRuntime(simulation, AISRepository(), store)
    try:
        runtime.start()
        runtime.timer.stop()
        time.sleep(0.60)
        assert len(received_at) >= 9
        gaps = tuple(b - a for a, b in zip(received_at, received_at[1:]))
        assert gaps
        assert max(gaps) < 0.25
    finally:
        runtime.close()
        simulation.close()
        receiver_stop.set()
        receiver.close()
        receive_worker.join(1.0)


def test_output_runtime_registers_same_ais_protocol_on_udp_and_tcp_independently(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    store = ProtocolConfigurationStore(tmp_path / "protocols.json")
    store.update_protocol(
        "VDM",
        output_transport=TransportKind.TCP,
        enabled=True,
        host="127.0.0.1",
        port=14002,
        rate_hz=2.0,
    )
    runtime = OutputRuntime(service, AISRepository(), store)
    try:
        runtime.start()
        registrations = [
            registration
            for registration in runtime._service._outputs.values()
            if registration.schedule.protocol == "VDM"
        ]
        assert len(registrations) == 2
        assert {type(item.endpoint) for item in registrations} == {UdpEndpoint, TcpEndpoint}
        rates = {type(item.endpoint): item.schedule.rate_hz for item in registrations}
        assert rates == {UdpEndpoint: 1.0, TcpEndpoint: 2.0}
    finally:
        runtime.close()
        service.close()
        app.processEvents()


def test_output_runtime_registers_same_protocol_on_two_udp_paths_independently(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    store = ProtocolConfigurationStore(tmp_path / "protocols.json")
    second_udp = store.add_output_path(TransportKind.UDP)
    store.update_protocol(
        "VDM",
        output_transport=TransportKind.UDP,
        output_path_id=second_udp.path_id,
        enabled=True,
        host="239.192.192.10",
        port=14100,
        rate_hz=2.0,
    )
    runtime = OutputRuntime(service, AISRepository(), store)
    try:
        runtime.start()
        registrations = [
            registration
            for registration in runtime._service._outputs.values()
            if registration.schedule.protocol == "VDM"
        ]
        assert len(registrations) == 2
        assert {item.endpoint for item in registrations} == {
            UdpEndpoint("239.192.192.0", 4096),
            UdpEndpoint("239.192.192.10", 14100),
        }
        assert {item.schedule.rate_hz for item in registrations} == {1.0, 2.0}
        assert len({item.schedule.schedule_id for item in registrations}) == 2
    finally:
        runtime.close()
        service.close()
        app.processEvents()


def test_advanced_protocol_validation_marks_invalid_talker_immediately():
    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    dialog = AdvancedProtocolDialog(snapshot=service.snapshot, send_sentence=lambda *_args: None)
    try:
        row = dialog.protocol_rows[0]
        row.protocol.setCurrentIndex(row.protocol.findData("HDT"))
        row.talker.setText("1")
        assert row.talker.property("validationState") == "invalid"
        assert row.feedback.text().startswith("Invalid:")
        row.talker.setText("GP")
        assert row.talker.property("validationState") == "valid"
    finally:
        for row in dialog.protocol_rows:
            row.stop()
        service.close()
        dialog.deleteLater()
        app.processEvents()
