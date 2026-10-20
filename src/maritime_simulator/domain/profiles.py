# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from importlib.resources import files
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class VesselProfile:
    profile_id: str
    display_name: str
    length_m: float
    beam_m: float
    draft_m: float
    pivot_fraction_from_bow: float
    max_speed_kn: float
    max_astern_kn: float
    acceleration_kn_s: float
    deceleration_kn_s: float
    max_rot_deg_min: float
    rot_time_constant_s: float
    max_rudder_deg: float
    rudder_rate_deg_s: float
    windage: float
    wind_lateral_gain: float
    wind_forward_gain: float
    wind_damping_floor: float
    turn_slip: float
    sway_time_constant_s: float
    rudder_effectiveness: float = 1.0
    track_parameters: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    factory_profile_id: str | None = None
    bow_thruster_power_kw: float = 0.0
    bow_thruster_thrust_kn_per_kw: float = 0.12
    bow_thruster_rated_rpm: float = 300.0
    bow_thruster_ramp_s: float = 5.0
    bow_thruster_half_effect_speed_kn: float = 3.0
    bow_thruster_fraction_from_bow: float = 0.08
    displacement_t: float = 0.0

    def __post_init__(self) -> None:
        positive = {
            "length_m": self.length_m,
            "beam_m": self.beam_m,
            "draft_m": self.draft_m,
            "max_speed_kn": self.max_speed_kn,
            "max_astern_kn": self.max_astern_kn,
            "acceleration_kn_s": self.acceleration_kn_s,
            "deceleration_kn_s": self.deceleration_kn_s,
            "max_rot_deg_min": self.max_rot_deg_min,
            "rot_time_constant_s": self.rot_time_constant_s,
            "max_rudder_deg": self.max_rudder_deg,
            "rudder_rate_deg_s": self.rudder_rate_deg_s,
            "rudder_effectiveness": self.rudder_effectiveness,
            "sway_time_constant_s": self.sway_time_constant_s,
            "bow_thruster_thrust_kn_per_kw": self.bow_thruster_thrust_kn_per_kw,
            "bow_thruster_rated_rpm": self.bow_thruster_rated_rpm,
            "bow_thruster_ramp_s": self.bow_thruster_ramp_s,
            "bow_thruster_half_effect_speed_kn": self.bow_thruster_half_effect_speed_kn,
        }
        invalid = [name for name, value in positive.items() if not math.isfinite(float(value)) or float(value) <= 0.0]
        if invalid:
            raise ValueError(f"Vessel profile values must be positive: {', '.join(invalid)}")
        if not 0.0 < self.pivot_fraction_from_bow < 1.0:
            raise ValueError("pivot_fraction_from_bow must be between 0 and 1")
        if not 0.0 <= self.bow_thruster_fraction_from_bow < self.pivot_fraction_from_bow:
            raise ValueError("Bow thruster must be forward of the vessel pivot")
        for name in ("bow_thruster_power_kw", "displacement_t"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")

    @property
    def effective_displacement_t(self) -> float:
        if self.displacement_t > 0.0:
            return self.displacement_t
        block_coefficient = 0.45 if self.length_m < 60.0 else 0.70
        return max(1.0, self.length_m * self.beam_m * self.draft_m * block_coefficient * 1.025)

    @property
    def effective_rudder_rate_deg_s(self) -> float:
        minimum_rate = 2.0 * self.max_rudder_deg / 30.0
        return max(self.rudder_rate_deg_s, minimum_rate)

    def engine_steps(self) -> tuple[tuple[str, float], ...]:
        ahead = self.max_speed_kn
        astern = self.max_astern_kn
        return (
            ("Full Ahead", ahead),
            ("Half Ahead", ahead * 0.60),
            ("Slow Ahead", ahead * 0.32),
            ("Dead Slow Ahead", ahead * 0.16),
            ("Stop", 0.0),
            ("Dead Slow Astern", -astern * 0.18),
            ("Slow Astern", -astern * 0.40),
            ("Half Astern", -astern * 0.65),
            ("Full Astern", -astern),
        )

    def speed_from_lever_percent(self, percent: float) -> float:
        """Match the telegraph's equally spaced detents, including intermediate orders."""
        if not math.isfinite(percent):
            raise ValueError("Lever percentage must be finite")
        values = self.engine_steps()
        position = (100.0 - max(-100.0, min(100.0, percent))) / 25.0
        index = min(len(values) - 2, int(position))
        fraction = position - index
        return values[index][1] + fraction * (values[index + 1][1] - values[index][1])

    def lever_percent_from_speed(self, speed_kn: float) -> float:
        if not math.isfinite(speed_kn):
            raise ValueError("Speed order must be finite")
        values = self.engine_steps()
        speed = max(values[-1][1], min(values[0][1], speed_kn))
        for index in range(len(values) - 1):
            high, low = values[index][1], values[index + 1][1]
            if low <= speed <= high:
                return 100.0 - 25.0 * (index + (high - speed) / (high - low))
        return 0.0

    def with_frozen_track_parameters(self) -> "VesselProfile":
        return replace(self, track_parameters=MappingProxyType(dict(self.track_parameters)))


_PROFILE_GEOMETRY = {
    "Crew Transfer Vessel": (10.0, 1.8, 0.35),
    "Container Feeder": (28.0, 9.0, 0.38),
    "15k TEU Container": (51.0, 15.5, 0.40),
    "24k TEU Container": (61.5, 16.5, 0.42),
    "VLCC": (60.0, 20.5, 0.40),
}

_FACTORY_PROFILE_ORDER = (
    "crew-transfer-vessel",
    "container-feeder",
    "15k-teu-container",
    "24k-teu-container",
    "vlcc",
)

# Representative demo installations, not manufacturer-calibrated vessel data.
_BOW_THRUSTER_DEFAULTS = {
    "crew-transfer-vessel": (100.0, 900.0, 3.0),
    "container-feeder": (900.0, 420.0, 5.0),
    "15k-teu-container": (3000.0, 250.0, 6.0),
    "24k-teu-container": (4000.0, 220.0, 7.0),
    "vlcc": (2500.0, 220.0, 7.0),
}


def _migrate_thruster_fields(item: dict) -> None:
    if "bow_thruster_fraction_from_bow" not in item:
        item["bow_thruster_fraction_from_bow"] = min(0.08, float(item.get("pivot_fraction_from_bow", 0.35)) * 0.5)
    defaults = _BOW_THRUSTER_DEFAULTS.get(item.get("factory_profile_id") or item.get("profile_id"))
    if defaults is not None:
        for key, value in zip(("bow_thruster_power_kw", "bow_thruster_rated_rpm", "bow_thruster_ramp_s"), defaults):
            item.setdefault(key, value)


def _slug(name: str) -> str:
    return "-".join(part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in name).split())


def _profile_from_legacy(name: str, raw: Mapping[str, Any]) -> VesselProfile:
    beam, draft, pivot = _PROFILE_GEOMETRY[name]
    core_keys = {
        "length_m",
        "max_speed",
        "max_astern",
        "acceleration",
        "deceleration",
        "max_rot",
        "rot_time_constant",
        "max_rudder",
        "rudder_rate_deg_s",
        "windage",
        "wind_lateral_gain",
        "wind_forward_gain",
        "wind_damping_floor",
        "turn_slip",
        "sway_time_constant",
        "rudder_effectiveness",
    }
    track = {key: float(value) for key, value in raw.items() if key not in core_keys and isinstance(value, (int, float))}
    return VesselProfile(
        profile_id=_slug(name),
        display_name=name,
        length_m=float(raw["length_m"]),
        beam_m=beam,
        draft_m=draft,
        pivot_fraction_from_bow=pivot,
        max_speed_kn=float(raw["max_speed"]),
        max_astern_kn=float(raw["max_astern"]),
        acceleration_kn_s=float(raw["acceleration"]),
        deceleration_kn_s=float(raw["deceleration"]),
        max_rot_deg_min=float(raw["max_rot"]),
        rot_time_constant_s=float(raw["rot_time_constant"]),
        max_rudder_deg=float(raw["max_rudder"]),
        rudder_rate_deg_s=float(raw["rudder_rate_deg_s"]),
        windage=float(raw["windage"]),
        wind_lateral_gain=float(raw["wind_lateral_gain"]),
        wind_forward_gain=float(raw["wind_forward_gain"]),
        wind_damping_floor=float(raw["wind_damping_floor"]),
        turn_slip=float(raw["turn_slip"]),
        sway_time_constant_s=float(raw["sway_time_constant"]),
        rudder_effectiveness=float(raw.get("rudder_effectiveness", 1.0)),
        track_parameters=MappingProxyType(track),
        factory_profile_id=_slug(name),
        bow_thruster_power_kw=_BOW_THRUSTER_DEFAULTS[_slug(name)][0],
        bow_thruster_rated_rpm=_BOW_THRUSTER_DEFAULTS[_slug(name)][1],
        bow_thruster_ramp_s=_BOW_THRUSTER_DEFAULTS[_slug(name)][2],
    )


def load_factory_profiles() -> Mapping[str, VesselProfile]:
    resource = files("maritime_simulator.resources").joinpath("factory_vessel_profiles.json")
    raw = json.loads(resource.read_text(encoding="utf-8"))
    profiles = {
        profile.profile_id: profile
        for name, values in raw.items()
        for profile in (_profile_from_legacy(name, values),)
    }
    return MappingProxyType(profiles)


class VesselProfileStore:
    """Immutable factory profiles plus explicitly persisted user copies."""

    def __init__(self, user_file: Path | None = None):
        self._factory = load_factory_profiles()
        self._user_file = user_file
        self._users: dict[str, VesselProfile] = {}
        if user_file is not None and user_file.exists():
            self._load_users()

    @property
    def factory_profiles(self) -> Mapping[str, VesselProfile]:
        return self._factory

    @property
    def user_profiles(self) -> Mapping[str, VesselProfile]:
        return MappingProxyType(dict(self._users))

    def get(self, profile_id: str) -> VesselProfile:
        if profile_id in self._users:
            return self._users[profile_id]
        return self._factory[profile_id]

    def all_profiles(self) -> tuple[VesselProfile, ...]:
        factory_slots = tuple(
            self._users.get(profile_id, self._factory[profile_id])
            for profile_id in _FACTORY_PROFILE_ORDER
        )
        custom = tuple(sorted(
            (profile for profile_id, profile in self._users.items() if profile_id not in self._factory),
            key=lambda profile: (
                profile.length_m,
                profile.beam_m,
                -profile.max_rot_deg_min,
                -profile.max_speed_kn,
                profile.display_name.casefold(),
            ),
        ))
        return factory_slots + custom

    def is_factory_profile(self, profile_id: str) -> bool:
        return profile_id in self._factory

    def update_profile(self, profile_id: str, **changes: Any) -> VesselProfile:
        """Persist an override while keeping a factory profile's stable identity."""
        current = self.get(profile_id)
        identity_fields = {"profile_id", "factory_profile_id"}
        unknown = set(changes) - ({item.name for item in fields(VesselProfile)} - identity_fields)
        if unknown:
            raise ValueError(f"Unsupported profile fields: {', '.join(sorted(unknown))}")
        track = changes.pop("track_parameters", dict(current.track_parameters))
        updated = replace(
            current,
            factory_profile_id=current.factory_profile_id or current.profile_id,
            track_parameters=MappingProxyType(dict(track)),
            **changes,
        )
        self._users[profile_id] = updated
        self._save_users()
        return updated

    def create_user_profile(
        self,
        base_profile_id: str,
        profile_id: str,
        display_name: str,
        **overrides: Any,
    ) -> VesselProfile:
        if profile_id in self._factory or profile_id in self._users:
            raise ValueError(f"Profile already exists: {profile_id}")
        base = self.get(base_profile_id)
        identity_fields = {"profile_id", "display_name", "factory_profile_id"}
        allowed = {field.name for field in VesselProfile.__dataclass_fields__.values()} - identity_fields
        unknown = set(overrides) - allowed
        if unknown:
            raise ValueError(f"Unsupported profile fields: {', '.join(sorted(unknown))}")
        track = overrides.pop("track_parameters", dict(base.track_parameters))
        profile = replace(
            base,
            profile_id=profile_id,
            display_name=display_name,
            factory_profile_id=base.factory_profile_id or base.profile_id,
            track_parameters=MappingProxyType(dict(track)),
            **overrides,
        )
        self._users[profile_id] = profile
        self._save_users()
        return profile

    def rename_user_profile(self, profile_id: str, display_name: str) -> VesselProfile:
        return self.update_profile(profile_id, display_name=display_name)

    def reset_user_profile(self, profile_id: str) -> VesselProfile:
        if profile_id in self._factory:
            self._users.pop(profile_id, None)
            self._save_users()
            return self._factory[profile_id]
        current = self._users[profile_id]
        factory_id = current.factory_profile_id
        if not factory_id or factory_id not in self._factory:
            raise ValueError(f"No factory origin for {profile_id}")
        factory = self._factory[factory_id]
        reset = replace(
            factory,
            profile_id=profile_id,
            display_name=current.display_name,
            factory_profile_id=factory_id,
        )
        self._users[profile_id] = reset
        self._save_users()
        return reset

    def delete_user_profile(self, profile_id: str) -> None:
        if profile_id in self._factory:
            raise ValueError("Built-in vessel profiles cannot be deleted")
        del self._users[profile_id]
        self._save_users()

    def calculate_track_parameters(self, profile_id: str) -> Mapping[str, float]:
        """Estimate a stable baseline from the tuned factory vessel envelope.

        This is deliberately an interpolation, not an auto-tuner: geometry and
        response characteristics select nearby shipped models and every output
        remains inside the range of the known controller values.
        """
        profile = self.get(profile_id)
        references = tuple(self._factory.values())
        feature_names = (
            "length_m",
            "beam_m",
            "max_speed_kn",
            "max_rot_deg_min",
            "rot_time_constant_s",
            "rudder_rate_deg_s",
            "rudder_effectiveness",
        )

        def distance(reference: VesselProfile) -> float:
            squared = 0.0
            for name in feature_names:
                value = max(1e-6, float(getattr(profile, name)))
                baseline = max(1e-6, float(getattr(reference, name)))
                squared += math.log(value / baseline) ** 2
            return math.sqrt(squared / len(feature_names))

        distances = [(reference, distance(reference)) for reference in references]
        exact = next((reference for reference, value in distances if value <= 1e-9), None)
        if exact is not None:
            return MappingProxyType(dict(exact.track_parameters))
        weights = [(reference, 1.0 / max(1e-6, value * value)) for reference, value in distances]
        keys = set.intersection(*(set(reference.track_parameters) for reference in references))
        estimated: dict[str, float] = {}
        for key in sorted(keys):
            values = [float(reference.track_parameters[key]) for reference in references]
            weighted = sum(float(reference.track_parameters[key]) * weight for reference, weight in weights)
            value = weighted / sum(weight for _reference, weight in weights)
            estimated[key] = max(min(values), min(max(values), value))
        return MappingProxyType(estimated)

    def to_payload(self) -> list[dict[str, Any]]:
        return [self._profile_payload(profile) for profile in self._users.values()]

    def apply_payload(self, payload: object) -> None:
        if not isinstance(payload, list):
            raise ValueError("Vessel profiles must be stored as a list")
        loaded: dict[str, VesselProfile] = {}
        for raw in payload:
            if not isinstance(raw, dict):
                raise ValueError("Invalid vessel profile entry")
            item = dict(raw)
            _migrate_thruster_fields(item)
            item.setdefault("rudder_effectiveness", 1.0)
            item["track_parameters"] = MappingProxyType(dict(item.get("track_parameters", {})))
            profile = VesselProfile(**item)
            if profile.profile_id in loaded:
                raise ValueError(f"Duplicate vessel profile: {profile.profile_id}")
            loaded[profile.profile_id] = profile
        self._users = loaded
        self._save_users()

    def _save_users(self) -> None:
        if self._user_file is None:
            return
        self._user_file.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_payload()
        temporary = self._user_file.with_suffix(self._user_file.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self._user_file)

    def _load_users(self) -> None:
        raw = json.loads(self._user_file.read_text(encoding="utf-8"))
        for item in raw:
            _migrate_thruster_fields(item)
            item.setdefault("rudder_effectiveness", 1.0)
            item["track_parameters"] = MappingProxyType(dict(item.get("track_parameters", {})))
            profile = VesselProfile(**item)
            self._users[profile.profile_id] = profile

    @staticmethod
    def _profile_payload(profile: VesselProfile) -> dict[str, Any]:
        data = {
            item.name: getattr(profile, item.name)
            for item in fields(VesselProfile)
            if item.name != "track_parameters"
        }
        data["track_parameters"] = dict(profile.track_parameters)
        return data
