# SPDX-License-Identifier: MPL-2.0
"""Small numeric depth queries, independent of map portrayal and viewport zoom."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
import math
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET


GRID_STEP = 1.0 / 240.0  # GEBCO 15 arc-second, pixel-centred grid.
GRID_COLUMNS = 86400
GRID_ROWS = 43200


def grid_corners(latitude: float, longitude: float):
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Invalid bathymetry position")
    x = (longitude + 180.0) / GRID_STEP - 0.5
    y = min(GRID_ROWS - 1.0, max(0.0, (latitude + 90.0) / GRID_STEP - 0.5))
    ix, iy = math.floor(x), min(GRID_ROWS - 2, math.floor(y))
    fx, fy = x - ix, y - iy
    return tuple(((col % GRID_COLUMNS, row), weight) for col, row, weight in (
        (ix, iy, (1-fx)*(1-fy)), (ix+1, iy, fx*(1-fy)),
        (ix, iy+1, (1-fx)*fy), (ix+1, iy+1, fx*fy),
    ))


def grid_position(key):
    x, y = key
    return -90.0 + (y + 0.5) * GRID_STEP, -180.0 + (x + 0.5) * GRID_STEP


def gebco_url(key) -> str:
    lat, lon = grid_position(key)
    # MapServer returns an empty raster query for a 1x1 viewport. A 3x3
    # native-resolution window still queries exactly its centre pixel.
    delta = GRID_STEP * 1.5
    return "https://wms.gebco.net/mapserv?" + urlencode({
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo",
        "LAYERS": "GEBCO_LATEST_2", "QUERY_LAYERS": "GEBCO_LATEST_2",
        "STYLES": "", "CRS": "CRS:84",
        "BBOX": f"{lon-delta:.10f},{lat-delta:.10f},{lon+delta:.10f},{lat+delta:.10f}",
        "WIDTH": 3, "HEIGHT": 3, "I": 1, "J": 1,
        "INFO_FORMAT": "application/vnd.ogc.gml", "FEATURE_COUNT": 1,
    })


def parse_gebco(payload: bytes, key) -> float:
    if len(payload) > 65536 or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Unexpected GEBCO response")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise ValueError("Invalid GEBCO XML") from error
    fields = {element.tag.rsplit("}", 1)[-1]: element.text for element in root.iter()}
    lat, lon = grid_position(key)
    x, y, value = (float(fields[name]) for name in ("x", "y", "value_list"))
    if (not all(math.isfinite(v) for v in (x, y, value))
            or abs(x-lon) > GRID_STEP or abs(y-lat) > GRID_STEP
            or not -12000 <= value <= 9000):
        raise ValueError("Invalid GEBCO elevation")
    return value


def grid_depth(corners, elevations) -> float | None:
    elevation = sum(elevations[key] * weight for key, weight in corners)
    return -elevation if elevation < 0 else None


@dataclass(frozen=True, slots=True)
class ChartDepthReading:
    latitude: float
    longitude: float
    water_depth_m: float | None
    source: str


class S57DepthProvider:
    """Read-only, indexed and work-bounded. Invoke only on a background worker."""

    def __init__(self, store):
        self.store = store

    def depth_at(self, latitude: float, longitude: float) -> float | None:
        from shapely import from_wkb
        from shapely.geometry import Point, LineString

        grid_corners(latitude, longitude)  # Validate before querying the index.
        if not self.store.path.is_file():
            return None
        point = Point(longitude, latitude)
        deadline = time.monotonic() + 0.75
        size = 0

        def shape_from(data):
            nonlocal size
            if data is None:
                raise ValueError("S57 depth geometry exceeds its byte budget")
            size += len(data)
            if size > 8 * 1024**2 or time.monotonic() >= deadline:
                raise ValueError("S57 depth query exceeded its work budget")
            shape = from_wkb(data)
            return shape if shape.is_valid else None

        def number(properties, name):
            value = properties.get(name)
            try:
                value = float(value)
                return value if math.isfinite(value) and -100 <= value <= 12000 else None
            except (TypeError, ValueError):
                return None

        with closing(self.store.connect()) as db:
            db.execute("PRAGMA cache_size=-2048")
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            # The R-tree narrows the whole NOAA catalogue before any WKB is read.
            charts = db.execute(
                "SELECT DISTINCT c.code,c.scale FROM extent e CROSS JOIN features f ON f.id=e.id "
                "JOIN charts c ON c.code=f.chart WHERE c.enabled=1 AND e.west<=? AND e.east>=? "
                "AND e.south<=? AND e.north>=? AND f.layer IN ('DEPARE','DRGARE','LNDARE','SOUNDG') "
                "ORDER BY CASE WHEN c.scale>0 THEN c.scale ELSE 2147483647 END,c.code LIMIT 32",
                (longitude, longitude, latitude, latitude),
            ).fetchall()
            for code, _scale in charts:
                coverage = db.execute(
                    "SELECT CASE WHEN length(geometry)<=8388608 THEN geometry END "
                    "FROM chart_coverage WHERE code=?", (code,),
                ).fetchone()
                if coverage:
                    footprint = shape_from(coverage[0])
                    if footprint is None or not footprint.covers(point):
                        continue
                rows = db.execute(
                    "SELECT f.layer,CASE WHEN length(f.geometry)<=8388608 THEN f.geometry END,f.properties "
                    "FROM extent e CROSS JOIN features f ON f.id=e.id "
                    "WHERE f.chart=? AND e.west<=? AND e.east>=? AND e.south<=? AND e.north>=? "
                    "AND f.layer IN ('DEPARE','DRGARE','LNDARE','SLCONS') LIMIT 65",
                    (code, longitude, longitude, latitude, latitude),
                )
                areas = []
                for index, (layer, raw, props) in enumerate(rows):
                    if index >= 64:
                        raise ValueError("Too many S57 depth surfaces")
                    shape = shape_from(raw)
                    if shape is None or shape.geom_type not in {'Polygon', 'MultiPolygon'} or not shape.covers(point):
                        continue
                    if layer in {'LNDARE', 'SLCONS'}:
                        return None  # Never borrow a coarser chart's water across land.
                    value = number(json.loads(props), 'DRVAL1')
                    if value is not None:
                        areas.append((layer != 'DRGARE', shape.area, value, shape))
                if not areas:
                    continue
                _, _, fallback, area = min(areas, key=lambda item: item[:2])
                if fallback < 0:
                    return None
                # Keep sounding interpolation within the same depth area/water body.
                radius_lat = 1000.0 / 111320.0
                radius_lon = min(1.0, radius_lat / max(0.01, math.cos(math.radians(latitude))))
                samples = db.execute(
                    "SELECT CASE WHEN length(f.geometry)<=8388608 THEN f.geometry END,f.properties "
                    "FROM extent e CROSS JOIN features f ON f.id=e.id "
                    "WHERE f.chart=? AND f.layer='SOUNDG' AND e.west<=? AND e.east>=? "
                    "AND e.south<=? AND e.north>=? LIMIT 513",
                    (code, longitude+radius_lon, longitude-radius_lon, latitude+radius_lat, latitude-radius_lat),
                )
                nearest = []
                for index, (raw, props) in enumerate(samples):
                    if index >= 512:
                        return max(0.0, fallback)
                    shape = shape_from(raw)
                    if shape is None or shape.geom_type != 'Point' or not area.covers(shape):
                        continue
                    if not area.covers(LineString([(point.x, point.y), (shape.x, shape.y)])):
                        continue
                    depth = number(json.loads(props), 'DEPTH')
                    if depth is None and shape.has_z:
                        depth = shape.z if math.isfinite(shape.z) else None
                    if depth is None or not 0 <= depth <= 12000:
                        continue
                    distance = math.hypot((shape.x-longitude)*math.cos(math.radians(latitude)), shape.y-latitude)*111320
                    if distance <= 1000:
                        nearest.append((distance, depth))
                nearest.sort()
                if nearest and nearest[0][0] < 0.1:
                    return nearest[0][1]
                if nearest:
                    weights = [(1 / max(1, d)**2, value) for d, value in nearest[:8]]
                    return sum(w*v for w, v in weights) / sum(w for w, _ in weights)
                return max(0.0, fallback)
        return None
