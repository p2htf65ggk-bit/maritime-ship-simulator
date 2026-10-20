# SPDX-License-Identifier: MPL-2.0
import itertools
import os
import socket
import time
from dataclasses import asdict

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtWidgets import QApplication
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import (
    SetControlMode, SetExternalRudderInputType, ExternalRudderOrder, ExternalRudderResponse,
    SetExternalActuatorPolicy, ExternalActuatorOrder, SetRudderOrder, SetSpeedOrder, SetBowThrusterOrder,
)
from maritime_simulator.domain.enums import ControlMode, ExternalRudderControlPolicy
from maritime_simulator.domain.external_control import RUDDER_SOURCES, source_policy
from maritime_simulator.protocols.external_control import decode_xdr_rudder
from maritime_simulator.protocols.iec61162 import IEC61162Codec, IEC61162Error
from maritime_simulator.ui.input_runtime import InputRuntime
from maritime_simulator.ui.control_sources import ControlSourceSelector


def rudder_command(protocol, angle, now):
    command = ExternalRudderResponse if protocol in {"RSA", "XDR_RESPONSE"} else ExternalRudderOrder
    return command(angle, None, now, protocol=protocol)


@pytest.mark.parametrize("main,fallback", itertools.permutations(RUDDER_SOURCES, 2))
def test_every_rudder_priority_pair_falls_back_and_recovers(main, fallback):
    now = [100.0]
    service = SimulatorService(monotonic=lambda: now[0])
    try:
        policy = source_policy(main, fallback, RUDDER_SOURCES)
        assert ExternalRudderControlPolicy.parse(policy).sources == (main, fallback)
        service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
        service.submit(SetExternalRudderInputType(policy))
        service.submit(rudder_command(main, 12, now[0]))
        service.advance_wall_time(.1)
        assert service.snapshot().external_rudder_active_source == main
        now[0] += 2.6
        service.submit(rudder_command(fallback, -8, now[0]))
        service.advance_wall_time(.1)
        assert service.snapshot().external_rudder_active_source == fallback
        service.submit(rudder_command(main, 7, now[0]))
        service.advance_wall_time(.1)
        snapshot = service.snapshot()
        assert snapshot.external_rudder_active_source == main
        if main in {"RSA", "XDR_RESPONSE"}:
            assert snapshot.rudder_response_starboard_deg == 7
        else:
            assert snapshot.rudder_order_starboard_deg == 7
    finally:
        service.close()


@pytest.mark.parametrize("loss", ["MANUAL", "FREEZE", "ZERO"])
@pytest.mark.parametrize("protocol", ["ROR", "XDR_ORDER", "RSA", "XDR_RESPONSE"])
def test_rudder_input_loss_ownership_and_physical_response(loss, protocol):
    now = [100.0]
    service = SimulatorService(monotonic=lambda: now[0])
    try:
        service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
        service.submit(SetExternalRudderInputType(protocol, on_loss=loss))
        service.submit(rudder_command(protocol, 12, now[0]))
        service.advance_wall_time(.5)
        assert not service.snapshot().external_rudder_manual_enabled
        now[0] += 2.6
        service.advance_wall_time(.1)
        snapshot = service.snapshot()
        assert not snapshot.external_rudder_active_source
        assert snapshot.external_rudder_manual_enabled == (loss == "MANUAL")
        if protocol in {"RSA", "XDR_RESPONSE"}:
            if loss == "FREEZE":
                assert snapshot.rudder_response_starboard_deg == 12
            else:
                assert snapshot.rudder_response_starboard_deg < 12
        else:
            assert snapshot.rudder_order_starboard_deg == (12 if loss == "FREEZE" else 0)
        before = snapshot.rudder_order_starboard_deg
        service.submit(SetRudderOrder(-5))
        service.advance_wall_time(.1)
        assert service.snapshot().rudder_order_starboard_deg == (-5 if loss == "MANUAL" else before)
    finally:
        service.close()


@pytest.mark.parametrize("actuator,protocol", [("engine", "PRC"), ("bow_thruster", "TRC")])
@pytest.mark.parametrize("loss", ["MANUAL", "FREEZE", "ZERO"])
def test_propulsion_loss_modes_and_manual_lock(actuator, protocol, loss):
    now = [100.0]
    service = SimulatorService(monotonic=lambda: now[0])
    try:
        service.submit(SetControlMode(ControlMode.SHIP))
        service.submit(SetExternalActuatorPolicy(actuator, protocol, on_loss=loss))
        service.submit(ExternalActuatorOrder(actuator, protocol, 60, now[0]))
        service.advance_wall_time(.1)
        before = service.snapshot()
        now[0] += 2.6
        service.advance_wall_time(.1)
        snapshot = service.snapshot()
        if actuator == "engine":
            expected = 0 if loss == "ZERO" else before.speed_order_kn
            assert snapshot.speed_order_kn == expected
            assert snapshot.engine_manual_enabled == (loss == "MANUAL")
            service.submit(SetSpeedOrder(-2))
            service.advance_wall_time(.1)
            assert service.snapshot().speed_order_kn == (-2 if loss == "MANUAL" else expected)
        else:
            expected = 60 if loss == "FREEZE" else 0
            assert snapshot.bow_thruster_order_percent == expected
            assert snapshot.bow_thruster_manual_enabled == (loss == "MANUAL")
            service.submit(SetBowThrusterOrder(-20))
            service.advance_wall_time(.1)
            assert service.snapshot().bow_thruster_order_percent == (-20 if loss == "MANUAL" else expected)
    finally:
        service.close()


@pytest.mark.parametrize("type_code,unit,value,expected", [("A", "D", 10, 10), ("A", "P", -50, -17.5), ("G", "P", 100, 35)])
def test_rudder_xdr_uses_explicit_units_and_coherent_dual_groups(type_code, unit, value, expected):
    sentence = IEC61162Codec.decode(IEC61162Codec.frame(
        f"GPXDR,C,12,C,Air#0,{type_code},{value},{unit},Order#0,{type_code},{-value},{unit},Order#1"))
    assert decode_xdr_rudder(sentence, type_code=type_code, unit=unit, identifier="Order#0", port_id="Order#1",
                             max_rudder_deg=35) == pytest.approx((expected, -expected))


@pytest.mark.parametrize("payload", [
    "GPXDR,A,nan,D,Rudder#0", "GPXDR,A,36,D,Rudder#0", "GPXDR,A,1,D,Rudder#0,A,2,D,Rudder#0",
    "GPXDR,A,1,D,Rudder#0,A,2", "GPXDR,A,,D,Rudder#0",
])
def test_bad_rudder_xdr_is_rejected(payload):
    with pytest.raises((ValueError, IEC61162Error)):
        decode_xdr_rudder(IEC61162Codec.decode(IEC61162Codec.frame(payload)),
                         type_code="A", unit="D", identifier="Rudder#0", max_rudder_deg=35)


def test_shared_rudder_xdr_runtime_and_settings_roundtrip(tmp_path):
    app = QApplication.instance() or QApplication([])
    now = [100.0]
    service = SimulatorService(monotonic=lambda: now[0])
    runtime = InputRuntime(service, tmp_path / "inputs.json", monotonic=lambda: now[0])
    runtime._health_timer.stop()
    runtime.stop()
    try:
        endpoint = {"host": "127.0.0.1", "port": 43123}
        runtime.update(input_type="PRIO_XDR_ORDER_XDR_RESPONSE", on_loss="FREEZE",
                       xdr_order={"endpoint": endpoint}, xdr_response={"endpoint": endpoint},
                       engine={"policy": "XDR", "on_loss": "ZERO"})
        assert asdict(runtime._load()) == asdict(runtime.settings)
        service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
        source = "external-xdr-order+external-xdr-response"
        runtime._on_sentence(source, IEC61162Codec.frame("GPXDR,A,15,D,RudderOrder#0,A,8,D,Rudder#0"))
        service.advance_wall_time(.1)
        assert service.snapshot().rudder_order_starboard_deg == 15
        assert service.snapshot().external_rudder_active_source == "XDR_ORDER"
        now[0] += 2.6
        runtime._on_sentence(source, IEC61162Codec.frame("GPXDR,A,6,D,Rudder#0"))
        service.advance_wall_time(.1)
        assert service.snapshot().external_rudder_active_source == "XDR_RESPONSE"
        assert service.snapshot().rudder_response_starboard_deg == 6
        saved = asdict(runtime.settings)
        with pytest.raises(ValueError, match="must differ"):
            runtime.update(xdr_response={"xdr_id": "RudderOrder#0"})
        assert asdict(runtime.settings) == saved
        with pytest.raises(ValueError):
            runtime.update(on_loss="invalid")
        assert asdict(runtime.settings) == saved
    finally:
        runtime.close()
        service.close()
        app.processEvents()


def test_source_selector_prevents_duplicate_sources_and_exposes_loss_modes():
    app = QApplication.instance() or QApplication([])
    selector = ControlSourceSelector(RUDDER_SOURCES)
    try:
        selector.load("PRIO_RSA_XDR_ORDER", "FREEZE")
        assert selector.values() == ("PRIO_RSA_XDR_ORDER", "FREEZE")
        assert not selector.buttons["fallback"]["RSA"].isEnabled()
        selector.buttons["main"]["XDR_ORDER"].click()
        assert selector.values() == ("XDR_ORDER", "FREEZE")
        selector.buttons["fallback"]["ROR"].click()
        assert selector.values()[0] == "PRIO_XDR_ORDER_ROR"
        selector.buttons["loss"]["ZERO"].click()
        assert selector.values()[1] == "ZERO"
        selector.buttons["main"]["MANUAL"].click()
        assert selector.values()[0] == "MANUAL"
        assert all(not b.isEnabled() for b in selector.buttons["fallback"].values())
    finally:
        selector.deleteLater()
        app.processEvents()


def test_freeze_ignores_unselected_response_reports():
    now = [100.0]
    service = SimulatorService(monotonic=lambda: now[0])
    try:
        service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
        service.submit(SetExternalRudderInputType("RSA", on_loss="FREEZE"))
        service.submit(rudder_command("RSA", 12, now[0]))
        service.advance_wall_time(.1)
        now[0] += 3
        service.submit(rudder_command("XDR_RESPONSE", -20, now[0]))
        service.advance_wall_time(.1)
        assert service.snapshot().rudder_response_starboard_deg == 12
    finally:
        service.close()


@pytest.mark.parametrize("transport", ["UDP", "TCP"])
@pytest.mark.parametrize("protocol,identifier", [("XDR_ORDER", "RudderOrder#0"), ("XDR_RESPONSE", "Rudder#0")])
def test_rudder_xdr_over_real_transport(tmp_path, transport, protocol, identifier):
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    runtime = InputRuntime(service, tmp_path / "input.json")
    runtime._health_timer.stop()
    runtime.stop()
    connection = None
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    endpoint = {"transport": transport, "host": "127.0.0.1", "port": port}
    try:
        runtime.update(input_type=protocol, ror=endpoint, rsa=endpoint,
                       xdr_order={"endpoint": endpoint}, xdr_response={"endpoint": endpoint},
                       bow_thruster={"primary": endpoint, "xdr": endpoint},
                       engine={"primary": endpoint, "xdr": endpoint})
        runtime.start()
        service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
        frame = (IEC61162Codec.frame(f"GPXDR,A,-12,D,{identifier}") + "\r\n").encode("ascii")
        if transport == "UDP":
            deadline = time.monotonic() + 2
            while not any(status.connected for status in runtime.statuses()) and time.monotonic() < deadline:
                QTest.qWait(10)
            assert any(status.connected for status in runtime.statuses()), runtime.statuses()
            connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            connection.sendto(frame, ("127.0.0.1", port))
        else:
            deadline = time.monotonic() + 2
            while connection is None:
                try:
                    connection = socket.create_connection(("127.0.0.1", port), timeout=.1)
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    QTest.qWait(10)
            connection.sendall(frame[:16])
            connection.sendall(frame[16:])
        deadline = time.monotonic() + 2
        while not runtime.events() and time.monotonic() < deadline:
            QTest.qWait(10)
        assert runtime.events(), runtime.statuses()
        assert runtime.events()[-1].accepted_for_control
        service.advance_wall_time(.1)
        snapshot = service.snapshot()
        assert snapshot.external_rudder_active_source == protocol
        assert (snapshot.rudder_order_starboard_deg if protocol == "XDR_ORDER"
                else snapshot.rudder_response_starboard_deg) == -12
    finally:
        if connection is not None:
            connection.close()
        runtime.close()
        service.close()
        app.processEvents()
