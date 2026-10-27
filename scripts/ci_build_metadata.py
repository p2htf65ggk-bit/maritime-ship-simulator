# SPDX-License-Identifier: MPL-2.0
"""Record a small, non-secret provenance report for a native build attempt."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys


def collect_metadata(target: str) -> dict:
    packages = {}
    for name in ("PyInstaller", "PySide6", "numpy", "pyogrio", "shapely"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "target": target,
        "source_commit": os.environ.get("GITHUB_SHA"),
        "source_ref": os.environ.get("GITHUB_REF"),
        "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "build_result": os.environ.get("BUILD_RESULT", "unknown"),
        "runner_image": os.environ.get("ImageOS"),
        "runner_image_version": os.environ.get("ImageVersion"),
        "system": platform.system(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "build_python": sys.executable,
        "packages": packages,
        "signing": "unsigned (macOS bundles use ad-hoc signatures)",
        "release_published": False,
        "qualification": "Automated build checks only; hardware acceptance remains required.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect_metadata(args.target)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
