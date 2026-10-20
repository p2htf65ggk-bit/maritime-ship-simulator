# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import asdict, dataclass
import time

from maritime_simulator.application.system_resources import SystemMemoryStatus


@dataclass(frozen=True, slots=True)
class VisualQualityProfile:
    name: str
    display_name: str
    tile_cache_mb: int
    tile_cache_overflow_mb: int
    render_pixel_ratio: float
    target_fps: int
    forward_screen_space_error: int
    orbit_screen_space_error: int
    top_screen_space_error: int
    interaction_error_addition: int

    def payload(self, mode: str, memory: SystemMemoryStatus) -> dict:
        return {
            **asdict(self),
            "mode": mode,
            "system_total_memory_mb": memory.total_mb,
            "system_available_memory_mb": memory.available_mb,
            "memory_source": memory.source,
        }


VISUAL_QUALITY_PROFILES = {
    "critical": VisualQualityProfile("critical", "Critical memory", 64, 24, 0.65, 20, 32, 34, 36, 16),
    "low_memory": VisualQualityProfile("low_memory", "Low memory", 128, 48, 0.80, 24, 24, 26, 28, 12),
    "balanced": VisualQualityProfile("balanced", "Balanced", 256, 96, 1.00, 28, 18, 20, 22, 10),
    "quality": VisualQualityProfile("quality", "Quality", 512, 192, 1.25, 30, 14, 16, 18, 8),
}
VISUAL_QUALITY_MODES = ("auto", "quality", "balanced", "low_memory")
_QUALITY_ORDER = ("critical", "low_memory", "balanced", "quality")


def normalize_visual_quality_mode(value: object) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in VISUAL_QUALITY_MODES else "auto"


class AdaptiveVisualQualityController:
    """Select a stable visual profile from current host memory pressure."""

    def __init__(self, mode: str = "auto", *, upgrade_stable_s: float = 60.0):
        self.mode = normalize_visual_quality_mode(mode)
        self.upgrade_stable_s = max(0.0, float(upgrade_stable_s))
        self.active_name: str | None = None
        self._upgrade_candidate: str | None = None
        self._upgrade_candidate_since = 0.0

    def set_mode(self, mode: str) -> None:
        self.mode = normalize_visual_quality_mode(mode)
        self.active_name = None
        self._upgrade_candidate = None

    def update(
        self,
        memory: SystemMemoryStatus,
        *,
        now_s: float | None = None,
        force: bool = False,
    ) -> VisualQualityProfile:
        now = time.monotonic() if now_s is None else float(now_s)
        desired = self.mode if self.mode != "auto" else self._automatic_profile(memory)
        if self.active_name is None or force or self.mode != "auto":
            self.active_name = desired
            self._upgrade_candidate = None
            return VISUAL_QUALITY_PROFILES[self.active_name]

        active_index = _QUALITY_ORDER.index(self.active_name)
        desired_index = _QUALITY_ORDER.index(desired)
        if desired_index < active_index:
            self.active_name = desired
            self._upgrade_candidate = None
        elif desired_index > active_index:
            if self._upgrade_candidate != desired:
                self._upgrade_candidate = desired
                self._upgrade_candidate_since = now
            elif now - self._upgrade_candidate_since >= self.upgrade_stable_s:
                self.active_name = _QUALITY_ORDER[active_index + 1]
                self._upgrade_candidate = None
        else:
            self._upgrade_candidate = None
        return VISUAL_QUALITY_PROFILES[self.active_name]

    @staticmethod
    def _automatic_profile(memory: SystemMemoryStatus) -> str:
        available = memory.available_mb
        total = memory.total_mb
        if available is not None:
            if available < 1536.0:
                return "critical"
            if available < 3072.0:
                return "low_memory"
            if available < 6144.0:
                return "balanced"
            if total is not None and total <= 9216.0:
                return "balanced"
            return "quality"
        if total is not None and total <= 9216.0:
            return "balanced"
        if total is not None and total <= 13312.0:
            return "balanced"
        return "quality"
