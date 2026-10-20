# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import pytest

from maritime_simulator.application.system_resources import (
    SystemMemoryStatus,
    _parse_linux_meminfo,
    _parse_macos_vm_stat,
)
from maritime_simulator.visual.quality import AdaptiveVisualQualityController


def memory(total_gb: float, available_gb: float) -> SystemMemoryStatus:
    return SystemMemoryStatus(total_gb * 1024.0, available_gb * 1024.0, "test")


def test_platform_memory_parsers_report_available_memory_without_extra_dependency():
    linux = _parse_linux_meminfo(
        "MemTotal:        8048576 kB\nMemAvailable:    2475008 kB\nSwapFree:        1000000 kB\n"
    )
    assert linux["MemTotal"] == 8_048_576
    assert linux["MemAvailable"] == 2_475_008

    macos = _parse_macos_vm_stat(
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                               1000.\n"
        "Pages inactive:                           2000.\n"
        "Pages speculative:                         500.\n"
        "Pages occupied by compressor:             9000.\n"
    )
    assert macos == pytest.approx(54.6875)


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (memory(16, 8), "quality"),
        (memory(16, 5), "balanced"),
        (memory(8, 2.5), "low_memory"),
        (memory(8, 1.0), "critical"),
    ),
)
def test_auto_visual_quality_uses_current_available_memory(status, expected):
    controller = AdaptiveVisualQualityController("auto")
    assert controller.update(status, now_s=0.0).name == expected


def test_auto_visual_quality_downgrades_immediately_and_upgrades_with_hysteresis():
    controller = AdaptiveVisualQualityController("auto", upgrade_stable_s=60.0)
    assert controller.update(memory(16, 8), now_s=0.0).name == "quality"
    assert controller.update(memory(16, 2.5), now_s=1.0).name == "low_memory"
    assert controller.update(memory(16, 8), now_s=2.0).name == "low_memory"
    assert controller.update(memory(16, 8), now_s=61.0).name == "low_memory"
    assert controller.update(memory(16, 8), now_s=62.0).name == "balanced"
    assert controller.update(memory(16, 8), now_s=63.0).name == "balanced"
    assert controller.update(memory(16, 8), now_s=123.0).name == "quality"


def test_manual_visual_quality_ignores_memory_pressure():
    controller = AdaptiveVisualQualityController("quality")
    assert controller.update(memory(8, 0.5), now_s=0.0).name == "quality"
    controller.set_mode("low_memory")
    assert controller.update(memory(16, 12), now_s=1.0).name == "low_memory"
