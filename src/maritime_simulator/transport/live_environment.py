# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import math
from urllib.parse import urlencode

from .live_weather import LiveWeatherClient, _distance_nm, current_payload


@dataclass(frozen=True, slots=True)
class LiveVectorReading:
    latitude_deg: float
    longitude_deg: float
    direction_from_deg: float
    speed_kn: float
    observation_time: str


def _vector_url(latitude_deg, longitude_deg, *, marine, api_key=""):
    parameters = {
        "latitude": f"{float(latitude_deg):.5f}",
        "longitude": f"{float(longitude_deg):.5f}",
        "current": "ocean_current_velocity,ocean_current_direction" if marine else "wind_speed_10m,wind_direction_10m",
        "timezone": "UTC",
        "cell_selection": "sea" if marine else "nearest",
    }
    if not marine:
        parameters["wind_speed_unit"] = "kn"
    if api_key:
        parameters["apikey"] = api_key
    host = "marine-api" if marine else "api"
    if api_key:
        host = "customer-" + host
    path = "marine" if marine else "forecast"
    return f"https://{host}.open-meteo.com/v1/{path}?{urlencode(parameters)}"


def wind_url(latitude_deg, longitude_deg, *, api_key=""):
    return _vector_url(latitude_deg, longitude_deg, marine=False, api_key=api_key)


def current_url(latitude_deg, longitude_deg, *, api_key=""):
    return _vector_url(latitude_deg, longitude_deg, marine=True, api_key=api_key)


def _parse_vector(payload, *, latitude_deg, longitude_deg, marine):
    data = current_payload(payload)
    current = data["current"]
    speed_field, direction_field = ("ocean_current_velocity", "ocean_current_direction") if marine else (
        "wind_speed_10m", "wind_direction_10m")
    if isinstance(current.get(speed_field), bool) or isinstance(current.get(direction_field), bool):
        raise ValueError("Invalid online vector values")
    speed = float(current[speed_field])
    direction = current.get(direction_field)
    direction = 0.0 if direction is None and speed == 0 else float(direction)
    units = data.get("current_units", {})
    if not isinstance(units, dict):
        raise ValueError("Invalid online vector units")
    unit = units.get(speed_field, "km/h" if marine else "kn")
    factors = {"kn": 1.0, "knots": 1.0, "km/h": 1/1.852, "m/s": 3600/1852, "mph": 1609.344/1852}
    if unit not in factors:
        raise ValueError("Unsupported Open-Meteo speed unit")
    speed *= factors[unit]
    if not (math.isfinite(speed) and math.isfinite(direction)
            and 0 <= speed <= (20 if marine else 150) and 0 <= direction <= 360):
        raise ValueError("Invalid online wind/current vector")
    if "latitude" in data and "longitude" in data:
        grid = (float(data["latitude"]), float(data["longitude"]))
        if not (all(math.isfinite(v) for v in grid) and -90 <= grid[0] <= 90 and -180 <= grid[1] <= 180):
            raise ValueError("Invalid Open-Meteo grid position")
        if _distance_nm(grid, (latitude_deg, longitude_deg)) > (8 if marine else 30):
            raise ValueError("Online grid cell is too far from own ship")
    return LiveVectorReading(
        float(latitude_deg), float(longitude_deg),
        (direction + (180 if marine else 0)) % 360, speed, str(current.get("time", "")),
    )


def parse_wind(payload, *, latitude_deg, longitude_deg):
    return _parse_vector(payload, latitude_deg=latitude_deg, longitude_deg=longitude_deg, marine=False)


def parse_current(payload, *, latitude_deg, longitude_deg):
    return _parse_vector(payload, latitude_deg=latitude_deg, longitude_deg=longitude_deg, marine=True)


class LiveWindClient(LiveWeatherClient):
    LABEL = "Wind"
    _url = staticmethod(wind_url)
    _parse = staticmethod(parse_wind)


class LiveCurrentClient(LiveWeatherClient):
    LABEL = "Current"
    _url = staticmethod(current_url)
    _parse = staticmethod(parse_current)
