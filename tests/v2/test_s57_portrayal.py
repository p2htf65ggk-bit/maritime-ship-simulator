# SPDX-License-Identifier: MPL-2.0
import json
import math
from contextlib import closing
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from shapely import to_wkb
from shapely.geometry import Point, LineString, box

from maritime_simulator.visual import s57
from maritime_simulator.visual.offline import OfflineSettings, OfflineTileServer
from maritime_simulator.application.startup_profile import StartupProfileStore


@pytest.fixture
def chart(tmp_path):
    store = s57.S57Store(tmp_path / "enc")
    rows = [
        ("DEPARE", box(0, 0, 2, 4), dict(DRVAL1=20, DRVAL2=40)),
        ("DEPARE", box(2, 0, 4, 4), dict(DRVAL1=0, DRVAL2=5)),
        ("DEPCNT", LineString([(2, 0), (2, 4)]), dict(VALDCO=20)),
        ("DEPCNT", LineString([(2.5, 0), (2.5, 4)]), dict(VALDCO=40)),
        ("WRECKS", Point(1, 1), dict(VALSOU=7, OBJNAM="Shoal wreck")),
        ("WRECKS", Point(1, 2), dict(VALSOU=18, OBJNAM="Deeper wreck")),
        ("UWTROC", Point(3, 1), dict(VALSOU=3, OBJNAM="Rock in shallow water")),
        ("OBSTRN", Point(1, 3), dict(EXPSOU=1, OBJNAM="Within surrounding depth range")),
        ("OBSTRN", Point(1, 3.5), dict(OBJNAM="Unknown depth")),
        ("SOUNDG", Point(1.5, 1), dict(DEPTH=7)),
        ("SOUNDG", Point(1.5, 2), dict(DEPTH=18)),
        ("TSSLPT", box(.1, .1, .5, .5), dict(ORIENT=90)),
        ("BOYLAT", Point(1, .5), dict(COLOUR=[3], BOYSHP=1)),
        ("LIGHTS", Point(1, .5), dict(SECTR1=350, SECTR2=20, COLOUR=[3], LITCHR=2)),
        ("LIGHTS", Point(1.0001, .5), dict(COLOUR=[4])),
    ]
    with closing(store.connect(write=True)) as db, db:
        db.execute("INSERT INTO charts VALUES(?,?,?,?,?,?,?,?,?)", ("TEST", "Test", "Local", "1", "0", "", 1_000_000, "[0,0,4,4]", 1))
        for layer, shape, values in rows:
            properties = dict(layer=layer, chart="TEST", **values)
            fid = db.execute("INSERT INTO features(chart,layer,geometry,properties) VALUES(?,?,?,?)",
                             ("TEST", layer, to_wkb(shape), json.dumps(properties))).lastrowid
            west, south, east, north = shape.bounds
            db.execute("INSERT INTO extent VALUES(?,?,?,?,?)", (fid, west, east, south, north))
    return store


def test_safety_depth_contour_and_hazards_are_independent(chart):
    result = chart.features((0, 0, 4, 4), 14, safety_contour=15, safety_depth=10)
    p = [f["properties"] for f in result["features"]]
    assert [f["VALDCO"] for f in p if f["layer"] == "DEPCNT" and f["safety_contour"]] == [20]
    assert [f["base_shallow"] for f in p if f["layer"] == "DEPARE"] == [False, True]
    hazards = {f["OBJNAM"]: f for f in p if f["layer"] in s57.HAZARD_LAYERS}
    assert hazards["Shoal wreck"]["dangerous"] and hazards["Shoal wreck"]["isolated_danger"]
    assert not hazards["Deeper wreck"]["dangerous"] and not hazards["Deeper wreck"]["isolated_danger"]
    assert hazards["Rock in shallow water"]["dangerous"] and not hazards["Rock in shallow water"]["isolated_danger"]
    assert not hazards["Within surrounding depth range"]["isolated_danger"]
    assert hazards["Unknown depth"]["isolated_danger"]
    assert all(f["water_context_known"] for f in hazards.values())
    assert [f["shallow_sounding"] for f in p if f["layer"] == "SOUNDG"] == [True, False]
    base = chart.features((0, 0, 4, 4), 14, base=True, safety_contour=15, safety_depth=10)
    assert {f["properties"].get("OBJNAM") for f in base["features"] if f["properties"]["layer"] in s57.HAZARD_LAYERS} == {"Shoal wreck", "Unknown depth"}
    higher = chart.features((0, 0, 4, 4), 14, safety_contour=25, safety_depth=10)
    assert [f["properties"]["VALDCO"] for f in higher["features"] if f["properties"]["layer"] == "DEPCNT" and f["properties"]["safety_contour"]] == [40]
    assert not any(f["properties"].get("isolated_danger") for f in higher["features"])


def test_hiding_water_layers_does_not_change_hazard_classification(chart):
    whole = chart.features((0, 0, 4, 4), 14, safety_contour=15, safety_depth=10)
    hazards = chart.features((0, 0, 4, 4), 14, ("WRECKS", "UWTROC", "OBSTRN"), safety_contour=15, safety_depth=10)
    expected = [f for f in whole["features"] if f["properties"]["layer"] in s57.HAZARD_LAYERS]
    assert hazards["features"] == expected


def test_depth_area_boundaries_supply_next_deeper_contour(chart):
    with closing(chart.connect(write=True)) as db, db:
        db.execute("DELETE FROM features WHERE layer='DEPCNT'")
    result = chart.features((0, 0, 4, 4), 14, safety_contour=10, safety_depth=8)
    assert result["effective_safety_contours"] == {"TEST": 20}
    areas = [f["properties"] for f in result["features"] if f["properties"]["layer"] == "DEPARE"]
    assert [p["base_shallow"] for p in areas] == [False, True]
    assert all(p["effective_safety_contour_m"] == 20 for p in areas)
    assert chart.features((0, 0, 4, 4), 14, ("SOUNDG",), safety_contour=10)["effective_safety_contours"] == {"TEST": 20}
    # No deeper boundary is reported explicitly, not silently invented at the requested value.
    assert chart.features((0, 0, 4, 4), 14, safety_contour=60)["effective_safety_contours"] == {"TEST": None}


def test_adjacent_cells_keep_independent_contours_and_dredged_paint_order(chart):
    with closing(chart.connect(write=True)) as db, db:
        db.execute("INSERT INTO charts VALUES(?,?,?,?,?,?,?,?,?)", ("EAST", "East", "Local", "1", "0", "", 1_000_000, "[4,0,8,4]", 1))
        # Insert a dredged area first: output order must not depend on feature IDs.
        for code, layer, geometry, fields in [
            ("EAST", "DRGARE", box(4.5, .5, 5.5, 1.5), dict(DRVAL1=13.8, DRVAL2=None)),
            ("EAST", "DEPARE", box(4, 0, 8, 4), dict(DRVAL1=10, DRVAL2=15)),
            ("EAST", "DEPCNT", LineString([(4, 0), (4, 4)]), dict(VALDCO=10)),
        ]:
            props = dict(chart=code, layer=layer, **fields)
            fid = db.execute("INSERT INTO features(chart,layer,geometry,properties) VALUES(?,?,?,?)",
                (code, layer, to_wkb(geometry), json.dumps(props))).lastrowid
            west, south, east, north = geometry.bounds
            db.execute("INSERT INTO extent VALUES(?,?,?,?,?)", (fid, west, east, south, north))
    for zoom in (12, 14, 16):
        result = chart.features((0, 0, 8, 4), zoom, safety_contour=10, safety_depth=8)
        assert result["effective_safety_contours"] == {"EAST": 10, "TEST": 20}
        layers = [f["properties"]["layer"] for f in result["features"]]
        assert max(i for i, layer in enumerate(layers) if layer == "DEPARE") < layers.index("DRGARE")
        east = [f["properties"] for f in result["features"] if f["properties"]["chart"] == "EAST"]
        assert all(not p["base_shallow"] for p in east if p["layer"] in {"DEPARE", "DRGARE"})
        base = chart.features((0, 0, 8, 4), zoom, base=True, safety_contour=10, safety_depth=8)
        assert any(f["properties"]["layer"] == "DRGARE" for f in base["features"])


def test_combination_requires_same_encoded_position_and_keeps_light_sectors(chart):
    p = [f["properties"] for f in chart.features((0, 0, 4, 4), 14)["features"]]
    aid = next(f for f in p if f["layer"] == "BOYLAT")
    assert len(aid["lights"]) == 1 and aid["lights"][0]["SECTR1"] == 350
    lights = [f for f in p if f["layer"] == "LIGHTS"]
    assert lights[0]["combined_light"] and "combined_light" not in lights[1]
    only = chart.features((0, 0, 4, 4), 14, ("LIGHTS",))
    assert all("combined_light" not in f["properties"] for f in only["features"])


def test_continental_overview_uses_world_not_cell_fragments(chart):
    overview = chart.features((0, 0, 4, 4), 6)
    assert overview["overview"] and not overview["features"] and overview["chart_count"] == 0
    closer = chart.features((0, 0, 4, 4), 8)
    assert closer["features"] and closer["chart_count"] == 1
    assert all(f["geometry"]["type"] not in {"Point", "MultiPoint"} for f in closer["features"])


@pytest.mark.parametrize("value", [math.nan, math.inf, -1, 1001, "bad", None])
def test_invalid_safety_parameters_rejected(chart, value):
    with pytest.raises(ValueError, match="safety"):
        chart.features((0, 0, 4, 4), 14, safety_contour=value)
    with pytest.raises(ValueError, match="safety"):
        OfflineSettings.from_payload({"enc_safety_depth_m": value})


def test_http_cache_includes_safety_settings_and_rejects_invalid_inputs(chart):
    server = OfflineTileServer(None, chart)
    try:
        url = server.enc_url + "?bbox=0,0,4,4&zoom=14&contour=15&depth="
        with urlopen(url + "10", timeout=5) as reply:
            low = json.load(reply)
        with urlopen(url + "25", timeout=5) as reply:
            high = json.load(reply)
        assert low["safety_depth_m"] == 10 and high["safety_depth_m"] == 25
        assert low["features"] != high["features"] and len(server._enc_cache) == 2
        with pytest.raises(HTTPError) as error:
            urlopen(url + "NaN", timeout=5)
        assert error.value.code == 400
    finally:
        server.close()


def test_settings_roundtrip_and_fixed_map_group_preserve_safety(tmp_path):
    settings = OfflineSettings.from_payload({"map_source": "s57", "enc_safety_contour_m": 12.5, "enc_safety_depth_m": 8.4})
    assert OfflineSettings.from_payload(json.loads(json.dumps(settings.payload()))) == settings
    startup = StartupProfileStore(tmp_path / "startup.json")
    startup.capture_fixed("map", {"format": "maritime-ship-simulator-v2-configuration", "offline": settings.payload()}, select=True)
    restored = StartupProfileStore(startup.path)
    defaults = {"format": "maritime-ship-simulator-v2-configuration", "offline": OfflineSettings().payload()}
    result = restored.resolve(None, defaults)["offline"]
    assert result["enc_safety_contour_m"] == 12.5 and result["enc_safety_depth_m"] == 8.4


def test_import_retains_real_additional_attributes(tmp_path):
    store = s57.S57Store(tmp_path / "import")
    store.import_files(["tests/fixtures/s57/US4NJ1DG.zip"])
    properties = [f["properties"] for f in store.features((-73.8, 39.6, -73.5, 39.9), 14)["features"]]
    assert any(p.get("QUASOU") == ["2"] for p in properties)
    assert {"ORIENT", "TRAFIC", "SECTR1", "SECTR2", "VERCLR", "VERCSA", "CATREA", "EXPSOU",
            "CATLMK", "FUNCTN", "CATMOR", "CATSIL", "CONVIS", "CATPYL", "CATCRN", "CATHAF"} <= set(s57.ATTRIBUTE_NAMES)


def test_import_preserves_direction_zero_and_sector_values(tmp_path, monkeypatch):
    import numpy as np
    import pyogrio.raw
    read = pyogrio.raw.read
    def populated(*args, **kwargs):
        info, ids, geometries, values = read(*args, **kwargs)
        fields = {"TSSLPT": {"ORIENT": 0}, "LIGHTS": {"SECTR1": 350, "SECTR2": 20},
                  "LNDARE": {"CATLMK": 17, "CATMOR": 3, "CATSIL": 2, "CONVIS": 1, "FUNCTN": 20,
                             "CATPYL": 4, "CATCRN": 2, "CATHAF": 10}}.get(kwargs.get("layer"), {})
        if fields and geometries is not None and len(geometries):
            values = list(values)
            for key, value in fields.items():
                names = list(info["fields"])
                column = np.full(len(geometries), value)
                if key in names:
                    values[names.index(key)] = column
                else:
                    info["fields"] = np.append(info["fields"], key)
                    values.append(column)
        return info, ids, geometries, values
    monkeypatch.setattr(pyogrio.raw, "read", populated)
    store = s57.S57Store(tmp_path)
    store.import_files(["tests/fixtures/s57/US4NJ1DG.zip"])
    p = [f["properties"] for f in store.features((-73.8, 39.6, -73.5, 39.9), 14)["features"]]
    assert all(f["ORIENT"] == 0 for f in p if f["layer"] == "TSSLPT")
    assert next(f for f in p if f["layer"] == "LIGHTS")["SECTR1"] == 350
    for f in p:
        if f["layer"] == "LNDARE":
            assert f["CATLMK"] == 17 and f["CATMOR"] == 3 and f["CATSIL"] == 2
            assert f["CONVIS"] == 1 and f["FUNCTN"] == 20
            assert f["CATPYL"] == 4 and f["CATCRN"] == 2 and f["CATHAF"] == 10


def test_safety_dialog_and_menu_preserve_layer_selection(monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog
    from maritime_simulator.ui import s57_layers
    app = QApplication.instance() or QApplication([])
    menu = s57_layers.S57LayerMenu({"LNDARE": 1}, {}, {"LNDARE"}, base=True)
    changes = []
    layer_changes = []
    menu.safety_changed.connect(lambda *values: changes.append(values))
    menu.selection_changed.connect(lambda *values: layer_changes.append(values))
    def accept(dialog):
        assert dialog.property("opaqueSurface")
        assert dialog.contour.decimals() == dialog.depth.decimals() == 1
        dialog.contour.setValue(12.5)
        dialog.depth.setValue(8.4)
        return QDialog.Accepted
    monkeypatch.setattr(s57_layers.S57SafetyDialog, "exec", accept)
    try:
        menu._choose_safety()
        assert changes == [(12.5, 8.4)] and layer_changes == [] and menu.base
        monkeypatch.setattr(s57_layers.S57SafetyDialog, "exec", lambda _dialog: QDialog.Rejected)
        menu._choose_safety()
        assert changes == [(12.5, 8.4)]
    finally:
        menu.deleteLater()
        app.processEvents()
