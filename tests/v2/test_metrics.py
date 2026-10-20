# SPDX-License-Identifier: MPL-2.0
import pytest

from maritime_simulator.application.metrics import PerformanceMonitor
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import SetControlMode, SetSpeedOrder
from maritime_simulator.domain.enums import ControlMode


def test_disabled_monitor_keeps_no_hot_path_data():
    monitor = PerformanceMonitor(enabled=False)
    with monitor.measure("ignored"):
        monitor.increment("ignored")
    snapshot = monitor.snapshot()
    assert snapshot["durations"] == {}
    assert snapshot["counters"] == {}


def test_service_reports_simulation_metrics_only_when_enabled():
    monitor = PerformanceMonitor(enabled=True)
    service = SimulatorService(performance_monitor=monitor)
    service.submit(SetControlMode(ControlMode.SHIP))
    service.submit(SetSpeedOrder(10.0))
    service.advance_wall_time(1.0)
    metrics = monitor.snapshot()
    assert metrics["counters"]["simulation_steps"] == 10
    assert metrics["durations"]["simulation_tick"]["count"] == 1
    assert metrics["durations"]["vessel_dynamics"]["count"] == 10
    assert metrics["gauges"]["simulation_time_s"] == pytest.approx(1.0)
