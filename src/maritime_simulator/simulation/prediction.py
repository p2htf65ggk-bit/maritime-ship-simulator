# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class PredictedPoint:
    latitude_deg: float
    longitude_deg: float
    simulation_offset_s: float


def predict_six_minute_path(
    latitude_deg: float,
    longitude_deg: float,
    cog_deg: float,
    sog_kn: float,
    rot_deg_min: float,
    *,
    step_s: float = 5.0,
) -> tuple[PredictedPoint, ...]:
    """Predict exactly six minutes of simulation motion, independent of wall time."""

    if step_s <= 0.0 or step_s > 360.0:
        raise ValueError("step_s must be in (0, 360]")
    latitude = float(latitude_deg)
    longitude = float(longitude_deg)
    course = float(cog_deg) % 360.0
    speed_mps = abs(float(sog_kn)) * 0.514444
    result = [PredictedPoint(latitude, longitude, 0.0)]
    elapsed = 0.0
    while elapsed < 360.0 - 1e-9:
        dt_s = min(step_s, 360.0 - elapsed)
        course = (course + float(rot_deg_min) * dt_s / 60.0) % 360.0
        distance_m = speed_mps * dt_s
        radians = math.radians(course)
        latitude += distance_m * math.cos(radians) / 111_320.0
        cos_latitude = max(1e-6, abs(math.cos(math.radians(latitude))))
        longitude += distance_m * math.sin(radians) / (111_320.0 * cos_latitude)
        elapsed += dt_s
        result.append(PredictedPoint(latitude, longitude, elapsed))
    return tuple(result)
