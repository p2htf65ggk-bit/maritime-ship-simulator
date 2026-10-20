#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Interactive QtWebEngine smoke for the V2 map route-command bridge."""

from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QInputDevice, QMouseEvent, QPointingDevice, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.ui.main_window import V2MainWindow
from ui.style import apply_global_ui_style


def _wait(app: QApplication, predicate, timeout_s: float = 8.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for QtWebEngine map")
        app.processEvents()
        time.sleep(0.02)


def _javascript(window: V2MainWindow, script: str):
    loop = QEventLoop()
    result = []
    window.map.view.page().runJavaScript(script, lambda value: (result.append(value), loop.quit()))
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    if not result:
        raise RuntimeError(f"JavaScript callback timed out: {script}")
    return result[0]


def _verify_native_map_input(app, window):
    view = window.map.view
    target = view.focusProxy() or view
    mouse = QPointingDevice("Smoke wheel mouse", 5001, QInputDevice.DeviceType.Mouse,
        QPointingDevice.PointerType.Generic, QInputDevice.Capability.Position | QInputDevice.Capability.Scroll, 1, 3)
    touchpad = QPointingDevice("Shared Cocoa mouse/touchpad", 5002, QInputDevice.DeviceType.TouchPad,
        QPointingDevice.PointerType.Finger, QInputDevice.Capability.Position | QInputDevice.Capability.PixelScroll, 2, 1)
    wheel_device = touchpad if sys.platform == "darwin" else mouse
    point = QPointF(target.width() * .5, target.height() * .45)
    global_point = QPointF(target.mapToGlobal(point.toPoint()))

    def state():
        return json.loads(_javascript(window,
            "JSON.stringify({zoom:map.getZoom(),lat:map.getCenter().lat,lng:map.getCenter().lng,follow:followOwnShip,pixel:map.project(map.getCenter())})"))

    def wheel(pixel, angle, phase, device, source=Qt.MouseEventNotSynthesized):
        event = QWheelEvent(point, global_point, QPoint(*pixel), QPoint(*angle), Qt.NoButton,
            Qt.NoModifier, phase, False, source, device)
        QApplication.sendEvent(target, event)
        app.processEvents()

    _javascript(window, "followOwnShip=false; map.setView([53.54,9.93],14,{animate:false}); true")
    before = state()
    wheel((0, 2), (0, 120), Qt.NoScrollPhase, wheel_device)
    for _ in range(4):
        wheel((0, 2), (0, 120), Qt.ScrollUpdate, wheel_device)
    wheel((0, 0), (0, 0), Qt.ScrollEnd, wheel_device)
    QTest.qWait(150)
    _wait(app, lambda: bool(_javascript(window, "!smoothZoomActive")))
    after = state()
    if after["zoom"] <= before["zoom"] or abs(after["lat"] - before["lat"]) + abs(after["lng"] - before["lng"]) > .0001:
        raise RuntimeError(f"Native wheel sequence panned instead of zooming: {before} -> {after}")
    _javascript(window, "map.setView([53.54,9.93],14,{animate:false}); true")
    before_pan = state()
    wheel((0, 0), (0, 0), Qt.ScrollBegin, touchpad, Qt.MouseEventSynthesizedBySystem)
    for _ in range(40 if sys.platform == "darwin" else 20):
        pixels = (0, 0) if sys.platform == "darwin" else (0, 1)
        wheel(pixels, (0, 1), Qt.ScrollUpdate, touchpad, Qt.MouseEventSynthesizedBySystem)
    wheel((0, 0), (0, 0), Qt.ScrollEnd, touchpad, Qt.MouseEventSynthesizedBySystem)
    QTest.qWait(150)
    _wait(app, lambda: bool(_javascript(window, "!smoothZoomActive")))
    after_pan = state()
    if after_pan["zoom"] != before_pan["zoom"] or (after_pan["lat"], after_pan["lng"]) == (before_pan["lat"], before_pan["lng"]):
        raise RuntimeError(f"Native trackpad did not remain pan-only: {before_pan} -> {after_pan}")
    if abs(after_pan["pixel"]["y"] - before_pan["pixel"]["y"] + 20) > 1:
        raise RuntimeError(f"Small trackpad movements were lost: {before_pan} -> {after_pan}")
    wheel((0, 2), (0, 120), Qt.NoScrollPhase, wheel_device)
    QTest.qWait(150)
    _wait(app, lambda: bool(_javascript(window, "!smoothZoomActive")))
    after_wheel = state()
    if abs(after_wheel["zoom"] - after_pan["zoom"] - 1) > .01:
        raise RuntimeError(f"Wheel failed after trackpad on shared Cocoa device: {after_pan} -> {after_wheel}")

    def mouse_event(kind, y, button, buttons):
        pos = QPointF(point.x(), y)
        event = QMouseEvent(kind, pos, QPointF(target.mapToGlobal(pos.toPoint())), button, buttons, Qt.NoModifier, mouse)
        QApplication.sendEvent(target, event)
        app.processEvents()

    for first, second in ((Qt.LeftButton, Qt.RightButton), (Qt.RightButton, Qt.LeftButton)):
        _javascript(window, "followOwnShip=false; map.setView([53.54,9.93],14,{animate:false}); true")
        _javascript(window, "window.nativeMouseLog=[]; ['mousedown','mousemove','mouseup'].forEach(type=>document.addEventListener(type,e=>nativeMouseLog.push({type:e.type,button:e.button,buttons:e.buttons,target:e.target.className}),{capture:true,once:true})); true")
        QTest.mouseMove(target, point.toPoint())
        QTest.qWait(50)
        before = state()
        mouse_event(QMouseEvent.Type.MouseButtonPress, point.y(), first, first)
        mouse_event(QMouseEvent.Type.MouseButtonPress, point.y(), second, Qt.LeftButton | Qt.RightButton)
        for step in range(1, 5):
            mouse_event(QMouseEvent.Type.MouseMove, point.y() + 12 * step, Qt.NoButton, Qt.LeftButton | Qt.RightButton)
        mouse_event(QMouseEvent.Type.MouseButtonRelease, point.y() + 48, second, first)
        mouse_event(QMouseEvent.Type.MouseButtonRelease, point.y() + 48, first, Qt.NoButton)
        QTest.qWait(150)
        _wait(app, lambda: bool(_javascript(window, "!smoothZoomActive")))
        after = state()
        if after["zoom"] >= before["zoom"] or abs(after["lat"] - before["lat"]) + abs(after["lng"] - before["lng"]) > .0001:
            trace = _javascript(window, "JSON.stringify(window.nativeMouseLog)")
            raise RuntimeError(f"Native two-button gesture did not zoom in place on {target.metaObject().className()}: {before} -> {after}; {trace}")
    print(f"Native map input passed on {target.metaObject().className()}: shared-device wheel/pad, half-pixel pan, both mouse chords")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    with tempfile.TemporaryDirectory() as temporary:
        window = V2MainWindow(config_dir=Path(temporary))
        window._interactive_close_confirmation_enabled = lambda: False
        window._request_mode(ControlMode.TRACK)
        window.show()
        _wait(app, lambda: window.map.loaded and window.map.view is not None)
        _wait(
            app,
            lambda: bool(_javascript(window, "window.mapReady && routeWaypointMarkers.length > 1")),
        )
        window.timer.stop()
        map_state = json.loads(_javascript(
            window,
            "JSON.stringify({context: contextMode, waypoints: routeWaypointMarkers.length})",
        ))
        if map_state.get("context") != "track" or int(map_state.get("waypoints", 0)) < 2:
            raise RuntimeError(f"Unexpected map state: {map_state}")

        try:
            _verify_native_map_input(app, window)
        except Exception:
            window.close()
            app.processEvents()
            raise

        own_ship = window.service.snapshot()
        ais_target = {
            "mmsi": 211000001,
            "name": "POPUP SMOKE",
            "lat": own_ship.latitude_deg,
            "lon": own_ship.longitude_deg,
            "speed": 8.0,
            "course": 90.0,
            "heading": 88.0,
            "dead_reckoning_active": True,
            "dead_reckoning_age_s": 12.6,
            "active": True,
        }
        _javascript(window, f"updateAIS({json.dumps([ais_target])}); true")
        _javascript(window, "aisMarkers.get('211000001').openPopup(); true")
        first_popup = json.loads(_javascript(
            window,
            "JSON.stringify({"
            "text: document.querySelector('.ais-nowrap').textContent,"
            "whiteSpace: getComputedStyle(document.querySelector('.ais-nowrap')).whiteSpace"
            "})",
        ))
        if first_popup != {"text": "Dead reckoning 13 s", "whiteSpace": "nowrap"}:
            raise RuntimeError(f"Unexpected initial AIS popup state: {first_popup}")
        ais_target["dead_reckoning_age_s"] = 999.0
        _javascript(window, f"updateAIS({json.dumps([ais_target])}); true")
        updated_popup = _javascript(window, "document.querySelector('.ais-nowrap').textContent")
        if updated_popup != "Dead reckoning 999 s":
            raise RuntimeError(f"Open AIS popup was not refreshed: {updated_popup}")
        print("Live AIS popup refresh: 13 s -> 999 s, nowrap")

        _javascript(window, "requestMapExpand(); true")
        original_view = window.map.view
        _wait(app, lambda: window._map_detached)
        if window.map_dialog is None or window.map.parentWidget() is not window.map_dialog:
            raise RuntimeError("Map expand command did not move the active map into its dialog")
        _verify_native_map_input(app, window)
        window._restore_map()
        _wait(
            app,
            lambda: (
                not window._map_detached
                and window.navigation_stack.indexOf(window.map) >= 0
                and window.navigation_stack.currentWidget() is window.map
            ),
        )
        if window.map.view is not original_view:
            raise RuntimeError("Map restore created a second QtWebEngine view")

        route = window.service.snapshot().route_waypoints
        target = route[2]
        _javascript(
            window,
            f"emitRouteEvent({{action:'set_ownship',lat:{target.latitude_deg},lon:{target.longitude_deg}}}); true",
        )
        _wait(app, lambda: abs(window.service.snapshot().latitude_deg - target.latitude_deg) < 1e-8)

        moved_lat = route[1].latitude_deg + 0.0002
        moved_lon = route[1].longitude_deg + 0.0002
        _javascript(
            window,
            f"emitRouteEvent({{action:'move',index:1,lat:{moved_lat},lon:{moved_lon}}}); true",
        )
        _wait(app, lambda: abs(window.service.snapshot().route_waypoints[1].latitude_deg - moved_lat) < 1e-8)
        window.close()
        app.processEvents()
    print("V2 QtWebEngine map bridge smoke passed")
    return 0


if __name__ == "__main__":
    try:
        result = main()
    finally:
        application = QApplication.instance()
        if application is not None:
            application.closeAllWindows()
            application.processEvents()
    raise SystemExit(result)
