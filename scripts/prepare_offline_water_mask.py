#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Create a bounded offline water mask from local GeoJSON; no online downloads."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from maritime_simulator.visual.water_package import create_water_mask

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("geojson", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--zoom", type=int, default=13)
    args = parser.parse_args()
    print(f"Created {create_water_mask(args.geojson, args.output, args.zoom)} tiles: {args.output}")
