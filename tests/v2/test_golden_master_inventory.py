# SPDX-License-Identifier: MPL-2.0
import json
from pathlib import Path


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "v1_track_golden_master.json"


def test_v1_track_golden_master_covers_models_speeds_and_scenarios():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["case_count"] == 60
    cases = payload["cases"]
    assert len(cases) == 60
    models = {case["model"] for case in cases.values()}
    assert models == {
        "Crew Transfer Vessel",
        "Container Feeder",
        "15k TEU Container",
        "24k TEU Container",
        "VLCC",
    }
    assert {case["speed"] for case in cases.values()} == {"half", "full"}
    assert all(case["samples"] for case in cases.values())
    assert all("max_abs_xte_nm" in case["metrics"] for case in cases.values())
