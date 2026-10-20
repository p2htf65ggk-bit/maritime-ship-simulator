# SPDX-License-Identifier: MPL-2.0
import math
import logging
from pathlib import Path
import random
import sys
import time
import xml.etree.ElementTree as ET

from ui.protocols import default_protocol_configs


LOGGER = logging.getLogger(__name__)


DEFAULT_ROUTE_FILE = "default_route.rtz"
DEFAULT_RTZ_PATH = str(Path(__file__).resolve().parents[1] / "routes" / DEFAULT_ROUTE_FILE)
DEFAULT_RTZ_ALTERNATE_PATHS = (
    DEFAULT_RTZ_PATH,
    str(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "routes" / DEFAULT_ROUTE_FILE),
)


class Simulation:
    def __init__(self):
        self.lat = 52.0
        self.lon = 3.0

        self.heading = 90.0
        self.cog = 90.0
        self.cog_delta = 0.0
        self.cog_linked_to_heading = True
        self.speed = 0.0
        self.stw = 0.0
        self.sog = 0.0
        self.sway_speed = 0.0
        self.target_speed = 0.0
        self.rudder = 0.0
        self.rate_of_turn = 0.0
        self.target_rate_of_turn = 0.0
        self.track_turn_rate = 18.0
        self.track_rate_of_turn = self.track_turn_rate
        self.track_turn_mode = "fixed_rot"
        self.track_turn_radius_nm = 0.6
        self.track_progress_nm = 0.0
        self.track_end_action = "Stop"
        self.track_direction = 1
        self.simulation_time_scale = 1.0
        self.udp_output_mode = "auto"
        self.udp_multicast_ttl = 1
        self.udp_interface = ""
        self.xte_noise = 0.0
        self.xte_noise_enabled = False
        self.xte = 0.0
        self.barometric_pressure = 1.013
        self.air_temperature = 15.0
        self.water_temperature = 10.0
        self.wind_direction = 0.0
        self.wind_speed = 0.0
        self.current_direction = 0.0
        self.current_speed = 0.0
        self.current_set = (self.current_direction + 180.0) % 360.0
        self.current_drift = self.current_speed
        self.true_wind_direction = self.wind_direction
        self.true_wind_speed = self.wind_speed
        self.relative_humidity = 65.0
        self.absolute_humidity = 8.0
        self.dew_point = 8.0
        self.rudder_order = 0.0
        self.rudder_response = 0.0
        self.rsa_angle = 0.0
        self.rudder_order_port = 0.0
        self.rudder_response_port = 0.0
        self.rsa_angle_port = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.prc_lever_demand = 0.0
        self.thruster_number = 1
        self.thruster_rpm_demand = 0.0
        self.thruster_pitch_demand = 0.0
        self.thruster_azimuth_demand = 0.0
        self.thruster_rpm_response = 0.0
        self.thruster_pitch_response = 0.0
        self.thruster_azimuth_response = 0.0
        self.vbw_transverse_water = 0.0
        self.vbw_transverse_ground = 0.0
        self.vbw_fwd_transverse_water = 0.0
        self.vbw_fwd_transverse_ground = 0.0
        self.vbw_aft_transverse_water = 0.0
        self.vbw_aft_transverse_ground = 0.0
        self.additional_sensors = {
            "TRC": False,
            "TRD": False,
            "PRC": False,
            "ROR": False,
            "VBW_transversal": False,
            "RSA": True,
            "RUDDER2": False,
        }
        self._xte_noise_started = time.monotonic()
        self.route_waypoints = self._normalize_waypoints(self._default_route())
        if self.route_waypoints:
            first = self.route_waypoints[0]
            self.lat = first["lat"]
            self.lon = first["lon"]
            initial_course = self.route_course_at(0.0)
            if initial_course is not None:
                self.heading = initial_course
                self.cog = initial_course
        self.depth = 18.0
        self.protocol_configs = default_protocol_configs()
        self.performance_monitor = None
        self.control_mode = "sensor"
        self.recenter_requested = True
        self.position_history_reset_requested = True
        self.position_history_reset_token = 1
        self.sensor = {}
        self._last_sensor_heading = self.heading
        self._last_sensor_time = time.time()
        self._last_heading_noise = 0.0
        self._heading_noise_was_enabled = False
        self._filtered_sensor_rot = 0.0
        self._noise_state = {}
        self._track_pid_integral_m = 0.0
        self._track_pid_last_error_m = 0.0
        self._track_pid_last_heading_error = 0.0
        self._track_straight_bias_deg = 0.0
        self._track_desired_rot_filtered = 0.0
        self._track_speed_cap_kn = None
        self._track_active_leg_index = -1
        self._track_waypoint_index = 1
        self._track_last_phase = "idle"
        self.track_segment_state = {"phase": "idle"}
        self._track_wp_gate_index = None
        self._track_wp_gate_stall_ticks = 0
        self._track_wp_gate_stable_ticks = 0
        self._track_wp_gate_last_progress_nm = None
        self._track_wp_gate_last_phase = "idle"
        self._track_wp_gate_last_xte_m = None
        self._track_seek_hint_segment = None
        self._track_seek_window_ticks = 0
        self._track_advance_stats = {
            "advance_primary_count": 0,
            "advance_fallback_count": 0,
            "advance_block_marker_count": 0,
            "index_diff_to_progress_naive_max": 0,
        }
        self._last_turn_limit_warning_key = None
        self.vessel_models = self._create_vessel_models()
        self.vessel_model_name = "Crew Transfer Vessel"
        self.vessel_model = self.vessel_models[self.vessel_model_name]
        self.engine_steps = self._engine_steps_for_model(self.vessel_model)
        self.sync_track_turn_constraints()

        self.last_time = time.time()
        self.update_sensor_outputs()

    def set_position(self, lat, lon, reset_history=True):
        self.lat = lat
        self.lon = lon
        self.recenter_requested = True
        if reset_history:
            self.position_history_reset_requested = True
            self.position_history_reset_token += 1

    def update(self):
        monitor = getattr(self, "performance_monitor", None)
        started = time.perf_counter() if monitor and monitor.enabled else None
        iterations = 0
        try:
            now = time.time()
            real_dt = now - self.last_time
            self.last_time = now
            real_dt = max(0.0, min(real_dt, 1.0))
            time_scale = max(1.0, float(getattr(self, "simulation_time_scale", 1.0)))
            sim_dt_total = real_dt * time_scale
            if sim_dt_total <= 0.0:
                self.update_sensor_outputs()
                return

            # Keep per-step integration close to real-time behaviour and speed up by
            # running multiple steps per GUI update.
            base_step = max(0.02, real_dt if real_dt > 0.0 else 0.2)
            max_substep = min(0.25, base_step)
            remaining = sim_dt_total
            max_iterations = 800
            while remaining > 1e-9 and iterations < max_iterations:
                dt = min(max_substep, remaining)
                self._integrate_step(dt)
                remaining -= dt
                iterations += 1
            if remaining > 1e-9:
                self._integrate_step(remaining)
                iterations += 1
            self.update_sensor_outputs()
        finally:
            if monitor and monitor.enabled and started is not None:
                monitor.record_duration("simulation.update", time.perf_counter() - started)
                monitor.increment("simulation.ticks")
                monitor.set_gauge("simulation.substeps", iterations)

    def _integrate_step(self, dt):
        monitor = getattr(self, "performance_monitor", None)
        started = time.perf_counter() if monitor and monitor.enabled else None
        model = self.vessel_model
        try:
            acceleration = model["acceleration"]
            deceleration = model["deceleration"]
            effective_target_speed = self.target_speed

            if abs(self.speed - effective_target_speed) < 0.05:
                self.speed = effective_target_speed
            else:
                if self.speed < effective_target_speed:
                    self.speed = min(effective_target_speed, self.speed + acceleration * dt)
                else:
                    self.speed = max(effective_target_speed, self.speed - deceleration * dt)
            if self.control_mode in ("ship", "track"):
                self.stw = self.speed

            if self.control_mode == "ship":
                self._update_rudder_response(dt)

            # Rudder drives ROT in ship-control mode. Sensor-control mode may set ROT directly.
            if self.control_mode == "ship":
                water_speed = self.stw if abs(self.stw) > 0.05 else self.speed
                speed_factor = min(1.0, abs(water_speed) / max(1.0, model["max_speed"]))
                direction = -1.0 if water_speed < -0.05 else 1.0
                self.target_rate_of_turn = (
                    (self.rudder / model["max_rudder"])
                    * model["max_rot"]
                    * speed_factor
                    * direction
                )
                alpha = min(1.0, dt / model["rot_time_constant"])
                self.rate_of_turn += (self.target_rate_of_turn - self.rate_of_turn) * alpha
            elif self.control_mode == "track":
                self.sync_track_turn_constraints(abs(self.stw if abs(self.stw) > 0.05 else self.speed))
                self._update_track_control(dt)

            self.heading += self.rate_of_turn * dt / 60.0
            self.heading %= 360
            if self.cog_linked_to_heading:
                self.cog = (self.heading + self.cog_delta) % 360
            if self.control_mode in ("ship", "track"):
                self._update_ship_ground_vector(dt)
            else:
                self.sog = self.speed

            self._move_by_ground_vector(dt)
            if self.control_mode == "track" and self.route_distance_nm() > 0:
                self._update_track_progress_after_movement(dt)
        finally:
            if monitor and monitor.enabled and started is not None:
                monitor.record_duration("simulation.integrate_step", time.perf_counter() - started)

    def _update_track_control(self, dt):
        total = self.route_distance_nm()
        self._track_speed_cap_kn = None
        speed = abs(self.stw if abs(self.stw) > 0.05 else self.speed)
        model = self.vessel_model
        self.sync_track_turn_constraints(speed)
        next_waypoint = self._determine_track_waypoint_index(speed)
        available_rot = self._available_track_rot(speed)
        commanded_rot_limit = self._effective_track_turn_rate(speed, next_waypoint)
        if total <= 0 or speed < 0.05 or commanded_rot_limit <= 0.05:
            self._reset_track_controller()
            self.track_segment_state = {"phase": "idle"}
            self.rudder_order = 0.0
            self._update_rudder_response(dt)
            desired_rot = 0.0
        else:
            seg_start, seg_end = self._progress_search_window()
            _, xte_nm = self._nearest_route_progress(self.lat, self.lon, seg_start=seg_start, seg_end=seg_end)
            self.xte = xte_nm
            self._apply_route_leg_commands()
            next_waypoint = self._determine_track_waypoint_index(speed)
            available_rot = self._available_track_rot(speed)
            commanded_rot_limit = self._effective_track_turn_rate(speed, next_waypoint)
            if available_rot <= 0.05 or commanded_rot_limit <= 0.05:
                self.track_segment_state = {"phase": "idle"}
                self.rudder_order = 0.0
                self._update_rudder_response(dt)
                desired_rot = 0.0
            else:
                active_leg = max(0, min(next_waypoint - 1, len(self.route_waypoints) - 2))
                if active_leg != self._track_active_leg_index:
                    self._track_active_leg_index = active_leg
                    self._track_pid_integral_m = 0.0
                    self._track_pid_last_error_m = 0.0
                    self._track_pid_last_heading_error = 0.0
                    self._track_straight_bias_deg = 0.0

                phase_state = self._track_phase_state(speed, next_waypoint)
                previous_phase = self._track_last_phase
                self._track_last_phase = phase_state.get("phase", "idle")
                if previous_phase == "planned_turn" and self._track_last_phase == "outbound_straight":
                    self._track_pid_integral_m = 0.0
                    self._track_pid_last_error_m = 0.0
                self.track_segment_state = phase_state
                turn_geometry = phase_state.get("turn_geometry")
                turn_start_progress = (
                    turn_geometry.get("turn_start_progress_nm")
                    if isinstance(turn_geometry, dict)
                    else None
                )
                # Keep selected speed authoritative in Track mode. We no longer
                # auto-cap speed to satisfy planned turn geometry.

                compromise_path = self._overlap_compromise_path(turn_geometry)
                compromise_active = self._overlap_compromise_path_active(compromise_path)
                if compromise_active:
                    # The displayed compromise curve is the authoritative
                    # guidance path for an overlap group. Treat it as a planned
                    # turn so the normal pre-WOP gates cannot modify the
                    # command after the path follower has calculated it.
                    phase_state["phase"] = "planned_turn"
                    phase_state["conflict_path"] = True
                    self._track_last_phase = "planned_turn"
                    self._track_straight_bias_deg *= max(0.0, 1.0 - 0.8 * dt)
                    desired_rot = self._follow_overlap_compromise_path(
                        compromise_path,
                        dt,
                        commanded_rot_limit,
                    )
                    self._track_desired_rot_filtered = desired_rot
                elif phase_state.get("phase") == "planned_turn" and isinstance(turn_geometry, dict):
                    self._track_straight_bias_deg *= max(0.0, 1.0 - 0.8 * dt)
                    turn_target_rot = turn_geometry["turn_direction"] * turn_geometry["effective_rot_deg_min"]
                    desired_rot = turn_target_rot
                    center_xy = turn_geometry.get("turn_center_xy")
                    radius_nm = max(0.05, float(turn_geometry.get("turn_arc_radius_nm", turn_geometry["effective_radius_nm"])))
                    if isinstance(center_xy, tuple) and len(center_xy) == 2 and self.route_waypoints:
                        origin = self.route_waypoints[0]
                        ship_xy = self._local_xy_nm(origin["lat"], origin["lon"], self.lat, self.lon)
                        dx = ship_xy[0] - center_xy[0]
                        dy = ship_xy[1] - center_xy[1]
                        ship_radius_nm = max(1e-6, math.hypot(dx, dy))
                        radial_error_m = (ship_radius_nm - radius_nm) * 1852.0
                        radius_gain = float(model.get("track_turn_radius_gain", 0.010))
                        if radial_error_m > 0.0:
                            # If the vessel is outside the planned arc, increase
                            # radial pull-in to avoid being carried out of turn.
                            outside_boost = max(
                                0.0,
                                float(model.get("track_turn_outside_radius_boost", 0.0)),
                            )
                            outside_scale = min(1.0, radial_error_m / 120.0)
                            radius_gain *= 1.0 + outside_boost * outside_scale
                        radial_unit = (dx / ship_radius_nm, dy / ship_radius_nm)
                        if turn_geometry["turn_direction"] >= 0.0:
                            tangent_vector = (radial_unit[1], -radial_unit[0])
                        else:
                            tangent_vector = (-radial_unit[1], radial_unit[0])
                        tangent_course = self._vector_to_bearing(tangent_vector)
                        heading_error = ((tangent_course - self.heading + 180.0) % 360.0) - 180.0
                        heading_derivative = (heading_error - self._track_pid_last_heading_error) / max(0.001, dt)
                        self._track_pid_last_heading_error = heading_error
                        desired_rot += (
                            turn_geometry["turn_direction"] * radial_error_m * radius_gain
                            + heading_error * float(model.get("track_turn_heading_gain", 0.30))
                            + heading_derivative * float(model.get("track_turn_heading_derivative_gain", 0.05))
                        )
                    else:
                        outgoing_course = turn_geometry["outbound_course_deg"]
                        heading_error = ((outgoing_course - self.heading + 180.0) % 360.0) - 180.0
                        heading_derivative = (heading_error - self._track_pid_last_heading_error) / max(0.001, dt)
                        self._track_pid_last_heading_error = heading_error
                        turn_kp = model.get("track_turn_pid_kp", 0.24)
                        turn_kd = model.get("track_turn_pid_kd", 0.05)
                        desired_rot += turn_kp * heading_error + turn_kd * heading_derivative
                    desired_rot *= float(model.get("track_turn_rudder_gain", 1.0))
                    desired_rot = self._apply_turn_exit_brake(desired_rot, turn_geometry, commanded_rot_limit)
                    self._track_desired_rot_filtered = desired_rot
                else:
                    xte_error_m = xte_nm * 1852.0
                    xte_for_lookahead_m = abs(xte_error_m)
                    base_lookahead_nm = self._track_lookahead_distance(speed)
                    lookahead_scale = max(0.30, min(1.0, 1.0 - xte_for_lookahead_m / 220.0))
                    lookahead_nm = base_lookahead_nm * lookahead_scale
                    target_progress = min(total, self.track_progress_nm + lookahead_nm)
                    if turn_start_progress is not None and self.track_progress_nm < turn_start_progress:
                        target_progress = min(target_progress, max(self.track_progress_nm, turn_start_progress - 0.001))
                    target = self.route_position_at(target_progress)
                    if target is None:
                        desired_rot = 0.0
                    else:
                        route_course = phase_state.get("route_course_deg") or self.route_course_at(self.track_progress_nm) or self.heading
                        target_bearing = self._bearing(self.lat, self.lon, target[0], target[1])
                        bearing_error = ((target_bearing - route_course + 180.0) % 360.0) - 180.0
                        self._track_pid_integral_m += xte_error_m * dt
                        integral_limit = max(5.0, float(model.get("track_pid_i_limit_m", 120.0)))
                        self._track_pid_integral_m = max(-integral_limit, min(integral_limit, self._track_pid_integral_m))
                        xte_derivative_m = (xte_error_m - self._track_pid_last_error_m) / max(0.001, dt)
                        self._track_pid_last_error_m = xte_error_m
                        pid_kp = float(model.get("track_pid_kp", 0.012))
                        pid_ki = float(model.get("track_pid_ki", 0.0020))
                        pid_kd = float(model.get("track_pid_kd", 0.0040))
                        xte_heading_correction = (
                            pid_kp * xte_error_m
                            + pid_ki * self._track_pid_integral_m
                            + pid_kd * xte_derivative_m
                        )
                        if phase_state.get("phase") in ("straight_leg", "inbound_straight", "outbound_straight"):
                            bias_deadband_m = float(model.get("track_straight_bias_deadband_m", 1.5))
                            bias_ki = float(model.get("track_straight_bias_ki_deg_per_m_s", 0.00030))
                            bias_cog_gain = float(model.get("track_straight_cog_bias_gain", 0.025))
                            bias_leak = float(model.get("track_straight_bias_leak_per_s", 0.005))
                            bias_limit = float(model.get("track_straight_bias_limit_deg", 24.0))

                            bias_error_m = xte_error_m if abs(xte_error_m) > bias_deadband_m else 0.0
                            self._track_straight_bias_deg += bias_error_m * bias_ki * dt
                            self._track_straight_bias_deg += (-self.cog_delta) * bias_cog_gain * dt

                            if bias_error_m == 0.0:
                                self._track_straight_bias_deg *= max(0.0, 1.0 - bias_leak * dt)
                            else:
                                self._track_straight_bias_deg *= max(0.0, 1.0 - (bias_leak * 0.25) * dt)

                            self._track_straight_bias_deg = max(
                                -bias_limit,
                                min(bias_limit, self._track_straight_bias_deg),
                            )
                        else:
                            self._track_straight_bias_deg *= max(0.0, 1.0 - 0.6 * dt)
                        aggressiveness = min(2.2, max(0.70, commanded_rot_limit / max(0.1, model["max_rot"])))
                        if phase_state.get("phase") == "inbound_straight":
                            aggressiveness = min(2.8, aggressiveness * 1.22)
                        elif phase_state.get("phase") == "outbound_straight":
                            aggressiveness = min(3.4, aggressiveness * 1.65)
                        xte_heading_correction *= aggressiveness
                        intercept_heading = self._leg_rejoin_intercept_angle_deg(
                            xte_error_m,
                            phase_state.get("phase"),
                        )
                        xte_heading_correction += intercept_heading
                        if abs(intercept_heading) > 0.0:
                            if xte_heading_correction * intercept_heading < 0.0:
                                xte_heading_correction = intercept_heading
                            elif abs(xte_heading_correction) < abs(intercept_heading):
                                xte_heading_correction = intercept_heading
                        if phase_state.get("phase") in ("straight_leg", "inbound_straight", "outbound_straight"):
                            xte_limit = max(
                                float(model.get("track_xte_limit", 24.0)),
                                float(model.get("track_outbound_intercept_max_deg", 45.0)),
                            )
                        else:
                            xte_limit = float(model.get("track_xte_limit", 24.0))
                        xte_heading_correction = max(-xte_limit, min(xte_limit, xte_heading_correction))
                        bearing_correction = bearing_error * float(model.get("track_bearing_gain", 0.14))
                        drift_correction = -self.cog_delta * float(model.get("track_drift_gain", 0.92))
                        target_heading = (
                            route_course
                            + xte_heading_correction
                            + bearing_correction
                            + drift_correction
                            + self._track_straight_bias_deg
                        ) % 360.0
                        target_heading = self._clamp_leg_approach_heading(
                            route_course,
                            target_heading,
                            phase_state.get("phase"),
                        )
                        target_heading = self._rollout_leg_approach_heading(
                            route_course,
                            target_heading,
                            xte_error_m,
                            speed,
                            commanded_rot_limit,
                            phase_state.get("phase"),
                        )
                        heading_error = ((target_heading - self.heading + 180.0) % 360.0) - 180.0
                        heading_derivative = (heading_error - self._track_pid_last_heading_error) / max(0.001, dt)
                        self._track_pid_last_heading_error = heading_error
                        xte_rot_gain = float(model.get("track_xte_rot_gain", 0.0017))
                        if phase_state.get("phase") == "outbound_straight":
                            xte_rot_gain = float(model.get("track_outbound_xte_rot_gain", xte_rot_gain * 2.0))
                        xte_rot_correction = xte_error_m * xte_rot_gain * aggressiveness
                        desired_rot = (
                            heading_error * float(model.get("track_heading_gain", 4.8))
                            + heading_derivative * float(model.get("track_heading_derivative_gain", 0.18))
                            + xte_rot_correction
                            - self.rate_of_turn * float(model.get("track_rot_damping", 0.13))
                        )
                        leg_gain = float(model.get("track_leg_rudder_gain", 1.0))
                        if phase_state.get("phase") == "outbound_straight":
                            leg_gain *= float(model.get("track_outbound_rudder_gain", 1.0))
                        desired_rot *= max(0.2, leg_gain)
                        if phase_state.get("phase") in ("straight_leg", "inbound_straight", "outbound_straight"):
                            cog_error_route = abs(((float(route_course) - float(self.cog) + 180.0) % 360.0) - 180.0)
                            desired_rot *= self._straight_leg_small_error_gain(xte_error_m, cog_error_route)
                        if phase_state.get("phase") == "straight_leg":
                            straight_limit_fraction = float(model.get("track_straight_rot_limit_fraction", 0.92))
                            straight_limit_fraction = max(0.65, min(1.0, straight_limit_fraction))
                            desired_rot = max(
                                -commanded_rot_limit * straight_limit_fraction,
                                min(commanded_rot_limit * straight_limit_fraction, desired_rot),
                            )
                        if phase_state.get("phase") in ("straight_leg", "inbound_straight", "outbound_straight"):
                            if previous_phase not in ("straight_leg", "inbound_straight", "outbound_straight"):
                                self._track_desired_rot_filtered = desired_rot
                            base_tau = max(
                                0.0,
                                float(model.get("track_straight_rot_filter_tau_s", 0.0)),
                            )
                            if base_tau > 0.0:
                                xte_full_m = max(
                                    1.0,
                                    float(model.get("track_straight_rot_filter_xte_full_m", 90.0)),
                                )
                                hdg_full_deg = max(
                                    1.0,
                                    float(model.get("track_straight_rot_filter_hdg_full_deg", 8.0)),
                                )
                                urgency = max(
                                    0.0,
                                    min(
                                        1.0,
                                        max(abs(xte_error_m) / xte_full_m, abs(heading_error) / hdg_full_deg),
                                    ),
                                )
                                tau = base_tau * (1.0 - 0.75 * urgency)
                                alpha_filter = 1.0 if tau <= 1e-6 else min(1.0, dt / (tau + dt))
                                self._track_desired_rot_filtered += (
                                    desired_rot - self._track_desired_rot_filtered
                                ) * alpha_filter
                                desired_rot = self._track_desired_rot_filtered

                desired_rot = self._gate_pre_wop_turn(desired_rot, phase_state, commanded_rot_limit)
                desired_rot = max(-commanded_rot_limit, min(commanded_rot_limit, desired_rot))
                target_rudder = (
                    desired_rot
                    / max(0.1, available_rot)
                    * model["max_rudder"]
                )
                adaptive_limit = self._adaptive_leg_rudder_limit(phase_state, desired_rot)
                target_rudder = max(-adaptive_limit, min(adaptive_limit, target_rudder))
                pre_wop_limit = self._pre_wop_rudder_limit(phase_state, desired_rot)
                if pre_wop_limit is not None:
                    target_rudder = max(-pre_wop_limit, min(pre_wop_limit, target_rudder))
                self.rudder_order = max(-model["max_rudder"], min(model["max_rudder"], target_rudder))
                self._update_rudder_response(dt)
        self.target_rate_of_turn = desired_rot
        self.track_rate_of_turn = commanded_rot_limit
        water_speed = self.stw if abs(self.stw) > 0.05 else self.speed
        rudder_rot = (
            (self.rudder / model["max_rudder"])
            * self._available_track_rot(abs(water_speed))
        )
        self.target_rate_of_turn = max(-commanded_rot_limit, min(commanded_rot_limit, rudder_rot))
        alpha = min(1.0, dt / model["rot_time_constant"])
        self.rate_of_turn += (self.target_rate_of_turn - self.rate_of_turn) * alpha

    def _reset_track_controller(self):
        self._track_pid_integral_m = 0.0
        self._track_pid_last_error_m = 0.0
        self._track_pid_last_heading_error = 0.0
        self._track_straight_bias_deg = 0.0
        self._track_desired_rot_filtered = 0.0
        self._track_speed_cap_kn = None
        self._track_active_leg_index = -1
        self._track_waypoint_index = 1
        self._track_last_phase = "idle"
        self._track_wp_gate_index = None
        self._track_wp_gate_stall_ticks = 0
        self._track_wp_gate_stable_ticks = 0
        self._track_wp_gate_last_progress_nm = None
        self._track_wp_gate_last_phase = "idle"
        self._track_wp_gate_last_xte_m = None
        self._track_seek_hint_segment = None
        self._track_seek_window_ticks = 0
        self._track_conflict_path_key = None
        self._track_conflict_path_completed_key = None
        self._track_conflict_path_index = 0
        self._track_conflict_path_fraction = 0.0
        self._track_conflict_path_travel_nm = 0.0
        self._track_advance_stats = {
            "advance_primary_count": 0,
            "advance_fallback_count": 0,
            "advance_block_marker_count": 0,
            "index_diff_to_progress_naive_max": 0,
        }

    def _set_track_wp_gate_index(self, index):
        if self._track_wp_gate_index == index:
            return
        self._track_wp_gate_index = index
        self._track_wp_gate_stall_ticks = 0
        self._track_wp_gate_stable_ticks = 0
        self._track_wp_gate_last_progress_nm = None
        self._track_wp_gate_last_phase = self.track_segment_state.get("phase", "idle")
        self._track_wp_gate_last_xte_m = None

    def _record_track_advance_metric(self, key, amount=1):
        self._track_advance_stats[key] = self._track_advance_stats.get(key, 0) + int(amount)
        monitor = getattr(self, "performance_monitor", None)
        if monitor and monitor.enabled:
            monitor.increment(f"track.{key}", amount)
            monitor.set_gauge(f"track.{key}", self._track_advance_stats[key])

    def _update_track_index_diff_metric(self, index, ticks):
        naive = max(1, min(len(ticks) - 1, index))
        for candidate in range(1, len(ticks)):
            if self.track_progress_nm <= ticks[candidate] + 1e-6:
                naive = candidate
                break
        diff = abs(index - naive)
        if diff > self._track_advance_stats["index_diff_to_progress_naive_max"]:
            self._track_advance_stats["index_diff_to_progress_naive_max"] = diff
        monitor = getattr(self, "performance_monitor", None)
        if monitor and monitor.enabled:
            monitor.set_gauge(
                "track.index_diff_to_progress_naive_max",
                self._track_advance_stats["index_diff_to_progress_naive_max"],
            )

    def _resolve_track_waypoint_advance(
        self,
        index,
        advance_progress_nm,
        progress_gate_pass,
        marker_pass,
        fallback_progress_pass,
    ):
        if marker_pass and progress_gate_pass:
            self._record_track_advance_metric("advance_primary_count")
            self._set_track_wp_gate_index(index + 1)
            return True

        self._track_wp_gate_stall_ticks += 1
        if fallback_progress_pass and not marker_pass:
            self._record_track_advance_metric("advance_block_marker_count")

        phase = str(self.track_segment_state.get("phase", "idle"))
        xte_m = abs(float(getattr(self, "xte", 0.0))) * 1852.0
        overshoot_nm = max(0.0, float(self.track_progress_nm) - float(advance_progress_nm))
        force_overshoot_nm = max(
            0.004,
            float(self.vessel_model.get("track_wp_fallback_force_overshoot_nm", 0.03)),
        )
        force_fallback = overshoot_nm >= force_overshoot_nm
        phase_ok = phase in ("planned_turn", "outbound_straight", "straight_leg")
        phase_regressed = (
            self._track_wp_gate_last_phase in ("planned_turn", "outbound_straight")
            and phase == "inbound_straight"
        )
        progress_regressed = (
            self._track_wp_gate_last_progress_nm is not None
            and self.track_progress_nm + 0.0004 < self._track_wp_gate_last_progress_nm
        )
        progress_jump_nm = (
            0.0
            if self._track_wp_gate_last_progress_nm is None
            else max(0.0, self.track_progress_nm - self._track_wp_gate_last_progress_nm)
        )
        max_progress_jump_nm = max(
            0.02,
            float(self.vessel_model.get("track_wp_fallback_max_progress_step_nm", 0.25)),
        )
        progress_continuity_ok = (
            self._track_wp_gate_last_progress_nm is None
            or progress_jump_nm <= max_progress_jump_nm
        )
        xte_divergence_limit_m = max(
            10.0,
            float(self.vessel_model.get("track_wp_fallback_xte_divergence_m", 35.0)),
        )
        xte_diverging = (
            self._track_wp_gate_last_xte_m is not None
            and xte_m > self._track_wp_gate_last_xte_m + xte_divergence_limit_m
        )
        stall_threshold = max(
            1,
            int(self.vessel_model.get("track_wp_fallback_stall_ticks", 3)),
        )
        debounce_ticks = max(
            1,
            int(self.vessel_model.get("track_wp_fallback_debounce_ticks", 2)),
        )

        fallback_ok = (
            self._track_wp_gate_stall_ticks >= stall_threshold
            and fallback_progress_pass
            and (phase_ok or force_fallback)
            and not phase_regressed
            and not progress_regressed
            and progress_continuity_ok
            and (not xte_diverging or force_fallback)
        )
        if fallback_ok:
            self._track_wp_gate_stable_ticks += 1
        else:
            self._track_wp_gate_stable_ticks = 0

        self._track_wp_gate_last_progress_nm = self.track_progress_nm
        self._track_wp_gate_last_phase = phase
        self._track_wp_gate_last_xte_m = xte_m

        if self._track_wp_gate_stable_ticks >= debounce_ticks:
            self._record_track_advance_metric("advance_fallback_count")
            self._set_track_wp_gate_index(index + 1)
            return True
        return False

    def _update_rudder_response(self, dt):
        model = self.vessel_model
        max_rudder = float(model["max_rudder"])
        order = max(-max_rudder, min(max_rudder, float(getattr(self, "rudder_order", self.rudder))))
        self.rudder_order = order
        response = float(getattr(self, "rudder", 0.0))
        error = order - response
        if abs(error) < 0.02:
            self.rudder = order
            self.rudder_response = self.rudder
            self.rsa_angle = self.rudder
            return
        max_rate = float(model.get("rudder_rate_deg_s", max_rudder / max(1.0, model.get("rudder_full_travel_time", 14.0))))
        # Operational safety bound: hard-over crossing (hard port to hard stbd)
        # may be faster, but must never be slower than 30 seconds.
        max_crossing_time_s = float(model.get("rudder_hardover_crossing_time_max_s", 30.0))
        if max_crossing_time_s > 0.0:
            min_required_rate = (2.0 * max_rudder) / max_crossing_time_s
            max_rate = max(max_rate, min_required_rate)
        step = max(0.0, max_rate) * max(0.0, dt)
        if step <= 0.0:
            alpha = min(1.0, dt / max(0.1, model.get("track_rudder_time_constant", 1.0)))
            self.rudder += error * alpha
        else:
            self.rudder += max(-step, min(step, error))
        self.rudder = max(-max_rudder, min(max_rudder, self.rudder))
        self.rudder_response = self.rudder
        self.rsa_angle = self.rudder

    def _move_by_ground_vector(self, dt):
        speed_mps = abs(self.sog) * 0.5144
        distance = speed_mps * dt
        rad = math.radians(self.cog)
        self.lat += (distance * math.cos(rad)) / 111320
        self.lon += (distance * math.sin(rad)) / (
            111320 * math.cos(math.radians(self.lat))
        )

    def _update_track_progress_after_movement(self, dt):
        total = self.route_distance_nm()
        if total <= 0:
            return
        previous_progress = float(self.track_progress_nm)
        seg_start, seg_end = self._progress_search_window()
        projected_progress, projected_xte = self._nearest_route_progress(
            self.lat,
            self.lon,
            seg_start=seg_start,
            seg_end=seg_end,
        )
        travel_nm = max(0.0, abs(self.sog) * max(0.0, dt) / 3600.0)
        max_advance = max(0.0005, travel_nm * 2.2 + 0.0008)
        max_rewind = max(0.0010, travel_nm * 1.5 + 0.0015)
        clamped_progress = max(
            max(0.0, previous_progress - max_rewind),
            min(total, min(previous_progress + max_advance, projected_progress)),
        )
        self.track_progress_nm = clamped_progress
        self.xte = projected_xte
        final = self.route_waypoints[-1]
        distance_to_final = self._distance_nm(self.lat, self.lon, final["lat"], final["lon"])
        if self.track_progress_nm >= total - 0.02 and distance_to_final < 0.25:
            if self.track_end_action == "Restart":
                self.set_track_progress(0.0)
            elif self.track_end_action == "Reverse":
                self._reverse_route()
            else:
                self.track_progress_nm = total
                self.speed = 0.0
                self.target_speed = 0.0

    def _reverse_route(self):
        position = self.route_position_at(self.route_distance_nm())
        self.route_waypoints = list(reversed(self.route_waypoints))
        self.track_progress_nm = 0.0
        self.track_direction = 1
        self.heading = (self.heading + 180.0) % 360.0
        self.cog = (self.cog + 180.0) % 360.0
        self.rate_of_turn = 0.0
        self.target_rate_of_turn = 0.0
        self._reset_track_controller()
        if position:
            self.lat, self.lon = position
        self.recenter_requested = True
        self.set_track_progress(0.0)

    def reverse_route_preserve_position(self):
        if len(self.route_waypoints) < 2:
            return
        current_lat = self.lat
        current_lon = self.lon
        self.route_waypoints = list(reversed(self.route_waypoints))
        self.track_direction = 1
        self.rate_of_turn = 0.0
        self.target_rate_of_turn = 0.0
        self._reset_track_controller()
        self.lat = current_lat
        self.lon = current_lon
        nearest_progress, nearest_xte = self._nearest_route_progress(current_lat, current_lon)
        self.track_progress_nm = max(0.0, min(nearest_progress, self.route_distance_nm()))
        self.xte = nearest_xte
        speed_kn = abs(self.stw if abs(self.stw) > 0.05 else self.speed)
        self._track_waypoint_index = self._determine_track_waypoint_index(speed_kn=speed_kn)
        self._set_track_wp_gate_index(self._track_waypoint_index)
        self.track_segment_state = self._track_phase_state(speed_kn, self._track_waypoint_index)
        self._track_last_phase = self.track_segment_state.get("phase", "idle")
        self._track_seek_hint_segment = self._track_waypoint_index
        self._track_seek_window_ticks = max(
            4,
            int(self.vessel_model.get("track_seek_window_ticks", 30)),
        )
        self.recenter_requested = True

    def _wheel_over_progress(self, progress_nm):
        ticks = self.route_tick_positions()
        if len(ticks) < 3:
            return progress_nm
        for index in range(1, len(ticks) - 1):
            lead = self.wheel_over_distance_nm(index)
            if ticks[index] - lead <= progress_nm < ticks[index]:
                return min(ticks[-1], ticks[index] + 0.01)
        return progress_nm

    def _track_lookahead_progress(self, progress_nm):
        total = self.route_distance_nm()
        lookahead = self._track_lookahead_distance(abs(self.sog))
        return min(total, self._wheel_over_progress(progress_nm) + lookahead)

    def _track_lookahead_distance(self, speed=None):
        if speed is None:
            speed = abs(self.sog if abs(self.sog) > 0.05 else self.stw if abs(self.stw) > 0.05 else self.speed)
        model = self.vessel_model
        return max(
            model.get("track_lookahead_min", 0.2),
            min(model.get("track_lookahead_max", 2.0), max(0.0, speed) / 60.0 * model.get("track_lookahead_time", 3.0)),
        )

    def wheel_over_distance_nm(self, waypoint_index=None):
        if waypoint_index is None:
            waypoint_index = self._next_waypoint_index()
        course_change = self._route_course_change_at(waypoint_index)
        if course_change <= 2.0:
            return 0.0
        constraints = self.route_turn_constraints_for_waypoint(waypoint_index)
        speed = constraints["speed_kn"]
        turn_rate = constraints["effective_rot_deg_min"]
        radius_nm = constraints["effective_radius_nm"]
        if speed <= 0.05 or turn_rate <= 0.05 or radius_nm <= 0.0:
            return 0.0
        tangent_nm = radius_nm * math.tan(math.radians(min(course_change, 150.0)) / 2.0)
        reaction_nm = speed / 3600.0 * (
            self.vessel_model["rot_time_constant"]
            + self.vessel_model.get("track_rudder_time_constant", 1.0)
            + self.vessel_model.get("track_wheel_over_allowance_s", 0.0)
        )
        lead = tangent_nm + reaction_nm
        return max(0.0, min(self.vessel_model.get("track_wheel_over_max_nm", 8.0), lead))

    def _available_track_rot(self, speed=None):
        if speed is None:
            speed = abs(self.stw if abs(self.stw) > 0.05 else self.speed)
        speed = abs(float(speed))
        model = self.vessel_model
        min_speed = model.get("track_min_steer_speed", 0.4)
        if speed < min_speed:
            return 0.0
        ratio = min(1.0, speed / max(1.0, model["max_speed"]))
        ratio = ratio ** model.get("rot_speed_exponent", 1.0)
        return model["max_rot"] * ratio * model.get("track_rot_authority", 0.92)

    def _effective_track_turn_rate(self, speed=None, waypoint_index=None):
        return self.route_turn_constraints_for_waypoint(
            waypoint_index=waypoint_index,
            speed_kn=speed,
        )["effective_rot_deg_min"]

    def _turn_speed_cap_for_waypoint(self, waypoint_index, preferred_speed_kn=None):
        _ = (waypoint_index, preferred_speed_kn)
        return None

    def _should_apply_turn_speed_cap(self, phase_state, turn_speed_cap_kn, current_speed_kn):
        _ = (phase_state, turn_speed_cap_kn, current_speed_kn)
        return False

    def _turn_radius_nm(self, speed_kn, rot_deg_min):
        rot_rad_min = math.radians(max(0.01, abs(rot_deg_min)))
        return (abs(speed_kn) / 60.0) / rot_rad_min

    def _turn_rate_for_radius(self, speed_kn, radius_nm):
        radius = max(0.01, float(radius_nm))
        return math.degrees((abs(float(speed_kn)) / 60.0) / radius)

    def _route_turn_mode_for_waypoint(self, waypoint_index):
        if waypoint_index is None or waypoint_index < 0 or waypoint_index >= len(self.route_waypoints):
            return self.track_turn_mode
        mode = str(self.route_waypoints[waypoint_index].get("turn_mode", self.track_turn_mode)).strip().lower()
        return "fixed_radius" if mode == "fixed_radius" else "fixed_rot"

    def route_turn_constraints_for_waypoint(self, waypoint_index=None, speed_kn=None):
        if speed_kn is None:
            if waypoint_index is not None:
                speed_kn = self._route_speed_for_waypoint(waypoint_index)
            else:
                speed_kn = abs(self.stw if abs(self.stw) > 0.05 else self.speed)
        speed_kn = max(0.0, abs(float(speed_kn)))
        available_rot = self._available_track_rot(speed_kn)

        if waypoint_index is not None and 0 <= waypoint_index < len(self.route_waypoints):
            route_point = self.route_waypoints[waypoint_index]
        else:
            route_point = {}
        mode = self._route_turn_mode_for_waypoint(waypoint_index)

        requested_rot = self._optional_float(route_point.get("turn_rate"))
        if requested_rot is None:
            requested_rot = float(self.track_turn_rate)
        requested_rot = max(0.1, abs(float(requested_rot)))

        requested_radius = self._optional_float(route_point.get("turn_radius"))
        if requested_radius is None:
            requested_radius = float(getattr(self, "track_turn_radius_nm", 0.6))
        requested_radius = max(0.1, float(requested_radius))

        clamped = False
        warning = None
        if mode == "fixed_radius":
            if speed_kn > 0.01:
                desired_rot = self._turn_rate_for_radius(speed_kn, requested_radius)
            else:
                desired_rot = requested_rot
            effective_rot = max(0.0, desired_rot)
            effective_radius = requested_radius
            if speed_kn > 0.01 and effective_rot > available_rot + 1e-6:
                clamped = True
                warning = (
                    f"Turn radius {requested_radius:.2f} NM cannot be achieved at {speed_kn:.1f} kn "
                    f"with ROT limit {available_rot:.1f} deg/min; keeping requested radius for planning."
                )
        else:
            effective_rot = max(0.0, requested_rot)
            if speed_kn > 0.01 and effective_rot > 0.01:
                effective_radius = self._turn_radius_nm(speed_kn, effective_rot)
            else:
                effective_radius = requested_radius
            if speed_kn > 0.01 and effective_rot > available_rot + 1e-6:
                clamped = True
                warning = (
                    f"Requested ROT {requested_rot:.1f} deg/min exceeds available {available_rot:.1f} deg/min; "
                    f"keeping requested ROT for planning."
                )

        if warning:
            key = (
                waypoint_index,
                mode,
                round(requested_rot, 1),
                round(requested_radius, 2),
                bool(clamped),
            )
            if key != self._last_turn_limit_warning_key:
                LOGGER.warning(warning)
                self._last_turn_limit_warning_key = key

        return {
            "mode": mode,
            "speed_kn": speed_kn,
            "requested_rot_deg_min": requested_rot,
            "requested_radius_nm": requested_radius,
            "effective_rot_deg_min": max(0.0, effective_rot),
            "effective_radius_nm": max(0.0, effective_radius),
            "available_rot_deg_min": max(0.0, available_rot),
            "clamped": clamped,
            "warning": warning,
        }

    def route_turn_geometry(self, waypoint_index, speed_kn=None):
        if waypoint_index <= 0 or waypoint_index >= len(self.route_waypoints) - 1:
            return None
        ticks = self.route_tick_positions()
        if waypoint_index >= len(ticks):
            return None
        constraints = self.route_turn_constraints_for_waypoint(waypoint_index, speed_kn=speed_kn)
        course_change = self._route_course_change_at(waypoint_index)
        if course_change <= 2.0:
            return None
        prev_wp = self.route_waypoints[waypoint_index - 1]
        turn_wp = self.route_waypoints[waypoint_index]
        next_wp = self.route_waypoints[waypoint_index + 1]
        origin = self.route_waypoints[0]
        prev_xy = self._local_xy_nm(origin["lat"], origin["lon"], prev_wp["lat"], prev_wp["lon"])
        turn_xy = self._local_xy_nm(origin["lat"], origin["lon"], turn_wp["lat"], turn_wp["lon"])
        next_xy = self._local_xy_nm(origin["lat"], origin["lon"], next_wp["lat"], next_wp["lon"])

        inbound_v = (turn_xy[0] - prev_xy[0], turn_xy[1] - prev_xy[1])
        outbound_v = (next_xy[0] - turn_xy[0], next_xy[1] - turn_xy[1])
        inbound_len = math.hypot(inbound_v[0], inbound_v[1])
        outbound_len = math.hypot(outbound_v[0], outbound_v[1])
        if inbound_len <= 0.0 or outbound_len <= 0.0:
            return None
        inbound_unit = (inbound_v[0] / inbound_len, inbound_v[1] / inbound_len)
        outbound_unit = (outbound_v[0] / outbound_len, outbound_v[1] / outbound_len)

        inbound_course = self._bearing(prev_wp["lat"], prev_wp["lon"], turn_wp["lat"], turn_wp["lon"])
        outbound_course = self._bearing(turn_wp["lat"], turn_wp["lon"], next_wp["lat"], next_wp["lon"])
        signed_change = ((outbound_course - inbound_course + 180.0) % 360.0) - 180.0
        turn_direction = 1.0 if signed_change >= 0.0 else -1.0

        lead = self.wheel_over_distance_nm(waypoint_index)
        turn_point_progress = ticks[waypoint_index]
        start_progress = max(ticks[waypoint_index - 1], turn_point_progress - lead)
        end_progress = min(ticks[waypoint_index + 1], turn_point_progress + lead)
        init_progress = max(ticks[waypoint_index - 1], start_progress - self._turn_initialization_window_nm())
        start_point = self.route_position_at(start_progress)
        end_point = self.route_position_at(end_progress)
        init_point = self.route_position_at(init_progress)
        if start_point is None or end_point is None or init_point is None:
            return None
        start_xy = self._local_xy_nm(origin["lat"], origin["lon"], start_point[0], start_point[1])
        end_xy = self._local_xy_nm(origin["lat"], origin["lon"], end_point[0], end_point[1])

        if turn_direction >= 0.0:
            inbound_normal = (inbound_unit[1], -inbound_unit[0])
            outbound_normal = (outbound_unit[1], -outbound_unit[0])
        else:
            inbound_normal = (-inbound_unit[1], inbound_unit[0])
            outbound_normal = (-outbound_unit[1], outbound_unit[0])
        center_xy = self._line_intersection(start_xy, inbound_normal, end_xy, outbound_normal)
        if center_xy is None:
            radius_nm = max(0.05, constraints["effective_radius_nm"])
            center_xy = (
                start_xy[0] + inbound_normal[0] * radius_nm,
                start_xy[1] + inbound_normal[1] * radius_nm,
            )
        radius_from_start = math.hypot(start_xy[0] - center_xy[0], start_xy[1] - center_xy[1])
        radius_from_end = math.hypot(end_xy[0] - center_xy[0], end_xy[1] - center_xy[1])
        turn_radius_nm = max(0.05, (radius_from_start + radius_from_end) / 2.0)
        center_point = self._xy_to_latlon(origin["lat"], origin["lon"], center_xy[0], center_xy[1])
        arc_points = self._turn_arc_points(center_xy, start_xy, end_xy, turn_direction, origin["lat"], origin["lon"])

        return {
            "waypoint_index": waypoint_index,
            "inbound_vector": inbound_unit,
            "outbound_vector": outbound_unit,
            "turn_angle_deg": abs(signed_change),
            "turn_direction": turn_direction,
            "wheel_over_distance_nm": lead,
            "requested_radius_nm": constraints["requested_radius_nm"],
            "effective_radius_nm": constraints["effective_radius_nm"],
            "effective_rot_deg_min": constraints["effective_rot_deg_min"],
            "turn_start_progress_nm": start_progress,
            "turn_end_progress_nm": end_progress,
            "turn_init_progress_nm": init_progress,
            "turn_start_point": start_point,
            "turn_end_point": end_point,
            "turn_init_point": init_point,
            "turn_start_xy": start_xy,
            "turn_end_xy": end_xy,
            "turn_center_xy": center_xy,
            "turn_center_point": center_point,
            "turn_arc_points": arc_points,
            "turn_arc_radius_nm": turn_radius_nm,
            "inbound_course_deg": inbound_course,
            "outbound_course_deg": outbound_course,
            "clamped": constraints["clamped"],
            "warning": constraints["warning"],
        }

    def _is_past_marker(self, marker_point, direction_vector, tolerance_nm=0.0):
        if marker_point is None or not self.route_waypoints:
            return False
        origin = self.route_waypoints[0]
        marker_xy = self._local_xy_nm(origin["lat"], origin["lon"], marker_point[0], marker_point[1])
        ship_xy = self._local_xy_nm(origin["lat"], origin["lon"], self.lat, self.lon)
        along_nm = (
            (ship_xy[0] - marker_xy[0]) * direction_vector[0]
            + (ship_xy[1] - marker_xy[1]) * direction_vector[1]
        )
        return along_nm >= -max(0.0, tolerance_nm)

    def _track_phase_state(self, speed_kn, next_waypoint=None):
        if next_waypoint is None:
            next_waypoint = self._next_waypoint_index()
        route_course = self.route_course_at(self.track_progress_nm) or self.heading
        state = {
            "phase": "straight_leg",
            "next_waypoint_index": next_waypoint,
            "route_course_deg": route_course,
            "turn_geometry": None,
        }
        geometry = self.route_turn_geometry(next_waypoint, speed_kn=speed_kn)
        if geometry is None:
            return state
        state["turn_geometry"] = geometry
        start_reached = self._is_past_marker(
            geometry.get("turn_start_point"),
            geometry.get("inbound_vector", (0.0, 0.0)),
            tolerance_nm=0.0,
        )
        if not start_reached:
            state["phase"] = "inbound_straight"
            return state
        end_reached = self._is_past_marker(
            geometry.get("turn_end_point"),
            geometry.get("outbound_vector", (0.0, 0.0)),
            tolerance_nm=0.003,
        )
        if not end_reached:
            state["phase"] = "planned_turn"
            return state
        state["phase"] = "outbound_straight"
        state["route_course_deg"] = geometry["outbound_course_deg"]
        return state

    def _ship_length_nm(self):
        length_m = float(self.vessel_model.get("length_m", 100.0))
        return max(0.005, length_m / 1852.0)

    def _turn_initialization_window_nm(self):
        preinit_lengths = float(self.vessel_model.get("track_turn_preinit_ship_lengths", 1.0))
        return max(0.002, self._ship_length_nm() * max(0.25, preinit_lengths))

    def _marker_ahead_distance_nm(self, marker_point, direction_vector):
        if marker_point is None or not self.route_waypoints:
            return None
        origin = self.route_waypoints[0]
        marker_xy = self._local_xy_nm(origin["lat"], origin["lon"], marker_point[0], marker_point[1])
        ship_xy = self._local_xy_nm(origin["lat"], origin["lon"], self.lat, self.lon)
        return (
            (marker_xy[0] - ship_xy[0]) * direction_vector[0]
            + (marker_xy[1] - ship_xy[1]) * direction_vector[1]
        )

    def _gate_pre_wop_turn(self, desired_rot, phase_state, commanded_rot_limit):
        if not isinstance(phase_state, dict):
            return desired_rot
        if phase_state.get("phase") != "inbound_straight":
            return desired_rot
        geometry = phase_state.get("turn_geometry")
        if not isinstance(geometry, dict):
            return desired_rot
        inbound_vector = geometry.get("inbound_vector", (0.0, 0.0))
        init_ahead_nm = self._marker_ahead_distance_nm(geometry.get("turn_init_point"), inbound_vector)
        preinit_fraction = float(self.vessel_model.get("track_turn_preinit_rot_fraction", 0.18))
        preinit_min_rot = float(self.vessel_model.get("track_turn_preinit_rot_min", 1.2))
        preinit_max_rot = max(preinit_min_rot, commanded_rot_limit * max(0.02, min(0.95, preinit_fraction)))
        xte_m = abs(float(getattr(self, "xte", 0.0))) * 1852.0
        fast_entry_m = max(20.0, float(self.vessel_model.get("track_prewop_fast_rejoin_xte_entry_m", 40.0)))
        fast_full_m = max(fast_entry_m + 1.0, float(self.vessel_model.get("track_prewop_fast_rejoin_xte_full_m", 120.0)))
        if xte_m <= fast_entry_m:
            prewop_rot_cap = preinit_max_rot
        elif xte_m >= fast_full_m:
            prewop_rot_cap = commanded_rot_limit
        else:
            t = (xte_m - fast_entry_m) / (fast_full_m - fast_entry_m)
            prewop_rot_cap = preinit_max_rot + (commanded_rot_limit - preinit_max_rot) * t
        # Do not initiate turn-direction rudder before we are abeam of the
        # initialisation marker (displayed WOP).
        if init_ahead_nm is not None and init_ahead_nm > 0.0:
            # Pure magnitude gating before WOP: symmetric for both rudder
            # directions so large-XTE capture is not direction-dependent.
            return max(-prewop_rot_cap, min(prewop_rot_cap, desired_rot))

        ahead_nm = self._marker_ahead_distance_nm(geometry.get("turn_start_point"), inbound_vector)
        if ahead_nm is None or ahead_nm <= 0.0:
            return desired_rot
        preinit_window_nm = self._turn_initialization_window_nm()

        if ahead_nm > preinit_window_nm:
            return max(-prewop_rot_cap, min(prewop_rot_cap, desired_rot))

        ramp = 1.0 - max(0.0, min(1.0, ahead_nm / preinit_window_nm))
        allowed_rot = prewop_rot_cap + (commanded_rot_limit - prewop_rot_cap) * ramp
        allowed_rot = max(prewop_rot_cap, min(commanded_rot_limit, allowed_rot))
        return max(-allowed_rot, min(allowed_rot, desired_rot))

    def _pre_wop_rudder_limit(self, phase_state, desired_rot=None):
        if not isinstance(phase_state, dict) or phase_state.get("phase") != "inbound_straight":
            return None
        # Never cap counter-rudder: we need full authority to arrest yaw and
        # capture the leg cleanly.
        if desired_rot is not None:
            rot_now = float(getattr(self, "rate_of_turn", 0.0))
            if abs(rot_now) > 0.2 and (float(desired_rot) * rot_now) < 0.0:
                return None
        geometry = phase_state.get("turn_geometry")
        if not isinstance(geometry, dict):
            return None
        init_ahead_nm = self._marker_ahead_distance_nm(
            geometry.get("turn_init_point"),
            geometry.get("inbound_vector", (0.0, 0.0)),
        )
        if init_ahead_nm is not None and init_ahead_nm > 0.0:
            return None
        ahead_nm = self._marker_ahead_distance_nm(
            geometry.get("turn_start_point"),
            geometry.get("inbound_vector", (0.0, 0.0)),
        )
        if ahead_nm is None or ahead_nm <= 0.0:
            return None
        preinit_window_nm = self._turn_initialization_window_nm()
        if ahead_nm <= preinit_window_nm:
            # Turn initialisation window: controller may fully control rudder.
            return None
        max_rudder = float(self.vessel_model["max_rudder"])
        min_fraction = float(self.vessel_model.get("track_turn_preinit_rudder_fraction", 0.30))
        min_rudder = max(3.0, max_rudder * max(0.05, min(1.0, min_fraction)))
        xte_m = abs(float(getattr(self, "xte", 0.0))) * 1852.0
        full_entry_m = max(20.0, float(self.vessel_model.get("track_prewop_rudder_full_xte_entry_m", 50.0)))
        full_m = max(full_entry_m + 1.0, float(self.vessel_model.get("track_prewop_rudder_full_xte_m", 130.0)))
        if xte_m <= full_entry_m:
            return min_rudder
        if xte_m >= full_m:
            return max_rudder
        t = (xte_m - full_entry_m) / (full_m - full_entry_m)
        return min_rudder + (max_rudder - min_rudder) * t

    def _is_turn_initialization_window(self, phase_state):
        if not isinstance(phase_state, dict) or phase_state.get("phase") != "inbound_straight":
            return False
        geometry = phase_state.get("turn_geometry")
        if not isinstance(geometry, dict):
            return False
        init_ahead_nm = self._marker_ahead_distance_nm(
            geometry.get("turn_init_point"),
            geometry.get("inbound_vector", (0.0, 0.0)),
        )
        if init_ahead_nm is None or init_ahead_nm > 0.0:
            return False
        ahead_nm = self._marker_ahead_distance_nm(
            geometry.get("turn_start_point"),
            geometry.get("inbound_vector", (0.0, 0.0)),
        )
        if ahead_nm is None or ahead_nm <= 0.0:
            return False
        preinit_window_nm = self._turn_initialization_window_nm()
        return ahead_nm <= preinit_window_nm

    def _adaptive_leg_rudder_limit(self, phase_state, desired_rot=None):
        max_rudder = float(self.vessel_model["max_rudder"])
        if not isinstance(phase_state, dict):
            return max_rudder
        phase = phase_state.get("phase")
        if phase == "planned_turn" or self._is_turn_initialization_window(phase_state):
            return max_rudder

        # Do not limit counter-rudder when we need to arrest active yaw while
        # rejoining the leg.
        if desired_rot is not None:
            rot_now = float(getattr(self, "rate_of_turn", 0.0))
            counter_unlock_rot_min = max(
                0.2,
                float(self.vessel_model.get("track_counterrudder_unlock_rot_deg_min", 1.0)),
            )
            if abs(rot_now) >= counter_unlock_rot_min and (float(desired_rot) * rot_now) < 0.0:
                if phase in ("straight_leg", "inbound_straight", "outbound_straight"):
                    return max_rudder
                return max_rudder

        route_course = phase_state.get("route_course_deg")
        if route_course is None:
            return max_rudder

        cog_error = abs(((float(route_course) - float(self.cog) + 180.0) % 360.0) - 180.0)
        xte_m = abs(float(getattr(self, "xte", 0.0))) * 1852.0

        soft_xte = float(self.vessel_model.get("track_rudder_soft_xte_m", 8.0))
        hard_xte = float(self.vessel_model.get("track_rudder_hard_xte_m", 120.0))
        soft_cog = float(self.vessel_model.get("track_rudder_soft_cog_error_deg", 1.5))
        hard_cog = float(self.vessel_model.get("track_rudder_hard_cog_error_deg", 24.0))

        def _scaled(value, soft, hard):
            if hard <= soft:
                return 1.0 if value >= hard else 0.0
            return max(0.0, min(1.0, (value - soft) / (hard - soft)))

        xte_factor = _scaled(xte_m, soft_xte, hard_xte)
        cog_factor = _scaled(cog_error, soft_cog, hard_cog)
        demand = max(xte_factor, cog_factor)

        min_fraction = float(self.vessel_model.get("track_rudder_min_fraction", 0.18))
        min_fraction = max(0.05, min(1.0, min_fraction))
        allowed_fraction = min_fraction + (1.0 - min_fraction) * demand
        return max_rudder * max(min_fraction, min(1.0, allowed_fraction))

    def _straight_leg_small_error_gain(self, xte_error_m, cog_error_deg):
        # Soft shaping only: reduce rudder in the small-error zone
        # (|XTE| < 0.1 NM and |course delta| < ~2 deg) without hard clipping.
        xte_soft_m = max(10.0, float(self.vessel_model.get("track_small_error_xte_m", 185.2)))
        cog_soft_deg = max(0.5, float(self.vessel_model.get("track_small_error_cog_deg", 2.0)))
        min_gain = max(0.30, min(1.0, float(self.vessel_model.get("track_small_error_rudder_gain_min", 0.62))))
        abs_xte_m = abs(float(xte_error_m))
        abs_cog_deg = abs(float(cog_error_deg))
        if abs_xte_m >= xte_soft_m or abs_cog_deg >= cog_soft_deg:
            return 1.0
        xte_blend = max(0.0, min(1.0, abs_xte_m / xte_soft_m))
        cog_blend = max(0.0, min(1.0, abs_cog_deg / cog_soft_deg))
        blend = max(xte_blend, cog_blend)
        return min_gain + (1.0 - min_gain) * blend

    def _clamp_leg_approach_heading(self, route_course_deg, target_heading_deg, phase):
        if phase not in ("straight_leg", "inbound_straight", "outbound_straight"):
            return float(target_heading_deg) % 360.0
        course = float(route_course_deg) % 360.0
        target = float(target_heading_deg) % 360.0
        raw_error = ((target - course + 180.0) % 360.0) - 180.0
        max_approach = max(5.0, min(45.0, float(self.vessel_model.get("track_leg_approach_max_deg", 45.0))))
        clamped_error = max(-max_approach, min(max_approach, raw_error))
        return (course + clamped_error) % 360.0

    def _rollout_leg_approach_heading(
        self,
        route_course_deg,
        target_heading_deg,
        xte_error_m,
        speed_kn,
        rot_limit_deg_min,
        phase,
    ):
        if phase not in ("straight_leg", "inbound_straight", "outbound_straight"):
            return float(target_heading_deg) % 360.0
        course = float(route_course_deg) % 360.0
        target = float(target_heading_deg) % 360.0
        err_deg = ((target - course + 180.0) % 360.0) - 180.0
        abs_err_deg = abs(err_deg)
        if abs_err_deg < 1.0:
            return target

        abs_xte_m = abs(float(xte_error_m))
        if abs_xte_m < 1.0:
            return course

        abs_err_rad = math.radians(abs_err_deg)
        sin_err = max(1e-6, math.sin(abs_err_rad))
        distance_to_leg_along_m = abs_xte_m / sin_err

        if abs(float(rot_limit_deg_min)) <= 0.05 or abs(float(speed_kn)) <= 0.05:
            return target
        turn_radius_m = max(1.0, self._turn_radius_nm(abs(float(speed_kn)), abs(float(rot_limit_deg_min))) * 1852.0)
        wheel_over_m = turn_radius_m * math.tan(abs_err_rad / 2.0)
        rollout_margin = max(0.5, float(self.vessel_model.get("track_leg_rollout_wop_margin", 1.0)))
        rollout_start_m = wheel_over_m * rollout_margin
        # For steep rejoin angles (e.g. ~45°), start alignment earlier so the
        # vessel can settle on leg bearing before crossing the leg.
        rollout_extra_radius_fraction = max(
            0.0,
            float(self.vessel_model.get("track_leg_rollout_extra_radius_fraction", 0.30)),
        )
        angle_scale = max(0.0, min(1.0, abs_err_deg / 45.0))
        rollout_soft_xte_m = max(
            20.0,
            float(self.vessel_model.get("track_leg_rollout_extra_xte_m", 185.2)),
        )
        xte_scale = max(0.0, min(1.0, 1.0 - (abs_xte_m / rollout_soft_xte_m)))
        rollout_start_m += turn_radius_m * rollout_extra_radius_fraction * angle_scale * xte_scale
        if distance_to_leg_along_m >= rollout_start_m:
            return target

        blend = 1.0 - max(0.0, min(1.0, distance_to_leg_along_m / max(1.0, rollout_start_m)))
        rollout_err = err_deg * (1.0 - blend)
        return (course + rollout_err) % 360.0

    def _outbound_intercept_angle_deg(self, xte_error_m):
        intercept_cap = max(5.0, float(self.vessel_model.get("track_outbound_intercept_max_deg", 45.0)))
        intercept_dist_m = max(20.0, float(self.vessel_model.get("track_outbound_intercept_xte_m", 220.0)))
        raw = (float(xte_error_m) / intercept_dist_m) * intercept_cap
        return max(-intercept_cap, min(intercept_cap, raw))

    def _leg_rejoin_intercept_angle_deg(self, xte_error_m, phase):
        if phase not in ("straight_leg", "inbound_straight", "outbound_straight"):
            return 0.0
        xte_abs_m = abs(float(xte_error_m))
        # Keep PID-dominant behaviour near the track and blend into explicit
        # intercept-angle behaviour only for larger offsets.
        entry_m = max(5.0, float(self.vessel_model.get("track_intercept_entry_xte_m", 30.0)))
        if xte_abs_m < entry_m:
            return 0.0
        intercept = self._outbound_intercept_angle_deg(xte_error_m)
        soft_xte_m = max(20.0, float(self.vessel_model.get("track_intercept_soft_xte_m", 185.2)))
        if xte_abs_m < soft_xte_m:
            t = max(0.0, min(1.0, xte_abs_m / soft_xte_m))
            power = max(1.0, float(self.vessel_model.get("track_intercept_soft_power", 1.7)))
            min_fraction = max(0.0, min(1.0, float(self.vessel_model.get("track_intercept_soft_min_fraction", 0.28))))
            intercept *= min_fraction + (1.0 - min_fraction) * (t ** power)
        return intercept

    def _apply_turn_exit_brake(self, desired_rot, turn_geometry, commanded_rot_limit):
        if not isinstance(turn_geometry, dict):
            return desired_rot
        turn_direction = float(turn_geometry.get("turn_direction", 0.0))
        if turn_direction == 0.0:
            return desired_rot
        start_progress = float(turn_geometry.get("turn_start_progress_nm", self.track_progress_nm))
        end_progress = float(turn_geometry.get("turn_end_progress_nm", self.track_progress_nm))
        overlap_transition = self._overlap_pretransition_rot(
            desired_rot,
            turn_geometry,
            commanded_rot_limit,
        )
        if overlap_transition is not None:
            return overlap_transition
        segment_len = max(1e-6, end_progress - start_progress)
        rel_progress = (self.track_progress_nm - start_progress) / segment_len
        brake_arm_fraction = float(self.vessel_model.get("track_turn_brake_arm_fraction", 0.80))
        if rel_progress < max(0.0, min(0.98, brake_arm_fraction)):
            return desired_rot
        outbound_course = turn_geometry.get("outbound_course_deg")
        if outbound_course is None:
            return desired_rot
        heading_error_out = ((float(outbound_course) - self.heading + 180.0) % 360.0) - 180.0
        alignment_deg = turn_direction * heading_error_out
        rot_abs = abs(float(self.rate_of_turn))
        brake_lead_per_rot = float(self.vessel_model.get("track_turn_brake_lead_per_rot", 0.16))
        # Predictive lead: when still rotating quickly, initiate brake earlier.
        effective_alignment = alignment_deg - rot_abs * brake_lead_per_rot
        brake_base = float(self.vessel_model.get("track_turn_brake_base_deg", 4.0))
        brake_per_rot = float(self.vessel_model.get("track_turn_brake_per_rot_deg", 0.45))
        brake_window_deg = max(2.0, brake_base + rot_abs * brake_per_rot)
        overshoot_full_deg = max(2.0, float(self.vessel_model.get("track_turn_overshoot_full_deg", 6.0)))

        # Already at / beyond outbound heading: apply counter-rudder to arrest yaw.
        if effective_alignment <= 0.0:
            overshoot = min(1.0, abs(effective_alignment) / overshoot_full_deg)
            rot_scale = min(1.0, rot_abs / max(0.1, commanded_rot_limit))
            counter_mix = max(0.50, min(1.0, 0.58 + 0.34 * rot_scale + 0.24 * overshoot))
            counter_target = -turn_direction * min(
                commanded_rot_limit,
                max(commanded_rot_limit * 0.40, rot_abs * 1.20),
            )
            return (1.0 - counter_mix) * desired_rot + counter_mix * counter_target

        # Near outbound heading: progressively pre-brake to avoid overshoot.
        if effective_alignment < brake_window_deg:
            t = 1.0 - max(0.0, min(1.0, effective_alignment / brake_window_deg))
            counter_target = -turn_direction * min(
                commanded_rot_limit * 0.85,
                max(commanded_rot_limit * 0.22, rot_abs * 0.95),
            )
            return (1.0 - t) * desired_rot + t * counter_target
        return desired_rot

    def _overlap_pretransition_rot(self, desired_rot, turn_geometry, commanded_rot_limit):
        """Blend the next turn from its displayed WOP through the shared conflict marker."""
        waypoint_index = int(turn_geometry.get("waypoint_index", -1))
        next_index = waypoint_index + 1
        if next_index <= 0 or next_index >= len(self.route_waypoints) - 1:
            return None
        next_geometry = self.route_turn_geometry(
            next_index,
            speed_kn=self._route_speed_for_waypoint(next_index),
        )
        if not isinstance(next_geometry, dict):
            return None
        current_end = float(turn_geometry.get("turn_end_progress_nm", 0.0))
        next_start = float(next_geometry.get("turn_start_progress_nm", current_end))
        if next_start >= current_end:
            return None
        transition = (current_end + next_start) / 2.0
        initialization = float(next_geometry.get("turn_init_progress_nm", next_start))
        if self.track_progress_nm < initialization or self.track_progress_nm > transition:
            return None
        span = max(0.001, transition - initialization)
        blend = max(0.0, min(1.0, (self.track_progress_nm - initialization) / span))
        blend = blend * blend * (3.0 - 2.0 * blend)
        next_direction = float(next_geometry.get("turn_direction", 0.0))
        next_rate = min(
            commanded_rot_limit,
            abs(float(next_geometry.get("effective_rot_deg_min", commanded_rot_limit))),
        )
        target = next_direction * next_rate
        return desired_rot * (1.0 - blend) + target * blend

    def _build_overlap_compromise_path(self, current_geometry, next_geometry):
        """Build one smooth, flyable path through the shared average marker."""
        if not isinstance(current_geometry, dict) or not isinstance(next_geometry, dict):
            return None
        current_end = float(current_geometry.get("turn_end_progress_nm", 0.0))
        next_start = float(next_geometry.get("turn_start_progress_nm", current_end))
        if next_start >= current_end or not self.route_waypoints:
            return None
        start_progress = float(current_geometry.get("turn_start_progress_nm", next_start))
        end_progress = float(next_geometry.get("turn_end_progress_nm", current_end))
        if end_progress <= start_progress:
            return None
        start = current_geometry.get("turn_start_point") or self.route_position_at(start_progress)
        end = next_geometry.get("turn_end_point") or self.route_position_at(end_progress)
        average_progress = (current_end + next_start) / 2.0
        average = self.route_position_at(average_progress)
        if start is None or end is None or average is None:
            return None

        origin = self.route_waypoints[0]
        p0 = self._local_xy_nm(origin["lat"], origin["lon"], start[0], start[1])
        p3 = self._local_xy_nm(origin["lat"], origin["lon"], end[0], end[1])
        pm = self._local_xy_nm(origin["lat"], origin["lon"], average[0], average[1])
        span = max(0.001, math.hypot(p3[0] - p0[0], p3[1] - p0[1]))
        inbound = current_geometry.get("inbound_vector", (0.0, 1.0))
        outbound = next_geometry.get("outbound_vector", (0.0, 1.0))
        arm = span * 0.32
        p1 = (p0[0] + float(inbound[0]) * arm, p0[1] + float(inbound[1]) * arm)
        p2 = (p3[0] - float(outbound[0]) * arm, p3[1] - float(outbound[1]) * arm)

        # A cubic Bezier evaluates to 1/8 P0 + 3/8 P1 + 3/8 P2 + 1/8 P3
        # at t=0.5. Shift both control points equally so the displayed average
        # transition is part of the path, while retaining smooth end tangents.
        required_sum = (
            (8.0 * pm[0] - p0[0] - p3[0]) / 3.0,
            (8.0 * pm[1] - p0[1] - p3[1]) / 3.0,
        )
        shift = (
            (required_sum[0] - p1[0] - p2[0]) / 2.0,
            (required_sum[1] - p1[1] - p2[1]) / 2.0,
        )
        p1 = (p1[0] + shift[0], p1[1] + shift[1])
        p2 = (p2[0] + shift[0], p2[1] + shift[1])

        xy_points = []
        points = []
        for index in range(41):
            t = index / 40.0
            one = 1.0 - t
            x = one ** 3 * p0[0] + 3.0 * one * one * t * p1[0] + 3.0 * one * t * t * p2[0] + t ** 3 * p3[0]
            y = one ** 3 * p0[1] + 3.0 * one * one * t * p1[1] + 3.0 * one * t * t * p2[1] + t ** 3 * p3[1]
            xy_points.append((x, y))
            lat, lon = self._xy_to_latlon(origin["lat"], origin["lon"], x, y)
            points.append((lat, lon))
        return {
            "pair": (int(current_geometry.get("waypoint_index", -1)), int(next_geometry.get("waypoint_index", -1))),
            "start_progress_nm": start_progress,
            "end_progress_nm": end_progress,
            "average_progress_nm": average_progress,
            "average_point": average,
            "xy_points": xy_points,
            "points": points,
        }

    def _overlap_compromise_path(self, turn_geometry):
        if not isinstance(turn_geometry, dict):
            return None
        waypoint_index = int(turn_geometry.get("waypoint_index", -1))
        if waypoint_index <= 0:
            return None
        group = [turn_geometry]
        while int(group[0].get("waypoint_index", waypoint_index)) > 1:
            previous_index = int(group[0]["waypoint_index"]) - 1
            previous = self.route_turn_geometry(previous_index, speed_kn=self._route_speed_for_waypoint(previous_index))
            if not isinstance(previous, dict) or float(previous.get("turn_end_progress_nm", 0.0)) <= float(group[0].get("turn_start_progress_nm", 0.0)):
                break
            group.insert(0, previous)
        while int(group[-1].get("waypoint_index", waypoint_index)) < len(self.route_waypoints) - 2:
            following_index = int(group[-1]["waypoint_index"]) + 1
            following = self.route_turn_geometry(following_index, speed_kn=self._route_speed_for_waypoint(following_index))
            if not isinstance(following, dict) or float(following.get("turn_start_progress_nm", 0.0)) >= float(group[-1].get("turn_end_progress_nm", 0.0)):
                break
            group.append(following)
        return self._build_overlap_compromise_group(group)

    def _build_overlap_compromise_group(self, geometries):
        """Create one continuous Hermite path for a complete overlap group."""
        geometries = [geometry for geometry in geometries if isinstance(geometry, dict)]
        if len(geometries) < 2 or not self.route_waypoints:
            return None
        if len(geometries) == 2:
            return self._build_overlap_compromise_path(geometries[0], geometries[1])
        origin = self.route_waypoints[0]
        start_progress = float(geometries[0]["turn_start_progress_nm"])
        end_progress = float(geometries[-1]["turn_end_progress_nm"])
        start = geometries[0].get("turn_start_point") or self.route_position_at(start_progress)
        end = geometries[-1].get("turn_end_point") or self.route_position_at(end_progress)
        if start is None or end is None:
            return None
        anchor_points = [start]
        average_progresses = []
        for current, following in zip(geometries, geometries[1:]):
            average_progress = (
                float(current["turn_end_progress_nm"]) + float(following["turn_start_progress_nm"])
            ) / 2.0
            average = self.route_position_at(average_progress)
            if average is None:
                return None
            average_progresses.append(average_progress)
            anchor_points.append(average)
        anchor_points.append(end)
        anchors = [
            self._local_xy_nm(origin["lat"], origin["lon"], point[0], point[1])
            for point in anchor_points
        ]

        tangents = []
        for index, anchor in enumerate(anchors):
            if index == 0:
                vector = geometries[0].get("inbound_vector", (0.0, 1.0))
                magnitude = math.hypot(anchors[1][0] - anchor[0], anchors[1][1] - anchor[1]) * 0.65
            elif index == len(anchors) - 1:
                vector = geometries[-1].get("outbound_vector", (0.0, 1.0))
                magnitude = math.hypot(anchor[0] - anchors[index - 1][0], anchor[1] - anchors[index - 1][1]) * 0.65
            else:
                vx = anchors[index + 1][0] - anchors[index - 1][0]
                vy = anchors[index + 1][1] - anchors[index - 1][1]
                length = max(1e-9, math.hypot(vx, vy))
                vector = (vx / length, vy / length)
                before = math.hypot(anchor[0] - anchors[index - 1][0], anchor[1] - anchors[index - 1][1])
                after = math.hypot(anchors[index + 1][0] - anchor[0], anchors[index + 1][1] - anchor[1])
                magnitude = min(before, after) * 0.65
            tangents.append((float(vector[0]) * magnitude, float(vector[1]) * magnitude))

        xy_points = []
        for segment in range(len(anchors) - 1):
            p0, p1 = anchors[segment], anchors[segment + 1]
            m0, m1 = tangents[segment], tangents[segment + 1]
            for step in range(13):
                if segment and step == 0:
                    continue
                t = step / 12.0
                t2, t3 = t * t, t * t * t
                h00 = 2.0 * t3 - 3.0 * t2 + 1.0
                h10 = t3 - 2.0 * t2 + t
                h01 = -2.0 * t3 + 3.0 * t2
                h11 = t3 - t2
                xy_points.append((
                    h00 * p0[0] + h10 * m0[0] + h01 * p1[0] + h11 * m1[0],
                    h00 * p0[1] + h10 * m0[1] + h01 * p1[1] + h11 * m1[1],
                ))
        points = [
            self._xy_to_latlon(origin["lat"], origin["lon"], point[0], point[1])
            for point in xy_points
        ]
        return {
            "pair": tuple(int(geometry.get("waypoint_index", -1)) for geometry in geometries),
            "start_progress_nm": start_progress,
            "end_progress_nm": end_progress,
            "average_progress_nm": average_progresses[len(average_progresses) // 2],
            "average_progresses_nm": average_progresses,
            "average_point": anchor_points[1 + len(average_progresses) // 2],
            "xy_points": xy_points,
            "points": points,
        }

    @staticmethod
    def _compromise_path_key(path):
        if not isinstance(path, dict):
            return None
        pair = path.get("pair")
        if not isinstance(pair, (tuple, list)):
            return None
        return tuple(int(value) for value in pair)

    def _overlap_compromise_path_active(self, path):
        """Latch one displayed conflict path until own ship passes its end."""
        key = self._compromise_path_key(path)
        if key is None:
            return False
        if getattr(self, "_track_conflict_path_completed_key", None) == key:
            return False
        if getattr(self, "_track_conflict_path_key", None) == key:
            return True
        start_progress = float(path.get("start_progress_nm", 0.0))
        end_progress = float(path.get("end_progress_nm", start_progress))
        if start_progress - 0.002 <= self.track_progress_nm <= end_progress + 0.002:
            self._track_conflict_path_key = key
            self._track_conflict_path_index = 0
            self._track_conflict_path_fraction = 0.0
            self._track_conflict_path_travel_nm = 0.0
            return True
        return False

    def _follow_overlap_compromise_path(self, path, dt, commanded_rot_limit):
        """Follow the displayed compromise curve with monotonic path progress."""
        points = path.get("xy_points") or []
        if len(points) < 2 or not self.route_waypoints:
            return 0.0
        key = self._compromise_path_key(path)
        if key != getattr(self, "_track_conflict_path_key", None):
            self._track_conflict_path_key = key
            self._track_conflict_path_index = 0
            self._track_conflict_path_fraction = 0.0
            self._track_conflict_path_travel_nm = 0.0
        origin = self.route_waypoints[0]
        ship = self._local_xy_nm(origin["lat"], origin["lon"], self.lat, self.lon)

        cumulative_nm = [0.0]
        for first, second in zip(points, points[1:]):
            cumulative_nm.append(
                cumulative_nm[-1] + math.hypot(second[0] - first[0], second[1] - first[1])
            )

        previous_index = max(0, min(int(getattr(self, "_track_conflict_path_index", 0)), len(points) - 2))
        # On first acquisition a seek may place own ship in the middle of the
        # transition. Afterwards search only a small forward window and never
        # let the guidance index move backwards.
        if previous_index == 0 and getattr(self, "_track_conflict_path_fraction", 0.0) <= 0.0:
            first_index = 0
            last_index = len(points) - 2
        else:
            first_index = max(0, previous_index - 1)
            last_index = min(len(points) - 2, previous_index + 8)
        best = None
        for index in range(first_index, last_index + 1):
            a, b = points[index], points[index + 1]
            vx, vy = b[0] - a[0], b[1] - a[1]
            length2 = vx * vx + vy * vy
            if length2 <= 1e-12:
                continue
            ratio = max(0.0, min(1.0, ((ship[0] - a[0]) * vx + (ship[1] - a[1]) * vy) / length2))
            px, py = a[0] + ratio * vx, a[1] + ratio * vy
            dx, dy = ship[0] - px, ship[1] - py
            distance2 = dx * dx + dy * dy
            if best is None or distance2 < best[0]:
                cross = vx * (ship[1] - a[1]) - vy * (ship[0] - a[0])
                best = (distance2, index, ratio, vx, vy, cross)
        if best is None:
            return 0.0
        _, index, ratio, vx, vy, cross = best
        if index < previous_index:
            index = previous_index
            a, b = points[index], points[index + 1]
            vx, vy = b[0] - a[0], b[1] - a[1]
            length2 = max(1e-12, vx * vx + vy * vy)
            ratio = max(0.0, min(1.0, ((ship[0] - a[0]) * vx + (ship[1] - a[1]) * vy) / length2))
            px, py = a[0] + ratio * vx, a[1] + ratio * vy
            dx, dy = ship[0] - px, ship[1] - py
            cross = vx * (ship[1] - a[1]) - vy * (ship[0] - a[0])
            best = (dx * dx + dy * dy, index, ratio, vx, vy, cross)

        while index < len(points) - 2:
            next_point = points[index + 1]
            segment_length = math.hypot(vx, vy)
            if segment_length <= 1e-12:
                index += 1
            else:
                passed = ((ship[0] - next_point[0]) * vx + (ship[1] - next_point[1]) * vy) / segment_length
                if passed < 0.0:
                    break
                index += 1
            a, b = points[index], points[index + 1]
            vx, vy = b[0] - a[0], b[1] - a[1]
            length2 = max(1e-12, vx * vx + vy * vy)
            ratio = max(0.0, min(1.0, ((ship[0] - a[0]) * vx + (ship[1] - a[1]) * vy) / length2))

        projected_along_nm = cumulative_nm[index] + ratio * max(
            0.0,
            cumulative_nm[index + 1] - cumulative_nm[index],
        )
        speed_kn = abs(self.stw if abs(self.stw) > 0.05 else self.speed)
        if (
            getattr(self, "_track_conflict_path_travel_nm", 0.0) <= 0.0
            and getattr(self, "_track_conflict_path_fraction", 0.0) <= 0.0
        ):
            self._track_conflict_path_travel_nm = projected_along_nm
        self._track_conflict_path_travel_nm += speed_kn * max(0.0, dt) / 3600.0
        guidance_along_nm = max(
            projected_along_nm,
            min(cumulative_nm[-1], self._track_conflict_path_travel_nm),
        )
        while index < len(points) - 2 and cumulative_nm[index + 1] < guidance_along_nm:
            index += 1
        segment_span_nm = max(1e-12, cumulative_nm[index + 1] - cumulative_nm[index])
        ratio = max(0.0, min(1.0, (guidance_along_nm - cumulative_nm[index]) / segment_span_nm))
        vx = points[index + 1][0] - points[index][0]
        vy = points[index + 1][1] - points[index][1]
        self._track_conflict_path_index = max(previous_index, index)
        self._track_conflict_path_fraction = min(1.0, guidance_along_nm / max(1e-9, cumulative_nm[-1]))

        px, py = points[index][0] + ratio * vx, points[index][1] + ratio * vy
        dx, dy = ship[0] - px, ship[1] - py
        distance2 = dx * dx + dy * dy
        cross = vx * (ship[1] - points[index][1]) - vy * (ship[0] - points[index][0])
        xte_m = math.copysign(math.sqrt(distance2) * 1852.0, cross if abs(cross) > 1e-12 else 1.0)

        achievable_radius_nm = self._turn_radius_nm(speed_kn, commanded_rot_limit)
        lookahead_nm = max(
            0.02,
            min(
                0.75,
                max(
                    speed_kn * 15.0 / 3600.0 + self._ship_length_nm() * 0.35,
                    achievable_radius_nm * 0.35,
                ),
            ),
        )
        target_along_nm = min(cumulative_nm[-1], guidance_along_nm + lookahead_nm)
        lookahead_index = index + 1
        while lookahead_index < len(cumulative_nm) - 1 and cumulative_nm[lookahead_index] < target_along_nm:
            lookahead_index += 1
        before_nm = cumulative_nm[lookahead_index - 1]
        after_nm = cumulative_nm[lookahead_index]
        target_ratio = 0.0 if after_nm <= before_nm else (target_along_nm - before_nm) / (after_nm - before_nm)
        first = points[lookahead_index - 1]
        second = points[lookahead_index]
        target = (
            first[0] + (second[0] - first[0]) * target_ratio,
            first[1] + (second[1] - first[1]) * target_ratio,
        )
        tangent_course = self._vector_to_bearing((vx, vy))
        target_course = self._vector_to_bearing((target[0] - ship[0], target[1] - ship[1]))
        intercept_error = ((target_course - tangent_course + 180.0) % 360.0) - 180.0
        intercept_error = max(-45.0, min(45.0, intercept_error))
        desired_course = (tangent_course + intercept_error) % 360.0
        heading_error = ((desired_course - self.heading + 180.0) % 360.0) - 180.0
        heading_derivative = (heading_error - self._track_pid_last_heading_error) / max(0.001, dt)
        self._track_pid_last_heading_error = heading_error

        curve_previous_index = max(0, index - 2)
        future_index = min(len(points) - 1, index + 3)
        before_vector = (
            points[index + 1][0] - points[curve_previous_index][0],
            points[index + 1][1] - points[curve_previous_index][1],
        )
        after_vector = (
            points[future_index][0] - points[index][0],
            points[future_index][1] - points[index][1],
        )
        before_course = self._vector_to_bearing(before_vector)
        after_course = self._vector_to_bearing(after_vector)
        course_change = ((after_course - before_course + 180.0) % 360.0) - 180.0
        curve_distance_nm = max(
            1e-5,
            math.hypot(
                points[future_index][0] - points[curve_previous_index][0],
                points[future_index][1] - points[curve_previous_index][1],
            ),
        )
        feedforward_rot = course_change * speed_kn / (curve_distance_nm * 60.0)
        feedforward_rot = max(-commanded_rot_limit, min(commanded_rot_limit, feedforward_rot))

        model = self.vessel_model
        heading_gain = max(
            float(model.get("track_turn_heading_gain", 0.30)),
            min(1.20, float(model.get("track_heading_gain", 4.8)) * 0.18),
        )
        desired = (
            feedforward_rot
            + heading_error * heading_gain
            + heading_derivative * float(model.get("track_turn_heading_derivative_gain", 0.05))
            + xte_m * float(model.get("track_turn_radius_gain", 0.010))
            - self.rate_of_turn * float(model.get("track_rot_damping", 0.13))
        )
        desired *= float(model.get("track_turn_rudder_gain", 1.0))

        final_vector = (
            points[-1][0] - points[-2][0],
            points[-1][1] - points[-2][1],
        )
        final_length = max(1e-12, math.hypot(*final_vector))
        past_end_nm = (
            (ship[0] - points[-1][0]) * final_vector[0]
            + (ship[1] - points[-1][1]) * final_vector[1]
        ) / final_length
        distance_to_end_nm = math.hypot(ship[0] - points[-1][0], ship[1] - points[-1][1])
        path_timeout_nm = cumulative_nm[-1] + max(0.02, self._ship_length_nm())
        if index >= len(points) - 2 and (
            past_end_nm >= 0.0
            or distance_to_end_nm <= 0.01
            or self._track_conflict_path_travel_nm >= path_timeout_nm
        ):
            self._track_conflict_path_completed_key = key
        return max(-commanded_rot_limit, min(commanded_rot_limit, desired))

    def set_track_turn_mode(self, mode):
        self.track_turn_mode = "fixed_radius" if mode == "fixed_radius" else "fixed_rot"
        self.sync_track_turn_constraints()

    def track_turn_rate_limits(self):
        engine_speeds = [abs(speed) for _, speed in getattr(self, "engine_steps", [(None, 1.0)]) if abs(speed) > 0.1]
        max_speed = max(engine_speeds) if engine_speeds else max(1.0, float(self.vessel_model.get("max_speed", 1.0)))
        max_rate = max(0.2, float(self._available_track_rot(max_speed)))
        return 0.2, max_rate

    def track_turn_radius_limits(self):
        min_rate, max_rate = self.track_turn_rate_limits()
        engine_speeds = [abs(speed) for _, speed in getattr(self, "engine_steps", [(None, 1.0)]) if abs(speed) > 0.1]
        max_speed = max(engine_speeds) if engine_speeds else max(1.0, float(self.vessel_model.get("max_speed", 1.0)))
        min_steer_speed = max(0.2, float(self.vessel_model.get("track_min_steer_speed", 0.4)))
        min_radius = max(0.05, float(self._turn_radius_nm(max_speed, max_rate)))
        max_radius = min(25.0, float(self._turn_radius_nm(min_steer_speed, min_rate)))
        if max_radius < min_radius:
            max_radius = min_radius
        return min_radius, max_radius

    def sync_track_turn_constraints(self, speed_kn=None):
        if speed_kn is None:
            speed_kn = abs(self.stw if abs(self.stw) > 0.05 else self.speed)
        speed_kn = abs(float(speed_kn))
        min_rate, max_rate = self.track_turn_rate_limits()
        min_radius, max_radius = self.track_turn_radius_limits()
        if self.track_turn_mode == "fixed_radius":
            self.track_turn_radius_nm = max(min_radius, min(max_radius, float(getattr(self, "track_turn_radius_nm", 0.6))))
            if speed_kn > 0.01:
                derived = self._turn_rate_for_radius(speed_kn, self.track_turn_radius_nm)
                self.track_turn_rate = max(min_rate, min(max_rate, derived))
        else:
            self.track_turn_rate = max(min_rate, min(max_rate, float(getattr(self, "track_turn_rate", 18.0))))
            if speed_kn > 0.01:
                self.track_turn_radius_nm = max(min_radius, min(max_radius, self._turn_radius_nm(speed_kn, self.track_turn_rate)))
        self.track_rate_of_turn = self.track_turn_rate

    def _next_waypoint_index(self):
        ticks = self.route_tick_positions()
        if len(ticks) < 2:
            return 0
        for index in range(1, len(ticks)):
            if self.track_progress_nm <= ticks[index] + 0.001:
                return index
        return len(ticks) - 1

    def _progress_search_window(self):
        max_index = len(self.route_waypoints) - 1
        if max_index < 1:
            return 0, 0
        seek_hint = getattr(self, "_track_seek_hint_segment", None)
        if isinstance(seek_hint, int) and self._track_seek_window_ticks > 0:
            hint = max(1, min(seek_hint, max_index))
            self._track_seek_window_ticks -= 1
        else:
            hint = max(1, min(int(getattr(self, "_track_waypoint_index", 1)), max_index))
        ticks = self.route_tick_positions()
        back_span_nm = max(
            0.5,
            float(self.vessel_model.get("track_progress_window_back_nm", 6.0)),
        )
        forward_span_nm = max(
            0.5,
            float(self.vessel_model.get("track_progress_window_forward_nm", 8.0)),
        )
        min_segments = max(
            2,
            int(self.vessel_model.get("track_progress_window_min_segments", 8)),
        )
        max_segments = max(
            min_segments + 2,
            int(self.vessel_model.get("track_progress_window_max_segments", 32)),
        )

        seg_start = hint - 1
        covered = 0.0
        while seg_start > 0 and (
            covered < back_span_nm or (hint - seg_start) < min_segments
        ):
            covered += ticks[seg_start] - ticks[seg_start - 1]
            seg_start -= 1
            if (hint - seg_start) >= max_segments:
                break

        seg_end = hint
        covered = 0.0
        while seg_end < max_index - 1 and (
            covered < forward_span_nm or (seg_end - hint + 1) < min_segments
        ):
            covered += ticks[seg_end + 1] - ticks[seg_end]
            seg_end += 1
            if (seg_end - hint + 1) >= max_segments:
                break
        return seg_start, seg_end

    def _leg_unit_vector(self, from_index, to_index):
        if from_index < 0 or to_index < 0:
            return None
        if from_index >= len(self.route_waypoints) or to_index >= len(self.route_waypoints):
            return None
        if from_index == to_index:
            return None
        origin = self.route_waypoints[0]
        start = self.route_waypoints[from_index]
        end = self.route_waypoints[to_index]
        start_xy = self._local_xy_nm(origin["lat"], origin["lon"], start["lat"], start["lon"])
        end_xy = self._local_xy_nm(origin["lat"], origin["lon"], end["lat"], end["lon"])
        dx = end_xy[0] - start_xy[0]
        dy = end_xy[1] - start_xy[1]
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            return None
        return (dx / length, dy / length)

    def _route_speed_for_waypoint(self, waypoint_index):
        if 0 <= waypoint_index < len(self.route_waypoints):
            speed = self._optional_float(self.route_waypoints[waypoint_index].get("speed"))
            if speed is not None:
                return min(abs(speed), self.vessel_model["max_speed"])
        speed = abs(self.sog if abs(self.sog) > 0.05 else self.target_speed if abs(self.target_speed) > 0.05 else self.speed)
        return min(max(0.0, speed), self.vessel_model["max_speed"])

    def _route_course_change_at(self, waypoint_index):
        if waypoint_index <= 0 or waypoint_index >= len(self.route_waypoints) - 1:
            return 0.0
        prev_wp = self.route_waypoints[waypoint_index - 1]
        turn_wp = self.route_waypoints[waypoint_index]
        next_wp = self.route_waypoints[waypoint_index + 1]
        incoming = self._bearing(prev_wp["lat"], prev_wp["lon"], turn_wp["lat"], turn_wp["lon"])
        outgoing = self._bearing(turn_wp["lat"], turn_wp["lon"], next_wp["lat"], next_wp["lon"])
        return abs(((outgoing - incoming + 180.0) % 360.0) - 180.0)

    def set_track_progress(self, progress_nm):
        total = self.route_distance_nm()
        self.track_progress_nm = max(0.0, min(float(progress_nm), total))
        self._reset_track_controller()
        ticks = self.route_tick_positions()
        waypoint_index = 1
        for index in range(1, len(ticks)):
            if self.track_progress_nm <= ticks[index] + 1e-6:
                waypoint_index = index
                break
        else:
            waypoint_index = max(1, len(ticks) - 1)
        self._track_waypoint_index = waypoint_index
        position = self.route_position_at(self.track_progress_nm)
        if position:
            self.set_position(*position, reset_history=True)
            course = self.route_course_at(self.track_progress_nm)
            if course is not None:
                self.heading = course
                self.cog = course
                self.cog_delta = 0.0
        if len(self.route_waypoints) < 2:
            self.xte = 0.0
            return

        hint = max(1, min(self._track_waypoint_index, len(self.route_waypoints) - 1))
        seg_start = max(0, hint - 24)
        seg_end = min(len(self.route_waypoints) - 2, hint + 24)
        nearest_progress, nearest_xte = self._nearest_route_progress(
            self.lat,
            self.lon,
            seg_start=seg_start,
            seg_end=seg_end,
        )
        self.track_progress_nm = max(0.0, min(total, nearest_progress))
        self.xte = nearest_xte

        self._track_seek_hint_segment = hint
        self._track_seek_window_ticks = max(
            4,
            int(self.vessel_model.get("track_seek_window_ticks", 30)),
        )
        speed_kn = abs(self.stw if abs(self.stw) > 0.05 else self.speed)
        self._track_waypoint_index = self._determine_track_waypoint_index(speed_kn=speed_kn)
        self._set_track_wp_gate_index(self._track_waypoint_index)
        self.track_segment_state = self._track_phase_state(speed_kn, self._track_waypoint_index)
        self._track_last_phase = self.track_segment_state.get("phase", "idle")

    def set_route_waypoints(self, waypoints):
        self.route_waypoints = self._normalize_waypoints(waypoints)
        self._reset_track_controller()
        self._track_waypoint_index = 1
        if len(self.route_waypoints) >= 2:
            self.track_progress_nm, self.xte = self._nearest_route_progress(self.lat, self.lon)
        else:
            self.track_progress_nm = max(0.0, min(self.track_progress_nm, self.route_distance_nm()))
            self.xte = 0.0
        self.track_direction = 1

    def load_default_route(self):
        self.set_route_waypoints(self._default_route())
        if self.route_waypoints:
            first = self.route_waypoints[0]
            self.set_position(first["lat"], first["lon"], reset_history=True)
            course = self.route_course_at(0.0)
            if course is not None:
                self.heading = course
                self.cog = course
                self.cog_delta = 0.0
        self.set_track_progress(0.0)
        self.update_sensor_outputs()

    def _normalize_waypoints(self, waypoints):
        normalized = []
        for index, point in enumerate(waypoints):
            speed = self._normalize_route_value(point.get("speed", "Manual"))
            turn_rate = self._normalize_route_value(point.get("turn_rate", "Manual"))
            turn_radius = self._normalize_route_value(point.get("turn_radius", "Manual"))
            turn_mode = str(point.get("turn_mode", "")).strip().lower()
            if turn_mode not in ("fixed_rot", "fixed_radius"):
                if turn_radius != "Manual" and turn_rate == "Manual":
                    turn_mode = "fixed_radius"
                else:
                    turn_mode = "fixed_rot"
            normalized.append({
                "name": str(point.get("name") or point.get("id") or index + 1),
                "lat": float(point["lat"]),
                "lon": float(point["lon"]),
                "speed": speed,
                "turn_rate": turn_rate,
                "turn_radius": turn_radius,
                "turn_mode": turn_mode,
            })
        return normalized

    def _normalize_route_value(self, value):
        if value is None:
            return "Manual"
        text = str(value).strip()
        if not text or text.lower() == "manual":
            return "Manual"
        try:
            return f"{float(text):.1f}"
        except (TypeError, ValueError):
            return "Manual"

    def _apply_route_leg_commands(self):
        row = self._active_route_row()
        if row is None or row >= len(self.route_waypoints):
            return
        waypoint = self.route_waypoints[row]
        speed = self._optional_float(waypoint.get("speed"))
        turn_rate = self._optional_float(waypoint.get("turn_rate"))
        turn_radius = self._optional_float(waypoint.get("turn_radius"))
        turn_mode = self._route_turn_mode_for_waypoint(row)
        if speed is not None:
            self.target_speed = max(self.engine_steps[0][1], min(self.engine_steps[-1][1], speed))
        if turn_mode == "fixed_radius" and turn_radius is not None:
            self.track_turn_radius_nm = max(0.1, float(turn_radius))
            self.track_turn_mode = "fixed_radius"
        elif turn_rate is not None:
            self.track_turn_rate = max(1.0, min(90.0, turn_rate))
            self.track_turn_mode = "fixed_rot"
            self.track_rate_of_turn = self.track_turn_rate
        self.sync_track_turn_constraints(abs(self.stw if abs(self.stw) > 0.05 else self.speed))

    def _active_route_row(self):
        ticks = self.route_tick_positions()
        if len(ticks) < 2:
            return None
        if self.control_mode == "track":
            waypoint_index = int(getattr(self, "_track_waypoint_index", 1))
            if 1 <= waypoint_index < len(ticks):
                return waypoint_index
        for row in range(1, len(ticks)):
            if self.track_progress_nm <= ticks[row] + 0.001:
                return row
        return len(ticks) - 1

    def progress_row_from_track_progress(self):
        ticks = self.route_tick_positions()
        if len(ticks) < 2:
            return None
        progress = float(getattr(self, "track_progress_nm", 0.0))
        for row in range(1, len(ticks)):
            if progress <= ticks[row] + 1e-6:
                return row
        return len(ticks) - 1

    def route_progress_snapshot(self):
        ticks = self.route_tick_positions()
        max_row = len(ticks) - 1
        if max_row < 1 or len(self.route_waypoints) < 2:
            return {
                "progress_row": None,
                "progress_wp_name": None,
                "controller_row": None,
                "controller_wp_name": None,
                "leg_bearing_deg": None,
            }
        progress_row = self.progress_row_from_track_progress()
        controller_row = max(1, min(int(getattr(self, "_track_waypoint_index", 1)), max_row))

        def wp_name(row):
            if row is None or row < 0 or row >= len(self.route_waypoints):
                return None
            name = self.route_waypoints[row].get("name")
            text = str(name).strip() if name is not None else ""
            return text or str(row + 1)

        leg_bearing = None
        if progress_row is not None and 0 < progress_row < len(self.route_waypoints):
            prev = self.route_waypoints[progress_row - 1]
            curr = self.route_waypoints[progress_row]
            leg_bearing = self._bearing(prev["lat"], prev["lon"], curr["lat"], curr["lon"])

        return {
            "progress_row": progress_row,
            "progress_wp_name": wp_name(progress_row),
            "controller_row": controller_row,
            "controller_wp_name": wp_name(controller_row),
            "leg_bearing_deg": leg_bearing,
        }

    def _determine_track_waypoint_index(self, speed_kn=None):
        max_index = len(self.route_waypoints) - 1
        if max_index < 1:
            self._track_waypoint_index = 0
            return 0
        if speed_kn is None:
            speed_kn = abs(self.stw if abs(self.stw) > 0.05 else self.speed)
        index = max(1, min(int(getattr(self, "_track_waypoint_index", 1)), max_index))
        ticks = self.route_tick_positions()
        naive_progress_index = 1
        for candidate in range(1, len(ticks)):
            if self.track_progress_nm <= ticks[candidate] + 1e-6:
                naive_progress_index = candidate
                break
        else:
            naive_progress_index = len(ticks) - 1
        max_lag = max(
            0,
            int(self.vessel_model.get("track_wp_index_max_progress_lag", 1)),
        )
        index = max(index, max(1, naive_progress_index - max_lag))
        progress_gate_margin_nm = max(
            0.0008,
            float(self.vessel_model.get("track_wp_progress_gate_margin_nm", 0.002)),
        )
        progress_fallback_margin_nm = max(
            progress_gate_margin_nm,
            float(self.vessel_model.get("track_wp_progress_fallback_margin_nm", 0.01)),
        )
        max_advances_per_tick = max(
            1,
            int(self.vessel_model.get("track_wp_max_advances_per_tick", 1)),
        )
        advances = 0

        while index < max_index:
            self._set_track_wp_gate_index(index)
            geometry = self.route_turn_geometry(index, speed_kn=speed_kn) if index < max_index else None
            if geometry:
                end_progress = float(geometry.get("turn_end_progress_nm", ticks[index]))
                marker_pass = self._is_past_marker(
                    geometry.get("turn_end_point"),
                    geometry.get("outbound_vector", (0.0, 0.0)),
                    tolerance_nm=0.004,
                )
                progress_gate_pass = self.track_progress_nm >= end_progress - progress_gate_margin_nm
                fallback_progress_pass = self.track_progress_nm >= end_progress - progress_fallback_margin_nm
                if self._resolve_track_waypoint_advance(
                    index,
                    advance_progress_nm=end_progress,
                    progress_gate_pass=progress_gate_pass,
                    marker_pass=marker_pass,
                    fallback_progress_pass=fallback_progress_pass,
                ):
                    index += 1
                    advances += 1
                    if advances >= max_advances_per_tick:
                        break
                    continue
                break

            turn_wp = self.route_waypoints[index]
            inbound_unit = self._leg_unit_vector(index - 1, index)
            if inbound_unit is None:
                index += 1
                continue
            marker_pass = self._is_past_marker(
                (turn_wp["lat"], turn_wp["lon"]),
                inbound_unit,
                tolerance_nm=0.002,
            )
            progress_gate_pass = self.track_progress_nm >= ticks[index] - progress_gate_margin_nm
            fallback_progress_pass = self.track_progress_nm >= ticks[index] - progress_fallback_margin_nm
            if self._resolve_track_waypoint_advance(
                index,
                advance_progress_nm=ticks[index],
                progress_gate_pass=progress_gate_pass,
                marker_pass=marker_pass,
                fallback_progress_pass=fallback_progress_pass,
            ):
                index += 1
                advances += 1
                if advances >= max_advances_per_tick:
                    break
                continue
            break

        self._track_waypoint_index = max(1, min(index, max_index))
        self._update_track_index_diff_metric(self._track_waypoint_index, ticks)
        return self._track_waypoint_index

    def _optional_float(self, value):
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in ("", "manual"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def route_distance_nm(self):
        points = self.route_waypoints
        return sum(
            self._distance_nm(points[index]["lat"], points[index]["lon"], points[index + 1]["lat"], points[index + 1]["lon"])
            for index in range(max(0, len(points) - 1))
        )

    def route_tick_positions(self):
        ticks = [0.0]
        distance = 0.0
        for index in range(max(0, len(self.route_waypoints) - 1)):
            a = self.route_waypoints[index]
            b = self.route_waypoints[index + 1]
            distance += self._distance_nm(a["lat"], a["lon"], b["lat"], b["lon"])
            ticks.append(distance)
        return ticks

    def route_wheel_over_marks(self):
        if len(self.route_waypoints) < 3:
            return []
        marks = []
        for index in range(1, len(self.route_waypoints) - 1):
            geometry = self.route_turn_geometry(index, speed_kn=self._route_speed_for_waypoint(index))
            if not geometry:
                continue
            center = geometry.get("turn_init_point")
            progress = float(geometry.get("turn_init_progress_nm", 0.0))
            course = geometry.get("inbound_course_deg")
            if center is None or course is None:
                continue
            cross = (course + 90.0) % 360.0
            marks.append({
                "lat": center[0],
                "lon": center[1],
                "bearing": cross,
                "length_nm": 0.18,
                "turn_rate": geometry.get("effective_rot_deg_min"),
                "turn_radius": geometry.get("effective_radius_nm"),
                "lead_nm": geometry.get("wheel_over_distance_nm"),
                "init_lead_nm": max(0.0, geometry.get("turn_start_progress_nm", progress) - progress),
            })
        return marks

    def route_turn_arcs(self):
        if len(self.route_waypoints) < 3:
            return []
        arcs = []
        for index in range(1, len(self.route_waypoints) - 1):
            geometry = self.route_turn_geometry(index, speed_kn=self._route_speed_for_waypoint(index))
            if not geometry:
                continue
            arc_points = geometry.get("turn_arc_points") or []
            if len(arc_points) < 2:
                continue
            arcs.append({
                "waypoint_index": index,
                "points": [{"lat": lat, "lon": lon} for lat, lon in arc_points],
                "radius_nm": geometry.get("turn_arc_radius_nm"),
                "turn_rate": geometry.get("effective_rot_deg_min"),
                "start_progress_nm": geometry.get("turn_start_progress_nm"),
                "end_progress_nm": geometry.get("turn_end_progress_nm"),
                "overlap": False,
                "_geometry": geometry,
            })
        arcs = sorted(arcs, key=lambda arc: float(arc.get("start_progress_nm") or 0.0))
        for i in range(len(arcs) - 1):
            current_end = arcs[i].get("end_progress_nm")
            next_start = arcs[i + 1].get("start_progress_nm")
            if current_end is None or next_start is None:
                continue
            if float(next_start) < float(current_end):
                arcs[i]["overlap"] = True
                arcs[i + 1]["overlap"] = True
                midpoint_progress = (float(current_end) + float(next_start)) / 2.0
                midpoint = self.route_position_at(midpoint_progress)
                if midpoint is not None:
                    arcs[i]["conflict_transition"] = {
                        "lat": midpoint[0],
                        "lon": midpoint[1],
                        "progress_nm": midpoint_progress,
                    }
        group_start = 0
        while group_start < len(arcs):
            group_end = group_start
            while group_end + 1 < len(arcs) and (
                float(arcs[group_end + 1].get("start_progress_nm") or 0.0)
                < float(arcs[group_end].get("end_progress_nm") or 0.0)
            ):
                group_end += 1
            if group_end > group_start:
                compromise = self._build_overlap_compromise_group([
                    arcs[index].get("_geometry")
                    for index in range(group_start, group_end + 1)
                ])
                if compromise is not None:
                    arcs[group_start]["compromise_points"] = [
                        {"lat": lat, "lon": lon}
                        for lat, lon in compromise["points"]
                    ]
            group_start = group_end + 1
        for arc in arcs:
            arc.pop("_geometry", None)
        return arcs

    def _nearest_route_progress(self, lat, lon, seg_start=None, seg_end=None):
        if len(self.route_waypoints) < 2:
            return 0.0, 0.0
        origin = self.route_waypoints[0]
        x, y = self._local_xy_nm(origin["lat"], origin["lon"], lat, lon)
        ticks = self.route_tick_positions()
        best_progress = 0.0
        best_xte = 0.0
        best_dist2 = float("inf")
        first_segment = 0 if seg_start is None else max(0, min(int(seg_start), len(self.route_waypoints) - 2))
        last_segment = (len(self.route_waypoints) - 2) if seg_end is None else max(first_segment, min(int(seg_end), len(self.route_waypoints) - 2))
        for index in range(len(self.route_waypoints) - 1):
            a = self.route_waypoints[index]
            b = self.route_waypoints[index + 1]
            ax, ay = self._local_xy_nm(origin["lat"], origin["lon"], a["lat"], a["lon"])
            bx, by = self._local_xy_nm(origin["lat"], origin["lon"], b["lat"], b["lon"])
            vx = bx - ax
            vy = by - ay
            seg2 = vx * vx + vy * vy
            if seg2 <= 0:
                continue
            seg_len = math.sqrt(seg2)
            if first_segment <= index <= last_segment:
                t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / seg2))
                px = ax + vx * t
                py = ay + vy * t
                dx = x - px
                dy = y - py
                dist2 = dx * dx + dy * dy
                if dist2 < best_dist2:
                    cross = vx * (y - ay) - vy * (x - ax)
                    best_xte = (1.0 if cross >= 0 else -1.0) * math.sqrt(dist2)
                    best_dist2 = dist2
                    seg_start_nm = ticks[index]
                    seg_nm = max(0.0, ticks[index + 1] - ticks[index])
                    best_progress = seg_start_nm + seg_nm * t
        if best_dist2 == float("inf"):
            if seg_start is None and seg_end is None:
                return 0.0, 0.0
            return self._nearest_route_progress(lat, lon)
        return max(0.0, min(best_progress, self.route_distance_nm())), best_xte

    def _local_xy_nm(self, origin_lat, origin_lon, lat, lon):
        north = (lat - origin_lat) * 60.0
        east = (lon - origin_lon) * 60.0 * math.cos(math.radians(origin_lat))
        return east, north

    def _xy_to_latlon(self, origin_lat, origin_lon, x_nm, y_nm):
        lat = origin_lat + (y_nm / 60.0)
        scale = max(1e-6, math.cos(math.radians(origin_lat)))
        lon = origin_lon + (x_nm / (60.0 * scale))
        return lat, lon

    def _line_intersection(self, p1, d1, p2, d2):
        denom = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(denom) < 1e-9:
            return None
        delta_x = p2[0] - p1[0]
        delta_y = p2[1] - p1[1]
        t = (delta_x * d2[1] - delta_y * d2[0]) / denom
        return (p1[0] + t * d1[0], p1[1] + t * d1[1])

    def _vector_to_bearing(self, vector):
        return (math.degrees(math.atan2(vector[0], vector[1])) + 360.0) % 360.0

    def _turn_arc_points(self, center_xy, start_xy, end_xy, turn_direction, origin_lat, origin_lon):
        start_angle = math.atan2(start_xy[1] - center_xy[1], start_xy[0] - center_xy[0])
        end_angle = math.atan2(end_xy[1] - center_xy[1], end_xy[0] - center_xy[0])
        if turn_direction >= 0.0:
            sweep = -((start_angle - end_angle) % (2.0 * math.pi))
        else:
            sweep = (end_angle - start_angle) % (2.0 * math.pi)
        point_count = max(8, min(80, int(abs(sweep) / math.radians(3.0)) + 1))
        points = []
        for index in range(point_count + 1):
            ratio = index / float(point_count)
            angle = start_angle + sweep * ratio
            x_nm = center_xy[0] + math.cos(angle) * math.hypot(start_xy[0] - center_xy[0], start_xy[1] - center_xy[1])
            y_nm = center_xy[1] + math.sin(angle) * math.hypot(start_xy[0] - center_xy[0], start_xy[1] - center_xy[1])
            lat, lon = self._xy_to_latlon(origin_lat, origin_lon, x_nm, y_nm)
            points.append((lat, lon))
        return points

    def route_position_at(self, progress_nm):
        points = self.route_waypoints
        if not points:
            return None
        if len(points) == 1:
            return points[0]["lat"], points[0]["lon"]
        remaining = max(0.0, float(progress_nm))
        for index in range(len(points) - 1):
            a = points[index]
            b = points[index + 1]
            segment = self._distance_nm(a["lat"], a["lon"], b["lat"], b["lon"])
            if remaining <= segment or index == len(points) - 2:
                ratio = 0.0 if segment <= 0 else max(0.0, min(1.0, remaining / segment))
                return (
                    a["lat"] + (b["lat"] - a["lat"]) * ratio,
                    a["lon"] + (b["lon"] - a["lon"]) * ratio,
                )
            remaining -= segment
        return points[-1]["lat"], points[-1]["lon"]

    def route_course_at(self, progress_nm):
        points = self.route_waypoints
        if len(points) < 2:
            return None
        remaining = max(0.0, float(progress_nm))
        for index in range(len(points) - 1):
            a = points[index]
            b = points[index + 1]
            segment = self._distance_nm(a["lat"], a["lon"], b["lat"], b["lon"])
            if remaining <= segment or index == len(points) - 2:
                return self._bearing(a["lat"], a["lon"], b["lat"], b["lon"])
            remaining -= segment
        return self._bearing(points[-2]["lat"], points[-2]["lon"], points[-1]["lat"], points[-1]["lon"])

    def _distance_nm(self, lat1, lon1, lat2, lon2):
        radius_m = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)) / 1852.0

    def _bearing(self, lat1, lon1, lat2, lon2):
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dlambda = math.radians(lon2 - lon1)
        y = math.sin(dlambda) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
        return math.degrees(math.atan2(y, x)) % 360.0

    def _default_route(self):
        for path in DEFAULT_RTZ_ALTERNATE_PATHS:
            route = self._load_default_rtz(path)
            if route:
                return route
        return self._fallback_route()

    def _load_default_rtz(self, path):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            return []
        route = []
        waypoint_names = {}
        for element in root.iter():
            local = element.tag.rsplit("}", 1)[-1].lower()
            if local in ("waypoint", "routewaypoint"):
                name = self._rtz_attr(element, ("name", "id", "routepointname"))
                for child in element:
                    if child.tag.rsplit("}", 1)[-1].lower() == "position":
                        waypoint_names[id(child)] = name
            if local not in ("position", "waypoint", "routewaypoint"):
                continue
            lat = self._rtz_attr(element, ("lat", "latitude"))
            lon = self._rtz_attr(element, ("lon", "longitude"))
            if lat is None or lon is None:
                continue
            try:
                route.append({
                    "name": waypoint_names.get(id(element)) or self._rtz_attr(element, ("name", "id", "routepointname")) or str(len(route) + 1),
                    "lat": float(lat),
                    "lon": float(lon),
                    "speed": "Manual",
                    "turn_rate": "Manual",
                    "turn_radius": "Manual",
                    "turn_mode": "fixed_rot",
                })
            except (TypeError, ValueError):
                continue
        return route

    def _rtz_attr(self, element, names):
        lower = {key.lower(): value for key, value in element.attrib.items()}
        for name in names:
            if name in lower:
                return lower[name]
        return None

    def _fallback_route(self):
        return [
            {"name": "P&H DOCK", "lat": 43.27626785, "lon": -79.85237616666667},
            {"name": "BURLINGTON BAY", "lat": 43.3030, "lon": -79.7870},
            {"name": "BURLINGTON CANAL", "lat": 43.3000, "lon": -79.7930},
            {"name": "LAKE ONTARIO W", "lat": 43.3200, "lon": -79.4800},
            {"name": "OAKVILLE OFFING", "lat": 43.3900, "lon": -79.1700},
            {"name": "TORONTO SOUTH", "lat": 43.5200, "lon": -79.0200},
            {"name": "AJAX OFFING", "lat": 43.7200, "lon": -78.7400},
            {"name": "OSHAWA OFFING", "lat": 43.8400, "lon": -78.5200},
            {"name": "COBOURG OFFING", "lat": 43.9000, "lon": -77.9400},
            {"name": "PRESQU'ILE", "lat": 43.9400, "lon": -77.6200},
            {"name": "KINGSTON APPROACH", "lat": 44.1200, "lon": -76.7800},
            {"name": "KINGSTON E", "lat": 44.2000, "lon": -76.3600},
            {"name": "WOLFE ISLAND E", "lat": 44.2200, "lon": -76.0500},
            {"name": "BROCKVILLE W", "lat": 44.4500, "lon": -75.8500},
            {"name": "BROCKVILLE E", "lat": 44.6000, "lon": -75.6500},
            {"name": "OGDENSBURG", "lat": 44.7000, "lon": -75.4200},
            {"name": "MASSENA W", "lat": 44.9300, "lon": -75.0700},
            {"name": "CORNWALL", "lat": 45.0200, "lon": -74.7200},
            {"name": "LAKE ST FRANCIS", "lat": 45.0900, "lon": -74.3400},
            {"name": "BEAUHARNOIS", "lat": 45.2500, "lon": -73.9200},
            {"name": "LACHINE", "lat": 45.4100, "lon": -73.6800},
            {"name": "MONTREAL", "lat": 45.5050, "lon": -73.5550},
        ]

    def _update_ship_ground_vector(self, dt):
        model = self.vessel_model
        turn_induced_sway = (
            -self.rate_of_turn
            / max(1.0, model["max_rot"])
            * abs(self.speed)
            * model["turn_slip"]
        )
        if self.speed < -0.05:
            turn_induced_sway *= -1.0
        sway_alpha = min(1.0, dt / model["sway_time_constant"])
        self.sway_speed += (turn_induced_sway - self.sway_speed) * sway_alpha
        self._apply_wind_set_drift()

    def _apply_wind_set_drift(self):
        wind_speed = float(getattr(self, "wind_speed", 0.0))
        if wind_speed <= 0.05:
            wind_lateral_kn = 0.0
            wind_forward_kn = 0.0
        else:
            wind_from = float(getattr(self, "wind_direction", 0.0)) % 360.0
            wind_to = (wind_from + 180.0) % 360.0
            rel = math.radians((wind_to - self.heading + 540.0) % 360.0 - 180.0)
            lateral_wind = math.sin(rel)
            longitudinal_wind = math.cos(rel)

            windage = self.vessel_model["windage"]
            stw_kn = abs(self.stw if abs(self.stw) > 0.05 else self.speed)
            damping = 1.0 / (1.0 + stw_kn / max(1.0, self.vessel_model["max_speed"] * 0.45))
            damping_floor = float(self.vessel_model.get("wind_damping_floor", 0.38))
            damping = max(0.05, min(1.0, max(damping_floor, damping)))
            lateral_gain = float(self.vessel_model.get("wind_lateral_gain", 1.55))
            forward_gain = float(self.vessel_model.get("wind_forward_gain", 0.50))
            wind_lateral_kn = wind_speed * windage * lateral_gain * lateral_wind * damping
            wind_forward_kn = wind_speed * windage * forward_gain * longitudinal_wind * damping

        current_from = float(getattr(self, "current_direction", 0.0)) % 360.0
        current_set = (current_from + 180.0) % 360.0
        current_drift = max(0.0, min(10.0, float(getattr(self, "current_speed", 0.0))))
        self.current_speed = current_drift
        self.current_set = current_set
        self.current_drift = current_drift

        heading_rad = math.radians(self.heading)
        right_rad = heading_rad + math.pi / 2.0
        forward_kn = self.speed + wind_forward_kn
        lateral_kn = self.sway_speed + wind_lateral_kn
        current_rad = math.radians(current_set)
        current_north = math.cos(current_rad) * current_drift
        current_east = math.sin(current_rad) * current_drift

        north = math.cos(heading_rad) * forward_kn + math.cos(right_rad) * lateral_kn + current_north
        east = math.sin(heading_rad) * forward_kn + math.sin(right_rad) * lateral_kn + current_east
        self.vbw_transverse_water = lateral_kn
        self.vbw_transverse_ground = lateral_kn + current_north * math.cos(right_rad) + current_east * math.sin(right_rad)
        stern_kn = self._stern_rotational_transverse_kn()
        self.vbw_fwd_transverse_water = self.vbw_transverse_water - stern_kn
        self.vbw_fwd_transverse_ground = self.vbw_transverse_ground - stern_kn
        self.vbw_aft_transverse_water = self.vbw_transverse_water + stern_kn
        self.vbw_aft_transverse_ground = self.vbw_transverse_ground + stern_kn
        if abs(north) > 0.001 or abs(east) > 0.001:
            self.cog = math.degrees(math.atan2(east, north)) % 360.0
            if self.control_mode == "track":
                self.cog = (self.cog + self._track_xte_noise_angle()) % 360.0
            self.cog_delta = ((self.cog - self.heading + 180.0) % 360.0) - 180.0
            sog_abs = math.hypot(north, east)
            heading_rad = math.radians(self.heading)
            forward_component = north * math.cos(heading_rad) + east * math.sin(heading_rad)
            self.sog = sog_abs if forward_component >= 0.0 else -sog_abs
        else:
            self.sog = 0.0
            self.cog = self.heading % 360.0
            self.cog_delta = 0.0
            return

    def _stern_rotational_transverse_kn(self):
        vessel_length_m = float(self.vessel_model.get("length", 120.0))
        half_length_m = max(1.0, vessel_length_m * 0.5)
        rot_rad_s = math.radians(float(getattr(self, "rate_of_turn", 0.0))) / 60.0
        stern_lateral_mps = -rot_rad_s * half_length_m
        return stern_lateral_mps / 0.5144

    def update_sensor_outputs(self):
        now = time.time()
        heading_noise = self._noise("heading")
        heading_noise_enabled = getattr(self, "heading_noise_enabled", False)
        if heading_noise_enabled != self._heading_noise_was_enabled:
            self._last_heading_noise = heading_noise
            self._filtered_sensor_rot = self.rate_of_turn
            self._heading_noise_was_enabled = heading_noise_enabled
        heading_sensor = (self.heading + heading_noise) % 360.0
        dt = max(0.001, now - self._last_sensor_time)
        noise_delta = heading_noise - self._last_heading_noise
        rot_sensor = self.rate_of_turn + noise_delta * 60.0 / dt
        alpha = min(1.0, dt / 2.0)
        self._filtered_sensor_rot += (rot_sensor - self._filtered_sensor_rot) * alpha
        self._last_sensor_heading = heading_sensor
        self._last_heading_noise = heading_noise
        self._last_sensor_time = now
        rel_wind_angle, rel_wind_speed = self._relative_wind()
        true_wind_dir, true_wind_speed = self._true_wind_from_relative(rel_wind_angle, rel_wind_speed)

        self.sensor = {
            "lat": self._noisy_position(self.lat, "lat"),
            "lon": self._noisy_position(self.lon, "lon"),
            "heading": heading_sensor,
            "cog": self._noisy_angle(self.cog, "cog"),
            "sog": self._noisy_linear(self.sog, "speed", scale=0.1),
            "stw": self._noisy_linear(self.stw, "stw", scale=0.1),
            "rot": self._filtered_sensor_rot,
            "xte": self.xte,
            "depth": self._noisy_linear(self.depth, "depth", minimum=0.0),
            "wind_direction": self._noisy_angle(getattr(self, "wind_direction", 0.0), "wind_direction"),
            "wind_speed": self._noisy_linear(getattr(self, "wind_speed", 0.0), "wind_speed", scale=0.1, minimum=0.0),
            "relative_wind_angle": rel_wind_angle,
            "relative_wind_speed": rel_wind_speed,
            "true_wind_direction": true_wind_dir,
            "true_wind_speed": true_wind_speed,
            "current_direction": self._noisy_angle(getattr(self, "current_direction", 0.0), "current_direction"),
            "current_set": self._noisy_angle(getattr(self, "current_set", 0.0), "current_set"),
            "current_drift": self._noisy_linear(getattr(self, "current_drift", 0.0), "current_drift", scale=0.1, minimum=0.0),
            "barometric_pressure": self._noisy_linear(getattr(self, "barometric_pressure", 1.013), "barometric_pressure", scale=0.001, minimum=0.0),
            "air_temperature": self._noisy_linear(getattr(self, "air_temperature", 15.0), "air_temperature", scale=0.1),
            "water_temperature": self._noisy_linear(getattr(self, "water_temperature", 10.0), "water_temperature", scale=0.1),
            "relative_humidity": self._noisy_linear(getattr(self, "relative_humidity", 65.0), "relative_humidity", scale=0.1, minimum=0.0),
            "absolute_humidity": self._noisy_linear(getattr(self, "absolute_humidity", 8.0), "absolute_humidity", scale=0.1, minimum=0.0),
            "dew_point": self._noisy_linear(getattr(self, "dew_point", 8.0), "dew_point", scale=0.1),
            "rudder": self.rudder,
            "rsa_angle": self.rudder if self.control_mode in ("ship", "track") else getattr(self, "rudder_response", self.rudder),
            "rudder_order": getattr(self, "rudder_order", self.rudder),
            "rudder_response": self.rudder if self.control_mode in ("ship", "track") else getattr(self, "rudder_response", self.rudder),
            "rsa_angle_port": getattr(self, "rudder_response_port", getattr(self, "rudder_response", self.rudder)),
            "rudder_order_port": getattr(self, "rudder_order_port", getattr(self, "rudder_order", self.rudder)),
            "rudder_response_port": getattr(self, "rudder_response_port", getattr(self, "rudder_response", self.rudder)),
            "pitch": getattr(self, "pitch", 0.0),
            "roll": getattr(self, "roll", 0.0),
            "prc_lever_demand": getattr(self, "prc_lever_demand", 0.0),
            "thruster_number": getattr(self, "thruster_number", 1),
            "thruster_rpm_demand": getattr(self, "thruster_rpm_demand", 0.0),
            "thruster_pitch_demand": getattr(self, "thruster_pitch_demand", 0.0),
            "thruster_azimuth_demand": getattr(self, "thruster_azimuth_demand", 0.0),
            "thruster_rpm_response": getattr(self, "thruster_rpm_response", 0.0),
            "thruster_pitch_response": getattr(self, "thruster_pitch_response", 0.0),
            "thruster_azimuth_response": getattr(self, "thruster_azimuth_response", 0.0),
            "vbw_transverse_water": getattr(self, "vbw_transverse_water", 0.0),
            "vbw_transverse_ground": getattr(self, "vbw_transverse_ground", 0.0),
            "vbw_fwd_transverse_water": getattr(self, "vbw_fwd_transverse_water", 0.0),
            "vbw_fwd_transverse_ground": getattr(self, "vbw_fwd_transverse_ground", 0.0),
            "vbw_aft_transverse_water": getattr(self, "vbw_aft_transverse_water", 0.0),
            "vbw_aft_transverse_ground": getattr(self, "vbw_aft_transverse_ground", 0.0),
        }
        if self.control_mode in ("ship", "track") and self.additional_sensors.get("RUDDER2", False):
            self.sensor["rudder_order_port"] = self.sensor["rudder_order"]
            self.sensor["rudder_response_port"] = self.sensor["rudder_response"]
            self.rudder_order_port = self.sensor["rudder_order_port"]
            self.rudder_response_port = self.sensor["rudder_response_port"]
        self.sensor["rsa_angle"] = self.sensor["rudder_response"]
        self.sensor["rsa_angle_port"] = self.sensor["rudder_response_port"]
        self.rsa_angle = self.sensor["rsa_angle"]
        self.rsa_angle_port = self.sensor["rsa_angle_port"]
        self.true_wind_direction = self.sensor["true_wind_direction"]
        self.true_wind_speed = self.sensor["true_wind_speed"]

    def _relative_wind(self):
        wind_speed = float(getattr(self, "wind_speed", 0.0))
        wind_from = float(getattr(self, "wind_direction", 0.0)) % 360.0
        wind_to = math.radians((wind_from + 180.0) % 360.0)
        wind_north = math.cos(wind_to) * wind_speed
        wind_east = math.sin(wind_to) * wind_speed
        ship_course = math.radians(getattr(self, "cog", self.heading))
        ship_north = math.cos(ship_course) * abs(float(getattr(self, "sog", 0.0)))
        ship_east = math.sin(ship_course) * abs(float(getattr(self, "sog", 0.0)))
        apparent_to_north = wind_north - ship_north
        apparent_to_east = wind_east - ship_east
        speed = math.hypot(apparent_to_north, apparent_to_east)
        if speed <= 0.001:
            return 0.0, 0.0
        apparent_from = (math.degrees(math.atan2(apparent_to_east, apparent_to_north)) + 180.0) % 360.0
        relative_angle = (apparent_from - self.heading + 360.0) % 360.0
        return relative_angle, speed

    def _true_wind(self):
        rel_angle, rel_speed = self._relative_wind()
        return self._true_wind_from_relative(rel_angle, rel_speed)

    def _true_wind_from_relative(self, rel_angle, rel_speed):
        if rel_speed <= 0.001:
            return float(getattr(self, "wind_direction", 0.0)) % 360.0, 0.0
        apparent_from = (self.heading + rel_angle) % 360.0
        apparent_to = math.radians((apparent_from + 180.0) % 360.0)
        apparent_to_north = math.cos(apparent_to) * rel_speed
        apparent_to_east = math.sin(apparent_to) * rel_speed

        ship_course = math.radians(getattr(self, "cog", self.heading))
        ship_north = math.cos(ship_course) * abs(float(getattr(self, "sog", 0.0)))
        ship_east = math.sin(ship_course) * abs(float(getattr(self, "sog", 0.0)))

        true_to_north = apparent_to_north + ship_north
        true_to_east = apparent_to_east + ship_east
        true_speed = math.hypot(true_to_north, true_to_east)
        if true_speed <= 0.001:
            return float(getattr(self, "wind_direction", 0.0)) % 360.0, 0.0
        true_from = (math.degrees(math.atan2(true_to_east, true_to_north)) + 180.0) % 360.0
        return true_from, true_speed

    def _noise(self, key):
        if not getattr(self, f"{key}_noise_enabled", False):
            return 0.0
        amplitude = getattr(self, f"{key}_noise", 0)
        if amplitude <= 0:
            return 0.0
        t = time.monotonic()
        state = self._noise_state.get(key)
        if not state or state["amplitude"] != amplitude:
            state = {"amplitude": amplitude, "start": t}
            self._noise_state[key] = state
        elapsed = t - state["start"]

        if key in ("heading", "cog"):
            slow = math.sin(elapsed * 0.006) * amplitude * 0.95
            fast = math.sin(elapsed * 0.08) * amplitude * 0.05
            return slow + fast
        swing = math.sin(elapsed * 0.35) * amplitude * 0.80
        jitter = random.uniform(-amplitude, amplitude) * 0.05
        return swing + jitter

    def _noisy_position(self, value, key):
        return value + self._noise(key) / 111320.0

    def _noisy_angle(self, value, key):
        return (value + self._noise(key)) % 360.0

    def _noisy_linear(self, value, key, scale=1.0, minimum=None):
        noisy = value + self._noise(key) * scale
        if minimum is not None:
            noisy = max(minimum, noisy)
        return noisy

    def set_vessel_model(self, name):
        if name not in self.vessel_models:
            return
        self.vessel_model_name = name
        self.vessel_model = self.vessel_models[name]
        self._reset_track_controller()
        self.engine_steps = self._engine_steps_for_model(self.vessel_model)
        self.rudder = max(-self.vessel_model["max_rudder"], min(self.vessel_model["max_rudder"], self.rudder))
        self.rudder_order = max(-self.vessel_model["max_rudder"], min(self.vessel_model["max_rudder"], self.rudder_order))
        self.target_speed = max(self.engine_steps[0][1], min(self.engine_steps[-1][1], self.target_speed))
        self.sync_track_turn_constraints(abs(self.stw if abs(self.stw) > 0.05 else self.speed))

    def _create_vessel_models(self):
        return {
            "Crew Transfer Vessel": {
                "length_m": 30.0,
                "max_speed": 35.0,
                "max_astern": 10.0,
                "acceleration": 0.75,
                "deceleration": 1.10,
                "max_rot": 95.0,
                "rot_time_constant": 2.5,
                "max_rudder": 45.0,
                "rudder_rate_deg_s": 8.0,
                "windage": 0.012,
                "wind_lateral_gain": 1.30,
                "wind_forward_gain": 0.46,
                "wind_damping_floor": 0.26,
                "turn_slip": 0.035,
                "sway_time_constant": 2.0,
                "xte_noise_period": 45.0,
                "track_lookahead_min": 0.10,
                "track_lookahead_max": 0.80,
                "track_lookahead_time": 1.4,
                "track_xte_gain": 1.05,
                "track_xte_limit": 30.0,
                "track_heading_gain": 9.38522,
                "track_bearing_gain": 0.20,
                "track_drift_gain": 0.40,
                "track_rudder_time_constant": 0.45,
                "track_rot_authority": 0.95,
                "track_min_steer_speed": 0.4,
                "rot_speed_exponent": 0.85,
                "track_rot_damping": 0.05756,
                "track_wheel_over_allowance_s": 1.2,
                "track_wheel_over_max_nm": 2.5,
                "track_pid_kp": 0.02218,
                "track_pid_ki": 0.00552,
                "track_pid_kd": 0.00365,
                "track_pid_i_limit_m": 160.0,
                "track_turn_pid_kp": 0.28,
                "track_turn_pid_kd": 0.06,
                "track_heading_derivative_gain": 0.15689,
                "track_xte_rot_gain": 0.00295,
                "track_leg_rudder_gain": 1.72421,
                "track_outbound_rudder_gain": 1.31908,
                "track_turn_rudder_gain": 0.80626,
                "track_turn_radius_gain": 0.00772,
                "track_turn_outside_radius_boost": 0.0,
                "track_turn_heading_gain": 0.44415,
                "track_turn_heading_derivative_gain": 0.04150,
                "track_outbound_xte_rot_gain": 0.00468,
                "track_outbound_intercept_max_deg": 45.0,
                "track_outbound_intercept_xte_m": 180.0,
                "track_leg_rollout_extra_radius_fraction": 0.22,
                "track_turn_brake_lead_per_rot": 0.10,
                "track_intercept_soft_power": 1.45,
                "track_intercept_soft_min_fraction": 0.36,
                "track_intercept_soft_xte_m": 170.0,
                "track_intercept_entry_xte_m": 20.0,
                "track_turn_brake_base_deg": 3.0,
                "track_turn_brake_per_rot_deg": 0.10514,
                "track_turn_overshoot_full_deg": 4.46008,
                "track_turn_brake_arm_fraction": 0.94858,
                "track_turn_preinit_ship_lengths": 0.75,
                "track_turn_preinit_rot_fraction": 0.22,
                "track_turn_preinit_rot_min": 1.5,
                "track_turn_preinit_rudder_fraction": 0.35,
                "track_rudder_min_fraction": 0.18,
                "track_rudder_soft_xte_m": 6.0,
                "track_rudder_hard_xte_m": 55.0,
                "track_rudder_soft_cog_error_deg": 1.2,
                "track_rudder_hard_cog_error_deg": 12.0,
                "track_straight_bias_ki_deg_per_m_s": 0.00082,
                "track_straight_cog_bias_gain": 0.05769,
                "track_straight_bias_leak_per_s": 0.006,
                "track_straight_bias_limit_deg": 24.0,
                "track_straight_rot_limit_fraction": 1.0,
                "track_small_error_xte_m": 185.2,
                "track_small_error_cog_deg": 2.0,
                "track_small_error_rudder_gain_min": 0.58,
                "track_counterrudder_soft_xte_m": 185.2,
                "track_counterrudder_soft_cog_deg": 2.0,
                "track_counterrudder_soft_min_fraction": 0.30,
                "track_counterrudder_small_error_gain_min": 0.35,
            },
            "Container Feeder": {
                "length_m": 170.0,
                "max_speed": 20.0,
                "max_astern": 6.0,
                "acceleration": 0.22,
                "deceleration": 0.35,
                "max_rot": 32.0,
                "rot_time_constant": 8.0,
                "max_rudder": 45.0,
                "rudder_rate_deg_s": 4.2,
                "windage": 0.020,
                "wind_lateral_gain": 1.70,
                "wind_forward_gain": 0.54,
                "wind_damping_floor": 0.35,
                "turn_slip": 0.070,
                "sway_time_constant": 7.0,
                "xte_noise_period": 90.0,
                "track_lookahead_min": 0.20,
                "track_lookahead_max": 1.35,
                "track_lookahead_time": 2.2,
                "track_xte_gain": 0.88,
                "track_xte_limit": 25.0,
                "track_heading_gain": 6.19891,
                "track_bearing_gain": 0.16,
                "track_drift_gain": 1.60,
                "track_rudder_time_constant": 0.9,
                "track_rot_authority": 0.90,
                "track_min_steer_speed": 1.0,
                "rot_speed_exponent": 1.00,
                "track_rot_damping": 0.08493,
                "track_wheel_over_allowance_s": 4.2,
                "track_wheel_over_max_nm": 3.5,
                "track_pid_kp": 0.01053,
                "track_pid_ki": 0.00254,
                "track_pid_kd": 0.00433,
                "track_pid_i_limit_m": 190.0,
                "track_turn_pid_kp": 0.24,
                "track_turn_pid_kd": 0.05,
                "track_heading_derivative_gain": 0.27666,
                "track_xte_rot_gain": 0.00240,
                "track_leg_rudder_gain": 1.18296,
                "track_outbound_rudder_gain": 1.77856,
                "track_turn_rudder_gain": 0.92,
                "track_turn_radius_gain": 0.0190,
                "track_turn_outside_radius_boost": 1.4,
                "track_turn_heading_gain": 0.42,
                "track_turn_heading_derivative_gain": 0.02907,
                "track_outbound_xte_rot_gain": 0.00420,
                "track_outbound_intercept_max_deg": 45.0,
                "track_outbound_intercept_xte_m": 210.0,
                "track_leg_rollout_extra_radius_fraction": 0.30,
                "track_turn_brake_lead_per_rot": 0.12,
                "track_intercept_soft_power": 1.45,
                "track_intercept_soft_min_fraction": 0.36,
                "track_intercept_soft_xte_m": 170.0,
                "track_intercept_entry_xte_m": 30.0,
                "track_turn_brake_base_deg": 3.2,
                "track_turn_brake_per_rot_deg": 0.29123,
                "track_turn_overshoot_full_deg": 6.75963,
                "track_turn_brake_arm_fraction": 0.81760,
                "track_turn_preinit_ship_lengths": 0.90,
                "track_turn_preinit_rot_fraction": 0.20,
                "track_turn_preinit_rot_min": 1.2,
                "track_turn_preinit_rudder_fraction": 0.30,
                "track_rudder_min_fraction": 0.16,
                "track_rudder_soft_xte_m": 8.0,
                "track_rudder_hard_xte_m": 90.0,
                "track_rudder_soft_cog_error_deg": 1.5,
                "track_rudder_hard_cog_error_deg": 16.0,
                "track_straight_bias_ki_deg_per_m_s": 0.00110,
                "track_straight_cog_bias_gain": 0.05573,
                "track_straight_bias_leak_per_s": 0.006,
                "track_straight_bias_limit_deg": 24.0,
                "track_straight_rot_limit_fraction": 1.0,
            },
            "15k TEU Container": {
                "length_m": 370.0,
                "max_speed": 22.0,
                "max_astern": 5.5,
                "acceleration": 0.10,
                "deceleration": 0.18,
                "max_rot": 16.0,
                "rot_time_constant": 18.0,
                "max_rudder": 45.0,
                "rudder_rate_deg_s": 2.6,
                "windage": 0.030,
                "wind_lateral_gain": 2.05,
                "wind_forward_gain": 0.62,
                "wind_damping_floor": 0.44,
                "turn_slip": 0.110,
                "sway_time_constant": 14.0,
                "xte_noise_period": 150.0,
                "track_lookahead_min": 0.35,
                "track_lookahead_max": 2.20,
                "track_lookahead_time": 3.4,
                "track_xte_gain": 0.72,
                "track_xte_limit": 21.0,
                "track_heading_gain": 2.82978,
                "track_bearing_gain": 0.13,
                "track_drift_gain": 0.79890,
                "track_rudder_time_constant": 1.6,
                "track_rot_authority": 0.84,
                "track_min_steer_speed": 2.0,
                "rot_speed_exponent": 1.12,
                "track_rot_damping": 0.16938,
                "track_wheel_over_allowance_s": 12.5,
                "track_wheel_over_max_nm": 5.0,
                "track_pid_kp": 0.02375,
                "track_pid_ki": 0.00084,
                "track_pid_kd": 0.00414,
                "track_pid_i_limit_m": 220.0,
                "track_turn_pid_kp": 0.19,
                "track_turn_pid_kd": 0.04,
                "track_heading_derivative_gain": 0.24079,
                "track_xte_rot_gain": 0.00114,
                "track_leg_rudder_gain": 1.49608,
                "track_outbound_rudder_gain": 0.95454,
                "track_turn_rudder_gain": 1.05507,
                "track_turn_radius_gain": 0.01079,
                "track_turn_heading_gain": 0.25522,
                "track_turn_heading_derivative_gain": 0.02040,
                "track_outbound_xte_rot_gain": 0.00408,
                "track_outbound_intercept_max_deg": 45.0,
                "track_outbound_intercept_xte_m": 250.0,
                "track_leg_rollout_extra_radius_fraction": 0.38,
                "track_turn_brake_lead_per_rot": 0.18,
                "track_intercept_soft_power": 1.95,
                "track_intercept_soft_min_fraction": 0.22,
                "track_intercept_soft_xte_m": 210.0,
                "track_intercept_entry_xte_m": 20.0,
                "track_turn_brake_base_deg": 3.5,
                "track_turn_brake_per_rot_deg": 0.13794,
                "track_turn_overshoot_full_deg": 7.38505,
                "track_turn_brake_arm_fraction": 0.60703,
                "track_turn_preinit_ship_lengths": 1.15,
                "track_turn_preinit_rot_fraction": 0.16,
                "track_turn_preinit_rot_min": 1.0,
                "track_turn_preinit_rudder_fraction": 0.26,
                "track_rudder_min_fraction": 0.14,
                "track_rudder_soft_xte_m": 10.0,
                "track_rudder_hard_xte_m": 130.0,
                "track_rudder_soft_cog_error_deg": 1.8,
                "track_rudder_hard_cog_error_deg": 20.0,
                "track_straight_bias_ki_deg_per_m_s": 0.00117,
                "track_straight_cog_bias_gain": 0.00923,
                "track_straight_bias_leak_per_s": 0.001,
                "track_straight_bias_limit_deg": 24.0,
                "track_straight_rot_limit_fraction": 1.0,
            },
            "24k TEU Container": {
                "length_m": 400.0,
                "max_speed": 24.0,
                "max_astern": 5.0,
                "acceleration": 0.075,
                "deceleration": 0.13,
                "max_rot": 10.0,
                "rot_time_constant": 28.0,
                "max_rudder": 45.0,
                "rudder_rate_deg_s": 1.9,
                "windage": 0.036,
                "wind_lateral_gain": 2.20,
                "wind_forward_gain": 0.66,
                "wind_damping_floor": 0.48,
                "turn_slip": 0.140,
                "sway_time_constant": 20.0,
                "xte_noise_period": 210.0,
                "track_lookahead_min": 0.45,
                "track_lookahead_max": 3.00,
                "track_lookahead_time": 4.5,
                "track_xte_gain": 0.60,
                "track_xte_limit": 18.0,
                "track_heading_gain": 3.35666,
                "track_bearing_gain": 0.11,
                "track_drift_gain": 0.85,
                "track_rudder_time_constant": 2.4,
                "track_rot_authority": 0.80,
                "track_min_steer_speed": 2.5,
                "rot_speed_exponent": 1.18,
                "track_rot_damping": 0.16,
                "track_wheel_over_allowance_s": 20.0,
                "track_wheel_over_max_nm": 6.5,
                "track_pid_kp": 0.01050,
                "track_pid_ki": 0.00160,
                "track_pid_kd": 0.00420,
                "track_pid_i_limit_m": 260.0,
                "track_turn_pid_kp": 0.15,
                "track_turn_pid_kd": 0.03,
                "track_heading_derivative_gain": 0.14,
                "track_xte_rot_gain": 0.0018,
                "track_leg_rudder_gain": 1.08,
                "track_outbound_rudder_gain": 1.10,
                "track_turn_rudder_gain": 1.00,
                "track_turn_radius_gain": 0.030,
                "track_turn_heading_gain": 0.40,
                "track_turn_heading_derivative_gain": 0.10,
                "track_outbound_xte_rot_gain": 0.0025,
                "track_outbound_intercept_max_deg": 45.0,
                "track_outbound_intercept_xte_m": 280.0,
                "track_leg_rollout_extra_radius_fraction": 0.44,
                "track_turn_brake_lead_per_rot": 0.22,
                "track_intercept_soft_power": 1.45,
                "track_intercept_soft_min_fraction": 0.36,
                "track_intercept_soft_xte_m": 170.0,
                "track_intercept_entry_xte_m": 30.0,
                "track_turn_brake_base_deg": 3.0,
                "track_turn_brake_per_rot_deg": 0.45,
                "track_turn_overshoot_full_deg": 8.2,
                "track_turn_brake_arm_fraction": 0.76,
                "track_turn_preinit_ship_lengths": 1.25,
                "track_turn_preinit_rot_fraction": 0.14,
                "track_turn_preinit_rot_min": 0.8,
                "track_turn_preinit_rudder_fraction": 0.22,
                "track_rudder_min_fraction": 0.12,
                "track_rudder_soft_xte_m": 12.0,
                "track_rudder_hard_xte_m": 160.0,
                "track_rudder_soft_cog_error_deg": 2.0,
                "track_rudder_hard_cog_error_deg": 24.0,
                "track_straight_bias_ki_deg_per_m_s": 0.00050,
                "track_straight_cog_bias_gain": 0.03200,
                "track_straight_bias_leak_per_s": 0.001,
                "track_straight_bias_limit_deg": 24.0,
                "track_straight_rot_limit_fraction": 1.0,
            },
            "VLCC": {
                "length_m": 330.0,
                "max_speed": 17.0,
                "max_astern": 4.0,
                "acceleration": 0.035,
                "deceleration": 0.07,
                "max_rot": 6.0,
                "rot_time_constant": 45.0,
                "max_rudder": 45.0,
                "rudder_rate_deg_s": 1.2,
                "windage": 0.010,
                "wind_lateral_gain": 1.45,
                "wind_forward_gain": 0.48,
                "wind_damping_floor": 0.36,
                "turn_slip": 0.090,
                "sway_time_constant": 30.0,
                "xte_noise_period": 300.0,
                "track_lookahead_min": 0.60,
                "track_lookahead_max": 3.80,
                "track_lookahead_time": 5.6,
                "track_xte_gain": 0.48,
                "track_xte_limit": 16.0,
                "track_heading_gain": 3.335,
                "track_bearing_gain": 0.09,
                "track_drift_gain": 0.95,
                "track_rudder_time_constant": 3.5,
                "track_rot_authority": 0.76,
                "track_min_steer_speed": 3.0,
                "rot_speed_exponent": 1.25,
                "track_rot_damping": 0.207,
                "track_wheel_over_allowance_s": 31.0,
                "track_wheel_over_max_nm": 8.0,
                "track_pid_kp": 0.00750,
                "track_pid_ki": 0.00120,
                "track_pid_kd": 0.00290,
                "track_pid_i_limit_m": 320.0,
                "track_turn_pid_kp": 0.12,
                "track_turn_pid_kd": 0.02,
                "track_heading_derivative_gain": 0.14,
                "track_xte_rot_gain": 0.00144,
                "track_leg_rudder_gain": 1.3225,
                "track_outbound_rudder_gain": 1.28,
                "track_turn_rudder_gain": 1.03,
                "track_turn_radius_gain": 0.020,
                "track_turn_heading_gain": 0.40,
                "track_turn_heading_derivative_gain": 0.12,
                "track_outbound_xte_rot_gain": 0.0022,
                "track_outbound_intercept_max_deg": 45.0,
                "track_outbound_intercept_xte_m": 300.0,
                "track_leg_rollout_extra_radius_fraction": 0.48,
                "track_turn_brake_lead_per_rot": 0.26,
                "track_intercept_soft_power": 1.45,
                "track_intercept_soft_min_fraction": 0.36,
                "track_intercept_soft_xte_m": 170.0,
                "track_intercept_entry_xte_m": 30.0,
                "track_turn_brake_base_deg": 2.5,
                "track_turn_brake_per_rot_deg": 0.50,
                "track_turn_overshoot_full_deg": 9.2,
                "track_turn_brake_arm_fraction": 0.77,
                "track_turn_preinit_ship_lengths": 1.35,
                "track_turn_preinit_rot_fraction": 0.12,
                "track_turn_preinit_rot_min": 0.6,
                "track_turn_preinit_rudder_fraction": 0.20,
                "track_rudder_min_fraction": 0.10,
                "track_rudder_soft_xte_m": 14.0,
                "track_rudder_hard_xte_m": 220.0,
                "track_rudder_soft_cog_error_deg": 2.5,
                "track_rudder_hard_cog_error_deg": 28.0,
                "track_straight_bias_ki_deg_per_m_s": 0.00120,
                "track_straight_cog_bias_gain": 0.02400,
                "track_straight_bias_leak_per_s": 0.006,
                "track_straight_bias_limit_deg": 24.0,
                "track_straight_rot_limit_fraction": 1.0,
            },
        }

    def _engine_steps_for_model(self, model):
        ahead = model["max_speed"]
        astern = model["max_astern"]
        return [
            ("Full Astern", -astern),
            ("Half Astern", -astern * 0.65),
            ("Slow Astern", -astern * 0.40),
            ("Dead Slow Astern", -astern * 0.18),
            ("Stop", 0.0),
            ("Dead Slow Ahead", ahead * 0.16),
            ("Slow Ahead", ahead * 0.32),
            ("Half Ahead", ahead * 0.60),
            ("Full Ahead", ahead),
        ]

    def _track_xte_noise_angle(self):
        if not self.xte_noise_enabled or self.xte_noise <= 0:
            return 0.0
        elapsed = time.monotonic() - self._xte_noise_started
        period = max(10.0, self.vessel_model["xte_noise_period"])
        amplitude_deg = min(6.0, self.xte_noise / 20.0)
        return math.sin((elapsed / period) * math.tau) * amplitude_deg
