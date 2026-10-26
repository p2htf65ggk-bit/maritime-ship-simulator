# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
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


def open_meteo_url(latitude_deg: float, longitude_deg: float, *, api_key="") -> str:
    parameters = {
        "latitude": f"{float(latitude_deg):.5f}",
        "longitude": f"{float(longitude_deg):.5f}",
        "current": "temperature_2m,relative_humidity_2m,dew_point_2m,surface_pressure",
        "timezone": "UTC",
    }
    if api_key:
        parameters["apikey"] = api_key
    host = "customer-api" if api_key else "api"
    return f"https://{host}.open-meteo.com/v1/forecast?{urlencode(parameters)}"


def current_payload(payload):
    if isinstance(payload, bytes):
        if len(payload) > 131072:
            raise ValueError("Open-Meteo response exceeds its size limit")
        data = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        if len(payload) > 131072:
            raise ValueError("Open-Meteo response exceeds its size limit")
        data = json.loads(payload)
    else:
        data = payload
    if not isinstance(data, dict) or not isinstance(data.get("current"), dict):
        raise ValueError("Open-Meteo response has no current-weather object")
    return data


def validate_api_key(value):
    key = str(value or "").strip()
    if len(key) > 256 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ValueError("The API key must contain only visible ASCII characters (up to 256).")
    return key


def parse_open_meteo_current(
    payload: bytes | str | dict,
    *,
    latitude_deg: float,
    longitude_deg: float,
) -> LiveWeatherReading:
    data = current_payload(payload)
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
    """Bounded, position-aware polling shared by atmospheric, wind and current feeds."""

    reading_received = Signal(object)
    status_changed = Signal(str)
    unavailable = Signal()

    REFRESH_INTERVAL_MS = 10 * 60 * 1000
    POSITION_REFRESH_NM = 5.0
    MIN_REQUEST_INTERVAL_S = 60.0
    MAX_CACHE_READINGS = 32
    LABEL = "Weather"
    _url = staticmethod(open_meteo_url)
    _parse = staticmethod(parse_open_meteo_current)

    def __init__(self, parent=None, *, allow_network=True):
        super().__init__(parent)
        self.enabled = False
        self.allow_network = bool(allow_network)
        self.api_key = ""
        self._closed = False
        self._available = False
        self._position_dirty = True
        self._position: tuple[float, float] | None = None
        self._requested_position: tuple[float, float] | None = None
        self._reply: QNetworkReply | None = None
        self._last_request_s = -math.inf
        self._retry_at = 0.0
        self._failures = 0
        self._cache = OrderedDict()
        self.network = QNetworkAccessManager(self)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(self.REFRESH_INTERVAL_MS)
        self.refresh_timer.timeout.connect(self.refresh)

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.enabled == enabled or self._closed:
            return
        self.enabled = enabled
        if not enabled:
            self.refresh_timer.stop()
            self._cancel_reply()
            self._set_unavailable()
            self.status_changed.emit(f"Manual {self.LABEL.lower()} values")
            return
        self.refresh_timer.start()
        self._position_dirty = True
        self.status_changed.emit(f"Online {self.LABEL.lower()}: waiting for own-ship data")
        self.refresh(force=True)

    def set_api_key(self, key):
        key = validate_api_key(key)
        if key != self.api_key:
            self.api_key = key
            self._cancel_reply()
            self._cache.clear()
            self._set_unavailable()
            self._position_dirty = True
            self._last_request_s = -math.inf
            self._retry_at = 0.0
            self._failures = 0
            self.refresh(force=True)

    def update_position(self, latitude_deg: float, longitude_deg: float) -> None:
        try:
            position = (float(latitude_deg), float(longitude_deg))
        except (TypeError, ValueError, OverflowError):
            return
        if not (all(math.isfinite(v) for v in position) and -90 <= position[0] <= 90 and -180 <= position[1] <= 180):
            return
        self._position = position
        if not self.enabled:
            return
        if self._requested_position is None:
            self.refresh(force=True)
            return
        if not self._position_dirty and (self._requested_position is None or _distance_nm(
            position, self._requested_position
        ) >= self.POSITION_REFRESH_NM):
            self._position_dirty = True
            self._cancel_reply()
            self._set_unavailable()
            self.refresh(force=True)

    def refresh(self, *, force: bool = False) -> None:
        if self._closed or not self.enabled or self._position is None:
            return
        now = time.monotonic()
        if force:
            for position, (stamp, reading) in reversed(self._cache.items()):
                if now-stamp < self.REFRESH_INTERVAL_MS/1000 and _distance_nm(position, self._position) < 1:
                    self._cache.move_to_end(position)
                    self._requested_position = position
                    self._position_dirty = False
                    self._available = True
                    self.reading_received.emit(reading)
                    self.status_changed.emit(f"Open-Meteo {self.LABEL.lower()} (cached)")
                    self.refresh_timer.start(max(1000, int((stamp+self.REFRESH_INTERVAL_MS/1000-now)*1000)))
                    return
        due = max(self._retry_at, self._last_request_s+self.MIN_REQUEST_INTERVAL_S)
        if not (force or self._position_dirty):
            due = max(due, self._last_request_s+self.REFRESH_INTERVAL_MS/1000)
        if now < due:
            self.refresh_timer.start(max(1000, int((due-now)*1000)))
            return
        if self._reply is not None:
            return
        if not self.allow_network:
            return
        latitude_deg, longitude_deg = self._position
        self._requested_position = self._position
        self._last_request_s = now
        self._position_dirty = False
        request = QNetworkRequest(QUrl(self._url(latitude_deg, longitude_deg, api_key=self.api_key)))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "Maritime Ship Simulator V2")
        request.setTransferTimeout(8000)
        reply = self.network.get(request)
        reply.setReadBufferSize(131073)
        self._reply = reply
        payload = bytearray()

        def receive():
            payload.extend(bytes(reply.readAll()))
            if len(payload) > 131072 and reply is self._reply:
                reply.abort()

        reply.readyRead.connect(receive)
        reply.finished.connect(
            lambda current=reply, position=self._position: self._finished(current, position, payload, receive)
        )
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)
        self.status_changed.emit(
            f"Loading online {self.LABEL.lower()}..."
        )

    def close(self) -> None:
        self.enabled = False
        self._closed = True
        self.refresh_timer.stop()
        self._cancel_reply()

    def _cancel_reply(self):
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()

    def _set_unavailable(self):
        if self._available:
            self._available = False
            self.unavailable.emit()

    def _finished(
        self,
        reply: QNetworkReply,
        position: tuple[float, float],
        payload,
        receive,
    ) -> None:
        if reply is not self._reply:
            reply.deleteLater()
            return
        self._reply = None
        try:
            receive()
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise OSError(reply.errorString())
            reading = self._parse(
                bytes(payload),
                latitude_deg=position[0],
                longitude_deg=position[1],
            )
        except (KeyError, OSError, TypeError, ValueError, OverflowError, json.JSONDecodeError):
            if self.enabled:
                self._set_unavailable()
                self._failures = min(5, self._failures+1)
                self._retry_at = time.monotonic()+min(600, 30*2**self._failures)
                self._position_dirty = True
                self.refresh_timer.start(max(1000, int((self._retry_at-time.monotonic())*1000)))
                self.status_changed.emit(
                    f"{self.LABEL} unavailable; last values retained"
                )
        else:
            if self.enabled and _distance_nm(position, self._position) < self.POSITION_REFRESH_NM:
                self._cache[position] = (time.monotonic(), reading)
                self._cache.move_to_end(position)
                while len(self._cache) > self.MAX_CACHE_READINGS:
                    self._cache.popitem(last=False)
                self._available = True
                self._failures = 0
                self._retry_at = 0.0
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
