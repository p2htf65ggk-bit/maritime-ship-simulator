#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def run(label: str, command: list[str], *, environment: dict | None = None, log_path: Path | None = None) -> None:
    print(f"[v2-prebuild] {label}", flush=True)
    environment = dict(os.environ if environment is None else environment)
    environment.setdefault("PYTHONFAULTHANDLER", "1")
    if log_path is None:
        result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    else:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[v2-prebuild] Full log: {log_path}", flush=True)
        with log_path.open("w", encoding="utf-8") as log:
            with subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, errors="replace") as process:
                for line in process.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                result = subprocess.CompletedProcess(command, process.wait())
    if result.returncode:
        if sys.platform == "win32" and (result.returncode < 0 or result.returncode >= 0x80000000):
            # Windows process statuses are not negative POSIX signal numbers.
            reason = f"Windows process status {result.returncode} (0x{result.returncode & 0xFFFFFFFF:08X})"
        elif result.returncode < 0:
            try:
                reason = signal.Signals(-result.returncode).name
            except ValueError:
                reason = f"process status {result.returncode}"
            if reason == "SIGKILL":
                reason += "; check the system log for an out-of-memory kill or external termination"
        else:
            reason = f"exit code {result.returncode}"
        details = f"\nFull diagnostic log: {log_path}" if log_path is not None else ""
        raise SystemExit(f"ERROR: {label} failed ({reason}).{details}")


def resource_check() -> None:
    import pyogrio
    if "S57" not in pyogrio.list_drivers() or not all(
        (Path(pyogrio.get_gdal_data_path()) / filename).is_file()
        for filename in ("s57objectclasses.csv", "s57attributes.csv")
    ):
        raise SystemExit("S57 driver/data missing. Install requirements-v2-release.txt in the build environment.")
    required = (
        ROOT / "LICENSE",
        ROOT / "NOTICE",
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / "FREEWARE_LICENSE.txt",
        ROOT / "assets" / "app_icon.png",
        ROOT / "assets" / "geodata" / "osm-simplified-land-polygons-3857.zip",
        ROOT / "assets" / "geodata" / "README.txt",
        ROOT / "ui" / "web" / "map.html",
        ROOT / "ui" / "web" / "map_select.js",
        ROOT / "ui" / "web" / "s57_portrayal.js",
        ROOT / "ui" / "web" / "forward_view" / "cesium.html",
        ROOT / "ui" / "web" / "forward_view" / "cesium_simple_view.css",
        ROOT / "ui" / "web" / "forward_view" / "cesium_simple_view.js",
        ROOT / "ui" / "web" / "forward_view" / "google_maps_3d.html",
        ROOT / "ui" / "web" / "forward_view" / "google_maps_3d.css",
        ROOT / "ui" / "web" / "forward_view" / "google_maps_3d.js",
        ROOT / "routes" / "default_route.rtz",
        SRC / "maritime_simulator" / "resources" / "factory_vessel_profiles.json",
        ROOT / "tests" / "fixtures" / "v1_track_golden_master.json",
        ROOT / "tests" / "fixtures" / "s57" / "US4NJ1DG.zip",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Missing V2 migration/build resources:\n" + "\n".join(missing))


def ui_smoke() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(SRC), str(ROOT), environment.get("PYTHONPATH", "")))
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["SHIP_SIMULATOR_HEADLESS_MAP"] = "1"
    code = (
        "from pathlib import Path\n"
        "from tempfile import TemporaryDirectory\n"
        "from PySide6.QtCore import QEvent\n"
        "from PySide6.QtWidgets import QApplication\n"
        "import run_v2\n"
        "from ui.style import apply_global_ui_style\n"
        "from maritime_simulator.ui import V2MainWindow\n"
        "app=QApplication.instance() or QApplication([])\n"
        "apply_global_ui_style(app)\n"
        "with TemporaryDirectory(prefix='maritime-v2-smoke-') as directory:\n"
        "    window=V2MainWindow(config_dir=Path(directory))\n"
        "    try:\n"
        "        window.timer.stop()\n"
        "        window.service.advance_wall_time(0.1)\n"
        "        window._refresh()\n"
        "        assert window.map is not None and window.route_table.rowCount() > 1\n"
        "    finally:\n"
        "        window.close()\n"
        "        window.deleteLater(); app.processEvents()\n"
        "        app.sendPostedEvents(None, QEvent.DeferredDelete)\n"
    )
    run("headless UI smoke", [sys.executable, "-c", code], environment=environment)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Maritime Ship Simulator V2 migration/build gates")
    parser.add_argument("--profile", choices=("quick", "full"), default="full")
    args = parser.parse_args()
    resource_check()
    run(
        "release credential audit",
        [sys.executable, "scripts/check_v2_release_credentials.py", "--source-tree"],
    )
    run("compile", [sys.executable, "-m", "compileall", "-q", "src/maritime_simulator", "run_v2.py", "build_v2.py"])
    ui_smoke()
    if args.profile == "full":
        run("V2 tests", [sys.executable, "-m", "pytest", "-q", "tests/v2"])
        run("Track golden master", [sys.executable, "scripts/compare_v2_track_golden_master.py", "--strict"])
        run(
            "performance gate",
            [
                sys.executable,
                "scripts/performance_benchmark_v2.py",
                "--duration",
                "1",
                "--sim-minutes",
                "30",
                "--assert-gates",
                "--output",
                "docs/performance_v2_last_run.json",
            ],
            log_path=ROOT / "artifacts" / "performance-v2-build.log",
        )
    print(f"[v2-prebuild] completed ({args.profile})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
