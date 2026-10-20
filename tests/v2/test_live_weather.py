# SPDX-License-Identifier: MPL-2.0
import json

import pytest

from maritime_simulator.transport.live_weather import (
    _distance_nm,
    open_meteo_url,
    parse_open_meteo_current,
)


def test_open_meteo_request_uses_current_own_ship_position_and_no_api_key():
    url = open_meteo_url(53.53657, 9.97293)
    assert "latitude=53.53657" in url
    assert "longitude=9.97293" in url
    assert "temperature_2m" in url
    assert "relative_humidity_2m" in url
    assert "dew_point_2m" in url
    assert "surface_pressure" in url
    assert "api_key" not in url.lower()


def test_open_meteo_current_values_convert_pressure_to_bar():
    reading = parse_open_meteo_current(
        json.dumps(
            {
                "current": {
                    "time": "2026-09-18T10:15",
                    "temperature_2m": 17.4,
                    "relative_humidity_2m": 73.0,
                    "dew_point_2m": 12.5,
                    "surface_pressure": 1017.2,
                }
            }
        ),
        latitude_deg=53.53657,
        longitude_deg=9.97293,
    )
    assert reading.air_temperature_c == 17.4
    assert reading.relative_humidity_percent == 73.0
    assert reading.dew_point_c == 12.5
    assert reading.surface_pressure_bar == pytest.approx(1.0172)
    assert reading.observation_time == "2026-09-18T10:15"


def test_weather_position_refresh_threshold_is_measured_geographically():
    assert _distance_nm((53.5, 9.9), (53.5, 9.9)) == 0.0
    assert _distance_nm((53.5, 9.9), (53.6, 9.9)) > 5.0


@pytest.mark.parametrize(
    "field,value",
    (("relative_humidity_2m", 130.0), ("surface_pressure", 500.0)),
)
def test_open_meteo_rejects_implausible_values(field, value):
    current = {
        "temperature_2m": 17.4,
        "relative_humidity_2m": 73.0,
        "dew_point_2m": 12.5,
        "surface_pressure": 1017.2,
    }
    current[field] = value
    with pytest.raises(ValueError, match="outside plausible bounds"):
        parse_open_meteo_current(
            {"current": current}, latitude_deg=53.5, longitude_deg=9.9
        )
