# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import math
import time


def render_water_tile(store, z, x, y):
    """Transparent unknown coverage, white water, black land; worker-only ENC rasterization."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
    from shapely import from_wkb, make_valid
    from shapely.geometry import box

    count = 2**z
    west, east = x / count * 360 - 180, (x + 1) / count * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / count))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / count))))
    surfaces = store.water_surfaces((west, south, east, north))
    if not surfaces:
        return None
    viewport = box(west, south, east, north)
    image = QImage(256, 256, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setPen(Qt.NoPen)
    deadline = time.monotonic() + 1.0
    vertices = 0
    try:
        for water, raw in surfaces:
            if time.monotonic() >= deadline:
                raise ValueError("S57 water-mask rendering exceeded its work budget")
            shape = from_wkb(raw)
            if not shape.is_valid:
                shape = make_valid(shape)
            shape = shape.intersection(viewport)
            parts = list(shape.geoms) if shape.geom_type in {'MultiPolygon', 'GeometryCollection'} else [shape]
            painter.setBrush(QColor('white' if water else 'black'))
            for polygon in parts:
                if polygon.geom_type != 'Polygon' or polygon.is_empty:
                    continue
                path = QPainterPath()
                path.setFillRule(Qt.OddEvenFill)
                for ring in (polygon.exterior, *polygon.interiors):
                    for index, (lon, lat, *_rest) in enumerate(ring.coords):
                        vertices += 1
                        if vertices > 200_000:
                            raise ValueError("S57 water-mask tile has too many polygon vertices")
                        px = ((lon + 180) / 360 * count - x) * 256
                        py = ((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * count - y) * 256
                        path.moveTo(px, py) if index == 0 else path.lineTo(px, py)
                    path.closeSubpath()
                painter.drawPath(path)
    finally:
        painter.end()
    return image.convertToFormat(QImage.Format_Indexed8)
