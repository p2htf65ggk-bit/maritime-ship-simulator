#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Create a bounded offline water mask from local GeoJSON; no online downloads."""
from __future__ import annotations

import json
from contextlib import closing
import math
from pathlib import Path
import sqlite3

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QTransform


def create_water_mask(source: Path, destination: Path, zoom: int = 13):
    if source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("GeoJSON must be smaller than 16 MB; prepare a local demo area")
    data = json.loads(source.read_text(encoding="utf-8"))
    bounds = data.get("bbox")
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError("GeoJSON needs bbox [west, south, east, north] defining surveyed coverage")
    west, south, east, north = map(float, bounds)
    if not all(math.isfinite(v) for v in (west, south, east, north)) or not -180 <= west < east <= 180 or not -85 <= south < north <= 85:
        raise ValueError("Invalid coverage bbox (antimeridian areas must be split)")
    if not 6 <= zoom <= 15:
        raise ValueError("Zoom must be between 6 and 15")
    size = 2**zoom
    def point(lon, lat):
        if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -85 <= lat <= 85:
            raise ValueError("Invalid WGS84 polygon coordinate")
        return (lon + 180) / 360 * size, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size
    left, bottom = point(west, south)
    right, top = point(east, north)
    xmin, xmax, ymin, ymax = math.floor(left), min(size - 1, math.floor(right)), math.floor(top), min(size - 1, math.floor(bottom))
    count = (xmax - xmin + 1) * (ymax - ymin + 1)
    if count > 512:
        raise ValueError(f"Area requires {count} tiles; reduce coverage or zoom (limit 512)")
    features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    vertices = 0
    for feature in features:
        geometry = feature.get("geometry", feature)
        polygons = [geometry["coordinates"]] if geometry.get("type") == "Polygon" else geometry.get("coordinates", []) if geometry.get("type") == "MultiPolygon" else None
        if polygons is None:
            raise ValueError("Use only water Polygon/MultiPolygon features with WGS84 coordinates")
        feature_path = QPainterPath()
        feature_path.setFillRule(Qt.OddEvenFill)
        for polygon in polygons:
            for ring in polygon:
                for index, coordinate in enumerate(ring):
                    vertices += 1
                    if vertices > 200_000:
                        raise ValueError("Water polygons exceed 200,000 points; simplify the local area")
                    x, y = point(float(coordinate[0]), float(coordinate[1]))
                    feature_path.moveTo(x, y) if index == 0 else feature_path.lineTo(x, y)
                feature_path.closeSubpath()
        # Preserve holes within each polygon, and unite overlapping water features.
        path = path.united(feature_path)
    if path.isEmpty():
        raise ValueError("No water polygons supplied")
    temporary = destination.with_suffix(".mbtiles.tmp")
    temporary.unlink(missing_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
            db.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
            db.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level,tile_column,tile_row)")
            metadata = {"name": source.stem, "format": "png", "maritime_water_mask": "1", "bounds": ",".join(map(str, bounds)), "scheme": "tms", "attribution": str(data.get("attribution", "Operator-supplied water polygons"))}
            db.executemany("INSERT INTO metadata VALUES (?,?)", metadata.items())
            for x in range(xmin, xmax + 1):
                for y in range(ymin, ymax + 1):
                    image = QImage(256, 256, QImage.Format_ARGB32)
                    image.fill(Qt.transparent)
                    painter = QPainter(image)
                    painter.setTransform(QTransform(256, 0, 0, 256, -x * 256, -y * 256))
                    painter.setClipRect(QRectF(left, top, right - left, bottom - top))
                    painter.fillRect(QRectF(x, y, 1, 1), QColor("black"))
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor("white"))
                    painter.drawPath(path)
                    painter.end()
                    buffer = QBuffer()
                    buffer.open(QIODevice.WriteOnly)
                    image.save(buffer, "PNG")
                    db.execute("INSERT INTO tiles VALUES (?,?,?,?)", (zoom, x, size - 1 - y, bytes(buffer.data())))
            db.commit()
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return count
