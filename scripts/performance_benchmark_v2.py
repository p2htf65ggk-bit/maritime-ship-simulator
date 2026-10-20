#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import tracemalloc


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["SHIP_SIMULATOR_HEADLESS_MAP"] = "1"

from maritime_simulator.application.metrics import PerformanceMonitor
from maritime_simulator import __version__
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import SetControlMode, SetRoute, SetSpeedOrder, SetTimeScale
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.protocols.iec61162 import REGULAR_PROTOCOLS
from maritime_simulator.protocols.scheduler import ProtocolSchedule
from maritime_simulator.simulation.route_io import load_rtz
from maritime_simulator.transport.config import UdpEndpoint
from maritime_simulator.transport.manager import ProtocolOutputService, RegisteredOutput


class MemoryOutputManager:
    def __init__(self):
        self.sent = 0

    def send(self, _endpoint, _sentence):
        self.sent += 1
        return True

    def close(self):
        return None


def _result(case: str, monitor: PerformanceMonitor, elapsed_s: float, **extra) -> dict:
    snapshot = monitor.snapshot()
    return {
        "case": case,
        "wall_elapsed_s": round(elapsed_s, 6),
        "metrics": snapshot,
        **extra,
    }


def benchmark_simulation(simulated_minutes: float, time_scale: float) -> dict:
    monitor = PerformanceMonitor(enabled=True)
    service = SimulatorService(performance_monitor=monitor)
    service.submit(SetControlMode(ControlMode.SHIP))
    service.submit(SetSpeedOrder(21.0))
    service.submit(SetTimeScale(time_scale))
    started = time.perf_counter()
    simulated_seconds = max(1.0, simulated_minutes * 60.0)
    while service.snapshot().simulation_time_s < simulated_seconds:
        service.advance_wall_time(0.1 / time_scale)
    elapsed = time.perf_counter() - started
    snapshot = service.snapshot()
    service.close()
    return _result(
        f"ship_{time_scale:g}x",
        monitor,
        elapsed,
        simulated_time_s=snapshot.simulation_time_s,
        final_sog_kn=snapshot.sog_kn,
    )


def benchmark_track(simulated_minutes: float) -> dict:
    monitor = PerformanceMonitor(enabled=True)
    service = SimulatorService(performance_monitor=monitor)
    route = load_rtz(ROOT / "routes" / "default_route.rtz")
    service.submit(SetRoute(route))
    service.submit(SetControlMode(ControlMode.SHIP))
    service.submit(SetSpeedOrder(21.0))
    for _ in range(900):
        service.advance_wall_time(0.1)
    service.submit(SetControlMode(ControlMode.TRACK))
    monitor.reset()
    started = time.perf_counter()
    target = service.snapshot().simulation_time_s + max(1.0, simulated_minutes * 60.0)
    while service.snapshot().simulation_time_s < target and not service.snapshot().route_completed:
        service.advance_wall_time(0.1)
    elapsed = time.perf_counter() - started
    snapshot = service.snapshot()
    service.close()
    return _result(
        "track_default_route",
        monitor,
        elapsed,
        simulated_time_s=snapshot.simulation_time_s,
        final_progress_nm=snapshot.route_progress_nm,
        final_xte_nm=snapshot.xte_nm,
        next_waypoint_index=snapshot.next_waypoint_index,
    )


def _targets(count: int) -> tuple[dict, ...]:
    return tuple(
        {
            "mmsi": 211_000_000 + index,
            "lat": 54.0 + index * 0.00001,
            "lon": 10.0 + index * 0.00001,
            "speed": 12.0,
            "course": float(index % 360),
            "heading": index % 360,
        }
        for index in range(count)
    )


def benchmark_protocols(ais_count: int, duration_s: float) -> dict:
    monitor = PerformanceMonitor(enabled=True)
    memory_output = MemoryOutputManager()
    service = SimulatorService()
    output = ProtocolOutputService(
        output_manager=memory_output,
        performance_monitor=monitor,
        ais_targets=lambda: _targets(ais_count),
        now_factory=lambda: datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    endpoint = UdpEndpoint("127.0.0.1", 10110)
    for index, protocol in enumerate(REGULAR_PROTOCOLS):
        rate = 5.0 if protocol in {"GGA", "RMC", "HDT", "ROT", "VHW", "VBW"} else 1.0
        output.register(
            RegisteredOutput(ProtocolSchedule(protocol, protocol, rate, "memory"), endpoint),
            now_s=0.0,
        )
    started = time.perf_counter()
    now_s = 0.0
    while now_s <= duration_s:
        output.pump(service.snapshot(), now_s=now_s)
        now_s += 0.01
    elapsed = time.perf_counter() - started
    output.close()
    return _result(
        f"protocols_ais_{ais_count}",
        monitor,
        elapsed,
        scheduled_duration_s=duration_s,
        sentences_sent=memory_output.sent,
    )


def benchmark_memory(simulated_minutes: float) -> dict:
    tracemalloc.start()
    service = SimulatorService()
    service.submit(SetControlMode(ControlMode.SHIP))
    service.submit(SetSpeedOrder(21.0))
    service.advance_wall_time(1.0)
    before, _ = tracemalloc.get_traced_memory()
    started = time.perf_counter()
    for _ in range(max(1, round(simulated_minutes * 600.0))):
        service.advance_wall_time(0.1)
    elapsed = time.perf_counter() - started
    after, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    service.close()
    return {
        "case": "memory_long_run",
        "wall_elapsed_s": round(elapsed, 6),
        "simulated_minutes": simulated_minutes,
        "retained_growth_kib": round((after - before) / 1024.0, 3),
        "peak_kib": round(peak / 1024.0, 3),
    }


def benchmark_ui(duration_s: float) -> dict:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    from maritime_simulator.ui.main_window import V2MainWindow
    from ui.style import apply_global_ui_style

    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    with TemporaryDirectory(prefix="maritime-v2-performance-") as directory:
        monitor = PerformanceMonitor(enabled=True)
        service = SimulatorService(performance_monitor=monitor)
        window = None
        try:
            window = V2MainWindow(service, config_dir=Path(directory))
            window.timer.stop()
            service.submit(SetControlMode(ControlMode.SHIP))
            service.submit(SetSpeedOrder(21.0))
            monitor.reset()
            iterations = 0
            started = time.perf_counter()
            while time.perf_counter() - started < max(0.05, duration_s):
                service.advance_wall_time(0.05)
                window._refresh()
                app.processEvents()
                iterations += 1
            elapsed = time.perf_counter() - started
            return _result("ui_headless", monitor, elapsed, refreshes=iterations)
        finally:
            if window is not None:
                # closeEvent stops output/input workers, local HTTP and map services.
                window.close()
                window.deleteLater()
            service.close()
            app.processEvents()
            app.sendPostedEvents(None, QEvent.DeferredDelete)


def assert_release_gates(results: list[dict]) -> None:
    failures = []
    for result in results:
        metrics = result.get("metrics", {})
        simulation = metrics.get("durations", {}).get("simulation_tick")
        if simulation and simulation["max_ms"] > 100.0:
            failures.append(f"{result['case']}: simulation tick max {simulation['max_ms']:.1f} ms")
        generation = metrics.get("durations", {}).get("protocol_generation")
        if generation and generation["max_ms"] > 100.0:
            failures.append(f"{result['case']}: protocol generation max {generation['max_ms']:.1f} ms")
        if result["case"] == "memory_long_run" and result["retained_growth_kib"] > 4096.0:
            failures.append(f"memory retained growth {result['retained_growth_kib']:.1f} KiB")
    if failures:
        raise SystemExit("V2 performance gate failed:\n" + "\n".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description="Maritime Ship Simulator V2 deterministic performance benchmark")
    parser.add_argument("--duration", type=float, default=1.0, help="Wall-clock schedule duration for protocol cases")
    parser.add_argument("--sim-minutes", type=float, default=30.0, help="Simulated duration for endurance cases")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--assert-gates", action="store_true")
    args = parser.parse_args()

    cases = [
        ("Ship 1x", benchmark_simulation, (args.sim_minutes, 1.0)),
        ("Ship 30x", benchmark_simulation, (args.sim_minutes, 30.0)),
        ("Track", benchmark_track, (min(args.sim_minutes, 10.0),)),
        *((f"Protocols / {count} AIS targets", benchmark_protocols, (count, args.duration))
          for count in (10, 50, 100, 250, 500)),
        ("Headless UI", benchmark_ui, (args.duration,)),
        ("Retained memory", benchmark_memory, (args.sim_minutes,)),
    ]
    payload = {
        "version": __version__,
        "platform": sys.platform,
        "python": sys.version,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "results": [],
        "status": "running",
    }
    for label, benchmark, parameters in cases:
        print(f"[v2-performance] {label}", flush=True)
        payload["results"].append(benchmark(*parameters))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    failure = None
    if args.assert_gates:
        try:
            assert_release_gates(payload["results"])
        except SystemExit as error:
            failure = str(error)
    payload["status"] = "failed" if failure else ("passed" if args.assert_gates else "completed")
    if failure:
        payload["failure"] = failure
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if failure:
        raise SystemExit(failure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
