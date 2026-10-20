# SPDX-License-Identifier: MPL-2.0
"""Native Qt discard/reload smoke check. Offline, without API credentials."""
from pathlib import Path
import json
import os
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ.pop("SHIP_SIMULATOR_HEADLESS_MAP", None)
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ["MARITIME_SIM_GOOGLE_3D_TILES_API_KEY"] = ""

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from maritime_simulator.domain.state import SimulationState
from maritime_simulator.ui.forward_view import ForwardViewDialog
from ui.webengine_profile import set_webengine_offline
from ui.widgets.map_widget import MapWidget


def wait_for(condition, timeout=15):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for native WebEngine state")
        time.sleep(.01)


def evaluate(widget, code):
    results = []
    widget.view.page().runJavaScript(code, results.append)
    wait_for(lambda: bool(results))
    return results[0]


app = QApplication([])
set_webengine_offline(True)
result = {}
map_widget = MapWidget()
map_widget.resize(1000, 700)
try:
    map_widget.show()
    wait_for(lambda: map_widget.loaded)
    map_widget.update_ship(40.7,-74,45)
    map_widget.update_route([{"name":"Test","lat":40.7,"lon":-74}])
    state = {"center":[40.705,-74.01],"zoom":14.5,"follow":False,"vectorMode":"predicted","busy":False}
    evaluate(map_widget, f"window.restoreNavigationState({json.dumps(state)});")
    before = evaluate(map_widget, "JSON.stringify(window.exportNavigationState());")
    map_widget.hide()
    wait_for(lambda: map_widget.memory_page.state is not None)
    map_widget.memory_page.hidden_since -= 61
    assert map_widget.memory_page.discard_if_idle(True)
    assert not map_widget.loaded
    # New route/position received while discarded must survive too.
    map_widget.update_route([{"name":"Updated","lat":40.72,"lon":-74.02}])
    map_widget.update_ship(40.72,-74.02,90)
    map_widget.show()
    wait_for(lambda: map_widget.loaded)
    after = evaluate(map_widget, "JSON.stringify(window.exportNavigationState());")
    assert json.loads(after) == json.loads(before), (before,after)
    assert evaluate(map_widget, "ship.getLatLng().lat;") == 40.72
    assert evaluate(map_widget, "routeWaypointMarkers[0].getLatLng().lat;") == 40.72
    result["map"] = "discarded/reloaded; view, current own position and route retained"
finally:
    map_widget.close()
    map_widget.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

with tempfile.TemporaryDirectory() as directory:
    settings = Path(directory)/"visual_settings_v2.json"
    settings.write_text('{"renderer_mode":"google_maps_3d","google_tiles_api_key":""}', encoding="utf-8")
    snapshot = SimulationState().snapshot()
    dialog = ForwardViewDialog(lambda:snapshot,lambda _snapshot:[],Path(directory),settings_path=settings)
    try:
        dialog.show()
        wait_for(lambda: dialog.loaded)
        state = {"viewMode":"orbit","viewBearingDeg":73,"viewElevationDeg":45,"viewRangeM":730,
                 "panRightM":125,"panForwardM":-60,"hudVisible":False,"namesVisible":False,"controlsVisible":False}
        evaluate(dialog, f"window.restoreNavigationState({json.dumps(state)});")
        before = evaluate(dialog, "JSON.stringify(window.exportNavigationState());")
        renderer = dialog.active_renderer
        dialog.hide()
        wait_for(lambda: dialog.memory_page.state is not None)
        dialog.memory_page.hidden_since -= 61
        assert dialog.memory_page.discard_if_idle(True)
        dialog.show()
        wait_for(lambda: dialog.loaded)
        after = evaluate(dialog, "JSON.stringify(window.exportNavigationState());")
        assert json.loads(after) == json.loads(before), (before,after)
        assert dialog.active_renderer == renderer
        result["3d"] = "discarded/reloaded; camera, HUD and provider retained"
    finally:
        dialog.shutdown()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
print(json.dumps(result, indent=2))
