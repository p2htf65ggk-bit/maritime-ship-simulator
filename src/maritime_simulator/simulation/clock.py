# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import math
import time


SUPPORTED_TIME_SCALES = (1.0, 2.0, 5.0, 10.0, 30.0)


@dataclass(frozen=True, slots=True)
class ClockAdvance:
    wall_elapsed_s: float
    simulation_elapsed_s: float
    fixed_steps_s: tuple[float, ...]
    pending_simulation_s: float
    dropped_simulation_s: float = 0.0


class SimulationClock:
    """Translate monotonic wall time into deterministic simulation steps."""

    def __init__(
        self,
        *,
        fixed_step_s: float = 0.1,
        time_scale: float = 1.0,
        maximum_wall_step_s: float = 1.0,
        maximum_steps_per_advance: int = 600,
        monotonic=time.monotonic,
    ):
        if fixed_step_s <= 0.0:
            raise ValueError("fixed_step_s must be positive")
        if maximum_wall_step_s <= 0.0:
            raise ValueError("maximum_wall_step_s must be positive")
        if maximum_steps_per_advance < 1:
            raise ValueError("maximum_steps_per_advance must be positive")
        self.fixed_step_s = float(fixed_step_s)
        self.maximum_wall_step_s = float(maximum_wall_step_s)
        self.maximum_steps_per_advance = int(maximum_steps_per_advance)
        self._monotonic = monotonic
        self._last_wall_s: float | None = None
        self._accumulator_s = 0.0
        self._time_scale = 1.0
        self.set_time_scale(time_scale)

    @property
    def time_scale(self) -> float:
        return self._time_scale

    @property
    def pending_simulation_s(self) -> float:
        return self._accumulator_s

    def set_time_scale(self, scale: float) -> None:
        normalized = float(scale)
        if normalized not in SUPPORTED_TIME_SCALES:
            raise ValueError(f"Unsupported time scale {scale!r}; expected one of {SUPPORTED_TIME_SCALES}")
        self._time_scale = normalized

    def reset(self, wall_time_s: float | None = None) -> None:
        self._last_wall_s = self._monotonic() if wall_time_s is None else float(wall_time_s)
        self._accumulator_s = 0.0

    def tick(self, wall_time_s: float | None = None) -> ClockAdvance:
        now = self._monotonic() if wall_time_s is None else float(wall_time_s)
        if self._last_wall_s is None:
            self._last_wall_s = now
            return ClockAdvance(0.0, 0.0, (), self._accumulator_s)
        elapsed = max(0.0, now - self._last_wall_s)
        self._last_wall_s = now
        return self.advance_wall_time(elapsed)

    def advance_wall_time(self, wall_elapsed_s: float) -> ClockAdvance:
        wall_elapsed = max(0.0, min(float(wall_elapsed_s), self.maximum_wall_step_s))
        simulation_elapsed = wall_elapsed * self._time_scale
        self._accumulator_s += simulation_elapsed

        available_steps = int(math.floor((self._accumulator_s + 1e-12) / self.fixed_step_s))
        step_count = min(available_steps, self.maximum_steps_per_advance)
        if step_count:
            self._accumulator_s -= step_count * self.fixed_step_s
            if abs(self._accumulator_s) < 1e-12:
                self._accumulator_s = 0.0

        dropped = 0.0
        if available_steps > self.maximum_steps_per_advance:
            dropped_steps = available_steps - self.maximum_steps_per_advance
            dropped = dropped_steps * self.fixed_step_s
            self._accumulator_s = max(0.0, self._accumulator_s - dropped)

        return ClockAdvance(
            wall_elapsed_s=wall_elapsed,
            simulation_elapsed_s=simulation_elapsed,
            fixed_steps_s=(self.fixed_step_s,) * step_count,
            pending_simulation_s=self._accumulator_s,
            dropped_simulation_s=dropped,
        )
