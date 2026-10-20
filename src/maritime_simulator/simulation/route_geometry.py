# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import math

from maritime_simulator.domain.state import RouteWaypoint


EARTH_RADIUS_NM = 3440.065


def distance_nm(a: RouteWaypoint, b: RouteWaypoint) -> float:
    lat1 = math.radians(a.latitude_deg)
    lat2 = math.radians(b.latitude_deg)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(b.longitude_deg - a.longitude_deg)
    value = math.sin(delta_lat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(value)))


def bearing_deg(a: RouteWaypoint, b: RouteWaypoint) -> float:
    lat1 = math.radians(a.latitude_deg)
    lat2 = math.radians(b.latitude_deg)
    delta_lon = math.radians(b.longitude_deg - a.longitude_deg)
    north = math.sin(delta_lon) * math.cos(lat2)
    east = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    return math.degrees(math.atan2(north, east)) % 360.0


def signed_angle_delta(target_deg: float, source_deg: float) -> float:
    return ((target_deg - source_deg + 180.0) % 360.0) - 180.0


def radius_from_rot(speed_kn: float, rot_deg_min: float) -> float:
    if speed_kn <= 0.0 or rot_deg_min <= 0.0:
        raise ValueError("Speed and ROT must be positive")
    return float(speed_kn) * 3.0 / (math.pi * float(rot_deg_min))


def rot_from_radius(speed_kn: float, radius_nm: float) -> float:
    if speed_kn <= 0.0 or radius_nm <= 0.0:
        raise ValueError("Speed and radius must be positive")
    return float(speed_kn) * 3.0 / (math.pi * float(radius_nm))


@dataclass(frozen=True, slots=True)
class RouteProjection:
    progress_nm: float
    cross_track_nm: float
    segment_index: int
    segment_fraction: float


@dataclass(frozen=True, slots=True)
class PlannedTurnGeometry:
    waypoint_index: int
    inbound_bearing_deg: float
    outbound_bearing_deg: float
    signed_turn_angle_deg: float
    radius_nm: float
    effective_rot_deg_min: float
    wheel_over_distance_nm: float
    turn_start_progress_nm: float
    turn_end_progress_nm: float
    overlaps_inbound_leg: bool
    overlaps_outbound_leg: bool
    radius_clamped_by_rot_limit: bool


class RouteGeometry:
    def __init__(self, waypoints: tuple[RouteWaypoint, ...]):
        self.waypoints = tuple(waypoints)
        ticks = [0.0]
        for first, second in zip(self.waypoints, self.waypoints[1:]):
            ticks.append(ticks[-1] + distance_nm(first, second))
        self.ticks_nm = tuple(ticks)

    @property
    def total_distance_nm(self) -> float:
        return self.ticks_nm[-1] if self.ticks_nm else 0.0

    def project(self, latitude_deg: float, longitude_deg: float) -> RouteProjection:
        if len(self.waypoints) < 2:
            return RouteProjection(0.0, 0.0, 0, 0.0)
        origin_lat = self.waypoints[0].latitude_deg
        cos_lat = max(1e-6, abs(math.cos(math.radians(origin_lat))))
        ship_x = (longitude_deg - self.waypoints[0].longitude_deg) * 60.0 * cos_lat
        ship_y = (latitude_deg - origin_lat) * 60.0
        best: tuple[float, RouteProjection] | None = None
        for index, (first, second) in enumerate(zip(self.waypoints, self.waypoints[1:])):
            ax = (first.longitude_deg - self.waypoints[0].longitude_deg) * 60.0 * cos_lat
            ay = (first.latitude_deg - origin_lat) * 60.0
            bx = (second.longitude_deg - self.waypoints[0].longitude_deg) * 60.0 * cos_lat
            by = (second.latitude_deg - origin_lat) * 60.0
            dx, dy = bx - ax, by - ay
            length_squared = dx * dx + dy * dy
            fraction = 0.0 if length_squared <= 1e-12 else max(0.0, min(1.0, ((ship_x - ax) * dx + (ship_y - ay) * dy) / length_squared))
            nearest_x = ax + fraction * dx
            nearest_y = ay + fraction * dy
            offset_x = ship_x - nearest_x
            offset_y = ship_y - nearest_y
            distance = math.hypot(offset_x, offset_y)
            cross = 0.0 if length_squared <= 1e-12 else (dx * (ship_y - ay) - dy * (ship_x - ax)) / math.sqrt(length_squared)
            progress = self.ticks_nm[index] + (self.ticks_nm[index + 1] - self.ticks_nm[index]) * fraction
            projection = RouteProjection(progress, cross, index, fraction)
            if best is None or distance < best[0]:
                best = (distance, projection)
        assert best is not None
        return best[1]

    def position_at(self, progress_nm: float) -> tuple[float, float] | None:
        if not self.waypoints:
            return None
        progress = max(0.0, min(self.total_distance_nm, float(progress_nm)))
        for index in range(len(self.ticks_nm) - 1):
            end = self.ticks_nm[index + 1]
            if progress <= end + 1e-12:
                start = self.ticks_nm[index]
                fraction = 0.0 if end <= start else (progress - start) / (end - start)
                first, second = self.waypoints[index], self.waypoints[index + 1]
                return (
                    first.latitude_deg + (second.latitude_deg - first.latitude_deg) * fraction,
                    first.longitude_deg + (second.longitude_deg - first.longitude_deg) * fraction,
                )
        final = self.waypoints[-1]
        return final.latitude_deg, final.longitude_deg

    def next_waypoint_index(self, progress_nm: float) -> int:
        if len(self.waypoints) < 2:
            return 0
        for index in range(1, len(self.ticks_nm)):
            if progress_nm <= self.ticks_nm[index] + 1e-9:
                return index
        return len(self.waypoints) - 1

    def planned_turn(
        self,
        waypoint_index: int,
        *,
        speed_kn: float,
        max_rot_deg_min: float,
    ) -> PlannedTurnGeometry | None:
        if waypoint_index <= 0 or waypoint_index >= len(self.waypoints) - 1 or speed_kn <= 0.0:
            return None
        waypoint = self.waypoints[waypoint_index]
        inbound = bearing_deg(self.waypoints[waypoint_index - 1], waypoint)
        outbound = bearing_deg(waypoint, self.waypoints[waypoint_index + 1])
        signed_turn = signed_angle_delta(outbound, inbound)
        if abs(signed_turn) < 0.05:
            return None

        clamped = False
        minimum_radius = radius_from_rot(speed_kn, max_rot_deg_min)
        if waypoint.turn_mode == "fixed_radius" and waypoint.turn_radius_nm is not None:
            requested_radius = max(1e-6, waypoint.turn_radius_nm)
            radius = max(requested_radius, minimum_radius)
            clamped = radius > requested_radius + 1e-9
        else:
            requested_rot = waypoint.turn_rate_deg_min or max_rot_deg_min
            effective_rot = min(max_rot_deg_min, max(1e-6, requested_rot))
            radius = radius_from_rot(speed_kn, effective_rot)
            clamped = requested_rot > max_rot_deg_min + 1e-9
        effective_rot = rot_from_radius(speed_kn, radius)
        wheel_over = radius * math.tan(math.radians(abs(signed_turn)) / 2.0)
        inbound_leg = self.ticks_nm[waypoint_index] - self.ticks_nm[waypoint_index - 1]
        outbound_leg = self.ticks_nm[waypoint_index + 1] - self.ticks_nm[waypoint_index]
        return PlannedTurnGeometry(
            waypoint_index=waypoint_index,
            inbound_bearing_deg=inbound,
            outbound_bearing_deg=outbound,
            signed_turn_angle_deg=signed_turn,
            radius_nm=radius,
            effective_rot_deg_min=effective_rot,
            wheel_over_distance_nm=wheel_over,
            turn_start_progress_nm=self.ticks_nm[waypoint_index] - wheel_over,
            turn_end_progress_nm=self.ticks_nm[waypoint_index] + wheel_over,
            overlaps_inbound_leg=wheel_over > inbound_leg,
            overlaps_outbound_leg=wheel_over > outbound_leg,
            radius_clamped_by_rot_limit=clamped,
        )
