# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import math

from maritime_simulator.domain.commands import (
    Command,
    ExternalRudderOrder,
    ExternalRudderResponse,
    SeekRoute,
    SelectVesselProfile,
    SetControlMode,
    SetAuxiliarySensors,
    SetEnvironment,
    SetExternalRudderInputType,
    SetPosition,
    SetRoute,
    SetRouteOptions,
    UpdateRoute,
    SetRudderOrder,
    SetSensorCoupling,
    SetSensorNoise,
    SetSensorValues,
    SetSpeedOrder,
    SetBowThrusterOrder,
    SetExternalActuatorPolicy,
    ExternalActuatorOrder,
    SetTimeScale,
)
from maritime_simulator.domain.enums import (
    CommandSource,
    ControlMode,
    ExternalRudderControlPolicy,
    InputValidity,
)
from maritime_simulator.domain.profiles import VesselProfile, VesselProfileStore
from maritime_simulator.domain.external_control import actuator_sources, ACTUATOR_PROTOCOLS, input_loss_mode
from maritime_simulator.domain.state import (
    MAX_SENSOR_NOISE_PERIOD_S,
    MIN_SENSOR_NOISE_PERIOD_S,
    SimulationState,
    normalize_angle,
    sensor_noise_settings,
)
from maritime_simulator.simulation.route_geometry import RouteGeometry, bearing_deg


class CommandArbitrator:
    """Own command priority and prevent stale producers from overwriting controls."""

    def __init__(self, *, external_rudder_timeout_s: float = 2.5):
        if external_rudder_timeout_s <= 0.0:
            raise ValueError("external_rudder_timeout_s must be positive")
        self.external_rudder_timeout_s = float(external_rudder_timeout_s)
        self._user_rudder_starboard_deg = 0.0
        self._user_rudder_port_deg = 0.0
        self._track_rudder_starboard_deg = 0.0
        self._track_rudder_port_deg = 0.0

    def apply_command(
        self,
        state: SimulationState,
        command: Command,
        profiles: VesselProfileStore,
    ) -> float | None:
        profile = profiles.get(state.vessel_profile_id)
        if isinstance(command, SetControlMode):
            state.control_mode = command.mode
        elif isinstance(command, SetSpeedOrder):
            if math.isfinite(command.speed_kn) and state.external_engine.manual_available:
                state.engine.user_speed_order_kn = self._clamp_speed(command.speed_kn, profile)
        elif isinstance(command, SetBowThrusterOrder):
            if math.isfinite(command.percent) and state.external_bow_thruster.manual_available:
                state.bow_thruster.user_order_percent = max(-100.0, min(100.0, command.percent))
        elif isinstance(command, SetExternalActuatorPolicy):
            actuator_sources(command.actuator, command.policy)
            external = getattr(state, f"external_{command.actuator}")
            on_loss = input_loss_mode(command.on_loss or external.on_loss)
            if external.policy != command.policy or external.on_loss != on_loss or command.reset:
                if command.actuator == "bow_thruster" and external.active_source:
                    state.bow_thruster.user_order_percent = 0.0
                external.policy = command.policy
                external.on_loss = on_loss
                external.samples.clear()
                external.active_source = ""
                external.manual_available = command.policy == "MANUAL" or on_loss == "MANUAL"
        elif isinstance(command, ExternalActuatorOrder):
            if command.actuator in ACTUATOR_PROTOCOLS and command.protocol in {ACTUATOR_PROTOCOLS[command.actuator], "XDR"}:
                if math.isfinite(command.percent) and -100.0 <= command.percent <= 100.0 and math.isfinite(command.received_monotonic_s):
                    external = getattr(state, f"external_{command.actuator}")
                    external.samples[command.protocol] = (command.percent, command.received_monotonic_s)
        elif isinstance(command, SetRudderOrder):
            if not math.isfinite(command.angle_deg) or (
                state.control_mode is ControlMode.EXTERNAL_RUDDER and not state.external_rudder.manual_available
            ):
                return None
            value = self._clamp_rudder(command.angle_deg, profile)
            self._user_rudder_starboard_deg = value
            self._user_rudder_port_deg = value
        elif isinstance(command, SetSensorValues):
            self._apply_sensor_values(state, command, profile)
        elif isinstance(command, SetSensorCoupling):
            if command.cog_follows_heading is not None:
                state.sensor_control.cog_follows_heading = bool(command.cog_follows_heading)
                state.sensor_control.cog_heading_offset_deg = (
                    state.motion.cog_deg - state.motion.heading_deg + 540.0
                ) % 360.0 - 180.0
            if command.heading_follows_rot is not None:
                state.sensor_control.heading_follows_rot = bool(command.heading_follows_rot)
        elif isinstance(command, SetSensorNoise):
            current = sensor_noise_settings(state.sensor_control.noise.get(command.name))
            enabled = current[0] if command.enabled is None else bool(command.enabled)
            amplitude = current[1] if command.amplitude is None else max(0.0, float(command.amplitude))
            period_s = current[2] if command.period_s is None else max(
                MIN_SENSOR_NOISE_PERIOD_S,
                min(MAX_SENSOR_NOISE_PERIOD_S, float(command.period_s)),
            )
            state.sensor_control.noise[command.name] = (enabled, amplitude, period_s)
        elif isinstance(command, SetAuxiliarySensors):
            self._apply_auxiliary_values(state, command, profile)
        elif isinstance(command, SetTimeScale):
            return float(command.scale)
        elif isinstance(command, SetPosition):
            if not -90.0 <= command.latitude_deg <= 90.0:
                raise ValueError("Latitude must be in [-90, 90]")
            if not -180.0 <= command.longitude_deg <= 180.0:
                raise ValueError("Longitude must be in [-180, 180]")
            state.position.latitude_deg = float(command.latitude_deg)
            state.position.longitude_deg = float(command.longitude_deg)
            if command.heading_deg is not None:
                state.motion.heading_deg = normalize_angle(command.heading_deg)
            self._project_route_state(state, phase="position_set")
        elif isinstance(command, SetEnvironment):
            if command.wind_from_deg is not None:
                state.environment.wind_from_deg = normalize_angle(command.wind_from_deg)
            if command.wind_speed_kn is not None:
                state.environment.wind_speed_kn = max(0.0, float(command.wind_speed_kn))
            if command.current_from_deg is not None:
                state.environment.current_from_deg = normalize_angle(command.current_from_deg)
            if command.current_speed_kn is not None:
                state.environment.current_speed_kn = max(0.0, float(command.current_speed_kn))
            if command.depth_m is not None:
                state.auxiliary.depth_m = max(0.0, float(command.depth_m))
        elif isinstance(command, SelectVesselProfile):
            selected = profiles.get(command.profile_id)
            state.vessel_profile_id = selected.profile_id
            state.engine.user_speed_order_kn = self._clamp_speed(state.engine.user_speed_order_kn, selected)
            self._user_rudder_starboard_deg = self._clamp_rudder(self._user_rudder_starboard_deg, selected)
            self._user_rudder_port_deg = self._clamp_rudder(self._user_rudder_port_deg, selected)
        elif isinstance(command, SetRoute):
            state.route.waypoints = tuple(command.waypoints)
            state.route.progress_nm = 0.0
            state.route.xte_nm = 0.0
            state.route.next_waypoint_index = 1 if len(command.waypoints) > 1 else 0
            state.route.phase = "idle"
            state.route.completed = False
            if command.waypoints:
                first = command.waypoints[0]
                state.position.latitude_deg = first.latitude_deg
                state.position.longitude_deg = first.longitude_deg
                if len(command.waypoints) > 1:
                    heading = bearing_deg(first, command.waypoints[1])
                    state.motion.heading_deg = heading
                    state.motion.cog_deg = heading
        elif isinstance(command, UpdateRoute):
            state.route.waypoints = tuple(command.waypoints)
            self._project_route_state(state, phase="route_edited")
        elif isinstance(command, SetRouteOptions):
            if command.use_planned_speed is not None:
                state.route.use_planned_speed = bool(command.use_planned_speed)
            if command.use_planned_turns is not None:
                state.route.use_planned_turns = bool(command.use_planned_turns)
            if command.control_turn_mode is not None:
                mode = str(command.control_turn_mode)
                if mode not in {"fixed_rot", "fixed_radius"}:
                    raise ValueError(f"Unsupported control turn mode: {mode}")
                state.route.control_turn_mode = mode
            if command.control_turn_rate_deg_min is not None:
                state.route.control_turn_rate_deg_min = max(
                    0.001,
                    min(profile.max_rot_deg_min, float(command.control_turn_rate_deg_min)),
                )
            if command.control_turn_radius_nm is not None:
                state.route.control_turn_radius_nm = max(
                    0.001,
                    float(command.control_turn_radius_nm),
                )
            if command.end_action is not None:
                action = str(command.end_action).title()
                if action not in {"Stop", "Restart", "Reverse"}:
                    raise ValueError(f"Unsupported route end action: {command.end_action}")
                state.route.end_action = action
        elif isinstance(command, SeekRoute):
            geometry = RouteGeometry(state.route.waypoints)
            progress = max(0.0, min(geometry.total_distance_nm, float(command.progress_nm)))
            position = geometry.position_at(progress)
            if position is not None:
                state.position.latitude_deg, state.position.longitude_deg = position
            next_index = geometry.next_waypoint_index(progress)
            if len(state.route.waypoints) >= 2:
                leg_end = max(1, min(next_index, len(state.route.waypoints) - 1))
                leg_heading = bearing_deg(
                    state.route.waypoints[leg_end - 1],
                    state.route.waypoints[leg_end],
                )
                state.motion.heading_deg = leg_heading
                state.motion.cog_deg = leg_heading
            state.route.progress_nm = progress
            state.route.xte_nm = 0.0
            state.route.next_waypoint_index = next_index
            state.route.phase = "seek"
            state.route.completed = False
        elif isinstance(command, ExternalRudderOrder):
            if command.protocol not in {"ROR", "XDR_ORDER"} or not all(math.isfinite(value) for value in (
                command.starboard_or_single_deg, command.received_monotonic_s,
                command.port_deg if command.port_deg is not None else 0.0,
            )):
                return None
            if not math.isfinite(command.starboard_or_single_deg):
                state.external_rudder.order_validity = InputValidity.INVALID
            else:
                state.external_rudder.order_starboard_deg = self._clamp_rudder(
                    command.starboard_or_single_deg,
                    profile,
                )
                state.external_rudder.order_port_deg = (
                    None
                    if command.port_deg is None
                    else self._clamp_rudder(command.port_deg, profile)
                )
                state.external_rudder.order_received_monotonic_s = float(command.received_monotonic_s)
                state.external_rudder.order_source = command.source
                state.external_rudder.order_validity = InputValidity.VALID
                state.external_rudder.order_age_s = 0.0
                state.external_rudder.samples[command.protocol] = (
                    state.external_rudder.order_starboard_deg, state.external_rudder.order_port_deg,
                    float(command.received_monotonic_s),
                )
                if state.external_rudder.input_type == "ROR":
                    state.steering.dual_rudder = command.port_deg is not None
        elif isinstance(command, ExternalRudderResponse):
            if command.protocol not in {"RSA", "XDR_RESPONSE"} or not all(math.isfinite(value) for value in (
                command.starboard_or_single_deg, command.received_monotonic_s,
                command.port_deg if command.port_deg is not None else 0.0,
            )):
                return None
            if not math.isfinite(command.starboard_or_single_deg):
                state.external_rudder.response_validity = InputValidity.INVALID
            else:
                state.external_rudder.response_starboard_deg = self._clamp_rudder(
                    command.starboard_or_single_deg,
                    profile,
                )
                state.external_rudder.response_port_deg = (
                    None
                    if command.port_deg is None
                    else self._clamp_rudder(command.port_deg, profile)
                )
                state.external_rudder.response_received_monotonic_s = float(command.received_monotonic_s)
                state.external_rudder.response_source = command.source
                state.external_rudder.response_validity = InputValidity.VALID
                state.external_rudder.response_age_s = 0.0
                state.external_rudder.samples[command.protocol] = (
                    state.external_rudder.response_starboard_deg, state.external_rudder.response_port_deg,
                    float(command.received_monotonic_s),
                )
                if state.external_rudder.input_type == "RSA":
                    state.steering.dual_rudder = command.port_deg is not None
        elif isinstance(command, SetExternalRudderInputType):
            policy = ExternalRudderControlPolicy.parse(command.input_type)
            state.external_rudder.on_loss = input_loss_mode(command.on_loss or state.external_rudder.on_loss)
            state.external_rudder.input_policy = policy.value
            state.external_rudder.manual_available = policy is ExternalRudderControlPolicy.MANUAL
            if command.reset:
                external = state.external_rudder
                external.samples.clear()
                external.active_source = external.last_source = ""
                for prefix in ("order", "response"):
                    setattr(external, f"{prefix}_received_monotonic_s", None)
                    setattr(external, f"{prefix}_validity", InputValidity.UNAVAILABLE)
            if not policy.allows_fallback:
                state.external_rudder.input_type = "RSA" if policy.preferred_source in {"RSA", "XDR_RESPONSE"} else "ROR"
        else:
            raise TypeError(f"Unsupported command: {type(command).__name__}")
        return None

    @staticmethod
    def _project_route_state(state: SimulationState, *, phase: str) -> None:
        geometry = RouteGeometry(state.route.waypoints)
        if len(state.route.waypoints) < 2:
            state.route.progress_nm = 0.0
            state.route.xte_nm = 0.0
            state.route.next_waypoint_index = 0
            state.route.phase = "idle"
            state.route.completed = False
            return
        projection = geometry.project(
            state.position.latitude_deg,
            state.position.longitude_deg,
        )
        state.route.progress_nm = projection.progress_nm
        state.route.xte_nm = projection.cross_track_nm
        state.route.next_waypoint_index = geometry.next_waypoint_index(projection.progress_nm)
        state.route.phase = phase
        state.route.completed = False

    def set_track_rudder_order(self, starboard_deg: float, port_deg: float | None, profile: VesselProfile) -> None:
        self._track_rudder_starboard_deg = self._clamp_rudder(starboard_deg, profile)
        self._track_rudder_port_deg = self._clamp_rudder(
            starboard_deg if port_deg is None else port_deg,
            profile,
        )

    def resolve(self, state: SimulationState, profile: VesselProfile, monotonic_s: float) -> None:
        engine_percent = self._resolve_actuator(state, "engine", monotonic_s)
        bow_percent = self._resolve_actuator(state, "bow_thruster", monotonic_s)
        if engine_percent is not None:
            state.engine.user_speed_order_kn = profile.speed_from_lever_percent(engine_percent)
        if bow_percent is not None:
            state.bow_thruster.user_order_percent = bow_percent
        state.bow_thruster.order_percent = state.bow_thruster.user_order_percent if profile.bow_thruster_power_kw > 0.0 else 0.0
        if (
            state.control_mode is ControlMode.TRACK
            and state.route.completed
            and state.route.end_action == "Stop"
        ):
            state.engine.effective_speed_order_kn = 0.0
            state.engine.command_source = CommandSource.ROUTE_END
        elif state.control_mode is ControlMode.TRACK and state.route.use_planned_speed and state.external_engine.policy == "MANUAL":
            waypoint = None
            if state.route.waypoints:
                index = max(0, min(len(state.route.waypoints) - 1, state.route.next_waypoint_index))
                waypoint = state.route.waypoints[index]
            if waypoint is not None and waypoint.speed_kn is not None:
                state.engine.effective_speed_order_kn = self._clamp_speed(waypoint.speed_kn, profile)
                state.engine.command_source = CommandSource.ROUTE_PLAN
            else:
                state.engine.effective_speed_order_kn = self._clamp_speed(state.engine.user_speed_order_kn, profile)
                state.engine.command_source = CommandSource.USER
        else:
            state.engine.effective_speed_order_kn = self._clamp_speed(state.engine.user_speed_order_kn, profile)
            state.engine.command_source = CommandSource.USER
        if engine_percent is not None and state.engine.command_source is not CommandSource.ROUTE_END:
            state.engine.command_source = CommandSource.EXTERNAL_PROPULSION
        self._refresh_external_validity(state, monotonic_s)
        self._select_external_rudder_source(state, monotonic_s)

        if state.control_mode is ControlMode.TRACK:
            starboard = self._track_rudder_starboard_deg
            port = self._track_rudder_port_deg
            source = CommandSource.TRACK_CONTROLLER
        elif state.control_mode is ControlMode.EXTERNAL_RUDDER and not state.external_rudder.manual_available:
            if state.external_rudder.validity is InputValidity.VALID:
                if state.external_rudder.input_type == "RSA":
                    # RSA owns the physical response layer. A concurrently received
                    # ROR is still useful as the operator/order indication.
                    if state.external_rudder.order_validity is InputValidity.VALID:
                        starboard = state.external_rudder.order_starboard_deg
                        port = state.external_rudder.order_port_deg
                    else:
                        starboard = 0.0
                        port = 0.0
                    source = CommandSource.EXTERNAL_RSA
                else:
                    starboard = state.external_rudder.order_starboard_deg
                    port = state.external_rudder.order_port_deg
                    source = CommandSource.EXTERNAL_ROR
            elif state.external_rudder.on_loss == "FREEZE":
                starboard = state.external_rudder.held_order_starboard_deg
                port = state.external_rudder.held_order_port_deg
                source = CommandSource.SAFETY
            else:
                starboard = 0.0
                port = 0.0
                source = CommandSource.SAFETY
        elif state.control_mode is ControlMode.HEADING_RESERVED:
            starboard = 0.0
            port = 0.0
            source = CommandSource.SAFETY
        else:
            starboard = self._user_rudder_starboard_deg
            port = self._user_rudder_port_deg
            source = CommandSource.USER

        state.steering.order_starboard_deg = self._clamp_rudder(starboard, profile)
        state.steering.order_port_deg = self._clamp_rudder(
            starboard if port is None else port,
            profile,
        )
        state.steering.command_source = source
        if state.external_rudder.active_source:
            state.external_rudder.held_order_starboard_deg = state.steering.order_starboard_deg
            state.external_rudder.held_order_port_deg = state.steering.order_port_deg

    def _resolve_actuator(self, state: SimulationState, actuator: str, now: float) -> float | None:
        external = getattr(state, f"external_{actuator}")
        previous = external.active_source
        external.active_source = ""
        external.age_s = None
        sources = actuator_sources(actuator, external.policy) if state.control_mode is not ControlMode.SENSOR else ()
        external.manual_available = not sources or external.on_loss == "MANUAL"
        for source in sources:
            sample = external.samples.get(source)
            if sample is not None:
                value, received = sample
                age = max(0.0, now - received)
                if age <= self.external_rudder_timeout_s:
                    external.active_source = source
                    external.manual_available = False
                    external.age_s = age
                    return value
        if sources and external.on_loss == "ZERO":
            return 0.0
        if previous and actuator == "bow_thruster" and external.on_loss == "MANUAL":
            state.bow_thruster.user_order_percent = 0.0
        return None

    def _refresh_external_validity(self, state: SimulationState, monotonic_s: float) -> None:
        for prefix in ("order", "response"):
            received = getattr(state.external_rudder, f"{prefix}_received_monotonic_s")
            validity_name = f"{prefix}_validity"
            age_name = f"{prefix}_age_s"
            if received is None:
                setattr(state.external_rudder, age_name, None)
                if getattr(state.external_rudder, validity_name) is not InputValidity.INVALID:
                    setattr(state.external_rudder, validity_name, InputValidity.UNAVAILABLE)
                continue
            age = max(0.0, float(monotonic_s) - received)
            setattr(state.external_rudder, age_name, age)
            if getattr(state.external_rudder, validity_name) is not InputValidity.INVALID:
                setattr(
                    state.external_rudder,
                    validity_name,
                    InputValidity.VALID if age <= self.external_rudder_timeout_s else InputValidity.STALE,
                )

    def _select_external_rudder_source(self, state: SimulationState, monotonic_s: float) -> None:
        external = state.external_rudder
        policy = ExternalRudderControlPolicy.parse(external.input_policy)
        previous = external.active_source
        external.active_source = ""
        external.selected_validity = InputValidity.UNAVAILABLE
        external.selected_age_s = None
        for source in policy.sources:
            sample = external.samples.get(source)
            if sample is None:
                continue
            starboard, port, received = sample
            age = max(0.0, monotonic_s - received)
            if external.selected_age_s is None:
                external.selected_age_s = age
                external.selected_validity = InputValidity.STALE
            if age > self.external_rudder_timeout_s:
                continue
            external.active_source = external.last_source = source
            external.selected_age_s = age
            external.selected_validity = InputValidity.VALID
            prefix = "response" if source in {"RSA", "XDR_RESPONSE"} else "order"
            external.input_type = "RSA" if prefix == "response" else "ROR"
            setattr(external, f"{prefix}_starboard_deg", starboard)
            setattr(external, f"{prefix}_port_deg", port)
            setattr(external, f"{prefix}_validity", InputValidity.VALID)
            state.steering.dual_rudder = port is not None
            break
        # Unselected response reports must not change a frozen physical rudder.
        if not external.active_source and external.on_loss == "FREEZE" and external.last_source:
            starboard, port, _received = external.samples[external.last_source]
            prefix = "response" if external.last_source in {"RSA", "XDR_RESPONSE"} else "order"
            setattr(external, f"{prefix}_starboard_deg", starboard)
            setattr(external, f"{prefix}_port_deg", port)
            state.steering.dual_rudder = port is not None
        external.manual_available = (
            state.control_mode is not ControlMode.EXTERNAL_RUDDER or not policy.sources
            or (not external.active_source and external.on_loss == "MANUAL")
        )
        if previous and not external.active_source and external.on_loss == "MANUAL":
            self._user_rudder_starboard_deg = self._user_rudder_port_deg = 0.0

    def _apply_sensor_values(
        self,
        state: SimulationState,
        command: SetSensorValues,
        profile: VesselProfile,
    ) -> None:
        if state.control_mode is not ControlMode.SENSOR:
            raise ValueError("Manual sensor values are only accepted in Sensor Control mode")
        if command.latitude_deg is not None:
            if not -90.0 <= command.latitude_deg <= 90.0:
                raise ValueError("Latitude must be in [-90, 90]")
            state.position.latitude_deg = float(command.latitude_deg)
        if command.longitude_deg is not None:
            if not -180.0 <= command.longitude_deg <= 180.0:
                raise ValueError("Longitude must be in [-180, 180]")
            state.position.longitude_deg = float(command.longitude_deg)
        if command.heading_deg is not None:
            state.motion.heading_deg = normalize_angle(command.heading_deg)
            if state.sensor_control.cog_follows_heading and command.cog_deg is None:
                state.motion.cog_deg = normalize_angle(
                    state.motion.heading_deg + state.sensor_control.cog_heading_offset_deg
                )
        if command.cog_deg is not None:
            state.motion.cog_deg = normalize_angle(command.cog_deg)
            state.sensor_control.cog_heading_offset_deg = (
                state.motion.cog_deg - state.motion.heading_deg + 540.0
            ) % 360.0 - 180.0
        if command.sog_kn is not None:
            state.motion.sog_kn = float(command.sog_kn)
        if command.stw_kn is not None:
            state.motion.stw_kn = float(command.stw_kn)
            state.motion.longitudinal_water_kn = float(command.stw_kn)
        if command.rot_deg_min is not None:
            state.motion.rate_of_turn_deg_min = float(command.rot_deg_min)
        if command.xte_nm is not None:
            state.route.xte_nm = float(command.xte_nm)
        if command.dual_rudder is not None:
            state.steering.dual_rudder = bool(command.dual_rudder)
        if command.rudder_order_starboard_deg is not None:
            value = self._clamp_rudder(command.rudder_order_starboard_deg, profile)
            state.steering.order_starboard_deg = value
            self._user_rudder_starboard_deg = value
        if command.rudder_response_starboard_deg is not None:
            state.steering.response_starboard_deg = self._clamp_rudder(
                command.rudder_response_starboard_deg,
                profile,
            )
        if command.rudder_order_port_deg is not None:
            value = self._clamp_rudder(command.rudder_order_port_deg, profile)
            state.steering.order_port_deg = value
            self._user_rudder_port_deg = value
        if command.rudder_response_port_deg is not None:
            state.steering.response_port_deg = self._clamp_rudder(
                command.rudder_response_port_deg,
                profile,
            )
        if command.depth_m is not None:
            state.auxiliary.depth_m = max(0.0, float(command.depth_m))

    def _apply_auxiliary_values(
        self,
        state: SimulationState,
        command: SetAuxiliarySensors,
        profile: VesselProfile,
    ) -> None:
        if command.dual_rudder is not None:
            state.steering.dual_rudder = bool(command.dual_rudder)
            if state.steering.dual_rudder:
                state.steering.order_port_deg = state.steering.order_starboard_deg
                state.steering.response_port_deg = state.steering.response_starboard_deg
        if state.control_mode is ControlMode.SENSOR:
            for name in (
                "transverse_water_kn", "transverse_ground_kn",
                "forward_transverse_water_kn", "forward_transverse_ground_kn",
                "aft_transverse_water_kn", "aft_transverse_ground_kn",
            ):
                value = getattr(command, name)
                if value is not None:
                    setattr(state.motion, name, float(value))
        for name in (
            "pitch_deg", "roll_deg", "water_distance_nm", "ground_distance_nm",
            "propeller_lever_demand_percent", "thruster_rpm_demand_percent",
            "thruster_pitch_demand_percent", "thruster_azimuth_demand_deg",
            "thruster_rpm_response_percent", "thruster_pitch_response_percent",
            "thruster_azimuth_response_deg", "barometric_pressure_bar",
            "air_temperature_c", "water_temperature_c", "relative_humidity_percent",
            "absolute_humidity_percent", "dew_point_c", "xdr_air_temperature_c",
            "xdr_relative_humidity_percent", "xdr_dew_point_c",
            "xdr_surface_pressure_bar",
        ):
            value = getattr(command, name)
            if value is not None:
                setattr(state.auxiliary, name, float(value))
        if command.thruster_number is not None:
            state.auxiliary.thruster_number = max(1, int(command.thruster_number))

        # Manual steering sensors remain confined to Sensor Control. In model modes
        # RSA is always the steering-gear response and both rudders stay synchronous.
        if state.control_mode is not ControlMode.SENSOR and state.steering.dual_rudder:
            state.steering.order_port_deg = self._clamp_rudder(state.steering.order_starboard_deg, profile)
            state.steering.response_port_deg = self._clamp_rudder(state.steering.response_starboard_deg, profile)

    @staticmethod
    def _clamp_speed(value: float, profile: VesselProfile) -> float:
        return max(-profile.max_astern_kn, min(profile.max_speed_kn, float(value)))

    @staticmethod
    def _clamp_rudder(value: float, profile: VesselProfile) -> float:
        return max(-profile.max_rudder_deg, min(profile.max_rudder_deg, float(value)))
