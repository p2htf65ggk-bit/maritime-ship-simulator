#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _case(results, name):
    for item in results:
        if item.get("case") == name:
            return item
    return None


def _read_rate(case, key):
    return float(case.get("rates", {}).get(key, 0.0))


def _read_avg_ms(case, key):
    return float(case.get("durations", {}).get(key, {}).get("avg_ms", 0.0))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Windows release performance benchmark hard gate"
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to benchmark JSON payload from scripts/performance_benchmark.py",
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    results = payload.get("results", [])
    failures = []

    required_cases = ("normal", "ui_headless", "route_track")
    for case_name in required_cases:
        if _case(results, case_name) is None:
            failures.append(f"missing benchmark case: {case_name}")

    normal = _case(results, "normal")
    ui_headless = _case(results, "ui_headless")
    route_track = _case(results, "route_track")

    if normal is not None:
        sent_rate = _read_rate(normal, "nmea.sentences_sent")
        if sent_rate < 20.0:
            failures.append(
                f"normal case nmea.sentences_sent too low ({sent_rate:.2f}/s < 20.0/s)"
            )

    if ui_headless is not None:
        ui_update_avg = _read_avg_ms(ui_headless, "simulation.update")
        if ui_update_avg <= 0.0 or ui_update_avg > 80.0:
            failures.append(
                f"ui_headless simulation.update avg out of bounds ({ui_update_avg:.3f} ms, expected 0..80 ms)"
            )

    if route_track is not None:
        tick_rate = _read_rate(route_track, "simulation.ticks")
        if tick_rate < 80.0:
            failures.append(
                f"route_track simulation.ticks too low ({tick_rate:.2f}/s < 80.0/s)"
            )
        route_avg = _read_avg_ms(route_track, "simulation.update")
        if route_avg <= 0.0 or route_avg > 120.0:
            failures.append(
                f"route_track simulation.update avg out of bounds ({route_avg:.3f} ms, expected 0..120 ms)"
            )

    if failures:
        print("Windows performance hard gate: FAILED")
        for item in failures:
            print(f" - {item}")
        return 1

    print("Windows performance hard gate: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
