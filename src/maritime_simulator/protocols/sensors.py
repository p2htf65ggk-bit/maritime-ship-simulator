# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING

from maritime_simulator.domain.state import SimulationSnapshot, sensor_noise_settings

if TYPE_CHECKING:
    from maritime_simulator.application.fault_injection import FaultInjectionController


@dataclass(frozen=True, slots=True)
class SensorSnapshot:
    revision: int
    gnss_valid: bool
    latitude_deg: float
    longitude_deg: float
    heading_deg: float
    cog_deg: float
    sog_kn: float
    stw_kn: float
    rot_deg_min: float
    xte_nm: float
    true_wind_from_deg: float
    true_wind_speed_kn: float
    relative_wind_from_bow_deg: float
    relative_wind_speed_kn: float
    current_set_deg: float
    current_drift_kn: float
    rudder_order_starboard_deg: float
    rudder_response_starboard_deg: float
    rudder_order_port_deg: float
    rudder_response_port_deg: float
    dual_rudder: bool
    longitudinal_water_kn: float
    transverse_water_kn: float
    transverse_ground_kn: float
    forward_transverse_water_kn: float
    forward_transverse_ground_kn: float
    aft_transverse_water_kn: float
    aft_transverse_ground_kn: float
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

    @property
    def theoretical_wind_from_bow_deg(self) -> float:
        """True theoretical wind direction relative to the noisy sensor heading."""
        return (self.true_wind_from_deg - self.heading_deg) % 360.0

    @property
    def theoretical_wind_speed_kn(self) -> float:
        """True theoretical wind uses the projected true-wind speed."""
        return self.true_wind_speed_kn


class SensorProjector:
    """Derive protocol-ready sensor values once per simulation revision."""

    def __init__(self, fault_injector: FaultInjectionController | None = None):
        self._cached_revision = -1
        self._cached_fault_revision = -1
        self._cached: SensorSnapshot | None = None
        self.fault_injector = fault_injector

    def project(self, snapshot: SimulationSnapshot) -> SensorSnapshot:
        fault_revision = self.fault_injector.revision if self.fault_injector is not None else 0
        if (
            self._cached is not None
            and snapshot.revision == self._cached_revision
            and fault_revision == self._cached_fault_revision
        ):
            return self._cached
        noise = dict(snapshot.sensor_noise)
        time_s = snapshot.simulation_time_s

        def value(name: str, phase: float = 0.0, fallback: str | None = None) -> float:
            settings = noise.get(name, noise.get(fallback))
            enabled, amplitude, period_s = sensor_noise_settings(settings)
            angular_frequency = math.tau / period_s
            return float(amplitude) * math.sin(time_s * angular_frequency + phase) if enabled else 0.0

        def linked_value(name: str, phase: float, legacy: tuple[str, ...]) -> float:
            if name in noise:
                settings = noise[name]
            else:
                settings = next(
                    (
                        noise[key]
                        for key in legacy
                        if key in noise and sensor_noise_settings(noise[key])[0]
                    ),
                    None,
                )
            enabled, amplitude, period_s = sensor_noise_settings(settings)
            angular_frequency = math.tau / period_s
            return float(amplitude) * math.sin(time_s * angular_frequency + phase) if enabled else 0.0

        position_m = value("position", 0.2)
        latitude_noise = position_m / 111_120.0
        longitude_scale = max(1e-6, math.cos(math.radians(snapshot.latitude_deg)))
        longitude_noise = value("position", 1.7) / (111_120.0 * longitude_scale)
        heading_noise = value("heading", 0.0)
        wind_direction_noise = linked_value(
            "wind_direction", 2.2, ("true_wind_direction", "relative_wind_direction")
        )
        wind_speed_noise = linked_value(
            "wind_speed", 2.4, ("true_wind_speed", "relative_wind_speed")
        )
        humidity_noise = linked_value(
            "humidity", 6.4, ("relative_humidity", "absolute_humidity")
        )
        projected = SensorSnapshot(
            revision=snapshot.revision,
            gnss_valid=True,
            latitude_deg=snapshot.latitude_deg + latitude_noise,
            longitude_deg=snapshot.longitude_deg + longitude_noise,
            heading_deg=(snapshot.heading_deg + heading_noise) % 360.0,
            cog_deg=(snapshot.cog_deg + value("cog", 0.8)) % 360.0,
            sog_kn=snapshot.sog_kn + value("sog", 1.1, "speed"),
            stw_kn=snapshot.stw_kn + value("stw", 1.5),
            rot_deg_min=snapshot.rate_of_turn_deg_min + value("rot", 1.8),
            xte_nm=snapshot.xte_nm + value("xte", 2.0),
            true_wind_from_deg=(
                snapshot.true_wind_from_deg
                + wind_direction_noise
            ) % 360.0,
            true_wind_speed_kn=max(
                0.0,
                snapshot.true_wind_speed_kn + wind_speed_noise,
            ),
            relative_wind_from_bow_deg=(
                snapshot.relative_wind_from_bow_deg
                + wind_direction_noise
            ) % 360.0,
            relative_wind_speed_kn=max(
                0.0,
                snapshot.relative_wind_speed_kn
                + wind_speed_noise,
            ),
            current_set_deg=(
                snapshot.current_from_deg + 180.0 + value("current_direction", 3.0)
            ) % 360.0,
            current_drift_kn=max(
                0.0,
                snapshot.current_speed_kn + value("current_speed", 3.2),
            ),
            rudder_order_starboard_deg=(
                snapshot.rudder_order_starboard_deg + value("rudder_order_starboard", 3.4)
            ),
            rudder_response_starboard_deg=(
                snapshot.rudder_response_starboard_deg + value("rudder_response_starboard", 3.6)
            ),
            rudder_order_port_deg=(
                snapshot.rudder_order_port_deg + value("rudder_order_port", 3.8)
            ),
            rudder_response_port_deg=(
                snapshot.rudder_response_port_deg + value("rudder_response_port", 4.0)
            ),
            dual_rudder=snapshot.dual_rudder,
            longitudinal_water_kn=snapshot.longitudinal_water_kn + value("longitudinal_water", 4.2),
            transverse_water_kn=snapshot.transverse_water_kn + value("transverse_water", 4.4),
            transverse_ground_kn=snapshot.transverse_ground_kn + value("transverse_ground", 4.6),
            forward_transverse_water_kn=(
                snapshot.forward_transverse_water_kn + value("forward_transverse_water", 4.8)
            ),
            forward_transverse_ground_kn=(
                snapshot.forward_transverse_ground_kn + value("forward_transverse_ground", 5.0)
            ),
            aft_transverse_water_kn=(
                snapshot.aft_transverse_water_kn + value("aft_transverse_water", 5.2)
            ),
            aft_transverse_ground_kn=(
                snapshot.aft_transverse_ground_kn + value("aft_transverse_ground", 5.4)
            ),
            depth_m=max(0.0, snapshot.depth_m + value("depth", 5.6)),
            barometric_pressure_bar=max(
                0.0,
                snapshot.barometric_pressure_bar + value("pressure", 5.8, "meteorological"),
            ),
            air_temperature_c=(
                snapshot.air_temperature_c + value("air_temperature", 6.0, "meteorological")
            ),
            water_temperature_c=(
                snapshot.water_temperature_c + value("water_temperature", 6.2, "meteorological")
            ),
            relative_humidity_percent=max(
                0.0,
                min(
                    100.0,
                    snapshot.relative_humidity_percent
                    + humidity_noise,
                ),
            ),
            absolute_humidity_percent=max(
                0.0,
                min(
                    100.0,
                    snapshot.absolute_humidity_percent
                    + humidity_noise,
                ),
            ),
            dew_point_c=snapshot.dew_point_c + value("dew_point", 6.8, "meteorological"),
            pitch_deg=snapshot.pitch_deg + value("pitch", 7.0),
            roll_deg=snapshot.roll_deg + value("roll", 7.2),
            water_distance_nm=max(
                0.0,
                snapshot.water_distance_nm + value("water_distance", 7.4),
            ),
            ground_distance_nm=max(
                0.0,
                snapshot.ground_distance_nm + value("ground_distance", 7.6),
            ),
            propeller_lever_demand_percent=(
                snapshot.propeller_lever_demand_percent + value("propeller_lever", 7.8)
            ),
            thruster_number=snapshot.thruster_number,
            thruster_rpm_demand_percent=(
                snapshot.thruster_rpm_demand_percent + value("thruster_rpm_demand", 8.0)
            ),
            thruster_pitch_demand_percent=(
                snapshot.thruster_pitch_demand_percent + value("thruster_pitch_demand", 8.2)
            ),
            thruster_azimuth_demand_deg=(
                snapshot.thruster_azimuth_demand_deg + value("thruster_azimuth_demand", 8.4)
            ) % 360.0,
            thruster_rpm_response_percent=(
                snapshot.thruster_rpm_response_percent + value("thruster_rpm_response", 8.6)
            ),
            thruster_pitch_response_percent=(
                snapshot.thruster_pitch_response_percent + value("thruster_pitch_response", 8.8)
            ),
            thruster_azimuth_response_deg=(
                snapshot.thruster_azimuth_response_deg + value("thruster_azimuth_response", 9.0)
            ) % 360.0,
            xdr_air_temperature_c=(
                snapshot.xdr_air_temperature_c
                + value("air_temperature", 6.0, "meteorological")
            ),
            xdr_relative_humidity_percent=max(
                0.0,
                min(100.0, snapshot.xdr_relative_humidity_percent + humidity_noise),
            ),
            xdr_dew_point_c=(
                snapshot.xdr_dew_point_c + value("dew_point", 6.8, "meteorological")
            ),
            xdr_surface_pressure_bar=max(
                0.0,
                snapshot.xdr_surface_pressure_bar
                + value("pressure", 5.8, "meteorological"),
            ),
        )
        if self.fault_injector is not None:
            projected = self.fault_injector.apply_sensor(projected, snapshot)
        self._cached_revision = snapshot.revision
        self._cached_fault_revision = fault_revision
        self._cached = projected
        return projected
