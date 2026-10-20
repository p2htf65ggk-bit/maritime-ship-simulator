#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Generate deterministic V1 Track Control traces used by the V2 migration."""

from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path
import random
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.simulation import Simulation

logging.getLogger("ui.simulation").setLevel(logging.ERROR)


DEFAULT_OUTPUT = ROOT / "tests" / "fixtures" / "v1_track_golden_master.json"
DT_S = 0.2
DURATION_S = 600.0
SAMPLE_INTERVAL_S = 10.0
BASE_LATITUDE_DEG = 45.0
BASE_LONGITUDE_DEG = -73.0


def waypoint(name: str, north_nm: float, east_nm: float, *, turn_rate: float = 18.0) -> dict[str, str | float]:
    return {
        "name": name,
        "lat": BASE_LATITUDE_DEG + north_nm / 60.0,
        "lon": BASE_LONGITUDE_DEG + east_nm / (60.0 * math.cos(math.radians(BASE_LATITUDE_DEG))),
        "speed": "Manual",
        "turn_rate": f"{turn_rate:.3f}",
        "turn_mode": "fixed_rot",
    }


def route_from_bearings(bearings: tuple[float, ...], leg_nm: float = 0.8) -> list[dict[str, str | float]]:
    north = 0.0
    east = 0.0
    route = [waypoint("P0", north, east)]
    for index, bearing in enumerate(bearings, 1):
        radians = math.radians(bearing)
        north += math.cos(radians) * leg_nm
        east += math.sin(radians) * leg_nm
        route.append(waypoint(f"P{index}", north, east))
    return route


def scenarios() -> dict[str, tuple[list[dict[str, str | float]], float, float, float, float]]:
    straight = route_from_bearings((90.0, 90.0, 90.0), leg_nm=1.0)
    turns = route_from_bearings((90.0, 60.0, 90.0, 120.0, 90.0), leg_nm=0.8)
    dense = route_from_bearings((90.0, 75.0, 90.0, 105.0, 90.0, 75.0, 90.0, 105.0), leg_nm=0.30)
    reverse = list(reversed(turns))
    for index, point in enumerate(reverse):
        point = dict(point)
        point["name"] = f"R{index}"
        reverse[index] = point
    return {
        # route, initial lateral offset NM, wind from/speed, current from/speed
        "straight": (straight, 0.0, 0.0, 0.0, 0.0),
        "turns_plus_minus_30": (turns, 0.0, 0.0, 0.0, 0.0),
        "set_and_drift": (straight, 0.0, 180.0, 30.0, 180.0),
        "xte_recovery": (straight, 0.08, 0.0, 0.0, 0.0),
        "dense_15_degree": (dense, 0.0, 0.0, 0.0, 0.0),
        "reverse_route": (reverse, 0.0, 0.0, 0.0, 0.0),
    }


def run_case(
    model_name: str,
    speed_name: str,
    route: list[dict[str, str | float]],
    lateral_offset_nm: float,
    wind_from_deg: float,
    wind_speed_kn: float,
    current_from_deg: float,
) -> dict:
    sim = Simulation()
    sim.set_vessel_model(model_name)
    sim.set_route_waypoints(route)
    sim.set_track_progress(0.0)
    sim.control_mode = "track"
    speed = sim.vessel_model["max_speed"] * (0.6 if speed_name == "half" else 1.0)
    sim.target_speed = speed
    sim.speed = speed
    sim.stw = speed
    sim.sog = speed
    sim.wind_direction = wind_from_deg
    sim.wind_speed = wind_speed_kn
    sim.current_direction = current_from_deg
    sim.current_speed = 1.5 if wind_speed_kn else 0.0
    sim.xte_noise_enabled = False
    sim.heading = sim.route_course_at(0.0) or 90.0
    sim.cog = sim.heading
    if lateral_offset_nm:
        sim.lat += lateral_offset_nm / 60.0

    samples = []
    max_abs_xte_nm = 0.0
    max_abs_rot = 0.0
    max_abs_rudder_order = 0.0
    sample_every = max(1, int(round(SAMPLE_INTERVAL_S / DT_S)))
    steps = int(round(DURATION_S / DT_S))
    for step in range(steps + 1):
        if step % sample_every == 0:
            segment = getattr(sim, "track_segment_state", {})
            samples.append(
                {
                    "t_s": round(step * DT_S, 3),
                    "lat": round(sim.lat, 8),
                    "lon": round(sim.lon, 8),
                    "heading_deg": round(sim.heading, 4),
                    "cog_deg": round(sim.cog, 4),
                    "rot_deg_min": round(sim.rate_of_turn, 4),
                    "rudder_order_deg": round(sim.rudder_order, 4),
                    "rudder_response_deg": round(sim.rudder, 4),
                    "xte_nm": round(sim.xte, 6),
                    "progress_nm": round(sim.track_progress_nm, 6),
                    "phase": str(segment.get("phase", "idle")),
                    "next_wp_index": int(getattr(sim, "_track_waypoint_index", 0)),
                }
            )
        max_abs_xte_nm = max(max_abs_xte_nm, abs(sim.xte))
        max_abs_rot = max(max_abs_rot, abs(sim.rate_of_turn))
        max_abs_rudder_order = max(max_abs_rudder_order, abs(sim.rudder_order))
        if step < steps:
            sim._integrate_step(DT_S)

    return {
        "model": model_name,
        "speed": speed_name,
        "ordered_speed_kn": speed,
        "duration_s": DURATION_S,
        "dt_s": DT_S,
        "metrics": {
            "max_abs_xte_nm": round(max_abs_xte_nm, 6),
            "max_abs_rot_deg_min": round(max_abs_rot, 4),
            "max_abs_rudder_order_deg": round(max_abs_rudder_order, 4),
            "final_progress_nm": round(sim.track_progress_nm, 6),
            "final_xte_nm": round(sim.xte, 6),
        },
        "samples": samples,
    }


def generate() -> dict:
    random.seed(61162)
    model_names = tuple(Simulation().vessel_models)
    cases = {}
    for scenario_name, values in scenarios().items():
        route, offset, wind_from, wind_speed, current_from = values
        for model_name in model_names:
            for speed_name in ("half", "full"):
                key = f"{scenario_name}|{model_name}|{speed_name}"
                cases[key] = run_case(
                    model_name,
                    speed_name,
                    [dict(item) for item in route],
                    offset,
                    wind_from,
                    wind_speed,
                    current_from,
                )
    return {
        "format_version": 1,
        "source": "Maritime Ship Simulator V1.0.0 ui.simulation.Simulation",
        "case_count": len(cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = generate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {payload['case_count']} V1 golden-master cases to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
