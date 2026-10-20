# SPDX-License-Identifier: MPL-2.0
import os
import json
from contextlib import closing

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

from maritime_simulator.ui.s57_layers import S57LayerMenu, MAX_MENU_LAYERS, layer_path
from maritime_simulator.visual.offline import OfflineSettings
from maritime_simulator.visual import s57


def test_group_toggles_descendants_once_and_updates_partial_parents():
    app = QApplication.instance() or QApplication([])
    available = dict.fromkeys(["FAIRWY", "TSSBND", "BRIDGE", "CBLSUB", "BOYLAT"], None)
    menu = S57LayerMenu(available, {}, {"FAIRWY"})
    changes = []
    menu.selection_changed.connect(lambda layers, base: changes.append((layers, base)))
    try:
        routes = menu.groups[("Other Layers", "Routes and Traffic")][0]
        other = menu.groups[("Other Layers",)][0]
        assert routes.checkState() == other.checkState() == Qt.PartiallyChecked
        routes.click()
        assert menu.selected == {"FAIRWY", "TSSBND"} and len(changes) == 1
        assert routes.checkState() == Qt.Checked and other.checkState() == Qt.PartiallyChecked
        other.click()
        assert menu.selected == {"FAIRWY", "TSSBND", "BRIDGE", "CBLSUB"}
        assert len(changes) == 2 and not menu.leaves["BOYLAT"].isChecked()
        other.click()
        assert not menu.selected and len(changes) == 3
        menu.groups[()][0].click()
        assert changes[-1] == (None, False)
        menu.base_action.trigger()
        assert menu.base and changes[-1] == (None, True)
        menu.leaves["BOYLAT"].click()
        assert not menu.base and changes[-1][1] is False
    finally:
        menu.deleteLater()
        app.processEvents()


def test_long_groups_are_paginated_without_losing_layers():
    app = QApplication.instance() or QApplication([])
    available = dict.fromkeys((f"EXTRA{i}" for i in range(65)), None)
    menu = S57LayerMenu(available, {}, available)
    try:
        assert menu.leaves.keys() == available.keys()
        assert layer_path("boylat") == ("Aids to Navigation", "Buoys")
        assert layer_path("EXTRA") == ("Other Layers", "Additional Objects")
        for submenu in menu.findChildren(QMenu):
            assert len(submenu.actions()) <= MAX_MENU_LAYERS + 2
        assert len([path for path in menu.groups if len(path) == 3]) == 7
        menu.groups[("Other Layers", "Additional Objects", "1-10")][0].click()
        assert len(menu.selected) == 55
        assert menu.groups[("Other Layers", "Additional Objects")][0].checkState() == Qt.PartiallyChecked
    finally:
        menu.deleteLater()
        app.processEvents()


def test_legacy_catalogue_can_disable_single_layers_groups_and_restore_profiles(tmp_path, monkeypatch):
    from maritime_simulator.ui import main_window
    app = QApplication.instance() or QApplication([])
    window = main_window.V2MainWindow(config_dir=tmp_path)
    warnings = []
    monkeypatch.setattr(main_window, "show_warning", lambda *args: warnings.append(args[-1]))
    try:
        window.timer.stop()
        window.s57_store.import_files(["tests/fixtures/s57/US4NJ1DG.zip"])
        with closing(window.s57_store.connect(write=True)) as db, db:
            db.execute("DROP TABLE chart_layers")
        window._apply_offline_settings(OfflineSettings(False, map_source="s57"))
        window._choose_s57_layers()
        menu = window._s57_layers_menu
        assert len(menu.available) > 200
        menu.leaves["SOUNDG"].click()
        assert len(window.offline_settings.enc_layers) > 200
        assert "SOUNDG" not in window.offline_settings.enc_layers
        assert window.map._map_source["layers"] == window.offline_settings.enc_layers
        snapshot = json.loads(json.dumps(window._configuration_payload()))
        window.startup_profile.capture_fixed("map", snapshot, select=True)
        resolved = window.startup_profile.resolve(None, snapshot)
        assert OfflineSettings.from_payload(resolved["offline"]).enc_layers == window.offline_settings.enc_layers
        for _ in range(3):
            menu.groups[("Other Layers",)][0].click()
            assert set(window.offline_settings.enc_layers) == menu.selected
        menu.groups[()][0].click()
        assert window.offline_settings.enc_layers is None
        menu.base_action.trigger()
        assert window.offline_settings.enc_base
        menu.selection_changed.emit(("invalid name",), False)
        assert window.offline_settings.enc_base and menu.base
        assert menu.selected == menu.available.keys() & s57.BASE_LAYERS
        assert len(warnings) == 1 and "Invalid S57 layer selection" in warnings[0]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("bad", ["DEPARE", {"DEPARE"}, [123], [None], ["invalid name"], [""], ["A" * 17],
                                      [f"L{i}" for i in range(s57.MAX_LAYER_SELECTION + 1)]])
def test_layer_validation_is_identical_for_settings_and_queries(tmp_path, bad):
    store = s57.S57Store(tmp_path)
    with pytest.raises(ValueError, match="Invalid S57 layer selection"):
        OfflineSettings.from_payload({"enc_layers": bad})
    with pytest.raises(ValueError, match="Invalid S57 layer selection"):
        store.features((0, 0, 1, 1), 14, bad)


def test_full_selection_limit_preserves_extension_case_and_empty_semantics():
    values = [f"L{i}" for i in range(s57.MAX_LAYER_SELECTION - 2)] + ["DEPARE", "depare"]
    assert len(OfflineSettings.from_payload({"enc_layers": values}).enc_layers) == s57.MAX_LAYER_SELECTION
    assert s57.layer_selection(["DEPARE", "depare", "DEPARE"]) == ("DEPARE", "depare")
    assert s57.layer_selection([]) == ()
    assert s57.layer_selection(None) is None
