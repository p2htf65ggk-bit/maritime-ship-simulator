# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import logging
import math
import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from maritime_simulator.visual.bathymetry import (
    ChartDepthReading, S57DepthProvider, gebco_url, grid_corners, grid_depth, parse_gebco,
)

LOG = logging.getLogger(__name__)


class ChartDepthClient(QObject):
    """Coalesced position updates, one network request or local worker at a time."""

    reading_received = Signal(object)
    MAX_CACHE_POINTS = 512
    CACHE_TTL_S = 3600.0
    LOCAL_RESULT_MAX_AGE_S = 5.0
    GEBCO_BATCH_MAX_AGE_S = 15.0

    def __init__(self, s57_store, parent=None, *, allow_network=True):
        super().__init__(parent)
        self.enabled = False
        self.offline = False
        self.allow_network = allow_network
        self._closed = False
        self._position = None
        self._time_scale = 1.0
        self._sog_kn = 0.0
        self._speed_mps = 0.0
        self._reply = None
        self._cache = OrderedDict()
        self._retry_at = 0.0
        self._failures = 0
        self._last_reading = None
        self._future = None
        self._generation = 0
        self._local_key = None
        self._local_depth = None
        self._local_read_at = 0.0
        self._local_start_at = 0.0
        self._local_retry_at = 0.0
        self._local_request_speed = 0.0
        self._local_sample_at = 0.0
        self._local_sample_speed = 0.0
        self._gebco_batch = None
        self._gebco_last_start_at = -math.inf
        self._gebco_last_sample = None
        self._store = s57_store
        self._provider = S57DepthProvider(s57_store)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chart-depth")
        self.network = QNetworkAccessManager(self)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)

    def set_enabled(self, enabled, *, force=False):
        if (self.enabled == bool(enabled) and not force) or self._closed:
            return
        self.enabled = bool(enabled)
        self._reset()
        if self.enabled:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()

    def set_offline(self, offline):
        if self.offline != bool(offline):
            self.offline = bool(offline)
            self._reset()
            self.refresh()

    def _reset(self):
        self._generation += 1
        self._local_key = None
        self._local_depth = None
        self._local_start_at = -math.inf
        self._gebco_batch = None
        self._gebco_last_start_at = -math.inf
        self._gebco_last_sample = None
        self._retry_at = 0.0
        self._local_retry_at = 0.0
        self._failures = 0
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
        self._last_reading = None
        self._emit(None)

    @property
    def sample_interval_s(self):
        if self._time_scale >= 100:
            return 5.0
        if self._time_scale >= 30:
            return 3.0
        if self._time_scale >= 10:
            return 2.0
        return 1.0

    def update_position(self, latitude, longitude, *, time_scale=None, sog_kn=None, reset=False):
        try:
            grid_corners(latitude, longitude)
            scale = self._time_scale if time_scale is None else float(time_scale)
            speed = self._sog_kn if sog_kn is None else float(sog_kn)
            if not (math.isfinite(scale) and math.isfinite(speed) and scale >= 0):
                return
        except (TypeError, ValueError, OverflowError):
            return
        self._position = (float(latitude), float(longitude))
        self._time_scale = min(1000.0, scale)
        self._sog_kn = min(300.0, abs(speed))
        self._speed_mps = self._sog_kn * 0.514444 * self._time_scale
        if reset:
            self._reset()

    def _emit(self, depth, source="manual", *, position=None):
        lat, lon = position or self._position or (0.0, 0.0)
        reading = ChartDepthReading(lat, lon, depth, source if depth is not None else "manual")
        previous = self._last_reading
        same_depth = (previous is not None and (
            depth is None and previous.water_depth_m is None
            or depth is not None and previous.water_depth_m is not None
            and math.isclose(depth, previous.water_depth_m, rel_tol=0, abs_tol=0.001)
        ))
        if previous is None or reading.source != previous.source or not same_depth:
            self._last_reading = reading
            self.reading_received.emit(reading)

    def refresh(self):
        if self._closed or not self.enabled or self._position is None:
            return
        now = time.monotonic()
        local_ready, depth = self._s57_depth(now)
        if local_ready and depth is not None:
            reply, self._reply = self._reply, None
            if reply is not None:
                reply.abort()
            self._gebco_batch = None
            self._emit(depth, "s57", position=self._local_key[0])
            return
        if not local_ready or self.offline:
            self._emit(None)
            return
        self._refresh_gebco(now)

    def _cached_gebco(self, position, now):
        corners = grid_corners(*position)
        missing = [key for key, _ in corners if key not in self._cache or now-self._cache[key][1] > self.CACHE_TTL_S]
        if not missing:
            elevations = {}
            for key, _ in corners:
                elevations[key] = self._cache[key][0]
                self._cache.move_to_end(key)
            return True, grid_depth(corners, elevations), []
        return False, None, missing

    def _refresh_gebco(self, now):
        ready, depth, _missing = self._cached_gebco(self._position, now)
        if ready:
            self._gebco_batch = None
            reply, self._reply = self._reply, None
            if reply is not None:
                reply.abort()
            self._gebco_last_sample = (self._position, now, self._speed_mps)
            self._emit(depth, "gebco")
            return
        if self._gebco_batch is None:
            if now-self._gebco_last_start_at < self.sample_interval_s:
                self._wait_for_gebco(now)
                return
            self._gebco_batch = (self._position, now, self._speed_mps)
            self._gebco_last_start_at = now
        position, started, speed = self._gebco_batch
        if not self._matches_motion(position, started, speed, now, self.GEBCO_BATCH_MAX_AGE_S):
            self._gebco_batch = None
            reply, self._reply = self._reply, None
            if reply is not None:
                reply.abort()
            self._emit(None)
            return
        # Finish one four-corner sample before targeting the latest position.
        # Otherwise accelerated travel can outrun every partially loaded cell.
        ready, depth, missing = self._cached_gebco(position, now)
        if ready:
            self._gebco_batch = None
            self._gebco_last_sample = (position, started, speed)
            self._emit(depth, "gebco", position=position)
            return
        self._wait_for_gebco(now)
        if self._reply is None and self.allow_network and now >= self._retry_at:
            self._request(missing[0])

    def _wait_for_gebco(self, now):
        if (self._last_reading is not None and self._last_reading.source == "gebco"
                and self._gebco_last_sample is not None
                and self._matches_motion(*self._gebco_last_sample, now, self.GEBCO_BATCH_MAX_AGE_S)):
            return
        self._emit(None)

    def _matches_motion(self, position, started, speed, now, max_age):
        age = now-started
        allowance = max(25.0, max(speed, self._speed_mps) * (age+0.25) * 1.5)
        return 0 <= age <= max_age and self._distance(position, self._position) <= allowance

    def _s57_depth(self, now):
        """Resolve local coverage first in both online and offline operation."""
        revision = self._store.cache_revision()
        if self._future is not None and self._future.done():
            generation, position, key, future = self._future_info
            self._future = None
            try:
                depth = future.result()
                if depth is not None and (not math.isfinite(depth) or not 0 <= depth <= 12000):
                    raise ValueError("Invalid S57 depth")
            except Exception:
                depth = None
                if generation == self._generation:
                    LOG.debug("Local chart depth unavailable", exc_info=True)
                    self._local_retry_at = now + 30.0
            if (generation == self._generation
                    and self._matches_motion(position, self._local_start_at, self._local_request_speed,
                                             now, self.LOCAL_RESULT_MAX_AGE_S)
                    and key[1] == revision):
                self._local_key = key
                self._local_read_at = now
                self._local_depth = depth
                self._local_sample_at = self._local_start_at
                self._local_sample_speed = self._local_request_speed
        if not self._store.path.is_file():
            return True, None
        key = (self._position, revision)
        distance = (self._distance(self._local_key[0], self._position)
                    if self._local_key is not None else math.inf)
        moving = self._speed_mps > 0.1
        usable = (self._local_key is not None and revision == self._local_key[1]
                  and now-self._local_read_at < 60
                  and self._matches_motion(self._local_key[0], self._local_sample_at, self._local_sample_speed,
                                           now, 2*self.sample_interval_s+5 if moving else 60))
        fast = moving and self._time_scale >= 10
        distance_limit = max(5.0, self._speed_mps*self.sample_interval_s) if fast else 5.0
        fresh = (usable and distance <= distance_limit and now-self._local_read_at < 30
                 and (not fast or now-self._local_sample_at < self.sample_interval_s))
        if not fresh:
            if self._future is None and now >= self._local_retry_at and now-self._local_start_at >= self.sample_interval_s:
                self._local_start_at = now
                self._local_request_speed = self._speed_mps
                future = self._executor.submit(self._provider.depth_at, *self._position)
                self._future = future
                self._future_info = (self._generation, self._position, key, future)
        if usable:
            return True, self._local_depth
        # A busy/invalid local database must not block the online fallback.
        return now < self._local_retry_at, None

    def _request(self, key):
        request = QNetworkRequest(QUrl(gebco_url(key)))
        request.setRawHeader(b"User-Agent", b"MaritimeShipSimulator/2.3.1 (numeric bathymetry)")
        request.setTransferTimeout(8000)
        reply = self.network.get(request)
        reply.setReadBufferSize(65537)
        self._reply = reply
        payload = bytearray()

        def receive():
            payload.extend(bytes(reply.readAll()))
            if len(payload) > 65536:
                reply.abort()

        def finished():
            receive()
            current = reply is self._reply
            if current:
                self._reply = None
                try:
                    if reply.error() != QNetworkReply.NoError:
                        raise ValueError("GEBCO request failed")
                    value = parse_gebco(bytes(payload), key)
                    self._cache[key] = (value, time.monotonic())
                    self._cache.move_to_end(key)
                    while len(self._cache) > self.MAX_CACHE_POINTS:
                        self._cache.popitem(last=False)
                    self._failures = 0
                except (KeyError, TypeError, ValueError, OverflowError):
                    LOG.debug("GEBCO depth unavailable", exc_info=True)
                    # Transport/XML errors use the same capped retry pause; no request storm.
                    self._failures = min(5, self._failures + 1)
                    self._retry_at = time.monotonic() + min(300, 15 * 2**self._failures)
                    self._emit(None)
            reply.deleteLater()
            if current:
                self.refresh()

        reply.readyRead.connect(receive)
        reply.finished.connect(finished)

    @staticmethod
    def _distance(a, b):
        lon = (a[1]-b[1]+180) % 360-180
        return 111320 * math.hypot(a[0]-b[0], lon*math.cos(math.radians(b[0])))

    def close(self):
        self.enabled = False
        self.timer.stop()
        self._closed = True
        self._reset()
        self._executor.shutdown(wait=False, cancel_futures=True)
