#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Compare exact versus batched PMTiles reads; live tests never modify saved maps."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import random
import sys
from threading import Event
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pmtiles.reader import Reader
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from maritime_simulator.visual import offline_areas as areas


def fixture_response(coordinates, latency):
    output = io.BytesIO()
    writer = Writer(output)
    generator = random.Random(42)
    for xyz in coordinates:
        writer.write_tile(zxy_to_tileid(*xyz), gzip.compress(generator.randbytes(3000)))
    writer.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP}, {})
    payload = output.getvalue()
    def response(_url, *, limit, headers, cancel):
        start, end = map(int, headers["Range"].split("=")[1].split("-"))
        if cancel.wait(latency):
            areas.check_cancel(cancel)
        return payload[start:end+1], 206, {"Content-Range": f"bytes {start}-{end}/{len(payload)}", "ETag": "fixture"}
    return response


def compare(url, coordinates, read_response, render_delay_s=0, render=False):
    results, outputs = {}, []
    render_outputs = []
    for label, reader_type in (("exact", Reader), ("batched", areas._BasemapReader)):
        requests = 0
        def counted(*args, **kwargs):
            nonlocal requests
            requests += 1
            return read_response(*args, **kwargs)
        source = areas.RangeSource(url, Event())
        started = time.perf_counter()
        data = []
        rendered_hash, render_seconds = hashlib.sha256(), 0.0
        def consume(tile):
            nonlocal render_seconds
            data.append(tile)
            if render and tile is not None:
                started = time.perf_counter()
                rendered, _ = areas.rasterize_tile(tile, compressed=True, include_water=False)
                render_seconds += time.perf_counter() - started
                rendered_hash.update(rendered)
            time.sleep(render_delay_s)
        with patch.object(areas, "_read_response", counted):
            reader = reader_type(source)
            if label == "batched":
                for _, xyz, tile in areas._tile_payloads(reader, coordinates, source.cancel):
                    consume(tile)
            else:
                for index in range(0, len(coordinates), 64):
                    batch = coordinates[index:index+64]
                    for xyz in batch:
                        consume(reader.get(*xyz))
        results[label] = dict(seconds=round(time.perf_counter() - started, 3), requests=requests,
                              network_bytes=source.download_bytes, tile_bytes=sum(len(tile or b"") for tile in data),
                              render_seconds=round(render_seconds,3))
        outputs.append(data)
        render_outputs.append(rendered_hash.hexdigest())
    if any(output != outputs[0] for output in outputs[1:]) or any(tile is None for tile in outputs[0]):
        raise ValueError("Downloaded tile bytes do not match or a selected tile is missing")
    if len(set(render_outputs)) != 1:
        raise ValueError("Rendered maps differ between download strategies")
    results.update(identical_tiles=True, tiles=len(coordinates), source=url,
                   speedup=round(results["exact"]["seconds"] / max(0.001, results["batched"]["seconds"]), 2),
                   render_delay_ms=render_delay_s * 1000)
    results["actual_rendering"] = render
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the current Protomaps archive instead of a latency fixture")
    parser.add_argument("--bounds", nargs=4, type=float, default=[9.93, 53.52, 9.99, 53.56], metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--zoom", type=int, choices=(13, 14, 15), default=15)
    parser.add_argument("--latency-ms", type=float, default=20)
    parser.add_argument("--render-delay-ms", type=float, default=0, help="Simulated per-tile rendering cost, not actual map rendering")
    parser.add_argument("--render", action="store_true", help="Also render each tile; requires --live and a working Qt platform")
    args = parser.parse_args()
    coordinates = sorted(areas.area_tiles(args.bounds, args.zoom), key=lambda xyz: zxy_to_tileid(*xyz))
    if len(coordinates) > 256:
        parser.error("Benchmark is limited to 256 tiles; choose a smaller rectangle")
    if not 0 <= args.latency_ms <= 1000:
        parser.error("Latency must be between 0 and 1000 ms")
    if not 0 <= args.render_delay_ms <= 1000:
        parser.error("Render delay must be between 0 and 1000 ms")
    if args.render and not args.live:
        parser.error("Actual rendering requires live vector tiles (--live)")
    if args.render:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
    response = areas._read_response if args.live else fixture_response(coordinates, args.latency_ms / 1000)
    url = areas.latest_build(Event()) if args.live else "latency-fixture"
    result = compare(url, coordinates, response, args.render_delay_ms / 1000, args.render)
    result["mode"] = "live" if args.live else "latency-fixture"
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
