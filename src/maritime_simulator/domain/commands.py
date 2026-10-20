# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass

from .enums import ControlMode
from .state import RouteWaypoint


@dataclass(frozen=True, slots=True)
class SetControlMode:
    mode: ControlMode


@dataclass(frozen=True, slots=True)
class SetSpeedOrder:
    speed_kn: float


@dataclass(frozen=True, slots=True)
class SetBowThrusterOrder:
    percent: float


@dataclass(frozen=True, slots=True)
class SetExternalActuatorPolicy:
    actuator: str
    policy: str
    reset: bool = False
    on_loss: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalActuatorOrder:
    actuator: str
    protocol: str
    percent: float
    received_monotonic_s: float


@dataclass(frozen=True, slots=True)
class SetRudderOrder:
    angle_deg: float


@dataclass(frozen=True, slots=True)
class SetSensorValues:
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    heading_deg: float | None = None
    cog_deg: float | None = None
    sog_kn: float | None = None
    stw_kn: float | None = None
    rot_deg_min: float | None = None
    xte_nm: float | None = None
    rudder_order_starboard_deg: float | None = None
    rudder_response_starboard_deg: float | None = None
    rudder_order_port_deg: float | None = None
    rudder_response_port_deg: float | None = None
    dual_rudder: bool | None = None
    depth_m: float | None = None


@dataclass(frozen=True, slots=True)
class SetSensorCoupling:
    cog_follows_heading: bool | None = None
    heading_follows_rot: bool | None = None


@dataclass(frozen=True, slots=True)
class SetSensorNoise:
    name: str
    enabled: bool | None = None
    amplitude: float | None = None
    period_s: float | None = None


@dataclass(frozen=True, slots=True)
class SetAuxiliarySensors:
    dual_rudder: bool | None = None
    pitch_deg: float | None = None
    roll_deg: float | None = None
    water_distance_nm: float | None = None
    ground_distance_nm: float | None = None
    propeller_lever_demand_percent: float | None = None
    thruster_number: int | None = None
    thruster_rpm_demand_percent: float | None = None
    thruster_pitch_demand_percent: float | None = None
    thruster_azimuth_demand_deg: float | None = None
    thruster_rpm_response_percent: float | None = None
    thruster_pitch_response_percent: float | None = None
    thruster_azimuth_response_deg: float | None = None
    transverse_water_kn: float | None = None
    transverse_ground_kn: float | None = None
    forward_transverse_water_kn: float | None = None
    forward_transverse_ground_kn: float | None = None
    aft_transverse_water_kn: float | None = None
    aft_transverse_ground_kn: float | None = None
    barometric_pressure_bar: float | None = None
    air_temperature_c: float | None = None
    water_temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    absolute_humidity_percent: float | None = None
    dew_point_c: float | None = None
    xdr_air_temperature_c: float | None = None
    xdr_relative_humidity_percent: float | None = None
    xdr_dew_point_c: float | None = None
    xdr_surface_pressure_bar: float | None = None


@dataclass(frozen=True, slots=True)
class SetTimeScale:
    scale: float


@dataclass(frozen=True, slots=True)
class SetPosition:
    latitude_deg: float
    longitude_deg: float
    heading_deg: float | None = None


@dataclass(frozen=True, slots=True)
class SetEnvironment:
    wind_from_deg: float | None = None
    wind_speed_kn: float | None = None
    current_from_deg: float | None = None
    current_speed_kn: float | None = None
    depth_m: float | None = None


@dataclass(frozen=True, slots=True)
class SelectVesselProfile:
    profile_id: str


@dataclass(frozen=True, slots=True)
class SetRoute:
    waypoints: tuple[RouteWaypoint, ...]


@dataclass(frozen=True, slots=True)
class UpdateRoute:
    """Replace route geometry while preserving and re-projecting own ship."""

    waypoints: tuple[RouteWaypoint, ...]


@dataclass(frozen=True, slots=True)
class SetRouteOptions:
    use_planned_speed: bool | None = None
    use_planned_turns: bool | None = None
    control_turn_mode: str | None = None
    control_turn_rate_deg_min: float | None = None
    control_turn_radius_nm: float | None = None
    end_action: str | None = None


@dataclass(frozen=True, slots=True)
class SeekRoute:
    progress_nm: float


@dataclass(frozen=True, slots=True)
class ExternalRudderOrder:
    starboard_or_single_deg: float
    port_deg: float | None
    received_monotonic_s: float
    source: str = "IEC61162:ROR"
    protocol: str = "ROR"


@dataclass(frozen=True, slots=True)
class ExternalRudderResponse:
    starboard_or_single_deg: float
    port_deg: float | None
    received_monotonic_s: float
    source: str = "IEC61162:RSA"
    protocol: str = "RSA"


@dataclass(frozen=True, slots=True)
class SetExternalRudderInputType:
    input_type: str
    on_loss: str | None = None
    reset: bool = False


Command = (
    SetControlMode
    | SetSpeedOrder
    | SetBowThrusterOrder
    | SetExternalActuatorPolicy
    | ExternalActuatorOrder
    | SetRudderOrder
    | SetSensorValues
    | SetSensorCoupling
    | SetSensorNoise
    | SetAuxiliarySensors
    | SetTimeScale
    | SetPosition
    | SetEnvironment
    | SelectVesselProfile
    | SetRoute
    | UpdateRoute
    | SetRouteOptions
    | SeekRoute
    | ExternalRudderOrder
    | ExternalRudderResponse
    | SetExternalRudderInputType
)
