# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
from pathlib import Path
import random
import math
import struct

from PySide6.QtGui import QColor, QImage

from maritime_simulator.visual.water_mask import (
    OSMNavigableWaterMask,
    _classify_image_pixel,
    _prune_tile_cache,
    _tile_coordinate,
    _ShapefileLandIndex,
    WEB_MERCATOR_RADIUS_M,
)


ROOT = Path(__file__).resolve().parents[2]


def test_bundled_land_mask_classifies_open_water_without_network(tmp_path: Path):
    mask = OSMNavigableWaterMask(
        ROOT / "assets/geodata/osm-simplified-land-polygons-3857.zip",
        tmp_path,
        allow_network=False,
    )
    try:
        assert mask.is_water(55.0, 6.0) is True
    finally:
        mask.close()


def test_water_tile_image_memory_is_bounded(tmp_path: Path):
    mask = OSMNavigableWaterMask(
        tmp_path / "unused.zip",
        tmp_path,
        allow_network=False,
        tile_image_limit=8,
    )
    try:
        for index in range(12):
            from PySide6.QtGui import QImage
            mask._remember_tile_image((9, index, index), QImage(256, 256, QImage.Format_Indexed8))
        assert len(mask._tile_images) == 8
        assert (9, 0, 0) not in mask._tile_images
        assert (9, 11, 11) in mask._tile_images
    finally:
        mask.close()


def test_water_classification_is_pixel_specific_within_one_tile():
    image = QImage(32, 32, QImage.Format_RGB32)
    image.fill(QColor(220, 215, 205))
    for x in range(16, 32):
        for y in range(32):
            image.setPixelColor(x, y, QColor(170, 211, 223))

    assert _classify_image_pixel(image, (4, 16)) is False
    assert _classify_image_pixel(image, (28, 16)) is True


def test_water_sampler_reuses_loaded_tile_without_redecoding_or_network(tmp_path: Path):
    latitude, longitude = 53.5403, 9.9536
    mask = OSMNavigableWaterMask(
        tmp_path / "unused.zip",
        tmp_path,
        allow_network=False,
    )
    image = QImage(256, 256, QImage.Format_RGB32)
    image.fill(QColor(170, 211, 223))
    key, _pixel = _tile_coordinate(latitude, longitude, mask.tile_zoom)
    mask._remember_tile_image(key, image)
    try:
        sampled = mask.sample_water_position(latitude, longitude, 20.0, random.Random(61162))
        assert sampled is not None
        assert mask.is_water(*sampled) is True
    finally:
        mask.close()


def test_water_tile_disk_cache_removes_oldest_files_first(tmp_path: Path):
    root = tmp_path / "tiles"
    files = []
    for index in range(3):
        path = root / f"{index}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([index]) * 10)
        os.utime(path, (100 + index, 100 + index))
        files.append(path)

    _prune_tile_cache(root, 20)

    assert not files[0].exists()
    assert files[1].exists()
    assert files[2].exists()


def test_visual_coastline_simplification_never_changes_exact_land_queries(tmp_path):
    # A dense synthetic ring avoids depending on host speed or external map updates.
    points = [(1000 * math.cos(i * 2 * math.pi / 1000),
               1000 * math.sin(i * 2 * math.pi / 1000)) for i in range(1000)]
    points.append(points[0])
    content = (struct.pack("<i4d3i", 5, -1000, -1000, 1000, 1000, 1, len(points), 0)
               + b"".join(struct.pack("<2d", *point) for point in points))
    header = bytearray(100)
    struct.pack_into(">i", header, 0, 9994)
    shape = tmp_path / "land.shp"
    shape.write_bytes(header + struct.pack(">2i", 1, len(content) // 2) + content)
    land = _ShapefileLandIndex(shape)
    try:
        precise = land._record_path(0)
        display = land._record_path(0, tolerance_m=100)
        assert display.elementCount() < precise.elementCount() // 10
        assert land._record_path(0) is precise
        assert land._record_path(0, tolerance_m=100) is display
        assert land.contains(0, 0)
        assert not land.contains(0, .02)
        assert land._record_path(0, tolerance_m=3000).isEmpty()
        assert land.contains(0, 0), "Invisible subpixel islands remain land for avoidance"
        assert land.apply_memory_budget(0) == 0 and not land._paths
        assert land.contains(0, 0)
    finally:
        land.close()


def test_world_background_skips_subpixel_paths_and_keeps_land_and_water(tmp_path, monkeypatch):
    mask = OSMNavigableWaterMask(
        ROOT / "assets/geodata/osm-simplified-land-polygons-3857.zip", tmp_path, allow_network=False,
    )
    try:
        land = mask._land_index()
        original = land._record_path
        tolerance = 2 * math.pi * WEB_MERCATOR_RADIUS_M / 512
        rendered = []
        def measured(index, *, tolerance_m=0):
            rendered.append((index, tolerance_m))
            return original(index, tolerance_m=tolerance_m)
        monkeypatch.setattr(land, "_record_path", measured)
        image = QImage.fromData(mask.coastline_tile(0, 0, 0))
        assert image.width() == 256
        assert {image.pixelColor(x, y).name() for x in range(0, 256, 4)
                for y in range(0, 256, 4)} == {"#a5cbdc", "#d6dfce"}
        assert 0 < len(rendered) < len(land._records) // 10
        assert all(value == tolerance for _, value in rendered)
        assert sum(path.elementCount() * 32 + 128 for path in land._paths.values()) <= land._memory_limit
    finally:
        mask.close()
