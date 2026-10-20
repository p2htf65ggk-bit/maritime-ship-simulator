# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import replace

from maritime_simulator.application.service import TrackControlOutput
from maritime_simulator.domain.state import RouteWaypoint, SimulationState
from maritime_simulator.domain.profiles import VesselProfile


class LegacyTrackControllerAdapter:
    """Preserve V1 Track Control while V2 owns vessel state and commands."""

    def __init__(self):
        from ui.simulation import Simulation

        self._simulation = Simulation()
        self._route_signature: tuple[RouteWaypoint, ...] = ()
        self._profile_name = ""
        self._profile_signature = None

    def set_vessel_profile(self, profile: VesselProfile) -> None:
        signature = (
            profile.profile_id,
            profile.display_name,
            profile.length_m,
            profile.max_speed_kn,
            profile.max_astern_kn,
            profile.acceleration_kn_s,
            profile.deceleration_kn_s,
            profile.max_rot_deg_min,
            profile.rot_time_constant_s,
            profile.max_rudder_deg,
            profile.effective_rudder_rate_deg_s,
            profile.rudder_effectiveness,
            tuple(sorted(profile.track_parameters.items())),
        )
        if signature == self._profile_signature:
            return
        model = {
            "length_m": profile.length_m,
            "max_speed": profile.max_speed_kn,
            "max_astern": profile.max_astern_kn,
            "acceleration": profile.acceleration_kn_s,
            "deceleration": profile.deceleration_kn_s,
            "max_rot": profile.max_rot_deg_min,
            "rot_time_constant": profile.rot_time_constant_s,
            "max_rudder": profile.max_rudder_deg,
            "rudder_rate_deg_s": profile.effective_rudder_rate_deg_s,
            "windage": profile.windage,
            "wind_lateral_gain": profile.wind_lateral_gain,
            "wind_forward_gain": profile.wind_forward_gain,
            "wind_damping_floor": profile.wind_damping_floor,
            "turn_slip": profile.turn_slip,
            "sway_time_constant": profile.sway_time_constant_s,
            **dict(profile.track_parameters),
        }
        self._simulation.vessel_models[profile.display_name] = model
        self._simulation.set_vessel_model(profile.display_name)
        self._profile_name = profile.display_name
        self._profile_signature = signature
        self._route_signature = ()

    def reset(self) -> None:
        self._simulation._reset_track_controller()
        self._simulation.track_segment_state = {"phase": "idle"}

    def update(
        self,
        state: SimulationState,
        profile_display_name: str,
        dt_s: float,
    ) -> TrackControlOutput:
        sim = self._simulation
        if profile_display_name != self._profile_name:
            sim.set_vessel_model(profile_display_name)
            self._profile_name = profile_display_name
        self._sync_simulation_state(sim, state)
        effective_route = self._effective_route(state)
        if effective_route != self._route_signature:
            sim.set_route_waypoints([self._to_legacy_waypoint(item) for item in effective_route])
            self._route_signature = effective_route
            sim.track_progress_nm = state.route.progress_nm

        if len(sim.route_waypoints) < 2:
            return TrackControlOutput(
                0.0,
                progress_nm=0.0,
                xte_nm=0.0,
                next_waypoint_index=0,
                phase="idle",
                route_completed=False,
            )

        projected, xte = sim._nearest_route_progress(sim.lat, sim.lon)
        travel_nm = abs(sim.sog) * max(0.0, dt_s) / 3600.0
        maximum_delta = max(0.001, travel_nm * 2.5 + 0.001)
        sim.track_progress_nm = max(
            0.0,
            min(sim.route_distance_nm(), min(state.route.progress_nm + maximum_delta, projected)),
        )
        sim.xte = xte
        sim._update_track_control(dt_s)
        next_index = int(getattr(sim, "_track_waypoint_index", 0))
        phase = str(getattr(sim, "track_segment_state", {}).get("phase", "idle"))
        final = sim.route_waypoints[-1]
        distance_to_final = sim._distance_nm(sim.lat, sim.lon, final["lat"], final["lon"])
        route_completed = (
            sim.track_progress_nm >= sim.route_distance_nm() - 0.02
            and distance_to_final < 0.25
        )
        return TrackControlOutput(
            rudder_order_starboard_deg=float(sim.rudder_order),
            rudder_order_port_deg=float(sim.rudder_order),
            progress_nm=float(sim.track_progress_nm),
            xte_nm=float(sim.xte),
            next_waypoint_index=next_index,
            phase=phase,
            route_completed=route_completed,
        )

    def route_visuals(
        self,
        state: SimulationState,
        profile_display_name: str,
    ) -> tuple[list[dict], list[dict]]:
        """Use the same legacy geometry engine as Track Control for map overlays."""
        sim = self._simulation
        if profile_display_name != self._profile_name:
            sim.set_vessel_model(profile_display_name)
            self._profile_name = profile_display_name
        self._sync_simulation_state(sim, state)
        effective_route = self._effective_route(state)
        if effective_route != self._route_signature:
            sim.set_route_waypoints([self._to_legacy_waypoint(item) for item in effective_route])
            self._route_signature = effective_route
        return list(sim.route_wheel_over_marks()), list(sim.route_turn_arcs())

    @staticmethod
    def _sync_simulation_state(sim, state: SimulationState) -> None:
        """Populate geometry and controller inputs before a route cache rebuild."""
        sim.control_mode = "track"
        sim.lat = state.position.latitude_deg
        sim.lon = state.position.longitude_deg
        sim.heading = state.motion.heading_deg
        sim.cog = state.motion.cog_deg
        sim.speed = state.motion.stw_kn
        sim.stw = state.motion.stw_kn
        sim.sog = state.motion.sog_kn
        sim.rate_of_turn = state.motion.rate_of_turn_deg_min
        sim.rudder = state.steering.response_starboard_deg
        sim.rudder_response = state.steering.response_starboard_deg
        sim.rudder_order = state.steering.order_starboard_deg
        sim.target_speed = state.engine.effective_speed_order_kn or state.engine.user_speed_order_kn
        sim.wind_direction = state.environment.wind_from_deg
        sim.wind_speed = state.environment.wind_speed_kn
        sim.current_direction = state.environment.current_from_deg
        sim.current_speed = state.environment.current_speed_kn
        sim.track_progress_nm = state.route.progress_nm
        xte_noise = state.sensor_control.noise.get("xte", (False, 0.0))
        sim.xte_noise_enabled = bool(xte_noise[0])
        sim.xte_noise = float(xte_noise[1])

    @staticmethod
    def _to_legacy_waypoint(item: RouteWaypoint) -> dict[str, str | float]:
        result: dict[str, str | float] = {
            "name": item.name,
            "lat": item.latitude_deg,
            "lon": item.longitude_deg,
            "turn_mode": item.turn_mode,
        }
        if item.speed_kn is not None:
            result["speed"] = f"{item.speed_kn:.6f}"
        if item.turn_rate_deg_min is not None:
            result["turn_rate"] = f"{item.turn_rate_deg_min:.6f}"
        if item.turn_radius_nm is not None:
            result["turn_radius"] = f"{item.turn_radius_nm:.6f}"
        return result

    @staticmethod
    def _effective_route(state: SimulationState) -> tuple[RouteWaypoint, ...]:
        manual_speed = abs(state.engine.effective_speed_order_kn or state.engine.user_speed_order_kn)
        mode = state.route.control_turn_mode
        return tuple(
            replace(
                waypoint,
                speed_kn=(
                    waypoint.speed_kn
                    if state.route.use_planned_speed and waypoint.speed_kn is not None
                    else manual_speed
                ),
                turn_mode=waypoint.turn_mode if state.route.use_planned_turns else mode,
                turn_rate_deg_min=(
                    waypoint.turn_rate_deg_min
                    if state.route.use_planned_turns or mode != "fixed_rot"
                    else state.route.control_turn_rate_deg_min
                ),
                turn_radius_nm=(
                    waypoint.turn_radius_nm
                    if state.route.use_planned_turns or mode != "fixed_radius"
                    else state.route.control_turn_radius_nm
                ),
            )
            for waypoint in state.route.waypoints
        )
