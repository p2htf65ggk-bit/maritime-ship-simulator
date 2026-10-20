# SPDX-License-Identifier: MPL-2.0
"""Read-only comparison of ENC water classification and displayed polygon surfaces."""
import argparse
from pathlib import Path
import sys
import time
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from maritime_simulator.visual.s57 import S57Store
from maritime_simulator.visual.s57_water import render_water_tile
from maritime_simulator.visual.water_mask import _tile_coordinate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--latitude', type=float, default=40.69)
    parser.add_argument('--longitude', type=float, default=-74.05)
    parser.add_argument('--span', type=float, default=.025)
    parser.add_argument('--zoom', type=int, default=16)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    store = S57Store(args.directory)
    bounds = (args.longitude-args.span, args.latitude-args.span,
              args.longitude+args.span, args.latitude+args.span)
    started = time.perf_counter()
    visible = store.features(bounds, args.zoom, ('LNDARE','DEPARE','DRGARE','SLCONS'))
    polygons, types, cells = [], [], []
    for feature in visible['features']:
        polygon = shape(feature['geometry'])
        if polygon.geom_type not in {'Polygon', 'MultiPolygon'}:
            continue
        props = feature['properties']
        polygons.append(polygon)
        depth = props.get('DRVAL2')
        types.append(props['layer'] not in {'LNDARE','SLCONS'} and
                     (not isinstance(depth,(int,float)) or depth > 0))
        cells.append(props['chart'])
    tree = STRtree(polygons)
    tiles, failures, mismatches = {}, [], []
    tested = 0
    for y in range(40):
        latitude = bounds[1] + (y+.5)/40*(bounds[3]-bounds[1])
        for x in range(40):
            longitude = bounds[0] + (x+.5)/40*(bounds[2]-bounds[0])
            point = Point(longitude,latitude)
            hits = tree.query(point, predicate='intersects')
            if not len(hits):
                continue
            index = max(hits)
            # Ignore raster/simplification edge ambiguity, approximately 10 metres.
            if polygons[index].boundary.distance(point) < .0001:
                continue
            key,pixel = _tile_coordinate(latitude,longitude,15)
            if key not in tiles:
                try:
                    tiles[key] = render_water_tile(store,*key)
                except Exception as error:
                    tiles[key] = None
                    failures.append(dict(tile=key,error=str(error)))
            image = tiles[key]
            color = image.pixelColor(*pixel) if image is not None else None
            water = color.red()>127 if color is not None and color.alpha()>127 else None
            tested += 1
            if water != types[index]:
                mismatches.append(dict(latitude=latitude,longitude=longitude,
                    displayed_water=types[index],mask_water=water,chart=cells[index]))
    result = dict(bounds=bounds,zoom=args.zoom,limited=visible['limited'],
        chart_count=visible.get('chart_count'),tested=tested,tiles=len(tiles),
        elapsed_s=round(time.perf_counter()-started,3),failures=failures,
        mismatches=mismatches)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({**result,'mismatches':mismatches[:15],'mismatch_count':len(mismatches)}))


if __name__ == '__main__':
    main()
