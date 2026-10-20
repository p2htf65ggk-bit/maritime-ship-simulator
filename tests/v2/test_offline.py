# SPDX-License-Identifier: MPL-2.0
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import random
import sqlite3
import socket
import time
from threading import Event, Thread
from urllib.request import urlopen
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtCore import QBuffer, QIODevice, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
import pytest

from maritime_simulator.visual.offline import MBTiles, OfflineSettings, OfflineTileServer, export_offline_package, import_offline_package, install_mbtiles
from maritime_simulator.visual.water_mask import OSMNavigableWaterMask, _tile_coordinate
from maritime_simulator.visual.water_package import create_water_mask

ROOT = Path(__file__).resolve().parents[2]


def image_bytes(color="white"):
    image = QImage(256, 256, QImage.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def tiles(path, *, water=False):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
        db.execute("CREATE TABLE tiles (zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB)")
        db.execute("CREATE UNIQUE INDEX xyz ON tiles(zoom_level,tile_column,tile_row)")
        db.executemany("INSERT INTO metadata VALUES (?,?)", [("format", "png"), ("maritime_water_mask", "1" if water else "0")])
        db.execute("INSERT INTO tiles VALUES (?,?,?,?)", (1, 1, 1, image_bytes()))
    return path


def mask(tmp_path):
    return OSMNavigableWaterMask(ROOT / "assets/geodata/osm-simplified-land-polygons-3857.zip", tmp_path, allow_network=False)


def test_mbtiles_tms_and_read_only(tmp_path):
    source = tiles(tmp_path / "area.mbtiles")
    before = source.read_bytes()
    package = MBTiles(source)
    assert package.tile(1, 1, 0) == image_bytes()
    assert package.tile(1, 1, 1) is None
    assert package.tile(20, 1, 0) is None
    assert source.read_bytes() == before


def test_mbtiles_open_seeks_zoom_levels_without_scanning_all_tiles(tmp_path, monkeypatch):
    source = tiles(tmp_path / "large.mbtiles")
    with sqlite3.connect(source) as db:
        db.executemany("INSERT INTO tiles VALUES (?,?,?,?)", ((15,index,0,b"stub") for index in range(20000)))
    connect, steps = MBTiles.connect, []
    def measured(self):
        db = connect(self)
        db.set_progress_handler(lambda: steps.append(1) or int(len(steps) > 2000), 1)
        return db
    monkeypatch.setattr(MBTiles, "connect", measured)
    package = MBTiles(source)
    assert package.zoom_levels == [15,1]
    assert len(steps) < 2000, "Opening a package must not visit each tile"


def test_mbtiles_rejects_vector_and_unlabelled_water(tmp_path):
    source = tiles(tmp_path / "area.mbtiles")
    with pytest.raises(ValueError, match="maritime_water_mask"):
        MBTiles(source, water_mask=True)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE metadata SET value='pbf' WHERE name='format'")
    with pytest.raises(ValueError, match="raster"):
        MBTiles(source)


def test_offline_package_usb_roundtrip_no_credentials(tmp_path):
    directory = tmp_path / "source"
    map_name = install_mbtiles(tiles(tmp_path / "map.mbtiles"), directory)
    water_name = install_mbtiles(tiles(tmp_path / "water.mbtiles", water=True), directory, water_mask=True)
    settings = OfflineSettings(True, map_name, water_name)
    zip_path = tmp_path / "offline.zip"
    export_offline_package(directory, settings, zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        assert sorted(archive.namelist()) == sorted(["offline.json", map_name, water_name])
    imported = import_offline_package(zip_path, tmp_path / "usb target")
    assert imported == settings
    assert MBTiles(tmp_path / "usb target" / imported.map_file).tile(1, 1, 0)


def test_offline_import_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        OfflineSettings.from_payload({"map_file": "../bad.mbtiles"})
    with pytest.raises(ValueError):
        OfflineSettings.from_payload({"water_file": "C:\\bad.mbtiles"})


def test_local_coastline_tile_and_http_no_internet(tmp_path):
    water = mask(tmp_path)
    server = OfflineTileServer(water)
    try:
        url = server.url.replace("{z}", "4").replace("{x}", "8").replace("{y}", "5")
        with urlopen(url, timeout=5) as reply:
            payload = reply.read()
        assert QImage.fromData(payload).width() == 256
        for index in range(120):
            server.tile(8, index, 100)
        assert len(server._tiles) <= 96
        assert server._cache_bytes <= 16 * 1024 * 1024
    finally:
        server.close()
        water.close()
    assert not server.thread.is_alive()


def test_s57_layer_selection_retains_schematic_coastline_cache():
    calls = []
    class Coastline:
        def coastline_tile(self, z, x, y):
            calls.append((z,x,y))
            return image_bytes()
    server = OfflineTileServer(Coastline())
    try:
        expected = server.tile(1,0,0)
        size, revision = server._cache_bytes, server._enc_revision
        for _ in range(20):
            server.select(None)
            assert server.tile(1,0,0) == expected
        assert calls == [(1,0,0)] and server._cache_bytes == size
        assert server._enc_revision == revision + 20, "Chart queries must still invalidate independently"
    finally:
        server.close()


def test_source_switch_never_waits_for_render_and_old_tiles_do_not_pollute_new_cache():
    entered, release, switched = Event(), Event(), Event()
    class Coastline:
        def coastline_tile(self, *_args):
            entered.set()
            assert release.wait(5)
            return b"old"
    class Package:
        def display_tile(self, *_args):
            return b"new"
    server = OfflineTileServer(Coastline())
    thread = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(server.tile, 1, 0, 0)
            assert entered.wait(5)
            package = Package()
            def change_source():
                server.select(package)
                switched.set()
            thread = Thread(target=change_source)
            thread.start()
            try:
                assert switched.wait(.5), "Qt source changes must not wait for a slow renderer"
            finally:
                release.set()
                thread.join(timeout=5)
            assert future.result(timeout=5) == b"old"
        assert not server._tiles and server._cache_bytes == 0
        assert server.tile(1, 0, 0) == b"new"
        size = server._cache_bytes
        server.select(package)
        assert server.tile(1, 0, 0) == b"new" and server._cache_bytes == size
    finally:
        release.set()
        if thread is not None:
            thread.join(timeout=5)
        server.close()


def test_mask_cache_reconfiguration_and_hits_preserve_hot_offline_tiles(tmp_path):
    water = mask(tmp_path)
    package = MBTiles(tiles(tmp_path / "water.mbtiles", water=True), water_mask=True)
    try:
        water.set_offline(True, package)
        assert water.is_water(53.54, 9.95) is True
        key, _ = _tile_coordinate(53.54, 9.95, 1)
        hot = ("offline", *key)
        image = water._tile_images[hot]
        for index in range(10):
            from PySide6.QtGui import QImage
            water._remember_tile_image(("offline", 15, index, index), QImage(256,256,QImage.Format_Indexed8))
        assert water.is_water(53.54, 9.95) is True
        assert next(reversed(water._tile_images)) == hot
        water.set_offline(True, package)
        assert water._tile_images[hot] is image
        assert not water.allow_network
    finally:
        water.close()


def test_busy_coastline_work_cannot_block_water_checks_sampling_or_status(tmp_path):
    water = mask(tmp_path)
    entered, release = Event(), Event()
    def hold_lock():
        with water._lock:
            entered.set()
            assert release.wait(5)
    thread = Thread(target=hold_lock)
    thread.start()
    try:
        assert entered.wait(5)
        assert water.is_water(53.54, 9.95) is None
        assert water.sample_water_position(53.54, 9.95, 20.0, random.Random(61162)) is None
        assert water.status == "Preparing local coastline background"
    finally:
        release.set()
        thread.join(timeout=5)
        water.close()


def test_layer_changes_reuse_packages_and_renewed_files_invalidate_them(tmp_path):
    from maritime_simulator.ui.main_window import V2MainWindow
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    directory = tmp_path / "offline-packages"
    directory.mkdir(exist_ok=True)
    path = tiles(directory / "water.mbtiles", water=True)
    settings = OfflineSettings(True, "water.mbtiles", "water.mbtiles", "protomaps")
    try:
        window.timer.stop()
        window._apply_offline_settings(settings)
        map_package, water_package = window.offline_tiles.package, window.water_mask._offline_package
        window._apply_offline_settings(replace(settings, enc_layers=("LNDARE",)))
        assert window.offline_tiles.package is map_package
        assert window.water_mask._offline_package is water_package
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO metadata VALUES('maritime_test_revision','renewed')")
        window._apply_offline_settings(settings)
        assert window.offline_tiles.package is not map_package
        assert window.water_mask._offline_package is not water_package
        assert window.offline_tiles.package.metadata["maritime_test_revision"] == "renewed"
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_first_settings_open_does_not_build_hidden_s57_catalogue_or_wait_on_water(tmp_path, monkeypatch):
    from maritime_simulator.ui.main_window import V2MainWindow
    from maritime_simulator.visual.s57 import S57Store
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    entered, release = Event(), Event()
    def hold_lock():
        with window.water_mask._lock:
            entered.set()
            assert release.wait(10)
    thread = Thread(target=hold_lock)
    try:
        window.timer.stop()
        window.s57_store.import_files([ROOT / 'tests/fixtures/s57/US4NJ1DG.zip'])
        window._apply_offline_settings(replace(window.offline_settings, enabled=True, map_source='s57'))
        read = S57Store.records
        monkeypatch.setattr(S57Store, 'records', lambda *_args: pytest.fail('Hidden catalogue loaded while opening Settings'))
        thread.start()
        assert entered.wait(10)
        window._open_settings()
        panel = window.settings_dialog.offline_panel.s57
        assert not panel._loaded and panel.table.model().rowCount() == 0
        release.set()
        thread.join(timeout=10)
        monkeypatch.setattr(S57Store, 'records', read)
        resets, cells = [], []
        panel.table.model().modelReset.connect(lambda: resets.append(1))
        panel.table.model().dataChanged.connect(lambda *_args: cells.append(1))
        offline = window.settings_dialog.offline_panel
        window.settings_dialog.tabs.setCurrentWidget(offline)
        offline.map_tabs.setCurrentWidget(panel)
        assert panel._loaded and panel.table.model().rowCount() == 1
        assert resets == [1] and not cells
    finally:
        release.set()
        if thread.is_alive():
            thread.join(timeout=10)
        window._configuration_baseline = window._configuration_payload()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_offline_water_mask_works_in_harbour_without_network(tmp_path):
    source = tiles(tmp_path / "water.mbtiles", water=True)
    water = mask(tmp_path)
    try:
        water.set_offline(True, MBTiles(source, water_mask=True))
        # The packaged tile covers north-east hemisphere; choose inland Europe.
        assert water.is_water(53.54, 9.95) is True
        assert not water.allow_network
        assert not water._tile_pending
    finally:
        water.close()


def test_failed_water_requests_have_backoff_and_bounded_queue(tmp_path, monkeypatch):
    water = mask(tmp_path)
    water.allow_network = True
    calls = []
    def submit(*_args):
        future = Future()
        calls.append(future)
        return future
    monkeypatch.setattr(water._executor, "submit", submit)
    try:
        water._inland_water(53.54, 9.95)
        calls[0].set_exception(OSError("offline"))
        for _ in range(100):
            water._inland_water(53.54, 9.95)
        assert len(calls) == 1
        for index in range(80):
            water._inland_water(45.0, -170.0 + index * 4)
        assert len(water._tile_pending) <= 16
    finally:
        water.close()


def test_geojson_generates_exact_offline_mask_and_coverage(tmp_path):
    source = tmp_path / "water.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "bbox": [9.9, 53.5, 10.0, 53.6], "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[9.92, 53.52], [9.98, 53.52], [9.98, 53.58], [9.92, 53.58], [9.92, 53.52]]]}}]}))
    destination = tmp_path / "water.mbtiles"
    assert create_water_mask(source, destination, 12) <= 512
    water = mask(tmp_path / "cache")
    try:
        water.set_offline(True, MBTiles(destination, water_mask=True))
        assert water.is_water(53.55, 9.95) is True
        assert water.is_water(53.51, 9.91) is False
    finally:
        water.close()


def test_offline_settings_restore_and_do_not_stop_local_io(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIP_SIMULATOR_HEADLESS_MAP", "1")
    from maritime_simulator.ui.main_window import V2MainWindow
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        for channel in ("ROR", "RSA"):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            window.input_runtime.update_endpoint(channel, host="127.0.0.1", port=port)
        endpoint = {"host": "127.0.0.1", "port": port}
        window.input_runtime.update(xdr_order={"endpoint": endpoint}, xdr_response={"endpoint": endpoint},
            bow_thruster={"primary": endpoint, "xdr": endpoint}, engine={"primary": endpoint, "xdr": endpoint})
        app.processEvents()
        window.input_runtime.ensure_started()
        deadline = time.monotonic() + 2
        while not window.input_runtime.is_listening() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window.input_runtime.is_listening(), window.input_runtime.statuses()
        window._apply_offline_settings(OfflineSettings(True))
        payload = window._configuration_payload()
        assert payload["offline"]["enabled"]
        assert window.output_runtime.is_running()
        assert window.input_runtime.is_listening(), window.input_runtime.statuses()
        window.startup_profile.capture_fixed("offline", payload, select=True)
        defaults = {**payload, "offline": OfflineSettings().payload()}
        assert window.startup_profile.resolve(defaults, defaults)["offline"]["enabled"]
        window._open_settings()
        assert window.settings_dialog.offline_panel.enabled.isChecked()
        window._apply_offline_settings(OfflineSettings(False))
        assert not window.ais_controls.client.offline
    finally:
        window._configuration_baseline = window._configuration_payload()
        window.close()
        app.processEvents()


def test_offline_browser_blocking_keeps_loopback(tmp_path):
    from ui.webengine_profile import _network_allowed, set_webengine_offline
    set_webengine_offline(True)
    try:
        assert not _network_allowed(QUrl("https://tile.openstreetmap.org/1/0/0.png"))
        assert _network_allowed(QUrl("http://127.0.0.1:1000/tiles"))
        assert _network_allowed(QUrl("file:///tmp/map.html"))
    finally:
        set_webengine_offline(False)
