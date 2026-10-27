# SPDX-License-Identifier: MPL-2.0
"""Select an allowlisted native target without interpolating input into a shell."""
from __future__ import annotations

import json
import os
from pathlib import Path


TARGETS_PATH = Path(__file__).resolve().parents[1] / ".github/native-build-targets.json"


def build_matrix(target: str = "all") -> dict:
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    selected = [item for item in targets if target == "all" or item["target"] == target]
    if not selected:
        raise ValueError(f"Unknown native build target: {target}")
    return {"include": selected}


def main() -> int:
    matrix = json.dumps(build_matrix(os.environ.get("BUILD_TARGET") or "all"))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"matrix={matrix}\n")
    print(matrix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
