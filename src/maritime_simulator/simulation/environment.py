# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import math

from maritime_simulator.domain.profiles import VesselProfile
from maritime_simulator.domain.state import SimulationState, normalize_angle


KNOT_TO_MPS = 0.514444


def vector_from_course(course_deg: float, speed_kn: float) -> tuple[float, float]:
    radians = math.radians(course_deg)
    return math.cos(radians) * speed_kn, math.sin(radians) * speed_kn


def course_and_speed(north_kn: float, east_kn: float) -> tuple[float, float]:
    speed = math.hypot(north_kn, east_kn)
    if speed <= 1e-12:
        return 0.0, 0.0
    return normalize_angle(math.degrees(math.atan2(east_kn, north_kn))), speed


def calculate_wind(state: SimulationState) -> None:
    env = state.environment
    motion = state.motion
    true_to = normalize_angle(env.wind_from_deg + 180.0)
    true_north, true_east = vector_from_course(true_to, env.wind_speed_kn)
    ship_north, ship_east = vector_from_course(motion.cog_deg, abs(motion.sog_kn))
    apparent_north = true_north - ship_north
    apparent_east = true_east - ship_east
    apparent_to, apparent_speed = course_and_speed(apparent_north, apparent_east)
    apparent_from = normalize_angle(apparent_to + 180.0) if apparent_speed > 1e-9 else state.motion.heading_deg

    env.true_wind_from_deg = normalize_angle(env.wind_from_deg)
    env.true_wind_speed_kn = max(0.0, env.wind_speed_kn)
    env.relative_wind_from_bow_deg = normalize_angle(apparent_from - motion.heading_deg)
    env.relative_wind_speed_kn = apparent_speed


def update_ground_vector(state: SimulationState, profile: VesselProfile, dt_s: float, *, thruster_sway_kn: float = 0.0) -> None:
    motion = state.motion
    env = state.environment
    heading = normalize_angle(motion.heading_deg)

    turn_sway_target = (
        -motion.rate_of_turn_deg_min
        / max(1.0, profile.max_rot_deg_min)
        * abs(motion.stw_kn)
        * profile.turn_slip
    )
    if motion.stw_kn < -0.05:
        turn_sway_target *= -1.0
    sway_alpha = min(1.0, max(0.0, dt_s) / max(0.1, profile.sway_time_constant_s))
    motion.sway_kn += (turn_sway_target + thruster_sway_kn - motion.sway_kn) * sway_alpha

    wind_to = normalize_angle(env.wind_from_deg + 180.0)
    relative_wind_rad = math.radians(((wind_to - heading + 180.0) % 360.0) - 180.0)
    stw_abs = abs(motion.stw_kn)
    damping = 1.0 / (1.0 + stw_abs / max(1.0, profile.max_speed_kn * 0.45))
    damping = max(profile.wind_damping_floor, min(1.0, damping))
    wind_lateral_kn = env.wind_speed_kn * profile.windage * profile.wind_lateral_gain * math.sin(relative_wind_rad) * damping
    wind_forward_kn = env.wind_speed_kn * profile.windage * profile.wind_forward_gain * math.cos(relative_wind_rad) * damping

    current_to = normalize_angle(env.current_from_deg + 180.0)
    current_north, current_east = vector_from_course(current_to, max(0.0, env.current_speed_kn))
    forward_north, forward_east = vector_from_course(heading, motion.stw_kn + wind_forward_kn)
    right_north, right_east = vector_from_course(normalize_angle(heading + 90.0), motion.sway_kn + wind_lateral_kn)

    north_kn = forward_north + right_north + current_north
    east_kn = forward_east + right_east + current_east
    cog, sog_abs = course_and_speed(north_kn, east_kn)
    heading_north, heading_east = vector_from_course(heading, 1.0)
    forward_component = north_kn * heading_north + east_kn * heading_east
    motion.cog_deg = cog if sog_abs > 1e-9 else heading
    motion.sog_kn = sog_abs if forward_component >= 0.0 else -sog_abs
    motion.longitudinal_water_kn = motion.stw_kn + wind_forward_kn

    right_north_unit, right_east_unit = vector_from_course(normalize_angle(heading + 90.0), 1.0)
    transverse_water = motion.sway_kn + wind_lateral_kn
    transverse_ground = transverse_water + current_north * right_north_unit + current_east * right_east_unit
    motion.transverse_water_kn = transverse_water
    motion.transverse_ground_kn = transverse_ground

    rot_rad_s = math.radians(motion.rate_of_turn_deg_min) / 60.0
    distance_to_bow_m = profile.length_m * profile.pivot_fraction_from_bow
    distance_to_stern_m = profile.length_m * (1.0 - profile.pivot_fraction_from_bow)
    bow_rot_kn = rot_rad_s * distance_to_bow_m / KNOT_TO_MPS
    stern_rot_kn = -rot_rad_s * distance_to_stern_m / KNOT_TO_MPS
    motion.forward_transverse_water_kn = transverse_water + bow_rot_kn
    motion.forward_transverse_ground_kn = transverse_ground + bow_rot_kn
    motion.aft_transverse_water_kn = transverse_water + stern_rot_kn
    motion.aft_transverse_ground_kn = transverse_ground + stern_rot_kn
    calculate_wind(state)


def move_by_ground_vector(state: SimulationState, dt_s: float) -> None:
    motion = state.motion
    distance_m = abs(motion.sog_kn) * KNOT_TO_MPS * max(0.0, dt_s)
    if distance_m <= 0.0:
        return
    course_rad = math.radians(motion.cog_deg)
    latitude = state.position.latitude_deg
    state.position.latitude_deg += distance_m * math.cos(course_rad) / 111_320.0
    cos_latitude = max(1e-6, abs(math.cos(math.radians(latitude))))
    state.position.longitude_deg += distance_m * math.sin(course_rad) / (111_320.0 * cos_latitude)
