# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import math
from threading import RLock, Thread
from typing import Callable, Iterable

from maritime_simulator.domain.state import SimulationSnapshot

from .coordinates import NM_TO_M, range_bearing_m
from .geodata import OverpassGeodataProvider
from .models import GeographicScene, VisualSettings


MAX_VISUAL_AIS_TARGETS = 80


class VisualWorldManager:
    """Transforms simulator snapshots into renderer DTOs without mutating simulation state."""

    def __init__(
        self,
        geodata: OverpassGeodataProvider,
        *,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.geodata = geodata
        self._loading = False
        self._result: tuple[GeographicScene | None, Exception | None] | None = None
        self._requested_key = ""
        self._scene: GeographicScene | None = None
        self._status = "Geographic data not loaded"
        self._lock = RLock()
        self._closed = False
        self._now = now
        current = now()
        self._wall_time_of_day_s = current.hour * 3600.0 + current.minute * 60.0 + current.second
        self._initial_simulation_time_s: float | None = None

    @property
    def scene(self) -> GeographicScene | None:
        with self._lock:
            return self._scene

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    def request_area(
        self,
        snapshot: SimulationSnapshot,
        settings: VisualSettings | None = None,
        *,
        force: bool = False,
    ) -> bool:
        settings = settings or VisualSettings()
        radius_nm = min(settings.draw_distance_nm, settings.visibility_nm)
        area_key = self.geodata.area_key(
            snapshot.latitude_deg, snapshot.longitude_deg, radius_nm
        )
        with self._lock:
            if self._closed or self._loading or self._result is not None:
                return False
            if not force and area_key == self._requested_key and self._scene is not None:
                return False
            self._requested_key = area_key
            self._status = "Loading OSM/OpenSeaMap area..."
            self._loading = True
            self._result = None
        Thread(
            target=self._load_area,
            args=(
                snapshot.latitude_deg,
                snapshot.longitude_deg,
                radius_nm,
                force,
            ),
            name="visual-geodata",
            daemon=True,
        ).start()
        return True

    def _load_area(
        self, latitude_deg: float, longitude_deg: float, radius_nm: float, force: bool
    ) -> None:
        scene = None
        error = None
        try:
            scene = self.geodata.load(
                latitude_deg, longitude_deg, radius_nm=radius_nm, force=force
            )
        except Exception as caught:  # defensive boundary around optional visual data
            error = caught
        with self._lock:
            if not self._closed:
                self._result = (scene, error)
            self._loading = False

    def poll_area(self) -> bool:
        with self._lock:
            result = self._result
            if result is None:
                return False
            self._result = None
        scene, error = result
        if error is not None:
            with self._lock:
                self._status = f"Water-only fallback; geographic loader failed: {error}"
            return True
        if scene is None:
            return False
        with self._lock:
            self._scene = scene
            self._status = scene.source_status
        return True

    def state_payload(
        self,
        snapshot: SimulationSnapshot,
        ais_targets: Iterable[dict],
        settings: VisualSettings | None = None,
        vessel_profile=None,
    ) -> dict:
        settings = settings or VisualSettings()
        if self._initial_simulation_time_s is None:
            self._initial_simulation_time_s = snapshot.simulation_time_s
        simulation_elapsed = snapshot.simulation_time_s - self._initial_simulation_time_s
        time_of_day_s = (self._wall_time_of_day_s + simulation_elapsed) % 86_400.0
        sea_state = settings.sea_state
        if sea_state is None:
            sea_state = min(9.0, max(0.0, snapshot.true_wind_speed_kn / 5.0))

        targets = []
        max_range_m = min(20.0, max(0.5, settings.draw_distance_nm)) * NM_TO_M
        for target in ais_targets:
            try:
                distance_m, bearing_deg = range_bearing_m(
                    snapshot.latitude_deg,
                    snapshot.longitude_deg,
                    float(target["lat"]),
                    float(target["lon"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if distance_m > max_range_m:
                continue
            course_deg = _normalized_angle(target.get("course"), 0.0)
            heading_deg = _normalized_heading(target.get("heading"), course_deg)
            rot_deg_min = _optional_float(target.get("rot"))
            prediction_age_s = max(
                0.0,
                _optional_float(target.get("dead_reckoning_age_s")) or 0.0,
            )
            if rot_deg_min is not None:
                heading_deg = (heading_deg + rot_deg_min * prediction_age_s / 60.0) % 360.0
            raw_length = _optional_float(target.get("length"))
            raw_beam = _optional_float(target.get("beam"))
            dimensions_known = bool(
                raw_length is not None and raw_length > 0.0
                and raw_beam is not None and raw_beam > 0.0
            )
            targets.append({
                "mmsi": int(target.get("mmsi", 0)),
                "name": str(target.get("name") or ""),
                "ship_type": str(target.get("ship_type") or "Unknown"),
                "lat": float(target["lat"]),
                "lon": float(target["lon"]),
                "heading": heading_deg,
                "cog": course_deg,
                "sog": float(target.get("speed", 0.0) or 0.0),
                "rot": rot_deg_min,
                "prediction_age_s": prediction_age_s,
                "length": max(6.0, raw_length or 60.0),
                "beam": max(2.0, raw_beam or 12.0),
                "dimensions_known": dimensions_known,
                "range_m": distance_m,
                "bearing": bearing_deg,
                "output_active": bool(target.get("active", True)),
                "source": "AIS",
            })
        targets.sort(key=lambda item: item["range_m"])

        scene = self.scene
        return {
            "revision": snapshot.revision,
            "simulation_time_s": snapshot.simulation_time_s,
            "origin": {"lat": snapshot.latitude_deg, "lon": snapshot.longitude_deg},
            "own_ship": {
                "profile_id": snapshot.vessel_profile_id,
                "name": str(getattr(vessel_profile, "display_name", "Own Ship")),
                "length": float(getattr(vessel_profile, "length_m", 90.0)),
                "beam": float(getattr(vessel_profile, "beam_m", 16.0)),
                "heading": snapshot.heading_deg,
                "cog": snapshot.cog_deg,
                "sog": snapshot.sog_kn,
                "stw": snapshot.stw_kn,
                "rot": snapshot.rate_of_turn_deg_min,
                "depth_m": snapshot.depth_m,
                "lat": snapshot.latitude_deg,
                "lon": snapshot.longitude_deg,
            },
            "environment": {
                "time_of_day_s": time_of_day_s,
                "visibility_nm": settings.visibility_nm,
                "sea_state": sea_state,
                "cloud_cover_percent": settings.cloud_cover_percent,
                "wind_from_deg": snapshot.true_wind_from_deg,
                "wind_speed_kn": snapshot.true_wind_speed_kn,
            },
            "camera": {
                "height_m": settings.camera_height_m,
                "longitudinal_m": settings.camera_longitudinal_m,
                "lateral_m": settings.camera_lateral_m,
                "pitch_deg": settings.camera_pitch_deg,
                "roll_deg": settings.camera_roll_deg,
                "horizontal_fov_deg": settings.horizontal_fov_deg,
                "show_bow": settings.show_own_ship_bow,
            },
            "ais_targets": targets[:MAX_VISUAL_AIS_TARGETS],
            "geodata_scene_id": scene.scene_id if scene is not None else "",
            "geodata_status": self.status,
            "settings": asdict(settings),
        }

    def geodata_payload(self) -> dict:
        scene = self.scene
        if scene is None:
            return {
                "scene_id": "",
                "reference": {"lat": 0.0, "lon": 0.0},
                "polygons": [],
                "lines": [],
                "objects": [],
                "source_status": self.status,
            }
        return scene.to_dict()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._result = None


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _normalized_angle(value, fallback: float) -> float:
    converted = _optional_float(value)
    if converted is None or not 0.0 <= converted < 360.0:
        return float(fallback) % 360.0
    return converted


def _normalized_heading(value, fallback: float) -> float:
    converted = _optional_float(value)
    if converted is None or not 0.0 <= converted <= 359.0:
        return float(fallback) % 360.0
    return converted
