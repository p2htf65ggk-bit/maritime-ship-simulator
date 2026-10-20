# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import random
from threading import RLock
import time
from typing import Callable, Iterable

from maritime_simulator.domain.state import SimulationSnapshot


class WaterMaskPendingError(ValueError):
    """A position/path cannot yet be validated; callers may retry asynchronously."""


@dataclass(frozen=True, slots=True)
class AISTarget:
    mmsi: int
    latitude_deg: float
    longitude_deg: float
    sog_kn: float
    cog_deg: float
    heading_deg: float
    name: str = ""
    callsign: str = ""
    nav_status: str = "Under way using engine"
    ship_type: str = ""
    length_m: float | None = None
    beam_m: float | None = None
    draught_m: float | None = None
    rot_deg_min: float | None = None
    imo_number: int | None = None
    destination: str = ""
    eta_month: int | None = None
    eta_day: int | None = None
    eta_hour: int | None = None
    eta_minute: int | None = None
    last_update_simulation_s: float = 0.0

    def map_payload(
        self,
        *,
        distance_nm: float,
        active: bool,
        provider_report_age_s: float | None = None,
        provider_report_interval_s: float | None = None,
        dead_reckoning_age_s: float = 0.0,
        dead_reckoning_active: bool = False,
    ) -> dict:
        return {
            "mmsi": self.mmsi,
            "name": self.name,
            "callsign": self.callsign,
            "nav_status": self.nav_status,
            "ship_type": self.ship_type,
            "lat": self.latitude_deg,
            "lon": self.longitude_deg,
            "speed": self.sog_kn,
            "course": self.cog_deg,
            "heading": self.heading_deg,
            "length": self.length_m,
            "beam": self.beam_m,
            "draught": self.draught_m,
            "rot": self.rot_deg_min,
            "imo": self.imo_number,
            "destination": self.destination,
            "eta_month": self.eta_month,
            "eta_day": self.eta_day,
            "eta_hour": self.eta_hour,
            "eta_minute": self.eta_minute,
            "distance_nm": distance_nm,
            "active": active,
            "provider_report_age_s": provider_report_age_s,
            "provider_report_interval_s": provider_report_interval_s,
            "dead_reckoning_age_s": dead_reckoning_age_s,
            "dead_reckoning_active": dead_reckoning_active,
        }


class AISRepository:
    """One AIS target source shared by map rendering and VDM output."""

    def __init__(
        self,
        *,
        generated_target_count: int = 40,
        active_range_nm: float = 40.0,
        map_range_nm: float = 2500.0,
        map_target_limit: int = 1000,
        output_filter_mode: str = "all",
        output_min_sog_kn: float = 0.5,
        random_seed: int = 61162,
        monotonic: Callable[[], float] = time.monotonic,
        live_prediction_limit_s: float = 300.0,
        water_checker: Callable[[float, float], bool | None] | None = None,
        water_sampler: Callable[[float, float, float, random.Random], tuple[float, float] | None] | None = None,
        water_preparer: Callable[[float, float, float], bool] | None = None,
        avoid_land: bool = True,
    ):
        self.generated_target_count = max(0, int(generated_target_count))
        self.active_range_nm = max(0.0, float(active_range_nm))
        self.map_range_nm = max(self.active_range_nm, float(map_range_nm))
        self.map_target_limit = max(0, int(map_target_limit))
        self.output_filter_mode = _normalize_output_filter_mode(output_filter_mode)
        self.output_min_sog_kn = max(0.0, float(output_min_sog_kn))
        self._random = random.Random(random_seed)
        self._targets: dict[int, AISTarget] = {}
        self._synthetic_targets: dict[int, AISTarget] = {}
        self._avoidance_courses: dict[int, float] = {}
        self._avoidance_cruise_speeds: dict[int, float] = {}
        self._next_avoidance_check_s: dict[int, float] = {}
        self._avoidance_holds_until_s: dict[int, float] = {}
        self._avoidance_biases: dict[int, float] = {}
        self._avoidance_horizon_scales: dict[int, float] = {}
        self._avoidance_turn_factors: dict[int, float] = {}
        self._live_observed_at: dict[int, float] = {}
        self._live_report_interval_s: dict[int, float] = {}
        self._mode = "generated"
        self._next_mmsi = 244_100_000
        self._last_simulation_s: float | None = None
        self._last_generated_fill_attempt_s = -1.0
        self._revision = 0
        self._lock = RLock()
        self._monotonic = monotonic
        self.live_prediction_limit_s = max(0.0, float(live_prediction_limit_s))
        self._water_checker = water_checker
        self._water_sampler = water_sampler
        self._water_preparer = water_preparer
        self.avoid_land = bool(avoid_land)

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def _clear_avoidance_state(self) -> None:
        self._avoidance_courses.clear()
        self._avoidance_cruise_speeds.clear()
        self._next_avoidance_check_s.clear()
        self._avoidance_holds_until_s.clear()
        self._avoidance_biases.clear()
        self._avoidance_horizon_scales.clear()
        self._avoidance_turn_factors.clear()

    def _forget_avoidance_state(self, mmsi: int) -> None:
        for values in (
            self._avoidance_courses,
            self._avoidance_cruise_speeds,
            self._next_avoidance_check_s,
            self._avoidance_holds_until_s,
            self._avoidance_biases,
            self._avoidance_horizon_scales,
            self._avoidance_turn_factors,
        ):
            values.pop(int(mmsi), None)

    def use_generated(self, own_ship: SimulationSnapshot) -> None:
        with self._lock:
            self._mode = "generated"
            self._targets.clear()
            self._clear_avoidance_state()
            self._live_observed_at.clear()
            self._live_report_interval_s.clear()
            self._last_simulation_s = own_ship.simulation_time_s
            self._fill_generated(own_ship, force=True)
            self._revision += 1

    def use_synthetic(self, own_ship: SimulationSnapshot) -> None:
        with self._lock:
            self._mode = "synthetic"
            self._targets = dict(self._synthetic_targets)
            self._clear_avoidance_state()
            self._live_observed_at.clear()
            self._live_report_interval_s.clear()
            self._last_simulation_s = own_ship.simulation_time_s
            self._revision += 1

    def add_synthetic_target(
        self,
        own_ship: SimulationSnapshot,
        *,
        ship_type: str = "Cargo",
        bearing_deg: float = 0.0,
        range_nm: float = 2.0,
        course_deg: float | None = None,
        heading_deg: float | None = None,
        speed_kn: float = 10.0,
        latitude_deg: float | None = None,
        longitude_deg: float | None = None,
        name: str = "",
        mmsi: int | None = None,
        callsign: str = "",
        length_m: float | None = None,
        beam_m: float | None = None,
        draught_m: float | None = None,
        validate_water: bool = True,
    ) -> AISTarget:
        with self._lock:
            if latitude_deg is None or longitude_deg is None:
                latitude_deg, longitude_deg = _destination(
                    own_ship.latitude_deg,
                    own_ship.longitude_deg,
                    own_ship.heading_deg + float(bearing_deg),
                    max(0.0, float(range_nm)),
                )
            latitude_deg = float(latitude_deg)
            longitude_deg = float(longitude_deg)
            profile = _synthetic_type_profile(ship_type)
            selected_mmsi = self._allocate_mmsi() if mmsi is None else int(mmsi)
            if not 100_000_000 <= selected_mmsi <= 999_999_999:
                raise ValueError("Synthetic MMSI must contain nine digits")
            if selected_mmsi in self._synthetic_targets:
                raise ValueError(f"Synthetic MMSI already exists: {selected_mmsi}")
            selected_course = own_ship.heading_deg if course_deg is None else float(course_deg)
            selected_heading = selected_course if heading_deg is None else float(heading_deg)
            target = AISTarget(
                mmsi=selected_mmsi,
                name=str(name).strip() or f"SYNTHETIC {selected_mmsi % 1000:03d}",
                callsign=str(callsign).strip() or f"S{selected_mmsi % 10000:04d}",
                latitude_deg=latitude_deg,
                longitude_deg=longitude_deg,
                sog_kn=max(0.0, float(speed_kn)),
                cog_deg=float(selected_course) % 360.0,
                heading_deg=float(selected_heading) % 360.0,
                ship_type=profile[0],
                length_m=profile[1] if length_m is None else max(1.0, float(length_m)),
                beam_m=profile[2] if beam_m is None else max(1.0, float(beam_m)),
                draught_m=profile[3] if draught_m is None else max(0.1, float(draught_m)),
                rot_deg_min=0.0,
                destination="SYNTHETIC SCENARIO",
                last_update_simulation_s=own_ship.simulation_time_s,
            )
            if validate_water:
                water = self._target_is_on_water(target)
                if water is None:
                    raise WaterMaskPendingError("Water data at the selected position is still loading")
                if water is False:
                    raise ValueError("Synthetic target cannot be created because the selected position is on land")
            self._synthetic_targets[target.mmsi] = target
            if self._mode == "synthetic":
                self._targets[target.mmsi] = target
            self._revision += 1
            return target

    def add_encounter(
        self,
        own_ship: SimulationSnapshot,
        *,
        encounter: str,
        cpa_nm: float = 0.2,
        tcpa_min: float = 10.0,
        target_speed_kn: float = 12.0,
        ship_type: str = "Cargo",
    ) -> AISTarget:
        normalized_encounter = _normalize_encounter_name(encounter)
        selected_target_speed = max(0.0, float(target_speed_kn))
        if normalized_encounter == "own_ship_overtakes":
            if own_ship.sog_kn <= 0.1:
                raise ValueError("Own Ship Overtakes requires own ship to be making way")
            if selected_target_speed >= own_ship.sog_kn:
                raise ValueError(
                    "Own Ship Overtakes requires target speed to be lower than own-ship speed"
                )
        elif (
            normalized_encounter == "overtaking_own_ship"
            and selected_target_speed <= own_ship.sog_kn
        ):
            raise ValueError(
                "Overtaking Own Ship requires target speed to be higher than own-ship speed"
            )
        course, side = _encounter_course_and_side(normalized_encounter, own_ship.cog_deg)
        own_velocity = _velocity_vector(own_ship.sog_kn, own_ship.cog_deg)
        target_velocity = _velocity_vector(selected_target_speed, course)
        relative_velocity = (
            target_velocity[0] - own_velocity[0],
            target_velocity[1] - own_velocity[1],
        )
        relative_speed = math.hypot(*relative_velocity)
        if relative_speed < 0.05:
            raise ValueError("Encounter requires a relative speed greater than zero")
        normal = (-relative_velocity[1] / relative_speed, relative_velocity[0] / relative_speed)
        cpa_vector = (normal[0] * float(cpa_nm) * side, normal[1] * float(cpa_nm) * side)
        hours = max(0.1, float(tcpa_min)) / 60.0
        relative_position = (
            cpa_vector[0] - relative_velocity[0] * hours,
            cpa_vector[1] - relative_velocity[1] * hours,
        )
        range_nm = math.hypot(*relative_position)
        absolute_bearing = math.degrees(math.atan2(relative_position[0], relative_position[1])) % 360.0
        target = self.add_synthetic_target(
            own_ship,
            ship_type=ship_type,
            bearing_deg=(absolute_bearing - own_ship.heading_deg) % 360.0,
            range_nm=range_nm,
            course_deg=course,
            heading_deg=course,
            speed_kn=selected_target_speed,
            name=_encounter_name(encounter, len(self._synthetic_targets) + 1),
        )
        safe = self._course_is_water_safe(target, horizon_minutes=max(10.0, tcpa_min + 5.0))
        if safe is not True:
            self.remove_synthetic_target(target.mmsi)
            if safe is None:
                raise WaterMaskPendingError("Water data along the encounter is still loading")
            raise ValueError("The selected encounter geometry crosses land")
        return target

    def update_synthetic_target(self, mmsi: int, **changes: float | str | int) -> AISTarget:
        with self._lock:
            target = self._synthetic_targets.get(int(mmsi))
            if target is None:
                raise KeyError(f"Unknown synthetic target: {mmsi}")
            values = {}
            if "ship_type" in changes:
                profile = _synthetic_type_profile(str(changes["ship_type"]))
                values.update(ship_type=profile[0], length_m=profile[1], beam_m=profile[2], draught_m=profile[3])
            mapping = {
                "latitude_deg": "latitude_deg",
                "longitude_deg": "longitude_deg",
                "speed_kn": "sog_kn",
                "course_deg": "cog_deg",
                "heading_deg": "heading_deg",
                "name": "name",
                "callsign": "callsign",
                "length_m": "length_m",
                "beam_m": "beam_m",
                "draught_m": "draught_m",
            }
            for source, destination in mapping.items():
                if source not in changes:
                    continue
                value = changes[source]
                if destination in {"name", "callsign"}:
                    values[destination] = str(value).strip()
                elif destination in {"cog_deg", "heading_deg"}:
                    values[destination] = float(value) % 360.0
                elif destination == "sog_kn":
                    values[destination] = max(0.0, float(value))
                elif destination in {"length_m", "beam_m"}:
                    values[destination] = max(1.0, float(value))
                elif destination == "draught_m":
                    values[destination] = max(0.1, float(value))
                else:
                    values[destination] = float(value)
            updated_mmsi = int(changes.get("new_mmsi", target.mmsi))
            if not 100_000_000 <= updated_mmsi <= 999_999_999:
                raise ValueError("Synthetic MMSI must contain nine digits")
            if updated_mmsi != target.mmsi and updated_mmsi in self._synthetic_targets:
                raise ValueError(f"Synthetic MMSI already exists: {updated_mmsi}")
            updated = replace(target, mmsi=updated_mmsi, **values)
            water = self._target_is_on_water(updated)
            if water is not True:
                reason = "water data is still loading" if water is None else "the selected position is on land"
                raise ValueError(f"Synthetic target cannot be moved because {reason}")
            if updated.mmsi != target.mmsi:
                self._synthetic_targets.pop(target.mmsi, None)
                self._targets.pop(target.mmsi, None)
                self._forget_avoidance_state(target.mmsi)
            self._synthetic_targets[updated.mmsi] = updated
            if self._mode == "synthetic":
                self._targets[updated.mmsi] = updated
            self._forget_avoidance_state(updated.mmsi)
            self._revision += 1
            return updated

    def duplicate_synthetic_target(self, mmsi: int, own_ship: SimulationSnapshot) -> AISTarget:
        with self._lock:
            target = self._synthetic_targets.get(int(mmsi))
            if target is None:
                raise KeyError(f"Unknown synthetic target: {mmsi}")
        latitude, longitude = _destination(target.latitude_deg, target.longitude_deg, 90.0, 0.15)
        return self.add_synthetic_target(
            own_ship,
            ship_type=target.ship_type,
            latitude_deg=latitude,
            longitude_deg=longitude,
            course_deg=target.cog_deg,
            heading_deg=target.heading_deg,
            speed_kn=target.sog_kn,
            name=f"{target.name} COPY",
        )

    def remove_synthetic_target(self, mmsi: int) -> None:
        with self._lock:
            self._synthetic_targets.pop(int(mmsi), None)
            if self._mode == "synthetic":
                self._targets.pop(int(mmsi), None)
            self._forget_avoidance_state(int(mmsi))
            self._revision += 1

    def clear_synthetic_targets(self) -> None:
        with self._lock:
            self._synthetic_targets.clear()
            self._clear_avoidance_state()
            if self._mode == "synthetic":
                self._targets.clear()
            self._revision += 1

    def synthetic_targets(self) -> tuple[AISTarget, ...]:
        with self._lock:
            return tuple(self._synthetic_targets.values())

    def synthetic_payload(self) -> list[dict]:
        return [
            {
                "mmsi": target.mmsi,
                "name": target.name,
                "callsign": target.callsign,
                "ship_type": target.ship_type,
                "latitude_deg": target.latitude_deg,
                "longitude_deg": target.longitude_deg,
                "speed_kn": target.sog_kn,
                "course_deg": target.cog_deg,
                "heading_deg": target.heading_deg,
                "length_m": target.length_m,
                "beam_m": target.beam_m,
                "draught_m": target.draught_m,
            }
            for target in self.synthetic_targets()
        ]

    def apply_synthetic_payload(self, values: Iterable[dict], own_ship: SimulationSnapshot) -> None:
        self.clear_synthetic_targets()
        for value in values or ():
            self.add_synthetic_target(own_ship, validate_water=False, **dict(value))

    def configure(
        self,
        *,
        target_count: int | None = None,
        active_range_nm: float | None = None,
        map_range_nm: float | None = None,
        map_target_limit: int | None = None,
        output_filter_mode: str | None = None,
        output_min_sog_kn: float | None = None,
        avoid_land: bool | None = None,
    ) -> None:
        with self._lock:
            if avoid_land is not None and self.avoid_land != bool(avoid_land):
                self.avoid_land = bool(avoid_land)
                if not self.avoid_land:
                    for targets in (self._targets, self._synthetic_targets):
                        for mmsi, target in tuple(targets.items()):
                            cruise_speed = self._avoidance_cruise_speeds.get(mmsi)
                            if cruise_speed is not None:
                                targets[mmsi] = replace(target, sog_kn=cruise_speed, rot_deg_min=0.0)
                self._clear_avoidance_state()
                self._last_generated_fill_attempt_s = -1.0
            if target_count is not None:
                self.generated_target_count = max(0, int(target_count))
            if active_range_nm is not None:
                self.active_range_nm = max(0.0, float(active_range_nm))
            if map_range_nm is not None:
                self.map_range_nm = max(self.active_range_nm, float(map_range_nm))
            if map_target_limit is not None:
                self.map_target_limit = max(0, int(map_target_limit))
            if output_filter_mode is not None:
                self.output_filter_mode = _normalize_output_filter_mode(output_filter_mode)
            if output_min_sog_kn is not None:
                self.output_min_sog_kn = max(0.0, float(output_min_sog_kn))
            if self._mode == "generated" and len(self._targets) > self.generated_target_count:
                keep = tuple(self._targets)[: self.generated_target_count]
                self._targets = {mmsi: self._targets[mmsi] for mmsi in keep}
                self._retain_avoidance_state(set(self._targets))
            self._revision += 1

    def ingest_live(
        self,
        targets: Iterable[AISTarget],
        *,
        replace_all: bool = False,
        motion_update: bool = True,
    ) -> None:
        with self._lock:
            if self._mode != "live":
                self._clear_avoidance_state()
            self._mode = "live"
            incoming = tuple(targets)
            previous_targets = dict(self._targets)
            if replace_all:
                self._targets.clear()
            observed_at = self._monotonic()
            for target in incoming:
                if not 0 <= target.mmsi <= 999_999_999:
                    raise ValueError("AIS MMSI must be in [0, 999999999]")
                if not -90.0 <= target.latitude_deg <= 90.0 or not -180.0 <= target.longitude_deg <= 180.0:
                    raise ValueError("AIS target position is outside valid bounds")
                previous = previous_targets.get(target.mmsi)
                self._targets[target.mmsi] = _merge_live_target(previous, target)
                if motion_update or previous is None:
                    previous_observed_at = self._live_observed_at.get(target.mmsi)
                    if previous_observed_at is not None:
                        interval_s = max(0.0, observed_at - previous_observed_at)
                        if interval_s > 0.0:
                            self._live_report_interval_s[target.mmsi] = interval_s
                    self._live_observed_at[target.mmsi] = observed_at
            if replace_all:
                retained = set(self._targets)
                self._live_observed_at = {
                    mmsi: received_at
                    for mmsi, received_at in self._live_observed_at.items()
                    if mmsi in retained
                }
                self._live_report_interval_s = {
                    mmsi: interval_s
                    for mmsi, interval_s in self._live_report_interval_s.items()
                    if mmsi in retained
                }
            self._revision += 1

    def update_generated(self, own_ship: SimulationSnapshot) -> None:
        with self._lock:
            if self._mode not in {"generated", "synthetic"}:
                return
            if self._last_simulation_s is None:
                self._last_simulation_s = own_ship.simulation_time_s
            dt_s = max(0.0, own_ship.simulation_time_s - self._last_simulation_s)
            self._last_simulation_s = own_ship.simulation_time_s
            changed = False
            if dt_s > 0.0:
                moved = {}
                for target in self._targets.values():
                    target = self._apply_land_avoidance(
                        target,
                        dt_s,
                        own_ship.simulation_time_s,
                    )
                    target, _water = self._move_target_over_water(target, dt_s)
                    inside_generated_range = (
                        self._mode == "synthetic"
                        or _distance_nm(
                            target.latitude_deg,
                            target.longitude_deg,
                            own_ship.latitude_deg,
                            own_ship.longitude_deg,
                        ) <= self.active_range_nm * 1.2
                    )
                    if inside_generated_range:
                        moved[target.mmsi] = replace(
                            target,
                            last_update_simulation_s=own_ship.simulation_time_s,
                        )
                    changed = True
                self._targets = moved
                self._retain_avoidance_state(set(moved))
            before = len(self._targets)
            if self._mode == "generated":
                self._fill_generated(own_ship)
            else:
                self._synthetic_targets = dict(self._targets)
            if changed or len(self._targets) != before:
                self._revision += 1

    def all_targets(self) -> tuple[AISTarget, ...]:
        with self._lock:
            return tuple(self._targets.values())

    def target(self, mmsi: int) -> AISTarget | None:
        with self._lock:
            return self._targets.get(int(mmsi))

    def map_targets(self, own_ship: SimulationSnapshot) -> tuple[dict, ...]:
        with self._lock:
            now = self._monotonic()
            targets = tuple(
                self._project_live_target(target, now)
                if self._mode == "live"
                else target
                for target in self._targets.values()
            )
            ranked = sorted(
                (
                    (_distance_nm(target.latitude_deg, target.longitude_deg, own_ship.latitude_deg, own_ship.longitude_deg), target)
                    for target in targets
                ),
                key=lambda item: item[0],
            )
            visible = [item for item in ranked if item[0] <= self.map_range_nm][: self.map_target_limit]
            active_count = 0
            payloads = []
            for distance, target in visible:
                active = (
                    distance <= self.active_range_nm
                    and active_count < self.generated_target_count
                    and self._output_filter_allows(target)
                )
                if active:
                    active_count += 1
                payload = target.map_payload(
                    distance_nm=distance,
                    active=active,
                    provider_report_age_s=self._provider_report_age_s(target.mmsi, now),
                    provider_report_interval_s=self._live_report_interval_s.get(target.mmsi),
                    dead_reckoning_age_s=self._dead_reckoning_age_s(target.mmsi, now),
                    dead_reckoning_active=self._dead_reckoning_active(target, now),
                )
                if self._mode == "synthetic":
                    payload["synthetic"] = True
                payloads.append(payload)
            return tuple(payloads)

    def output_targets(self, own_ship: SimulationSnapshot) -> tuple[dict, ...]:
        return tuple(target for target in self.map_targets(own_ship) if target["active"])

    def _output_filter_allows(self, target: AISTarget) -> bool:
        return (
            self.output_filter_mode == "all"
            or abs(float(target.sog_kn)) > self.output_min_sog_kn
        )

    def maximum_live_prediction_age_s(self) -> float:
        with self._lock:
            if self._mode != "live" or not self._live_observed_at:
                return 0.0
            now = self._monotonic()
            return min(
                self.live_prediction_limit_s,
                max(max(0.0, now - observed_at) for observed_at in self._live_observed_at.values()),
            )

    def live_report_diagnostics(self) -> dict[str, float | int]:
        with self._lock:
            if self._mode != "live":
                return {
                    "moving_targets": 0,
                    "stale_moving_targets": 0,
                    "oldest_provider_report_age_s": 0.0,
                    "maximum_provider_interval_s": 0.0,
                }
            now = self._monotonic()
            ages = [
                max(0.0, now - observed_at)
                for observed_at in self._live_observed_at.values()
            ]
            moving_ages = [
                max(0.0, now - self._live_observed_at[target.mmsi])
                for target in self._targets.values()
                if abs(target.sog_kn) >= 0.5 and target.mmsi in self._live_observed_at
            ]
            return {
                "moving_targets": len(moving_ages),
                "stale_moving_targets": sum(age_s > 60.0 for age_s in moving_ages),
                "oldest_provider_report_age_s": max(ages, default=0.0),
                "maximum_provider_interval_s": max(self._live_report_interval_s.values(), default=0.0),
            }

    def _project_live_target(self, target: AISTarget, now: float) -> AISTarget:
        observed_at = self._live_observed_at.get(target.mmsi)
        if observed_at is None or abs(target.sog_kn) <= 0.01:
            return target
        age_s = min(self.live_prediction_limit_s, max(0.0, now - observed_at))
        if age_s <= 1e-6:
            return target
        latitude, longitude = _destination(
            target.latitude_deg,
            target.longitude_deg,
            target.cog_deg,
            abs(target.sog_kn) * age_s / 3600.0,
        )
        return replace(target, latitude_deg=latitude, longitude_deg=longitude)

    def _provider_report_age_s(self, mmsi: int, now: float) -> float | None:
        if self._mode != "live":
            return None
        observed_at = self._live_observed_at.get(mmsi)
        return None if observed_at is None else round(max(0.0, now - observed_at), 1)

    def _dead_reckoning_age_s(self, mmsi: int, now: float) -> float:
        age_s = self._provider_report_age_s(mmsi, now)
        return 0.0 if age_s is None else min(self.live_prediction_limit_s, age_s)

    def _dead_reckoning_active(self, target: AISTarget, now: float) -> bool:
        age_s = self._provider_report_age_s(target.mmsi, now)
        return bool(
            age_s is not None and
            0.0 < age_s < self.live_prediction_limit_s and
            abs(target.sog_kn) > 0.01
        )

    def _fill_generated(self, own_ship: SimulationSnapshot, *, force: bool = False) -> None:
        now = self._monotonic()
        if (
            self.avoid_land and self._water_checker is not None
            and not force
            and now - self._last_generated_fill_attempt_s < 0.75
        ):
            return
        self._last_generated_fill_attempt_s = now
        if (self.generated_target_count and self.avoid_land and self._water_preparer is not None
                and not self._water_preparer(own_ship.latitude_deg, own_ship.longitude_deg, self.active_range_nm)):
            return
        if self.avoid_land and self._water_checker is not None:
            self._water_checker(own_ship.latitude_deg, own_ship.longitude_deg)
        attempts = 0
        maximum_attempts = max(40, self.generated_target_count * 4)
        deadline = time.perf_counter() + 0.020
        while len(self._targets) < self.generated_target_count and attempts < maximum_attempts:
            if self.avoid_land and self._water_checker is not None and attempts and time.perf_counter() >= deadline:
                break
            attempts += 1
            sampled = (
                self._water_sampler(
                    own_ship.latitude_deg,
                    own_ship.longitude_deg,
                    self.active_range_nm,
                    self._random,
                )
                if self.avoid_land and self._water_sampler is not None and attempts % 2
                else None
            )
            if sampled is None:
                radius = self._random.uniform(0.2, max(0.2, self.active_range_nm))
                bearing = self._random.uniform(0.0, 360.0)
                latitude, longitude = _destination(
                    own_ship.latitude_deg,
                    own_ship.longitude_deg,
                    bearing,
                    radius,
                )
            else:
                latitude, longitude = sampled
            mmsi = self._allocate_mmsi()
            heading_deg = self._random.uniform(0.0, 360.0)
            cog_deg = (heading_deg + self._random.uniform(-5.0, 5.0)) % 360.0
            target = AISTarget(
                mmsi=mmsi,
                name=f"V2 TARGET {mmsi % 1000:03d}",
                callsign=f"V{mmsi % 10000:04d}",
                latitude_deg=latitude,
                longitude_deg=longitude,
                sog_kn=self._random.uniform(0.0, 18.0),
                cog_deg=cog_deg,
                heading_deg=heading_deg,
                length_m=self._random.uniform(20.0, 180.0),
                beam_m=self._random.uniform(6.0, 32.0),
                draught_m=self._random.uniform(2.0, 12.0),
                ship_type="Cargo",
                destination="SIMULATED PORT",
                rot_deg_min=self._random.uniform(-3.0, 3.0),
                last_update_simulation_s=own_ship.simulation_time_s,
            )
            if self._target_is_on_water(target) is not True:
                continue
            # A few warm mask tiles must not attract the entire requested fleet.
            if self.avoid_land and any(
                _distance_nm(latitude, longitude, other.latitude_deg, other.longitude_deg)
                < max(0.04, (target.length_m + other.length_m) / (2 * 1852))
                for other in self._targets.values()
            ):
                continue
            safe_course = self._find_water_safe_course(
                target,
                horizon_minutes=_avoidance_horizon_minutes(target) * 1.5,
                require_full_clearance=True,
                allow_pending_horizon=True,
            ) if self.avoid_land else target.cog_deg
            if safe_course is None:
                continue
            target = replace(
                target,
                cog_deg=safe_course,
                heading_deg=safe_course,
            )
            if self.avoid_land and not self._hull_is_on_water(target):
                continue
            self._targets[mmsi] = target

    def _allocate_mmsi(self) -> int:
        while True:
            self._next_mmsi += 1
            if self._next_mmsi not in self._targets and self._next_mmsi not in self._synthetic_targets:
                return self._next_mmsi

    def _retain_avoidance_state(self, retained: set[int]) -> None:
        self._avoidance_courses = {
            mmsi: course
            for mmsi, course in self._avoidance_courses.items()
            if mmsi in retained
        }
        self._avoidance_cruise_speeds = {
            mmsi: speed
            for mmsi, speed in self._avoidance_cruise_speeds.items()
            if mmsi in retained
        }
        self._next_avoidance_check_s = {
            mmsi: check_time
            for mmsi, check_time in self._next_avoidance_check_s.items()
            if mmsi in retained
        }
        self._avoidance_holds_until_s = {
            mmsi: hold_time
            for mmsi, hold_time in self._avoidance_holds_until_s.items()
            if mmsi in retained
        }
        self._avoidance_biases = {
            mmsi: bias
            for mmsi, bias in self._avoidance_biases.items()
            if mmsi in retained
        }
        self._avoidance_horizon_scales = {
            mmsi: scale
            for mmsi, scale in self._avoidance_horizon_scales.items()
            if mmsi in retained
        }
        self._avoidance_turn_factors = {
            mmsi: factor
            for mmsi, factor in self._avoidance_turn_factors.items()
            if mmsi in retained
        }

    def _avoidance_profile(self, mmsi: int) -> tuple[float, float, float]:
        if mmsi not in self._avoidance_biases:
            self._avoidance_biases[mmsi] = -1.0 if self._random.random() < 0.5 else 1.0
            self._avoidance_horizon_scales[mmsi] = self._random.uniform(0.90, 1.15)
            self._avoidance_turn_factors[mmsi] = self._random.uniform(0.20, 1.0)
        return (
            self._avoidance_biases[mmsi],
            self._avoidance_horizon_scales[mmsi],
            self._avoidance_turn_factors[mmsi],
        )

    def _target_is_on_water(self, target: AISTarget) -> bool | None:
        if not self.avoid_land or self._water_checker is None:
            return True
        return self._water_checker(target.latitude_deg, target.longitude_deg)

    def _hull_is_on_water(self, target: AISTarget) -> bool:
        if not self.avoid_land or self._water_checker is None:
            return True
        for offset, distance in ((0, target.length_m / 2), (180, target.length_m / 2),
                                 (90, target.beam_m / 2), (270, target.beam_m / 2)):
            latitude, longitude = _destination(target.latitude_deg, target.longitude_deg,
                                               target.heading_deg + offset, distance / 1852)
            if self._water_checker(latitude, longitude) is not True:
                return False
        return True

    def _course_is_water_safe(self, target: AISTarget, *, horizon_minutes: float) -> bool | None:
        clearance = self._course_water_clearance(target, horizon_minutes=horizon_minutes)
        return None if clearance is None else clearance >= 1.0

    def _course_water_clearance(self, target: AISTarget, *, horizon_minutes: float) -> float | None:
        if not self.avoid_land or self._water_checker is None:
            return 1.0
        water = self._water_checker(target.latitude_deg, target.longitude_deg)
        if water is not True:
            return None if water is None else 0.0
        planning_speed = max(
            target.sog_kn,
            self._avoidance_cruise_speeds.get(target.mmsi, 0.0),
        )
        if planning_speed <= 0.01:
            return 1.0
        distance_nm = planning_speed * max(1.0, float(horizon_minutes)) / 60.0
        distances = _water_probe_distances(distance_nm)
        pending = False
        for index, distance in enumerate(distances):
            latitude, longitude = _destination(
                target.latitude_deg,
                target.longitude_deg,
                target.cog_deg,
                distance,
            )
            water = self._water_checker(latitude, longitude)
            if water is False:
                return index / len(distances)
            if water is None:
                pending = True
        return None if pending else 1.0

    def _departure_is_water_safe(self, target: AISTarget) -> bool:
        if not self._hull_is_on_water(target):
            return False
        distance_nm = max((target.length_m or 20) / 1852, target.sog_kn / 60)
        for distance in _water_probe_distances(distance_nm):
            latitude, longitude = _destination(target.latitude_deg, target.longitude_deg,
                                               target.cog_deg, distance)
            if self._water_checker(latitude, longitude) is not True:
                return False
        return True

    def _find_water_safe_course(
        self,
        target: AISTarget,
        *,
        horizon_minutes: float,
        require_full_clearance: bool,
        allow_pending_horizon: bool = False,
    ) -> float | None:
        bias, _horizon_scale, _turn_factor = self._avoidance_profile(target.mmsi)
        magnitudes = (
            0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0,
            60.0, 75.0, 90.0, 110.0, 135.0, 160.0,
        )
        offsets = [0.0]
        for magnitude in magnitudes[1:]:
            offsets.extend((bias * magnitude, -bias * magnitude))
        candidates = []
        pending_courses = []
        for offset in offsets:
            course = (target.cog_deg + offset) % 360.0
            candidate = replace(target, cog_deg=course, heading_deg=course)
            clearance = self._course_water_clearance(
                candidate,
                horizon_minutes=horizon_minutes,
            )
            if clearance is None:
                if allow_pending_horizon and self._departure_is_water_safe(candidate):
                    pending_courses.append(course)
                continue
            if require_full_clearance and clearance >= 1.0:
                extended_clearance = self._course_water_clearance(
                    candidate,
                    horizon_minutes=max(horizon_minutes + 4.0, horizon_minutes * 1.8),
                )
                if extended_clearance is not None and extended_clearance >= 1.0:
                    return course
            candidates.append((course, offset, clearance, candidate))
        full_clearance = [item for item in candidates if item[2] >= 1.0]
        if full_clearance:
            contenders = sorted(full_clearance, key=lambda item: abs(item[1]))[:6]
            ranked = []
            for course, offset, _clearance, candidate in contenders:
                extended_clearance = self._course_water_clearance(
                    candidate,
                    horizon_minutes=max(horizon_minutes + 4.0, horizon_minutes * 1.8),
                )
                preferred_side = 1.0 if offset == 0.0 or math.copysign(1.0, offset) == bias else 0.0
                ranked.append((extended_clearance or 0, -abs(offset), preferred_side, course))
            return max(ranked)[3]
        if pending_courses:
            return pending_courses[0]
        if require_full_clearance or not candidates:
            return None
        best_course, _best_offset, best_clearance, _candidate = max(
            candidates,
            key=lambda item: (
                item[2],
                -abs(item[1]),
                1.0 if item[1] == 0.0 or math.copysign(1.0, item[1]) == bias else 0.0,
            ),
        )
        if best_clearance <= 0.0:
            return None
        return best_course

    def _move_target_over_water(
        self,
        target: AISTarget,
        dt_s: float,
    ) -> tuple[AISTarget, bool | None]:
        if target.sog_kn <= 0.01 or dt_s <= 0.0:
            return target, self._target_is_on_water(target)
        original_speed = target.sog_kn
        for factor in (1.0, 0.75, 0.5, 0.25, 0.1, 0.02):
            speed = original_speed * factor
            latitude, longitude = _destination(
                target.latitude_deg,
                target.longitude_deg,
                target.cog_deg,
                speed * dt_s / 3600.0,
            )
            candidate = replace(
                target,
                latitude_deg=latitude,
                longitude_deg=longitude,
                sog_kn=speed,
            )
            water = self._target_is_on_water(candidate)
            if water is True and self._hull_is_on_water(candidate):
                if factor < 1.0:
                    self._avoidance_cruise_speeds.setdefault(target.mmsi, original_speed)
                return candidate, True
        self._avoidance_cruise_speeds.setdefault(target.mmsi, original_speed)
        return replace(target, sog_kn=0.0), self._target_is_on_water(target)

    def _apply_land_avoidance(
        self,
        target: AISTarget,
        dt_s: float,
        simulation_time_s: float,
    ) -> AISTarget:
        if not self.avoid_land:
            return replace(target, rot_deg_min=0.0)
        bias, horizon_scale, turn_factor = self._avoidance_profile(target.mmsi)
        desired = self._avoidance_courses.get(target.mmsi)
        check_due = simulation_time_s >= self._next_avoidance_check_s.get(target.mmsi, 0.0)
        if desired is None and check_due:
            self._next_avoidance_check_s[target.mmsi] = simulation_time_s + self._random.uniform(3.0, 6.0)
        horizon_minutes = _avoidance_horizon_minutes(target) * horizon_scale
        if desired is None and check_due and self._course_is_water_safe(
            target,
            horizon_minutes=horizon_minutes,
        ) is False:
            self._avoidance_cruise_speeds.setdefault(target.mmsi, target.sog_kn)
            desired = self._find_water_safe_course(
                target,
                horizon_minutes=horizon_minutes,
                require_full_clearance=False,
            )
            if desired is None:
                desired = (target.cog_deg + bias * self._random.uniform(25.0, 70.0)) % 360.0
            self._avoidance_courses[target.mmsi] = desired
            turn_duration_s = abs(_signed_angle(desired - target.cog_deg)) / max(
                1.0,
                _ais_maximum_turn_rate_deg_min(target) * turn_factor,
            ) * 60.0
            self._avoidance_holds_until_s[target.mmsi] = (
                simulation_time_s + turn_duration_s + self._random.uniform(6.0, 18.0)
            )
        if desired is None:
            cruise_speed = self._avoidance_cruise_speeds.get(target.mmsi)
            if cruise_speed is None:
                return replace(target, rot_deg_min=0.0)
            speed_change = _ais_speed_change_rate_kn_min(target) * max(0.0, dt_s) / 60.0
            speed = _approach(target.sog_kn, cruise_speed, speed_change)
            if abs(speed - cruise_speed) <= 0.01:
                speed = cruise_speed
                self._avoidance_cruise_speeds.pop(target.mmsi, None)
            return replace(target, sog_kn=speed, rot_deg_min=0.0)

        cruise_speed = self._avoidance_cruise_speeds.setdefault(target.mmsi, target.sog_kn)
        difference = _signed_angle(desired - target.cog_deg)
        maximum_rot = max(1.0, _ais_maximum_turn_rate_deg_min(target) * turn_factor)
        maximum_change = maximum_rot * max(0.0, dt_s) / 60.0
        change = max(-maximum_change, min(maximum_change, difference))
        course = (target.cog_deg + change) % 360.0

        absolute_difference = abs(difference)
        turn_speed_factor = max(0.35, 1.0 - absolute_difference / 180.0 * 0.65)
        maneuver_speed = cruise_speed * turn_speed_factor
        forward_clearance = self._course_water_clearance(
            target,
            horizon_minutes=horizon_minutes,
        )
        if forward_clearance is not None:
            clearance_speed_factor = max(0.0, min(1.0, (forward_clearance - 0.03) / 0.72))
            maneuver_speed = min(maneuver_speed, cruise_speed * clearance_speed_factor)
        speed_change = _ais_speed_change_rate_kn_min(target) * max(0.0, dt_s) / 60.0
        speed = _approach(target.sog_kn, maneuver_speed, speed_change)

        reached = abs(difference) <= max(0.2, maximum_change)
        if reached:
            course = desired
            hold_until = self._avoidance_holds_until_s.get(target.mmsi, simulation_time_s)
            release_clearance = self._course_water_clearance(
                replace(target, cog_deg=course, heading_deg=course),
                horizon_minutes=horizon_minutes,
            )
            if release_clearance is not None and release_clearance >= 0.90:
                self._avoidance_courses.pop(target.mmsi, None)
                self._avoidance_holds_until_s.pop(target.mmsi, None)
                self._next_avoidance_check_s[target.mmsi] = (
                    simulation_time_s + self._random.uniform(8.0, 20.0)
                )
            elif release_clearance is not None and simulation_time_s >= hold_until and check_due:
                current = replace(target, cog_deg=course, heading_deg=course)
                revised = self._find_water_safe_course(
                    current,
                    horizon_minutes=horizon_minutes,
                    require_full_clearance=False,
                )
                if revised is not None and abs(_signed_angle(revised - course)) >= 8.0:
                    self._avoidance_courses[target.mmsi] = revised
                    revised_duration_s = abs(_signed_angle(revised - course)) / maximum_rot * 60.0
                    self._avoidance_holds_until_s[target.mmsi] = (
                        simulation_time_s + revised_duration_s + self._random.uniform(6.0, 18.0)
                    )
                self._next_avoidance_check_s[target.mmsi] = (
                    simulation_time_s + self._random.uniform(4.0, 10.0)
                )
        return replace(
            target,
            sog_kn=speed,
            cog_deg=course,
            heading_deg=course,
            rot_deg_min=(change * 60.0 / dt_s if dt_s > 1e-6 else 0.0),
        )


def _normalize_output_filter_mode(value: str) -> str:
    normalized = str(value or "all").strip().lower().replace("-", "_").replace(" ", "_")
    return "sog_dependent" if normalized in {"sog", "sog_dependent"} else "all"


def _merge_live_target(previous: AISTarget | None, current: AISTarget) -> AISTarget:
    if previous is None:
        return current
    values = {}
    for name in (
        "name", "callsign", "nav_status", "ship_type", "destination",
    ):
        if not str(getattr(current, name) or "").strip():
            values[name] = getattr(previous, name)
    for name in (
        "length_m", "beam_m", "draught_m", "rot_deg_min", "imo_number",
        "eta_month", "eta_day", "eta_hour", "eta_minute",
    ):
        if getattr(current, name) is None:
            values[name] = getattr(previous, name)
    return replace(current, **values)


def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 6371000.0 * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a))) / 1852.0


def _destination(latitude: float, longitude: float, course_deg: float, distance_nm: float) -> tuple[float, float]:
    radius_m = 6371000.0
    angular = distance_nm * 1852.0 / radius_m
    course = math.radians(course_deg)
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    lat2 = math.asin(math.sin(lat1) * math.cos(angular) + math.cos(lat1) * math.sin(angular) * math.cos(course))
    lon2 = lon1 + math.atan2(
        math.sin(course) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), ((math.degrees(lon2) + 180.0) % 360.0) - 180.0


def _signed_angle(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


def _water_probe_distances(total_distance_nm: float) -> tuple[float, ...]:
    total = max(0.001, float(total_distance_nm))
    candidates = (
        min(total, 0.02),
        min(total, 0.05),
        min(total, 0.10),
        total * 0.18,
        total * 0.30,
        total * 0.44,
        total * 0.58,
        total * 0.72,
        total * 0.86,
        total,
    )
    return tuple(sorted({max(0.001, distance) for distance in candidates}))


def _ais_maximum_turn_rate_deg_min(target: AISTarget) -> float:
    length_m = max(10.0, min(200.0, float(target.length_m or 75.0)))
    fraction = (length_m - 10.0) / 190.0
    return 60.0 - 50.0 * fraction


def _avoidance_horizon_minutes(target: AISTarget) -> float:
    return max(8.0, 180.0 / _ais_maximum_turn_rate_deg_min(target) + 3.0)


def _ais_speed_change_rate_kn_min(target: AISTarget) -> float:
    length_m = max(10.0, min(200.0, float(target.length_m or 75.0)))
    fraction = (length_m - 10.0) / 190.0
    return 60.0 - 50.0 * fraction


def _approach(value: float, target: float, maximum_change: float) -> float:
    if value < target:
        return min(target, value + maximum_change)
    return max(target, value - maximum_change)


def _velocity_vector(speed_kn: float, course_deg: float) -> tuple[float, float]:
    course = math.radians(float(course_deg))
    speed = float(speed_kn)
    return speed * math.sin(course), speed * math.cos(course)


def _synthetic_type_profile(ship_type: str) -> tuple[str, float, float, float]:
    normalized = str(ship_type or "Other").strip().lower()
    profiles = {
        "cargo": ("Cargo", 140.0, 22.0, 7.5),
        "tanker": ("Tanker", 190.0, 32.0, 11.0),
        "passenger": ("Passenger", 115.0, 20.0, 6.0),
        "tug": ("Tug", 30.0, 10.0, 4.0),
        "pilot vessel": ("Pilot Vessel", 18.0, 6.0, 2.0),
        "fishing": ("Fishing", 28.0, 8.0, 3.5),
        "sailing": ("Sailing", 14.0, 4.0, 2.2),
        "other": ("Other", 75.0, 14.0, 5.0),
    }
    return profiles.get(normalized, profiles["other"])


def _encounter_course_and_side(encounter: str, own_course_deg: float) -> tuple[float, float]:
    normalized = _normalize_encounter_name(encounter)
    values = {
        "head_on": (own_course_deg + 180.0, 1.0),
        "crossing_from_starboard": (own_course_deg - 90.0, 1.0),
        "crossing_from_port": (own_course_deg + 90.0, -1.0),
        "overtaking_own_ship": (own_course_deg, 1.0),
        "own_ship_overtakes": (own_course_deg, -1.0),
        "close_passing_port": (own_course_deg + 180.0, -1.0),
        "close_passing_starboard": (own_course_deg + 180.0, 1.0),
    }
    if normalized not in values:
        raise ValueError(f"Unsupported encounter type: {encounter}")
    course, side = values[normalized]
    return float(course) % 360.0, side


def _normalize_encounter_name(encounter: str) -> str:
    return str(encounter).strip().lower().replace("-", "_").replace(" ", "_")


def _encounter_name(encounter: str, number: int) -> str:
    text = str(encounter).replace("_", " ").replace("-", " ").strip().title()
    return f"{text} {int(number)}"
