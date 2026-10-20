# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
import os
from threading import RLock
import time

try:
    import resource
except ImportError:  # pragma: no cover - unavailable on Windows
    resource = None


class PerformanceMonitor:
    """Thread-safe, opt-in counters and timings for V2 hot paths."""

    def __init__(self, enabled: bool | None = None):
        self.enabled = (
            os.environ.get("MARITIME_SIM_V2_PERF", "").strip().lower() in {"1", "true", "yes", "on"}
            if enabled is None
            else bool(enabled)
        )
        self._durations = defaultdict(lambda: {"count": 0, "total": 0.0, "max": 0.0})
        self._counters = defaultdict(int)
        self._gauges: dict[str, object] = {}
        self._recent_durations = defaultdict(lambda: deque(maxlen=16))
        self._counter_buckets = defaultdict(lambda: deque(maxlen=16))
        self._started = time.perf_counter()
        self._lock = RLock()

    @contextmanager
    def measure(self, name: str):
        if not self.enabled:
            yield
            return
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record_duration(name, time.perf_counter() - started)

    def record_duration(self, name: str, duration_s: float) -> None:
        if not self.enabled:
            return
        duration = max(0.0, float(duration_s))
        with self._lock:
            metric = self._durations[name]
            metric["count"] += 1
            metric["total"] += duration
            metric["max"] = max(metric["max"], duration)
            bucket = int(time.perf_counter())
            values = self._recent_durations[name]
            if values and values[-1][0] == bucket:
                _, count, total, maximum = values[-1]
                values[-1] = (bucket, count + 1, total + duration, max(maximum, duration))
            else:
                values.append((bucket, 1, duration, duration))

    def increment(self, name: str, amount: int = 1) -> None:
        if self.enabled:
            with self._lock:
                self._counters[name] += int(amount)
                bucket = int(time.perf_counter())
                values = self._counter_buckets[name]
                if values and values[-1][0] == bucket:
                    values[-1] = (bucket, values[-1][1] + int(amount))
                else:
                    values.append((bucket, int(amount)))

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.reset()

    def set_gauge(self, name: str, value: object) -> None:
        if self.enabled:
            with self._lock:
                self._gauges[name] = value

    def reset(self) -> None:
        with self._lock:
            self._durations.clear()
            self._counters.clear()
            self._gauges.clear()
            self._recent_durations.clear()
            self._counter_buckets.clear()
            self._started = time.perf_counter()

    def snapshot(self, *, reset: bool = False) -> dict:
        with self._lock:
            now = time.perf_counter()
            elapsed = max(1e-9, now - self._started)
            recent_elapsed = min(10.0, elapsed)
            cutoff = now - recent_elapsed
            recent_durations = {}
            for name, values in self._recent_durations.items():
                while values and values[0][0] + 1 <= cutoff:
                    values.popleft()
                count = sum(value[1] for value in values)
                total = sum(value[2] for value in values)
                recent_durations[name] = {
                    "count": count,
                    "avg_ms": total / max(1, count) * 1000,
                    "max_ms": max((value[3] for value in values), default=0) * 1000,
                    "rate_hz": count / recent_elapsed,
                    "busy_percent": total / recent_elapsed * 100,
                }
            recent_counters = {
                name: sum(value for bucket, value in values if bucket + 1 > cutoff)
                for name, values in self._counter_buckets.items()
            }
            durations = {
                name: {
                    "count": metric["count"],
                    "avg_ms": metric["total"] / max(1, metric["count"]) * 1000.0,
                    "max_ms": metric["max"] * 1000.0,
                    "rate_hz": metric["count"] / elapsed,
                }
                for name, metric in self._durations.items()
            }
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            gauges["rss_mb"] = self._rss_mb()
            result = {
                "enabled": self.enabled,
                "elapsed_s": elapsed,
                "durations": durations,
                "counters": counters,
                "rates": {name: value / elapsed for name, value in counters.items()},
                "gauges": gauges,
                "recent": {
                    "elapsed_s": recent_elapsed,
                    "durations": recent_durations,
                    "counters": recent_counters,
                    "rates": {name: value / recent_elapsed for name, value in recent_counters.items()},
                },
            }
        if reset:
            self.reset()
        return result

    @staticmethod
    def _rss_mb() -> float | None:
        if resource is None:
            return None
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / (1024.0 * 1024.0) if os.name == "posix" and os.uname().sysname == "Darwin" else value / 1024.0
