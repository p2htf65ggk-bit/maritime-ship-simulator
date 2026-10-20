# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import math


EARTH_RADIUS_M = 6_371_008.8
NM_TO_M = 1852.0


def _longitude_delta_deg(longitude_deg: float, reference_longitude_deg: float) -> float:
    return (float(longitude_deg) - float(reference_longitude_deg) + 180.0) % 360.0 - 180.0


def local_offset_m(
    reference_latitude_deg: float,
    reference_longitude_deg: float,
    latitude_deg: float,
    longitude_deg: float,
) -> tuple[float, float]:
    """Return east/north metres in a stable local tangent approximation."""
    reference_latitude_rad = math.radians(float(reference_latitude_deg))
    north_m = math.radians(float(latitude_deg) - float(reference_latitude_deg)) * EARTH_RADIUS_M
    east_m = (
        math.radians(_longitude_delta_deg(longitude_deg, reference_longitude_deg))
        * EARTH_RADIUS_M
        * max(1e-6, abs(math.cos(reference_latitude_rad)))
    )
    return east_m, north_m


def range_bearing_m(
    reference_latitude_deg: float,
    reference_longitude_deg: float,
    latitude_deg: float,
    longitude_deg: float,
) -> tuple[float, float]:
    east_m, north_m = local_offset_m(
        reference_latitude_deg,
        reference_longitude_deg,
        latitude_deg,
        longitude_deg,
    )
    distance_m = math.hypot(east_m, north_m)
    bearing_deg = math.degrees(math.atan2(east_m, north_m)) % 360.0
    return distance_m, bearing_deg


@dataclass(frozen=True, slots=True)
class LocalFrame:
    reference_latitude_deg: float
    reference_longitude_deg: float

    def offset_m(self, latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
        return local_offset_m(
            self.reference_latitude_deg,
            self.reference_longitude_deg,
            latitude_deg,
            longitude_deg,
        )
    def range_bearing(self, latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
        return range_bearing_m(
            self.reference_latitude_deg,
            self.reference_longitude_deg,
            latitude_deg,
            longitude_deg,
        )
