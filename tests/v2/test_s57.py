# SPDX-License-Identifier: MPL-2.0
from contextlib import closing
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
from threading import Event
import time
from urllib.error import HTTPError
from urllib.request import urlopen
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtCore import QPersistentModelIndex, Qt, QThread, QTimer
from PySide6.QtWidgets import QApplication
import pytest

from maritime_simulator.visual import s57
from maritime_simulator.visual.offline import OfflineSettings, OfflineTileServer

CELL = Path(__file__).resolve().parents[1] / "fixtures/s57/US4NJ1DG.zip"
BOUNDS = (-73.8, 39.6, -73.5, 39.9)


@pytest.fixture
def store(tmp_path):
    result = s57.S57Store(tmp_path / "s57")
    assert result.import_files([CELL]) == ["US4NJ1DG"]
    return result


def test_real_noaa_cell_import_and_spatial_index(store):
    record, = store.records()
    assert record["code"] == "US4NJ1DG"
    assert record["edition"] == "1" and record["update"] == "0"
    assert record["scale"] == 90000 and record["enabled"]
    assert record["content_bytes"] > 0 and record["feature_count"] == 466
    assert record["bounds"] == list(BOUNDS)
    assert sum(store.layers().values()) == 466
    assert s57.layer_descriptions()["DEPARE"] == "Depth area"
    collection = store.features(BOUNDS, 14)
    assert len(collection["features"]) == 466 and not collection["limited"]
    soundings = [row for row in collection["features"] if row["properties"]["layer"] == "SOUNDG"]
    assert len(soundings) == 369
    assert all(isinstance(row["properties"]["DEPTH"], (int, float)) for row in soundings)
    assert store.features((0, 0, 1, 1), 14)["features"] == []
    assert not any(row["properties"]["layer"] == "SOUNDG" for row in store.features(BOUNDS, 12)["features"])


def test_packaged_s57_smoke_imports_real_geometry():
    from run_v2 import _validate_s57_smoke_cell
    assert _validate_s57_smoke_cell(CELL) == ("US4NJ1DG", 466)


def test_packaged_s57_smoke_does_not_accept_broken_cell(tmp_path):
    from run_v2 import _validate_s57_smoke_cell
    cell = tmp_path / "INVALID.000"
    cell.write_bytes(b"not an ENC")
    with pytest.raises(Exception):
        _validate_s57_smoke_cell(cell)


def test_layers_enabled_cells_and_delete_are_independent(store):
    depths = store.features(BOUNDS, 14, ("DEPARE",))
    assert len(depths["features"]) == 25
    assert all(row["properties"]["layer"] == "DEPARE" for row in depths["features"])
    assert store.features(BOUNDS, 14, ())["features"] == []
    store.set_enabled("US4NJ1DG", False)
    assert store.layers() == {}
    assert store.features(BOUNDS, 14)["features"] == []
    store.import_files([CELL])
    assert not store.records()[0]["enabled"], "Refresh must retain the operator's Use selection"
    store.set_enabled("US4NJ1DG", True)
    assert len(store.layers()) > 3
    store.delete(["US4NJ1DG"])
    assert store.records() == []
    with closing(store.connect()) as db:
        assert db.execute("SELECT count(*) FROM features").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM extent").fetchone()[0] == 0


def test_viewport_feature_limit_is_reported(store, monkeypatch):
    monkeypatch.setattr(s57, "DISPLAY_LIMIT", 20)
    result = store.features(BOUNDS, 14)
    assert len(result["features"]) == 20 and result["limited"]


def test_display_budget_is_shared_between_visible_cells(store, monkeypatch):
    from shapely import from_wkb, to_wkb
    from shapely.affinity import translate
    with closing(store.connect(write=True)) as db, db:
        db.execute("INSERT INTO charts SELECT 'SECOND',name,source,edition,update_no,date,scale,'[-72.8,39.6,-72.5,39.9]',enabled FROM charts")
        originals = list(db.execute("SELECT f.layer,f.geometry,f.properties,e.west,e.east,e.south,e.north "
                                    "FROM features f JOIN extent e ON e.id=f.id"))
        for layer, geometry, properties, *extent in originals:
            props = {**json.loads(properties), "chart": "SECOND"}
            fid = db.execute("INSERT INTO features(chart,layer,geometry,properties) VALUES(?,?,?,?)",
                             ("SECOND", layer, to_wkb(translate(from_wkb(geometry), xoff=1)), json.dumps(props))).lastrowid
            west, east, south, north = extent
            db.execute("INSERT INTO extent VALUES(?,?,?,?,?)", (fid, west + 1, east + 1, south, north))
    monkeypatch.setattr(s57, "DISPLAY_LIMIT", 20)
    result = store.features((-73.8, 39.6, -72.5, 39.9), 14)
    assert result["limited"] and result["chart_count"] == 2
    charts = [feature["properties"]["chart"] for feature in result["features"]]
    assert charts.count("US4NJ1DG") == charts.count("SECOND") == 10


@pytest.mark.parametrize("precise_coverage", [False, True])
def test_finer_chart_masks_coarser_objects_but_keeps_uncovered_water(tmp_path, precise_coverage):
    from shapely import from_wkb, to_wkb
    from shapely.geometry import box, Point, shape
    store = s57.S57Store(tmp_path)
    with closing(store.connect(write=True)) as db, db:
        for code, scale, bounds in [("COARSE", 1000000, [0,0,3,1]), ("FINE", 20000, [1,0,2,1])]:
            db.execute("INSERT INTO charts VALUES(?,?,?,?,?,?,?,?,?)", (code,code,"test","1","0","",scale,json.dumps(bounds),1))
        footprint = box(1.25,0,1.75,1) if precise_coverage else box(1,0,2,1)
        if precise_coverage:
            db.execute("INSERT INTO chart_coverage VALUES(?,?)", ("FINE", to_wkb(footprint)))
        for code, layer, geom in [("COARSE","DEPARE",box(0,0,3,1)),
                                  ("COARSE","BOYLAT",Point(1.5,0.5)),
                                  ("COARSE","BOYLAT",Point(0.5,0.5)),
                                  ("FINE","DEPARE",box(1,0,2,1)),
                                  ("FINE","BOYLAT",Point(1.5,0.5))]:
            props = dict(layer=layer,chart=code,DRVAL1=20,DRVAL2=40)
            fid = db.execute("INSERT INTO features(chart,layer,geometry,properties) VALUES(?,?,?,?)",
                             (code,layer,to_wkb(geom),json.dumps(props))).lastrowid
            w,s,e,n = geom.bounds
            db.execute("INSERT INTO extent VALUES(?,?,?,?,?)", (fid,w,e,s,n))
    view = store.features((0,0,3,1), 15)
    assert view["chart_count"] == 2 and not view["limited"]
    water = [f for f in view["features"] if f["properties"]["layer"] == "DEPARE"]
    coarse = shape(next(f["geometry"] for f in water if f["properties"]["chart"] == "COARSE"))
    fine = shape(next(f["geometry"] for f in water if f["properties"]["chart"] == "FINE"))
    assert coarse.intersection(fine).area == 0
    assert abs(coarse.union(fine).area - 3) < 0.001
    assert abs(fine.area - footprint.area) < 0.001
    buoys = [f for f in view["features"] if f["properties"]["layer"] == "BOYLAT"]
    assert len(buoys) == 2
    assert sum(f["geometry"]["coordinates"] == [1.5,0.5] for f in buoys) == 1
    store.set_enabled("FINE", False)
    restored = store.features((0,0,3,1),15)
    assert restored["chart_count"] == 1
    assert shape(next(f["geometry"] for f in restored["features"] if f["properties"]["layer"] == "DEPARE")).area == 3


def test_import_retains_coverage_and_portrayal_attributes(store):
    from shapely import from_wkb
    with closing(store.connect()) as db:
        geometry, = db.execute("SELECT geometry FROM chart_coverage WHERE code='US4NJ1DG'").fetchone()
        assert from_wkb(geometry).is_valid
        assert from_wkb(geometry).bounds == BOUNDS
    assert {"CATLAM", "CATCAM", "COLPAT", "TOPSHP", "LITCHR", "SIGPER", "HEIGHT"} <= set(s57.ATTRIBUTE_NAMES)


def test_legacy_irregular_coverage_does_not_hide_coarse_land_or_water(tmp_path):
    from shapely import to_wkb, union_all
    from shapely.geometry import box, Point, Polygon, shape
    store = s57.S57Store(tmp_path)
    footprint = Polygon([(1,0),(2,0),(1.5,1),(1,0)])
    with closing(store.connect(write=True)) as db, db:
        db.execute("DROP TABLE chart_coverage")
        for code, scale, bounds in [("COARSE",1000000,[0,0,3,1]), ("FINE",20000,[1,0,2,1])]:
            db.execute("INSERT INTO charts VALUES(?,?,?,?,?,?,?,?,?)", (code,code,"test","1","0","",scale,json.dumps(bounds),1))
        for code, layer, geom in [("COARSE","LNDARE",box(0,.8,3,1)), ("COARSE","DEPARE",box(0,0,3,.8)),
                                  ("FINE","DEPARE",footprint), ("FINE","LNDARE",Point(1.8,.9))]:
            props = dict(layer=layer,chart=code,DRVAL1=20,DRVAL2=40)
            fid = db.execute("INSERT INTO features(chart,layer,geometry,properties) VALUES(?,?,?,?)",
                             (code,layer,to_wkb(geom),json.dumps(props))).lastrowid
            w,s,e,n = geom.bounds
            db.execute("INSERT INTO extent VALUES(?,?,?,?,?)", (fid,w,e,s,n))
    result = store.features((0,0,3,1),15)
    assert not result["limited"] and result["chart_count"] == 2
    coarse = union_all([shape(f["geometry"]) for f in result["features"] if f["properties"]["chart"] == "COARSE"])
    assert coarse.covers(Point(1.9,.95)), "Detailed cell bounds must not erase coarse land"
    assert coarse.covers(Point(1.9,.7)), "Detailed cell bounds must not erase coarse water"
    assert coarse.intersection(footprint).area < 1e-8
    assert abs(coarse.union(footprint).area - 3) < 1e-8


def test_legacy_coverage_cache_is_bounded_reused_and_invalidated(store, monkeypatch):
    with closing(store.connect(write=True)) as db, db:
        db.execute("DROP TABLE chart_coverage")
        db.execute("INSERT INTO charts SELECT 'COARSER',name,source,edition,update_no,date,1000000,bounds,enabled FROM charts")
    connect = store.connect
    queries = []
    def traced_connect(**kwargs):
        db = connect(**kwargs)
        db.set_trace_callback(queries.append)
        return db
    monkeypatch.setattr(store, "connect", traced_connect)
    assert store.features(BOUNDS,14)["features"]
    def count():
        return sum("SELECT f.geometry" in sql for sql in queries)
    original = count()
    assert original == 1
    assert store.features(BOUNDS,14)["features"] and count() == original
    with closing(store.connect(write=True)) as db, db:
        db.execute("DELETE FROM chart_coverage")
        db.execute("UPDATE features SET properties=json_set(properties,'$.INFORM','Updated')")
    assert store.features(BOUNDS,14)["features"] and count() == original + 1
    store.apply_memory_budget(64)
    store._coverage_cache.clear()
    store.features(BOUNDS,14)
    assert sum(len(data) for data in store._coverage_cache.values() if data is not None) <= 64


def test_legacy_coverage_budget_keeps_visible_features(store, monkeypatch):
    with closing(store.connect(write=True)) as db, db:
        db.execute("DROP TABLE chart_coverage")
        db.execute("INSERT INTO charts SELECT 'COARSER',name,source,edition,update_no,date,1000000,bounds,enabled FROM charts")
    monkeypatch.setattr(s57, "COVERAGE_INPUT_BYTES", 1)
    result = store.features(BOUNDS,14)
    assert result["limited"] and result["features"], "Bounded fallback must retain existing chart data"


def test_chart_scale_filters_detailed_cells_until_zoomed_in(store):
    overview = store.features(BOUNDS, 3)
    assert overview["features"] == [] and overview["chart_count"] == 0
    assert overview["minimum_chart_scale"] == 1_000_000
    assert store.features(BOUNDS, 14)["chart_count"] == 1
    with closing(store.connect(write=True)) as db, db:
        db.execute("UPDATE charts SET scale=12000")
    assert store.features(BOUNDS, 10)["chart_count"] == 0
    assert store.features(BOUNDS, 15)["chart_count"] == 1


def test_legacy_layer_menu_never_scans_feature_geometry_on_ui(store, monkeypatch):
    with closing(store.connect(write=True)) as db, db:
        db.execute("DROP TABLE chart_layers")
    connect = store.connect
    statements = []
    def traced_connect(**kwargs):
        db = connect(**kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(store, "connect", traced_connect)
    layers = store.layers()
    assert "DEPARE" in layers and layers["DEPARE"] is None
    assert not any("FROM features" in query for query in statements)
    assert store.has_enabled_cells()
    assert all(s57.re.fullmatch(r"[A-Za-z0-9_]{1,16}", code) for code in layers)
    assert not any(code.startswith(("M_", "C_")) for code in layers)
    chosen = tuple(code for code in layers if code != "SOUNDG")
    assert len(chosen) > 200
    settings = OfflineSettings.from_payload({"enc_layers": chosen})
    assert set(settings.enc_layers) == set(chosen)
    assert OfflineSettings.from_payload(json.loads(json.dumps(settings.payload()))) == settings
    assert store.features(BOUNDS, 14, chosen)["features"]
    server = OfflineTileServer(None, store)
    try:
        url = server.enc_url + "?bbox=-73.8,39.6,-73.5,39.9&zoom=14&layers=" + ",".join(chosen)
        with urlopen(url, timeout=5) as response:
            properties = [row["properties"] for row in json.load(response)["features"]]
        assert properties and not any(row["layer"] == "SOUNDG" for row in properties)
    finally:
        server.close()


def test_chart_query_budget_returns_limited_response_not_fetch_failure(store, monkeypatch):
    monkeypatch.setattr(s57, "DISPLAY_SECONDS", -1)
    result = store.features(BOUNDS, 14)
    assert result["limited"] and not result["features"]


def test_scale_minimum_suppresses_detail_objects(store):
    with closing(store.connect(write=True)) as db, db:
        db.execute("UPDATE features SET properties=json_set(properties,'$.SCAMIN',100)")
    assert store.features(BOUNDS, 14)["features"] == []


@pytest.mark.parametrize("bounds,zoom,layers", [
    ((10, 0, 0, 10), 14, None), ((0, 0, float("nan"), 10), 14, None),
    ((0, 0, 1, 1), 23, None), ((0, 0, 1, 1), float("nan"), None),
    ((0, 0, 1, 1), 14, ("DEPARE'); DROP TABLE charts;--",)),
])
def test_invalid_query_is_rejected(store, bounds, zoom, layers):
    with pytest.raises(ValueError):
        store.features(bounds, zoom, layers)


def test_failure_and_cancellation_do_not_replace_installed_charts(store, tmp_path):
    before = store.features(BOUNDS, 14)
    invalid = tmp_path / "INVALID.000"
    invalid.write_bytes(b"Not an S57 file")
    with pytest.raises(Exception):
        store.import_files([CELL, invalid])
    assert store.features(BOUNDS, 14) == before
    cancel = Event()
    with pytest.raises(ValueError, match="cancelled"):
        store.import_files([CELL], cancel=cancel, progress=lambda *_: cancel.set())
    assert store.features(BOUNDS, 14) == before
    assert not list(store.directory.glob("enc-import-*"))


def test_older_cell_rollback_is_rejected_transactionally(store):
    before = store.features(BOUNDS, 14)
    with closing(store.connect(write=True)) as db, db:
        db.execute("UPDATE charts SET edition='2' WHERE code='US4NJ1DG'")
    with pytest.raises(ValueError, match="older"):
        store.import_files([CELL])
    assert store.records()[0]["edition"] == "2"
    assert store.features(BOUNDS, 14) == before


@pytest.mark.parametrize("windows_normalization", [False, True])
@pytest.mark.parametrize("filename", [
    "../escape.000", "/escape.000", "dir\\escape.000",
    "C:/escape.000", "C:escape.000", "//server/share/escape.000",
    "US4NJ1DG.000\x00ignored",
])
def test_exchange_set_rejects_unsafe_paths(tmp_path, filename, windows_normalization, monkeypatch):
    if windows_normalization:
        monkeypatch.setattr(zipfile.os, "sep", "\\")
    source = tmp_path / "bad.zip"
    item = zipfile.ZipInfo("placeholder.000")
    # Write the malformed name verbatim; ZipInfo(filename) would sanitize this fixture on Windows.
    item.filename = item.orig_filename = filename
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(item, b"data")
    with zipfile.ZipFile(source) as archive:
        stored, = archive.infolist()
        assert stored.orig_filename == filename
        expected = filename.split("\x00", 1)[0]
        if os.sep == "\\":
            expected = expected.replace("\\", "/")
        assert stored.filename == expected
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(ValueError, match="Unsafe"):
        s57.plan_import_cells([source])


@pytest.mark.parametrize("windows_normalization", [False, True])
def test_exchange_set_accepts_nested_posix_paths(tmp_path, windows_normalization, monkeypatch):
    if windows_normalization:
        monkeypatch.setattr(zipfile.os, "sep", "\\")
    source = tmp_path / "valid.zip"
    names = ["ENC_ROOT/US4NJ1DG/US4NJ1DG.000", "ENC_ROOT/US4NJ1DG/US4NJ1DG.001"]
    with zipfile.ZipFile(source, "w") as archive:
        for name in names:
            archive.writestr(name, b"data")
    cell, = s57.plan_import_cells([source])
    assert cell.code == "US4NJ1DG"
    assert [item.filename for item in cell.files] == names


def test_exchange_set_rejects_symlinks_and_missing_updates(tmp_path):
    source = tmp_path / "bad.zip"
    item = zipfile.ZipInfo("US4NJ1DG.000")
    item.create_system = 3
    item.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(item, "elsewhere")
    work = tmp_path / "symlink"
    work.mkdir()
    with pytest.raises(ValueError, match="Unsafe"):
        s57.plan_import_cells([source])
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("US4NJ1DG.000", b"base")
        archive.writestr("US4NJ1DG.002", b"missing .001")
    work = tmp_path / "gap"
    work.mkdir()
    with pytest.raises(ValueError, match="Missing update"):
        s57.plan_import_cells([source])


def test_catalog_filters_non_noaa_cancelled_and_cache_avoids_network(tmp_path, monkeypatch):
    xml = tmp_path / "catalog.xml"
    xml.write_text('''<EncProductCatalog>
      <cell><name>US4NJ1DG</name><status>Active</status><lname>Test area</lname><cscale>90000</cscale>
        <zipfile_location>https://www.charts.noaa.gov/ENCs/US4NJ1DG.zip</zipfile_location><zipfile_size>39538</zipfile_size>
        <cov><panel><vertex><long>-73.8</long><lat>39.6</lat></vertex><vertex><long>-73.5</long><lat>39.9</lat></vertex></panel></cov></cell>
      <cell><name>US4NJ1DG</name><status>Cancelled</status></cell>
      <cell><name>US4BAD00</name><status>Active</status><zipfile_location>http://127.0.0.1/secret</zipfile_location></cell>
    </EncProductCatalog>''', encoding="utf-8")
    records = s57.parse_catalog(xml)
    assert len(records) == 1 and records[0]["bounds"] == list(BOUNDS)
    calls = []
    def download(_url, target, **_kwargs):
        calls.append(_url)
        target.write_bytes(xml.read_bytes())
    monkeypatch.setattr(s57, "download", download)
    directory = tmp_path / "cache"
    assert s57.load_catalog(directory) == records
    assert s57.load_catalog(directory) == records and len(calls) == 1
    assert s57.load_catalog(directory, refresh=True) == records and len(calls) == 2
    (directory / "noaa-catalog.json").write_text(json.dumps([{"url":records[0]["url"], "code":"../../bad"}]))
    assert s57.load_catalog(directory) == records and len(calls) == 3


@pytest.mark.parametrize("url", [
    "http://www.charts.noaa.gov/ENCs/US4NJ1DG.zip",
    "https://www.charts.noaa.gov.evil.test/ENCs/US4NJ1DG.zip",
    "https://user:password@www.charts.noaa.gov/ENCs/US4NJ1DG.zip",
    "https://www.charts.noaa.gov:444/ENCs/US4NJ1DG.zip",
    "https://www.charts.noaa.gov/ENCs/US4NJ1DG.zip?key=x",
])
def test_only_official_https_noaa_downloads_are_accepted(url):
    with pytest.raises(ValueError):
        s57.noaa_url(url)


def test_download_rejects_incomplete_response(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        headers = {"Content-Length": "10"}
        def geturl(self):
            return "https://www.charts.noaa.gov/ENCs/US4NJ1DG.zip"
    monkeypatch.setattr(s57.request, "urlopen", lambda *_args, **_kwargs: Response(b"short"))
    with pytest.raises(ValueError, match="Incomplete"):
        s57.download("https://www.charts.noaa.gov/ENCs/US4NJ1DG.zip", tmp_path / "result")


def test_incomplete_chart_query_is_retried_without_caching_partial_result(store, monkeypatch):
    server = OfflineTileServer(None, store)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(s57, "DISPLAY_SECONDS", 0)
            first = json.loads(server.chart_features(BOUNDS, 14, None))
        assert first["limited"] and first["retryable"] and not first["features"]
        assert not server._enc_cache
        second = json.loads(server.chart_features(BOUNDS, 14, None))
        assert not second["limited"] and len(second["features"]) == 466
        assert len(server._enc_cache) == 1
    finally:
        server.close()


def test_http_chart_cache_and_invalidation(store):
    server = OfflineTileServer(None, store)
    try:
        url = server.enc_url + "?bbox=-73.8,39.6,-73.5,39.9&zoom=14"
        with urlopen(url, timeout=5) as reply:
            assert reply.headers["Content-Type"] == "application/geo+json"
            assert len(json.load(reply)["features"]) == 466
        with urlopen(url + "&layers=", timeout=5) as reply:
            assert json.load(reply)["features"] == []
        with pytest.raises(HTTPError) as error:
            urlopen(server.enc_url + "?bbox=0,0,NaN,1&zoom=14", timeout=5)
        assert error.value.code == 400
        assert error.value.headers["Access-Control-Allow-Origin"] == "*"
        assert "S57 chart request" in json.load(error.value)["error"]
        store.set_enabled("US4NJ1DG", False)
        server.select(None)
        with urlopen(url, timeout=5) as reply:
            assert json.load(reply)["features"] == []
        for zoom in range(8, 19):
            server.chart_features(BOUNDS, zoom, None)
        assert len(server._enc_cache) <= 4
        assert sum(map(len, server._enc_cache.values())) <= 16 * 1024**2
    finally:
        server.close()
    assert not server.thread.is_alive()


def test_offline_source_and_layers_survive_configuration_and_startup(tmp_path):
    from maritime_simulator.ui.main_window import V2MainWindow
    from ui.webengine_profile import set_webengine_offline
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.s57_store.import_files([CELL])
        window._apply_offline_settings(OfflineSettings(False, map_source="s57", enc_layers=("DEPARE", "BOYSPP")))
        source = window.map._map_source
        assert source["id"] == "s57" and source["offline"]
        assert source["encUrl"].startswith("http://127.0.0.1:")
        assert source["url"] == window.offline_tiles.url
        assert source["minZoom"] == 0 and source["maxZoom"] == 12
        assert window.offline_tiles.package is None, "S57 must have its own schematic background, not mix in saved raster maps"
        tile_url = source["url"].replace("{z}","0").replace("{x}","0").replace("{y}","0")
        # A cold schematic tile also opens/extracts the bundled coastlines on slow build hosts.
        with urlopen(tile_url, timeout=15) as response:
            from PySide6.QtGui import QImage
            image = QImage.fromData(response.read())
            assert image.width() == 256
            assert len({image.pixelColor(x,y).name() for y in range(0,256,4) for x in range(0,256,4)}) >= 2
        assert not window.ais_controls.client.offline, "Local chart selection must not disable Live AIS"
        payload = window._configuration_payload()
        loaded = OfflineSettings.from_payload(json.loads(json.dumps(payload["offline"])))
        assert loaded == window.offline_settings
        window.startup_profile.capture_fixed("map", payload, select=True)
        resolved = window.startup_profile.resolve(payload, payload)
        assert resolved["offline"]["map_source"] == "s57"
        window._open_settings()
        panel = window.settings_dialog.offline_panel
        assert [panel.map_tabs.tabText(i) for i in range(2)] == ["Protomaps", "S57 / ENC"]
        window.settings_dialog.tabs.setCurrentWidget(panel)
        panel.map_tabs.setCurrentWidget(panel.s57)
        assert panel.s57.table.model().rowCount() == 1
        panel.s57.table.model().setData(panel.s57.table.model().index(0, 0), Qt.Unchecked, Qt.CheckStateRole)
        assert not window.s57_store.records()[0]["enabled"]
        assert not next(row for row in window.map._map_source["available"] if row["id"] == "s57")["enabled"]
        panel.s57.table.model().setData(panel.s57.table.model().index(0, 0), Qt.Checked, Qt.CheckStateRole)
        window._set_s57_layer("DEPARE", False)
        assert "DEPARE" not in window.offline_settings.enc_layers
        window._apply_offline_settings(replace(window.offline_settings, enabled=True))
        panel.refresh()
        assert not panel.s57.noaa_button.isEnabled()
    finally:
        window.close()
        set_webengine_offline(False)
        app.processEvents()


def test_panel_background_import_and_corrupt_index(tmp_path):
    from maritime_simulator.ui.s57_panel import S57Panel
    app = QApplication.instance() or QApplication([])
    panel = S57Panel(tmp_path, lambda: False)
    try:
        panel.show()
        app.processEvents()
        panel._start("import", [CELL])
        assert panel.job is not None and panel.cancel_button.isEnabled()
        deadline = time.monotonic() + 10
        while panel.job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert panel.job is None and panel.table.model().rowCount() == 1, panel.status.text()
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()
    broken = tmp_path / "broken/s57"
    broken.mkdir(parents=True)
    (broken / "charts.sqlite3").write_bytes(b"broken")
    panel = S57Panel(broken.parent, lambda: True)
    assert "Cannot read" in panel.status.text()
    assert panel.import_button.isEnabled() and not panel.noaa_button.isEnabled()
    panel.shutdown()
    panel.deleteLater()
    app.processEvents()


def test_noaa_select_all_applies_only_to_filtered_visible_rows():
    from maritime_simulator.ui.s57_panel import _NOAAChooser
    app = QApplication.instance() or QApplication([])
    records = [dict(code=f"US4TEST{index}", name=name, scale=50000, date="20261008", size=1024)
               for index, name in enumerate(("Boston North", "Boston South", "Seattle"))]
    chooser = _NOAAChooser(records, set(), None)
    try:
        chooser.select_all.click()
        assert chooser.selected == {0, 1, 2}
        assert chooser.select_all.checkState() == Qt.Checked
        chooser.search.setText("boston")
        chooser.select_all.click()
        assert chooser.selected == {2}, "Filtered bulk clear must retain hidden selections"
        assert chooser.select_all.checkState() == Qt.Unchecked
        chooser.table.model().setData(chooser.table.model().index(0, 0), Qt.Checked, Qt.CheckStateRole)
        assert chooser.select_all.checkState() == Qt.PartiallyChecked
        signals = []
        chooser.cell_model.selection_changed.connect(lambda: signals.append(True))
        chooser.select_all.click()
        assert chooser.selected == {0, 1, 2}
        assert chooser.select_all.checkState() == Qt.Checked
        assert signals == [True], "Bulk selection must emit one update, not a refresh for every row"
        chooser.search.setText("not found")
        assert not chooser.select_all.isEnabled()
        assert chooser.select_all.checkState() == Qt.Unchecked
        assert chooser.selected == {0, 1, 2}
        chooser.search.clear()
        assert chooser.select_all.checkState() == Qt.Checked
        chooser.select_all.click()
        assert not chooser.selected and not chooser.accept_button.isEnabled()
    finally:
        chooser.deleteLater()
        app.processEvents()


def test_noaa_bulk_selection_does_not_disable_large_downloads():
    from maritime_simulator.ui.s57_panel import _NOAAChooser
    app = QApplication.instance() or QApplication([])
    records = [dict(code=f"US{index:06d}", name="Area", scale=50000, date="", size=1024)
               for index in range(65)]
    chooser = _NOAAChooser(records, set(), None)
    try:
        chooser.select_all.click()
        assert len(chooser.selected) == len(records)
        assert chooser.accept_button.isEnabled(), "All visible cells must be downloadable, even above 64"
        chooser.table.model().setData(chooser.table.model().index(0, 0), Qt.Unchecked, Qt.CheckStateRole)
        assert chooser.accept_button.isEnabled()
    finally:
        chooser.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("zipped", [False, True])
def test_folder_import_is_recursive_and_ignores_metadata(tmp_path, zipped):
    folder = tmp_path / "folder"
    nested = folder / "ENC_ROOT" / "charts"
    nested.mkdir(parents=True)
    if zipped:
        (nested / CELL.name).write_bytes(CELL.read_bytes())
    else:
        with zipfile.ZipFile(CELL) as archive:
            member = next(name for name in archive.namelist() if name.endswith(".000"))
            (nested / "US4NJ1DG.000").write_bytes(archive.read(member))
    (folder / "._US4NJ1DG.000").write_bytes(b"macOS metadata, not a chart")
    (folder / "CATALOG.031").write_bytes(b"catalogue, not a cell update")
    (folder / "readme.txt").write_text("Not a chart")
    metadata = folder / "__MACOSX"
    metadata.mkdir()
    (metadata / "BAD.000").write_bytes(b"ignored")
    store = s57.S57Store(tmp_path / "index")
    assert store.import_files([folder]) == ["US4NJ1DG"]
    assert sum(store.layers().values()) == 466


def test_folder_preserves_updates_and_deduplicates_selected_paths(tmp_path):
    folder = tmp_path / "charts" / "ENC_ROOT"
    folder.mkdir(parents=True)
    for extension in ("000", "001", "002"):
        (folder / f"GB4TESTA.{extension}").write_bytes(extension.encode())
    work = tmp_path / "work"
    work.mkdir()
    cells = s57.plan_import_cells([folder.parent, folder / "GB4TESTA.000"])
    assert len(cells) == 1
    assert sorted(file.suffix for file in cells[0].files) == [".000", ".001", ".002"]
    assert len(list(s57.staged_import_cells(cells, work))) == 1
    assert not list(work.iterdir())
    (folder / "GB4TESTA.001").unlink()
    second = tmp_path / "second"
    second.mkdir()
    with pytest.raises(ValueError, match="Missing update"):
        s57.plan_import_cells([folder.parent])


def test_folder_import_error_is_transactional_and_compiled_format_is_explained(store, tmp_path):
    folder = tmp_path / "compiled"
    folder.mkdir()
    (folder / "TEST.7CB").write_bytes(b"SevenCs SENC")
    before = store.features(BOUNDS, 14)
    with pytest.raises(ValueError, match=r"SevenCs.*original unencrypted .000"):
        store.import_files([folder])
    with zipfile.ZipFile(CELL) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".000"))
        (folder / "US4NJ1DG.000").write_bytes(archive.read(member))
    (folder / "INVALID.000").write_bytes(b"Not a cell")
    with pytest.raises(Exception):
        store.import_files([folder])
    assert store.features(BOUNDS, 14) == before
    with pytest.raises(ValueError, match="Reconnect the drive"):
        store.import_files([tmp_path / "disconnected-drive"])


def test_folder_rejects_duplicate_editions_and_ignores_directory_symlinks(tmp_path):
    folder = tmp_path / "charts"
    for name in ("a", "b"):
        target = folder / name
        target.mkdir(parents=True)
        (target / "GB4TESTA.000").write_bytes(b"dummy")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(ValueError, match="Duplicate S57 cell"):
        s57.plan_import_cells([folder])
    (folder / "b" / "GB4TESTA.000").unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "INVALID.000").write_bytes(b"must not be read")
    try:
        (folder / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this platform")
    assert [path.name for path in s57._import_sources([folder], None)] == ["GB4TESTA.000"]


def test_folder_prefers_unpacked_cells_over_noaa_backup_zip(tmp_path):
    folder = tmp_path / "NOAA"
    unpacked = folder / "All_ENCs/ENC_ROOT/US4NJ1DG"
    unpacked.mkdir(parents=True)
    with zipfile.ZipFile(CELL) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".000"))
        (unpacked / "US4NJ1DG.000").write_bytes(archive.read(member))
    (folder / "All_ENCs.zip").write_bytes(CELL.read_bytes())
    assert [cell.source for cell in s57.plan_import_cells([folder])] == [unpacked / "US4NJ1DG.000"]
    store = s57.S57Store(tmp_path / "index")
    assert store.import_files([folder]) == ["US4NJ1DG"]
    assert sum(store.layers().values()) == 466
    with pytest.raises(ValueError, match="Duplicate S57 cell"):
        s57.plan_import_cells([folder / "All_ENCs.zip", unpacked / "US4NJ1DG.000"])


@pytest.mark.parametrize("zipped", [False, True])
def test_catalogue_limits_are_per_cell_not_aggregate(tmp_path, monkeypatch, zipped):
    monkeypatch.setattr(s57, "MAX_IMPORT_BYTES", 16)
    source = tmp_path / ("All_ENCs.zip" if zipped else "ENC_ROOT")
    if zipped:
        with zipfile.ZipFile(source, "w") as archive:
            for index in range(80):
                archive.writestr(f"ENC_ROOT/US{index:06d}.000", b"base")
                archive.writestr(f"ENC_ROOT/US{index:06d}.001", b"update")
    else:
        source.mkdir()
        for index in range(80):
            (source / f"US{index:06d}.000").write_bytes(b"base")
            (source / f"US{index:06d}.001").write_bytes(b"update")
    cells = s57.plan_import_cells([source])
    assert len(cells) == 80
    work = tmp_path / "work"
    work.mkdir()
    for cell in s57.staged_import_cells(cells, work):
        assert cell.read_bytes() == b"base"
        assert cell.with_suffix(".001").read_bytes() == b"update"
        assert len(list(work.rglob("*"))) == 3, "Stage only one cell/update set at a time"
    assert not list(work.iterdir())
    oversized = tmp_path / "OVERSIZE.000"
    oversized.write_bytes(b"x" * 17)
    with pytest.raises(ValueError, match="exceeds 512 MB"):
        s57.plan_import_cells([oversized])


def test_full_catalogue_job_reuses_zip_and_keeps_staging_bounded(tmp_path, monkeypatch):
    from maritime_simulator.ui import s57_panel
    count = 7114
    source = tmp_path / "All_ENCs.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for index in range(count):
            archive.writestr(f"ENC_ROOT/US{index:06d}/US{index:06d}.000", b"cell")
            # A complete catalogue also exceeds the old 20,000-entry guard.
            archive.writestr(f"ENC_ROOT/US{index:06d}/README.TXT", b"ignored")
            archive.writestr(f"ENC_ROOT/US{index:06d}/LICENSE.TXT", b"ignored")
    opens = []
    original = zipfile.ZipFile
    def open_zip(*args, **kwargs):
        opens.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(s57.zipfile, "ZipFile", open_zip)
    class Store:
        directory = tmp_path
        def import_files(self, files, **_kwargs):
            cell, = files
            assert cell.read_bytes() == b"cell"
            assert len(list(cell.parent.parent.rglob("*"))) == 2
            return [cell.stem]
    job = s57_panel._ENCJob(Store(), "import", [source])
    results, progress = [], []
    job.result.connect(lambda value, error: results.append((value, error)))
    job.progress.connect(lambda *values: progress.append(values))
    job.run()
    assert results == [([f"US{index:06d}" for index in range(count)], "")]
    assert progress[-1][:2] == (count, count)
    assert len(opens) == 3, "Read ZIP headers twice, then keep one handle for all cells"
    assert not list(tmp_path.glob("enc-batch-*"))


def test_local_batch_cancellation_keeps_completed_cells(tmp_path):
    from maritime_simulator.ui.s57_panel import _ENCJob
    folder = tmp_path / "ENC_ROOT"
    folder.mkdir()
    with zipfile.ZipFile(CELL) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".000"))
        for code in ("US4NJ1DG", "US4TESTA"):
            (folder / (code + ".000")).write_bytes(archive.read(member))
    store = s57.S57Store(tmp_path / "index")
    store.directory.mkdir()
    job = _ENCJob(store, "import", [folder])
    results = []
    job.result.connect(lambda value, error: results.append((value, error)))
    job.cell_progress.connect(lambda _code, state, _value: job.cancel.set() if state == "Ready" else None)
    job.run()
    assert results[0][0] == ["US4NJ1DG"]
    assert "1 completed cell(s) retained" in results[0][1] and "cancelled" in results[0][1]
    assert [row["code"] for row in store.records()] == ["US4NJ1DG"]
    assert not list(store.directory.glob("enc-*-*"))


def test_base_keeps_coast_and_conditional_dangers_not_standard_layers(tmp_path):
    from shapely import to_wkb
    from shapely.geometry import Point, LineString, box
    store = s57.S57Store(tmp_path / "base")
    rows = [
        ("DEPARE", box(0, 0, 2, 4), {"DRVAL1": 35, "DRVAL2": 50}),
        ("DEPARE", box(2, 0, 4, 4), {"DRVAL1": 5, "DRVAL2": 20}),
        ("LNDARE", box(3.8, 0, 4, 4), {}),
        ("COALNE", LineString([(3.8, 0), (3.8, 4)]), {}),
        ("DEPCNT", LineString([(2, 0), (2, 4)]), {"VALDCO": 35}),
        ("DEPCNT", LineString([(2.5, 0), (2.5, 4)]), {"VALDCO": 20}),
        ("DEPCNT", LineString([(0.5, 0), (0.5, 4)]), {"VALDCO": 50}),
        ("DEPCNT", LineString([(0.4, 0), (0.4, 4)]), {}),
        ("WRECKS", Point(1, 1), {"VALSOU": 10, "OBJNAM": "Danger in safe water"}),
        ("WRECKS", Point(3, 1), {"VALSOU": 10, "OBJNAM": "Danger in shallow water"}),
        ("WRECKS", Point(1, 2), {"VALSOU": 40, "OBJNAM": "Deep wreck"}),
        ("UWTROC", Point(1, 3), {"OBJNAM": "Unknown-depth rock"}),
        ("CBLOHD", LineString([(1, 0.5), (1, 3.5)]), {}),
        ("SOUNDG", Point(1, 1), {"DEPTH": 10}),
        ("BOYLAT", Point(1, 1), {}),
        ("TSSLPT", box(0.5, 0.5, 1.5, 1.5), {}),
    ]
    with closing(store.connect(write=True)) as db, db:
        db.execute("INSERT INTO charts VALUES(?,?,?,?,?,?,?,?,?)", ("TEST", "Test", "Test", "1", "0", "", 50000, "[0,0,4,4]", 1))
        for layer, shape, attributes in rows:
            props = dict(layer=layer, chart="TEST", **attributes)
            fid = db.execute("INSERT INTO features(chart,layer,geometry,properties) VALUES(?,?,?,?)",
                             ("TEST", layer, to_wkb(shape), json.dumps(props))).lastrowid
            west, south, east, north = shape.bounds
            db.execute("INSERT INTO extent VALUES(?,?,?,?,?)", (fid, west, east, south, north))
    result = store.features((0, 0, 4, 4), 14, base=True)
    assert result["safety_contour_m"] == 30
    features = result["features"]
    assert {row["properties"]["layer"] for row in features} == {"DEPARE", "LNDARE", "COALNE", "DEPCNT", "WRECKS", "UWTROC", "CBLOHD"}
    assert [row["properties"]["VALDCO"] for row in features if row["properties"]["layer"] == "DEPCNT"] == [35]
    assert [row["properties"]["OBJNAM"] for row in features if row["properties"]["layer"] == "WRECKS"] == ["Danger in safe water"]
    assert len(store.features((0, 0, 4, 4), 14)["features"]) == len(rows)


def test_base_http_cache_is_distinct_and_invalid_display_rejected(store):
    server = OfflineTileServer(None, store)
    try:
        url = server.enc_url + "?bbox=-73.8,39.6,-73.5,39.9&zoom=14"
        with urlopen(url + "&display=base", timeout=5) as reply:
            base = json.load(reply)
            assert base["features"] and base["display"] == "base"
            assert not any(row["properties"]["layer"] == "SOUNDG" for row in base["features"])
        with urlopen(url, timeout=5) as reply:
            assert len(json.load(reply)["features"]) == 466
        assert len(server._enc_cache) == 2
        with pytest.raises(HTTPError) as error:
            urlopen(url + "&display=invalid", timeout=5)
        assert error.value.code == 400
    finally:
        server.close()


def test_base_menu_and_independent_map_startup(tmp_path):
    from maritime_simulator.ui.main_window import V2MainWindow
    from maritime_simulator.ui.startup_profile_panel import _details, _summary
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        window.s57_store.import_files([CELL])
        window._apply_offline_settings(OfflineSettings(False, map_source="s57"))
        window._choose_s57_layers()
        actions = {action.text(): action for action in window._s57_layers_menu.actions()}
        assert "Base" in actions and "Hide All Layers" not in actions
        actions["Base"].trigger()
        assert window.offline_settings.enc_base and window.map._map_source["base"]
        window._s57_layers_menu.safety_changed.emit(12.5, 8.4)
        assert window.offline_settings.enc_safety_contour_m == 12.5
        assert window.offline_settings.enc_safety_depth_m == 8.4
        assert window.map._map_source["safetyContour"] == 12.5 and window.map._map_source["safetyDepth"] == 8.4
        assert window.map._map_source["base"], "Changing safety settings must not leave Base"
        payload = window._configuration_payload()
        assert OfflineSettings.from_payload(json.loads(json.dumps(payload["offline"]))) == window.offline_settings
        window.startup_profile.capture_fixed("map", payload, select=True)
        other = {**payload, "offline": OfflineSettings(True, map_source="online").payload()}
        window.startup_profile.capture_all_last_saved(other)
        resolved = window.startup_profile.resolve(other, other)
        assert resolved["offline"]["enabled"], "Map Fixed must not replace the separate Offline group"
        assert resolved["offline"]["map_source"] == "s57" and resolved["offline"]["enc_base"]
        assert resolved["offline"]["enc_safety_contour_m"] == 12.5 and resolved["offline"]["enc_safety_depth_m"] == 8.4
        assert "Base" in _summary("map", window.startup_profile.fixed_group("map"))
        assert "S-57 (Experimental)" in _details("map", window.startup_profile.fixed_group("map"))
        assert next(source["label"] for source in window.map._map_source["available"] if source["id"] == "s57") == "S-57 (Experimental)"
        window._set_s57_layer("SOUNDG", True)
        assert not window.offline_settings.enc_base
        assert "SOUNDG" in window.offline_settings.enc_layers
        window._s57_layers_menu.groups[()][0].click()
        assert window.offline_settings.enc_layers is None and not window.offline_settings.enc_base
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_large_noaa_job_keeps_parallel_download_staging_bounded_and_cleans_it(tmp_path, monkeypatch):
    from maritime_simulator.ui import s57_panel
    from maritime_simulator.visual.noaa import PREFETCH_CELLS
    class Store:
        directory = tmp_path
        def __init__(self):
            self.imported = []
        def records(self):
            return []
        def import_files(self, files, **kwargs):
            assert len(files) == 1
            code = files[0].stem
            assert files[0].exists()
            assert kwargs["origin"] == "NOAA ENC"
            self.imported.append(code)
            return [code]
    store = Store()
    calls = []
    def download(url, target, **kwargs):
        assert len(list(target.parent.glob("*.zip"))) <= PREFETCH_CELLS, "Do not accumulate the entire catalogue before import"
        calls.append(url)
        target.write_bytes(b"test archive")
        kwargs["progress"](1, 1, "Downloading")
    monkeypatch.setattr(s57_panel, "download", download)
    records = [dict(code=f"US{index:06d}", name=f"Cell {index}", url=f"https://www.charts.noaa.gov/ENCs/US{index:06d}.zip")
               for index in range(69)]
    job = s57_panel._ENCJob(store, "download", records)
    results, progress = [], []
    job.result.connect(lambda value, error: results.append((value, error)))
    job.cell_progress.connect(lambda *values: progress.append(values))
    job.run()
    assert results == [([record["code"] for record in records], "")]
    assert len(calls) == len(records) == len(store.imported)
    assert sum(state == "Ready" for _code, state, _value in progress) == len(records)
    assert not list(tmp_path.glob("noaa-download-*"))


def test_noaa_batch_continues_after_failure_and_renew_uses_fresh_catalog(tmp_path, monkeypatch):
    from maritime_simulator.ui import s57_panel
    records = [dict(code=f"US{index:06d}", name="Cell", url=f"https://www.charts.noaa.gov/ENCs/US{index:06d}.zip") for index in range(3)]
    class Store:
        directory = tmp_path
        def records(self):
            return []
        def import_files(self, files, **_kwargs):
            return [files[0].stem]
    calls = []
    def download(url, target, **_kwargs):
        calls.append(url)
        if "000001" in url:
            raise OSError("Network disconnected")
        target.write_bytes(b"archive")
    monkeypatch.setattr(s57_panel, "download", download)
    refreshed = []
    def catalog(_directory, **kwargs):
        refreshed.append(kwargs["refresh"])
        return records
    monkeypatch.setattr(s57_panel, "load_catalog", catalog)
    job = s57_panel._ENCJob(Store(), "renew", [{"code": record["code"]} for record in records])
    results = []
    job.result.connect(lambda value, error: results.append((value, error)))
    job.run()
    assert refreshed == [True]
    assert results[0][0] == ["US000000", "US000002"]
    assert "2 completed" in results[0][1] and "1 failed" in results[0][1] and "Network disconnected" in results[0][1]
    assert len(calls) == 3 and not list(tmp_path.glob("noaa-download-*"))


def test_renew_missing_noaa_and_local_cells_are_skipped_without_blocking_valid_cells(tmp_path, monkeypatch):
    from maritime_simulator.ui import s57_panel
    old = dict(US1EEZ2M='old overview', GB4TESTA='old local', US4NJ1DG='old NOAA')
    installed = dict(old)
    class Store:
        directory = tmp_path
        def records(self):
            return []
        def import_files(self, files, **_kwargs):
            code = files[0].stem
            installed[code] = 'new NOAA'
            return [code]
    row = dict(code='US4NJ1DG', name='Fresh NOAA', url='https://www.charts.noaa.gov/ENCs/US4NJ1DG.zip')
    monkeypatch.setattr(s57_panel, 'load_catalog', lambda *_args, **_kwargs: [row])
    calls = []
    def download(url, target, **_kwargs):
        calls.append(url)
        target.write_bytes(b'archive')
    monkeypatch.setattr(s57_panel, 'download', download)
    job = s57_panel._ENCJob(Store(), 'renew', [dict(code=code) for code in installed])
    results, progress = [], []
    job.result.connect(lambda *args: results.append(args))
    job.cell_progress.connect(lambda *args: progress.append(args))
    job.run()
    assert calls == [row['url']]
    assert installed == {**old, 'US4NJ1DG': 'new NOAA'}
    assert results[0][0] == ['US4NJ1DG']
    assert '2 skipped' in results[0][1] and '0 failed' in results[0][1]
    assert [code for code, state, _ in progress if state == 'Skipped'] == ['US1EEZ2M', 'GB4TESTA']
    assert set(job.cell_errors) == {'US1EEZ2M', 'GB4TESTA'}
    assert progress[-1] == ('US4NJ1DG', 'Ready', 100)


def test_bad_parallel_download_and_import_each_keep_old_cell_and_clean_staging(tmp_path, monkeypatch):
    from maritime_simulator.ui import s57_panel
    records = [dict(code=f'US{index:06d}', name='Cell', url=f'https://www.charts.noaa.gov/ENCs/US{index:06d}.zip') for index in range(4)]
    old = {row['code']: 'old chart' for row in records}
    installed = dict(old)
    class Store:
        directory = tmp_path
        def records(self):
            return []
        def import_files(self, files, **_kwargs):
            if files[0].stem == 'US000002':
                raise ValueError('Invalid ENC archive')
            installed[files[0].stem] = 'new chart'
            return [files[0].stem]
    calls = []
    def download(url, target, **_kwargs):
        assert len(list(target.parent.glob('*.zip'))) <= 4
        calls.append(url)
        target.write_bytes(b'partial archive')
        if target.stem == 'US000001':
            raise OSError('Incomplete download')
    monkeypatch.setattr(s57_panel, 'download', download)
    job = s57_panel._ENCJob(Store(), 'download', records)
    results = []
    job.result.connect(lambda *args: results.append(args))
    job.run()
    assert len(calls) == 4
    assert results[0][0] == ['US000000', 'US000003']
    assert '2 failed' in results[0][1]
    assert installed == {**old, 'US000000': 'new chart', 'US000003': 'new chart'}
    assert set(job.cell_errors) == {'US000001', 'US000002'}
    assert not list(tmp_path.glob('noaa-download-*'))


def test_cancelling_noaa_batch_stops_and_retains_completed_cells(tmp_path, monkeypatch):
    from maritime_simulator.ui import s57_panel
    records = [dict(code=f'US{index:06d}', name='Cell', url=f'https://www.charts.noaa.gov/ENCs/US{index:06d}.zip') for index in range(3)]
    first_imported = Event()
    class Store:
        directory = tmp_path
        def records(self):
            return []
        def import_files(self, files, **_kwargs):
            first_imported.set()
            return [files[0].stem]
    job = s57_panel._ENCJob(Store(), 'download', records)
    calls = []
    def download(url, target, **_kwargs):
        calls.append(url)
        if target.stem == 'US000001':
            assert first_imported.wait(5), "Cancellation must follow a completed import"
            job.cancel.set()
            s57.check_cancel(job.cancel)
        if target.stem == 'US000002':
            assert job.cancel.wait(5)
            s57.check_cancel(job.cancel)
        target.write_bytes(b'archive')
    monkeypatch.setattr(s57_panel, 'download', download)
    results, progress = [], []
    job.result.connect(lambda *args: results.append(args))
    job.cell_progress.connect(lambda *args: progress.append(args))
    job.run()
    assert 2 <= len(calls) <= 3 and results[0][0] == ['US000000']
    assert 'cancelled' in results[0][1].lower()
    assert ('US000001', 'Cancelled', 0) in progress
    assert not job.cell_errors
    assert not list(tmp_path.glob('noaa-download-*'))


def test_cell_manager_selection_progress_actions_and_stable_columns(tmp_path, monkeypatch):
    from maritime_simulator.ui import s57_panel
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    monkeypatch.setattr(s57_panel, "load_catalog", lambda *_args, **_kwargs: [])
    try:
        panel.store.import_files([CELL])
        with closing(panel.store.connect(write=True)) as db, db:
            db.execute("INSERT INTO charts SELECT 'GB4TESTA','Local area',source,edition,update_no,date,scale,bounds,0 FROM charts WHERE code='US4NJ1DG'")
        panel.refresh()
        item = QPersistentModelIndex(panel.table.model().index(0, 1))
        panel.refresh()
        assert item.isValid() and panel.table.model().index(0, 1) == item, "Unchanged cell tables must not be rebuilt"
        assert panel.table.model().headerData(4, Qt.Horizontal) == "Size"
        assert panel.table.model().headerData(5, Qt.Horizontal) == "Status"
        assert panel.table.model().headerData(6, Qt.Horizontal) == "Progress"
        assert not any(button.text() == "Layers..." for button in panel.findChildren(s57_panel.QPushButton))
        panel.select_all.click()
        assert len(panel.table.selectionModel().selectedRows()) == 2
        assert panel.store.enabled_cells() == ("US4NJ1DG",), "Selecting must not change Use"
        assert panel.store.records("US4NJ1DG")[0]["content_bytes"] > 0
        panel.search.setText("US4NJ1DG")
        panel.select_all.click()
        assert [row["code"] for row in panel._selected_records()] == ["GB4TESTA"]
        assert not panel.table.selectionModel().selectedRows(), "The retained selection is currently hidden"
        panel.search.clear()
        widths = [panel.table.columnWidth(index) for index in range(panel.table.model().columnCount())]
        row = panel._rows["US4NJ1DG"]
        panel._cell_progress("US4NJ1DG", "Downloading", 20)
        assert panel.table.model().index(row, 5).data() == "Downloading"
        assert panel.table.model().index(row, 6).data(Qt.UserRole) == 20
        panel._cell_progress("US4NJ1DG", "Ready", 100)
        assert panel.table.model().index(row, 4).data() != "--"
        assert widths == [panel.table.columnWidth(index) for index in range(panel.table.model().columnCount())]
        panel.show()
        app.processEvents()  # Exercise the delegate's progress/button rendering.
        monkeypatch.setattr(s57_panel, "ask_question", lambda *_args, **_kwargs: True)
        panel._cell_action(row, True)
        deadline = time.monotonic() + 5
        while panel.job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert panel.job is None
        assert [record["code"] for record in panel.store.records()] == ["GB4TESTA"]
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_enabled_cell_selection_can_be_restored_from_configuration(store):
    settings = OfflineSettings(False, map_source="s57", enc_base=True, enc_cells=())
    restored = OfflineSettings.from_payload(json.loads(json.dumps(settings.payload())))
    assert restored == settings
    store.set_enabled_cells(restored.enc_cells)
    assert store.enabled_cells() == () and not store.features(BOUNDS, 14)["features"]
    store.set_enabled_cells(("US4NJ1DG", "MISSING_CELL"))
    assert store.enabled_cells() == ("US4NJ1DG",)


def test_bulk_enable_is_one_transaction_and_cancellation_rolls_back(store, monkeypatch):
    connect, writes = store.connect, []
    def counted(**kwargs):
        if kwargs.get("write"):
            writes.append(1)
        return connect(**kwargs)
    monkeypatch.setattr(store, "connect", counted)
    store.set_enabled_many(["US4NJ1DG", "MISSING"], False)
    assert len(writes) == 1 and store.enabled_cells() == ()
    cancel = Event()
    def cancelled_codes():
        yield "US4NJ1DG"
        cancel.set()
    with pytest.raises(ValueError, match="cancelled"):
        store.set_enabled_many(cancelled_codes(), True, cancel=cancel)
    assert store.enabled_cells() == ()


def test_bulk_selection_coalesces_adjacent_rows_without_selecting_hidden_gaps():
    from PySide6.QtGui import QStandardItemModel
    from maritime_simulator.ui.s57_panel import _row_selection
    app = QApplication.instance() or QApplication([])
    model = QStandardItemModel(6808, 12)
    selection = _row_selection(model, range(6808))
    assert len(selection) == 1
    assert selection[0].top() == 0 and selection[0].bottom() == 6807
    selection = _row_selection(model, [6, 2, 1, 5, 2])
    assert [(item.top(), item.bottom()) for item in selection] == [(1, 2), (5, 6)]
    assert not _row_selection(model, [])


def test_bulk_show_runs_off_ui_and_keeps_table_items_and_selection(tmp_path, monkeypatch):
    from maritime_simulator.ui.s57_panel import S57Panel
    app = QApplication.instance() or QApplication([])
    shown = []
    panel = S57Panel(tmp_path, lambda: True, show_chart=shown.append)
    timer = QTimer()
    ticks = []
    timer.timeout.connect(lambda: ticks.append(1))
    try:
        panel.store.import_files([CELL])
        panel.store.set_enabled_many(["US4NJ1DG"], False)
        panel.refresh()
        original_item = QPersistentModelIndex(panel.table.model().index(0, 1))
        panel.table.selectRow(0)
        enable = panel.store.set_enabled_many
        def slow_enable(*args, **kwargs):
            assert QThread.currentThread() is not app.thread()
            time.sleep(.15)
            enable(*args, **kwargs)
        monkeypatch.setattr(panel.store, "set_enabled_many", slow_enable)
        timer.start(10)
        panel._show()
        assert panel.job is not None and panel.cancel_button.isEnabled()
        deadline = time.monotonic() + 5
        while panel.job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert panel.job is None and len(ticks) >= 5
        assert len(shown) == 1 and shown[0] == panel.store.records()[0]["bounds"]
        assert original_item.isValid() and panel.table.model().index(0, 1) == original_item
        assert panel.table.model().index(0, 0).data(Qt.CheckStateRole) == Qt.Checked
        assert len(panel.table.selectionModel().selectedRows()) == 1
    finally:
        timer.stop()
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_renew_local_cell_prompts_for_updated_files_even_offline(tmp_path, monkeypatch):
    from maritime_simulator.ui import s57_panel
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: True)
    try:
        calls = []
        monkeypatch.setattr(s57_panel.QFileDialog, "getOpenFileNames", lambda *_args: ([str(CELL)], ""))
        monkeypatch.setattr(panel, "_start", lambda operation, files: calls.append((operation, files)))
        panel._renew([{"code": "GB4TESTA"}])
        assert calls == [("import", [str(CELL)])]
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_bulk_renew_accepts_mixed_local_and_noaa_selection(tmp_path, monkeypatch):
    from maritime_simulator.ui import s57_panel
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    try:
        rows = [dict(code='GB4TESTA'), dict(code='US4NJ1DG')]
        calls = []
        monkeypatch.setattr(s57_panel, 'ask_question', lambda *_args: True)
        monkeypatch.setattr(panel, '_start', lambda operation, data: calls.append((operation, data)))
        panel._renew(rows)
        assert calls == [('renew', rows)]
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('state', ['Failed', 'Skipped'])
def test_failed_or_skipped_renew_keeps_installed_cell_and_clears_pending_marker(tmp_path, state):
    from maritime_simulator.ui import s57_panel
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    try:
        panel.store.import_files([CELL])
        panel.refresh()
        old = panel.store.records()[0]
        code = old['code']
        panel._pending[code] = dict(old)
        panel._cell_progress(code, state, 0)
        assert code not in panel._pending
        assert panel.store.records()[0] == old
        assert panel._row_states[code] == (state, 0)
        panel.refresh()
        assert panel.summary.text() == '1 installed / 0 pending cells'
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_cell_manager_refresh_retains_selected_cell_identity_after_reordering(tmp_path):
    from maritime_simulator.ui.s57_panel import S57Panel
    app = QApplication.instance() or QApplication([])
    panel = S57Panel(tmp_path, lambda: False)
    try:
        panel.store.import_files([CELL])
        panel.refresh()
        panel.table.selectRow(0)
        with closing(panel.store.connect(write=True)) as db, db:
            db.execute("INSERT INTO charts SELECT 'GB4TESTA','New overview',source,edition,update_no,date,500000,bounds,0 FROM charts WHERE code='US4NJ1DG'")
        panel.refresh()
        assert panel._rows["US4NJ1DG"] == 1
        assert [panel._items[index.row()]["code"] for index in panel.table.selectionModel().selectedRows()] == ["US4NJ1DG"]
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()
