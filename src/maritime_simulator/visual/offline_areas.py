# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections import Counter, OrderedDict
from contextlib import closing
from datetime import datetime, timezone
import gzip
import io
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import time
from threading import Event
from uuid import uuid4
from urllib import request
from urllib.parse import urlsplit

from PySide6.QtCore import QBuffer, QIODevice, QLockFile, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QTransform

from .geodata import verified_ssl_context


AREAS_FILE = "offline-areas.mbtiles"
ATTRIBUTION = "OpenStreetMap contributors / ODbL | Natural Earth | Protomaps"
LARGE_AREA_TILES = 20_000
MAX_TILES = 200_000
MAX_STORED_BYTES = 3 * 1024**3
FAST_DELETE_BYTES = 64 * 1024**2
MAX_DOWNLOAD_BYTES = 1024**3
BUILD_INDEX = "https://build-metadata.protomaps.dev/builds.json"
USER_AGENT = "MaritimeShipSimulator/2.2 (offline region download; +https://github.com/p2htf65ggk-bit/maritime-ship-simulator)"


class DownloadCancelled(Exception):
    pass


def check_cancel(cancel: Event):
    if cancel.is_set():
        raise DownloadCancelled("Download cancelled; existing offline areas were retained")


def normalize_area(item):
    bounds = list(map(float, item["bounds"]))
    zoom = int(item["max_zoom"])
    next(_tile_rectangles(bounds, zoom))
    result = dict(bounds=bounds, max_zoom=zoom)
    if "polygon" in item:
        from shapely.geometry import Polygon
        points = item["polygon"]
        if not isinstance(points, (list, tuple)) or not 3 <= len(points) <= 65:
            raise ValueError("A polygon needs 3 to 64 corners")
        points = [tuple(map(float, point)) for point in points]
        if points[0] == points[-1]:
            points.pop()
        if not 3 <= len(points) <= 64 or any(len(point) != 2 or not all(math.isfinite(v) for v in point)
                                            or not -180 <= point[0] <= 180 or not -85 <= point[1] <= 85 for point in points):
            raise ValueError("Choose 3 to 64 finite polygon corners within 85 degrees latitude")
        shape = Polygon(points)
        if not shape.is_valid or shape.area <= 0:
            raise ValueError("Polygon edges must not cross; choose at least three distinct corners")
        if any(not math.isclose(actual, stated, abs_tol=1e-9, rel_tol=0) for actual, stated in zip(shape.bounds, bounds)):
            raise ValueError("Polygon bounds do not match its corners")
        # Starting corner and drawing direction must not create duplicate areas.
        first = min(range(len(points)), key=points.__getitem__)
        ring = points[first:] + points[:first]
        reverse = ring[:1] + list(reversed(ring[1:]))
        result["polygon"] = [list(point) for point in min(ring, reverse)]
    return result


def area_identity(area):
    bounds = tuple(area["bounds"])
    if "polygon" not in area:
        return bounds
    normalized = normalize_area(area)
    return (*bounds, tuple(map(tuple, normalized["polygon"])))


def _tile_rectangles(bounds, max_zoom=15, polygon=None):
    if len(bounds) != 4:
        raise ValueError("Invalid area rectangle")
    west, south, east, north = map(float, bounds)
    if not all(math.isfinite(v) for v in (west, south, east, north)) or not -180 <= west < east <= 180 or not -85 <= south < north <= 85:
        raise ValueError("Choose a rectangle without crossing the date line, within 85 degrees latitude")
    if max_zoom not in (13, 14, 15):
        raise ValueError("Unsupported offline detail level")
    if polygon is not None:
        from shapely.geometry import Polygon, box
        normalized = normalize_area(dict(bounds=bounds, max_zoom=max_zoom, polygon=polygon))
        points = [((lon + 180) / 360, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2)
                  for lon, lat in normalized["polygon"]]
        for z in range(max_zoom + 1):
            scale = 2**z
            shape = Polygon([(x * scale, y * scale) for x, y in points])
            left, top, right, bottom = shape.bounds
            for y in range(max(0, math.floor(top)), min(scale - 1, math.floor(bottom)) + 1):
                strip = shape.intersection(box(left - 1, y, right + 1, y + 1))
                parts = list(strip.geoms) if hasattr(strip, "geoms") else [strip]
                intervals = []
                for part in parts:
                    if not part.is_empty:
                        intervals.append((max(0, math.floor(part.bounds[0])), min(scale - 1, math.floor(part.bounds[2]))))
                end = -1
                for start, stop in sorted(intervals):
                    start = max(start, end + 1)
                    if start <= stop:
                        yield z, start, y, stop, y
                    end = max(end, stop)
        return
    for z in range(0, max_zoom + 1):
        scale = 2**z
        left = max(0, math.floor((west + 180) / 360 * scale))
        right = min(scale - 1, math.floor((east + 180) / 360 * scale))
        def row(lat):
            return max(0, min(scale - 1, math.floor((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)))
        top, bottom = row(north), row(south)
        yield z, left, top, right, bottom


def area_tile_count(bounds, max_zoom=15, polygon=None):
    count = 0
    for z,left,top,right,bottom in _tile_rectangles(bounds, max_zoom, polygon):
        count += (right - left + 1) * (bottom - top + 1)
        if count > MAX_TILES:
            raise ValueError(f"Selection exceeds the {MAX_TILES:,}-tile memory safety limit; reduce detail or split it into separate downloads")
    return count


def area_tiles(bounds, max_zoom=15, polygon=None):
    area_tile_count(bounds, max_zoom, polygon)
    result = []
    for z, left, top, right, bottom in _tile_rectangles(bounds, max_zoom, polygon):
        result.extend((z, x, y) for x in range(left, right + 1) for y in range(top, bottom + 1))
    return result


def _read_response(url, *, limit, headers=None, cancel):
    check_cancel(cancel)
    req = request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with request.urlopen(req, timeout=12, context=verified_ssl_context()) as reply:
        chunks, size = [], 0
        while chunk := reply.read(min(65536, limit + 1 - size)):
            check_cancel(cancel)
            size += len(chunk)
            if size > limit:
                raise ValueError("Map download exceeded the bounded response size")
            chunks.append(chunk)
        return b"".join(chunks), reply.status, reply.headers


def latest_build(cancel):
    data, _, _ = _read_response(BUILD_INDEX, limit=1024**2, cancel=cancel)
    builds = json.loads(data)
    keys = [b["key"] for b in builds if str(b.get("version", "")).startswith("4.") and re.fullmatch(r"\d{8}\.pmtiles", str(b.get("key", "")))]
    if not keys:
        raise ValueError("No compatible Protomaps basemap is currently available")
    return "https://build.protomaps.com/" + max(keys)


class RangeSource:
    """Read small ranges from a permitted downloadable archive, never its whole planet."""

    def __init__(self, url, cancel):
        self.url, self.cancel = url, cancel
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.download_bytes = 0
        self.etag = None
        self.total_size = None

    def __call__(self, offset, length):
        check_cancel(self.cancel)
        if offset < 0 or not 0 < length <= 8 * 1024**2:
            raise ValueError("Invalid archive range")
        key = (offset, length)
        for cached_offset, cached_length in reversed(self.cache):
            if cached_offset <= offset and offset + length <= cached_offset + cached_length:
                cached_key = (cached_offset, cached_length)
                data = self.cache[cached_key]
                self.cache.move_to_end(cached_key)
                return data[offset - cached_offset:offset - cached_offset + length]
        if self.total_size is not None and offset + length > self.total_size:
            raise ValueError("Invalid archive range beyond its end")
        if self.download_bytes + length > MAX_DOWNLOAD_BYTES:
            raise ValueError("Area download exceeds 1 GiB; choose a smaller rectangle")
        headers = {"Range": f"bytes={offset}-{offset + length - 1}"}
        if self.etag:
            headers["If-Match"] = self.etag
        last_error = None
        for attempt in range(2):
            check_cancel(self.cancel)
            try:
                data, status, response_headers = _read_response(self.url, limit=length, headers=headers, cancel=self.cancel)
                content_range = response_headers.get("Content-Range", "")
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                if status != 206 or len(data) != length or not match or (int(match[1]), int(match[2])) != (offset, offset + length - 1):
                    raise ValueError("Map server did not return the requested partial archive; full download refused")
                total = int(match[3])
                etag = response_headers.get("ETag")
                if total <= offset + length - 1 or self.total_size is not None and total != self.total_size or self.etag and etag != self.etag:
                    raise ValueError("Map archive changed during download; retry the area")
                self.total_size, self.etag = total, etag
                break
            except OSError as error:
                last_error = error
                if attempt == 1:
                    raise OSError(f"Offline map download failed: {last_error}. Check Internet access and retry.") from error
                if self.cancel.wait(0.5):
                    check_cancel(self.cancel)
        self.download_bytes += len(data)
        if self.download_bytes > MAX_DOWNLOAD_BYTES:
            raise ValueError("Area download exceeds 1 GiB; choose a smaller rectangle")
        self.cache[key] = data
        self.cache_bytes += len(data)
        while self.cache_bytes > 8 * 1024**2:
            _, removed = self.cache.popitem(last=False)
            self.cache_bytes -= len(removed)
        return data

    def prefetch(self, ranges):
        """Coalesce selected nearby ranges, with bounded gaps and memory use."""
        groups = []
        budget = 4 * 1024**2
        for offset, length in sorted(set(ranges)):
            check_cancel(self.cancel)
            if length > budget:
                continue
            if groups:
                start, end, useful = groups[-1]
                new_end = max(end, offset + length)
                gap = max(0, offset - end)
                new_useful = useful + max(0, offset + length - max(end, offset))
                if gap <= 16 * 1024 and new_end - start <= 512 * 1024 and new_end - start - new_useful <= new_useful / 4:
                    extra = new_end - end
                    if extra <= budget:
                        groups[-1] = (start, new_end, new_useful)
                        budget -= extra
                        continue
            groups.append((offset, offset + length, length))
            budget -= length
        for start, end, _ in groups:
            self(start, end - start)


def _path(geometry, extent):
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    kind, coordinates = geometry.get("type"), geometry.get("coordinates", [])
    polygons = [coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else []
    for polygon in polygons:
        for ring in polygon:
            for index, coordinate in enumerate(ring):
                x, y = coordinate[:2]
                path.moveTo(x / extent, y / extent) if index == 0 else path.lineTo(x / extent, y / extent)
            path.closeSubpath()
    lines = [coordinates] if kind == "LineString" else coordinates if kind == "MultiLineString" else []
    for line in lines:
        for index, coordinate in enumerate(line):
            x, y = coordinate[:2]
            path.moveTo(x / extent, y / extent) if index == 0 else path.lineTo(x / extent, y / extent)
    return path


def _png(image):
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("Offline tile rendering failed")
    return bytes(buffer.data())


def _decode_tile(data, compressed):
    from mapbox_vector_tile import decode
    if compressed:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
            data = source.read(8 * 1024**2 + 1)
    if len(data) > 8 * 1024**2:
        raise ValueError("Vector tile is too large")
    layers = decode(data, default_options={"y_coord_down": True})
    if sum(len(layer["features"]) for layer in layers.values()) > 50_000:
        raise ValueError("Vector tile has too many objects")
    return layers


def rasterize_water_mask(data, *, compressed=True):
    layers = _decode_tile(data, compressed)
    water = QImage(256, 256, QImage.Format_RGB32)
    water.fill(Qt.white)
    painter = QPainter(water)
    painter.setTransform(QTransform.fromScale(256, 256))
    painter.setPen(Qt.NoPen)
    try:
        for name in ("earth", "water", "landuse"):
            layer = layers.get(name, {})
            extent = max(1, int(layer.get("extent", 4096)))
            for feature in layer.get("features", []):
                geometry = feature["geometry"]
                if geometry["type"] not in {"Polygon", "MultiPolygon"}:
                    continue
                if name == "landuse" and feature.get("properties", {}).get("kind") not in {"pier", "platform"}:
                    continue
                painter.setBrush(Qt.white if name == "water" else Qt.black)
                painter.drawPath(_path(geometry, extent))
    finally:
        painter.end()
    return _png(water)


def rasterize_tile(data, *, compressed=True, include_water=True):
    layers = _decode_tile(data, compressed)
    image = QImage(512, 512, QImage.Format_RGB32)
    image.fill(QColor("#a5cbdc"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setTransform(QTransform.fromScale(512, 512))
    painter.setPen(Qt.NoPen)
    try:
        for name in ("earth", "landcover", "landuse", "water", "buildings", "roads", "boundaries"):
            layer = layers.get(name, {})
            extent = max(1, int(layer.get("extent", 4096)))
            for feature in layer.get("features", []):
                geometry = feature["geometry"]
                kind = str(feature.get("properties", {}).get("kind", ""))
                path = _path(geometry, extent)
                if geometry["type"] in {"LineString", "MultiLineString"} and name not in {"roads", "boundaries"}:
                    continue
                color = "#dfe5d9" if name == "earth" else "#b7d2a7" if kind in {"forest", "wood", "park", "grass", "grassland", "meadow", "scrub"} else "#d7d9d6"
                if name == "water":
                    color = "#a5cbdc"
                elif name == "buildings":
                    color = "#b8bab9"
                elif name in {"roads", "boundaries"}:
                    pen = QPen(QColor("#eee9d7" if name == "roads" else "#afb3ae"))
                    pen.setWidthF(1.3 / 512 if name == "roads" else 0.7 / 512)
                    painter.setPen(pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawPath(path)
                    painter.setPen(Qt.NoPen)
                    continue
                painter.setBrush(QColor(color))
                painter.drawPath(path)
        # Labels use the installed Qt font and need no remote glyph/font requests.
        painter.resetTransform()
        painter.setFont(QFont("Arial", 9))
        painter.setPen(QColor("#334039"))
        occupied = []
        for layer_name in ("places", "pois"):
            layer = layers.get(layer_name, {})
            extent = max(1, int(layer.get("extent", 4096)))
            for feature in layer.get("features", [])[:200]:
                geometry = feature["geometry"]
                label = str(feature.get("properties", {}).get("name", ""))[:48]
                if not label or geometry["type"] != "Point":
                    continue
                x, y = (float(value) / extent * 512 for value in geometry["coordinates"][:2])
                box = painter.fontMetrics().boundingRect(label).translated(int(x), int(y))
                if any(box.intersects(other) for other in occupied):
                    continue
                occupied.append(box)
                painter.drawText(QPointF(x, y), label)
    finally:
        painter.end()
    return _png(image), rasterize_water_mask(data, compressed=compressed) if include_water else None


def saved_areas(directory: Path):
    path = directory / AREAS_FILE
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT value FROM metadata WHERE name='maritime_areas'").fetchone()
        return json.loads(row[0]) if row else []
    except (OSError, ValueError, sqlite3.Error):
        return []


def _package_lock(directory):
    lock = QLockFile(str(directory / (AREAS_FILE + ".lock")))
    if not lock.tryLock(0):
        raise ValueError("Another map download or update is active; wait for it to finish")
    return lock


class _BasemapReader:
    """PMTiles library lookup with bounded decoded-directory and batch caches."""

    def __init__(self, source):
        from pmtiles.reader import Reader
        self.source = source
        self._header = Reader(source).header()
        self.directories = OrderedDict()
        self.directory_entries = 0
        self.batch = {}

    def header(self):
        return self._header

    def tile_range(self, z, x, y):
        from pmtiles.tile import deserialize_directory, find_tile, zxy_to_tileid
        coordinate = (z, x, y)
        if coordinate in self.batch:
            return self.batch[coordinate]
        tile_id = zxy_to_tileid(z, x, y)
        header = self._header
        offset, length = header["root_offset"], header["root_length"]
        for _ in range(4):
            key = (offset, length)
            if key not in self.directories:
                directory = deserialize_directory(self.source(offset, length))
                self.directories[key] = directory
                self.directory_entries += len(directory)
                while len(self.directories) > 32 or self.directory_entries > 65_536:
                    _, removed = self.directories.popitem(last=False)
                    self.directory_entries -= len(removed)
            else:
                directory = self.directories[key]
                self.directories.move_to_end(key)
            result = find_tile(directory, tile_id)
            if result is None:
                return None
            if result.run_length:
                return header["tile_data_offset"] + result.offset, result.length
            offset = header["leaf_directory_offset"] + result.offset
            length = result.length
        return None

    def prefetch(self, coordinates):
        self.batch = {coordinate: self.tile_range(*coordinate) for coordinate in coordinates}
        if hasattr(self.source, "prefetch"):
            self.source.prefetch(location for location in self.batch.values() if location is not None)

    def get(self, z, x, y):
        location = self.tile_range(z, x, y)
        return self.source(*location) if location is not None else None


def _open_basemap(max_zoom, cancel, source_factory, build_resolver):
    from pmtiles.tile import Compression, TileType
    source = source_factory(build_resolver(cancel), cancel)
    reader = _BasemapReader(source)
    header = reader.header()
    if header["tile_type"] != TileType.MVT or header["tile_compression"] not in {Compression.GZIP, Compression.NONE} or header["max_zoom"] < max_zoom:
        raise ValueError("Incompatible Protomaps archive")
    return reader, header, source


class _AreaTileProgress:
    def __init__(self, areas, callback):
        self.callback = callback
        self.members = {}
        self.rows = []
        for index, area in enumerate(areas):
            coordinates = area_tiles(area["bounds"], area["max_zoom"], area.get("polygon"))
            self.rows.append(dict(normalize_area(area), done=0, total=len(coordinates),
                                  content_bytes=0, status="Connecting"))
            for coordinate in coordinates:
                self.members.setdefault(coordinate, []).append(index)
        self.emit("Connecting")

    def tile(self, coordinate, rendered_bytes, vector_bytes):
        for index in self.members.get(coordinate, ()):
            row = self.rows[index]
            row["done"] += 1
            row["content_bytes"] += rendered_bytes + vector_bytes.get(index, 0)

    def ordered_coordinates(self):
        from pmtiles.tile import zxy_to_tileid
        seen, ordered = set(), []
        for area in self.rows:
            for coordinate in sorted(area_tiles(area["bounds"], area["max_zoom"], area.get("polygon")), key=lambda xyz: zxy_to_tileid(*xyz)):
                if coordinate not in seen:
                    seen.add(coordinate)
                    ordered.append(coordinate)
        return ordered

    def emit(self, status):
        active = next((index for index, row in enumerate(self.rows) if row["done"] < row["total"]), -1)
        payload = []
        for index, row in enumerate(self.rows):
            state = status
            if status in {"Connecting", "Reading", "Downloading"}:
                state = "Finalizing" if row["done"] == row["total"] else status if index == active else "Queued"
            payload.append(dict(row, status=state))
        self.callback(payload)


def _record_area_sizes(db, areas, cancel):
    db.execute("CREATE TEMP TABLE IF NOT EXISTS area_metrics (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,PRIMARY KEY(zoom_level,tile_column,tile_row))")
    for area in areas:
        check_cancel(cancel)
        coordinates = area_tiles(area["bounds"], area["max_zoom"], area.get("polygon"))
        db.execute("DELETE FROM area_metrics")
        db.executemany("INSERT INTO area_metrics VALUES (?,?,?)", ((z,x,2**z-1-y) for z,x,y in coordinates))
        count, size = db.execute("SELECT COUNT(*),COALESCE(SUM(length(tile_data)),0) FROM area_metrics CROSS JOIN tiles USING (zoom_level,tile_column,tile_row)").fetchone()
        vector_size = db.execute("SELECT COALESCE(SUM(length(tile_data)),0) FROM area_metrics CROSS JOIN vector_tiles USING (zoom_level,tile_column,tile_row) WHERE area_metrics.zoom_level=?", (area["max_zoom"],)).fetchone()[0]
        water_size = db.execute("SELECT COALESCE(SUM(length(tile_data)),0) FROM area_metrics CROSS JOIN water_tiles USING (zoom_level,tile_column,tile_row) WHERE area_metrics.zoom_level=?", (area["max_zoom"],)).fetchone()[0]
        area.pop("size_by_zoom", None)
        area.update(tile_count=count, content_bytes=size + vector_size + water_size)


def _tile_payloads(reader, coordinates, cancel, tracker=None):
    """Keep bounded archive batches inside each area's sequential download."""
    end = 0
    for index, xyz in enumerate(coordinates):
        check_cancel(cancel)
        if index == end:
            end = min(index + 64, len(coordinates))
            if tracker is not None:
                owner = tracker.members[xyz][0]
                end = next((i for i in range(index + 1, end)
                            if tracker.members[coordinates[i]][0] != owner), end)
            reader.prefetch(coordinates[index:end])
        yield index, xyz, reader.get(*xyz)


def _write_tiles(db, coordinates, water_coordinates, reader, header, temporary, cancel, progress, tracker=None):
    from pmtiles.tile import Compression
    compressed = header["tile_compression"] == Compression.GZIP
    progress(0, len(coordinates), "Downloading maps...")
    if tracker is not None:
        tracker.emit("Downloading")
    with closing(_tile_payloads(reader, coordinates, cancel, tracker)) as payloads:
        for index, (z, x, y), tile in payloads:
            check_cancel(cancel)
            if tile is None:
                raise ValueError(f"Basemap tile {z}/{x}/{y} is unavailable; existing areas were retained")
            rendered, _ = rasterize_tile(tile, compressed=compressed, include_water=False)
            row = 2**z - 1 - y
            db.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", (z, x, row, rendered))
            vector_size = 0
            if (z, x, y) in water_coordinates:
                if len(tile) > 8 * 1024**2:
                    raise ValueError("Water source tile exceeds 8 MiB")
                vector = tile if compressed else gzip.compress(tile)
                vector_size = len(vector)
                db.execute("INSERT OR REPLACE INTO vector_tiles VALUES (?,?,?,?)", (z, x, row, vector))
                db.execute("DELETE FROM water_tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?", (z, x, row))
            if tracker is not None:
                tracker.tile((z,x,y), len(rendered), {
                    area_index: vector_size for area_index in tracker.members.get((z,x,y), ())
                    if z == tracker.rows[area_index]["max_zoom"]
                })
            if index % 16 == 0 or index + 1 == len(coordinates):
                progress(index + 1, len(coordinates), "Downloading maps...")
                if tracker is not None:
                    tracker.emit("Downloading")
            if index % 64 == 0 and temporary.stat().st_size > MAX_STORED_BYTES:
                raise ValueError("Offline areas exceed the 3 GiB storage limit")


def _replace_package(temporary, target, cancel):
    if temporary.stat().st_size > MAX_STORED_BYTES:
        raise ValueError("Offline areas exceed the 3 GiB storage limit")
    check_cancel(cancel)
    for attempt in range(10):
        try:
            temporary.replace(target)
            return
        except PermissionError:
            if attempt == 9:
                raise
            if cancel.wait(0.1):
                check_cancel(cancel)


def _delete_area_tiles(target, removed, kept, cancel, progress):
    """Delete only exclusive tile keys, with one rollback-safe transaction and no BLOB copy."""
    with closing(sqlite3.connect(target, timeout=0.5)) as db, db:
        db.execute("PRAGMA temp_store=FILE")
        db.set_progress_handler(lambda: int(cancel.is_set()), 10_000)
        db.execute("CREATE TEMP TABLE removed_tiles (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,PRIMARY KEY(zoom_level,tile_column,tile_row))")
        db.execute("BEGIN IMMEDIATE")
        for index, table in enumerate(("tiles", "water_tiles", "vector_tiles")):
            check_cancel(cancel)
            progress(index, 3, "Deleting unused offline tiles...")
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                continue
            db.execute("DELETE FROM removed_tiles")
            for area in removed:
                for z, left, top, right, bottom in _tile_rectangles(area["bounds"], area["max_zoom"], area.get("polygon")):
                    check_cancel(cancel)
                    if table != "tiles" and z != area["max_zoom"]:
                        continue
                    db.execute(f"INSERT OR IGNORE INTO removed_tiles SELECT zoom_level,tile_column,tile_row FROM {table} "
                               "WHERE zoom_level=? AND tile_column BETWEEN ? AND ? AND tile_row BETWEEN ? AND ?",
                               (z, left, right, 2**z - 1 - bottom, 2**z - 1 - top))
            for area in kept:
                for z, left, top, right, bottom in _tile_rectangles(area["bounds"], area["max_zoom"], area.get("polygon")):
                    check_cancel(cancel)
                    if table != "tiles" and z != area["max_zoom"]:
                        continue
                    db.execute("DELETE FROM removed_tiles WHERE zoom_level=? AND tile_column BETWEEN ? AND ? AND tile_row BETWEEN ? AND ?",
                               (z, left, right, 2**z - 1 - bottom, 2**z - 1 - top))
            db.execute(f"DELETE FROM {table} WHERE (zoom_level,tile_column,tile_row) IN (SELECT * FROM removed_tiles)")
        missing = [area for area in kept if "content_bytes" not in area or "tile_count" not in area]
        if missing:
            _record_area_sizes(db, missing, cancel)
        db.execute("UPDATE metadata SET value=? WHERE name='maritime_areas'", (json.dumps(kept),))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('maritime_package_id',?)", (uuid4().hex,))
        progress(3, 3, "Deleting unused offline tiles...")
        check_cancel(cancel)


def manage_areas(directory: Path, desired, expected, *, additions=(), cancel=None, progress=lambda *_: None,
                 area_progress=lambda *_: None,
                 source_factory=RangeSource, build_resolver=latest_build):
    """Keep shared tiles; use transactions for maintenance and staged replacement for downloads."""
    cancel = cancel or Event()
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / AREAS_FILE
    lock = _package_lock(directory)
    temporary = directory / (AREAS_FILE + ".update")
    try:
        current = saved_areas(directory)
        if current != expected:
            raise ValueError("Saved areas changed; reopen the area manager before applying changes")
        identity = area_identity
        originals = {identity(area): area for area in current}
        kept, updates, seen = [], [], set()
        for item in desired:
            original = originals.get(identity(item))
            if original is None or identity(item) in seen:
                raise ValueError("Area manager cannot add or duplicate area coverage")
            seen.add(identity(item))
            zoom = int(item["max_zoom"])
            if zoom != original["max_zoom"]:
                area_tile_count(item["bounds"], zoom, item.get("polygon"))
            area = dict(original, name=str(item["name"]).strip()[:64] or "Offline area", max_zoom=zoom)
            kept.append(area)
            if item.get("refresh") or zoom != original["max_zoom"]:
                updates.append(area)
        for item in additions:
            if identity(item) in seen or identity(item) in originals:
                raise ValueError("Area manager cannot add duplicate area coverage")
            seen.add(identity(item))
            zoom = int(item["max_zoom"])
            area_tile_count(item["bounds"], zoom, item.get("polygon"))
            area = dict(name=str(item["name"]).strip()[:64] or "Offline area",
                        **normalize_area(item))
            kept.append(area)
            updates.append(area)
        check_cancel(cancel)
        if len(kept) == len(current) and not updates:
            if not kept:
                return ""
            with closing(sqlite3.connect(target, timeout=0.5)) as db:
                missing = [area for area in kept if "content_bytes" not in area or "tile_count" not in area]
                if missing:
                    _record_area_sizes(db, missing, cancel)
                db.execute("UPDATE metadata SET value=? WHERE name='maritime_areas'", (json.dumps(kept),))
                check_cancel(cancel)
                db.commit()
        elif not kept:
            target.unlink(missing_ok=True)
        elif not updates and target.stat().st_size > FAST_DELETE_BYTES:
            removed = [area for area in current if identity(area) not in seen]
            _delete_area_tiles(target, removed, kept, cancel, progress)
        else:
            exists = target.exists()
            size = target.stat().st_size if exists else 0
            if shutil.disk_usage(directory).free < size + 64 * 1024**2:
                raise ValueError("Insufficient temporary space to safely compact the map package")
            progress(0, 1, "Keeping shared tiles and compacting maps...")
            coordinates, water_coordinates = set(), set()
            for item in kept:
                tiles = area_tiles(item["bounds"], item["max_zoom"], item.get("polygon"))
                coordinates.update(tiles)
                water_coordinates.update(tile for tile in tiles if tile[0] == item["max_zoom"])
            reader = header = source = None
            tracker = _AreaTileProgress(updates, area_progress) if updates else None
            if updates:
                update_tiles, update_water = selection_tiles(updates)
                update_tiles = tracker.ordered_coordinates()
                progress(0, len(update_tiles), "Connecting to renew selected areas...")
                reader, header, source = _open_basemap(max(area["max_zoom"] for area in updates), cancel, source_factory, build_resolver)
            temporary.unlink(missing_ok=True)
            with closing(sqlite3.connect(temporary, uri=True)) as db:
                db.set_progress_handler(lambda: int(cancel.is_set()), 10_000)
                if exists:
                    db.execute("ATTACH DATABASE ? AS original", (target.as_uri() + "?mode=ro",))
                db.execute("CREATE TABLE metadata (name TEXT PRIMARY KEY,value TEXT)")
                if exists:
                    db.execute("INSERT INTO metadata SELECT * FROM original.metadata")
                for table in ("tiles", "water_tiles", "vector_tiles"):
                    db.execute(f"CREATE TABLE {table} (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB,PRIMARY KEY(zoom_level,tile_column,tile_row))")
                db.execute("CREATE TEMP TABLE needed (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,PRIMARY KEY(zoom_level,tile_column,tile_row))")
                for tables, needed in ((('tiles',), coordinates), (('water_tiles', 'vector_tiles'), water_coordinates)):
                    db.execute("DELETE FROM needed")
                    db.executemany("INSERT INTO needed VALUES (?,?,?)", ((z,x,2**z-1-y) for z,x,y in needed))
                    for table in tables:
                        if exists and db.execute("SELECT 1 FROM original.sqlite_master WHERE name=?", (table,)).fetchone():
                            db.execute(f"INSERT INTO {table} SELECT source.* FROM original.{table} source JOIN needed USING (zoom_level,tile_column,tile_row)")
                if updates:
                    _write_tiles(db, update_tiles, update_water, reader, header, temporary, cancel, progress, tracker)
                    for area in updates:
                        area.update(saved=datetime.now(timezone.utc).isoformat(timespec="seconds"), source=source.url)
                    db.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", {
                        "format": "png", "scheme": "tms", "attribution": ATTRIBUTION,
                        "maritime_water_mask": "1", "maritime_water_table": "water_tiles",
                        "maritime_deferred_water": "1", "maritime_vector_compression": "gzip",
                    }.items())
                _record_area_sizes(db, kept, cancel)
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('maritime_areas',?)", (json.dumps(kept),))
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('maritime_package_id',?)", (uuid4().hex,))
                db.commit()
            _replace_package(temporary, target, cancel)
            if tracker is not None:
                tracker.emit("Downloaded")
        progress(1, 1, "Saved areas updated")
        return AREAS_FILE if kept else ""
    except sqlite3.OperationalError:
        check_cancel(cancel)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlock()


def download_area(bounds, name, directory: Path, *, max_zoom=15, cancel=None, progress=lambda *_: None, source_factory=RangeSource, build_resolver=latest_build):
    return download_areas([{"bounds": bounds, "max_zoom": max_zoom, "name": name}], name, directory,
                          cancel=cancel, progress=progress, source_factory=source_factory, build_resolver=build_resolver)


def selection_tile_count(selections):
    """Count tile-run unions without allocating a list of every selected tile."""
    if not 1 <= len(selections) <= 32:
        raise ValueError("Choose between 1 and 32 areas")
    levels = {}
    for item in selections:
        zoom = int(item["max_zoom"])
        area_tile_count(item["bounds"], zoom, item.get("polygon"))
        for z, left, top, right, bottom in _tile_rectangles(item["bounds"], zoom, item.get("polygon")):
            events = levels.setdefault(z, {})
            events.setdefault(top, []).append(((left, right + 1), 1))
            events.setdefault(bottom + 1, []).append(((left, right + 1), -1))
    count = 0
    for events in levels.values():
        active = Counter()
        edges = sorted(events)
        for top, bottom in zip(edges, edges[1:]):
            for interval, change in events[top]:
                active[interval] += change
                if not active[interval]:
                    del active[interval]
            width, end = 0, -1
            for left, right in sorted(active):
                width += max(0, right - max(end, left))
                end = max(end, right)
            count += width * (bottom - top)
            if count > MAX_TILES:
                raise ValueError(f"Selection exceeds the {MAX_TILES:,}-tile memory safety limit; split it into separate downloads")
    return count


def selection_tiles(selections):
    selection_tile_count(selections)
    coordinates, water_coordinates = set(), set()
    for item in selections:
        zoom = int(item["max_zoom"])
        tiles = area_tiles(item["bounds"], zoom, item.get("polygon"))
        coordinates.update(tiles)
        water_coordinates.update(tile for tile in tiles if tile[0] == zoom)
    return sorted(coordinates), water_coordinates


def download_areas(selections, name, directory: Path, *, cancel=None, progress=lambda *_: None,
                   area_progress=lambda *_: None, source_factory=RangeSource, build_resolver=latest_build):
    cancel = cancel or Event()
    check_cancel(cancel)
    coordinates, water_coordinates = selection_tiles(selections)
    max_zoom = max(int(item["max_zoom"]) for item in selections)
    tracker = _AreaTileProgress(selections, area_progress)
    coordinates = tracker.ordered_coordinates()
    progress(0, len(coordinates), "Connecting to Protomaps...")
    reader, header, source = _open_basemap(max_zoom, cancel, source_factory, build_resolver)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / AREAS_FILE
    if target.exists() and target.stat().st_size > MAX_STORED_BYTES:
        raise ValueError("Saved offline areas exceed 3 GiB; remove unneeded areas first")
    if shutil.disk_usage(directory).free < (target.stat().st_size if target.exists() else 0) + 256 * 1024**2:
        raise ValueError("Insufficient disk space; allow 256 MiB plus the existing package size")
    temporary = directory / (AREAS_FILE + ".download")
    lock = _package_lock(directory)
    temporary.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            if target.exists():
                with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as existing:
                    existing.backup(db)
            else:
                db.execute("CREATE TABLE metadata (name TEXT PRIMARY KEY,value TEXT)")
                for table in ("tiles", "water_tiles"):
                    db.execute(f"CREATE TABLE {table} (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB,PRIMARY KEY(zoom_level,tile_column,tile_row))")
            db.execute("CREATE TABLE IF NOT EXISTS vector_tiles (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB,PRIMARY KEY(zoom_level,tile_column,tile_row))")
            _write_tiles(db, coordinates, water_coordinates, reader, header, temporary, cancel, progress, tracker)
            areas = saved_areas(directory)
            for index, item in enumerate(selections):
                geometry = normalize_area(item)
                areas = [area for area in areas if area_identity(area) != area_identity(geometry)]
                area_name = item.get("name", name if len(selections) == 1 else f"{name} {index + 1}")
                areas.append(dict(geometry, name=str(area_name).strip()[:64] or "Offline area",
                                  saved=datetime.now(timezone.utc).isoformat(timespec="seconds"), source=source.url))
            _record_area_sizes(db, areas, cancel)
            metadata = {"format": "png", "scheme": "tms", "attribution": ATTRIBUTION, "maritime_water_mask": "1", "maritime_water_table": "water_tiles", "maritime_areas": json.dumps(areas),
                        "maritime_deferred_water": "1", "maritime_package_id": uuid4().hex,
                        "maritime_vector_compression": "gzip"}
            db.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", metadata.items())
            db.commit()
        _replace_package(temporary, target, cancel)
        tracker.emit("Downloaded")
        progress(len(coordinates), len(coordinates), "All offline areas ready")
        return AREAS_FILE
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlock()


def _transfer_package(path, *, cancel=None, progress=lambda *_: None):
    from .offline import MBTiles
    cancel = cancel or Event()
    check_cancel(cancel)
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > MAX_STORED_BYTES:
        raise ValueError("Choose an offline-area MBTiles package smaller than 3 GiB")
    try:
        package = MBTiles(path, water_mask=True)
    except ValueError as error:
        raise ValueError(f"Offline package has invalid or missing map/water data: {error}") from error
    if package.scheme != "tms" or package.table != "water_tiles":
        raise ValueError("Choose a Maritime Ship Simulator offline-area package (TMS)")
    raw = package.metadata.get("maritime_areas", "[]")
    if len(raw) > 1024**2:
        raise ValueError("Offline area metadata is too large")
    entries = json.loads(raw)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 512:
        raise ValueError("Offline package must contain between 1 and 512 areas")
    areas, seen = [], set()
    for entry in entries:
        check_cancel(cancel)
        geometry = normalize_area(entry)
        area_tile_count(geometry["bounds"], geometry["max_zoom"], geometry.get("polygon"))
        identity = area_identity(geometry)
        if identity in seen:
            raise ValueError("Offline package contains duplicate areas")
        seen.add(identity)
        source = str(entry.get("source", ""))
        url = urlsplit(source)
        if url.scheme != "https" or url.netloc != "build.protomaps.com" or url.query or url.fragment:
            source = "Imported offline package"
        areas.append(dict(geometry, name=str(entry.get("name", "Offline area"))[:64],
                          saved=str(entry.get("saved", ""))[:64], source=source))
    with closing(package.connect()) as db:
        last_report = 0.0
        def checked():
            nonlocal last_report
            now = time.monotonic()
            if now - last_report >= 0.25:
                last_report = now
                progress(0, 0, "Checking offline map integrity...")
            return int(cancel.is_set())
        db.set_progress_handler(checked, 10_000)
        for table in ("tiles", "water_tiles") + (("vector_tiles",) if package.deferred_water else ()):
            kind = db.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
            if kind != ("table",):
                raise ValueError("Offline packages must contain indexed tile tables, not views")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Offline package failed its integrity check")
    check_cancel(cancel)
    return package, areas


def _transfer_metadata(areas, deferred):
    return {"format": "png", "scheme": "tms", "attribution": ATTRIBUTION,
            "maritime_water_mask": "1", "maritime_water_table": "water_tiles",
            "maritime_deferred_water": "1" if deferred else "0", "maritime_vector_compression": "gzip",
            "maritime_package_id": uuid4().hex, "maritime_areas": json.dumps(areas)}


def _create_transfer_tables(db):
    db.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY,value TEXT)")
    for table in ("tiles", "water_tiles", "vector_tiles"):
        db.execute(f"CREATE TABLE IF NOT EXISTS {table} (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB,PRIMARY KEY(zoom_level,tile_column,tile_row))")


def _copy_area_content(db, areas, deferred, cancel, progress, area_progress, verb):
    db.execute("CREATE TEMP TABLE transfer_needed (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,PRIMARY KEY(zoom_level,tile_column,tile_row))")
    rows = [dict(area, done=0, total=area_tile_count(area["bounds"], area["max_zoom"], area.get("polygon")), content_bytes=0, status="Queued") for area in areas]
    area_progress(rows)
    for index, area in enumerate(areas):
        check_cancel(cancel)
        rows[index] = dict(rows[index], status=verb)
        area_progress([dict(row) for row in rows])
        progress(index, len(areas), f'{verb} {area["name"]}...')
        coordinates = area_tiles(area["bounds"], area["max_zoom"], area.get("polygon"))
        content_bytes = 0
        last_report = 0.0
        for table, needed, required in (("tiles", coordinates, True),
                                        ("vector_tiles", [tile for tile in coordinates if tile[0] == area["max_zoom"]], deferred),
                                        ("water_tiles", [tile for tile in coordinates if tile[0] == area["max_zoom"]], not deferred)):
            exists = db.execute("SELECT 1 FROM incoming.sqlite_master WHERE name=? AND type='table'", (table,)).fetchone()
            if not exists:
                if required:
                    raise ValueError(f"Offline area is missing its {table} data")
                continue
            for start in range(0, len(needed), 128):
                check_cancel(cancel)
                batch = needed[start:start + 128]
                db.execute("DELETE FROM transfer_needed")
                db.executemany("INSERT INTO transfer_needed VALUES (?,?,?)", ((z,x,2**z-1-y) for z,x,y in batch))
                if required:
                    missing = db.execute(f"SELECT COUNT(*) FROM transfer_needed LEFT JOIN incoming.{table} USING (zoom_level,tile_column,tile_row) WHERE tile_data IS NULL").fetchone()[0]
                    if missing:
                        raise ValueError(f'Offline area "{area["name"]}" has {missing} missing {table} tile(s)')
                oversized = db.execute(f"SELECT 1 FROM transfer_needed CROSS JOIN incoming.{table} USING (zoom_level,tile_column,tile_row) WHERE length(tile_data)>? LIMIT 1", (8 * 1024**2,)).fetchone()
                if oversized:
                    raise ValueError("Offline package contains oversized tile data")
                # Seek only this batch; shared BLOBs already copied must not be read/written again.
                db.execute(f"INSERT OR IGNORE INTO {table} SELECT source.* FROM transfer_needed needed "
                           f"CROSS JOIN incoming.{table} source USING (zoom_level,tile_column,tile_row) "
                           f"WHERE NOT EXISTS (SELECT 1 FROM {table} existing WHERE existing.zoom_level=needed.zoom_level "
                           "AND existing.tile_column=needed.tile_column AND existing.tile_row=needed.tile_row)")
                content_bytes += db.execute(f"SELECT COALESCE(SUM(length(tile_data)),0) FROM transfer_needed CROSS JOIN {table} USING (zoom_level,tile_column,tile_row)").fetchone()[0]
                if table == "tiles":
                    rows[index]["done"] = start + len(batch)
                rows[index]["content_bytes"] = content_bytes
                now = time.monotonic()
                if now - last_report >= 0.2:
                    last_report = now
                    progress(index, len(areas), f'{verb} {area["name"]}: {rows[index]["done"]:,} / {len(coordinates):,} tiles')
                    area_progress([dict(row) for row in rows])
        area.update(tile_count=len(coordinates), content_bytes=content_bytes)
        rows[index] = dict(rows[index], done=rows[index]["total"], content_bytes=area["content_bytes"], status="Finalizing")
        area_progress([dict(row) for row in rows])
    return rows


def _export_size(package, areas, cancel, progress):
    """Measure selected, unique BLOBs without reading their content into Python."""
    with closing(package.connect()) as db:
        db.set_progress_handler(lambda: int(cancel.is_set()), 10_000)
        db.execute("CREATE TEMP TABLE export_needed (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,detail INTEGER,PRIMARY KEY(zoom_level,tile_column,tile_row))")
        for index, area in enumerate(areas):
            check_cancel(cancel)
            progress(index, len(areas), f'Checking export size: {area["name"]}...')
            coordinates = area_tiles(area["bounds"], area["max_zoom"], area.get("polygon"))
            for start in range(0, len(coordinates), 128):
                check_cancel(cancel)
                db.executemany("INSERT INTO export_needed VALUES (?,?,?,?) "
                               "ON CONFLICT(zoom_level,tile_column,tile_row) DO UPDATE SET detail=MAX(detail,excluded.detail)",
                               ((z,x,2**z-1-y,int(z == area["max_zoom"])) for z,x,y in coordinates[start:start+128]))
        size, count = 0, 0
        for table in ("tiles", "vector_tiles", "water_tiles"):
            check_cancel(cancel)
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)).fetchone():
                continue
            where = " WHERE detail=1" if table != "tiles" else ""
            rows, payload = db.execute(f"SELECT COUNT(*),COALESCE(SUM(length(tile_data)),0) "
                                      f"FROM export_needed CROSS JOIN {table} USING (zoom_level,tile_column,tile_row)" + where).fetchone()
            count += rows
            size += payload
    check_cancel(cancel)
    # Include B-tree/page slack and metadata; keep a separate 64 MiB free-space reserve.
    return size + size // 4 + count * 128 + 64 * 1024 + len(json.dumps(areas).encode("utf-8"))


def export_areas(directory, selected, destination, *, cancel=None, progress=lambda *_: None, area_progress=lambda *_: None):
    cancel = cancel or Event()
    directory = Path(directory).resolve()
    source = directory / AREAS_FILE
    destination = Path(destination).resolve()
    if destination == source:
        raise ValueError("Export to a separate file, not the active offline package")
    lock = _package_lock(directory)
    temporary = destination.with_name(destination.name + ".export")
    try:
        progress(0, 0, "Checking saved maps before export...")
        current = saved_areas(directory)
        if not selected or any(area not in current for area in selected):
            raise ValueError("Selected areas changed; refresh the list before exporting")
        if len({area_identity(area) for area in selected}) != len(selected):
            raise ValueError("Select each exported area only once")
        area_progress([dict(area, done=0, total=area.get("tile_count", 0), content_bytes=0, status="Checking") for area in selected])
        package, clean = _transfer_package(source, cancel=cancel, progress=progress)
        clean_by_bounds = {area_identity(area): area for area in clean}
        exported = [dict(clean_by_bounds[area_identity(area)]) for area in selected]
        required = min(source.stat().st_size, _export_size(package, exported, cancel, progress)) + 64 * 1024**2
        progress(0, 0, "Preparing destination drive...")
        if shutil.disk_usage(destination.parent).free < required:
            raise ValueError("Insufficient temporary space to export the maps safely")
        temporary.unlink(missing_ok=True)
        with closing(sqlite3.connect(temporary, uri=True)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            db.set_progress_handler(lambda: int(cancel.is_set()), 10_000)
            db.execute("ATTACH DATABASE ? AS incoming", (source.as_uri() + "?mode=ro",))
            _create_transfer_tables(db)
            rows = _copy_area_content(db, exported, package.deferred_water, cancel, progress, area_progress, "Exporting")
            db.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", _transfer_metadata(exported, package.deferred_water).items())
            db.commit()
        _replace_package(temporary, destination, cancel)
        area_progress([dict(row, status="Exported") for row in rows])
        progress(len(exported), len(exported), "Selected areas exported")
        return str(destination)
    except (sqlite3.Error, OSError) as error:
        check_cancel(cancel)
        raise ValueError(f"Cannot export offline maps to {destination}: {error}. "
                         "Check that the destination drive is connected, writable and has enough free space.") from error
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlock()


def import_areas(directory, source, *, cancel=None, progress=lambda *_: None, area_progress=lambda *_: None, preview=lambda *_: None):
    cancel = cancel or Event()
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source = Path(source).resolve()
    target = directory / AREAS_FILE
    if source == target:
        raise ValueError("This is already the active offline package")
    lock = _package_lock(directory)
    temporary = directory / (AREAS_FILE + ".import")
    try:
        progress(0, 0, "Checking imported maps...")
        package, incoming = _transfer_package(source, cancel=cancel, progress=progress)
        current = _transfer_package(target, cancel=cancel, progress=progress)[1] if target.exists() else []
        identities = {area_identity(area) for area in current}
        added = [area for area in incoming if area_identity(area) not in identities]
        skipped = len(incoming) - len(added)
        if not added:
            return AREAS_FILE, 0, skipped
        if len(current) + len(added) > 512:
            raise ValueError("The combined package would exceed 512 areas")
        size = source.stat().st_size + (target.stat().st_size if target.exists() else 0)
        if shutil.disk_usage(directory).free < size + 64 * 1024**2:
            raise ValueError("Insufficient temporary space to import maps safely")
        preview(added)
        temporary.unlink(missing_ok=True)
        with closing(sqlite3.connect(temporary, uri=True)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            if target.exists():
                with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as existing:
                    existing.backup(db)
            db.set_progress_handler(lambda: int(cancel.is_set()), 10_000)
            deferred = package.deferred_water
            if target.exists():
                deferred = deferred or dict(db.execute("SELECT name,value FROM metadata")).get("maritime_deferred_water") == "1"
            _create_transfer_tables(db)
            db.execute("ATTACH DATABASE ? AS incoming", (source.as_uri() + "?mode=ro",))
            rows = _copy_area_content(db, added, package.deferred_water, cancel, progress, area_progress, "Importing")
            all_areas = current + added
            _record_area_sizes(db, all_areas, cancel)
            # Replace metadata with a whitelist; credentials/unrecognized fields never transfer.
            db.execute("PRAGMA secure_delete=ON")
            db.execute("DELETE FROM metadata")
            db.executemany("INSERT INTO metadata VALUES (?,?)", _transfer_metadata(all_areas, deferred).items())
            db.commit()
        _replace_package(temporary, target, cancel)
        area_progress([dict(row, status="Downloaded") for row in rows])
        progress(len(added), len(added), f"Imported {len(added)} area(s); kept {skipped} existing area(s)")
        return AREAS_FILE, len(added), skipped
    except sqlite3.OperationalError:
        check_cancel(cancel)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlock()
