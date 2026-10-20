# SPDX-License-Identifier: MPL-2.0
import os
import json
import sys
import time

from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QInputDevice

from ui.webengine_profile import configure_webengine_profile


def _use_headless_map():
    platform = os.environ.get("QT_QPA_PLATFORM", "").lower()
    return (
        os.environ.get("SHIP_SIMULATOR_HEADLESS_MAP") == "1"
        or platform in {"offscreen", "minimal"}
    )


def _resource_path(*parts):
    base_path = getattr(sys, "_MEIPASS", None)
    if base_path:
        return os.path.join(base_path, *parts)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", *parts))


def _trackpad_pan_pixels(event):
    """Return browser-style pan deltas for a high-resolution trackpad scroll."""
    if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
        return None
    if sys.platform == "darwin" and _cocoa_precise_scroll(event):
        # Cocoa preserves half-pixels in angleDelta, but truncates pixelDelta.
        angle_delta = event.angleDelta()
        if not angle_delta.isNull():
            return -angle_delta.x() / 2.0, -angle_delta.y() / 2.0
    pixel_delta = event.pixelDelta()
    if pixel_delta.isNull():
        return None
    try:
        is_touchpad = event.device().type() == QInputDevice.DeviceType.TouchPad
    except (AttributeError, RuntimeError):
        is_touchpad = False
    has_scroll_phase = event.phase() != Qt.ScrollPhase.NoScrollPhase
    if not is_touchpad and not has_scroll_phase and pixel_delta.x() == 0:
        return None
    return -int(pixel_delta.x()), -int(pixel_delta.y())


def _cocoa_precise_scroll(event):
    try:
        return event.source() == Qt.MouseEventSource.MouseEventSynthesizedBySystem
    except (AttributeError, RuntimeError):
        return False


def _map_wheel_zoom_steps(event, *, mouse_wheel=False):
    angle_delta = event.angleDelta()
    if mouse_wheel and angle_delta.y():
        return max(-4.0, min(4.0, float(angle_delta.y()) / 120.0))
    pixel_delta = event.pixelDelta()
    if not pixel_delta.isNull():
        return max(-4.0, min(4.0, float(pixel_delta.y()) / 120.0))
    return max(-4.0, min(4.0, float(angle_delta.y()) / 120.0))


def _map_scroll_kind(event):
    pixel, angle = event.pixelDelta(), event.angleDelta()
    if sys.platform == "darwin":
        # Cocoa can reuse a device marked TouchPad for a later physical wheel.
        # Trust per-event precision/phase before that shared device descriptor.
        if event.phase() == Qt.ScrollPhase.NoScrollPhase:
            return "pan" if pixel.x() else "zoom"
        try:
            if event.source() == Qt.MouseEventSource.MouseEventNotSynthesized:
                return "zoom"
        except (AttributeError, RuntimeError):
            pass
    try:
        if event.device().type() == QInputDevice.DeviceType.TouchPad:
            return "pan"
    except (AttributeError, RuntimeError):
        pass
    if pixel.isNull():
        return "zoom"
    if event.phase() == Qt.ScrollPhase.NoScrollPhase:
        return "pan" if pixel.x() else "zoom"
    if angle.y() and abs(angle.y()) % 120 == 0:
        return "zoom"
    return "pan"


def _enforce_map_view_zoom(view):
    if (
        view is not None
        and hasattr(view, "zoomFactor")
        and hasattr(view, "setZoomFactor")
        and abs(float(view.zoomFactor()) - 1.0) > 1e-6
    ):
        view.setZoomFactor(1.0)


def _event_position_in_view(watched, event, view):
    try:
        point = event.position().toPoint()
        if isinstance(watched, QWidget) and watched is not view:
            point = view.mapFromGlobal(watched.mapToGlobal(point))
        return float(point.x()), float(point.y())
    except (AttributeError, RuntimeError, TypeError):
        return None


class _MapInputFilter(QObject):
    """Route wheel, trackpad and pinch to Leaflet without changing page scale."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self._active = True
        self._last_native_zoom_at = 0.0
        self._scroll_kind = None
        self._scroll_device = None
        self._scroll_source = None
        self._last_scroll_at = 0.0
        self._install_pending = False

    def install_tree(self, root):
        if not self._active:
            return
        try:
            for widget in (root, *root.findChildren(QWidget)):
                widget.installEventFilter(self)
        except RuntimeError:
            return

    def disable(self):
        self._active = False
        self._view = None

    def _refresh_filters(self):
        self._install_pending = False
        if self._active and self._view is not None:
            self.install_tree(self._view)

    def eventFilter(self, watched, event):
        if not self._active or self._view is None:
            return False
        if event.type() in (QEvent.Type.ChildAdded, QEvent.Type.ChildPolished):
            # Wait until Chromium's render widget has finished construction.
            if not self._install_pending:
                self._install_pending = True
                QTimer.singleShot(0, self._refresh_filters)
            return False
        steps = None
        if event.type() == QEvent.Type.NativeGesture:
            try:
                if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                    steps = float(event.value())
                    self._last_native_zoom_at = time.monotonic()
            except AttributeError:
                steps = None
        elif (
            event.type() == QEvent.Type.Wheel
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            # macOS and some Windows touchpad drivers emit a Ctrl-wheel event
            # immediately after the native pinch event. Consume that duplicate
            # instead of zooming the map twice.
            if time.monotonic() - self._last_native_zoom_at < 0.16:
                event.accept()
                return True
            steps = _map_wheel_zoom_steps(event)
        elif event.type() == QEvent.Type.Wheel:
            now = time.monotonic()
            phase = event.phase()
            try:
                device = event.device().systemId()
            except (AttributeError, RuntimeError):
                device = event.device().type()
            try:
                source = event.source()
            except (AttributeError, RuntimeError):
                source = None
            if (phase == Qt.ScrollPhase.ScrollBegin
                    or device != self._scroll_device
                    or (source != self._scroll_source
                        and source == Qt.MouseEventSource.MouseEventNotSynthesized)
                    or now - self._last_scroll_at > .35):
                self._scroll_kind = None
            pixel, angle = event.pixelDelta(), event.angleDelta()
            if not pixel.isNull() or not angle.isNull():
                if self._scroll_kind is None or phase == Qt.ScrollPhase.NoScrollPhase:
                    self._scroll_kind = _map_scroll_kind(event)
                self._scroll_device, self._last_scroll_at = device, now
                self._scroll_source = source
            kind = self._scroll_kind
            if phase == Qt.ScrollPhase.ScrollEnd:
                self._scroll_kind = None
            if kind == "pan":
                pan = _trackpad_pan_pixels(event)
                if pan is None:
                    pan = (-angle.x() / 8.0, -angle.y() / 8.0)
                try:
                    self._view.page().runJavaScript(
                        "if (window.panMapByTrackpad) "
                        f"window.panMapByTrackpad({pan[0]}, {pan[1]});"
                    )
                except RuntimeError:
                    return False
                event.accept()
                return True
            steps = _map_wheel_zoom_steps(event, mouse_wheel=True)
            if abs(steps) < 1e-6:
                event.accept()
                return True
        if steps is None or abs(steps) < 1e-6:
            return False
        try:
            position = _event_position_in_view(watched, event, self._view)
            arguments = f"{steps:.8f}"
            if position is not None:
                arguments += f", {position[0]:.3f}, {position[1]:.3f}"
            self._view.page().runJavaScript(
                "if (window.zoomMapByNativeGesture) "
                f"window.zoomMapByNativeGesture({arguments});"
            )
            event.accept()
            return True
        except RuntimeError:
            return False


class MapBridge(QObject):
    routeEvent = Signal(dict)
    mapCommand = Signal(dict)

    @Slot(str)
    def sendRouteEvent(self, payload):
        try:
            event = json.loads(payload)
        except (TypeError, ValueError):
            return
        if isinstance(event, dict):
            self.routeEvent.emit(event)

    @Slot(str)
    def sendMapCommand(self, payload):
        try:
            command = json.loads(payload)
        except (TypeError, ValueError):
            return
        if isinstance(command, dict):
            self.mapCommand.emit(command)


class MapWidget(QWidget):
    route_event = Signal(dict)
    map_command = Signal(dict)

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        self.view = None
        self.bridge = None
        self.channel = None
        self.loaded = _use_headless_map()
        self._last_ship = None
        self._last_route_json = ""
        self._pending_wheel_over_marks = []
        self._pending_turn_arcs = []
        self._last_ais_json = ""
        self._last_ais_emit = 0.0
        self._last_ship_emit = 0.0
        self._last_context_mode = None
        self._last_ais_edit_mode = None
        self._last_history_reset_token = None
        self._detached = False
        self._page_active = True
        self._map_source = None
        self._pending_scripts = {}
        self._ship_emit_interval = 0.10 if sys.platform.startswith("win") else 0.06
        self._ais_emit_min_interval = 0.45 if sys.platform.startswith("win") else 0.35
        self.performance_monitor = None
        self._input_filter = None
        self._zoom_guard_timer = None
        self.memory_page = None

        if self.loaded:
            placeholder = QLabel("Map disabled in headless/offscreen mode")
            placeholder.setObjectName("HeadlessMapPlaceholder")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(
                "QLabel#HeadlessMapPlaceholder {"
                "background:#101820;"
                "color:#d6e7ee;"
                "border:1px solid #2f4652;"
                "border-radius:8px;"
                "padding:24px;"
                "}"
            )
            layout.addWidget(placeholder)
            return

        from PySide6.QtWebEngineCore import QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.view = QWebEngineView()
        from ui.web_memory import InactiveWebPage
        self.memory_page = InactiveWebPage(self.view, self._prepare_page_reload, self._page_discarded)
        configure_webengine_profile(self.view.page())
        self._input_filter = _MapInputFilter(self.view)
        self._input_filter.install_tree(self.view)
        self._zoom_guard_timer = QTimer(self)
        self._zoom_guard_timer.setInterval(180)
        self._zoom_guard_timer.setTimerType(Qt.CoarseTimer)
        self._zoom_guard_timer.timeout.connect(lambda: _enforce_map_view_zoom(self.view))
        self._zoom_guard_timer.start()
        layout.addWidget(self.view)
        self.bridge = MapBridge()
        self.bridge.routeEvent.connect(self.route_event.emit)
        self.bridge.mapCommand.connect(self.map_command.emit)
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("mapBridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        # WebEngine Settings (wichtig für Tiles!)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.AllowRunningInsecureContent, True)
        self.view.loadFinished.connect(self.on_loaded)

        path = _resource_path("ui", "web", "map.html")
        self.view.setUrl(QUrl.fromLocalFile(path))

    def closeEvent(self, event):
        if self.memory_page is not None:
            self.memory_page.close()
        if self._zoom_guard_timer is not None:
            self._zoom_guard_timer.stop()
        if self._input_filter is not None:
            self._input_filter.disable()
        super().closeEvent(event)

    def on_loaded(self, success=True):
        self.loaded = bool(success)
        if self.loaded:
            if self._input_filter is not None:
                self._input_filter.install_tree(self.view)
            self._set_page_active(self.isVisible())
            self.set_detached(self._detached)
            if self._map_source is not None:
                self.set_map_source(self._map_source)
            if self.memory_page is not None:
                self.memory_page.loaded()
            if self._last_route_json:
                self._run_map_script(f"updateRoute({self._last_route_json});", "route")
            if self._last_ship is not None:
                lat, lon, hdg, _center, sog, cog, rot, _token, scale = self._last_ship
                self._run_map_script(f"updateShip({lat},{lon},{hdg},false,{sog},{cog},{rot},false,{scale});", "ship")
            if self._last_ais_json:
                self._run_map_script(f"updateAIS({self._last_ais_json});", "ais")
            if self._last_context_mode is not None:
                self._run_map_script(f"setContextMode({json.dumps(self._last_context_mode)});", "context")
            if self._last_ais_edit_mode is not None:
                self._run_map_script(f"setAISEditMode({str(self._last_ais_edit_mode).lower()});", "ais_edit_mode")

    def _prepare_page_reload(self):
        self.loaded = False
        self._last_ais_emit = 0

    def _page_discarded(self):
        self.loaded = False
        self._pending_scripts.clear()

    def set_map_source(self, source):
        self._map_source = source
        if self.loaded and self.view is not None:
            self._run_map_script(f"setMapSource({json.dumps(source)});", "map_source")

    def fit_chart_bounds(self, bounds):
        if self.loaded and self.view is not None:
            self._run_map_script(f"fitChartBounds({json.dumps(bounds)});", "chart_view")

    def begin_offline_area_selection(self, downloaded_areas=()):
        if self.loaded and self.view is not None:
            from maritime_simulator.visual.offline_areas import LARGE_AREA_TILES, MAX_TILES
            options = json.dumps({
                "maxTiles": MAX_TILES,
                "warningTiles": LARGE_AREA_TILES,
                "downloadedAreas": list(downloaded_areas),
            })
            self._run_map_script(f"beginOfflineAreaSelection({options});", "offline_selection")

    def set_detached(self, detached):
        self._detached = bool(detached)
        if self.loaded and self.view is not None:
            self._run_map_script(
                f"setMapDetached({str(self._detached).lower()});",
                "window_mode",
            )

    def showEvent(self, event):
        super().showEvent(event)
        self._set_page_active(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._set_page_active(False)

    def _set_page_active(self, active):
        self._page_active = bool(active)
        if self.view is None:
            return
        if self._zoom_guard_timer is not None:
            if active:
                self._zoom_guard_timer.start()
            else:
                self._zoom_guard_timer.stop()
        if not active:
            if self.memory_page is not None:
                self.memory_page.hide(self._freeze_page_if_inactive)
            else:
                QTimer.singleShot(0, self._freeze_page_if_inactive)
            return
        if self.memory_page is not None:
            self.memory_page.show()
        self._apply_page_lifecycle(True)
        if self.loaded:
            self._flush_pending_scripts()

    def _freeze_page_if_inactive(self):
        if not self._page_active:
            self._apply_page_lifecycle(False)

    def _apply_page_lifecycle(self, active):
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage
            if hasattr(self.view.page(), "setVisible"):
                self.view.page().setVisible(active)

            state = (
                QWebEnginePage.LifecycleState.Active
                if active
                else QWebEnginePage.LifecycleState.Frozen
            )
            self.view.page().setLifecycleState(state)
        except (AttributeError, RuntimeError):
            pass

    def update_ship(self, lat, lon, heading, center=False, sog=0.0, cog=None, rot=0.0, history_reset_token=0, time_scale=1.0):
        if cog is None:
            cog = heading
        reset_history = self._last_history_reset_token != history_reset_token
        self._last_history_reset_token = history_reset_token
        state = (
            round(lat, 6),
            round(lon, 6),
            round(heading, 1),
            bool(center),
            round(sog, 1),
            round(cog, 1),
            round(rot, 2),
            history_reset_token,
            round(float(time_scale or 1.0), 3),
        )
        if state == self._last_ship:
            return
        now = time.monotonic()
        if (
            self._page_active
            and not center
            and not reset_history
            and now - self._last_ship_emit < self._ship_emit_interval
        ):
            return
        self._last_ship = state
        if self._page_active:
            self._last_ship_emit = now
        if self.view is None or not self.loaded:
            return

        js = (
            f"updateShip({lat}, {lon}, {heading}, {str(center).lower()}, "
            f"{sog}, {cog}, {rot}, {str(reset_history).lower()}, {float(time_scale or 1.0)});"
        )
        self._run_map_script(js, "ship")

    def ais_update_due(self, now=None, *, force=False):
        if self.view is not None and not self._page_active:
            return False
        if force:
            return True
        timestamp = time.monotonic() if now is None else float(now)
        return timestamp - self._last_ais_emit >= self._ais_emit_min_interval

    def update_ais(self, targets, *, force=False):
        if not self.loaded:
            return False

        now = time.monotonic()
        if not self.ais_update_due(now, force=force):
            return False
        js_array = json.dumps(targets)
        if js_array == self._last_ais_json and now - self._last_ais_emit < 1.0:
            return False
        self._last_ais_json = js_array
        self._last_ais_emit = now
        if self.view is None:
            return True
        js = f"updateAIS({js_array});"
        self._run_map_script(js, "ais", payload_size=len(js_array), item_count=len(targets or []))
        return True

    def update_route(self, waypoints, wheel_over_marks=None, turn_arcs=None):
        if wheel_over_marks is None:
            wheel_over_marks = self._pending_wheel_over_marks
        if turn_arcs is None:
            turn_arcs = self._pending_turn_arcs
        route_json = json.dumps({
            "waypoints": waypoints,
            "wheelOver": wheel_over_marks or [],
            "turnArcs": turn_arcs or [],
        })
        if route_json == self._last_route_json:
            return
        self._last_route_json = route_json
        if self.view is None or not self.loaded:
            return
        js = f"updateRoute({route_json});"
        self._run_map_script(js, "route", payload_size=len(route_json))

    def set_route_geometry(self, wheel_over_marks, turn_arcs):
        self._pending_wheel_over_marks = list(wheel_over_marks or [])
        self._pending_turn_arcs = list(turn_arcs or [])

    def poll_route_events(self):
        if not self.loaded or self.view is None or not self._page_active:
            return
        self.view.page().runJavaScript("consumeRouteEvents();", self._handle_route_events)

    def set_context_mode(self, mode):
        if mode == self._last_context_mode:
            return
        self._last_context_mode = mode
        if self.view is None or not self.loaded:
            return
        self._run_map_script(f"setContextMode({json.dumps(mode)});", "context")

    def set_ais_edit_mode(self, active):
        active = bool(active)
        if active == self._last_ais_edit_mode:
            return
        self._last_ais_edit_mode = active
        if self.view is None or not self.loaded:
            return
        self._run_map_script(
            f"setAISEditMode({str(active).lower()});",
            "ais_edit_mode",
        )

    def _run_map_script(self, script, kind, payload_size=0, item_count=None):
        if not self._page_active:
            self._pending_scripts[kind] = (script, payload_size, item_count)
            return
        self._dispatch_map_script(script, kind, payload_size, item_count)

    def _flush_pending_scripts(self):
        pending = self._pending_scripts
        self._pending_scripts = {}
        for kind in ("context", "ais_edit_mode", "route", "ship", "ais"):
            payload = pending.pop(kind, None)
            if payload is not None:
                self._dispatch_map_script(payload[0], kind, payload[1], payload[2])
        for kind, payload in pending.items():
            self._dispatch_map_script(payload[0], kind, payload[1], payload[2])

    def _dispatch_map_script(self, script, kind, payload_size=0, item_count=None):
        monitor = self.performance_monitor
        started = time.perf_counter() if monitor and monitor.enabled else None
        self.view.page().runJavaScript(script)
        if monitor and monitor.enabled and started is not None:
            monitor.record_duration(f"map.{kind}.dispatch", time.perf_counter() - started)
            monitor.increment(f"map.{kind}.updates")
            if payload_size:
                monitor.set_gauge(f"map.{kind}.payload_bytes", payload_size)
            if item_count is not None:
                monitor.set_gauge(f"map.{kind}.items", item_count)

    def _handle_route_events(self, events):
        if not events:
            return
        for event in events:
            if isinstance(event, dict):
                self.route_event.emit(event)
