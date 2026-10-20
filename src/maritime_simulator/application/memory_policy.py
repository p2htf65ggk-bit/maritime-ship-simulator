# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import math
import time

from .system_resources import SystemMemoryStatus


MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class MemoryBudget:
    cache_mb: int
    pressure: bool
    paging_active: bool = False

    def allocations(self) -> dict[str, int]:
        # One pool, partitioned with priority for the active AIS water region.
        water = self.cache_mb // 2 + 8
        raster = self.cache_mb // 4 - 4
        enc = self.cache_mb // 8 - 2
        coverage = self.cache_mb // 16
        return {"water": water * MIB, "raster": raster * MIB, "enc": enc * MIB,
                "coverage": coverage * MIB,
                "coast": (self.cache_mb - water - raster - enc - coverage) * MIB}


class AdaptiveMemoryPolicy:
    def __init__(self, *, recovery_s: float = 60):
        self.budget = MemoryBudget(96, False)
        self.recovery_s = recovery_s
        self._recovery_since = None
        self._recovery_target = None
        self._previous_paging = None
        self._sampled = False

    def update(self, status: SystemMemoryStatus, *, now_s: float | None = None) -> MemoryBudget:
        now = time.monotonic() if now_s is None else now_s
        available = status.available_mb
        paging = (status.paging_out_mb is not None and self._previous_paging is not None
                  and status.paging_out_mb - self._previous_paging >= 1.0)
        self._previous_paging = status.paging_out_mb
        if available is None or not math.isfinite(available):
            self._recovery_since = None
            self._recovery_target = None
            return self.budget
        target = 64 if available < 1536 else 96 if available < 3072 else 128 if available < 6144 else 192
        if paging:
            target = min(target, 96)
        if not self._sampled or target <= self.budget.cache_mb:
            self._sampled = True
            self._recovery_since = None
            self._recovery_target = None
            self.budget = MemoryBudget(target, target <= 96, paging)
        else:
            if self._recovery_since is None or self._recovery_target != target:
                self._recovery_since = now
                self._recovery_target = target
            if now - self._recovery_since >= self.recovery_s:
                self.budget = MemoryBudget(target, target <= 96, paging)
                self._recovery_since = None
                self._recovery_target = None
            else:
                self.budget = MemoryBudget(self.budget.cache_mb, self.budget.pressure, paging)
        return self.budget
