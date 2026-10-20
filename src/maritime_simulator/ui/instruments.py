# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtWidgets import QLabel

from maritime_simulator.domain.profiles import VesselProfile
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.domain.state import SimulationSnapshot
from ui.instrument_cluster import InstrumentCluster


class _SnapshotAISView:
    def __init__(self):
        self.targets: tuple[dict, ...] = ()

    def get_map_targets(self) -> tuple[dict, ...]:
        return self.targets


class _SnapshotInstrumentModel:
    """Read-only compatibility view for V1's proven instrument painters."""

    def __init__(self):
        self.lat = 0.0
        self.lon = 0.0
        self.heading = 0.0
        self.cog = 0.0
        self.sog = 0.0
        self.stw = 0.0
        self.target_speed = 0.0
        self.rate_of_turn = 0.0
        self.rudder = 0.0
        self.rudder_order = 0.0
        self.rudder_response = 0.0
        self.rudder_order_port = 0.0
        self.rudder_response_port = 0.0
        self.depth = 0.0
        self.wind_direction = 0.0
        self.wind_speed = 0.0
        self.current_direction = 0.0
        self.current_speed = 0.0
        self.current_set = 0.0
        self.current_drift = 0.0
        self.prc_lever_demand = 0.0
        self.thruster_rpm_demand = 0.0
        self.thruster_pitch_demand = 0.0
        self.thruster_azimuth_demand = 0.0
        self.thruster_rpm_response = 0.0
        self.thruster_pitch_response = 0.0
        self.thruster_azimuth_response = 0.0
        self.additional_sensors = {"RUDDER2": False}
        self.engine_steps = (
            ("Full Astern", -10.0), ("Half Astern", -6.5), ("Slow Astern", -4.0),
            ("Stop", 0.0), ("Slow Ahead", 11.0), ("Half Ahead", 21.0), ("Full Ahead", 35.0),
        )
        self.sensor: dict[str, float] = {}

    def update(self, snapshot: SimulationSnapshot, profile: VesselProfile | None) -> None:
        self.lat = snapshot.latitude_deg
        self.lon = snapshot.longitude_deg
        self.heading = snapshot.heading_deg
        self.cog = snapshot.cog_deg
        self.sog = snapshot.sog_kn
        self.stw = snapshot.stw_kn
        self.target_speed = snapshot.speed_order_kn
        self.rate_of_turn = snapshot.rate_of_turn_deg_min
        self.rudder = snapshot.rudder_response_starboard_deg
        self.rudder_order = snapshot.rudder_order_starboard_deg
        self.rudder_response = snapshot.rudder_response_starboard_deg
        self.rudder_order_port = snapshot.rudder_order_port_deg
        self.rudder_response_port = snapshot.rudder_response_port_deg
        self.depth = snapshot.depth_m
        self.wind_direction = snapshot.true_wind_from_deg
        self.wind_speed = snapshot.true_wind_speed_kn
        self.current_direction = snapshot.current_from_deg
        self.current_speed = snapshot.current_speed_kn
        self.current_set = snapshot.current_from_deg
        self.current_drift = snapshot.current_speed_kn
        self.prc_lever_demand = snapshot.propeller_lever_demand_percent
        self.thruster_rpm_demand = snapshot.thruster_rpm_demand_percent
        self.thruster_pitch_demand = snapshot.thruster_pitch_demand_percent
        self.thruster_azimuth_demand = snapshot.thruster_azimuth_demand_deg
        self.thruster_rpm_response = snapshot.thruster_rpm_response_percent
        self.thruster_pitch_response = snapshot.thruster_pitch_response_percent
        self.thruster_azimuth_response = snapshot.thruster_azimuth_response_deg
        self.bow_thruster_order = snapshot.bow_thruster_order_percent
        self.bow_thruster_response = snapshot.bow_thruster_response_percent
        self.bow_thruster_efficiency = snapshot.bow_thruster_efficiency
        if snapshot.control_mode is ControlMode.SENSOR:
            self.bow_thruster_order = snapshot.thruster_rpm_demand_percent
            self.bow_thruster_response = snapshot.thruster_rpm_response_percent
            self.bow_thruster_efficiency = 1.0
        self.additional_sensors = {"RUDDER2": snapshot.dual_rudder}
        if profile is not None:
            # The engine painter expects ascending numeric limits; the control combo itself remains Full Ahead first.
            self.engine_steps = tuple(sorted(profile.engine_steps(), key=lambda item: item[1]))
        self.sensor = {
            "lat": snapshot.latitude_deg,
            "lon": snapshot.longitude_deg,
            "heading": snapshot.heading_deg,
            "cog": snapshot.cog_deg,
            "sog": snapshot.sog_kn,
            "stw": snapshot.stw_kn,
            "rot": snapshot.rate_of_turn_deg_min,
            "depth": snapshot.depth_m,
            "wind_direction": snapshot.true_wind_from_deg,
            "wind_speed": snapshot.true_wind_speed_kn,
            "current_direction": snapshot.current_from_deg,
            "current_drift": snapshot.current_speed_kn,
            "rudder_order": snapshot.rudder_order_starboard_deg,
            "rudder_response": snapshot.rudder_response_starboard_deg,
            "rsa_angle": snapshot.rudder_response_starboard_deg,
            "rudder_order_port": snapshot.rudder_order_port_deg,
            "rudder_response_port": snapshot.rudder_response_port_deg,
            "vbw_transverse_water": snapshot.transverse_water_kn,
            "vbw_transverse_ground": snapshot.transverse_ground_kn,
            "vbw_fwd_transverse_water": snapshot.forward_transverse_water_kn,
            "vbw_fwd_transverse_ground": snapshot.forward_transverse_ground_kn,
            "vbw_aft_transverse_water": snapshot.aft_transverse_water_kn,
            "vbw_aft_transverse_ground": snapshot.aft_transverse_ground_kn,
        }

    @staticmethod
    def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta = math.radians(lon2 - lon1)
        y = math.sin(delta) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta)
        return math.degrees(math.atan2(y, x)) % 360.0

    @staticmethod
    def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        value = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lon / 2.0) ** 2
        return 3440.065 * 2.0 * math.asin(min(1.0, math.sqrt(value)))


class SnapshotInstrumentCluster(InstrumentCluster):
    """Configurable graphical cluster that consumes immutable V2 snapshots only."""

    DEFAULT_ITEMS = [
        "hdg", "cog", "speed", "engine", "bow_thruster", "rot", "rudder", "dual_rudder",
        "docking_speed", "wind", "current", "depth", "ais", "time",
    ]

    def __init__(
        self,
        parent=None,
        *,
        profile_lookup: Callable[[str], VesselProfile] | None = None,
        ais_targets: Callable[[SimulationSnapshot], tuple[dict, ...]] | None = None,
    ):
        self._snapshot_model = _SnapshotInstrumentModel()
        self._snapshot_ais = _SnapshotAISView()
        self._profile_lookup = profile_lookup
        self._ais_targets = ais_targets
        self._last_snapshot_refresh = 0.0
        super().__init__(self._snapshot_model, self._snapshot_ais)
        self.items = list(self.DEFAULT_ITEMS)
        self._rebuild_grid()
        # Compatibility probe used by the V2 boundary test; these labels are not part of the visible layout.
        self._values = {"rudder": QLabel("ORD -- / ACT --", self)}
        self._values["rudder"].hide()

    def update_snapshot(self, snapshot: SimulationSnapshot) -> None:
        profile = self._profile_lookup(snapshot.vessel_profile_id) if self._profile_lookup else None
        self._snapshot_model.update(snapshot, profile)
        if self._ais_targets is not None:
            self._snapshot_ais.targets = tuple(self._ais_targets(snapshot))
        self._values["rudder"].setText(
            f"ORD {snapshot.rudder_order_starboard_deg:+.1f} / ACT {snapshot.rudder_response_starboard_deg:+.1f}"
        )
        now = time.monotonic()
        if now - self._last_snapshot_refresh >= 0.08:
            self._last_snapshot_refresh = now
            self.refresh()

    def reset_to_defaults(self) -> None:
        self.items = list(self.DEFAULT_ITEMS)
        self._rebuild_grid()

    def export_state(self):
        return {**super().export_state(), "bow_thruster_instrument_version": 1}

    def apply_state(self, state):
        super().apply_state(state)
        if isinstance(state, dict) and not state.get("bow_thruster_instrument_version") and "bow_thruster" not in self.items:
            index = self.items.index("engine") + 1 if "engine" in self.items else len(self.items)
            self.items.insert(index, "bow_thruster")
            self._rebuild_grid()
