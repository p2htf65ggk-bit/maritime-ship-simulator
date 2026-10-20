# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import time
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


@dataclass(frozen=True, slots=True)
class LiveWeatherReading:
    latitude_deg: float
    longitude_deg: float
    air_temperature_c: float
    relative_humidity_percent: float
    dew_point_c: float
    surface_pressure_bar: float
    observation_time: str


def open_meteo_url(latitude_deg: float, longitude_deg: float) -> str:
    query = urlencode(
        {
            "latitude": f"{float(latitude_deg):.5f}",
            "longitude": f"{float(longitude_deg):.5f}",
            "current": (
                "temperature_2m,relative_humidity_2m,dew_point_2m,surface_pressure"
            ),
            "timezone": "UTC",
        }
    )
    return f"https://api.open-meteo.com/v1/forecast?{query}"


def parse_open_meteo_current(
    payload: bytes | str | dict,
    *,
    latitude_deg: float,
    longitude_deg: float,
) -> LiveWeatherReading:
    if isinstance(payload, bytes):
        data = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        data = json.loads(payload)
    else:
        data = payload
    if not isinstance(data, dict) or not isinstance(data.get("current"), dict):
        raise ValueError("Open-Meteo response has no current-weather object")
    current = data["current"]

    def number(name: str) -> float:
        value = float(current[name])
        if not math.isfinite(value):
            raise ValueError(f"Open-Meteo {name} is not finite")
        return value

    temperature = number("temperature_2m")
    humidity = number("relative_humidity_2m")
    dew_point = number("dew_point_2m")
    pressure_hpa = number("surface_pressure")
    if not -100.0 <= temperature <= 100.0:
        raise ValueError("Open-Meteo temperature is outside plausible bounds")
    if not 0.0 <= humidity <= 100.0:
        raise ValueError("Open-Meteo humidity is outside plausible bounds")
    if not 700.0 <= pressure_hpa <= 1200.0:
        raise ValueError("Open-Meteo pressure is outside plausible bounds")
    return LiveWeatherReading(
        latitude_deg=float(latitude_deg),
        longitude_deg=float(longitude_deg),
        air_temperature_c=temperature,
        relative_humidity_percent=humidity,
        dew_point_c=dew_point,
        surface_pressure_bar=pressure_hpa / 1000.0,
        observation_time=str(current.get("time", "")),
    )


class LiveWeatherClient(QObject):
    """Non-blocking, position-aware Open-Meteo current-weather client."""

    reading_received = Signal(object)
    status_changed = Signal(str)

    REFRESH_INTERVAL_MS = 10 * 60 * 1000
    POSITION_REFRESH_NM = 5.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = False
        self._position: tuple[float, float] | None = None
        self._requested_position: tuple[float, float] | None = None
        self._reply: QNetworkReply | None = None
        self._last_request_s = 0.0
        self.network = QNetworkAccessManager(self)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(self.REFRESH_INTERVAL_MS)
        self.refresh_timer.timeout.connect(self.refresh)

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.enabled == enabled:
            return
        self.enabled = enabled
        if not enabled:
            self.refresh_timer.stop()
            if self._reply is not None:
                self._reply.abort()
            self.status_changed.emit("Manual weather values")
            return
        self.refresh_timer.start()
        self.status_changed.emit("Live weather enabled; waiting for own-ship position...")
        self.refresh(force=True)

    def update_position(self, latitude_deg: float, longitude_deg: float) -> None:
        position = (float(latitude_deg), float(longitude_deg))
        self._position = position
        if not self.enabled:
            return
        if self._requested_position is None or _distance_nm(
            position, self._requested_position
        ) >= self.POSITION_REFRESH_NM:
            self.refresh(force=True)

    def refresh(self, *, force: bool = False) -> None:
        if not self.enabled or self._position is None:
            return
        now = time.monotonic()
        if not force and now - self._last_request_s < self.REFRESH_INTERVAL_MS / 1000.0:
            return
        if self._reply is not None:
            if not force:
                return
            self._reply.abort()
        latitude_deg, longitude_deg = self._position
        self._requested_position = self._position
        self._last_request_s = now
        request = QNetworkRequest(QUrl(open_meteo_url(latitude_deg, longitude_deg)))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "Maritime Ship Simulator V2")
        request.setTransferTimeout(15_000)
        reply = self.network.get(request)
        self._reply = reply
        reply.finished.connect(
            lambda current=reply, position=self._position: self._finished(current, position)
        )
        self.status_changed.emit(
            f"Loading live weather at {latitude_deg:.4f}, {longitude_deg:.4f}..."
        )

    def close(self) -> None:
        self.refresh_timer.stop()
        if self._reply is not None:
            self._reply.abort()
            self._reply = None

    def _finished(
        self,
        reply: QNetworkReply,
        position: tuple[float, float],
    ) -> None:
        if reply is not self._reply:
            reply.deleteLater()
            return
        self._reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise OSError(reply.errorString())
            reading = parse_open_meteo_current(
                bytes(reply.readAll()),
                latitude_deg=position[0],
                longitude_deg=position[1],
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            if self.enabled:
                self.status_changed.emit(
                    f"Live weather unavailable; retaining current values: {error}"
                )
        else:
            if self.enabled:
                self.reading_received.emit(reading)
                stamp = reading.observation_time.replace("T", " ") or "current model time"
                self.status_changed.emit(
                    f"Open-Meteo {stamp} UTC at {reading.latitude_deg:.4f}, "
                    f"{reading.longitude_deg:.4f}"
                )
        finally:
            reply.deleteLater()


def _distance_nm(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 3440.065 * 2.0 * math.asin(min(1.0, math.sqrt(value)))
