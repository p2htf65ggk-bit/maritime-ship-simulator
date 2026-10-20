# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

STARTUP_GROUPS = (
    "control",
    "position_heading",
    "environment",
    "ais",
    "sensors",
    "noise",
    "fault_injection",
    "mode",
    "offline",
    "map",
    "vessel_models",
    "protocol_output",
    "external_rudder_input",
    "instruments",
    "advanced",
    "route",
)

STARTUP_GROUP_LABELS = {
    "offline": "Offline",
    "map": "Map & Layers",
    "mode": "Mode",
    "vessel_models": "Vessel Models",
    "control": "Control",
    "position_heading": "Position & Heading",
    "environment": "Environment",
    "ais": "AIS",
    "sensors": "Sensors",
    "noise": "Noise",
    "fault_injection": "Fault Injection",
    "protocol_output": "Protocol Output",
    "external_rudder_input": "External Input",
    "instruments": "Instruments",
    "advanced": "Advanced",
    "route": "Route",
}

_GROUP_PATHS = {
    "offline": (("offline", "enabled"), ("offline", "water_file")),
    "map": (
        ("offline", "map_source"),
        ("offline", "map_file"),
        ("offline", "enc_layers"),
        ("offline", "enc_base"),
        ("offline", "enc_cells"),
        ("offline", "enc_safety_contour_m"),
        ("offline", "enc_safety_depth_m"),
    ),
    "mode": (
        ("app_state", "simulation", "control_mode"),
        ("app_state", "simulation", "time_scale"),
    ),
    "vessel_models": (
        ("app_state", "simulation", "vessel_profile_id"),
        ("vessel_profiles",),
    ),
    "control": (
        ("app_state", "simulation", "control"),
        ("app_state", "use_planned_speed"),
        ("app_state", "use_planned_turns"),
        ("app_state", "control_turn_mode"),
        ("app_state", "control_turn_rate_deg_min"),
        ("app_state", "control_turn_radius_nm"),
    ),
    "position_heading": (("app_state", "simulation", "position"),),
    "environment": (("app_state", "simulation", "environment"),),
    "ais": (("app_state", "ais"),),
    "sensors": (
        ("app_state", "simulation", "sensor_values", "cog_deg"),
        ("app_state", "simulation", "sensor_values", "sog_kn"),
        ("app_state", "simulation", "sensor_values", "stw_kn"),
        ("app_state", "simulation", "sensor_values", "rot_deg_min"),
        ("app_state", "simulation", "sensor_values", "rudder_order_starboard_deg"),
        ("app_state", "simulation", "sensor_values", "rudder_response_starboard_deg"),
        ("app_state", "simulation", "sensor_values", "rudder_order_port_deg"),
        ("app_state", "simulation", "sensor_values", "rudder_response_port_deg"),
        ("app_state", "simulation", "sensor_values", "dual_rudder"),
        ("app_state", "simulation", "sensor_values", "depth_m"),
        ("app_state", "simulation", "sensor_coupling"),
        ("app_state", "simulation", "auxiliary_sensors"),
    ),
    "noise": (("app_state", "simulation", "sensor_noise"),),
    "fault_injection": (("app_state", "fault_injection"),),
    "protocol_output": (("protocol_output",),),
    "external_rudder_input": (("external_rudder_input",),),
    "instruments": (("instrument_cluster",),),
    "advanced": (("advanced_output",), ("advanced_ports",)),
    "route": (
        ("app_state", "route"),
        ("app_state", "route_end_action"),
    ),
}


class StartupProfileStore:
    """Persist and resolve independent Fixed/Last-saved startup groups."""

    FORMAT = "maritime-ship-simulator-v2-startup-profile"
    FORMAT_VERSION = 5

    def __init__(self, path: Path):
        self.path = Path(path)
        self.modes = {group: "last" for group in STARTUP_GROUPS}
        self.fixed_groups: dict[str, dict] = {}
        self.last_saved_configuration: dict | None = None
        self.route_attachment: dict[str, str] | None = None
        self.load()

    def mode(self, group: str) -> str:
        self._validate_group(group)
        return self.modes[group]

    def set_mode(
        self, group: str, mode: str, current_configuration: dict | None = None
    ) -> None:
        self._validate_group(group)
        normalized = str(mode).strip().lower()
        if normalized not in {"fixed", "last"}:
            raise ValueError("Startup group mode must be Fixed or Last Saved")
        if normalized == "fixed" and group not in self.fixed_groups:
            if current_configuration is None:
                raise ValueError(
                    f"No fixed startup snapshot exists for {STARTUP_GROUP_LABELS[group]}"
                )
            self.capture(group, current_configuration)
            return
        self.modes[group] = normalized
        self.save()

    def capture(self, group: str, configuration: dict) -> None:
        """Compatibility API: capture one fixed group and select it."""
        self.capture_fixed(group, configuration, select=True)

    def capture_fixed(
        self, group: str, configuration: dict, *, select: bool = False
    ) -> None:
        self._validate_group(group)
        self._validate_configuration(configuration)
        fragment = self.group_fragment(group, configuration)
        if not fragment:
            raise ValueError(
                f"Current configuration has no {STARTUP_GROUP_LABELS[group]} data"
            )
        self.fixed_groups[group] = fragment
        if group == "route":
            self.route_attachment = None
        if select:
            self.modes[group] = "fixed"
        self.save()

    def capture_last_saved(
        self, group: str, configuration: dict, *, select: bool = False
    ) -> None:
        self._validate_group(group)
        self._validate_configuration(configuration)
        fragment = self.group_fragment(group, configuration)
        if not fragment:
            raise ValueError(
                f"Current configuration has no {STARTUP_GROUP_LABELS[group]} data"
            )
        base = deepcopy(self.last_saved_configuration or configuration)
        for path in _GROUP_PATHS[group]:
            present, value = _read_path(fragment, path)
            if present:
                _write_path(base, path, deepcopy(value))
        _synchronize_position_heading(base)
        self.last_saved_configuration = base
        if select:
            self.modes[group] = "last"
        self.save()

    def capture_all_fixed(self, configuration: dict) -> None:
        self._validate_configuration(configuration)
        fragments = {
            group: self.group_fragment(group, configuration) for group in STARTUP_GROUPS
        }
        missing = [
            STARTUP_GROUP_LABELS[group]
            for group, value in fragments.items()
            if not value
        ]
        if missing:
            raise ValueError(
                f"Current configuration has no data for: {', '.join(missing)}"
            )
        self.fixed_groups = fragments
        self.route_attachment = None
        self.save()

    def capture_all_last_saved(self, configuration: dict) -> None:
        self._validate_configuration(configuration)
        self.last_saved_configuration = deepcopy(configuration)
        _synchronize_position_heading(self.last_saved_configuration)
        self.save()

    def set_all_modes(
        self, mode: str, current_configuration: dict | None = None
    ) -> None:
        normalized = str(mode).strip().lower()
        if normalized not in {"fixed", "last"}:
            raise ValueError("Startup group mode must be Fixed or Last Saved")
        if normalized == "fixed":
            missing = [
                group for group in STARTUP_GROUPS if group not in self.fixed_groups
            ]
            if missing:
                if current_configuration is None:
                    labels = ", ".join(STARTUP_GROUP_LABELS[group] for group in missing)
                    raise ValueError(f"No fixed startup snapshot exists for: {labels}")
                self._validate_configuration(current_configuration)
                for group in missing:
                    fragment = self.group_fragment(group, current_configuration)
                    if not fragment:
                        raise ValueError(
                            f"Current configuration has no {STARTUP_GROUP_LABELS[group]} data"
                        )
                    self.fixed_groups[group] = fragment
        self.modes = {group: normalized for group in STARTUP_GROUPS}
        self.save()

    def group_fragment(self, group: str, configuration: dict | None) -> dict:
        self._validate_group(group)
        if not isinstance(configuration, dict):
            return {}
        if group in {"offline", "map"}:
            defaults = {"enabled": False, "water_file": ""} if group == "offline" else {
                "map_source": "auto", "map_file": "", "enc_layers": None, "enc_base": False, "enc_cells": None,
                "enc_safety_contour_m": 30.0, "enc_safety_depth_m": 30.0}
            values = configuration.get("offline", {})
            return {"offline": {key: deepcopy(values.get(key, value)) for key, value in defaults.items()}}
        fragment: dict = {}
        for path in _GROUP_PATHS[group]:
            present, value = _read_path(configuration, path)
            if present:
                _write_path(fragment, path, deepcopy(value))
        return fragment

    def fixed_group(self, group: str) -> dict:
        self._validate_group(group)
        return deepcopy(self.fixed_groups.get(group, {}))

    def last_saved_group(self, group: str) -> dict:
        return self.group_fragment(group, self.last_saved_configuration)

    def attach_route(
        self, *, name: str, rtz_text: str, route_configuration: dict
    ) -> None:
        self.capture("route", route_configuration)
        self.route_attachment = {"name": str(name), "rtz": str(rtz_text)}
        self.save()

    def resolve(self, last_used: dict | None, defaults: dict) -> dict:
        self._validate_configuration(defaults)
        saved = self.last_saved_configuration or last_used
        if isinstance(saved, dict) and saved.get("format") == defaults.get("format"):
            resolved = deepcopy(saved)
        else:
            resolved = deepcopy(defaults)
        for group in STARTUP_GROUPS:
            if self.modes[group] != "fixed":
                continue
            fragment = self.fixed_groups.get(group)
            if not isinstance(fragment, dict):
                continue
            for path in _GROUP_PATHS[group]:
                present, value = _read_path(fragment, path)
                if present:
                    _write_path(resolved, path, deepcopy(value))
        _synchronize_position_heading(resolved)
        return resolved

    def selected_configuration(self, group: str, current_last_used: dict) -> dict:
        self._validate_group(group)
        if self.modes[group] == "fixed" and group in self.fixed_groups:
            return deepcopy(self.fixed_groups[group])
        return self.group_fragment(
            group,
            self.last_saved_configuration or current_last_used,
        )

    def to_payload(self) -> dict:
        return {
            "format": self.FORMAT,
            "format_version": self.FORMAT_VERSION,
            "groups": {
                group: {
                    "mode": self.modes[group],
                    "fixed": deepcopy(self.fixed_groups.get(group)),
                }
                for group in STARTUP_GROUPS
            },
            "last_saved": deepcopy(self.last_saved_configuration),
            "route_attachment": deepcopy(self.route_attachment),
        }

    def apply_payload(self, payload: dict) -> None:
        if not isinstance(payload, dict) or payload.get("format") != self.FORMAT:
            raise ValueError(
                "The selected file is not a Maritime Ship Simulator V2 Startup Setup"
            )
        version = int(payload.get("format_version", -1))
        if version not in {1, 2, 3, 4, self.FORMAT_VERSION}:
            raise ValueError("Unsupported Startup Setup version")
        groups = payload.get("groups")
        if not isinstance(groups, dict):
            raise ValueError("Startup profile has no groups")
        modes = {group: "last" for group in STARTUP_GROUPS}
        fixed: dict[str, dict] = {}
        for group in STARTUP_GROUPS:
            value = groups.get(group, {})
            if not isinstance(value, dict):
                raise ValueError(f"Invalid startup group: {group}")
            mode = str(value.get("mode", "last")).lower()
            if mode not in {"fixed", "last"}:
                raise ValueError(f"Invalid startup mode for {group}: {mode}")
            fragment = value.get("fixed")
            if fragment is not None and not isinstance(fragment, dict):
                raise ValueError(f"Invalid fixed data for startup group: {group}")
            if mode == "fixed" and fragment is None:
                raise ValueError(f"Fixed startup group has no snapshot: {group}")
            modes[group] = mode
            if fragment is not None:
                fixed[group] = deepcopy(fragment)
        if version == 1 and "position_heading" not in groups:
            old_sensors = fixed.get("sensors", {})
            present, heading = _read_path(
                old_sensors,
                ("app_state", "simulation", "sensor_values", "heading_deg"),
            )
            if present:
                position_fragment: dict = {}
                _write_path(
                    position_fragment,
                    ("app_state", "simulation", "position", "heading_deg"),
                    deepcopy(heading),
                )
                fixed["position_heading"] = position_fragment
                if modes.get("sensors") == "fixed":
                    modes["position_heading"] = "fixed"
        if version <= 2 and "vessel_models" not in groups:
            old_mode = fixed.get("mode", {})
            vessel_models: dict = {}
            for path in _GROUP_PATHS["vessel_models"]:
                present, value = _read_path(old_mode, path)
                if present:
                    _write_path(vessel_models, path, deepcopy(value))
            if vessel_models:
                fixed["vessel_models"] = vessel_models
                modes["vessel_models"] = modes.get("mode", "last")
        if version <= 4 and "map" not in groups and "offline" in fixed:
            old_offline = fixed["offline"]
            fixed["map"] = self.group_fragment("map", old_offline)
            modes["map"] = modes["offline"]
            fixed["offline"] = self.group_fragment("offline", old_offline)
        attachment = payload.get("route_attachment")
        if attachment is not None:
            if not isinstance(attachment, dict) or not isinstance(
                attachment.get("rtz"), str
            ):
                raise ValueError("Invalid embedded RTZ route")
            attachment = {
                "name": str(attachment.get("name", "startup-route.rtz")),
                "rtz": attachment["rtz"],
            }
        last_saved = payload.get("last_saved") if version >= 2 else None
        if last_saved is not None:
            self._validate_configuration(last_saved)
            last_saved = deepcopy(last_saved)
            _synchronize_position_heading(last_saved)
        self.modes = modes
        self.fixed_groups = fixed
        self.last_saved_configuration = last_saved
        self.route_attachment = deepcopy(attachment)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.apply_payload(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self.modes = {group: "last" for group in STARTUP_GROUPS}
            self.fixed_groups = {}
            self.last_saved_configuration = None
            self.route_attachment = None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def export_to(self, path: Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def import_from(self, path: Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.apply_payload(payload)
        self.save()

    @staticmethod
    def _validate_group(group: str) -> None:
        if group not in STARTUP_GROUPS:
            raise KeyError(group)

    @staticmethod
    def _validate_configuration(configuration: dict) -> None:
        if (
            not isinstance(configuration, dict)
            or configuration.get("format") != "maritime-ship-simulator-v2-configuration"
        ):
            raise ValueError("Invalid simulator configuration")


def _read_path(source: dict, path: tuple[str, ...]) -> tuple[bool, object]:
    value: object = source
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _write_path(target: dict, path: tuple[str, ...], value: object) -> None:
    cursor = target
    for part in path[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[path[-1]] = value


def _synchronize_position_heading(configuration: dict) -> None:
    """Keep the single startup heading owner consistent with legacy sensor state."""
    present, heading = _read_path(
        configuration,
        ("app_state", "simulation", "position", "heading_deg"),
    )
    if present:
        _write_path(
            configuration,
            ("app_state", "simulation", "sensor_values", "heading_deg"),
            deepcopy(heading),
        )
