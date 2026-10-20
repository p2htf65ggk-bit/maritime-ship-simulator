# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from maritime_simulator.domain.profiles import VesselProfile
from maritime_simulator.domain.enums import ControlMode, InputValidity
from maritime_simulator.domain.state import SimulationState, normalize_angle

from .environment import move_by_ground_vector, update_ground_vector
from .steering import clamp, update_steering
from .bow_thruster import integrate_bow_thruster


class VesselDynamics:
    """V2 vessel integration with no UI or protocol dependencies."""

    def integrate(self, state: SimulationState, profile: VesselProfile, dt_s: float) -> None:
        if dt_s <= 0.0:
            return

        target_speed = clamp(
            state.engine.effective_speed_order_kn,
            -profile.max_astern_kn,
            profile.max_speed_kn,
        )
        current_speed = state.motion.stw_kn
        if abs(target_speed - current_speed) < 0.01:
            current_speed = target_speed
        elif current_speed < target_speed:
            current_speed = min(target_speed, current_speed + profile.acceleration_kn_s * dt_s)
        else:
            current_speed = max(target_speed, current_speed - profile.deceleration_kn_s * dt_s)
        state.motion.stw_kn = current_speed
        thruster_rot, thruster_sway = integrate_bow_thruster(state, profile, dt_s)

        external_rsa = (
            state.control_mode is ControlMode.EXTERNAL_RUDDER
            and state.external_rudder.response_override
        )
        if external_rsa:
            state.steering.response_starboard_deg = clamp(
                state.external_rudder.response_starboard_deg,
                -profile.max_rudder_deg,
                profile.max_rudder_deg,
            )
            response_port = state.external_rudder.response_port_deg
            state.steering.response_port_deg = clamp(
                state.steering.response_starboard_deg if response_port is None else response_port,
                -profile.max_rudder_deg,
                profile.max_rudder_deg,
            )
        else:
            update_steering(state.steering, profile, dt_s)
        speed_factor = min(1.0, abs(current_speed) / max(1.0, profile.max_speed_kn))
        direction = -1.0 if current_speed < -0.05 else 1.0
        maximum_rot = profile.max_rot_deg_min * speed_factor
        if state.control_mode is ControlMode.TRACK:
            parameters = profile.track_parameters
            minimum_steer_speed = float(parameters.get("track_min_steer_speed", 0.0))
            if abs(current_speed) < minimum_steer_speed:
                maximum_rot = 0.0
            else:
                exponent = max(0.1, float(parameters.get("rot_speed_exponent", 1.0)))
                authority = max(0.0, float(parameters.get("track_rot_authority", 1.0)))
                maximum_rot = profile.max_rot_deg_min * (speed_factor**exponent) * authority
        effective_rudder_response = state.steering.response_starboard_deg
        if state.steering.dual_rudder:
            effective_rudder_response = 0.5 * (
                state.steering.response_starboard_deg + state.steering.response_port_deg
            )
        normalized_rudder = clamp(
            effective_rudder_response / max(0.1, profile.max_rudder_deg),
            -1.0,
            1.0,
        )
        if abs(normalized_rudder) > 1e-12:
            normalized_rudder = (
                1.0 if normalized_rudder > 0.0 else -1.0
            ) * abs(normalized_rudder) ** (1.0 / profile.rudder_effectiveness)
        target_rot = (
            normalized_rudder
            * maximum_rot
            * direction
        ) + thruster_rot
        rot_alpha = min(1.0, dt_s / max(0.1, profile.rot_time_constant_s))
        state.motion.rate_of_turn_deg_min += (target_rot - state.motion.rate_of_turn_deg_min) * rot_alpha
        state.motion.heading_deg = normalize_angle(
            state.motion.heading_deg + state.motion.rate_of_turn_deg_min * dt_s / 60.0
        )

        update_ground_vector(state, profile, dt_s, thruster_sway_kn=thruster_sway)
        state.auxiliary.propeller_lever_demand_percent = profile.lever_percent_from_speed(state.engine.effective_speed_order_kn)
        state.auxiliary.thruster_number = 1
        state.auxiliary.thruster_rpm_demand_percent = state.bow_thruster.order_percent
        state.auxiliary.thruster_rpm_response_percent = state.bow_thruster.response_percent
        state.auxiliary.thruster_pitch_demand_percent = 0.0
        state.auxiliary.thruster_pitch_response_percent = 0.0
        state.auxiliary.thruster_azimuth_demand_deg = 90.0
        state.auxiliary.thruster_azimuth_response_deg = 90.0
        move_by_ground_vector(state, dt_s)
        state.auxiliary.water_distance_nm += abs(state.motion.stw_kn) * dt_s / 3600.0
        state.auxiliary.ground_distance_nm += abs(state.motion.sog_kn) * dt_s / 3600.0
