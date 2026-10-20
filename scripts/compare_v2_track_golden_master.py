#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Compare V2 Track integration metrics with the frozen V1 golden master."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for item in (ROOT, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from generate_v1_golden_master import DT_S, DURATION_S, scenarios
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import (
    SetControlMode,
    SetEnvironment,
    SetPosition,
    SetRoute,
    SetSpeedOrder,
)
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.domain.profiles import VesselProfileStore
from maritime_simulator.domain.state import RouteWaypoint, SimulationState
from maritime_simulator.simulation.clock import SimulationClock

logging.getLogger("ui.simulation").setLevel(logging.ERROR)


GOLDEN = ROOT / "tests" / "fixtures" / "v1_track_golden_master.json"
DEFAULT_REPORT = ROOT / "docs" / "v2_track_migration_report.json"


def slug(name: str) -> str:
    return "-".join(part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in name).split())


def convert_route(raw_route) -> tuple[RouteWaypoint, ...]:
    result = []
    for item in raw_route:
        def number(key):
            value = item.get(key)
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        result.append(
            RouteWaypoint(
                name=str(item["name"]),
                latitude_deg=float(item["lat"]),
                longitude_deg=float(item["lon"]),
                speed_kn=number("speed"),
                turn_rate_deg_min=number("turn_rate"),
                turn_radius_nm=number("turn_radius"),
                turn_mode=str(item.get("turn_mode", "fixed_rot")),
            )
        )
    return tuple(result)


def run_v2_case(model_name: str, speed_name: str, scenario_name: str) -> dict[str, float]:
    raw_route, offset, wind_from, wind_speed, current_from = scenarios()[scenario_name]
    profile_id = slug(model_name)
    profile = VesselProfileStore().get(profile_id)
    speed = profile.max_speed_kn * (0.6 if speed_name == "half" else 1.0)
    initial_state = SimulationState(vessel_profile_id=profile_id)
    initial_state.motion.stw_kn = speed
    initial_state.motion.sog_kn = speed
    service = SimulatorService(
        clock=SimulationClock(fixed_step_s=DT_S),
        initial_state=initial_state,
    )
    service.submit(SetRoute(convert_route(raw_route)))
    service.submit(SetSpeedOrder(speed))
    service.submit(
        SetEnvironment(
            wind_from_deg=wind_from,
            wind_speed_kn=wind_speed,
            current_from_deg=current_from,
            current_speed_kn=1.5 if wind_speed else 0.0,
        )
    )
    service.submit(SetControlMode(ControlMode.TRACK))
    if offset:
        first = convert_route(raw_route)[0]
        service.submit(SetPosition(first.latitude_deg + offset / 60.0, first.longitude_deg))
    service.advance_wall_time(0.0)

    maximum_xte = 0.0
    maximum_rot = 0.0
    maximum_rudder = 0.0
    steps = int(round(DURATION_S / DT_S))
    for _ in range(steps):
        service.advance_wall_time(DT_S)
        snapshot = service.snapshot()
        maximum_xte = max(maximum_xte, abs(snapshot.xte_nm))
        maximum_rot = max(maximum_rot, abs(snapshot.rate_of_turn_deg_min))
        maximum_rudder = max(maximum_rudder, abs(snapshot.rudder_order_starboard_deg))
    final = service.snapshot()
    service.close()
    return {
        "max_abs_xte_nm": round(maximum_xte, 6),
        "max_abs_rot_deg_min": round(maximum_rot, 4),
        "max_abs_rudder_order_deg": round(maximum_rudder, 4),
        "final_progress_nm": round(final.route_progress_nm, 6),
        "final_xte_nm": round(final.xte_nm, 6),
    }


def compare() -> dict:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    results = {}
    failures = []
    for key, reference in golden.items():
        scenario_name, model_name, speed_name = key.split("|")
        candidate = run_v2_case(model_name, speed_name, scenario_name)
        baseline = reference["metrics"]
        xte_limit = max(0.03, baseline["max_abs_xte_nm"] * 1.5 + 0.01)
        progress_tolerance = max(0.05, baseline["final_progress_nm"] * 0.15)
        passed = (
            candidate["max_abs_xte_nm"] <= xte_limit
            and abs(candidate["final_progress_nm"] - baseline["final_progress_nm"]) <= progress_tolerance
        )
        if not passed:
            failures.append(key)
        results[key] = {
            "passed": passed,
            "v1": baseline,
            "v2": candidate,
            "limits": {
                "max_abs_xte_nm": round(xte_limit, 6),
                "final_progress_tolerance_nm": round(progress_tolerance, 6),
            },
        }
    return {
        "format_version": 1,
        "case_count": len(results),
        "passed_count": len(results) - len(failures),
        "failed_count": len(failures),
        "failures": failures,
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--strict", action="store_true", help="Return non-zero while any migration case is outside tolerance")
    args = parser.parse_args()
    report = compare()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"V2 Track migration: {report['passed_count']}/{report['case_count']} within tolerance; "
        f"report: {args.output}"
    )
    return 1 if args.strict and report["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
