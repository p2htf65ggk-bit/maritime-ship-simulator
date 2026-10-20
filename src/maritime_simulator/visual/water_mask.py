# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
import math
import mmap
from pathlib import Path
import struct
from threading import RLock
import time
from urllib import request
import zipfile

from maritime_simulator.visual.geodata import verified_ssl_context


WEB_MERCATOR_RADIUS_M = 6_378_137.0
WEB_MERCATOR_LIMIT_DEG = 85.05112878
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


class OSMNavigableWaterMask:
    """Offline coastline mask with a small asynchronous OSM inland-water cache."""

    def __init__(
        self,
        land_archive: Path,
        cache_dir: Path,
        *,
        tile_zoom: int = 9,
        tile_url: str = OSM_TILE_URL,
        allow_network: bool = True,
        tile_cache_max_bytes: int = 64 * 1024 * 1024,
        tile_image_limit: int = 64,
        enc_tile_image_limit: int = 512,
    ):
        self.land_archive = Path(land_archive)
        self.cache_dir = Path(cache_dir)
        self.tile_zoom = max(6, min(15, int(tile_zoom)))
        self.tile_url = str(tile_url)
        self.allow_network = bool(allow_network)
        self.tile_cache_max_bytes = max(1024 * 1024, int(tile_cache_max_bytes))
        self.tile_image_limit = max(8, int(tile_image_limit))
        self.enc_tile_image_limit = max(8, min(1024, int(enc_tile_image_limit)))
        self._land: _ShapefileLandIndex | None = None
        self._land_error = ""
        self._tile_images: OrderedDict[tuple[int, int, int], object] = OrderedDict()
        self._coastline_results: OrderedDict[tuple[int, int, int, int, int], bool] = OrderedDict()
        self._tile_pending: dict[tuple[int, int, int], Future] = {}
        self._tile_retry: OrderedDict[tuple[int, int, int], float] = OrderedDict()
        self._last_prune_s = time.monotonic()
        self._prune_lock = RLock()
        self._offline_package = None
        self._offline_missing = OrderedDict()
        self._offline_pending = {}
        self._offline_error = ""
        self._enc_store = None
        self._enc_source = None
        self._enc_images = OrderedDict()
        self._enc_lock = RLock()
        self._enc_pending = {}
        self._enc_missing = OrderedDict()
        self._enc_error = ""
        self._enc_region = None
        self._enc_region_keys = ()
        self._enc_region_key_set = frozenset()
        self._enc_region_pumping = False
        self._enc_region_images = OrderedDict()
        self._enc_region_missing = OrderedDict()
        self._enc_region_pending = {}
        self._memory_limit = 56 * 1024**2
        self._enc_memory_bytes = 0
        self._tile_memory_bytes = 0
        self._coast_memory_limit = 6 * 1024**2
        self._closed = False
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="osm-water-mask")
        self._enc_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="enc-water-mask")

    @property
    def status(self) -> str:
        with self._enc_lock:
            if self._enc_store is not None and self._enc_region_keys:
                done = sum(self._enc_region_complete(key) for key in self._enc_region_keys)
                if done < len(self._enc_region_keys):
                    return f"Preparing AIS water area: {done}/{len(self._enc_region_keys)} tiles"
                failed = sum(isinstance(self._enc_region_missing.get(key), float) for key in self._enc_region_keys)
                if failed:
                    return f"AIS water area prepared; {failed} tile(s) unavailable"
        if self._land_error:
            return f"Land mask unavailable: {self._land_error}"
        if not self._lock.acquire(blocking=False):
            return "Preparing local coastline background"
        try:
            local_pending = len(self._offline_pending) + len(self._enc_pending)
            pending = len(self._tile_pending)
        finally:
            self._lock.release()
        if local_pending:
            return f"Preparing {local_pending} local water-mask tile(s)"
        if self._offline_error:
            return f"Offline water-mask refinement unavailable: {self._offline_error}"
        if self._enc_error:
            return f"S57 water-mask refinement unavailable: {self._enc_error}"
        return "Water mask ready" if not pending else f"Water mask loading {pending} inland tile(s)"

    def is_water(self, latitude_deg: float, longitude_deg: float) -> bool | None:
        # ENC reads/loads must remain independent of the schematic map renderer's lock.
        known, water = self._enc_water(latitude_deg, longitude_deg)
        if known:
            return water
        # A background coastline render must not stall the simulation/UI's avoidance checks.
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._is_water(latitude_deg, longitude_deg)
        finally:
            self._lock.release()

    def _is_water(self, latitude_deg: float, longitude_deg: float) -> bool | None:
        if self._closed:
            return None
        if self._offline_package is not None:
            from PySide6.QtGui import QImage
            package = self._offline_package
            for zoom in package.zoom_levels:
                key, pixel = _tile_coordinate(latitude_deg, longitude_deg, zoom)
                with self._lock:
                    image = self._tile_images.get(("offline", *key))
                    if image is not None:
                        self._tile_images.move_to_end(("offline", *key))
                    missing = self._offline_missing.get(key, False)
                    local_pending = key in self._offline_pending
                if local_pending:
                    return None
                if isinstance(missing, float) and time.monotonic() >= missing:
                    missing = False
                if image is None and not missing:
                    data = package.tile(*key)
                    if data:
                        image = QImage.fromData(data)
                        with self._lock:
                            self._remember_tile_image(("offline", *key), image)
                    else:
                        if package.deferred_water:
                            self._queue_offline_tile(package, key)
                            return None
                        with self._lock:
                            self._offline_missing[key] = True
                            self._offline_missing.move_to_end(key)
                            while len(self._offline_missing) > 512:
                                self._offline_missing.popitem(last=False)
                if image is not None and not image.isNull():
                    color = image.pixelColor(*pixel)
                    if color.alpha() > 127:
                        return color.red() > 127
        tile_key, pixel = _tile_coordinate(latitude_deg, longitude_deg, self.tile_zoom)
        with self._lock:
            image = self._tile_images.get(tile_key)
            if image is not None:
                self._tile_images.move_to_end(tile_key)
                return _classify_image_pixel(image, pixel)
            mask_key = (*tile_key, *pixel)
            cached = self._coastline_results.get(mask_key)
            if cached is not None:
                self._coastline_results.move_to_end(mask_key)
                return cached
        with self._lock:
            land = self._land_index()
            if land is None:
                return None
            if not land.contains(latitude_deg, longitude_deg):
                self._remember_coastline_result(mask_key, True)
                return True
        return self._inland_water(latitude_deg, longitude_deg)

    def _queue_offline_tile(self, package, key):
        with self._lock:
            if self._closed or key in self._offline_pending or self._pending_count() >= 16:
                return
            future = self._executor.submit(self._prepare_offline_tile, package, key)
            self._offline_pending[key] = future
            future.add_done_callback(lambda done, source=package, tile=key: self._offline_tile_finished(source, tile, done))

    def _pending_count(self):
        return len(self._offline_pending) + len(self._tile_pending) + len(self._enc_pending)

    def set_s57(self, store):
        source = (store, store.cache_revision()) if store is not None else None
        if source == self._enc_source:
            return
        with self._enc_lock:
            self._enc_store, self._enc_source = store, source
            for future in tuple(self._enc_pending.values()):
                future.cancel()
            for future in tuple(self._enc_region_pending.values()):
                future.cancel()
            self._enc_pending.clear()
            self._enc_region_pending.clear()
            self._enc_missing.clear()
            self._enc_error = ""
            self._enc_images.clear()
            self._enc_region = None
            self._enc_region_keys = ()
            self._enc_region_key_set = frozenset()
            self._enc_region_images.clear()
            self._enc_memory_bytes = 0
            self._enc_region_missing.clear()

    def prepare_ais_region(self, latitude, longitude, radius_nm):
        """Warm the AIS circle from raw ENC detail, not from displayed chart cells."""
        with self._enc_lock:
            if self._closed or self._enc_store is None:
                return True
            region, keys = _enc_region_tiles(latitude, longitude, radius_nm)
            if region != self._enc_region:
                self._enc_region, self._enc_region_keys = region, keys
                self._enc_region_key_set = frozenset(keys)
                # Keep completed overlap, but do not keep loading an abandoned region.
                for key, future in tuple(self._enc_region_pending.items()):
                    if key not in self._enc_region_key_set:
                        future.cancel()
            self._pump_enc_region()
            return all(self._enc_region_complete(key) for key in keys)

    def _enc_region_complete(self, key):
        retry = self._enc_region_missing.get(key, 0)
        return key in self._enc_region_images or retry is True or (retry and time.monotonic() < retry)

    def _pump_enc_region(self):
        from maritime_simulator.visual.s57_water import render_water_tile
        if self._closed or self._enc_store is None or self._enc_region_pumping:
            return
        # At most two background jobs; point/hull requests keep their own bounded queue.
        self._enc_region_pumping = True
        try:
            for key in self._enc_region_keys:
                if len(self._enc_region_pending) >= 2:
                    break
                if self._enc_region_complete(key) or key in self._enc_region_pending:
                    continue
                image = self._enc_images.get(key)
                if image is not None:
                    self._remember_enc_region_image(key, image)
                    continue
                source = self._enc_source
                future = self._enc_executor.submit(render_water_tile, self._enc_store, *key)
                self._enc_region_pending[key] = future
                future.add_done_callback(lambda done, token=source, tile=key: self._enc_region_finished(token, tile, done))
        finally:
            self._enc_region_pumping = False

    def _remember_enc_region_image(self, key, image):
        cached = self._enc_images.get(key)
        if cached is not None:
            image = cached
        self._enc_region_images[key] = image
        self._enc_region_images.move_to_end(key)
        while len(self._enc_region_images) > 512:
            # Retain the current sampling area, rather than evicting its first tiles.
            victim = next((tile for tile in self._enc_region_images if tile not in self._enc_region_key_set),
                          next(iter(self._enc_region_images)))
            self._enc_region_images.pop(victim)
        self._trim_enc_images()

    def _trim_enc_images(self):
        images = [*self._enc_region_images.values(), *self._enc_images.values()]
        unique = {image.cacheKey(): image for image in images}
        references = Counter(image.cacheKey() for image in images)
        size = sum(image.sizeInBytes() + 1024 for image in unique.values())
        for key in dict.fromkeys([*self._enc_region_images, *self._enc_images]):
            if size <= self._memory_limit:
                break
            if key in self._enc_region_key_set:
                continue
            for cache in (self._enc_images, self._enc_region_images):
                image = cache.pop(key, None)
                if image is not None:
                    references[image.cacheKey()] -= 1
                    if not references[image.cacheKey()]:
                        size -= image.sizeInBytes() + 1024
        self._enc_memory_bytes = size
        return size

    def apply_memory_budget(self, water_bytes, coast_bytes):
        self._memory_limit = max(0, int(water_bytes))
        self._coast_memory_limit = max(0, int(coast_bytes))
        usage = {}
        # The active region is a protected working set, not a zoom-dependent cache.
        if self._enc_lock.acquire(blocking=False):
            try:
                enc_size = self._trim_enc_images()
                usage["water_enc"] = enc_size
            finally:
                self._enc_lock.release()
        else:
            enc_size = self._memory_limit
        if self._lock.acquire(blocking=False):
            try:
                limit = max(0, self._memory_limit - enc_size)
                size = sum(image.sizeInBytes() + 1024 for image in self._tile_images.values())
                while self._tile_images and size > limit:
                    _, image = self._tile_images.popitem(last=False)
                    size -= image.sizeInBytes() + 1024
                self._tile_memory_bytes = size
                usage["water_tiles"] = size
                if self._land is not None:
                    usage["coast"] = self._land.apply_memory_budget(self._coast_memory_limit)
            finally:
                self._lock.release()
        return usage

    def _enc_region_finished(self, source, key, future):
        try:
            image, error = future.result(), ""
        except CancelledError:
            image, error = None, ""
        except Exception as failure:
            image, error = None, str(failure)[:160]
        with self._enc_lock:
            if self._enc_region_pending.get(key) is future:
                self._enc_region_pending.pop(key, None)
            if self._closed or source != self._enc_source:
                return
            if not future.cancelled():
                if image is not None:
                    self._remember_enc_region_image(key, image)
                    self._enc_region_missing.pop(key, None)
                else:
                    self._enc_region_missing[key] = time.monotonic() + 60 if error else True
                    self._enc_region_missing.move_to_end(key)
                    while len(self._enc_region_missing) > 1024:
                        self._enc_region_missing.popitem(last=False)
                    if error:
                        self._enc_error = error
            self._pump_enc_region()

    def _enc_water(self, latitude, longitude):
        from maritime_simulator.visual.s57_water import render_water_tile
        key, pixel = _tile_coordinate(latitude, longitude, 15)
        with self._enc_lock:
            if self._closed:
                return True, None
            if self._enc_store is None:
                return False, None
            image = self._enc_images.get(key)
            if image is None:
                image = self._enc_region_images.get(key)
                if image is not None:
                    self._enc_images[key] = image
                    while len(self._enc_images) > self.enc_tile_image_limit:
                        self._enc_images.popitem(last=False)
            if image is not None:
                if key in self._enc_images:
                    self._enc_images.move_to_end(key)
                color = image.pixelColor(*pixel)
                return True, color.red() > 127 if color.alpha() > 127 else None
            missing = self._enc_missing.get(key, False)
            if missing is True:
                return False, None
            if missing and time.monotonic() < missing:
                return True, None
            if key not in self._enc_pending and len(self._enc_pending) < 16:
                source = self._enc_source
                future = self._enc_executor.submit(render_water_tile, self._enc_store, *key)
                self._enc_pending[key] = future
                future.add_done_callback(lambda done, token=source, tile=key: self._enc_tile_finished(token, tile, done))
            return True, None

    def _enc_tile_finished(self, source, key, future):
        try:
            image, error = future.result(), ""
        except CancelledError:
            image, error = None, ""
        except Exception as failure:
            image, error = None, str(failure)[:160]
        with self._enc_lock:
            if self._enc_pending.get(key) is future:
                self._enc_pending.pop(key, None)
            if self._closed or source != self._enc_source:
                return
            if image is not None:
                cached = self._enc_region_images.get(key)
                if cached is not None:
                    image = cached
                self._enc_images[key] = image
                self._enc_images.move_to_end(key)
                while len(self._enc_images) > self.enc_tile_image_limit:
                    self._enc_images.popitem(last=False)
                self._trim_enc_images()
                self._enc_missing.pop(key, None)
                self._enc_error = ""
            else:
                self._enc_missing[key] = time.monotonic() + 60 if error else True
                self._enc_error = error
                while len(self._enc_missing) > 512:
                    self._enc_missing.popitem(last=False)

    def _prepare_offline_tile(self, package, key):
        from PySide6.QtGui import QImage
        from maritime_simulator.visual.offline_areas import rasterize_water_mask
        path = self.cache_dir / "offline-water-tiles" / package.package_id / str(key[0]) / str(key[1]) / f"{key[2]}.png"
        image = _load_tile_image(path) if path.is_file() else None
        if image is not None:
            return image
        data = package.vector_tile(*key)
        if data is None or self._closed:
            return None
        rendered = rasterize_water_mask(data, compressed=package.metadata.get("maritime_vector_compression") == "gzip")
        image = QImage.fromData(rendered)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            import tempfile
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(rendered)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            self._prune_caches()
        except OSError:
            pass  # A full/read-only disk must not prevent in-memory avoidance.
        return image

    def _prune_caches(self):
        with self._prune_lock:
            now = time.monotonic()
            if now - self._last_prune_s >= 60.0:
                for name in ("osm-water-tiles", "offline-water-tiles"):
                    _prune_tile_cache(self.cache_dir / name, self.tile_cache_max_bytes)
                self._last_prune_s = now

    def _offline_tile_finished(self, package, key, future):
        try:
            image = future.result()
            error = ""
        except CancelledError:
            image, error = None, ""
        except Exception as failure:
            image, error = None, str(failure)[:160]
        with self._lock:
            if self._offline_pending.get(key) is future:
                self._offline_pending.pop(key, None)
            if self._closed or package is not self._offline_package:
                return
            if image is not None:
                self._remember_tile_image(("offline", *key), image)
                self._offline_missing.pop(key, None)
                self._offline_error = ""
            else:
                self._offline_missing[key] = time.monotonic() + 60 if error else True
                self._offline_error = error
                while len(self._offline_missing) > 512:
                    self._offline_missing.popitem(last=False)

    def close(self) -> None:
        with self._enc_lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._enc_executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            if self._land is not None:
                self._land.close()
                self._land = None

    def set_offline(self, enabled: bool, package=None) -> None:
        if self.allow_network == (not enabled) and package is self._offline_package:
            return
        with self._lock:
            if package is not self._offline_package:
                for future in tuple(self._offline_pending.values()):
                    future.cancel()
                self._offline_pending.clear()
                self._offline_error = ""
            self.allow_network = not enabled
            self._offline_package = package
            self._offline_missing.clear()
            self._tile_images.clear()
            self._tile_memory_bytes = 0
            self._coastline_results.clear()
            if enabled:
                for future in tuple(self._tile_pending.values()):
                    future.cancel()

    def coastline_tile(self, z: int, x: int, y: int) -> bytes | None:
        """Schematic local basemap, not a harbour chart or navigation water mask."""
        from PySide6.QtCore import QBuffer, QIODevice, Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QTransform
        size = 2 * math.pi * WEB_MERCATOR_RADIUS_M / 2**z
        left = x * size - math.pi * WEB_MERCATOR_RADIUS_M
        top = math.pi * WEB_MERCATOR_RADIUS_M - y * size
        image = QImage(256, 256, QImage.Format_RGB32)
        image.fill(QColor("#a5cbdc"))
        with self._lock:
            if self._closed:
                return None
            land = self._land_index()
            if land is None:
                return None
            painter = QPainter(image)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#d6dfce"))
            painter.setTransform(QTransform(256 / size, 0, 0, -256 / size, -left * 256 / size, top * 256 / size))
            bounds = (left, top - size, left + size, top)
            tolerance_m = size / 512
            for index, (_offset, _length, box) in enumerate(land._records):
                if box[2] < bounds[0] or box[0] > bounds[2] or box[3] < bounds[1] or box[1] > bounds[3]:
                    continue
                if max(box[2] - box[0], box[3] - box[1]) < tolerance_m:
                    continue
                painter.drawPath(land._record_path(index, tolerance_m=tolerance_m))
            painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(buffer.data())

    def sample_water_position(
        self,
        latitude_deg: float,
        longitude_deg: float,
        maximum_range_nm: float,
        random_source,
    ) -> tuple[float, float] | None:
        with self._enc_lock:
            enc_selected = self._enc_store is not None
            regional = [(key, self._enc_region_images[key]) for key in self._enc_region_keys
                        if key in self._enc_region_images]
            available = regional or tuple(self._enc_images.items())
            images = tuple((("enc", *key), image) for key, image in available) if enc_selected else ()
        if not enc_selected:
            if not self._lock.acquire(blocking=False):
                return None
            try:
                images = tuple(self._tile_images.items())
            finally:
                self._lock.release()
        if not images:
            return None
        nearby = []
        for key, image in images:
            coordinates = key[1:] if len(key) == 4 else key
            center = _tile_pixel_position(coordinates, (128, 128))
            if _approximate_distance_nm(latitude_deg, longitude_deg, *center) <= maximum_range_nm + 30.0:
                nearby.append((key, image))
        if not nearby:
            return None
        for _attempt in range(96):
            key, image = random_source.choice(nearby)
            pixel = (random_source.randrange(0, 256), random_source.randrange(0, 256))
            color = image.pixelColor(*pixel)
            water = color.alpha() > 127 and color.red() > 127 if len(key) == 4 else _classify_image_pixel(image, pixel)
            if not water:
                continue
            candidate = _tile_pixel_position(key[1:] if len(key) == 4 else key, pixel)
            distance_nm = _approximate_distance_nm(
                latitude_deg,
                longitude_deg,
                *candidate,
            )
            if 0.2 <= distance_nm <= maximum_range_nm:
                return candidate
        return None

    def _land_index(self) -> _ShapefileLandIndex | None:
        if self._land is not None:
            return self._land
        if self._land_error:
            return None
        try:
            shape_path = self._extract_land_shape()
            self._land = _ShapefileLandIndex(shape_path)
            self._land.apply_memory_budget(self._coast_memory_limit)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            self._land_error = str(error)
        return self._land

    def _extract_land_shape(self) -> Path:
        if not self.land_archive.is_file():
            raise OSError(f"Bundled OSM land mask is missing: {self.land_archive}")
        target_dir = self.cache_dir / "osm-land-mask"
        target = target_dir / "simplified_land_polygons.shp"
        if target.is_file() and target.stat().st_size > 100:
            return target
        target_dir.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".shp.tmp")
        with zipfile.ZipFile(self.land_archive) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".shp")]
            if len(names) != 1:
                raise ValueError("Bundled OSM land mask has no unique shapefile")
            with archive.open(names[0]) as source, temporary.open("wb") as destination:
                while chunk := source.read(1024 * 1024):
                    destination.write(chunk)
        temporary.replace(target)
        return target

    def _inland_water(self, latitude_deg: float, longitude_deg: float) -> bool | None:
        key, pixel = _tile_coordinate(latitude_deg, longitude_deg, self.tile_zoom)
        with self._lock:
            image = self._tile_images.get(key)
            if image is not None:
                self._tile_images.move_to_end(key)
                return _classify_image_pixel(image, pixel)
        path = self.cache_dir / "osm-water-tiles" / str(key[0]) / str(key[1]) / f"{key[2]}.png"
        if path.is_file():
            image = _load_tile_image(path)
            if image is not None:
                with self._lock:
                    self._remember_tile_image(key, image)
                return _classify_image_pixel(image, pixel)
        if not self.allow_network or self._closed:
            return None
        with self._lock:
            if (
                key not in self._tile_pending
                and self._pending_count() < 16
                and time.monotonic() >= self._tile_retry.get(key, 0.0)
            ):
                future = self._executor.submit(self._download_tile, key, path)
                self._tile_pending[key] = future
                future.add_done_callback(lambda completed, tile=key: self._tile_finished(tile, completed))
        return None

    def _download_tile(
        self,
        key: tuple[int, int, int],
        path: Path,
    ) -> Path:
        if self._closed or not self.allow_network:
            raise OSError("Water downloads paused")
        path.parent.mkdir(parents=True, exist_ok=True)
        url = self.tile_url.format(z=key[0], x=key[1], y=key[2])
        http_request = request.Request(
            url,
            headers={
                "Accept": "image/png",
                "User-Agent": "MaritimeShipSimulatorV2/2.2 water-mask",
            },
        )
        with request.urlopen(http_request, timeout=8.0, context=verified_ssl_context()) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("OSM water-mask tile exceeded 2 MB")
        temporary = path.with_suffix(".png.tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
        self._prune_caches()
        return path

    def _tile_finished(self, key: tuple[int, int, int], future: Future) -> None:
        try:
            image = _load_tile_image(future.result())
        except (OSError, TimeoutError, ValueError, RuntimeError, CancelledError):
            image = None
        with self._lock:
            self._tile_pending.pop(key, None)
            if image is not None:
                self._tile_retry.pop(key, None)
                if not self._closed and self.allow_network:
                    self._remember_tile_image(key, image)
            else:
                self._tile_retry[key] = time.monotonic() + 60.0
                while len(self._tile_retry) > 512:
                    self._tile_retry.popitem(last=False)

    def _remember_tile_image(self, key: tuple[int, int, int], image: object) -> None:
        previous = self._tile_images.get(key)
        if previous is not None:
            self._tile_memory_bytes -= previous.sizeInBytes() + 1024
        self._tile_images[key] = image
        self._tile_memory_bytes += image.sizeInBytes() + 1024
        self._tile_images.move_to_end(key)
        limit = max(0, self._memory_limit - self._enc_memory_bytes)
        while self._tile_images and (len(self._tile_images) > self.tile_image_limit or self._tile_memory_bytes > limit):
            _, removed = self._tile_images.popitem(last=False)
            self._tile_memory_bytes -= removed.sizeInBytes() + 1024

    def _remember_coastline_result(
        self,
        key: tuple[int, int, int, int, int],
        result: bool,
    ) -> None:
        self._coastline_results[key] = bool(result)
        self._coastline_results.move_to_end(key)
        while len(self._coastline_results) > 16_384:
            self._coastline_results.popitem(last=False)


class _ShapefileLandIndex:
    GRID_M = 100_000.0

    def __init__(self, path: Path, *, ring_cache_size: int = 96):
        self._handle = Path(path).open("rb")
        self._map = mmap.mmap(self._handle.fileno(), 0, access=mmap.ACCESS_READ)
        self._records: list[tuple[int, int, tuple[float, float, float, float]]] = []
        self._grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        self._paths: OrderedDict[int | tuple[int, float], object] = OrderedDict()
        self._ring_cache_size = max(8, int(ring_cache_size))
        self._memory_limit = 6 * 1024**2
        self._build_index()

    def close(self) -> None:
        self._map.close()
        self._handle.close()

    def contains(self, latitude_deg: float, longitude_deg: float) -> bool:
        x, y = _web_mercator(latitude_deg, longitude_deg)
        candidates = self._grid.get(self._grid_key(x, y), ())
        for record_index in candidates:
            _offset, _length, bounds = self._records[record_index]
            if not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]):
                continue
            from PySide6.QtCore import QPointF

            if self._record_path(record_index).contains(QPointF(x, y)):
                return True
        return False

    def _build_index(self) -> None:
        if len(self._map) < 100 or struct.unpack_from(">i", self._map, 0)[0] != 9994:
            raise ValueError("Invalid OSM land-mask shapefile")
        position = 100
        while position + 8 <= len(self._map):
            _record_number, content_words = struct.unpack_from(">2i", self._map, position)
            content_offset = position + 8
            content_length = content_words * 2
            next_position = content_offset + content_length
            if next_position > len(self._map):
                raise ValueError("Truncated OSM land-mask shapefile")
            shape_type = struct.unpack_from("<i", self._map, content_offset)[0]
            if shape_type in {5, 15, 25} and content_length >= 44:
                bounds = struct.unpack_from("<4d", self._map, content_offset + 4)
                index = len(self._records)
                self._records.append((content_offset, content_length, bounds))
                left = math.floor(bounds[0] / self.GRID_M)
                right = math.floor(bounds[2] / self.GRID_M)
                bottom = math.floor(bounds[1] / self.GRID_M)
                top = math.floor(bounds[3] / self.GRID_M)
                for grid_x in range(left, right + 1):
                    for grid_y in range(bottom, top + 1):
                        self._grid[(grid_x, grid_y)].append(index)
            position = next_position

    def _record_path(self, record_index: int, *, tolerance_m: float = 0.0):
        key = (record_index, tolerance_m) if tolerance_m else record_index
        cached = self._paths.get(key)
        if cached is not None:
            self._paths.move_to_end(key)
            return cached
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainterPath

        offset, _length, bounds = self._records[record_index]
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        if tolerance_m and max(bounds[2] - bounds[0], bounds[3] - bounds[1]) < tolerance_m:
            return path
        part_count, point_count = struct.unpack_from("<2i", self._map, offset + 36)
        parts = struct.unpack_from(f"<{part_count}i", self._map, offset + 44)
        point_offset = offset + 44 + part_count * 4
        for index, start in enumerate(parts):
            end = parts[index + 1] if index + 1 < part_count else point_count
            if end - start < 3:
                continue
            if tolerance_m:
                import numpy as np
                from shapely.geometry import LineString

                # Only the visual background is simplified to half a pixel; contains() stays exact.
                points = np.frombuffer(self._map, dtype="<f8", count=(end - start) * 2,
                                       offset=point_offset + start * 16).reshape(-1, 2)
                coordinates = LineString(points).simplify(tolerance_m, preserve_topology=False).coords
                if len(coordinates) < 4:
                    continue
                path.moveTo(*coordinates[0])
                for x, y in coordinates[1:]:
                    path.lineTo(x, y)
            else:
                first_x, first_y = struct.unpack_from("<2d", self._map, point_offset + start * 16)
                path.moveTo(first_x, first_y)
                for point_index in range(start + 1, end):
                    x, y = struct.unpack_from("<2d", self._map, point_offset + point_index * 16)
                    path.lineTo(x, y)
            path.closeSubpath()
        self._paths[key] = path
        while len(self._paths) > self._ring_cache_size:
            self._paths.popitem(last=False)
        self.apply_memory_budget(self._memory_limit)
        return path

    def apply_memory_budget(self, maximum_bytes):
        self._memory_limit = max(0, int(maximum_bytes))
        # Qt path elements contain two doubles plus a type; include allocation overhead.
        size = sum(path.elementCount() * 32 + 128 for path in self._paths.values())
        while self._paths and size > self._memory_limit:
            _, path = self._paths.popitem(last=False)
            size -= path.elementCount() * 32 + 128
        return size

    def _grid_key(self, x: float, y: float) -> tuple[int, int]:
        return math.floor(x / self.GRID_M), math.floor(y / self.GRID_M)


def _web_mercator(latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
    latitude = max(-WEB_MERCATOR_LIMIT_DEG, min(WEB_MERCATOR_LIMIT_DEG, float(latitude_deg)))
    longitude = ((float(longitude_deg) + 180.0) % 360.0) - 180.0
    x = WEB_MERCATOR_RADIUS_M * math.radians(longitude)
    y = WEB_MERCATOR_RADIUS_M * math.log(math.tan(math.pi / 4.0 + math.radians(latitude) / 2.0))
    return x, y


def _enc_region_tiles(latitude, longitude, radius_nm):
    latitude = max(-WEB_MERCATOR_LIMIT_DEG, min(WEB_MERCATOR_LIMIT_DEG, float(latitude)))
    longitude = (float(longitude) + 180) % 360 - 180
    radius = max(.2, float(radius_nm))
    angular_radius = min(math.pi, radius * 1852 / 6_371_000)
    latitude_span = math.degrees(angular_radius)
    south = max(-WEB_MERCATOR_LIMIT_DEG, latitude - latitude_span)
    north = min(WEB_MERCATOR_LIMIT_DEG, latitude + latitude_span)
    clipped = latitude + latitude_span > WEB_MERCATOR_LIMIT_DEG or latitude - latitude_span < -WEB_MERCATOR_LIMIT_DEG
    if abs(latitude) + latitude_span >= 90:
        half_longitude = 180
    else:
        half_longitude = math.degrees(math.asin(min(1, math.sin(angular_radius) / math.cos(math.radians(latitude)))))
    for zoom in range(15, -1, -1):
        count = 2**zoom
        west_x = math.floor((longitude - half_longitude + 180) / 360 * count)
        east_x = math.floor((longitude + half_longitude + 180) / 360 * count)
        top = _tile_coordinate(north, longitude, zoom)[0][2]
        bottom = _tile_coordinate(south, longitude, zoom)[0][2]
        columns = min(count, east_x - west_x + 1)
        if columns * (bottom - top + 1) > 1024:
            continue
        keys = []
        for x in range(west_x, west_x + columns):
            for y in range(top, bottom + 1):
                key = (zoom, x % count, y)
                lat, lon = _tile_pixel_position(key, (128, 128))
                distance = _spherical_distance_nm(latitude, longitude, lat, lon)
                corner = _tile_pixel_position(key, (0, 0))
                diagonal = _spherical_distance_nm(lat, lon, *corner)
                # Mercator projects positions beyond its limit onto the boundary row.
                boundary = clipped and y in (0, count - 1)
                if boundary or distance <= radius + diagonal:
                    keys.append((distance, key))
        if len(keys) <= 512:
            keys = tuple(key for _distance, key in sorted(keys))
            # A stable tile-set key prevents rebuilding the area for tiny position changes.
            return (round(radius, 3), frozenset(keys)), keys
    return (radius, frozenset()), ()


def _spherical_distance_nm(latitude_a, longitude_a, latitude_b, longitude_b):
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lon = math.radians(longitude_b - longitude_a)
    haversine = math.sin((lat_b - lat_a) / 2)**2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2)**2
    return 2 * 6_371_000 / 1852 * math.asin(math.sqrt(min(1, max(0, haversine))))


def _tile_coordinate(
    latitude_deg: float,
    longitude_deg: float,
    zoom: int,
) -> tuple[tuple[int, int, int], tuple[int, int]]:
    latitude = max(-WEB_MERCATOR_LIMIT_DEG, min(WEB_MERCATOR_LIMIT_DEG, float(latitude_deg)))
    scale = 2 ** int(zoom)
    world_x = (float(longitude_deg) + 180.0) / 360.0 * scale
    latitude_rad = math.radians(latitude)
    world_y = (1.0 - math.asinh(math.tan(latitude_rad)) / math.pi) / 2.0 * scale
    tile_x = max(0, min(scale - 1, int(math.floor(world_x))))
    tile_y = max(0, min(scale - 1, int(math.floor(world_y))))
    pixel_x = max(0, min(255, int((world_x - tile_x) * 256.0)))
    pixel_y = max(0, min(255, int((world_y - tile_y) * 256.0)))
    return (int(zoom), tile_x, tile_y), (pixel_x, pixel_y)


def _tile_pixel_position(
    key: tuple[int, int, int],
    pixel: tuple[int, int],
) -> tuple[float, float]:
    zoom, tile_x, tile_y = key
    scale = 2 ** int(zoom)
    world_x = tile_x + (pixel[0] + 0.5) / 256.0
    world_y = tile_y + (pixel[1] + 0.5) / 256.0
    longitude = world_x / scale * 360.0 - 180.0
    mercator_y = math.pi - 2.0 * math.pi * world_y / scale
    latitude = math.degrees(math.atan(math.sinh(mercator_y)))
    return latitude, longitude


def _approximate_distance_nm(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    north_nm = (latitude_b - latitude_a) * 60.0
    east_nm = (longitude_b - longitude_a) * 60.0 * math.cos(
        math.radians((latitude_a + latitude_b) * 0.5)
    )
    return math.hypot(north_nm, east_nm)


def _prune_tile_cache(root: Path, maximum_bytes: int) -> None:
    try:
        files = [path for path in root.rglob("*.png") if path.is_file()]
        entries = sorted(
            ((path.stat().st_mtime, path.stat().st_size, path) for path in files),
            key=lambda item: item[0],
        )
    except OSError:
        return
    total = sum(size for _modified, size, _path in entries)
    for _modified, size, path in entries:
        if total <= maximum_bytes:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size


def _load_tile_image(path: Path):
    try:
        from PySide6.QtGui import QImage

        image = QImage(str(path))
        return None if image.isNull() else image
    except (ImportError, OSError, RuntimeError):
        return None


def _classify_image_pixel(image, pixel: tuple[int, int]) -> bool:
    center_x, center_y = pixel
    x = max(0, min(image.width() - 1, center_x))
    y = max(0, min(image.height() - 1, center_y))
    colour = image.pixelColor(x, y)
    red, green, blue = colour.red(), colour.green(), colour.blue()
    return blue - red >= 18 and green - red >= 14 and blue >= 125
