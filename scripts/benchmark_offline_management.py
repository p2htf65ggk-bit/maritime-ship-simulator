#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Measure offline maintenance on a private copy, never on the operator's maps."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maritime_simulator.visual.offline import MBTiles
from maritime_simulator.visual.offline_areas import AREAS_FILE, export_areas, manage_areas, saved_areas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--export", action="store_true", help="Also export all areas to a separate temporary file")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        parser.error("Choose an existing offline-areas.mbtiles package")
    signature = (source.stat().st_size, source.stat().st_mtime_ns)
    with tempfile.TemporaryDirectory(prefix="mss-map-maintenance-") as directory:
        directory = Path(directory)
        target = directory / AREAS_FILE
        # APFS clone avoids writing gigabytes just to prepare this read-only-source probe.
        clone = subprocess.run(["cp", "-c", str(source), str(target)], capture_output=True) if sys.platform == "darwin" else None
        if clone is None or clone.returncode:
            shutil.copyfile(source, target)
        rows = saved_areas(directory)
        if len(rows) < 2:
            parser.error("The benchmark needs at least two saved areas")
        result = {"package_mib": round(target.stat().st_size / 1024**2, 1), "areas": len(rows)}
        for water, label in ((False, "map_open_s"), (True, "water_open_s")):
            started = time.perf_counter()
            MBTiles(target, water_mask=water)
            result[label] = round(time.perf_counter() - started, 4)
        if args.export:
            started = time.perf_counter()
            updates, first_update = [], None
            def progress(_done, _total, text):
                nonlocal first_update
                elapsed = time.perf_counter() - started
                first_update = elapsed if first_update is None else first_update
                updates.append(elapsed)
            exported = directory / "portable-export.mbtiles"
            export_areas(directory, rows, exported, progress=progress)
            result.update(export_s=round(time.perf_counter() - started, 3),
                          export_mib=round(exported.stat().st_size / 1024**2, 1),
                          export_first_update_s=round(first_update, 4), export_progress_updates=len(updates),
                          export_max_update_gap_s=round(max((right-left for left,right in zip(updates, updates[1:])), default=0), 3))
            package = MBTiles(exported)
            assert json.loads(package.metadata["maritime_areas"]) == rows
            with closing(sqlite3.connect(exported)) as db:
                result["export_integrity"] = db.execute("PRAGMA integrity_check").fetchone()[0]
                assert result["export_integrity"] == "ok"
        renamed = [dict(row) for row in rows]
        renamed[0]["name"] = "Maintenance benchmark"
        started = time.perf_counter()
        manage_areas(directory, renamed, rows)
        result["rename_s"] = round(time.perf_counter() - started, 4)
        removed = min(range(len(renamed)), key=lambda index: renamed[index].get("tile_count", 0))
        result["deleted_area_tiles"] = renamed[removed].get("tile_count")
        kept = [row for index, row in enumerate(renamed) if index != removed]
        started = time.perf_counter()
        manage_areas(directory, kept, renamed)
        result["delete_s"] = round(time.perf_counter() - started, 4)
        assert saved_areas(directory) == kept
        with closing(sqlite3.connect(target)) as db:
            result["integrity"] = db.execute("PRAGMA integrity_check").fetchone()[0]
            result["reusable_mib"] = round(db.execute("PRAGMA freelist_count").fetchone()[0]
                                           * db.execute("PRAGMA page_size").fetchone()[0] / 1024**2, 1)
        assert result["integrity"] == "ok"
        result["source_untouched"] = (source.stat().st_size, source.stat().st_mtime_ns) == signature
        assert result["source_untouched"]
        if args.report:
            args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
