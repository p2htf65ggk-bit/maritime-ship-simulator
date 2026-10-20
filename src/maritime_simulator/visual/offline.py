# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
from threading import BoundedSemaphore, RLock, Thread
from urllib.parse import parse_qs, urlsplit

from .s57 import layer_selection, safety_value


@dataclass(frozen=True, slots=True)
class OfflineSettings:
    enabled: bool = False
    map_file: str = ""
    water_file: str = ""
    map_source: str = "auto"
    enc_layers: tuple[str, ...] | None = None
    enc_base: bool = False
    enc_cells: tuple[str, ...] | None = None
    enc_safety_contour_m: float = 30.0
    enc_safety_depth_m: float = 30.0

    @classmethod
    def from_payload(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        # Package filenames, not host-specific absolute paths, travel in configs.
        def filename(key):
            value = str(payload.get(key, ""))
            if value and (Path(value).name != value or "/" in value or "\\" in value):
                raise ValueError("Offline package names must not contain directories")
            return value
        source = str(payload.get("map_source", "auto"))
        if source not in {"auto", "online", "protomaps", "s57"}:
            raise ValueError("Unknown 2D map source")
        layers = layer_selection(payload.get("enc_layers"))
        cells = payload.get("enc_cells")
        if cells is not None:
            if not isinstance(cells, (list, tuple)) or len(cells) > 20_000 or any(not isinstance(v, str) or not re.fullmatch(r"[A-Z0-9_-]{1,32}", v) for v in cells):
                raise ValueError("Invalid S57 cell selection")
            cells = tuple(sorted(set(cells)))
        return cls(bool(payload.get("enabled", False)), filename("map_file"), filename("water_file"), source, layers,
                   bool(payload.get("enc_base", False)), cells,
                   safety_value(payload.get("enc_safety_contour_m", 30)),
                   safety_value(payload.get("enc_safety_depth_m", 30)))

    def payload(self):
        return {"enabled": self.enabled, "map_file": self.map_file, "water_file": self.water_file,
                "map_source": self.map_source, "enc_layers": self.enc_layers, "enc_base": self.enc_base, "enc_cells": self.enc_cells,
                "enc_safety_contour_m": self.enc_safety_contour_m, "enc_safety_depth_m": self.enc_safety_depth_m}


class MBTiles:
    """Read-only raster MBTiles; each query owns its connection, never the UI thread's."""

    MAX_TILE_BYTES = 2 * 1024 * 1024

    @staticmethod
    def _zoom_levels(db, table):
        # DISTINCT walks the whole tile index; seek the first key of each level instead.
        levels = []
        row = db.execute(f"SELECT zoom_level FROM {table} ORDER BY zoom_level LIMIT 1").fetchone()
        while row:
            level = row[0]
            if not isinstance(level, int) or not 0 <= level <= 19:
                raise ValueError("Empty or invalid raster MBTiles zoom range")
            levels.append(level)
            row = db.execute(f"SELECT zoom_level FROM {table} WHERE zoom_level>? ORDER BY zoom_level LIMIT 1", (level,)).fetchone()
        return list(reversed(levels))

    def __init__(self, path: Path, *, water_mask=False):
        self.path = Path(path).resolve()
        info = self.path.stat()
        self.signature = (info.st_size, info.st_mtime_ns)
        with closing(self.connect()) as db:
            db.execute("PRAGMA query_only=ON")
            self.metadata = dict(db.execute("SELECT name, value FROM metadata"))
            self.deferred_water = water_mask and self.metadata.get("maritime_deferred_water") == "1"
            self.package_id = self.metadata.get("maritime_package_id", "")
            if self.deferred_water and not re.fullmatch(r"[0-9a-f]{32}", self.package_id):
                raise ValueError("Invalid deferred water-mask package ID")
            self.table = "water_tiles" if water_mask and self.metadata.get("maritime_water_table") == "water_tiles" else "tiles"
            self.zoom_levels = self._zoom_levels(db, self.table)
            self.min_zoom, self.max_zoom = (self.zoom_levels[-1], self.zoom_levels[0]) if self.zoom_levels else (None, None)
            sample = db.execute(f"SELECT tile_data FROM {self.table} LIMIT 1").fetchone()
            if self.deferred_water:
                vector_levels = self._zoom_levels(db, "vector_tiles")
                if vector_levels:
                    self.min_zoom = min(self.min_zoom if self.min_zoom is not None else 19, vector_levels[-1])
                    self.max_zoom = max(self.max_zoom if self.max_zoom is not None else 0, vector_levels[0])
                    self.zoom_levels = sorted(set(self.zoom_levels) | set(vector_levels), reverse=True)
                if sample is None:
                    sample = db.execute("SELECT tile_data FROM tiles LIMIT 1").fetchone()
                    deferred_sample = True
                else:
                    deferred_sample = False
            indices = db.execute(f"PRAGMA index_list('{self.table}')").fetchall()
            # Views used by normalized MBTiles are also supported.
            is_view = db.execute("SELECT type FROM sqlite_master WHERE name=?", (self.table,)).fetchone()
            indexed_columns = [tuple(row[2] for row in db.execute(f"PRAGMA index_info('{str(index[1]).replace(chr(39), chr(39) * 2)}')")) for index in indices]
            if not any(columns[:3] == ("zoom_level", "tile_column", "tile_row") for columns in indexed_columns) and is_view and is_view[0] != "view":
                raise ValueError("MBTiles needs an index on zoom_level, tile_column, tile_row")
        if sample is None or self.min_zoom is None or not 0 <= self.min_zoom <= self.max_zoom <= 19:
            raise ValueError("Empty or invalid raster MBTiles zoom range")
        if self.metadata.get("format", "png").lower() not in {"png", "jpg", "jpeg", "webp"}:
            raise ValueError("Use raster MBTiles (PNG/JPEG/WebP), not vector PBF tiles")
        self.water_mask = water_mask and not (self.deferred_water and deferred_sample)
        if water_mask and self.metadata.get("maritime_water_mask") != "1":
            raise ValueError("Water MBTiles must declare maritime_water_mask=1 (white=water, black=land)")
        payload = bytes(sample[0])
        if len(payload) > self.MAX_TILE_BYTES or not self.valid_raster(payload):
            raise ValueError("Invalid or oversized raster tile")
        self.water_mask = water_mask
        self.scheme = self.metadata.get("scheme", "tms").lower()
        if self.scheme not in {"tms", "xyz"}:
            raise ValueError("Unsupported MBTiles tile-row scheme")

    def connect(self):
        return sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.5)

    def valid_raster(self, data):
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice
        from PySide6.QtGui import QImageReader
        if not self.mime(data):
            return False
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.ReadOnly)
        dimensions = QImageReader(buffer).size()
        if self.water_mask:
            return dimensions.width() == 256 and dimensions.height() == 256
        return 0 < dimensions.width() <= 1024 and 0 < dimensions.height() <= 1024

    @staticmethod
    def mime(data):
        if not data:
            return None
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8"):
            return "image/jpeg"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    def tile(self, z, x, y):
        if not self.min_zoom <= z <= self.max_zoom or not 0 <= x < 2**z or not 0 <= y < 2**z:
            return None
        row = 2**z - 1 - y if self.scheme == "tms" else y
        with closing(self.connect()) as db:
            result = db.execute(
                f"SELECT tile_data FROM {self.table} WHERE zoom_level=? AND tile_column=? AND tile_row=? LIMIT 1",
                (z, x, row),
            ).fetchone()
        data = bytes(result[0]) if result else None
        return data if data and len(data) <= self.MAX_TILE_BYTES and self.valid_raster(data) else None

    def vector_tile(self, z, x, y):
        if not self.deferred_water or not self.min_zoom <= z <= self.max_zoom:
            return None
        with closing(self.connect()) as db:
            result = db.execute("SELECT tile_data FROM vector_tiles WHERE zoom_level=? AND tile_column=? AND tile_row=? LIMIT 1",
                                (z, x, 2**z - 1 - y)).fetchone()
        return bytes(result[0]) if result and len(result[0]) <= 8 * 1024**2 else None

    def display_tile(self, z, x, y):
        data = self.tile(z, x, y)
        if data or self.water_mask:
            return data
        # Different rectangles can have different finest levels in the same package.
        # Enlarge the nearest saved parent, but do not treat world overview as coverage.
        areas = json.loads(self.metadata.get("maritime_areas", "[]"))
        if not areas:
            return None
        finest_minimum = min(int(area["max_zoom"]) for area in areas)
        from PySide6.QtCore import QBuffer, QIODevice, Qt
        from PySide6.QtGui import QImage
        for parent_zoom in range(min(z - 1, self.max_zoom), finest_minimum - 1, -1):
            scale = 2 ** (z - parent_zoom)
            data = self.tile(parent_zoom, x // scale, y // scale)
            if not data:
                continue
            image = QImage.fromData(data)
            width, height = max(1, image.width() // scale), max(1, image.height() // scale)
            crop = image.copy((x % scale) * width, (y % scale) * height, width, height)
            enlarged = crop.scaled(image.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if enlarged.save(buffer, "PNG"):
                return bytes(buffer.data())
        return None


def install_mbtiles(source: Path, directory: Path, *, water_mask=False) -> str:
    MBTiles(source, water_mask=water_mask)
    directory.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(source).stem)[:80] or "area"
    suffix = "-water" if water_mask else "-map"
    name += ("" if name.endswith(suffix) else suffix) + ".mbtiles"
    target = directory / name
    if Path(source).resolve() == target.resolve():
        return name
    temporary = target.with_suffix(".mbtiles.tmp")
    try:
        shutil.copyfile(source, temporary)
        MBTiles(temporary, water_mask=water_mask)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return name


class OfflineTileServer:
    """Loopback-only, bounded tile service. Never proxies or downloads remote tiles."""

    def __init__(self, coastline_mask, enc_store=None):
        self.mask = coastline_mask
        self.enc_store = enc_store
        self.package = None
        self._lock = RLock()
        self._render_lock = RLock()
        self._tile_revision = 0
        self._tiles = OrderedDict()
        self._cache_bytes = 0
        self._tile_cache_limit = 16 * 1024**2
        self._enc_cache_limit = 16 * 1024**2
        self._enc_lock = RLock()
        self._enc_cache = OrderedDict()
        self._enc_revision = 0
        self.token = secrets.token_hex(16)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == f"/{owner.token}/enc":
                    try:
                        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8)
                        bounds = tuple(float(v) for v in query["bbox"][0].split(","))
                        if len(bounds) != 4:
                            raise ValueError("Invalid chart viewport")
                        zoom = float(query["zoom"][0])
                        layers = query.get("layers", ["*"])[0]
                        display = query.get("display", ["all"])[0]
                        if display not in {"all", "base"}:
                            raise ValueError("Invalid S57 display mode")
                        data = owner.chart_features(bounds, zoom, None if layers == "*" else tuple(filter(None, layers.split(","))),
                                                    base=display == "base",
                                                    safety_contour=safety_value(query.get("contour", [30])[0]),
                                                    safety_depth=safety_value(query.get("depth", [30])[0]))
                    except (KeyError, ValueError, OSError, sqlite3.Error) as error:
                        data = json.dumps({"error": f"S57 chart request: {error}"}).encode("utf-8")
                        self.send_response(400)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        try:
                            self.wfile.write(data)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        return
                    self.send_response(200)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Type", "application/geo+json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                match = re.fullmatch(r"/" + owner.token + r"/(\d{1,2})/(\d+)/(\d+)\.png", urlsplit(self.path).path)
                if not match:
                    self.send_error(404)
                    return
                try:
                    data = owner.tile(*(int(value) for value in match.groups()))
                except (OSError, ValueError, sqlite3.Error):
                    data = None
                self.send_response(200 if data else 404)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", MBTiles.mime(data) if data else "text/plain")
                self.send_header("Content-Length", str(len(data) if data else 0))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if data:
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            def log_message(self, *_args):
                pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            slots = BoundedSemaphore(8)
            def process_request(self, request, address):
                if not self.slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, address)
                except Exception:
                    self.slots.release()
                    raise

            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    self.slots.release()
            # Serialize the bounded renderer/cache work, while keeping HTTP off Qt.
            def verify_request(self, request, address):
                request.settimeout(5.0)
                return address[0] == "127.0.0.1"

        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/{self.token}/{{z}}/{{x}}/{{y}}.png"

    @property
    def enc_url(self):
        return f"http://127.0.0.1:{self.server.server_port}/{self.token}/enc"

    def chart_features(self, bounds, zoom, layers, *, base=False, safety_contour=30.0, safety_depth=30.0):
        safety_contour, safety_depth = safety_value(safety_contour), safety_value(safety_depth)
        key = (self._enc_revision, bounds, zoom, layers, base, safety_contour, safety_depth)
        with self._enc_lock:
            if key in self._enc_cache:
                self._enc_cache.move_to_end(key)
                return self._enc_cache[key]
            if self.enc_store is None:
                raise ValueError("No S57 store")
            result = self.enc_store.features(bounds, zoom, layers, base=base,
                                            safety_contour=safety_contour, safety_depth=safety_depth)
            data = json.dumps(result, separators=(",", ":")).encode("utf-8")
            if not result.get("limited"):
                self._enc_cache[key] = data
            while len(self._enc_cache) > 4 or sum(map(len, self._enc_cache.values())) > self._enc_cache_limit:
                self._enc_cache.popitem(last=False)
            return data

    def select(self, package):
        with self._lock:
            if self.package is not package:
                self._tiles.clear()
                self._cache_bytes = 0
                self._tile_revision += 1
            self.package = package
        # The UI must not wait for a background chart query to finish.
        self._enc_revision += 1

    def tile(self, z, x, y):
        if not 0 <= z <= 19 or not 0 <= x < 2**z or not 0 <= y < 2**z:
            return None
        key = (z, x, y)
        with self._lock:
            if key in self._tiles:
                self._tiles.move_to_end(key)
                return self._tiles[key]
            package, revision = self.package, self._tile_revision
        # Renderer serialization belongs to workers, not the state/cache lock used by Qt.
        with self._render_lock:
            with self._lock:
                if revision == self._tile_revision and key in self._tiles:
                    self._tiles.move_to_end(key)
                    return self._tiles[key]
            data = package.display_tile(z, x, y) if package else self.mask.coastline_tile(z, x, y)
            if package and data is None:
                data = self.mask.coastline_tile(z, x, y)
            with self._lock:
                if data and revision == self._tile_revision:
                    self._tiles[key] = data
                    self._cache_bytes += len(data)
                    while len(self._tiles) > 96 or self._cache_bytes > self._tile_cache_limit:
                        _, removed = self._tiles.popitem(last=False)
                        self._cache_bytes -= len(removed)
            return data

    def apply_memory_budget(self, raster_bytes, enc_bytes):
        self.set_memory_limits(raster_bytes, enc_bytes)
        usage = {}
        # Never wait behind a chart query or tile render, even on the sampler thread.
        if self._lock.acquire(blocking=False):
            try:
                while self._tiles and self._cache_bytes > self._tile_cache_limit:
                    _, removed = self._tiles.popitem(last=False)
                    self._cache_bytes -= len(removed)
                usage["raster"] = self._cache_bytes
            finally:
                self._lock.release()
        if self._enc_lock.acquire(blocking=False):
            try:
                size = sum(map(len, self._enc_cache.values()))
                while self._enc_cache and size > self._enc_cache_limit:
                    _, removed = self._enc_cache.popitem(last=False)
                    size -= len(removed)
                usage["enc"] = size
            finally:
                self._enc_lock.release()
        return usage

    def set_memory_limits(self, raster_bytes, enc_bytes):
        self._tile_cache_limit = max(0, int(raster_bytes))
        self._enc_cache_limit = max(0, int(enc_bytes))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
        with self._lock:
            self._tiles.clear()
            self._cache_bytes = 0
            self._tile_revision += 1
        with self._enc_lock:
            self._enc_cache.clear()


def export_offline_package(directory: Path, settings: OfflineSettings, destination: Path):
    """Portable ZIP with explicit files only, no config credentials or cache harvesting."""
    import zipfile
    settings = OfflineSettings.from_payload(settings.payload())
    destination = Path(destination)
    for name in {settings.map_file, settings.water_file} - {""}:
        if not (directory / name).is_file():
            raise ValueError(f"Offline package is missing: {name}")
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("offline.json", json.dumps(settings.payload(), indent=2))
            for name in {settings.map_file, settings.water_file} - {""}:
                archive.write(directory / name, name)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def import_offline_package(source: Path, directory: Path) -> OfflineSettings:
    import tempfile
    import zipfile
    with tempfile.TemporaryDirectory() as work, zipfile.ZipFile(source) as archive:
        info = archive.getinfo("offline.json")
        if info.file_size > 8192:
            raise ValueError("Invalid offline package manifest")
        settings = OfflineSettings.from_payload(json.loads(archive.read(info)))
        # Only listed MBTiles are extracted; ignore unrelated ZIP entries.
        files = []
        for name, water in ((settings.map_file, False), (settings.water_file, True)):
            if not name:
                continue
            item = archive.getinfo(name)
            if item.file_size > 4 * 1024**3:
                raise ValueError("Offline packages must be smaller than 4 GB per file")
            target = Path(work) / name
            with archive.open(item) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            MBTiles(target, water_mask=water)
            files.append((target, water))
        names = {}
        for path, water in files:
            names[water] = install_mbtiles(path, directory, water_mask=water)
        return replace(settings, map_file=names.get(False, ""), water_file=names.get(True, ""))
