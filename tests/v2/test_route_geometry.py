# SPDX-License-Identifier: MPL-2.0
import pytest

from maritime_simulator.domain.state import RouteWaypoint
from maritime_simulator.simulation.route_geometry import (
    RouteGeometry,
    radius_from_rot,
    rot_from_radius,
)


def route():
    return (
        RouteWaypoint("A", 0.0, 0.0),
        RouteWaypoint("B", 0.0, 0.1, speed_kn=12.0, turn_rate_deg_min=20.0),
        RouteWaypoint("C", 0.1, 0.1),
    )


def test_rot_radius_conversion_round_trip():
    radius = radius_from_rot(12.0, 20.0)
    assert rot_from_radius(12.0, radius) == pytest.approx(20.0)


def test_projection_uses_geodetic_ticks_as_canonical_progress():
    geometry = RouteGeometry(route())
    projection = geometry.project(0.0, 0.05)
    assert projection.progress_nm == pytest.approx(geometry.ticks_nm[1] / 2.0, rel=1e-4)
    assert projection.cross_track_nm == pytest.approx(0.0, abs=1e-6)
    assert geometry.next_waypoint_index(projection.progress_nm) == 1


def test_turn_geometry_is_symmetric_and_reports_overlap_without_forcing_radius():
    waypoints = list(route())
    waypoints[1] = RouteWaypoint(
        "B",
        0.0,
        0.1,
        speed_kn=12.0,
        turn_radius_nm=20.0,
        turn_mode="fixed_radius",
    )
    geometry = RouteGeometry(tuple(waypoints))
    turn = geometry.planned_turn(1, speed_kn=12.0, max_rot_deg_min=30.0)
    assert turn is not None
    assert turn.radius_nm == 20.0
    assert turn.overlaps_inbound_leg is True
    assert turn.overlaps_outbound_leg is True
    waypoint_progress = geometry.ticks_nm[1]
    assert waypoint_progress - turn.turn_start_progress_nm == pytest.approx(turn.wheel_over_distance_nm)
    assert turn.turn_end_progress_nm - waypoint_progress == pytest.approx(turn.wheel_over_distance_nm)


def test_impossible_radius_clamps_only_to_vessel_rot_limit():
    waypoints = list(route())
    waypoints[1] = RouteWaypoint("B", 0.0, 0.1, turn_radius_nm=0.01, turn_mode="fixed_radius")
    geometry = RouteGeometry(tuple(waypoints))
    turn = geometry.planned_turn(1, speed_kn=20.0, max_rot_deg_min=10.0)
    assert turn is not None
    assert turn.radius_clamped_by_rot_limit is True
    assert turn.effective_rot_deg_min == pytest.approx(10.0)
