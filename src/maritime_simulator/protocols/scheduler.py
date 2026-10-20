# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import heapq
import itertools
import time


@dataclass(frozen=True, slots=True)
class ProtocolSchedule:
    schedule_id: str
    protocol: str
    rate_hz: float
    output_id: str
    enabled: bool = True

    @property
    def interval_s(self) -> float:
        if self.rate_hz <= 0.0:
            raise ValueError("rate_hz must be positive")
        return 1.0 / self.rate_hz


class SentenceScheduler:
    """Independent wall-clock deadlines; no sensor grouping or simulation scaling."""

    def __init__(self, monotonic=time.monotonic):
        self._monotonic = monotonic
        self._schedules: dict[str, ProtocolSchedule] = {}
        self._generation: dict[str, int] = {}
        self._deadlines: list[tuple[float, int, str, int]] = []
        self._sequence = itertools.count()

    def configure(self, schedule: ProtocolSchedule, *, now_s: float | None = None, immediate: bool = True) -> None:
        if schedule.rate_hz <= 0.0:
            raise ValueError("rate_hz must be positive")
        now = self._monotonic() if now_s is None else float(now_s)
        generation = self._generation.get(schedule.schedule_id, 0) + 1
        self._generation[schedule.schedule_id] = generation
        self._schedules[schedule.schedule_id] = schedule
        deadline = now if immediate else now + schedule.interval_s
        heapq.heappush(self._deadlines, (deadline, next(self._sequence), schedule.schedule_id, generation))

    def remove(self, schedule_id: str) -> None:
        self._schedules.pop(schedule_id, None)
        self._generation[schedule_id] = self._generation.get(schedule_id, 0) + 1

    def due(self, now_s: float | None = None, *, maximum: int = 1000) -> tuple[ProtocolSchedule, ...]:
        now = self._monotonic() if now_s is None else float(now_s)
        result: list[ProtocolSchedule] = []
        epsilon = 1e-9
        while self._deadlines and self._deadlines[0][0] <= now + epsilon and len(result) < maximum:
            deadline, _, schedule_id, generation = heapq.heappop(self._deadlines)
            schedule = self._schedules.get(schedule_id)
            if schedule is None or self._generation.get(schedule_id) != generation:
                continue
            if schedule.enabled:
                result.append(schedule)
            interval = schedule.interval_s
            missed = max(1, int((now - deadline) // interval) + 1)
            next_deadline = deadline + missed * interval
            heapq.heappush(self._deadlines, (next_deadline, next(self._sequence), schedule_id, generation))
        return tuple(result)

    def next_delay_s(self, now_s: float | None = None, default: float = 1.0) -> float:
        now = self._monotonic() if now_s is None else float(now_s)
        self._discard_stale_head()
        if not self._deadlines:
            return default
        return max(0.0, self._deadlines[0][0] - now)

    def _discard_stale_head(self) -> None:
        while self._deadlines:
            _, _, schedule_id, generation = self._deadlines[0]
            if schedule_id in self._schedules and self._generation.get(schedule_id) == generation:
                return
            heapq.heappop(self._deadlines)
