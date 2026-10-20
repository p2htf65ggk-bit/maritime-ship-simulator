# SPDX-License-Identifier: MPL-2.0
import math
import os
import socket
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import (
    SetControlMode, SetBowThrusterOrder, SetSpeedOrder, SetExternalActuatorPolicy,
    ExternalActuatorOrder, SetExternalRudderInputType, SetRudderOrder,
)
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.domain.profiles import VesselProfileStore
from maritime_simulator.domain.state import SimulationState
from maritime_simulator.protocols.iec61162 import IEC61162Codec, IEC61162Error
from maritime_simulator.protocols.external_control import decode_actuator_percent, validate_xdr_filter
from maritime_simulator.simulation.dynamics import VesselDynamics


def _decode(payload, actuator="bow_thruster", **kwargs):
    return decode_actuator_percent(IEC61162Codec.decode(IEC61162Codec.frame(payload)), actuator=actuator, **kwargs)


def test_trc_rpm_and_prc_lever_and_xdr_fields():
    assert _decode("HCTRC,1,-40,P,0,P,,B,R") == -40
    assert _decode("HCTRC,1,150,R,0,P,,B,C", rated_rpm=300) == 50
    assert _decode("HCTRC,2,40,P,0,P,,B,R") is None
    assert _decode("GPPRC,-32,A,0,P,0,P,B,1", "engine") == -32
    assert _decode("GPPRC,32,A,0,P,0,P,B,0", "engine") is None
    assert _decode("GPXDR,C,10,C,Air#0,A,45,P,BowThrust#0") == 45
    assert _decode("GPXDR,A,-15,P,EngTele#0", "engine", xdr_id="EngTele#0") == -15
    assert _decode("GPXDR,A,50,P,Other#0") is None


@pytest.mark.parametrize("type_code", ["A", "G"])
@pytest.mark.parametrize("value", [-100, -45.5, 0, 100])
def test_supported_xdr_percent_formats_and_boundaries(type_code, value):
    assert _decode(f"GPXDR,{type_code},{value},P,BowThrust#0", xdr_type=type_code) == value
    other = "G" if type_code == "A" else "A"
    assert _decode(f"GPXDR,{other},{value},P,BowThrust#0", xdr_type=type_code) is None


@pytest.mark.parametrize("type_code,unit", [("A", "D"), ("C", "C"), ("H", "P"), ("T", "R"), ("G", ""), ("G", "C")])
def test_external_xdr_filters_reject_units_without_percent_conversion(type_code, unit):
    message = "only A/P or G/P" if unit else "Unit must be one uppercase letter"
    with pytest.raises(ValueError, match=message):
        validate_xdr_filter(type_code, unit, "BowThrust#0")
    with pytest.raises(IEC61162Error, match=message):
        _decode(f"GPXDR,{type_code},12,{unit},BowThrust#0", xdr_type=type_code, xdr_unit=unit)


@pytest.mark.parametrize("payload,actuator", [
    ("HCTRC,1,101,P,0,P,,B,R", "bow_thruster"),
    ("HCTRC,1,nan,P,0,P,,B,R", "bow_thruster"),
    ("HCTRC,1,30,V,0,P,,B,R", "bow_thruster"),
    ("HCTRC,1,30,P,0,P,,B,V", "bow_thruster"),
    ("HCTRC,1,30,P", "bow_thruster"),
    ("GPPRC,25,V,0,P,0,P,B,1", "engine"),
    ("GPPRC,-101,A,0,P,0,P,B,1", "engine"),
    ("GPXDR,A,inf,P,BowThrust#0", "bow_thruster"),
    ("GPXDR,A,10,P", "bow_thruster"),
    ("GPXDR,A,10,P,BowThrust#0,A,20,P,BowThrust#0", "bow_thruster"),
])
def test_invalid_external_values_are_rejected(payload, actuator):
    with pytest.raises(IEC61162Error):
        _decode(payload, actuator)


def test_thruster_spools_through_zero_and_has_speed_dependent_force():
    profile = VesselProfileStore().get("container-feeder")
    dynamics = VesselDynamics()
    state = SimulationState(control_mode=ControlMode.SHIP)
    state.bow_thruster.order_percent = 100
    dynamics.integrate(state, profile, 1)
    assert state.bow_thruster.response_percent == pytest.approx(20)
    assert state.motion.rate_of_turn_deg_min > 0
    assert state.motion.transverse_water_kn > 0
    state.bow_thruster.order_percent = -100
    dynamics.integrate(state, profile, 1)
    assert state.bow_thruster.response_percent == pytest.approx(0)
    dynamics.integrate(state, profile, 1)
    assert state.bow_thruster.response_percent < 0

    forces = []
    for speed in (0, 3, 6, -3):
        state = SimulationState(control_mode=ControlMode.SHIP)
        state.motion.stw_kn = speed
        state.engine.effective_speed_order_kn = speed
        state.bow_thruster.order_percent = state.bow_thruster.response_percent = 100
        dynamics.integrate(state, profile, .05)
        forces.append(state.bow_thruster.force_kn)
    assert forces[1] == pytest.approx(forces[0] / 2)
    assert forces[2] == pytest.approx(forces[0] / 17)
    assert forces[3] == pytest.approx(forces[1])


def test_zero_power_has_no_motion_and_profiles_migrate(tmp_path):
    store = VesselProfileStore(tmp_path / "profiles.json")
    for profile in store.all_profiles():
        assert profile.bow_thruster_power_kw > 0
    profile = replace(store.get("container-feeder"), bow_thruster_power_kw=0)
    state = SimulationState(control_mode=ControlMode.SHIP)
    state.bow_thruster.order_percent = state.bow_thruster.response_percent = 100
    VesselDynamics().integrate(state, profile, 1)
    assert state.bow_thruster.force_kn == state.bow_thruster.response_percent == 0
    assert state.motion.sog_kn == state.motion.rate_of_turn_deg_min == 0
    payload = store._profile_payload(store.get("container-feeder"))
    for key in tuple(payload):
        if key.startswith("bow_thruster_") or key == "displacement_t":
            del payload[key]
    payload["pivot_fraction_from_bow"] = .05
    store.apply_payload([payload])
    assert store.get("container-feeder").bow_thruster_power_kw == 900
    assert store.get("container-feeder").bow_thruster_fraction_from_bow == .025


def test_independent_priority_timeout_and_manual_takeover():
    now = [100.0]
    service = SimulatorService(monotonic=lambda: now[0])
    service.submit(SetControlMode(ControlMode.SHIP))
    service.submit(SetExternalActuatorPolicy("bow_thruster", "PRIO_TRC_XDR"))
    service.submit(SetExternalActuatorPolicy("engine", "PRC"))
    service.submit(ExternalActuatorOrder("bow_thruster", "TRC", 60, now[0]))
    service.submit(ExternalActuatorOrder("engine", "PRC", 40, now[0]))
    service.advance_wall_time(.1)
    before = service.snapshot()
    assert before.bow_thruster_active_source == "TRC"
    assert before.engine_active_source == "PRC"
    service.submit(SetSpeedOrder(-5))
    service.submit(SetBowThrusterOrder(-30))
    service.advance_wall_time(.1)
    assert service.snapshot().speed_order_kn == before.speed_order_kn
    now[0] += 2.6
    service.submit(ExternalActuatorOrder("bow_thruster", "XDR", -20, now[0]))
    service.advance_wall_time(.1)
    assert service.snapshot().bow_thruster_active_source == "XDR"
    assert service.snapshot().engine_active_source == ""
    assert service.snapshot().speed_order_kn == before.speed_order_kn
    service.submit(SetSpeedOrder(3))
    service.submit(ExternalActuatorOrder("bow_thruster", "TRC", 50, now[0]))
    service.advance_wall_time(.1)
    assert service.snapshot().bow_thruster_active_source == "TRC"
    assert service.snapshot().speed_order_kn == 3
    now[0] += 2.6
    service.advance_wall_time(.1)
    assert service.snapshot().bow_thruster_order_percent == 0
    assert service.snapshot().bow_thruster_active_source == ""
    service.submit(SetBowThrusterOrder(25))
    service.advance_wall_time(.1)
    assert service.snapshot().bow_thruster_order_percent == 25
    service.close()


def test_manual_rudder_in_external_mode_and_invalid_commands_do_not_poison_state():
    service = SimulatorService()
    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    service.submit(SetExternalRudderInputType("MANUAL"))
    service.submit(SetRudderOrder(12))
    service.submit(SetBowThrusterOrder(math.nan))
    service.submit(ExternalActuatorOrder("engine", "PRC", math.inf, 0))
    service.advance_wall_time(.1)
    snapshot = service.snapshot()
    assert snapshot.rudder_order_starboard_deg == 12
    assert snapshot.bow_thruster_order_percent == 0
    assert snapshot.speed_order_kn == 0
    service.close()


def test_input_flood_keeps_only_latest_sample_per_source():
    service = SimulatorService(monotonic=lambda: 100)
    service.submit(SetControlMode(ControlMode.SHIP))
    service.submit(SetExternalActuatorPolicy("engine", "PRC"))
    for value in range(10000):
        service.submit(ExternalActuatorOrder("engine", "PRC", value % 101, 100))
    assert len(service._commands) == 3
    service.advance_wall_time(.1)
    assert not service._commands
    assert not service._pending_actuator_orders
    assert service.snapshot().engine_active_source == "PRC"
    service.close()


def test_runtime_settings_and_received_values_roundtrip(tmp_path):
    from PySide6.QtWidgets import QApplication
    from maritime_simulator.ui.input_runtime import InputRuntime

    app = QApplication.instance() or QApplication([])
    service = SimulatorService(monotonic=lambda: 100)
    runtime = InputRuntime(service, tmp_path / "input.json", monotonic=lambda: 100)
    runtime.stop()
    runtime._health_timer.stop()
    try:
        runtime.update(bow_thruster={"policy": "PRIO_TRC_XDR", "xdr_id": "Custom#0"}, engine={"policy": "PRC"})
        service.submit(SetControlMode(ControlMode.SHIP))
        runtime._on_sentence("external-bow_thruster-trc", IEC61162Codec.frame("HCTRC,1,55,P,0,P,,B,R"))
        runtime._on_sentence("external-engine-prc", IEC61162Codec.frame("GPPRC,25,A,0,P,0,P,B,1"))
        service.advance_wall_time(.1)
        assert service.snapshot().bow_thruster_order_percent == 55
        assert service.snapshot().engine_active_source == "PRC"
        assert runtime._load().bow_thruster.xdr_id == "Custom#0"
        assert runtime.settings.bow_thruster.xdr_type == "A"
        for bad in ({"xdr_id": "bad,id"}, {"xdr_type": "invalid"}, {"xdr_type": "C", "xdr_unit": "C"},
                    {"xdr_unit": "D"}, {"number": 0}, {"policy": "bad"}):
            with pytest.raises((TypeError, ValueError)):
                runtime.update(bow_thruster=bad)
            assert runtime.settings.bow_thruster.xdr_id == "Custom#0"
            assert runtime.settings.bow_thruster.xdr_type == "A"
        runtime.update(bow_thruster={"xdr_type": "G"}, engine={"xdr_type": "G"})
        restored = runtime._load()
        assert restored.bow_thruster.xdr_type == restored.engine.xdr_type == "G"
        assert restored.bow_thruster.xdr_id == "Custom#0"
        runtime._on_sentence("external-engine-prc", IEC61162Codec.frame("GPPRC,nan,A,0,P,0,P,B,1"))
        service.advance_wall_time(.1)
        assert math.isfinite(service.snapshot().speed_order_kn)
    finally:
        runtime.close()
        service.close()
        app.processEvents()


@pytest.mark.parametrize("transport", ["UDP", "TCP"])
@pytest.mark.parametrize("xdr_type", ["A", "G"])
def test_shared_endpoint_receives_each_axis_and_matches_each_xdr_id(tmp_path, transport, xdr_type):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from maritime_simulator.ui.input_runtime import InputRuntime

    app = QApplication.instance() or QApplication([])
    service = SimulatorService()
    runtime = InputRuntime(service, tmp_path / "input.json")
    runtime._health_timer.stop()
    runtime.stop()
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if transport == "UDP" else socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    endpoint = {"transport": transport, "host": "127.0.0.1", "port": port}
    connection = None
    try:
        runtime.update(ror=endpoint, rsa=endpoint,
                       xdr_order={"endpoint": endpoint}, xdr_response={"endpoint": endpoint},
                       bow_thruster={"policy": "XDR", "xdr_type": xdr_type, "primary": endpoint, "xdr": endpoint},
                       engine={"policy": "XDR", "xdr_type": xdr_type, "primary": endpoint, "xdr": endpoint})
        runtime.start()
        assert len(runtime._manager._sources) == 1
        service.submit(SetControlMode(ControlMode.SHIP))
        frame = IEC61162Codec.frame(f"GPXDR,{xdr_type},40,P,BowThrust#0,{xdr_type},-20,P,EngTele#0") + "\r\n"
        if transport == "UDP":
            connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            connection.sendto(frame.encode("ascii"), ("127.0.0.1", port))
        else:
            deadline = time.monotonic() + 2
            while connection is None:
                try:
                    connection = socket.create_connection(("127.0.0.1", port), timeout=.1)
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    QTest.qWait(10)
            connection.sendall(frame[:20].encode("ascii"))
            connection.sendall(frame[20:].encode("ascii"))
        deadline = time.monotonic() + 2
        while not runtime.events() and time.monotonic() < deadline:
            QTest.qWait(10)
        assert runtime.events()
        service.advance_wall_time(.1)
        assert service.snapshot().bow_thruster_order_percent == 40
        assert service.snapshot().speed_order_kn == pytest.approx(service.profiles.get(service.snapshot().vessel_profile_id).speed_from_lever_percent(-20))
        assert runtime.events()[-1].formatter == "XDR"
        assert runtime.events()[-1].accepted_for_control
    finally:
        if connection is not None:
            connection.close()
        runtime.close()
        service.close()
        app.processEvents()


def test_engine_lever_mapping_matches_existing_detents_and_roundtrips():
    for profile in VesselProfileStore().all_profiles():
        for index, (_name, speed) in enumerate(profile.engine_steps()):
            lever = 100.0 - index * 25.0
            assert profile.speed_from_lever_percent(lever) == pytest.approx(speed)
            assert profile.lever_percent_from_speed(speed) == pytest.approx(lever)
        for lever in range(-100, 101, 5):
            assert profile.lever_percent_from_speed(profile.speed_from_lever_percent(lever)) == pytest.approx(lever)


@pytest.mark.parametrize("actuator,primary,valid,empty,identifier", [
    ("bow_thruster", "TRC", "HCTRC,1,50,P,0,P,,B,R", "HCTRC,1,,V,0,P,,B,R", "BowThrust#0"),
    ("engine", "PRC", "GPPRC,25,A,0,P,0,P,B,1", "GPPRC,,V,0,P,0,P,B,1", "EngTele#0"),
])
def test_actuator_indications_use_same_age_and_empty_states_as_rudder(tmp_path, actuator, primary, valid, empty, identifier):
    from PySide6.QtWidgets import QApplication
    from maritime_simulator.ui.input_runtime import InputRuntime
    from maritime_simulator.ui.external_input import ActuatorInputPanel

    app = QApplication.instance() or QApplication([])
    now = [100.0]
    service = SimulatorService(monotonic=lambda: now[0])
    runtime = InputRuntime(service, tmp_path / "input.json", monotonic=lambda: now[0])
    runtime._health_timer.stop()
    runtime.stop()
    panel = ActuatorInputPanel(runtime, actuator, lambda: None, lambda: None)
    try:
        assert all(item.state == "alarm" for item in runtime.actuator_channel_statuses(actuator))
        source = f"external-{actuator}-{primary.lower()}"
        runtime._on_sentence(source, IEC61162Codec.frame(valid))
        panel.refresh()
        assert panel.input_lamps[primary].property("signalState") == "ok"
        assert panel.input_lamps["XDR"].property("signalState") == "alarm"
        now[0] = 105
        assert runtime.actuator_channel_statuses(actuator)[0].state == "ok"
        now[0] = 105.01
        panel.refresh()
        assert panel.input_lamps[primary].property("signalState") == "delayed"
        runtime._on_sentence(source, IEC61162Codec.frame(empty))
        panel.refresh()
        assert panel.input_lamps[primary].property("signalState") == "empty"
        assert "field empty" in panel.input_lamps[primary].toolTip()
        runtime._on_sentence(f"external-{actuator}-xdr", IEC61162Codec.frame(f"GPXDR,A,12,P,{identifier}"))
        panel.refresh()
        assert panel.input_lamps["XDR"].property("signalState") == "ok"
        runtime._on_sentence(f"external-{actuator}-xdr", IEC61162Codec.frame("GPXDR,A,,P,Wrong#0"))
        assert runtime.actuator_channel_statuses(actuator)[1].state == "ok"
        runtime._on_sentence(f"external-{actuator}-xdr", IEC61162Codec.frame(f"GPXDR,A,,P,{identifier}"))
        panel.refresh()
        assert panel.input_lamps["XDR"].property("signalState") == "empty"
        now[0] = 136
        panel.refresh()
        assert all(lamp.property("signalState") == "alarm" for lamp in panel.input_lamps.values())
    finally:
        runtime.close()
        service.close()
        panel.deleteLater()
        app.processEvents()
