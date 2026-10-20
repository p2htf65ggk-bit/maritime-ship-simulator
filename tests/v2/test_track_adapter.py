# SPDX-License-Identifier: MPL-2.0
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import SetControlMode, SetRoute, SetSpeedOrder
from maritime_simulator.domain.enums import CommandSource, ControlMode
from maritime_simulator.domain.state import RouteWaypoint, SimulationState
from maritime_simulator.simulation.clock import SimulationClock
from maritime_simulator.simulation.track_adapter import LegacyTrackControllerAdapter


def test_default_v2_service_wraps_v1_track_controller_without_speed_takeover():
    service = SimulatorService(clock=SimulationClock(fixed_step_s=0.2))
    route = (
        RouteWaypoint("A", 0.0, 0.0),
        RouteWaypoint("B", 0.0, 0.2, speed_kn=3.0, turn_rate_deg_min=18.0),
        RouteWaypoint("C", 0.1, 0.3732),
    )
    service.submit(SetRoute(route))
    service.submit(SetSpeedOrder(12.0))
    service.submit(SetControlMode(ControlMode.TRACK))
    for _ in range(20):
        service.advance_wall_time(0.2)
    snapshot = service.snapshot()
    assert snapshot.speed_order_kn == 12.0
    assert snapshot.speed_command_source is CommandSource.USER
    assert snapshot.rudder_command_source is CommandSource.TRACK_CONTROLLER
    assert snapshot.next_waypoint_index >= 1
    assert snapshot.track_phase in {"straight_leg", "inbound_straight", "planned_turn", "outbound_straight"}
    service.close()


def test_manual_speed_change_rebuilds_controller_aligned_wop_and_arc():
    state = SimulationState()
    state.route.waypoints = (
        RouteWaypoint("A", 0.0, 0.0),
        RouteWaypoint("B", 0.0, 0.2, turn_rate_deg_min=10.0),
        RouteWaypoint("C", 0.2, 0.2),
    )
    state.route.use_planned_speed = False
    state.route.use_planned_turns = False
    state.route.control_turn_mode = "fixed_rot"
    state.route.control_turn_rate_deg_min = 10.0
    state.engine.effective_speed_order_kn = 10.0
    adapter = LegacyTrackControllerAdapter()

    marks_slow, arcs_slow = adapter.route_visuals(state, "Crew Transfer Vessel")
    state.engine.effective_speed_order_kn = 20.0
    marks_fast, arcs_fast = adapter.route_visuals(state, "Crew Transfer Vessel")

    assert marks_fast[0]["lead_nm"] > marks_slow[0]["lead_nm"] * 1.9
    assert arcs_fast[0]["radius_nm"] > arcs_slow[0]["radius_nm"] * 1.9
    assert adapter._route_signature[1].speed_kn == 20.0


def test_manual_radius_change_moves_controller_wop_without_forcing_speed():
    state = SimulationState()
    state.route.waypoints = (
        RouteWaypoint("A", 0.0, 0.0),
        RouteWaypoint("B", 0.0, 0.2),
        RouteWaypoint("C", 0.2, 0.2),
    )
    state.route.use_planned_speed = False
    state.route.use_planned_turns = False
    state.route.control_turn_mode = "fixed_radius"
    state.route.control_turn_radius_nm = 0.5
    state.engine.effective_speed_order_kn = 12.0
    adapter = LegacyTrackControllerAdapter()

    marks_small, _arcs_small = adapter.route_visuals(state, "Crew Transfer Vessel")
    state.route.control_turn_radius_nm = 1.0
    marks_large, _arcs_large = adapter.route_visuals(state, "Crew Transfer Vessel")

    assert marks_large[0]["lead_nm"] > marks_small[0]["lead_nm"] + 0.45
    assert state.engine.effective_speed_order_kn == 12.0
