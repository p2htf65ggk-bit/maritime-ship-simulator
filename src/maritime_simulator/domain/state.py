# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass, field
import math

from .enums import CommandSource, ControlMode, InputValidity


DEFAULT_SENSOR_NOISE_PERIOD_S = math.tau / 0.37
MIN_SENSOR_NOISE_PERIOD_S = 0.1
MAX_SENSOR_NOISE_PERIOD_S = 60.0


def sensor_noise_settings(value: object) -> tuple[bool, float, float]:
    """Normalize current and legacy noise tuples to the current V2.1 shape."""
    if not isinstance(value, (list, tuple)):
        return False, 0.0, DEFAULT_SENSOR_NOISE_PERIOD_S
    enabled = bool(value[0]) if len(value) >= 1 else False
    try:
        amplitude = max(0.0, float(value[1])) if len(value) >= 2 else 0.0
    except (TypeError, ValueError):
        amplitude = 0.0
    try:
        period_s = float(value[2]) if len(value) >= 3 else DEFAULT_SENSOR_NOISE_PERIOD_S
    except (TypeError, ValueError):
        period_s = DEFAULT_SENSOR_NOISE_PERIOD_S
    period_s = max(MIN_SENSOR_NOISE_PERIOD_S, min(MAX_SENSOR_NOISE_PERIOD_S, period_s))
    return enabled, amplitude, period_s


def normalize_angle(value: float) -> float:
    return float(value) % 360.0


@dataclass(slots=True)
class PositionState:
    latitude_deg: float = 52.0
    longitude_deg: float = 3.0


@dataclass(slots=True)
class MotionState:
    heading_deg: float = 90.0
    cog_deg: float = 90.0
    stw_kn: float = 0.0
    sog_kn: float = 0.0
    longitudinal_water_kn: float = 0.0
    transverse_water_kn: float = 0.0
    transverse_ground_kn: float = 0.0
    forward_transverse_water_kn: float = 0.0
    forward_transverse_ground_kn: float = 0.0
    aft_transverse_water_kn: float = 0.0
    aft_transverse_ground_kn: float = 0.0
    rate_of_turn_deg_min: float = 0.0
    sway_kn: float = 0.0


@dataclass(slots=True)
class SteeringState:
    order_starboard_deg: float = 0.0
    response_starboard_deg: float = 0.0
    order_port_deg: float = 0.0
    response_port_deg: float = 0.0
    dual_rudder: bool = False
    command_source: CommandSource = CommandSource.USER


@dataclass(slots=True)
class EngineState:
    user_speed_order_kn: float = 0.0
    effective_speed_order_kn: float = 0.0
    command_source: CommandSource = CommandSource.USER


@dataclass(slots=True)
class BowThrusterState:
    user_order_percent: float = 0.0
    order_percent: float = 0.0
    response_percent: float = 0.0
    efficiency: float = 1.0
    force_kn: float = 0.0


@dataclass(slots=True)
class ExternalActuatorState:
    policy: str = "MANUAL"
    on_loss: str = "MANUAL"
    manual_available: bool = True
    # At most two sources per actuator; monotonic receipt time, not simulation time.
    samples: dict[str, tuple[float, float]] = field(default_factory=dict)
    active_source: str = ""
    age_s: float | None = None


@dataclass(slots=True)
class EnvironmentState:
    wind_from_deg: float = 0.0
    wind_speed_kn: float = 0.0
    current_from_deg: float = 0.0
    current_speed_kn: float = 0.0
    true_wind_from_deg: float = 0.0
    true_wind_speed_kn: float = 0.0
    relative_wind_from_bow_deg: float = 0.0
    relative_wind_speed_kn: float = 0.0
    wind_source: str = "manual"
    active_wind_source: str = "manual"
    current_source: str = "manual"
    active_current_source: str = "manual"


@dataclass(slots=True)
class DepthSourceState:
    requested: str = "manual"
    active: str = "manual"
    manual_depth_m: float = 18.0
    water_depth_m: float | None = None


@dataclass(slots=True)
class AuxiliarySensorState:
    depth_m: float = 18.0
    barometric_pressure_bar: float = 1.013
    air_temperature_c: float = 15.0
    water_temperature_c: float = 10.0
    relative_humidity_percent: float = 65.0
    absolute_humidity_percent: float = 8.0
    dew_point_c: float = 8.0
    xdr_air_temperature_c: float = 15.0
    xdr_relative_humidity_percent: float = 65.0
    xdr_dew_point_c: float = 8.0
    xdr_surface_pressure_bar: float = 1.013
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    water_distance_nm: float = 0.0
    ground_distance_nm: float = 0.0
    propeller_lever_demand_percent: float = 0.0
    thruster_number: int = 1
    thruster_rpm_demand_percent: float = 0.0
    thruster_pitch_demand_percent: float = 0.0
    thruster_azimuth_demand_deg: float = 0.0
    thruster_rpm_response_percent: float = 0.0
    thruster_pitch_response_percent: float = 0.0
    thruster_azimuth_response_deg: float = 0.0


@dataclass(slots=True)
class ExternalRudderState:
    input_policy: str = "ROR"
    input_type: str = "ROR"
    order_starboard_deg: float = 0.0
    order_port_deg: float | None = None
    response_starboard_deg: float = 0.0
    response_port_deg: float | None = None
    order_received_monotonic_s: float | None = None
    order_source: str = ""
    order_validity: InputValidity = InputValidity.UNAVAILABLE
    order_age_s: float | None = None
    response_received_monotonic_s: float | None = None
    response_source: str = ""
    response_validity: InputValidity = InputValidity.UNAVAILABLE
    response_age_s: float | None = None
    on_loss: str = "ZERO"
    manual_available: bool = False
    samples: dict[str, tuple[float, float | None, float]] = field(default_factory=dict)
    active_source: str = ""
    selected_validity: InputValidity = InputValidity.UNAVAILABLE
    selected_age_s: float | None = None
    last_source: str = ""
    held_order_starboard_deg: float = 0.0
    held_order_port_deg: float = 0.0

    @property
    def response_override(self) -> bool:
        return self.input_type == "RSA" and (
            bool(self.active_source) or (self.on_loss == "FREEZE" and bool(self.last_source))
        ) and self.input_policy != "MANUAL"

    @property
    def validity(self) -> InputValidity:
        return self.selected_validity

    @property
    def age_s(self) -> float | None:
        return self.selected_age_s


@dataclass(frozen=True, slots=True)
class RouteWaypoint:
    name: str
    latitude_deg: float
    longitude_deg: float
    speed_kn: float | None = None
    turn_rate_deg_min: float | None = None
    turn_radius_nm: float | None = None
    turn_mode: str = "fixed_rot"


@dataclass(slots=True)
class RouteState:
    waypoints: tuple[RouteWaypoint, ...] = ()
    progress_nm: float = 0.0
    xte_nm: float = 0.0
    next_waypoint_index: int = 0
    phase: str = "idle"
    end_action: str = "Stop"
    use_planned_speed: bool = False
    use_planned_turns: bool = True
    control_turn_mode: str = "fixed_rot"
    control_turn_rate_deg_min: float = 10.0
    control_turn_radius_nm: float = 0.10
    completed: bool = False


@dataclass(slots=True)
class SensorControlState:
    cog_follows_heading: bool = True
    cog_heading_offset_deg: float = 0.0
    heading_follows_rot: bool = False
    noise: dict[str, tuple[bool, float, float]] = field(default_factory=dict)


@dataclass(slots=True)
class SimulationState:
    position: PositionState = field(default_factory=PositionState)
    motion: MotionState = field(default_factory=MotionState)
    steering: SteeringState = field(default_factory=SteeringState)
    engine: EngineState = field(default_factory=EngineState)
    bow_thruster: BowThrusterState = field(default_factory=BowThrusterState)
    external_bow_thruster: ExternalActuatorState = field(default_factory=ExternalActuatorState)
    external_engine: ExternalActuatorState = field(default_factory=ExternalActuatorState)
    environment: EnvironmentState = field(default_factory=EnvironmentState)
    external_rudder: ExternalRudderState = field(default_factory=ExternalRudderState)
    auxiliary: AuxiliarySensorState = field(default_factory=AuxiliarySensorState)
    depth_source: DepthSourceState = field(default_factory=DepthSourceState)
    route: RouteState = field(default_factory=RouteState)
    sensor_control: SensorControlState = field(default_factory=SensorControlState)
    control_mode: ControlMode = ControlMode.SENSOR
    vessel_profile_id: str = "crew-transfer-vessel"
    simulation_time_s: float = 0.0
    time_scale: float = 1.0
    revision: int = 0

    def snapshot(self) -> "SimulationSnapshot":
        return SimulationSnapshot(
            revision=self.revision,
            simulation_time_s=self.simulation_time_s,
            time_scale=self.time_scale,
            control_mode=self.control_mode,
            vessel_profile_id=self.vessel_profile_id,
            latitude_deg=self.position.latitude_deg,
            longitude_deg=self.position.longitude_deg,
            heading_deg=self.motion.heading_deg,
            cog_deg=self.motion.cog_deg,
            stw_kn=self.motion.stw_kn,
            sog_kn=self.motion.sog_kn,
            rate_of_turn_deg_min=self.motion.rate_of_turn_deg_min,
            longitudinal_water_kn=self.motion.longitudinal_water_kn,
            transverse_water_kn=self.motion.transverse_water_kn,
            transverse_ground_kn=self.motion.transverse_ground_kn,
            forward_transverse_water_kn=self.motion.forward_transverse_water_kn,
            forward_transverse_ground_kn=self.motion.forward_transverse_ground_kn,
            aft_transverse_water_kn=self.motion.aft_transverse_water_kn,
            aft_transverse_ground_kn=self.motion.aft_transverse_ground_kn,
            speed_order_kn=self.engine.effective_speed_order_kn,
            speed_command_source=self.engine.command_source,
            rudder_order_starboard_deg=self.steering.order_starboard_deg,
            rudder_response_starboard_deg=self.steering.response_starboard_deg,
            rudder_order_port_deg=self.steering.order_port_deg,
            rudder_response_port_deg=self.steering.response_port_deg,
            dual_rudder=self.steering.dual_rudder,
            rudder_command_source=self.steering.command_source,
            wind_from_deg=self.environment.wind_from_deg,
            wind_speed_kn=self.environment.wind_speed_kn,
            true_wind_from_deg=self.environment.true_wind_from_deg,
            true_wind_speed_kn=self.environment.true_wind_speed_kn,
            relative_wind_from_bow_deg=self.environment.relative_wind_from_bow_deg,
            relative_wind_speed_kn=self.environment.relative_wind_speed_kn,
            current_from_deg=self.environment.current_from_deg,
            current_speed_kn=self.environment.current_speed_kn,
            external_rudder_validity=self.external_rudder.validity,
            external_rudder_age_s=self.external_rudder.age_s,
            external_rudder_policy=self.external_rudder.input_policy,
            external_rudder_input_type=self.external_rudder.input_type,
            route_waypoints=self.route.waypoints,
            route_progress_nm=self.route.progress_nm,
            xte_nm=self.route.xte_nm,
            next_waypoint_index=self.route.next_waypoint_index,
            track_phase=self.route.phase,
            route_completed=self.route.completed,
            route_end_action=self.route.end_action,
            use_planned_speed=self.route.use_planned_speed,
            use_planned_turns=self.route.use_planned_turns,
            control_turn_mode=self.route.control_turn_mode,
            control_turn_rate_deg_min=self.route.control_turn_rate_deg_min,
            control_turn_radius_nm=self.route.control_turn_radius_nm,
            cog_follows_heading=self.sensor_control.cog_follows_heading,
            heading_follows_rot=self.sensor_control.heading_follows_rot,
            sensor_noise=tuple(sorted(self.sensor_control.noise.items())),
            depth_m=self.auxiliary.depth_m,
            depth_source=self.depth_source.requested,
            active_depth_source=self.depth_source.active,
            manual_depth_m=(self.auxiliary.depth_m if self.depth_source.requested == "manual"
                            else self.depth_source.manual_depth_m),
            chart_water_depth_m=self.depth_source.water_depth_m,
            wind_source=self.environment.wind_source,
            active_wind_source=self.environment.active_wind_source,
            current_source=self.environment.current_source,
            active_current_source=self.environment.active_current_source,
            barometric_pressure_bar=self.auxiliary.barometric_pressure_bar,
            air_temperature_c=self.auxiliary.air_temperature_c,
            water_temperature_c=self.auxiliary.water_temperature_c,
            relative_humidity_percent=self.auxiliary.relative_humidity_percent,
            absolute_humidity_percent=self.auxiliary.absolute_humidity_percent,
            dew_point_c=self.auxiliary.dew_point_c,
            xdr_air_temperature_c=self.auxiliary.xdr_air_temperature_c,
            xdr_relative_humidity_percent=self.auxiliary.xdr_relative_humidity_percent,
            xdr_dew_point_c=self.auxiliary.xdr_dew_point_c,
            xdr_surface_pressure_bar=self.auxiliary.xdr_surface_pressure_bar,
            pitch_deg=self.auxiliary.pitch_deg,
            roll_deg=self.auxiliary.roll_deg,
            water_distance_nm=self.auxiliary.water_distance_nm,
            ground_distance_nm=self.auxiliary.ground_distance_nm,
            propeller_lever_demand_percent=self.auxiliary.propeller_lever_demand_percent,
            thruster_number=self.auxiliary.thruster_number,
            thruster_rpm_demand_percent=self.auxiliary.thruster_rpm_demand_percent,
            thruster_pitch_demand_percent=self.auxiliary.thruster_pitch_demand_percent,
            thruster_azimuth_demand_deg=self.auxiliary.thruster_azimuth_demand_deg,
            thruster_rpm_response_percent=self.auxiliary.thruster_rpm_response_percent,
            thruster_pitch_response_percent=self.auxiliary.thruster_pitch_response_percent,
            thruster_azimuth_response_deg=self.auxiliary.thruster_azimuth_response_deg,
            bow_thruster_order_percent=self.bow_thruster.order_percent,
            bow_thruster_response_percent=self.bow_thruster.response_percent,
            bow_thruster_efficiency=self.bow_thruster.efficiency,
            bow_thruster_force_kn=self.bow_thruster.force_kn,
            bow_thruster_policy=self.external_bow_thruster.policy,
            bow_thruster_active_source=self.external_bow_thruster.active_source,
            engine_input_policy=self.external_engine.policy,
            engine_active_source=self.external_engine.active_source,
            bow_thruster_manual_enabled=self.external_bow_thruster.manual_available,
            engine_manual_enabled=self.external_engine.manual_available,
            external_rudder_manual_enabled=self.external_rudder.manual_available,
            external_rudder_active_source=self.external_rudder.active_source,
        )


@dataclass(frozen=True, slots=True)
class SimulationSnapshot:
    revision: int
    simulation_time_s: float
    time_scale: float
    control_mode: ControlMode
    vessel_profile_id: str
    latitude_deg: float
    longitude_deg: float
    heading_deg: float
    cog_deg: float
    stw_kn: float
    sog_kn: float
    rate_of_turn_deg_min: float
    longitudinal_water_kn: float
    transverse_water_kn: float
    transverse_ground_kn: float
    forward_transverse_water_kn: float
    forward_transverse_ground_kn: float
    aft_transverse_water_kn: float
    aft_transverse_ground_kn: float
    speed_order_kn: float
    speed_command_source: CommandSource
    rudder_order_starboard_deg: float
    rudder_response_starboard_deg: float
    rudder_order_port_deg: float
    rudder_response_port_deg: float
    dual_rudder: bool
    rudder_command_source: CommandSource
    wind_from_deg: float
    wind_speed_kn: float
    true_wind_from_deg: float
    true_wind_speed_kn: float
    relative_wind_from_bow_deg: float
    relative_wind_speed_kn: float
    current_from_deg: float
    current_speed_kn: float
    external_rudder_validity: InputValidity
    external_rudder_age_s: float | None
    external_rudder_policy: str
    external_rudder_input_type: str
    route_waypoints: tuple[RouteWaypoint, ...]
    route_progress_nm: float
    xte_nm: float
    next_waypoint_index: int
    track_phase: str
    route_completed: bool
    route_end_action: str
    use_planned_speed: bool
    use_planned_turns: bool
    control_turn_mode: str
    control_turn_rate_deg_min: float
    control_turn_radius_nm: float
    cog_follows_heading: bool
    heading_follows_rot: bool
    sensor_noise: tuple[tuple[str, tuple[bool, float, float]], ...]
    depth_m: float
    barometric_pressure_bar: float
    air_temperature_c: float
    water_temperature_c: float
    relative_humidity_percent: float
    absolute_humidity_percent: float
    dew_point_c: float
    pitch_deg: float
    roll_deg: float
    water_distance_nm: float
    ground_distance_nm: float
    propeller_lever_demand_percent: float
    thruster_number: int
    thruster_rpm_demand_percent: float
    thruster_pitch_demand_percent: float
    thruster_azimuth_demand_deg: float
    thruster_rpm_response_percent: float
    thruster_pitch_response_percent: float
    thruster_azimuth_response_deg: float
    xdr_air_temperature_c: float
    xdr_relative_humidity_percent: float
    xdr_dew_point_c: float
    xdr_surface_pressure_bar: float
    bow_thruster_order_percent: float = 0.0
    bow_thruster_response_percent: float = 0.0
    bow_thruster_efficiency: float = 1.0
    bow_thruster_force_kn: float = 0.0
    bow_thruster_policy: str = "MANUAL"
    bow_thruster_active_source: str = ""
    engine_input_policy: str = "MANUAL"
    engine_active_source: str = ""
    bow_thruster_manual_enabled: bool = True
    engine_manual_enabled: bool = True
    external_rudder_manual_enabled: bool = False
    external_rudder_active_source: str = ""
    depth_source: str = "manual"
    active_depth_source: str = "manual"
    manual_depth_m: float = 18.0
    chart_water_depth_m: float | None = None
    wind_source: str = "manual"
    active_wind_source: str = "manual"
    current_source: str = "manual"
    active_current_source: str = "manual"
