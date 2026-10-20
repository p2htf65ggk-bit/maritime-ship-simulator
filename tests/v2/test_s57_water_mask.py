# SPDX-License-Identifier: MPL-2.0
from contextlib import closing
from concurrent.futures import Future
from dataclasses import replace
import json
from pathlib import Path
import random
from threading import Event, Thread
import time

import pytest
from PySide6.QtWidgets import QApplication
from shapely import to_wkb
from shapely.geometry import Polygon, box

from maritime_simulator.visual.s57 import S57Store
from maritime_simulator.visual.s57_water import render_water_tile
from maritime_simulator.visual.water_mask import (
    OSMNavigableWaterMask, _enc_region_tiles, _tile_coordinate, _tile_pixel_position,
)

ROOT = Path(__file__).resolve().parents[2]
KEY, _ = _tile_coordinate(40.7, -74.0, 15)


def point(x, y):
    return _tile_pixel_position(KEY, (x, y))


def rectangle(x1, y1, x2, y2):
    lat1, lon1 = point(x1, y1)
    lat2, lon2 = point(x2, y2)
    return box(min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2))


def add_surface(store, code, scale, layer, geometry, **properties):
    with closing(store.connect(write=True)) as db, db:
        db.execute("INSERT OR IGNORE INTO charts VALUES(?,?,?,?,?,?,?,?,1)",
                   (code, code, 'test', '1', '0', '20261009', scale, json.dumps(rectangle(0, 0, 256, 256).bounds)))
        props = dict(layer=layer, chart=code, SCAMIN=1, **properties)
        cursor = db.execute("INSERT INTO features(chart,layer,geometry,properties) VALUES(?,?,?,?)",
                            (code, layer, to_wkb(geometry), json.dumps(props)))
        west, south, east, north = geometry.bounds
        db.execute("INSERT INTO extent VALUES(?,?,?,?,?)", (cursor.lastrowid, west, east, south, north))


def mask(tmp_path):
    return OSMNavigableWaterMask(ROOT / 'assets/geodata/osm-simplified-land-polygons-3857.zip',
                                tmp_path / 'cache', allow_network=False)


def wait_mask(water):
    deadline = time.monotonic() + 5
    while water._enc_pending and time.monotonic() < deadline:
        time.sleep(.005)
    assert not water._enc_pending


class QueuedExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, function, *args):
        future = Future()
        self.jobs.append((future, function, args))
        return future

    def run_next(self):
        future, function, args = next(job for job in self.jobs if not job[0].done())
        future.set_running_or_notify_cancel()
        try:
            result = function(*args)
        except Exception as error:
            future.set_exception(error)
        else:
            future.set_result(result)

    def shutdown(self, **_kwargs):
        for future, _function, _args in self.jobs:
            future.cancel()


def queued_mask(tmp_path, monkeypatch):
    water = mask(tmp_path)
    water._enc_executor.shutdown(wait=True)
    executor = QueuedExecutor()
    monkeypatch.setattr(water, '_enc_executor', executor)
    water.set_s57(S57Store(tmp_path / 's57'))
    return water, executor


@pytest.mark.parametrize('latitude,longitude,radius', [
    (40.705, -74.026, 5), (40.705, -74.026, 40), (53.5, 10, 1),
    (0, 179.99, 40), (85, -179, 250),
])
def test_ais_region_plan_covers_circle_and_has_bounded_memory(latitude, longitude, radius):
    from maritime_simulator.application.ais import _destination
    _region, keys = _enc_region_tiles(latitude, longitude, radius)
    assert 0 < len(keys) <= 512
    zoom = keys[0][0]
    assert all(z == zoom and 0 <= x < 2**z and 0 <= y < 2**z for z, x, y in keys)
    for bearing in range(0, 360, 15):
        lat, lon = _destination(latitude, longitude, bearing, radius)
        assert _tile_coordinate(lat, (lon + 180) % 360 - 180, zoom)[0] in keys
    assert _enc_region_tiles(latitude, longitude, radius)[0] == _region


def test_region_prepares_fine_enc_surfaces_independent_of_visible_scale(tmp_path):
    store = S57Store(tmp_path / 's57')
    add_surface(store, 'COARSE', 100000, 'LNDARE', rectangle(-1, -1, 257, 257))
    add_surface(store, 'FINE', 10000, 'DEPARE', rectangle(-1, -1, 257, 257), DRVAL2=20)
    water = mask(tmp_path)
    try:
        water.set_s57(store)
        center = point(128, 128)
        deadline = time.monotonic() + 5
        while not water.prepare_ais_region(*center, .2) and time.monotonic() < deadline:
            time.sleep(.005)
        assert water.prepare_ais_region(*center, .2)
        cached = water._enc_region_images[KEY]
        assert cached.sizeInBytes() == 256 * 256
        assert not water._tile_pending
        for zoom in (7, 11, 16, 18, 7):
            store.features(rectangle(0, 0, 256, 256).bounds, zoom, ('LNDARE',))
            water.set_s57(store)
            assert water.prepare_ais_region(*center, .2)
            assert water._enc_region_images[KEY] is cached
            assert water.is_water(*point(100, 100)) is True
            assert water._enc_images[KEY] is cached
        assert water.sample_water_position(*center, 1, random.Random(61162)) is not None
    finally:
        water.close()


def test_regional_prefetch_is_bounded_and_does_not_block_point_requests(tmp_path, monkeypatch):
    water, executor = queued_mask(tmp_path, monkeypatch)
    try:
        assert not water.prepare_ais_region(40.705, -74.026, 40)
        assert len(water._enc_region_pending) == 2
        assert len(executor.jobs) == 2
        assert 'Preparing AIS water area: 0/' in water.status
        assert water.is_water(40.7, -74) is None
        assert len(water._enc_pending) == 1
        assert len(executor.jobs) == 3
        water.set_s57(None)
        assert all(job[0].cancelled() for job in executor.jobs)
        assert not water._enc_region_images
        assert not water._enc_region_pending
        assert water.prepare_ais_region(40.705, -74.026, 40)
    finally:
        water.close()


def test_coarse_region_sample_is_never_used_as_final_point_verdict(tmp_path, monkeypatch):
    from PySide6.QtGui import QImage
    water, executor = queued_mask(tmp_path, monkeypatch)
    try:
        region, keys = _enc_region_tiles(40.7, -74, 40)
        water._enc_region = region
        water._enc_region_keys = keys
        water._enc_region_key_set = frozenset(keys)
        image = QImage(256, 256, QImage.Format_ARGB32)
        image.fill(0xffffffff)
        coarse = _tile_coordinate(40.7, -74, keys[0][0])[0]
        assert coarse[0] < 15
        water._enc_region_images[coarse] = image
        assert water.is_water(40.7, -74) is None
        assert executor.jobs[-1][2][1:] == KEY
        assert water.sample_water_position(40.7, -74, 40, random.Random(61162)) is not None
    finally:
        water.close()


def test_running_region_results_are_ignored_after_source_change_or_close(tmp_path, monkeypatch):
    from PySide6.QtGui import QImage
    water, executor = queued_mask(tmp_path, monkeypatch)
    image = QImage(256, 256, QImage.Format_ARGB32)
    image.fill(0xffffffff)
    try:
        assert not water.prepare_ais_region(40.7, -74, 5)
        running = executor.jobs[0][0]
        assert running.set_running_or_notify_cancel()
        water.set_s57(None)
        running.set_result(image)
        assert not water._enc_region_images
        assert not water._enc_region_pending
        water.set_s57(S57Store(tmp_path / 's57'))
        assert not water.prepare_ais_region(40.7, -74, 5)
        running = next(job[0] for job in executor.jobs if not job[0].done())
        assert running.set_running_or_notify_cancel()
        water.close()
        running.set_result(image)
        assert not water._enc_region_images
    finally:
        water.close()


def test_region_errors_back_off_but_do_not_stop_other_tiles(tmp_path, monkeypatch):
    from maritime_simulator.visual import s57_water, water_mask
    from PySide6.QtGui import QImage
    keys = tuple((15, KEY[1] + i, KEY[2]) for i in range(6))
    monkeypatch.setattr(water_mask, '_enc_region_tiles', lambda *_: ('region', keys))
    calls = []

    def render(_store, *key):
        calls.append(key)
        if key == keys[0]:
            raise ValueError('one failed cell')
        if key == keys[1]:
            return None
        image = QImage(256, 256, QImage.Format_ARGB32)
        image.fill(0xffffffff)
        return image

    monkeypatch.setattr(s57_water, 'render_water_tile', render)
    water, executor = queued_mask(tmp_path, monkeypatch)
    try:
        assert not water.prepare_ais_region(40.7, -74, 5)
        while water._enc_region_pending:
            executor.run_next()
            assert len(water._enc_region_pending) <= 2
        assert water.prepare_ais_region(40.7, -74, 5)
        assert calls == list(keys)
        assert '1 tile(s) unavailable' in water.status
        for _ in range(3):
            assert water.prepare_ais_region(40.7, -74, 5)
        assert calls == list(keys)
        water._enc_region_missing[keys[0]] = time.monotonic() - 1
        assert not water.prepare_ais_region(40.7, -74, 5)
        executor.run_next()
        assert calls == [*keys, keys[0]]
        assert water.prepare_ais_region(40.7, -74, 5)
    finally:
        water.close()


def test_region_switch_reuses_overlap_cancels_queued_work_and_keeps_fine_points(tmp_path, monkeypatch):
    from maritime_simulator.visual import water_mask
    from PySide6.QtGui import QImage
    keys = tuple((15, KEY[1] + i, KEY[2]) for i in range(5))
    monkeypatch.setattr(water_mask, '_enc_region_tiles',
                        lambda _lat, _lon, radius: (radius, keys[:3] if radius == 1 else keys[2:]))
    water, executor = queued_mask(tmp_path, monkeypatch)
    image = QImage(256, 256, QImage.Format_ARGB32)
    image.fill(0xffffffff)
    try:
        water._enc_region_images[keys[2]] = image
        water._enc_images[KEY] = image
        assert not water.prepare_ais_region(40.7, -74, 1)
        abandoned = [job[0] for job in executor.jobs]
        assert not water.prepare_ais_region(40.7, -74, 2)
        assert all(future.cancelled() for future in abandoned)
        assert water._enc_region_images[keys[2]] is image
        assert water._enc_images[KEY] is image
        assert set(water._enc_region_pending) == set(keys[3:])
        assert water.is_water(*point(100, 100)) is True
    finally:
        water.close()


def test_immediate_region_results_do_not_recurse_or_evict_current_area(tmp_path, monkeypatch):
    from maritime_simulator.visual import water_mask
    keys = tuple((15, x, KEY[2]) for x in range(512))
    monkeypatch.setattr(water_mask, '_enc_region_tiles', lambda *_: ('region', keys))
    water, _executor = queued_mask(tmp_path, monkeypatch)

    class ImmediateExecutor(QueuedExecutor):
        def submit(self, *_args):
            future = Future()
            future.set_result(None)
            return future

    monkeypatch.setattr(water, '_enc_executor', ImmediateExecutor())
    try:
        assert water.prepare_ais_region(40.7, -74, 5)
        assert len(water._enc_region_missing) == 512
        assert not water._enc_region_pending
        for key in [*( (16, x, 1) for x in range(20)), *keys]:
            from PySide6.QtGui import QImage
            water._remember_enc_region_image(key, QImage(256, 256, QImage.Format_Indexed8))
        assert len(water._enc_region_images) == 512
        assert set(water._enc_region_images) == set(keys)
    finally:
        water.close()


def test_enc_surfaces_are_raw_and_detailed_water_land_quays_and_drying_areas_win(tmp_path):
    store = S57Store(tmp_path / 's57')
    add_surface(store, 'COARSE', 100000, 'DEPARE', rectangle(0, 0, 256, 256), DRVAL2=30)
    add_surface(store, 'COARSE', 100000, 'LNDARE', rectangle(0, 0, 160, 256))
    add_surface(store, 'FINE', 10000, 'DEPARE', rectangle(20, 20, 130, 130), DRVAL2=20)
    add_surface(store, 'FINE', 10000, 'LNDARE', rectangle(45, 45, 60, 60))
    add_surface(store, 'FINE', 10000, 'SLCONS', rectangle(70, 20, 80, 100))
    add_surface(store, 'FINE', 10000, 'DRGARE', rectangle(95, 95, 110, 110), DRVAL2=-1)
    image = render_water_tile(store, *KEY)
    for x, y, expected in [(35, 35, 255), (50, 50, 0), (75, 50, 0), (100, 100, 0), (145, 145, 0), (220, 220, 255)]:
        color = image.pixelColor(x, y)
        assert color.alpha() == 255 and color.red() == expected
    # Display filters are deliberately absent; these features all have SCAMIN=1.
    assert store.water_surfaces(rectangle(0, 0, 256, 256).bounds)


def test_enc_polygon_holes_and_unmapped_pixels_remain_unknown(tmp_path):
    store = S57Store(tmp_path / 's57')
    outer, inner = rectangle(20, 20, 200, 200), rectangle(80, 80, 120, 120)
    add_surface(store, 'TEST', 10000, 'DEPARE', Polygon(outer.exterior.coords, [inner.exterior.coords]), DRVAL2=30)
    image = render_water_tile(store, *KEY)
    assert image.pixelColor(40, 40).red() == 255
    assert image.pixelColor(100, 100).alpha() == 0
    assert image.pixelColor(240, 240).alpha() == 0
    store.set_enabled('TEST', False)
    assert render_water_tile(store, *KEY) is None


def test_finest_cell_coverage_owns_mask_and_coarse_cells_only_fill_gaps(tmp_path):
    store = S57Store(tmp_path / 's57')
    add_surface(store, 'COARSE', 100000, 'DEPARE', rectangle(0, 0, 256, 256), DRVAL2=30)
    add_surface(store, 'FINE', 10000, 'LNDARE', rectangle(40, 40, 100, 100))
    add_surface(store, 'FINE', 10000, 'DEPARE', rectangle(0, 0, 256, 256), DRVAL2=20)
    with closing(store.connect(write=True)) as db, db:
        db.execute('INSERT INTO chart_coverage VALUES(?,?)', ('FINE', to_wkb(rectangle(20, 20, 120, 120))))
    image = render_water_tile(store, *KEY)
    assert image.pixelColor(60, 60).red() == 0
    assert image.pixelColor(110, 110).red() == 255
    assert image.pixelColor(180, 180).red() == 255


def test_complete_fine_cell_does_not_decode_coarse_geometry(tmp_path):
    store = S57Store(tmp_path / 's57')
    add_surface(store, 'COARSE', 100000, 'DEPARE', rectangle(0, 0, 256, 256), DRVAL2=30)
    add_surface(store, 'FINE', 10000, 'DEPARE', rectangle(-1, -1, 257, 257), DRVAL2=20)
    with closing(store.connect(write=True)) as db, db:
        db.execute("UPDATE features SET geometry=? WHERE chart='COARSE'", (b'invalid unused WKB',))
    image = render_water_tile(store, *KEY)
    assert image.pixelColor(100, 100).red() == 255


def test_cached_enc_is_independent_of_background_render_lock_and_osm_cache(tmp_path):
    from PySide6.QtGui import QImage
    store = S57Store(tmp_path / 's57')
    add_surface(store, 'TEST', 10000, 'DEPARE', rectangle(0, 0, 256, 256), DRVAL2=30)
    water = mask(tmp_path)
    entered, release = Event(), Event()

    def render_lock():
        with water._lock:
            entered.set()
            release.wait(5)

    worker = Thread(target=render_lock)
    try:
        water.set_s57(store)
        water.is_water(*point(100, 100))
        wait_mask(water)
        image = water._enc_images[KEY]
        assert image.format() == QImage.Format_Indexed8
        assert image.sizeInBytes() == 256 * 256
        for index in range(water.tile_image_limit + 1):
            water._remember_tile_image((10, index, 0), image)
        assert water._enc_images[KEY] is image
        worker.start()
        assert entered.wait(5)
        assert water.is_water(*point(100, 100)) is True
        assert not water._enc_pending
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(5)
        water.close()


def test_unknown_enc_pixel_never_falls_back_to_coarse_osm_water(tmp_path, monkeypatch):
    store = S57Store(tmp_path / 's57')
    add_surface(store, 'TEST', 10000, 'DEPARE', rectangle(0, 0, 100, 100), DRVAL2=30)
    water = mask(tmp_path)
    try:
        water.set_s57(store)
        water.is_water(*point(50, 50))
        wait_mask(water)
        monkeypatch.setattr(water, '_is_water', lambda *_args: True)
        assert water.is_water(*point(50, 50)) is True
        assert water.is_water(*point(150, 150)) is None
    finally:
        water.close()


def test_enc_mask_runs_offline_caches_samples_and_invalidates_on_cell_changes(tmp_path):
    store = S57Store(tmp_path / 's57')
    add_surface(store, 'TEST', 10000, 'DEPARE', rectangle(0, 0, 256, 256), DRVAL2=30)
    water = mask(tmp_path)
    try:
        water.set_s57(store)
        assert water.is_water(*point(100, 100)) is None
        wait_mask(water)
        assert water.is_water(*point(100, 100)) is True
        cached = water._enc_images[KEY]
        water.set_s57(store)
        assert water._enc_images[KEY] is cached
        assert water.sample_water_position(*point(128, 128), 20, random.Random(61162)) is not None
        assert not water._tile_pending
        add_surface(store, 'TEST', 10000, 'LNDARE', rectangle(50, 50, 150, 150))
        water.set_s57(store)
        assert KEY not in water._enc_images
        assert water.is_water(*point(100, 100)) is None
        wait_mask(water)
        assert water.is_water(*point(100, 100)) is False
        assert len(water._enc_images) <= water.enc_tile_image_limit
    finally:
        water.close()


def test_enc_preparation_is_nonblocking_bounded_and_late_results_are_ignored(tmp_path, monkeypatch):
    from maritime_simulator.visual import s57_water
    from PySide6.QtGui import QColor, QImage
    entered, release = Event(), Event()
    def slow(*_args):
        entered.set()
        assert release.wait(5)
        image = QImage(256, 256, QImage.Format_ARGB32)
        image.fill(QColor('white'))
        return image
    monkeypatch.setattr(s57_water, 'render_water_tile', slow)
    water = mask(tmp_path)
    try:
        water.set_s57(S57Store(tmp_path / 's57'))
        assert water.is_water(40.7, -74.0) is None
        assert entered.wait(5)
        for index in range(50):
            assert water.is_water(40.7 + index * .02, -74) is None
        assert len(water._enc_pending) == 16
        water.set_s57(None)
        release.set()
        water._enc_executor.shutdown(wait=True)
        assert not water._enc_images
    finally:
        release.set()
        water.close()


def test_zoom_and_display_layer_changes_preserve_raw_enc_water_classification(tmp_path):
    store = S57Store(tmp_path / 's57')
    add_surface(store, 'COARSE', 100000, 'DEPARE', rectangle(0, 0, 256, 256), DRVAL2=30)
    add_surface(store, 'FINE', 10000, 'LNDARE', rectangle(50, 50, 150, 150))
    water = mask(tmp_path)
    try:
        water.set_s57(store)
        water.is_water(*point(100, 100))
        wait_mask(water)
        image = water._enc_images[KEY]
        for zoom in (7, 11, 15, 18, 15, 11, 7):
            for layers in (None, (), ('DEPARE',), ('LNDARE',)):
                store.features(rectangle(0, 0, 256, 256).bounds, zoom, layers)
                water.set_s57(store)
                assert water.is_water(*point(100, 100)) is False
                assert water.is_water(*point(220, 220)) is True
                assert water._enc_images[KEY] is image
        assert not water._enc_pending
    finally:
        water.close()


def test_enc_failures_back_off_instead_of_repeatedly_querying(tmp_path, monkeypatch):
    from maritime_simulator.visual import s57_water
    calls = []
    def fail(*_args):
        calls.append(1)
        raise ValueError('bounded mask failure')
    monkeypatch.setattr(s57_water, 'render_water_tile', fail)
    water = mask(tmp_path)
    try:
        water.set_s57(S57Store(tmp_path / 's57'))
        assert water.is_water(*point(100, 100)) is None
        wait_mask(water)
        for _ in range(30):
            assert water.is_water(*point(100, 100)) is None
        assert len(calls) == 1
        assert 'bounded mask failure' in water.status
    finally:
        water.close()


def test_window_uses_enc_water_for_s57_only_and_hiding_layers_does_not_change_it(tmp_path):
    from maritime_simulator.ui.main_window import V2MainWindow
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        add_surface(window.s57_store, 'TEST', 10000, 'DEPARE', rectangle(0, 0, 256, 256), DRVAL2=30)
        settings = replace(window.offline_settings, enabled=True, map_source='s57')
        window._apply_offline_settings(settings)
        assert window.water_mask._enc_store is window.s57_store
        assert window.water_mask.is_water(*point(100, 100)) is None
        wait_mask(window.water_mask)
        assert window.water_mask.is_water(*point(100, 100)) is True
        image = window.water_mask._enc_images[KEY]
        window._apply_offline_settings(replace(settings, enc_layers=()))
        assert window.water_mask._enc_images[KEY] is image
        window._apply_offline_settings(replace(settings, enabled=False, map_source='online'))
        assert window.water_mask._enc_store is None
    finally:
        window._configuration_baseline = window._configuration_payload()
        window.close()
        window.deleteLater()
        app.processEvents()
