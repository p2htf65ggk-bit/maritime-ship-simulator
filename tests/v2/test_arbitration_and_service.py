# SPDX-License-Identifier: MPL-2.0
import pytest

from maritime_simulator.application.arbitration import CommandArbitrator
from maritime_simulator.application.service import SimulatorService, TrackControlOutput
from maritime_simulator.domain.commands import (
    ExternalRudderOrder,
    ExternalRudderResponse,
    SetControlMode,
    SetExternalRudderInputType,
    SetRudderOrder,
    SetSensorValues,
    SetSpeedOrder,
)
from maritime_simulator.domain.enums import CommandSource, ControlMode, InputValidity
from maritime_simulator.simulation.clock import SimulationClock


class FixedTrackController:
    def __init__(self, order=18.0):
        self.order = order
        self.reset_count = 0

    def update(self, state, profile_display_name, dt_s):
        return TrackControlOutput(self.order, progress_nm=1.2, xte_nm=0.01, next_waypoint_index=2, phase="straight_leg")

    def reset(self):
        self.reset_count += 1


def test_mode_arbitration_prevents_stale_sources_from_overwriting_commands():
    now = [100.0]
    service = SimulatorService(
        clock=SimulationClock(fixed_step_s=0.1),
        track_controller=FixedTrackController(),
        monotonic=lambda: now[0],
    )
    service.submit(SetSpeedOrder(20.0))
    service.submit(SetRudderOrder(-12.0))
    service.submit(ExternalRudderOrder(25.0, None, now[0]))
    service.submit(SetControlMode(ControlMode.SHIP))
    service.advance_wall_time(0.1)
    ship = service.snapshot()
    assert ship.speed_order_kn == 20.0
    assert ship.rudder_order_starboard_deg == -12.0
    assert ship.rudder_command_source is CommandSource.USER

    service.submit(SetControlMode(ControlMode.TRACK))
    service.advance_wall_time(0.1)
    track = service.snapshot()
    assert track.speed_order_kn == 20.0
    assert track.rudder_order_starboard_deg == 18.0
    assert track.rudder_command_source is CommandSource.TRACK_CONTROLLER

    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    service.advance_wall_time(0.1)
    external = service.snapshot()
    assert external.rudder_order_starboard_deg == 25.0
    assert external.rudder_command_source is CommandSource.EXTERNAL_ROR


def test_external_rudder_timeout_commands_midships_without_changing_speed():
    now = [10.0]
    service = SimulatorService(
        clock=SimulationClock(fixed_step_s=0.1),
        arbitrator=CommandArbitrator(external_rudder_timeout_s=1.0),
        monotonic=lambda: now[0],
    )
    service.submit(SetSpeedOrder(15.0))
    service.submit(ExternalRudderOrder(30.0, -20.0, now[0]))
    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    service.advance_wall_time(0.1)
    assert service.snapshot().external_rudder_validity is InputValidity.VALID

    now[0] = 11.5
    service.advance_wall_time(0.1)
    snapshot = service.snapshot()
    assert snapshot.external_rudder_validity is InputValidity.STALE
    assert snapshot.rudder_order_starboard_deg == 0.0
    assert snapshot.rudder_command_source is CommandSource.SAFETY
    assert snapshot.speed_order_kn == 15.0


def test_external_rsa_enters_at_response_layer_and_bypasses_order_dynamics():
    now = [20.0]
    service = SimulatorService(
        clock=SimulationClock(fixed_step_s=0.1),
        monotonic=lambda: now[0],
    )
    service.submit(SetExternalRudderInputType("RSA"))
    service.submit(ExternalRudderResponse(17.0, -11.0, now[0]))
    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    service.advance_wall_time(0.1)
    snapshot = service.snapshot()
    assert snapshot.external_rudder_input_type == "RSA"
    assert snapshot.rudder_order_starboard_deg == 0.0
    assert snapshot.rudder_response_starboard_deg == 17.0
    assert snapshot.rudder_response_port_deg == -11.0
    assert snapshot.rudder_command_source is CommandSource.EXTERNAL_RSA


def test_dual_external_rsa_uses_both_rudders_for_turning_effect():
    now = [25.0]
    service = SimulatorService(monotonic=lambda: now[0])
    service.submit(SetSpeedOrder(20.0))
    service.submit(SetControlMode(ControlMode.SHIP))
    for _ in range(100):
        now[0] += 0.1
        service.advance_wall_time(0.1)

    service.submit(SetExternalRudderInputType("RSA"))
    service.submit(ExternalRudderResponse(12.0, -12.0, now[0]))
    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    service.advance_wall_time(0.1)
    assert service.snapshot().rate_of_turn_deg_min == pytest.approx(0.0, abs=1e-6)

    now[0] += 0.1
    service.submit(ExternalRudderResponse(12.0, 12.0, now[0]))
    service.advance_wall_time(0.1)
    assert service.snapshot().rate_of_turn_deg_min > 0.0
    service.close()


def test_external_rsa_uses_parallel_ror_for_order_display_only():
    now = [30.0]
    service = SimulatorService(monotonic=lambda: now[0])
    service.submit(SetExternalRudderInputType("RSA"))
    service.submit(ExternalRudderOrder(14.0, -9.0, now[0]))
    service.submit(ExternalRudderResponse(5.0, -4.0, now[0]))
    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    service.advance_wall_time(0.1)

    snapshot = service.snapshot()
    assert snapshot.rudder_order_starboard_deg == 14.0
    assert snapshot.rudder_order_port_deg == -9.0
    assert snapshot.rudder_response_starboard_deg == 5.0
    assert snapshot.rudder_response_port_deg == -4.0
    assert snapshot.rudder_command_source is CommandSource.EXTERNAL_RSA
    service.close()


def test_priority_rsa_uses_fallback_then_returns_on_first_valid_preferred_sentence():
    now = [40.0]
    service = SimulatorService(monotonic=lambda: now[0])
    try:
        service.submit(SetExternalRudderInputType("PRIO_RSA_ROR"))
        service.submit(ExternalRudderOrder(11.0, -8.0, now[0]))
        service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
        service.advance_wall_time(0.1)
        fallback = service.snapshot()
        assert fallback.external_rudder_policy == "PRIO_RSA_ROR"
        assert fallback.external_rudder_input_type == "ROR"
        assert fallback.rudder_order_starboard_deg == 11.0
        assert fallback.dual_rudder is True

        now[0] += 0.1
        service.submit(ExternalRudderResponse(4.0, None, now[0]))
        service.advance_wall_time(0.1)
        preferred = service.snapshot()
        assert preferred.external_rudder_input_type == "RSA"
        assert preferred.rudder_response_starboard_deg == 4.0
        # Topology belongs wholly to the selected RSA sentence; the dual ROR
        # must not donate its PORT field to the model.
        assert preferred.dual_rudder is False

        now[0] += 2.6
        service.submit(ExternalRudderOrder(7.0, -6.0, now[0]))
        service.advance_wall_time(0.1)
        timed_out = service.snapshot()
        assert timed_out.external_rudder_input_type == "ROR"
        assert timed_out.rudder_order_starboard_deg == 7.0
        assert timed_out.dual_rudder is True
    finally:
        service.close()


def test_priority_ror_falls_back_to_rsa_without_mixing_protocol_sources():
    now = [50.0]
    service = SimulatorService(monotonic=lambda: now[0])
    try:
        service.submit(SetExternalRudderInputType("PRIO_ROR_RSA"))
        service.submit(ExternalRudderResponse(-5.0, 3.0, now[0]))
        service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
        service.advance_wall_time(0.1)
        fallback = service.snapshot()
        assert fallback.external_rudder_input_type == "RSA"
        assert fallback.rudder_response_starboard_deg == -5.0
        assert fallback.rudder_response_port_deg == 3.0

        now[0] += 0.1
        service.submit(ExternalRudderOrder(-12.0, None, now[0]))
        service.advance_wall_time(0.1)
        preferred = service.snapshot()
        assert preferred.external_rudder_input_type == "ROR"
        assert preferred.rudder_order_starboard_deg == -12.0
        assert preferred.dual_rudder is False
    finally:
        service.close()


def test_sensor_control_values_are_authoritative_and_not_integrated_over():
    service = SimulatorService(clock=SimulationClock(fixed_step_s=0.1))
    service.submit(
        SetSensorValues(
            latitude_deg=54.1,
            longitude_deg=10.2,
            heading_deg=271.0,
            cog_deg=265.0,
            sog_kn=-3.0,
            stw_kn=-2.5,
            rot_deg_min=4.2,
            rudder_order_starboard_deg=10.0,
            rudder_response_starboard_deg=8.0,
            depth_m=22.0,
        )
    )
    service.advance_wall_time(2.0)
    snapshot = service.snapshot()
    assert (snapshot.latitude_deg, snapshot.longitude_deg) == (54.1, 10.2)
    assert snapshot.heading_deg == 271.0
    assert snapshot.cog_deg == 265.0
    assert snapshot.sog_kn == -3.0
    assert snapshot.stw_kn == -2.5
    assert snapshot.rate_of_turn_deg_min == 4.2
    assert snapshot.rudder_order_starboard_deg == 10.0
    assert snapshot.rudder_response_starboard_deg == 8.0
    assert snapshot.depth_m == 22.0


def test_sensor_values_cannot_overwrite_model_driven_modes():
    service = SimulatorService()
    service.submit(SetControlMode(ControlMode.SHIP))
    service.advance_wall_time(0.1)
    service.submit(SetSensorValues(stw_kn=30.0))
    try:
        service.advance_wall_time(0.1)
    except ValueError as error:
        assert "Sensor Control" in str(error)
    else:
        raise AssertionError("Model-driven mode accepted a manual sensor overwrite")
