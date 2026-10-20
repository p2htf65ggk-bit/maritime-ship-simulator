#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Offline runtime audit: read operator packages; write only private temporary clones."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from maritime_simulator.visual.offline import MBTiles, OfflineTileServer
from maritime_simulator.visual.s57 import S57Store
from maritime_simulator.visual.s57_water import render_water_tile
from maritime_simulator.visual.water_mask import OSMNavigableWaterMask, _tile_coordinate


def elapsed(call):
    start = time.perf_counter()
    value = call()
    return time.perf_counter() - start, value


def timings(values):
    return dict(median_ms=round(statistics.median(values) * 1000, 3),
                maximum_ms=round(max(values) * 1000, 3), samples=len(values))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maps", type=Path, required=True)
    parser.add_argument("--enc", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ui", action="store_true", help="Also measure read-only S57 table operations")
    args = parser.parse_args()
    sources = [args.maps.resolve(), (args.enc / "charts.sqlite3").resolve()]
    signatures = [(p.stat().st_size, p.stat().st_mtime_ns) for p in sources]
    result = {}
    with tempfile.TemporaryDirectory(prefix="mss-offline-runtime-") as work:
        work = Path(work)
        mask = OSMNavigableWaterMask(ROOT / "assets/geodata/osm-simplified-land-polygons-3857.zip",
                                    work / "water", allow_network=False)
        store = S57Store(args.enc)
        server = OfflineTileServer(mask, store)
        try:
            package = MBTiles(args.maps)
            result["map_mib"] = round(args.maps.stat().st_size / 1024**2, 1)
            server.select(package)
            with closing(package.connect()) as db:
                coordinates = list(db.execute("SELECT zoom_level,tile_column,tile_row FROM tiles "
                                              "WHERE zoom_level=? LIMIT 64", (package.max_zoom,)))
            coordinates = [(z,x,2**z-1-y if package.scheme=="tms" else y) for z,x,y in coordinates]
            cold, hot = [], []
            for z,x,y in coordinates:
                duration, data = elapsed(lambda: server.tile(z,x,y))
                assert data
                cold.append(duration)
            for _ in range(3):
                for z,x,y in coordinates:
                    duration, data = elapsed(lambda: server.tile(z,x,y))
                    assert data
                    hot.append(duration)
            result["raster_uncached"] = timings(cold)
            result["raster_cached"] = timings(hot)
            result["tile_cache"] = dict(entries=len(server._tiles), bytes=server._cache_bytes)

            # Concurrent slow tile work must not stall switching map source on Qt.
            server.select(None)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: elapsed(lambda: server.tile(0,0,0)))
                time.sleep(.03)
                duration, _ = elapsed(lambda: server.select(package))
                result["source_switch_during_world_render_ms"] = round(duration * 1000, 3)
                result["world_tile_s"] = round(future.result()[0], 3)
            assert server.package is package

            scenes = [("ny-harbour",(-74.085,40.67,-74.015,40.72),15),
                      ("ny-detail",(-74.06,40.69,-74.04,40.71),17),
                      ("washington",(-124.5,48.3,-124.2,48.45),12)]
            result["s57_cells"] = len(store.records())
            result["s57_queries"] = []
            for name,bounds,zoom in scenes:
                durations = []
                for _ in range(4):
                    duration, data = elapsed(lambda: store.features(bounds,zoom))
                    durations.append(duration)
                result["s57_queries"].append(dict(name=name, **timings(durations),
                    features=len(data["features"]), limited=data["limited"]))
            result["coverage_cache"] = dict(entries=len(store._coverage_cache),
                bytes=sum(len(v) for v in store._coverage_cache.values() if v is not None))
            water_tiles = []
            for latitude, longitude in ((40.69,-74.05),(40.70,-74.045),(48.38,-124.35)):
                key, _ = _tile_coordinate(latitude, longitude, 15)
                duration, image = elapsed(lambda: render_water_tile(store, *key))
                assert image is not None
                known = sum(image.pixelColor(x,y).alpha() > 127 for y in range(0,256,4) for x in range(0,256,4))
                water_tiles.append(dict(latitude=latitude,longitude=longitude,ms=round(duration * 1000,3),known_samples=known))
            result["s57_water_tiles"] = water_tiles
            cached = []
            for _ in range(20):
                for _,bounds,zoom in scenes:
                    duration, _ = elapsed(lambda: server.chart_features(bounds,zoom,None))
                    cached.append(duration)
            result["s57_server_repeated"] = timings(cached)
            result["chart_cache"] = dict(entries=len(server._enc_cache),bytes=sum(map(len,server._enc_cache.values())))

            private = work / "enc"
            private.mkdir()
            destination = private / "charts.sqlite3"
            clone = subprocess.run(["cp","-c",str(store.path),str(destination)],capture_output=True) if sys.platform=="darwin" else None
            if clone is None or clone.returncode:
                shutil.copyfile(store.path,destination)
            copied = S57Store(private)
            codes = copied.enabled_cells()
            duration, _ = elapsed(lambda: [copied.set_enabled(code,True) for code in codes[:64]])
            result["s57_individual_enable_64_ms"] = round(duration * 1000,3)
            if hasattr(copied,"set_enabled_many"):
                copied.set_enabled_many(codes,False)
                duration, _ = elapsed(lambda: copied.set_enabled_many(codes,True))
                result["s57_batch_enable_all_ms"] = round(duration * 1000,3)
                assert copied.enabled_cells() == codes
            if args.ui:
                from PySide6.QtWidgets import QApplication
                from maritime_simulator.ui.s57_panel import S57Panel
                app = QApplication.instance() or QApplication([])
                duration, panel = elapsed(lambda: S57Panel(args.enc.parent, lambda: True))
                try:
                    result["s57_table"] = dict(rows=panel.table.model().rowCount(), open_ms=round(duration * 1000,3))
                    for name, call in (("refresh", panel.refresh),
                                       ("filter", lambda: panel.search.setText("US5NY")),
                                       ("clear_filter", lambda: panel.search.clear()),
                                       ("select_all", lambda: panel._select_visible(True)),
                                       ("clear_selection", lambda: panel._select_visible(False))):
                        duration, _ = elapsed(call)
                        result["s57_table"][name + "_ms"] = round(duration * 1000,3)
                    app.processEvents()
                finally:
                    panel.shutdown()
                    panel.close()
                    panel.deleteLater()
                    app.processEvents()
        finally:
            server.close()
            mask.close()
    result["source_untouched"] = signatures == [(p.stat().st_size,p.stat().st_mtime_ns) for p in sources]
    assert result["source_untouched"]
    args.report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
