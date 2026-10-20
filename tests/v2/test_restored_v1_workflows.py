# SPDX-License-Identifier: MPL-2.0
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtWidgets import QApplication

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import (
    SetControlMode,
    SetRoute,
    SetRouteOptions,
    SetSensorCoupling,
    SetSensorValues,
)
from maritime_simulator.domain.enums import CommandSource, ControlMode
from maritime_simulator.domain.state import RouteWaypoint
from maritime_simulator.protocols.sensors import SensorProjector
from maritime_simulator.simulation.clock import SimulationClock
from maritime_simulator.ui.controls import EngineTelegraphControl
from maritime_simulator.ui.route_planner import RoutePlannerPanel


def app():
    return QApplication.instance() or QApplication([])


def test_sensor_hdg_rot_and_cog_offset_couplings_are_explicit():
    service = SimulatorService(clock=SimulationClock(fixed_step_s=0.1))
    service.submit(SetSensorValues(heading_deg=100.0, cog_deg=106.0, rot_deg_min=60.0))
    service.submit(SetSensorCoupling(cog_follows_heading=True, heading_follows_rot=True))
    service.advance_wall_time(1.0)
    snapshot = service.snapshot()
    assert 100.9 <= snapshot.heading_deg <= 101.1
    assert 106.9 <= snapshot.cog_deg <= 107.1
    service.close()


def test_planned_route_speed_owns_effective_order_only_when_enabled():
    service = SimulatorService()
    route = (
        RouteWaypoint("A", 52.0, 3.0),
        RouteWaypoint("B", 52.01, 3.0, speed_kn=7.5),
    )
    service.submit(SetRoute(route))
    service.submit(SetControlMode(ControlMode.TRACK))
    service.submit(SetRouteOptions(use_planned_speed=True))
    service.advance_wall_time(0.0)
    snapshot = service.snapshot()
    assert snapshot.speed_order_kn == 7.5
    assert snapshot.speed_command_source is CommandSource.ROUTE_PLAN
    service.close()


def test_engine_telegraph_keeps_full_ahead_at_top_and_steps_directly():
    app()
    control = EngineTelegraphControl()
    selected = []
    control.speed_selected.connect(selected.append)
    steps = (("Full Ahead", 20.0), ("Half Ahead", 12.0), ("Stop", 0.0), ("Full Astern", -6.0))
    control.set_steps(steps, 0.0)
    assert control.itemText(0).startswith("Full Ahead")
    control.step(1)
    assert selected[-1] == 12.0


def test_route_planner_uses_separate_rot_and_radius_columns_with_calculated_peer():
    app()
    panel = RoutePlannerPanel()
    route = (
        RouteWaypoint("A", 52.0, 3.0, speed_kn=10.0),
        RouteWaypoint("B", 52.01, 3.0, speed_kn=10.0, turn_rate_deg_min=12.0),
        RouteWaypoint("C", 52.01, 3.01, speed_kn=10.0),
    )
    commands = []
    panel.command_submitted.connect(commands.append)
    panel.set_route(route)
    assert panel.table.item(1, panel.ROT).text() == "12.0"
    assert panel.table.item(1, panel.RADIUS).text() == "Calculated"
    panel.table.item(1, panel.RADIUS).setText("0.45")
    converted = commands[-1].waypoints[1]
    assert converted.turn_mode == "fixed_radius"
    assert converted.turn_radius_nm == 0.5
    assert panel.table.item(1, panel.ROT).text() == "Calculated"


def test_route_planner_pastes_multiple_rows_as_one_complete_update():
    app()
    panel = RoutePlannerPanel()
    route = (
        RouteWaypoint("A", 52.0, 3.0, speed_kn=10.0),
        RouteWaypoint("B", 52.01, 3.0, speed_kn=10.0),
        RouteWaypoint("C", 52.02, 3.0, speed_kn=10.0),
    )
    commands = []
    panel.command_submitted.connect(commands.append)
    panel.set_route(route)
    panel._paste(
        0,
        panel.SPEED,
        [["8.5", "Calculated", "0.45"], ["9.5", "Calculated", "0.55"]],
    )

    assert len(commands) == 1
    updated = commands[0].waypoints
    assert (updated[0].speed_kn, updated[1].speed_kn) == (8.5, 9.5)
    assert (updated[0].turn_radius_nm, updated[1].turn_radius_nm) == (0.5, 0.6)
    assert updated[0].turn_mode == updated[1].turn_mode == "fixed_radius"


def test_route_planner_apply_to_all_publishes_one_atomic_update():
    app()
    panel = RoutePlannerPanel()
    panel.set_route(tuple(
        RouteWaypoint(name, 52.0 + index * 0.01, 3.0, speed_kn=10.0)
        for index, name in enumerate(("A", "B", "C"))
    ))
    commands = []
    panel.command_submitted.connect(commands.append)

    panel._apply_value_to_all(panel.SPEED, "12.5")

    assert len(commands) == 1
    assert all(waypoint.speed_kn == 12.5 for waypoint in commands[0].waypoints)


def test_sensor_noise_is_projection_only_and_deterministic():
    service = SimulatorService()
    service.submit(SetSensorValues(heading_deg=90.0))
    from maritime_simulator.domain.commands import SetSensorNoise

    service.submit(SetSensorNoise("heading", enabled=True, amplitude=2.0))
    service.advance_wall_time(0.0)
    snapshot = service.snapshot()
    first = SensorProjector().project(snapshot)
    second = SensorProjector().project(snapshot)
    assert first == second
    assert snapshot.heading_deg == 90.0
    service.close()


def test_track_adapter_exposes_controller_aligned_wop_and_arcs():
    service = SimulatorService()
    route = (
        RouteWaypoint("A", 52.0, 3.0, speed_kn=10.0),
        RouteWaypoint("B", 52.02, 3.0, speed_kn=10.0, turn_rate_deg_min=10.0),
        RouteWaypoint("C", 52.02, 3.03, speed_kn=10.0),
    )
    service.submit(SetRoute(route))
    service.submit(SetControlMode(ControlMode.TRACK))
    service.advance_wall_time(0.0)
    marks, arcs = service.route_visuals()
    assert marks
    assert arcs
    assert len(arcs[0]["points"]) >= 2
    service.close()
