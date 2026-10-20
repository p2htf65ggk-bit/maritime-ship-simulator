# SPDX-License-Identifier: MPL-2.0
from contextlib import closing
import gzip
import io
import json
import os
import random
import sqlite3
from threading import Event
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

import mapbox_vector_tile
from PySide6.QtCore import Qt
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from maritime_simulator.visual.offline import MBTiles, OfflineSettings
from maritime_simulator.visual import offline_areas as areas


@pytest.fixture(scope="session")
def app():
    yield QApplication.instance() or QApplication([])


def vector_tile():
    def polygon(points, properties=None):
        return {"geometry": {"type": "Polygon", "coordinates": [points]}, "properties": properties or {}}
    earth = polygon([[0,0],[4096,0],[4096,4096],[0,4096],[0,0]])
    water = polygon([[0,0],[2048,0],[2048,4096],[0,4096],[0,0]])
    pier = polygon([[512,512],[1024,512],[1024,1024],[512,1024],[512,512]], {"kind": "pier"})
    return mapbox_vector_tile.encode([
        {"name": "earth", "features": [earth]},
        {"name": "water", "features": [water]},
        {"name": "landuse", "features": [pier]},
    ], default_options={"y_coord_down": True})


def archive(bounds_list, zoom=13):
    output = io.BytesIO()
    writer = Writer(output)
    coordinates = sorted({item for bounds in bounds_list for item in areas.area_tiles(bounds, zoom)}, key=lambda xyz: zxy_to_tileid(*xyz))
    for xyz in coordinates:
        writer.write_tile(zxy_to_tileid(*xyz), gzip.compress(vector_tile()))
    writer.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP}, {})
    payload = output.getvalue()
    class Source:
        url = "https://build.protomaps.com/test.pmtiles"
        def __call__(self, offset, length):
            return payload[offset:offset+length]
    return lambda *_: Source()


BOUNDS = [9.947,53.534,9.951,53.537]


def test_export_disk_budget_counts_selected_unique_tiles_not_whole_package(tmp_path, app, monkeypatch):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    other = [10.247,53.534,10.251,53.537]
    areas.download_areas([dict(bounds=b, max_zoom=13, name=f"Area {i}") for i,b in enumerate([BOUNDS, other])],
                         "Maps", tmp_path, source_factory=archive([BOUNDS, other]), build_resolver=lambda _: "fixture")
    source = tmp_path / areas.AREAS_FILE
    # Simulate a large unselected region in a valid package.
    with closing(sqlite3.connect(source)) as db:
        db.execute("UPDATE tiles SET tile_data=? WHERE zoom_level=13 AND tile_column=?",
                   (b"x" * (4 * 1024**2), areas.area_tiles(other, 13)[-1][1]))
        db.commit()
    saved = areas.saved_areas(tmp_path)
    package, clean = areas._transfer_package(source)
    selected_size = areas._export_size(package, clean[:1], Event(), lambda *_: None)
    monkeypatch.setattr(areas.shutil, "disk_usage", lambda *_: usage(1024**3, 0, selected_size + 64 * 1024**2 + 1024))
    assert source.stat().st_size > selected_size + 1024
    destination = tmp_path / "selection.mbtiles"
    areas.export_areas(tmp_path, saved[:1], destination)
    assert len(areas._transfer_package(destination)[1]) == 1
    assert destination.stat().st_size < source.stat().st_size


def test_export_size_deduplicates_shared_tiles_and_checks_cancellation(tmp_path, app):
    overlap = [9.9475,53.5345,9.9505,53.5365]
    areas.download_areas([dict(bounds=b, max_zoom=13, name=f"Area {i}") for i,b in enumerate([BOUNDS, overlap])],
                         "Maps", tmp_path, source_factory=archive([BOUNDS, overlap]), build_resolver=lambda _: "fixture")
    package, clean = areas._transfer_package(tmp_path / areas.AREAS_FILE)
    both = areas._export_size(package, clean, Event(), lambda *_: None)
    one = areas._export_size(package, clean[:1], Event(), lambda *_: None)
    # Overlap adds metadata, not duplicate tile BLOBs or index rows.
    assert both - one == len(json.dumps(clean).encode()) - len(json.dumps(clean[:1]).encode())
    cancel = Event()
    with pytest.raises(areas.DownloadCancelled, match="cancelled"):
        areas._export_size(package, clean, cancel, lambda *_: cancel.set())


def test_export_low_disk_space_preserves_existing_destination(tmp_path, app, monkeypatch):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    areas.download_area(BOUNDS, "Area", tmp_path, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    destination = tmp_path / "selection.mbtiles"
    destination.write_bytes(b"original")
    monkeypatch.setattr(areas.shutil, "disk_usage", lambda *_: usage(1000, 1000, 0))
    with pytest.raises(ValueError, match="Insufficient"):
        areas.export_areas(tmp_path, areas.saved_areas(tmp_path), destination)
    assert destination.read_bytes() == b"original"
    assert not destination.with_name(destination.name + ".export").exists()


def test_full_export_never_reserves_more_than_original_package_plus_margin(tmp_path, app, monkeypatch):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    areas.download_area(BOUNDS, "Area", tmp_path, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    source = tmp_path / areas.AREAS_FILE
    free = source.stat().st_size + 64 * 1024**2
    monkeypatch.setattr(areas.shutil, "disk_usage", lambda *_: usage(1024**3, 0, free))
    destination = tmp_path / "full.mbtiles"
    areas.export_areas(tmp_path, areas.saved_areas(tmp_path), destination)
    assert areas._transfer_package(destination)[1][0]["name"] == "Area"


def wait_for_maps(manager):
    for _ in range(500):
        QTest.qWait(10)
        if manager.job is None:
            return
    pytest.fail("Offline map worker did not finish")


def test_rectangle_and_detail_are_bounded():
    assert len(areas.area_tiles(BOUNDS)) < 40
    for invalid in ([170,0,-170,10], [0,0,1,float("nan")], [-180,-84,180,84]):
        with pytest.raises(ValueError):
            areas.area_tiles(invalid)
    assert 2500 < len(areas.area_tiles([9.7, 53.4, 10.3, 53.8])) < 20_000


def test_large_areas_are_allowed_up_to_raised_memory_safety_limit():
    bounds = [9.0, 52.8, 11.0, 54.0]
    count = areas.area_tile_count(bounds, 15)
    assert areas.LARGE_AREA_TILES < count < areas.MAX_TILES
    assert areas.MAX_TILES == 200_000
    assert len(areas.area_tiles(bounds, 15)) == count
    assert areas.selection_tile_count([{"bounds": bounds, "max_zoom": 15}] * 32) == count


def test_fast_union_count_matches_real_tiles_for_overlapping_and_mixed_detail():
    generator = random.Random(42)
    for size in (1, 2, 8, 32):
        selections = []
        for _ in range(size):
            west, south = 9.8 + generator.random() / 10, 53.5 + generator.random() / 10
            selections.append({"bounds": [west, south, west + generator.random() / 10, south + generator.random() / 10],
                               "max_zoom": generator.choice((13, 14, 15))})
        assert areas.selection_tile_count(selections) == len(areas.selection_tiles(selections)[0])


def polygon_area(points, zoom=15, name="Polygon"):
    return dict(bounds=[min(p[0] for p in points), min(p[1] for p in points),
                        max(p[0] for p in points), max(p[1] for p in points)],
                polygon=points, max_zoom=zoom, name=name)


def test_polygon_tiles_match_geometry_not_its_bounding_rectangle():
    from shapely.geometry import Polygon, box
    import math
    points = [[9.9,53.5], [10.1,53.5], [10.1,53.54], [9.94,53.54],
              [9.94,53.66], [10.1,53.66], [10.1,53.7], [9.9,53.7]]
    area = polygon_area(points)
    rectangle = set(areas.area_tiles(area["bounds"], 15))
    selected = set(areas.area_tiles(area["bounds"], 15, points))
    expected = set()
    for z in range(16):
        scale = 2**z
        shape = Polygon([((lon+180)/360*scale, (1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*scale)
                         for lon,lat in points])
        expected.update((z,x,y) for level,x,y in rectangle if level == z and shape.intersects(box(x,y,x+1,y+1)))
    assert selected == expected
    assert selected < rectangle
    assert areas.area_tile_count(area["bounds"], 15, points) == len(selected)
    mixed = [area, dict(bounds=[9.98,53.52,10.04,53.58], max_zoom=14)]
    assert areas.selection_tile_count(mixed) == len(areas.selection_tiles(mixed)[0])


@pytest.mark.parametrize("points", [
    [[9.9,53.5], [10.1,53.7], [9.9,53.7], [10.1,53.5]],
    [[9.9,53.5], [10.0,53.6], [10.1,53.7]],
    [[9.9,53.5], [10.1,53.5]],
    [[9.9,53.5], [10.1,53.5], [10.1,float("nan")]],
])
def test_invalid_polygons_are_rejected(points):
    with pytest.raises(ValueError):
        areas.normalize_area(dict(bounds=[9.9,53.5,10.1,53.7], max_zoom=15, polygon=points))


def test_polygon_identity_is_independent_of_first_corner_or_winding():
    points = [[9.9,53.5], [10.1,53.5], [10.1,53.7]]
    area = polygon_area(points)
    assert areas.area_identity(area) == areas.area_identity(polygon_area(points[1:]+points[:1]))
    assert areas.area_identity(area) == areas.area_identity(polygon_area(list(reversed(points))+[points[-1]]))
    assert areas.area_identity(area) != areas.area_identity(polygon_area([[9.9,53.5], [9.9,53.7], [10.1,53.7]]))
    assert areas.area_identity(area) != areas.area_identity(dict(bounds=area["bounds"], max_zoom=15))
    with pytest.raises(ValueError, match="bounds"):
        areas.normalize_area(dict(area, bounds=[9.8,53.5,10.1,53.7]))


def test_polygon_ui_targets_correct_area_when_bounds_match(app, tmp_path):
    from maritime_simulator.ui.offline_panel import OfflineAreaManager
    manager = OfflineAreaManager(tmp_path)
    first = polygon_area([[9.9,53.5], [10.1,53.5], [10.1,53.7]], name="First")
    second = polygon_area([[9.9,53.5], [9.9,53.7], [10.1,53.7]], name="Second")
    try:
        manager.queue_areas([first, second])
        with pytest.raises(ValueError, match="already listed"):
            manager.queue_areas([dict(first, polygon=list(reversed(first["polygon"])))])
        manager._area_progress([dict(second, done=3, total=10, content_bytes=1024, status="Downloading")])
        assert manager.table.item(0,4).text() == "Queued"
        assert manager.table.item(1,4).text() == "Downloading"
        assert "Polygon: 3 corners" in manager.table.item(1,0).toolTip()
        manager.table.itemDelegate().action.emit(1, True)
        assert [item["name"] for item in manager.pending] == ["First"]
        assert manager.table.rowCount() == 1
    finally:
        manager.shutdown()
        manager.close()


def test_polygon_roundtrip_and_delete_preserve_retained_overlap(app, tmp_path):
    first = polygon_area([[9.94,53.53], [10.0,53.53], [10.0,53.57]], name="First")
    second = polygon_area([[9.94,53.53], [9.94,53.57], [10.0,53.57]], name="Second")
    rectangle = dict(bounds=first["bounds"], max_zoom=14, name="Rectangle")
    source, target = tmp_path / "source", tmp_path / "target"
    factory = archive([first["bounds"]], zoom=15)
    areas.download_areas([first, second, rectangle], "Test", source,
                         source_factory=factory, build_resolver=lambda _: "fixture")
    original = areas.saved_areas(source)
    assert len(original) == 3
    exported = tmp_path / "polygons.mbtiles"
    areas.export_areas(source, original[:2], exported)
    assert areas.import_areas(target, exported)[1:] == (2,0)
    assert areas.import_areas(target, exported)[1:] == (0,2)
    assert [a["polygon"] for a in areas.saved_areas(target)] == [a["polygon"] for a in original[:2]]
    retained = original[1:]
    expected, water = areas.selection_tiles(retained)
    path = source / areas.AREAS_FILE
    with closing(sqlite3.connect(path)) as db:
        before = {(z,x,y): db.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                                      (z,x,2**z-1-y)).fetchone()[0] for z,x,y in expected}
    areas.manage_areas(source, retained, original, build_resolver=lambda _: pytest.fail("Deletion must work offline"))
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0] == len(expected)
        for (z,x,y), payload in before.items():
            assert db.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                              (z,x,2**z-1-y)).fetchone() == (payload,)
    changed = [dict(a, name="Renamed" if i == 0 else a["name"]) for i,a in enumerate(retained)]
    areas.manage_areas(source, changed, retained, build_resolver=lambda _: pytest.fail("Rename must work offline"))
    assert areas.saved_areas(source)[0]["polygon"] == retained[0]["polygon"]


def test_tile_reader_is_batched_ordered_and_serial_between_areas():
    from threading import get_ident
    from types import SimpleNamespace
    coordinates = [(15,i,2) for i in range(80)]
    requested, identities = [], set()
    class Reader:
        source = object()
        def prefetch(self, batch):
            requested.append(batch)
            identities.add(get_ident())
        def get(self, z,x,y):
            return bytes([x]) * (1024**2)
    reader = Reader()
    tracker = SimpleNamespace(members={xyz:[0 if i < 40 else 1] for i,xyz in enumerate(coordinates)})
    with closing(areas._tile_payloads(reader, coordinates, Event(), tracker)) as payloads:
        for index, xyz, data in payloads:
            assert xyz == coordinates[index] and data[0] == index
            if index == 39:
                assert requested == [coordinates[:40]], "The next area must wait until this area's final tile is rendered"
            time.sleep(0.001)
    assert requested == [coordinates[:40],coordinates[40:]]
    assert identities == {get_ident()}


def test_tile_reader_cancel_and_errors_stop_the_download():
    from threading import Thread
    cancel = Event()
    source = areas.RangeSource("fixture", cancel)
    class Reader:
        def __init__(self):
            self.source = source
        def prefetch(self, _):
            if self.source.cancel.wait(10):
                areas.check_cancel(self.source.cancel)
        def get(self, *_):
            pytest.fail("Cancelled prefetch must not continue")
    trigger = Thread(target=lambda: (time.sleep(0.05), cancel.set()))
    trigger.start()
    start = time.monotonic()
    with pytest.raises(areas.DownloadCancelled), closing(areas._tile_payloads(Reader(), [(0,0,0)], cancel)) as payloads:
        next(payloads)
    trigger.join()
    assert time.monotonic() - start < 1
    assert source.cancel is cancel
    class Broken:
        source = object()
        def prefetch(self, _):
            raise OSError("Download connection lost")
    with pytest.raises(OSError, match="connection lost"), closing(areas._tile_payloads(Broken(), [(0,0,0)], Event())) as payloads:
        next(payloads)


def test_large_area_ui_warns_but_does_not_block_or_allocate_tile_lists(app, tmp_path, monkeypatch):
    from maritime_simulator.ui import offline_panel
    panel = offline_panel.OfflinePanel(lambda: OfflineSettings(), lambda _: None, tmp_path, lambda: None)
    monkeypatch.setattr(areas, "area_tiles", lambda *_args, **_kwargs: pytest.fail("The UI must not allocate every tile"))
    panel.manager.queue_areas([{"bounds": [9.0, 52.8, 11.0, 54.0], "max_zoom": 15}])
    assert panel.manager.download.isEnabled()
    questions = []
    monkeypatch.setattr(offline_panel, "ask_question", lambda _parent, title, text: questions.append(text) or QMessageBox.Yes)
    assert panel.manager._confirm_download(panel.manager.pending)
    assert "Very large selection" in questions[0]
    assert "MB" not in questions[0]
    panel.shutdown()
    panel.close()


def test_rendered_map_and_water_have_correct_holes_and_piers(app):
    tile, water = areas.rasterize_tile(vector_tile(), compressed=False)
    assert QImage.fromData(tile).width() == 512
    image = QImage.fromData(water)
    assert image.pixelColor(20, 128).red() == 255
    assert image.pixelColor(200, 128).red() == 0
    assert image.pixelColor(48, 48).red() == 0  # pier must remain land above water


def test_download_merges_areas_and_water_in_one_local_file(app, tmp_path):
    other = [10.2,53.7,10.21,53.71]
    source = archive([BOUNDS, other])
    for bounds, name in ((BOUNDS, "Harbour"), (other, "Second area")):
        areas.download_area(bounds, name, tmp_path, max_zoom=13, source_factory=source, build_resolver=lambda _: "fixture")
    assert len(areas.saved_areas(tmp_path)) == 2
    assert [path.name for path in tmp_path.iterdir()] == [areas.AREAS_FILE]
    path = tmp_path / areas.AREAS_FILE
    assert MBTiles(path).max_zoom == 13
    assert MBTiles(path, water_mask=True).table == "water_tiles"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM water_tiles").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM vector_tiles").fetchone()[0] >= 2


def test_cancelled_download_keeps_previous_areas_exactly(app, tmp_path):
    source = archive([BOUNDS])
    areas.download_area(BOUNDS, "Original", tmp_path, max_zoom=13, source_factory=source, build_resolver=lambda _: "fixture")
    before = (tmp_path / areas.AREAS_FILE).read_bytes()
    cancel = Event()
    def progress(count, *_):
        if count > 0:
            cancel.set()
    with pytest.raises(areas.DownloadCancelled):
        areas.download_area(BOUNDS, "Cancelled", tmp_path, max_zoom=13, cancel=cancel, progress=progress, source_factory=source, build_resolver=lambda _: "fixture")
    assert (tmp_path / areas.AREAS_FILE).read_bytes() == before
    assert not (tmp_path / (areas.AREAS_FILE + ".download")).exists()


def test_range_cache_and_no_full_planet_download(monkeypatch):
    calls = []
    def response(_url, *, limit, headers, cancel):
        calls.append(headers)
        offset = int(headers["Range"].split("=")[1].split("-")[0])
        return b"x" * limit, 206, {"Content-Range": f"bytes {offset}-{offset+limit-1}/1000", "ETag": "same"}
    monkeypatch.setattr(areas, "_read_response", response)
    source = areas.RangeSource("https://build.protomaps.com/fixture", Event())
    assert source(0,127) == source(0,127)
    assert len(calls) == 1
    source(300,10)
    assert calls[-1]["If-Match"] == "same"
    monkeypatch.setattr(areas, "_read_response", lambda *_args, **_kwargs: (b"x", 200, {}))
    with pytest.raises(ValueError, match="partial archive"):
        source(400,1)


def test_offline_panel_embeds_map_manager_and_add_area(app, tmp_path):
    from maritime_simulator.ui.offline_panel import OfflinePanel
    selected = []
    panel = OfflinePanel(lambda: OfflineSettings(), lambda _: None, tmp_path, lambda: selected.append(True))
    assert panel.enabled.text() == "Offline mode"
    assert panel.isAncestorOf(panel.manager)
    assert panel.map_tabs.widget(0) is panel.manager
    assert panel.map_tabs.widget(1) is panel.s57
    assert panel.map_tabs.tabText(0) == "Protomaps"
    assert panel.map_tabs.tabText(1) == "S57 / ENC"
    assert not panel.manager.isWindow()
    assert panel.manager.table.rowCount() == 0
    panel.manager.add_area.click()
    assert selected == [True]
    panel.manager.queue_areas([{"bounds": BOUNDS, "max_zoom": 15}])
    assert panel.manager.download.isEnabled()
    assert panel.manager.table.rowCount() == 1
    assert panel.manager.table.cellWidget(0, 1).currentData() == 15
    assert panel.manager.table.item(0, 4).text() == "Queued"
    assert not hasattr(panel.manager, "apply")
    assert not hasattr(panel.manager, "discard")
    panel.close()


def test_offline_columns_stay_stable_during_progress_and_row_rebuilds(app, tmp_path):
    from maritime_simulator.ui.offline_panel import OfflineAreaManager
    manager = OfflineAreaManager(tmp_path)
    manager.queue_areas([{"bounds": BOUNDS, "max_zoom": 13, "name": "A very long offline area name which should not expand the column"}])
    manager.resize(1100, 500)
    manager.show()
    QTest.qWait(10)
    widths = [manager.table.columnWidth(column) for column in range(8)]
    name_width = max(manager.table.fontMetrics().horizontalAdvance("Offline area name"),
                     manager.table.horizontalHeader().fontMetrics().horizontalAdvance("Area name")) + 30
    assert widths[0] == 2 * name_width
    for state in ("Reading", "Downloading", "Finalizing", "Downloaded", "Queued", "Cancelled"):
        manager._area_progress([{"bounds": BOUNDS, "done": 150_000, "total": 200_000,
                                 "content_bytes": 900 * 1024**2, "status": state}])
        app.processEvents()
        assert [manager.table.columnWidth(column) for column in range(8)] == widths
    manager._populate()
    manager.resize(720, 500)
    app.processEvents()
    assert [manager.table.columnWidth(column) for column in range(8)] == widths
    assert manager.table.fontMetrics().horizontalAdvance("200,000 / 200,000") < widths[2]
    assert manager.table.fontMetrics().horizontalAdvance("Downloading") < widths[4]
    assert "A very long offline area name" in manager.table.item(0, 0).toolTip()
    manager.close()


def test_map_selection_receives_saved_coverage_without_changing_it(app, monkeypatch):
    from ui.widgets.map_widget import MapWidget
    widget = MapWidget()
    widget.loaded = True
    widget.view = object()
    scripts = []
    monkeypatch.setattr(widget, "_run_map_script", lambda script, kind: scripts.append((script, kind)))
    saved = [{"bounds": BOUNDS, "max_zoom": 14, "name": "Harbour <West>"}]
    widget.begin_offline_area_selection(saved)
    script, kind = scripts.pop()
    options = json.loads(script.removeprefix("beginOfflineAreaSelection(").removesuffix(");"))
    assert kind == "offline_selection"
    assert options["downloadedAreas"] == saved
    assert options["maxTiles"] == areas.MAX_TILES
    assert options["warningTiles"] == areas.LARGE_AREA_TILES
    assert set(options) == {"maxTiles", "warningTiles", "downloadedAreas"}
    widget.begin_offline_area_selection()
    assert '"downloadedAreas": []' in scripts[-1][0]
    assert saved == [{"bounds": BOUNDS, "max_zoom": 14, "name": "Harbour <West>"}]
    widget.view = None
    widget.deleteLater()
    app.processEvents()


def test_embedded_manager_saves_names_immediately_and_keeps_queued_areas(app, tmp_path):
    from maritime_simulator.ui.offline_panel import OfflinePanel
    panel = OfflinePanel(lambda: OfflineSettings(), lambda _: None, tmp_path, lambda: None)
    areas.download_area(BOUNDS, "Harbour", tmp_path, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    panel.refresh()
    manager = panel.manager
    assert manager.table.rowCount() == 1
    manager.table.item(0, 0).setText("Saved rename")
    assert not manager.add_area.isEnabled()
    panel.refresh()
    wait_for_maps(manager)
    assert areas.saved_areas(tmp_path)[0]["name"] == "Saved rename"
    assert manager.add_area.isEnabled()
    other = [10.2,53.7,10.21,53.71]
    manager.queue_areas([{"bounds": other, "max_zoom": 13}])
    manager.table.item(1, 0).setText("Pending download")
    panel.refresh()
    assert manager.table.item(1, 0).text() == "Pending download"
    assert manager.table.item(1, 3).text() == "--"
    assert manager.table.item(1, 4).text() == "Queued"
    assert manager.download.isEnabled()
    panel.shutdown()
    panel.close()


def test_embedded_manager_applies_in_background_and_keeps_errors_visible(app, tmp_path, monkeypatch):
    from maritime_simulator.ui import offline_panel
    areas.download_area(BOUNDS, "Harbour", tmp_path, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    changed = []
    panel = offline_panel.OfflinePanel(lambda: OfflineSettings(True), changed.append, tmp_path, lambda: None)
    manager = panel.manager
    manager.table.item(0, 0).setText("Renamed harbour")
    assert not manager.add_area.isEnabled()
    assert manager.cancel.isVisibleTo(manager)
    wait_for_maps(manager)
    assert changed[-1] == OfflineSettings(True, areas.AREAS_FILE, areas.AREAS_FILE)
    assert manager.table.item(0, 0).text() == "Renamed harbour"
    assert manager.add_area.isEnabled()
    assert not manager.download.isEnabled()
    def fail(*_args, **_kwargs):
        raise ValueError("Fixture update failed; previous maps retained")
    monkeypatch.setattr(offline_panel, "manage_areas", fail)
    manager.table.item(0, 0).setText("Uncommitted")
    wait_for_maps(manager)
    assert "Fixture update failed" in manager.status.text()
    assert areas.saved_areas(tmp_path)[0]["name"] == "Renamed harbour"
    assert manager.table.item(0, 0).text() == "Renamed harbour"
    panel.shutdown()
    panel.close()


def test_manager_renames_deletes_and_keeps_shared_tiles(app, tmp_path):
    other = [10.2,53.7,10.21,53.71]
    source = archive([BOUNDS, other])
    selections = [{"bounds": bounds, "max_zoom": 13, "name": name}
                  for bounds, name in ((BOUNDS, "Harbour"), (other, "Other"))]
    areas.download_areas(selections, "Test", tmp_path, source_factory=source, build_resolver=lambda _: "fixture")
    original = areas.saved_areas(tmp_path)
    renamed = [dict(item, name="Renamed") if index == 0 else item for index, item in enumerate(original)]
    areas.manage_areas(tmp_path, renamed, original, build_resolver=lambda _: pytest.fail("Rename should not require Internet"))
    assert areas.saved_areas(tmp_path)[0]["name"] == "Renamed"
    path = tmp_path / areas.AREAS_FILE
    size = path.stat().st_size
    areas.manage_areas(tmp_path, renamed[:1], renamed)
    with closing(sqlite3.connect(path)) as db:
        tiles = {(z, x, 2**z - 1 - row) for z, x, row in db.execute("SELECT zoom_level,tile_column,tile_row FROM tiles")}
    assert tiles == set(areas.area_tiles(BOUNDS, 13))
    assert path.stat().st_size < size
    assert areas.manage_areas(tmp_path, [], renamed[:1]) == ""
    assert not path.exists()


def test_manager_renews_and_changes_detail_atomically(app, tmp_path):
    source = archive([BOUNDS], zoom=15)
    areas.download_area(BOUNDS, "Original", tmp_path, max_zoom=13, source_factory=source, build_resolver=lambda _: "fixture")
    original = areas.saved_areas(tmp_path)
    calls = []
    def resolver(_):
        calls.append(True)
        return "fixture"
    areas.manage_areas(tmp_path, [dict(original[0], refresh=True)], original,
                       source_factory=source, build_resolver=resolver)
    assert calls == [True]
    assert "refresh" not in areas.saved_areas(tmp_path)[0]
    original = areas.saved_areas(tmp_path)
    areas.manage_areas(tmp_path, [dict(original[0], max_zoom=15)], original,
                       source_factory=source, build_resolver=resolver)
    assert MBTiles(tmp_path / areas.AREAS_FILE).max_zoom == 15
    original = areas.saved_areas(tmp_path)
    areas.manage_areas(tmp_path, [dict(original[0], max_zoom=13)], original,
                       source_factory=source, build_resolver=resolver)
    assert MBTiles(tmp_path / areas.AREAS_FILE).max_zoom == 13
    assert MBTiles(tmp_path / areas.AREAS_FILE, water_mask=True).zoom_levels == [13]
    path = tmp_path / areas.AREAS_FILE
    before = path.read_bytes()
    original = areas.saved_areas(tmp_path)
    cancel = Event()
    def progress(count, _total, text):
        if count > 0 and text == "Downloading maps...":
            cancel.set()
    with pytest.raises(areas.DownloadCancelled):
        areas.manage_areas(tmp_path, [dict(original[0], max_zoom=15)], original, cancel=cancel, progress=progress,
                           source_factory=source, build_resolver=resolver)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(ValueError, match="changed"):
        areas.manage_areas(tmp_path, original, [])
    with pytest.raises(ValueError, match="duplicate"):
        areas.manage_areas(tmp_path, original * 2, original)
    lock = areas._package_lock(tmp_path)
    try:
        with pytest.raises(ValueError, match="active"):
            areas.manage_areas(tmp_path, original, original)
    finally:
        lock.unlock()


def test_manager_delete_buttons_keep_their_identity_after_updates(app, tmp_path, monkeypatch):
    from maritime_simulator.ui import offline_panel
    monkeypatch.setattr(offline_panel, "ask_question", lambda *_args, **_kwargs: QMessageBox.Yes)
    other = [10.2,53.7,10.21,53.71]
    source = archive([BOUNDS, other])
    areas.download_areas([{"bounds": bounds, "max_zoom": 13, "name": str(index)} for index, bounds in enumerate((BOUNDS, other))],
                         "Test", tmp_path, source_factory=source, build_resolver=lambda _: "fixture")
    manager = offline_panel.OfflineAreaManager(tmp_path)
    manager.table.itemDelegate().action.emit(0, True)
    wait_for_maps(manager)
    assert manager.table.rowCount() == 1
    assert manager.table.item(0, 0).text() == "1"
    assert manager.table.item(0, 6).text() == "Renew"
    manager.table.itemDelegate().action.emit(0, True)
    wait_for_maps(manager)
    assert manager.table.rowCount() == 0
    assert not (tmp_path / areas.AREAS_FILE).exists()
    manager.shutdown()
    manager.close()


def test_batch_mixed_detail_deduplicates_and_is_atomic(app, tmp_path):
    other = [10.2,53.7,10.21,53.71]
    selections = [{"bounds": BOUNDS, "max_zoom": 15, "name": "Harbour"},
                  {"bounds": other, "max_zoom": 13, "name": "Regional area"}]
    source = archive([BOUNDS, other], zoom=15)
    progress = []
    areas.download_areas(selections, "Test", tmp_path, source_factory=source, build_resolver=lambda _: "fixture",
                         progress=lambda count, total, _text: progress.append((count, total)))
    saved = areas.saved_areas(tmp_path)
    assert [(item["name"], item["max_zoom"]) for item in saved] == [("Harbour", 15), ("Regional area", 13)]
    count = len(areas.selection_tiles(selections)[0])
    assert count < sum(len(areas.area_tiles(item["bounds"], item["max_zoom"])) for item in selections)
    assert progress[-1] == (count, count)
    path = tmp_path / areas.AREAS_FILE
    with closing(sqlite3.connect(path)) as db:
        assert {row[0] for row in db.execute("SELECT DISTINCT zoom_level FROM vector_tiles")} == {13, 15}
        assert db.execute("SELECT COUNT(*) FROM water_tiles").fetchone()[0] == 0
    package = MBTiles(path)
    z, x, y = next(tile for tile in areas.area_tiles(other, 13) if tile[0] == 13)
    assert package.tile(z + 1, x * 2, y * 2) is None
    assert QImage.fromData(package.display_tile(z + 1, x * 2, y * 2)).width() == 512
    assert package.display_tile(15, 0, 0) is None
    before = path.read_bytes()
    cancel = Event()
    def cancel_halfway(count, total, _):
        if count >= total // 2:
            cancel.set()
    with pytest.raises(areas.DownloadCancelled):
        areas.download_areas(selections, "Cancelled", tmp_path, cancel=cancel, progress=cancel_halfway,
                             source_factory=source, build_resolver=lambda _: "fixture")
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_each_queued_rectangle_detail_and_name_are_editable_in_offline_tab(app, tmp_path):
    from maritime_simulator.ui.offline_panel import OfflineAreaManager
    manager = OfflineAreaManager(tmp_path)
    other = [10.2,53.7,10.21,53.71]
    manager.queue_areas([{"bounds": BOUNDS, "max_zoom": 15}, {"bounds": other, "max_zoom": 13}])
    detail = manager.table.cellWidget(0, 1)
    detail.setCurrentIndex(detail.findData(14))
    manager.table.item(1, 0).setText("Overview region")
    manager._sync_pending()
    selections = manager.pending
    assert [item["max_zoom"] for item in selections] == [14, 13]
    assert selections[1]["name"] == "Overview region"
    assert "unique tiles" in manager.summary.text()
    assert manager.download.isEnabled()
    manager.table.itemDelegate().action.emit(0, True)
    assert manager.table.rowCount() == 1
    assert manager.pending[0]["name"] == "Overview region"
    assert manager.pending[0]["bounds"] == other
    manager.shutdown()
    manager.close()


@pytest.mark.parametrize("deleted_zoom,retained_zoom", [(15, 13), (13, 15), (15, 15)])
@pytest.mark.parametrize("fast_delete", [False, True])
def test_deleting_overlapping_areas_preserves_every_retained_map_and_water_tile(app, tmp_path, deleted_zoom, retained_zoom, fast_delete, monkeypatch):
    monkeypatch.setattr(areas, "FAST_DELETE_BYTES", 0 if fast_delete else 2**63)
    other = [9.949,53.535,9.955,53.539]
    selections = [{"bounds": BOUNDS, "max_zoom": deleted_zoom, "name": "Delete"},
                  {"bounds": other, "max_zoom": retained_zoom, "name": "Keep"}]
    areas.download_areas(selections, "Test", tmp_path, source_factory=archive([BOUNDS, other], zoom=15),
                         build_resolver=lambda _: "fixture")
    path = tmp_path / areas.AREAS_FILE
    needed = set(areas.area_tiles(other, retained_zoom))
    finest = {coordinate for coordinate in needed if coordinate[0] == retained_zoom}
    water = areas.rasterize_water_mask(vector_tile(), compressed=False)
    with closing(sqlite3.connect(path)) as db:
        db.executemany("INSERT OR REPLACE INTO water_tiles VALUES (?,?,?,?)", ((z,x,2**z-1-y,water) for z,x,y in finest))
        db.commit()
        def contents(table, coordinates):
            return {(z,x,2**z-1-y): db.execute(f"SELECT tile_data FROM {table} WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                                            (z,x,2**z-1-y)).fetchone()[0] for z,x,y in coordinates}
        expected = {"tiles": contents("tiles", needed), "vector_tiles": contents("vector_tiles", finest),
                    "water_tiles": contents("water_tiles", finest)}
    original = areas.saved_areas(tmp_path)
    areas.manage_areas(tmp_path, original[1:], original,
                       build_resolver=lambda _: pytest.fail("Deletion must not access the Internet"))
    with closing(sqlite3.connect(path)) as db:
        for table, before in expected.items():
            actual = {(z,x,y): blob for z,x,y,blob in db.execute(f"SELECT * FROM {table}")}
            assert actual == before
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert [area["name"] for area in areas.saved_areas(tmp_path)] == ["Keep"]
    assert MBTiles(path, water_mask=True).zoom_levels == [retained_zoom]


def test_large_package_deletion_rolls_back_all_tables_and_metadata(app, tmp_path, monkeypatch):
    other = [10.2,53.7,10.21,53.71]
    selections = [dict(bounds=bounds, max_zoom=13, name=str(index)) for index, bounds in enumerate((BOUNDS, other))]
    areas.download_areas(selections, "Test", tmp_path, source_factory=archive([BOUNDS, other]),
                         build_resolver=lambda _: "fixture")
    original = areas.saved_areas(tmp_path)
    path = tmp_path / areas.AREAS_FILE
    before = path.read_bytes()
    monkeypatch.setattr(areas, "FAST_DELETE_BYTES", 0)
    monkeypatch.setattr(areas, "_replace_package", lambda *_: pytest.fail("Deletion must not copy or replace the package"))
    cancelled = Event()
    def progress(done, _total, text):
        if done == 3 and text == "Deleting unused offline tiles...":
            cancelled.set()
    with pytest.raises(areas.DownloadCancelled):
        areas.manage_areas(tmp_path, original[1:], original, cancel=cancelled, progress=progress)
    assert path.read_bytes() == before
    assert areas.saved_areas(tmp_path) == original
    assert not (tmp_path / (areas.AREAS_FILE + ".update")).exists()


def test_rename_does_not_reload_active_map_or_rebuild_s57_table(app, tmp_path, monkeypatch):
    from maritime_simulator.ui import offline_panel
    areas.download_area(BOUNDS, "Harbour", tmp_path, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    settings = OfflineSettings(True, areas.AREAS_FILE, areas.AREAS_FILE)
    changed = []
    panel = offline_panel.OfflinePanel(lambda: settings, changed.append, tmp_path, lambda: None)
    monkeypatch.setattr(panel.s57, "refresh", lambda: pytest.fail("Protomaps rename must not rebuild ENC cells"))
    monkeypatch.setattr(areas, "_record_area_sizes", lambda *_: pytest.fail("Known tile sizes must not be recalculated on rename"))
    try:
        panel.manager.table.item(0,0).setText("Renamed harbour")
        wait_for_maps(panel.manager)
        assert not changed
        assert areas.saved_areas(tmp_path)[0]["name"] == "Renamed harbour"
    finally:
        panel.shutdown()
        panel.close()


def test_batch_download_is_serial_deduplicated_and_reports_exact_per_area_sizes(app, tmp_path, monkeypatch):
    other = [10.2,53.7,10.21,53.71]
    selections = [{"bounds": BOUNDS, "max_zoom": 15, "name": "First"},
                  {"bounds": other, "max_zoom": 13, "name": "Second"}]
    original_reader = areas._BasemapReader
    calls = []
    class RecordingReader(original_reader):
        def get(self, z, x, y):
            calls.append((z,x,y))
            return super().get(z,x,y)
    monkeypatch.setattr(areas, "_BasemapReader", RecordingReader)
    progress = []
    areas.download_areas(selections, "Test", tmp_path, source_factory=archive([BOUNDS, other], zoom=15),
                         build_resolver=lambda _: "fixture", area_progress=progress.append)
    expected_order = list(dict.fromkeys(coordinate for area in selections
                                        for coordinate in sorted(areas.area_tiles(area["bounds"], area["max_zoom"]), key=lambda xyz: zxy_to_tileid(*xyz))))
    assert calls == expected_order
    assert len(calls) == len(set(calls))
    assert progress[0][0]["status"] == "Connecting"
    assert progress[0][1]["status"] == "Queued"
    saved = areas.saved_areas(tmp_path)
    for live, stored in zip(progress[-1], saved):
        assert live["status"] == "Downloaded"
        assert live["done"] == live["total"] == stored["tile_count"]
        assert live["content_bytes"] == stored["content_bytes"] > 0
        with closing(sqlite3.connect(tmp_path / areas.AREAS_FILE)) as db:
            total = 0
            for z,x,y in areas.area_tiles(stored["bounds"], stored["max_zoom"]):
                tables = ("tiles", "vector_tiles", "water_tiles") if z == stored["max_zoom"] else ("tiles",)
                for table in tables:
                    row = db.execute(f"SELECT length(tile_data) FROM {table} WHERE zoom_level=? AND tile_column=? AND tile_row=?", (z,x,2**z-1-y)).fetchone()
                    total += row[0] if row else 0
            assert total == stored["content_bytes"]


def test_range_prefetch_reuses_subranges_without_whole_archive_or_large_gaps(monkeypatch):
    payload = bytes(range(256)) * 32_768
    calls = []
    def response(_url, *, limit, headers, cancel):
        start, end = map(int, headers["Range"].split("=")[1].split("-"))
        calls.append((start, end))
        return payload[start:end+1], 206, {"Content-Range": f"bytes {start}-{end}/{len(payload)}", "ETag": "unchanged"}
    monkeypatch.setattr(areas, "_read_response", response)
    source = areas.RangeSource("fixture", Event())
    ranges = [(i * 1001, 1000) for i in range(128)]
    source.prefetch(ranges)
    assert len(calls) == 1
    for start, length in reversed(ranges):
        assert source(start, length) == payload[start:start+length]
    assert len(calls) == 1
    assert source.download_bytes == 128_127
    source.prefetch([(1_000_000, 10), (1_010_000, 10), (2_000_000, 10)])
    assert len(calls) == 4  # A small useful payload must not fetch large gaps.
    assert source.cache_bytes <= 8 * 1024**2
    source.cancel.set()
    with pytest.raises(areas.DownloadCancelled):
        source(0, 10)


def test_range_prefetch_cache_and_network_budget_are_bounded(monkeypatch):
    calls = []
    def response(_url, *, limit, headers, cancel):
        start, end = map(int, headers["Range"].split("=")[1].split("-"))
        calls.append((start, end))
        return b"x" * limit, 206, {"Content-Range": f"bytes {start}-{end}/{32 * 1024**2}", "ETag": "same"}
    monkeypatch.setattr(areas, "_read_response", response)
    source = areas.RangeSource("fixture", Event())
    for start in (0, 8 * 1024**2, 16 * 1024**2):
        source.prefetch([(start + i * 512 * 1024, 512 * 1024) for i in range(16)])
        assert source.cache_bytes <= 8 * 1024**2
    assert source.download_bytes == 12 * 1024**2  # At most 4 MiB per prefetch batch.
    source.download_bytes = areas.MAX_DOWNLOAD_BYTES
    before = len(calls)
    with pytest.raises(ValueError, match="1 GiB"):
        source(31 * 1024**2, 1000)
    assert len(calls) == before
    with pytest.raises(ValueError, match="beyond its end"):
        source(32 * 1024**2 - 1, 2)


def test_archive_consistency_is_checked_for_grouped_ranges(monkeypatch):
    source = areas.RangeSource("fixture", Event())
    source.total_size, source.etag = 10_000, "original"
    def response(_url, *, limit, headers, cancel):
        start = int(headers["Range"].split("=")[1].split("-")[0])
        return b"x" * limit, 206, {"Content-Range": f"bytes {start}-{start+limit-1}/10000", "ETag": "changed"}
    monkeypatch.setattr(areas, "_read_response", response)
    with pytest.raises(ValueError, match="archive changed"):
        source.prefetch([(100, 100), (200, 100)])
    assert source.download_bytes == 0
    assert source.cache_bytes == 0


def test_batched_pmtiles_reader_matches_library_and_reduces_requests(monkeypatch):
    from pmtiles.reader import Reader
    output = io.BytesIO()
    writer = Writer(output)
    coordinates = sorted(areas.area_tiles([9.94, 53.53, 9.98, 53.55], 15), key=lambda xyz: zxy_to_tileid(*xyz))
    tiles = {xyz: gzip.compress(vector_tile() + str(xyz).encode()) for xyz in coordinates}
    for xyz, data in tiles.items():
        writer.write_tile(zxy_to_tileid(*xyz), data)
    writer.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP}, {})
    payload = output.getvalue()
    requests = []
    def response(_url, *, limit, headers, cancel):
        start, end = map(int, headers["Range"].split("=")[1].split("-"))
        requests.append((start, end))
        return payload[start:end+1], 206, {"Content-Range": f"bytes {start}-{end}/{len(payload)}", "ETag": "fixture"}
    monkeypatch.setattr(areas, "_read_response", response)
    baseline = Reader(areas.RangeSource("fixture", Event()))
    expected = [baseline.get(*xyz) for xyz in coordinates]
    baseline_calls = len(requests)
    requests.clear()
    reader = areas._BasemapReader(areas.RangeSource("fixture", Event()))
    actual = []
    for start in range(0, len(coordinates), 64):
        batch = coordinates[start:start+64]
        reader.prefetch(batch)
        actual.extend(reader.get(*xyz) for xyz in batch)
    assert actual == expected == list(tiles.values())
    assert len(requests) < baseline_calls / 4
    assert reader.get(15, 0, 0) is None
    assert len(reader.batch) <= 64
    assert len(reader.directories) <= 32


def test_queued_areas_do_not_show_predicted_mb_and_large_tile_jobs_prompt(app, tmp_path, monkeypatch):
    from maritime_simulator.ui import offline_panel
    panel = offline_panel.OfflinePanel(lambda: OfflineSettings(), lambda _: None, tmp_path, lambda: None)
    manager = panel.manager
    manager.queue_areas([{"bounds": [9.9, 53.5, 10.2, 53.7], "max_zoom": 15}])
    assert int(manager.table.item(0, 2).text().replace(",", "")) >= 500
    assert manager.table.item(0, 3).text() == "--"
    assert "MB" not in manager.summary.text()
    assert "~" not in manager.summary.text()
    questions = []
    monkeypatch.setattr(offline_panel, "ask_question", lambda _parent, title, text: questions.append(text) or QMessageBox.No)
    assert not manager._confirm_download(manager.pending)
    assert "unique tiles" in questions[0]
    assert "size is only known during download" in questions[0]
    assert "MB" not in questions[0]
    panel.shutdown()
    panel.close()


def test_existing_packages_get_actual_sizes_without_network(app, tmp_path):
    areas.download_area(BOUNDS, "Legacy", tmp_path, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    original = areas.saved_areas(tmp_path)
    expected_size = original[0].pop("content_bytes")
    expected_count = original[0].pop("tile_count")
    with closing(sqlite3.connect(tmp_path / areas.AREAS_FILE)) as db:
        db.execute("UPDATE metadata SET value=? WHERE name='maritime_areas'", (json.dumps(original),))
        db.commit()
    areas.manage_areas(tmp_path, original, original, build_resolver=lambda _: pytest.fail("Statistics must not use Internet"))
    measured = areas.saved_areas(tmp_path)[0]
    assert measured["content_bytes"] == expected_size
    assert measured["tile_count"] == expected_count


def test_new_areas_and_live_download_progress_stay_inside_offline_tab(app, tmp_path, monkeypatch):
    from maritime_simulator.ui import offline_panel
    other = [10.2,53.7,10.21,53.71]
    original = areas.manage_areas
    held, release = Event(), Event()
    latest = []
    def fixture_download(directory, desired, expected, **kwargs):
        report = kwargs["area_progress"]
        def progress(rows):
            latest[:] = rows
            report(rows)
            if any(row["status"] == "Downloading" and row["done"] > 0 for row in rows) and not held.is_set():
                held.set()
                assert release.wait(5)
        kwargs["area_progress"] = progress
        return original(directory, desired, expected, **kwargs,
                        source_factory=archive([BOUNDS, other]), build_resolver=lambda _: "fixture")
    monkeypatch.setattr(offline_panel, "manage_areas", fixture_download)
    changed = []
    panel = offline_panel.OfflinePanel(lambda: OfflineSettings(True), changed.append, tmp_path, lambda: None)
    manager = panel.manager
    manager.queue_areas([{"bounds": BOUNDS, "max_zoom": 13}, {"bounds": other, "max_zoom": 13}])
    manager.download.click()
    job = manager.job
    try:
        for _ in range(200):
            QTest.qWait(10)
            if held.is_set() and manager.table.item(0, 4).text() == "Downloading" and manager.table.item(0, 5).data(Qt.UserRole) > 0:
                break
        assert held.is_set()
        assert manager.table.item(0, 4).text() == "Downloading"
        assert manager.table.item(1, 4).text() == "Queued"
        # Shared parent tiles already saved for area 1 also belong to queued area 2.
        expected_size = offline_panel._file_size(latest[1]["content_bytes"]) if latest[1]["done"] else "--"
        assert manager.table.item(1, 3).text() == expected_size
        assert manager.table.item(0, 3).text() != "--"
        assert manager.table.item(0, 5).data(Qt.UserRole) > 0
        assert " / " in manager.table.item(0, 2).text()
        assert not manager.download.isEnabled()
        assert not manager.add_area.isEnabled()
        manager._download_queued()
        assert manager.job is job
        assert not hasattr(offline_panel, "OfflineAreaDialog")
    finally:
        release.set()
        wait_for_maps(manager)
    assert len(areas.saved_areas(tmp_path)) == 2
    assert changed[-1] == OfflineSettings(True, areas.AREAS_FILE, areas.AREAS_FILE)
    assert all(manager.table.item(row, 4).text() == "Downloaded" for row in range(2))
    assert all(not manager.table.item(row, 3).text().startswith("~") for row in range(2))
    assert "Package:" in manager.summary.text()
    panel.shutdown()
    panel.close()


def test_large_download_and_delete_require_confirmation_and_no_does_not_change_maps(app, tmp_path, monkeypatch):
    from maritime_simulator.ui import offline_panel
    areas.download_area(BOUNDS, "Saved", tmp_path, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    before = (tmp_path / areas.AREAS_FILE).read_bytes()
    manager = offline_panel.OfflineAreaManager(tmp_path)
    manager.expected[0]["content_bytes"] = 101 * 1024**2
    prompts = []
    monkeypatch.setattr(offline_panel, "ask_question", lambda _parent, title, text: prompts.append((title, text)) or QMessageBox.No)
    manager.table.itemDelegate().action.emit(0, False)
    assert prompts[-1][0] == "Large offline map download"
    assert manager.job is None
    manager.table.itemDelegate().action.emit(0, True)
    assert prompts[-1][0] == "Delete offline area"
    assert manager.job is None
    assert (tmp_path / areas.AREAS_FILE).read_bytes() == before
    manager.close()


@pytest.mark.parametrize("selected", [(0, 2), (0, 1, 2)])
def test_delete_selected_confirms_once_preserves_shared_data_and_queued_areas(app, tmp_path, monkeypatch, selected):
    from maritime_simulator.ui import offline_panel
    bounds = [BOUNDS, [9.949,53.535,9.955,53.539], [9.950,53.534,9.956,53.540]]
    selections = [{"bounds": item, "max_zoom": 15, "name": name}
                  for item, name in zip(bounds, ("First", "Keep", "Last"))]
    areas.download_areas(selections, "Test", tmp_path, source_factory=archive(bounds, zoom=15),
                         build_resolver=lambda _: "fixture")
    path = tmp_path / areas.AREAS_FILE
    before = path.read_bytes()
    retained = {}
    with closing(sqlite3.connect(path)) as db:
        for table in ("tiles", "vector_tiles", "water_tiles"):
            coordinates = areas.area_tiles(bounds[1], 15)
            if table != "tiles":
                coordinates = [xyz for xyz in coordinates if xyz[0] == 15]
            retained[table] = {}
            for z,x,y in coordinates:
                key = (z,x,2**z-1-y)
                row = db.execute(f"SELECT tile_data FROM {table} WHERE zoom_level=? AND tile_column=? AND tile_row=?", key).fetchone()
                if row:
                    retained[table][key] = row[0]
    manager = offline_panel.OfflineAreaManager(tmp_path)
    assert not manager.delete_selected.isEnabled()
    manager.queue_areas([{"bounds": [10.2,53.7,10.21,53.71], "max_zoom": 13}])
    for index in selected:
        manager.table.item(index, 0).setCheckState(Qt.Checked)
    assert manager.delete_selected.isEnabled()
    prompts, answer = [], [QMessageBox.No]
    monkeypatch.setattr(offline_panel, "ask_question", lambda _parent, title, text: prompts.append((title, text)) or answer[0])
    monkeypatch.setattr(areas, "_read_response", lambda *_args, **_kwargs: pytest.fail("Deletion must work offline"))
    manager.delete_selected.click()
    assert len(prompts) == 1
    assert prompts[0][0] == "Delete selected offline areas"
    assert "First" in prompts[0][1] and "Last" in prompts[0][1]
    assert manager.job is None
    assert path.read_bytes() == before
    answer[0] = QMessageBox.Yes
    manager.delete_selected.click()
    job = manager.job
    assert job is not None
    assert not manager.delete_selected.isEnabled()
    assert not manager.export.isEnabled()
    manager._delete_selected()
    assert manager.job is job and len(prompts) == 2
    wait_for_maps(manager)
    assert len(manager.pending) == 1
    assert not manager.delete_selected.isEnabled()
    assert not manager._checked
    if len(selected) == 3:
        assert not path.exists()
        assert manager.expected == []
    else:
        assert [area["name"] for area in areas.saved_areas(tmp_path)] == ["Keep"]
        with closing(sqlite3.connect(path)) as db:
            for table, tiles in retained.items():
                assert {(z,x,y): data for z,x,y,data in db.execute(f"SELECT * FROM {table}")} == tiles
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    manager.shutdown()
    manager.close()


def test_failed_batch_retains_saved_maps_and_pending_rows_can_retry(app, tmp_path, monkeypatch):
    from maritime_simulator.ui import offline_panel
    areas.download_area(BOUNDS, "Saved", tmp_path, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    before = (tmp_path / areas.AREAS_FILE).read_bytes()
    other = [10.2,53.7,10.21,53.71]
    manager = offline_panel.OfflineAreaManager(tmp_path)
    manager.queue_areas([{"bounds": other, "max_zoom": 13}])
    def fail(*_args, **_kwargs):
        raise OSError("Fixture network failure")
    monkeypatch.setattr(offline_panel, "manage_areas", fail)
    manager.download.click()
    wait_for_maps(manager)
    assert (tmp_path / areas.AREAS_FILE).read_bytes() == before
    assert len(manager.pending) == 1
    assert manager.table.item(1, 4).text() == "Failed"
    assert "Fixture network failure" in manager.status.text()
    assert manager.download.isEnabled()
    manager.shutdown()
    manager.close()


def test_selected_export_and_import_roundtrip_without_internet_or_credentials(app, tmp_path):
    source = tmp_path / "source"
    other = [10.2,53.7,10.21,53.71]
    areas.download_areas([{"bounds": bounds, "max_zoom": 13, "name": name}
                          for bounds, name in ((BOUNDS, "First"), (other, "Second"))], "Test", source,
                         source_factory=archive([BOUNDS, other]), build_resolver=lambda _: "fixture")
    path = source / areas.AREAS_FILE
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO metadata VALUES ('google_api_key','PRIVATE_TEST_KEY')")
        entries = areas.saved_areas(source)
        entries[0]["client_secret"] = "PRIVATE_TEST_SECRET"
        db.execute("UPDATE metadata SET value=? WHERE name='maritime_areas'", (json.dumps(entries),))
        db.commit()
    selected = areas.saved_areas(source)[:1]
    exported = tmp_path / "selected.mbtiles"
    areas.export_areas(source, selected, exported)
    assert b"PRIVATE_TEST_KEY" not in exported.read_bytes()
    assert b"PRIVATE_TEST_SECRET" not in exported.read_bytes()
    with closing(sqlite3.connect(exported)) as db:
        exported_areas = json.loads(db.execute("SELECT value FROM metadata WHERE name='maritime_areas'").fetchone()[0])
        assert [area["name"] for area in exported_areas] == ["First"]
        assert "client_secret" not in exported_areas[0]
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    target = tmp_path / "target"
    assert areas.import_areas(target, exported) == (areas.AREAS_FILE, 1, 0)
    assert [area["name"] for area in areas.saved_areas(target)] == ["First"]
    package = MBTiles(target / areas.AREAS_FILE, water_mask=True)
    z, x, y = next(coordinate for coordinate in areas.area_tiles(BOUNDS, 13) if coordinate[0] == 13)
    assert package.vector_tile(z, x, y)


def test_import_keeps_existing_overlap_tiles_and_skips_duplicate_coverage(app, tmp_path):
    other = [9.949,53.535,9.955,53.539]
    source, target = tmp_path / "source", tmp_path / "target"
    factory = archive([BOUNDS, other], zoom=15)
    areas.download_areas([{"bounds": BOUNDS, "max_zoom": 15, "name": "Incoming duplicate"},
                          {"bounds": other, "max_zoom": 13, "name": "New area"}], "Test", source,
                         source_factory=factory, build_resolver=lambda _: "fixture")
    areas.download_area(BOUNDS, "Keep this name and detail", target, max_zoom=15,
                        source_factory=factory, build_resolver=lambda _: "fixture")
    path = target / areas.AREAS_FILE
    with closing(sqlite3.connect(path)) as db:
        before = {table: {(z,x,y): blob for z,x,y,blob in db.execute(f"SELECT * FROM {table}")}
                  for table in ("tiles", "vector_tiles", "water_tiles")}
    assert areas.import_areas(target, source / areas.AREAS_FILE) == (areas.AREAS_FILE, 1, 1)
    saved = areas.saved_areas(target)
    assert [(area["name"], area["max_zoom"]) for area in saved] == [("Keep this name and detail", 15), ("New area", 13)]
    with closing(sqlite3.connect(path)) as db:
        for table, data in before.items():
            actual = {(z,x,y): blob for z,x,y,blob in db.execute(f"SELECT * FROM {table}")}
            assert all(actual[key] == value for key, value in data.items())
    unchanged = path.read_bytes()
    assert areas.import_areas(target, source / areas.AREAS_FILE) == (areas.AREAS_FILE, 0, 2)
    assert path.read_bytes() == unchanged


def test_cancelled_export_and_import_keep_previous_files(app, tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    other = [10.2,53.7,10.21,53.71]
    factory = archive([BOUNDS, other])
    areas.download_areas([{"bounds": bounds, "max_zoom": 13, "name": str(index)} for index,bounds in enumerate((BOUNDS, other))],
                         "Test", source, source_factory=factory, build_resolver=lambda _: "fixture")
    areas.download_area(BOUNDS, "Keep", target, max_zoom=13, source_factory=factory, build_resolver=lambda _: "fixture")
    path = target / areas.AREAS_FILE
    before = path.read_bytes()
    cancelled = Event()
    def cancel_after_copy(count, _total, _text):
        if count:
            cancelled.set()
    exported = tmp_path / "previous.mbtiles"
    exported.write_bytes(b"existing exported file")
    with pytest.raises(areas.DownloadCancelled):
        areas.export_areas(source, areas.saved_areas(source), exported, cancel=cancelled, progress=cancel_after_copy)
    assert exported.read_bytes() == b"existing exported file"
    cancelled.clear()
    with pytest.raises(areas.DownloadCancelled):
        areas.import_areas(target, source / areas.AREAS_FILE, cancel=cancelled,
                           area_progress=lambda rows: cancelled.set() if any(row["status"] == "Finalizing" for row in rows) else None)
    assert path.read_bytes() == before
    assert not (target / (areas.AREAS_FILE + ".import")).exists()


def test_incomplete_import_is_rejected_without_touching_existing_maps(app, tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    other = [10.2,53.7,10.21,53.71]
    factory = archive([BOUNDS, other])
    areas.download_area(other, "Incomplete", source, max_zoom=13, source_factory=factory, build_resolver=lambda _: "fixture")
    areas.download_area(BOUNDS, "Keep", target, max_zoom=13, source_factory=factory, build_resolver=lambda _: "fixture")
    with closing(sqlite3.connect(source / areas.AREAS_FILE)) as db:
        db.execute("DELETE FROM vector_tiles")
        db.commit()
    path = target / areas.AREAS_FILE
    before = path.read_bytes()
    with pytest.raises(ValueError, match="missing"):
        areas.import_areas(target, source / areas.AREAS_FILE)
    assert path.read_bytes() == before


def test_export_reports_checking_before_validation_and_usb_errors_keep_old_file(app, tmp_path, monkeypatch):
    source = tmp_path / "source"
    areas.download_area(BOUNDS, "Harbour", source, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    destination = tmp_path / "USB SSD" / "maps.mbtiles"
    destination.parent.mkdir()
    destination.write_bytes(b"previous valid export")
    events = []
    original = areas._transfer_package
    def validate(*args, **kwargs):
        assert events and "Checking" in events[0]
        return original(*args, **kwargs)
    monkeypatch.setattr(areas, "_transfer_package", validate)
    monkeypatch.setattr(areas, "_replace_package", lambda *_: (_ for _ in ()).throw(OSError("USB drive disconnected")))
    with pytest.raises(ValueError, match="connected, writable"):
        areas.export_areas(source, areas.saved_areas(source), destination,
                           progress=lambda _done, _total, text: events.append(text))
    assert destination.read_bytes() == b"previous valid export"
    assert not destination.with_name(destination.name + ".export").exists()


def test_export_integrity_check_is_cancellable_before_copy(app, tmp_path):
    areas.download_area(BOUNDS, "Harbour", tmp_path, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: "fixture")
    cancelled = Event()
    destination = tmp_path / "exported.mbtiles"
    def progress(_done, _total, text):
        if text == "Checking saved maps before export...":
            cancelled.set()
    with pytest.raises(areas.DownloadCancelled):
        areas.export_areas(tmp_path, areas.saved_areas(tmp_path), destination, cancel=cancelled, progress=progress)
    assert not destination.exists()


def test_ui_multi_selection_export_all_and_import_use_background_jobs(app, tmp_path, monkeypatch):
    from maritime_simulator.ui import offline_panel
    other = [10.2,53.7,10.21,53.71]
    source = tmp_path / "source"
    areas.download_areas([{"bounds": bounds, "max_zoom": 13, "name": str(index)} for index,bounds in enumerate((BOUNDS, other))],
                         "Test", source, source_factory=archive([BOUNDS, other]), build_resolver=lambda _: "fixture")
    manager = offline_panel.OfflineAreaManager(source)
    exported = tmp_path / "all.mbtiles"
    monkeypatch.setattr(offline_panel.QFileDialog, "getSaveFileName", lambda *_args: (str(exported), ""))
    assert not manager.export.isEnabled()
    manager.table.item(0, 0).setCheckState(Qt.Checked)
    assert manager.export.isEnabled()
    assert not manager.select_all.isChecked()
    manager.select_all.setChecked(True)
    assert all(manager.table.item(row, 0).checkState() == Qt.Checked for row in range(2))
    manager.export.click()
    wait_for_maps(manager)
    assert exported.is_file()
    assert "Exported 2" in manager.status.text()
    assert len(manager.expected) == 2
    target = offline_panel.OfflineAreaManager(tmp_path / "target")
    monkeypatch.setattr(offline_panel.QFileDialog, "getOpenFileName", lambda *_args: (str(exported), ""))
    target.import_button.click()
    wait_for_maps(target)
    assert len(target.expected) == 2
    assert not target.pending
    assert "Imported 2" in target.status.text()
    for widget in (manager, target):
        widget.shutdown()
        widget.close()


def test_water_is_prepared_only_on_demand_and_cached_without_internet(app, tmp_path, monkeypatch):
    from maritime_simulator.visual.water_mask import OSMNavigableWaterMask, _tile_pixel_position
    directory = tmp_path / 'package'
    areas.download_area(BOUNDS, "Local", directory, max_zoom=13,
                        source_factory=archive([BOUNDS]), build_resolver=lambda _: 'fixture')
    package = MBTiles(directory / areas.AREAS_FILE, water_mask=True)
    assert package.deferred_water
    key = next(tile for tile in areas.area_tiles(BOUNDS, 13) if tile[0] == 13)
    water_position = _tile_pixel_position(key, (20,128))
    land_position = _tile_pixel_position(key, (200,128))
    mask = OSMNavigableWaterMask(tmp_path / 'not-needed.zip', tmp_path / 'cache', allow_network=False)
    original = areas.rasterize_water_mask
    started, resume = Event(), Event()
    calls = []
    def delayed(data, **kwargs):
        calls.append(True)
        started.set()
        assert resume.wait(2)
        return original(data, **kwargs)
    monkeypatch.setattr(areas, 'rasterize_water_mask', delayed)
    mask.set_offline(True, package)
    try:
        before = time.monotonic()
        assert mask.is_water(*water_position) is None
        assert time.monotonic() - before < 0.1
        assert started.wait(1)
        assert mask.is_water(*water_position) is None
        assert 'Preparing' in mask.status
        resume.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and mask.is_water(*water_position) is None:
            time.sleep(0.01)
        assert mask.is_water(*water_position) is True
        assert mask.is_water(*land_position) is False
        assert len(calls) == 1
        assert len(list((tmp_path / 'cache' / 'offline-water-tiles').rglob('*.png'))) == 1
    finally:
        resume.set()
        mask.close()
    monkeypatch.setattr(areas, 'rasterize_water_mask', lambda *_args, **_kwargs: pytest.fail('Existing mask should be loaded from cache'))
    restored = OSMNavigableWaterMask(tmp_path / 'not-needed.zip', tmp_path / 'cache', allow_network=False)
    restored.set_offline(True, package)
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and restored.is_water(*water_position) is None:
            time.sleep(0.01)
        assert restored.is_water(*water_position) is True
    finally:
        restored.close()
