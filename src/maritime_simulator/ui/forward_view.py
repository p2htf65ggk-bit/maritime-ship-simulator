# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, QUrlQuery, Signal, Slot
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from maritime_simulator.application.paths import bundled_resource_path
from maritime_simulator.application.system_resources import SystemMemoryStatus, cached_memory_status
from maritime_simulator.domain.state import SimulationSnapshot
from maritime_simulator.visual import (
    AdaptiveVisualQualityController,
    OverpassGeodataProvider,
    VISUAL_QUALITY_MODES,
    VisualSettings,
    VisualWorldManager,
    normalize_visual_quality_mode,
)
from ui.webengine_profile import configure_webengine_profile
from ui.web_input import NativePinchZoom, mouse_wheel_delta, scroll_kind, trackpad_pan_pixels
from ui.windows_scroll import WindowsScrollSource

from .dialogs import OpaqueDialog


def _headless_rendering() -> bool:
    platform = os.environ.get("QT_QPA_PLATFORM", "").lower()
    return (
        os.environ.get("SHIP_SIMULATOR_HEADLESS_MAP") == "1"
        or platform in {"offscreen", "minimal"}
    )


GOOGLE_MAP_TILES_SETUP_URL = "https://developers.google.com/maps/documentation/tile/get-api-key"
GOOGLE_MAPS_JS_SETUP_URL = "https://developers.google.com/maps/documentation/javascript/get-api-key"
VISUAL_QUALITY_LABELS = {
    "auto": "Auto (recommended)",
    "quality": "Quality",
    "balanced": "Balanced",
    "low_memory": "Low Memory",
    "critical": "Critical memory",
}
VISUAL_RENDERER_MODES = ("auto", "google_maps_3d", "photorealistic_tiles")
VISUAL_RENDERER_LABELS = {
    "auto": "Auto (recommended)",
    "google_maps_3d": "Google 3D Maps (EEA compatible)",
    "photorealistic_tiles": "Photorealistic Tiles / Cesium",
}
RENDERER_PROBE_TIMEOUT_MS = 8_000


def normalize_visual_renderer_mode(value: object) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in VISUAL_RENDERER_MODES else "auto"


def _stored_visual_settings(settings_path: Path | None) -> dict:
    if settings_path is None:
        return {}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _google_tiles_api_key(settings_path: Path | None = None) -> str:
    stored = str(_stored_visual_settings(settings_path).get("google_tiles_api_key", "")).strip()
    if stored:
        return stored
    return os.environ.get("MARITIME_SIM_GOOGLE_3D_TILES_API_KEY", "").strip()


def visual_settings_payload(settings_path: Path | None = None) -> dict:
    """Return the effective portable visual settings, including the Google key."""
    stored = _stored_visual_settings(settings_path)
    return {
        "google_tiles_api_key": _google_tiles_api_key(settings_path),
        "visual_quality_mode": normalize_visual_quality_mode(
            stored.get("visual_quality_mode", "auto")
        ),
        "renderer_mode": normalize_visual_renderer_mode(
            stored.get("renderer_mode", "auto")
        ),
    }


def auto_renderer_from_response(status_code: int | None, response_body: str) -> tuple[str, str]:
    """Resolve Auto without hiding authentication or regional failures."""
    message = str(response_body or "").lower()
    if status_code == 200:
        return "photorealistic_tiles", "direct-tiles-available"
    if status_code == 403 and (
        "account and region" in message
        or "not available for your account" in message
        or "eea" in message
    ):
        return "google_maps_3d", "region-restricted"
    return "google_maps_3d", "direct-tiles-unavailable"


def _visual_payload_signature(payload: dict) -> str:
    """Serialize rendered state while ignoring clock-only snapshot churn."""
    comparable = dict(payload)
    comparable.pop("revision", None)
    comparable.pop("simulation_time_s", None)
    environment = dict(comparable.get("environment") or {})
    environment.pop("time_of_day_s", None)
    comparable["environment"] = environment
    return json.dumps(comparable, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _enforce_web_view_zoom(view) -> None:
    """Keep native browser zoom from scaling the embedded operator interface."""
    if (
        view is not None
        and hasattr(view, "zoomFactor")
        and hasattr(view, "setZoomFactor")
        and abs(float(view.zoomFactor()) - 1.0) > 1e-6
    ):
        view.setZoomFactor(1.0)


def _wheel_zoom_steps(event) -> float:
    """Normalize a native Ctrl-wheel/pinch event to camera zoom steps."""
    pixel_delta = event.pixelDelta()
    if not pixel_delta.isNull():
        return max(-4.0, min(4.0, float(pixel_delta.y()) / 120.0))
    angle_delta = event.angleDelta()
    return max(-4.0, min(4.0, float(angle_delta.y()) / 120.0))


class _ForwardViewGestureMixin:
    """Keep native pinch zoom local to the 3D camera, never the Qt web page."""

    def _apply_native_camera_zoom(self, steps: float) -> None:
        if abs(float(steps)) < 1e-6:
            return
        script = (
            "if (window.applyNativeCameraZoom) "
            f"window.applyNativeCameraZoom({float(steps):.8f});"
        )
        try:
            self.page().runJavaScript(script)
        except RuntimeError:
            # A final gesture can arrive while the WebEngine page is shutting down.
            return

    def _handle_native_camera_input(self, event) -> bool:
        if event.type() == QEvent.Type.NativeGesture:
            try:
                pinch = getattr(self, "_native_pinch_zoom", None)
                if pinch is None:
                    pinch = self._native_pinch_zoom = NativePinchZoom()
                handled, steps = pinch.handle(event)
            except AttributeError:
                handled, steps = False, None
            if handled:
                self._last_native_pinch_at = time.monotonic()
                if steps is not None and abs(steps) >= 1e-9:
                    if pinch.wayland:
                        try:
                            self.page().runJavaScript(
                                "if (window.applyNativeCameraScale) "
                                f"window.applyNativeCameraScale({pinch.scale_ratio:.12g});"
                            )
                        except RuntimeError:
                            return False
                    else:
                        self._apply_native_camera_zoom(steps)
                event.accept()
                return True
        if (
            event.type() == QEvent.Type.Wheel
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            pinch = getattr(self, "_native_pinch_zoom", None)
            if pinch is not None and pinch.wayland and (pinch.active
                    or time.monotonic() - getattr(self, "_last_native_pinch_at", 0) < .16):
                event.accept()
                return True
            self._apply_native_camera_zoom(_wheel_zoom_steps(event))
            event.accept()
            return True
        if event.type() == QEvent.Type.Wheel:
            observer = getattr(self, "_native_scroll_source", None)
            kind = observer.kind_for(event) if observer is not None else None
            kind = kind or scroll_kind(event)
            if kind == "pan" or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                pan = trackpad_pan_pixels(event)
                if pan is None:
                    angle = event.angleDelta()
                    pan = (-angle.x() / 8.0, -angle.y() / 8.0)
                if not any(pan):
                    event.accept()
                    return True
                try:
                    self.page().runJavaScript(
                        "if (window.applyNativeCameraPan) "
                        f"window.applyNativeCameraPan({pan[0]}, {pan[1]});"
                    )
                except RuntimeError:
                    return False
                event.accept()
                return True
            delta = mouse_wheel_delta(event)
            if delta is None and kind == "zoom":
                angle = event.angleDelta()
                delta = -float(angle.y() or event.pixelDelta().y())
            if delta is not None:
                try:
                    self.page().runJavaScript(
                        "if (window.applyMouseWheelZoom) "
                        f"window.applyMouseWheelZoom({delta:.8f});"
                    )
                except RuntimeError:
                    return False
                event.accept()
                return True
        return False

    def event(self, event) -> bool:
        if self._handle_native_camera_input(event):
            return True
        return super().event(event)

    def wheelEvent(self, event) -> None:
        if self._handle_native_camera_input(event):
            return
        super().wheelEvent(event)


class _ForwardViewInputFilter(QObject):
    """Intercept native pinch only inside one WebEngine view and its render widgets."""

    def __init__(self, view) -> None:
        super().__init__(view)
        self._view = view
        self._active = True
        view._native_scroll_source = WindowsScrollSource.for_view(view)

    def install_tree(self, root) -> None:
        if not self._active:
            return
        try:
            widgets = [root, *root.findChildren(QWidget)]
            for widget in widgets:
                widget.installEventFilter(self)
        except RuntimeError:
            return

    def disable(self) -> None:
        self._active = False
        if self._view is not None and self._view._native_scroll_source is not None:
            self._view._native_scroll_source.close()
        self._view = None

    def eventFilter(self, watched, event) -> bool:
        if not self._active or self._view is None:
            return False
        if event.type() == QEvent.Type.ChildAdded:
            child = event.child()
            if isinstance(child, QWidget):
                self.install_tree(child)
            return False
        try:
            return self._view._handle_native_camera_input(event)
        except RuntimeError:
            return False


class _VisualBridge(QObject):
    api_key_settings_requested = Signal()

    @Slot()
    def requestApiKeySettings(self) -> None:
        self.api_key_settings_requested.emit()


class _GoogleTilesSettingsDialog(OpaqueDialog):
    def __init__(
        self,
        api_key: str,
        renderer_mode: str,
        active_renderer: str,
        quality_mode: str,
        active_quality: str,
        memory: SystemMemoryStatus,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Google 3D View Settings")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.api_key = QLineEdit(api_key)
        self.api_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        form.addRow("API key", self.api_key)
        self.renderer_mode = QComboBox()
        for mode in VISUAL_RENDERER_MODES:
            self.renderer_mode.addItem(VISUAL_RENDERER_LABELS[mode], mode)
        renderer_index = self.renderer_mode.findData(
            normalize_visual_renderer_mode(renderer_mode)
        )
        self.renderer_mode.setCurrentIndex(max(0, renderer_index))
        self.renderer_mode.setToolTip(
            "Auto uses direct Photorealistic Tiles where available and Google 3D Maps "
            "when the direct service is unavailable for the account or region."
        )
        form.addRow("3D provider", self.renderer_mode)
        self.quality_mode = QComboBox()
        for mode in VISUAL_QUALITY_MODES:
            self.quality_mode.addItem(VISUAL_QUALITY_LABELS[mode], mode)
        selected = self.quality_mode.findData(normalize_visual_quality_mode(quality_mode))
        self.quality_mode.setCurrentIndex(max(0, selected))
        self.quality_mode.setToolTip(
            "Auto adapts rendering to available system memory. Cesium also adjusts its "
            "tile cache and detail; Google 3D Maps manages tile quality internally while "
            "the simulator limits overlay resolution, target count and refresh rate."
        )
        form.addRow("3D quality", self.quality_mode)
        layout.addLayout(form)
        link = QLabel(
            f'<a href="{GOOGLE_MAP_TILES_SETUP_URL}">Create or manage a Google Map Tiles API key</a>'
        )
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        layout.addWidget(link)
        js_link = QLabel(
            f'<a href="{GOOGLE_MAPS_JS_SETUP_URL}">Enable or configure the Maps JavaScript API</a>'
        )
        js_link.setOpenExternalLinks(True)
        js_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        layout.addWidget(js_link)
        note = QLabel(
            "The key is stored in the Maritime Ship Simulator user configuration, not in the project or release package. "
            f"Active provider: {VISUAL_RENDERER_LABELS.get(active_renderer, active_renderer)}. "
            "Saving Auto runs provider detection again."
        )
        note.setObjectName("secondaryText")
        note.setWordWrap(True)
        layout.addWidget(note)
        available = "unknown" if memory.available_mb is None else f"{memory.available_mb / 1024.0:.1f} GB"
        total = "unknown" if memory.total_mb is None else f"{memory.total_mb / 1024.0:.1f} GB"
        quality_status = QLabel(
            f"Detected memory: {available} available of {total}. Active profile: "
            f"{VISUAL_QUALITY_LABELS.get(active_quality, active_quality)}."
        )
        quality_status.setObjectName("secondaryText")
        quality_status.setWordWrap(True)
        layout.addWidget(quality_status)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ForwardViewDialog(OpaqueDialog):
    """Scalable optional renderer consuming read-only simulation and AIS snapshots."""

    fullscreen_requested = Signal()

    def __init__(
        self,
        snapshot_provider: Callable[[], SimulationSnapshot],
        ais_provider: Callable[[SimulationSnapshot], Iterable[dict]],
        cache_dir: Path,
        parent=None,
        profile_provider: Callable[[str], object] | None = None,
        settings_path: Path | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("forwardViewDialog")
        self.setWindowTitle("Maritime Ship Simulator V2.1 - 3D Navigation View")
        self.setMinimumSize(760, 480)
        self.resize(1280, 760)
        self.snapshot_provider = snapshot_provider
        self.ais_provider = ais_provider
        self.profile_provider = profile_provider
        self.settings = VisualSettings()
        self.settings_path = settings_path or cache_dir / "visual_settings_v2.json"
        stored_settings = _stored_visual_settings(self.settings_path)
        self.google_tiles_api_key = _google_tiles_api_key(self.settings_path)
        self.renderer_mode = normalize_visual_renderer_mode(
            stored_settings.get("renderer_mode", "auto")
        )
        self.active_renderer = normalize_visual_renderer_mode(
            stored_settings.get("_auto_renderer", "google_maps_3d")
        )
        if self.active_renderer == "auto":
            self.active_renderer = "google_maps_3d"
        self._auto_renderer_reason = str(
            stored_settings.get("_auto_renderer_reason", "")
        )
        self.visual_quality_mode = normalize_visual_quality_mode(
            stored_settings.get("visual_quality_mode", "auto")
        )
        self._quality_controller = AdaptiveVisualQualityController(self.visual_quality_mode)
        self._memory_status = cached_memory_status()
        initial_profile = self._quality_controller.update(self._memory_status, force=True)
        self._visual_quality_payload = initial_profile.payload(
            self.visual_quality_mode,
            self._memory_status,
        )
        self.manager = VisualWorldManager(OverpassGeodataProvider(cache_dir))
        self.view = None
        self.memory_page = None
        self.loaded = False
        self._closed = False
        self._page_active = True
        self._scripts_in_flight: set[str] = set()
        self._pending_scripts: dict[str, dict] = {}
        self._script_generation = 0
        self._last_visual_signature: str | None = None
        self._renderer_probe_reply = None
        self._renderer_probe_timer = None
        self._renderer_probe_generation = 0
        self._renderer_page_pending = False
        self._force_renderer_probe = False
        self.visual_updates_sent = 0
        self.visual_updates_skipped = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if _headless_rendering():
            placeholder = QLabel(
                "3D Navigation View\n\nWebGL rendering is disabled in headless/offscreen mode."
            )
            placeholder.setObjectName("HeadlessForwardViewPlaceholder")
            placeholder.setAlignment(Qt.AlignCenter)
            layout.addWidget(placeholder)
        else:
            self._create_web_view(layout)

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.setTimerType(Qt.CoarseTimer)
        self.timer.timeout.connect(self.refresh)
        self.quality_timer = QTimer(self)
        self.quality_timer.setInterval(10_000)
        self.quality_timer.setTimerType(Qt.VeryCoarseTimer)
        self.quality_timer.timeout.connect(self._refresh_visual_quality)
        self.zoom_guard_timer = QTimer(self)
        self.zoom_guard_timer.setInterval(50)
        self.zoom_guard_timer.setTimerType(Qt.CoarseTimer)
        self.zoom_guard_timer.timeout.connect(lambda: _enforce_web_view_zoom(self.view))
        self._fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        self._fullscreen_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._fullscreen_shortcut.activated.connect(self._toggle_fullscreen)
        self._leave_fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._leave_fullscreen_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._leave_fullscreen_shortcut.activated.connect(self._leave_fullscreen)

    def _create_web_view(self, layout: QVBoxLayout) -> None:
        from PySide6.QtWebEngineCore import QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebChannel import QWebChannel

        class ForwardViewWebEngineView(_ForwardViewGestureMixin, QWebEngineView):
            pass

        self.view = ForwardViewWebEngineView(self)
        from ui.web_memory import InactiveWebPage
        self.memory_page = InactiveWebPage(self.view, self._prepare_page_reload, self._page_discarded)
        self.view.native_input_filter = _ForwardViewInputFilter(self.view)
        self.view.native_input_filter.install_tree(self.view)
        configure_webengine_profile(self.view.page())
        self.view.setObjectName("forwardViewWebEngine")
        self.view.setZoomFactor(1.0)
        self.view.page().setBackgroundColor(QColor("#75bfdc"))
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)
        profile = self.view.page().profile()
        user_agent = profile.httpUserAgent()
        if "MaritimeShipSimulator" not in user_agent:
            profile.setHttpUserAgent(f"{user_agent} MaritimeShipSimulator/2.1")
        self.view.loadFinished.connect(self._loaded)
        self.bridge = _VisualBridge(self)
        self.bridge.api_key_settings_requested.connect(self._open_api_key_settings)
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("visualBridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        layout.addWidget(self.view)
        self._load_renderer_page()

    def _load_renderer_page(self) -> None:
        if self.view is None:
            return
        self.setWindowTitle("Maritime Ship Simulator V2.1 - 3D Navigation View")
        self.loaded = False
        self._last_visual_signature = None
        if self.renderer_mode == "auto":
            stored = _stored_visual_settings(self.settings_path)
            cached_renderer = normalize_visual_renderer_mode(
                stored.get("_auto_renderer", "auto")
            )
            cached_reason = str(stored.get("_auto_renderer_reason", ""))
            if (
                not self._force_renderer_probe
                and cached_renderer == "google_maps_3d"
                and cached_reason == "region-restricted"
            ):
                self._activate_renderer(cached_renderer, cached_reason)
                return
            self._force_renderer_probe = False
            self._probe_renderer()
            return
        self._activate_renderer(self.renderer_mode, "operator-selected")

    def _probe_renderer(self) -> None:
        if not self.google_tiles_api_key:
            self._activate_renderer("google_maps_3d", "api-key-missing")
            return
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

        self._renderer_probe_generation += 1
        generation = self._renderer_probe_generation
        self._cancel_renderer_probe()
        if not hasattr(self, "_renderer_network_manager"):
            self._renderer_network_manager = QNetworkAccessManager(self)
        url = QUrl("https://tile.googleapis.com/v1/3dtiles/root.json")
        query = QUrlQuery()
        query.addQueryItem("key", self.google_tiles_api_key)
        url.setQuery(query)
        request = QNetworkRequest(url)
        request.setRawHeader(b"User-Agent", b"MaritimeShipSimulator/2.1")
        self._renderer_page_pending = False
        self.setWindowTitle(
            "Maritime Ship Simulator V2.1 - 3D Navigation View - Checking provider"
        )
        reply = self._renderer_network_manager.get(request)
        self._renderer_probe_reply = reply
        timeout_timer = QTimer(reply)
        timeout_timer.setSingleShot(True)
        timeout_timer.setTimerType(Qt.CoarseTimer)
        self._renderer_probe_timer = timeout_timer

        def completed() -> None:
            timeout_timer.stop()
            if self._renderer_probe_timer is timeout_timer:
                self._renderer_probe_timer = None
            if self._renderer_probe_reply is reply:
                self._renderer_probe_reply = None
            if generation != self._renderer_probe_generation:
                reply.deleteLater()
                return
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            body = bytes(reply.readAll()).decode("utf-8", "replace")
            renderer, reason = auto_renderer_from_response(
                int(status) if status is not None else None,
                body,
            )
            reply.deleteLater()
            self._activate_renderer(renderer, reason)

        reply.finished.connect(completed)

        def timed_out() -> None:
            if (
                generation == self._renderer_probe_generation
                and self._renderer_probe_reply is reply
                and reply.isRunning()
            ):
                reply.abort()

        timeout_timer.timeout.connect(timed_out)
        timeout_timer.start(RENDERER_PROBE_TIMEOUT_MS)

    def _cancel_renderer_probe(self) -> None:
        timeout_timer = self._renderer_probe_timer
        self._renderer_probe_timer = None
        if timeout_timer is not None:
            timeout_timer.stop()
        reply = self._renderer_probe_reply
        self._renderer_probe_reply = None
        if reply is not None:
            reply.abort()
            reply.deleteLater()

    def _activate_renderer(self, renderer: str, reason: str) -> None:
        if self.memory_page is not None:
            self.memory_page.reset()
        self.setWindowTitle("Maritime Ship Simulator V2.1 - 3D Navigation View")
        self.active_renderer = (
            renderer if renderer in {"google_maps_3d", "photorealistic_tiles"}
            else "google_maps_3d"
        )
        self._auto_renderer_reason = str(reason)
        if self.renderer_mode == "auto":
            stored = _stored_visual_settings(self.settings_path)
            stored["_auto_renderer"] = self.active_renderer
            stored["_auto_renderer_reason"] = self._auto_renderer_reason
            self._write_visual_settings(stored)
        page_name = (
            "google_maps_3d.html"
            if self.active_renderer == "google_maps_3d"
            else "cesium.html"
        )
        self._renderer_page_pending = True
        self.view.setUrl(QUrl.fromLocalFile(str(
            bundled_resource_path("ui", "web", "forward_view", page_name)
        )))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._closed:
            self._set_page_active(True)
            self.timer.start()
            self.quality_timer.start()
            self.zoom_guard_timer.start()
            self._refresh_visual_quality()
            self.refresh()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        self.quality_timer.stop()
        self.zoom_guard_timer.stop()
        super().hideEvent(event)
        self._set_page_active(False)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.quality_timer.stop()
        self.zoom_guard_timer.stop()
        self._set_page_active(False)
        event.accept()

    def _set_page_active(self, active: bool) -> None:
        self._page_active = bool(active)
        for name in ("timer", "quality_timer", "zoom_guard_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.start() if active else timer.stop()
        if self.view is None:
            return
        if not active:
            if not hasattr(self.view, "isVisible"):
                self._apply_page_activity(False)
                return
            if self.memory_page is not None:
                self.memory_page.hide(self._freeze_if_inactive)
            else:
                QTimer.singleShot(0, lambda: self._freeze_if_inactive())
            return
        if self.memory_page is not None:
            self.memory_page.show()
        self._apply_page_activity(True)
        pending, self._pending_scripts = self._pending_scripts, {}
        for name, payload in pending.items():
            self._send_script(name, payload)
        if self.loaded and self.memory_page is not None:
            self.memory_page.loaded()

    def _prepare_page_reload(self):
        # Resume the same provider, without running Auto detection again.
        self._renderer_page_pending = True

    def _page_discarded(self):
        self.loaded = False
        self._script_generation += 1
        self._scripts_in_flight.clear()
        self._pending_scripts.clear()
        self._last_visual_signature = None

    def _freeze_if_inactive(self):
        if not self._page_active:
            self._apply_page_activity(False)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._set_page_active(not self.isMinimized() and self.isVisible())

    def _apply_page_activity(self, active):
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage
            page = self.view.page()
            if self.loaded and hasattr(page, "runJavaScript"):
                page.runJavaScript(f"if (window.setNavigationViewActive) window.setNavigationViewActive({str(active).lower()});")
            if hasattr(page, "setVisible"):
                page.setVisible(active)
            state = (
                QWebEnginePage.LifecycleState.Active
                if active
                else QWebEnginePage.LifecycleState.Frozen
            )
            self.view.page().setLifecycleState(state)
        except (AttributeError, RuntimeError):
            # Older Qt builds can keep using timer suspension as the fallback.
            pass

    def _loaded(self, success: bool) -> None:
        if not self._renderer_page_pending:
            return
        self._renderer_page_pending = False
        self.loaded = bool(success)
        self._script_generation += 1
        self._scripts_in_flight.clear()
        self._pending_scripts.clear()
        self._last_visual_signature = None
        if success:
            self.view.native_input_filter.install_tree(self.view)
            _enforce_web_view_zoom(self.view)
            self._send_script(
                "configureVisualRenderer",
                {
                    "google_tiles_api_key": self.google_tiles_api_key,
                    "renderer": self.active_renderer,
                    "renderer_mode": self.renderer_mode,
                    "renderer_reason": self._auto_renderer_reason,
                    "visual_quality": self._visual_quality_payload,
                },
            )
            if self.memory_page is not None:
                self.memory_page.loaded()
            if not self._page_active:
                self._set_page_active(False)
            QTimer.singleShot(250, self.refresh)

    def refresh(self) -> None:
        _enforce_web_view_zoom(self.view)
        if self._closed or not self._page_active or not self.loaded or self.view is None:
            return
        snapshot = self.snapshot_provider()
        targets = self.ais_provider(snapshot)
        profile = self.profile_provider(snapshot.vessel_profile_id) if self.profile_provider else None
        payload = self.manager.state_payload(snapshot, targets, self.settings, profile)
        signature = _visual_payload_signature(payload)
        if signature == self._last_visual_signature:
            self.visual_updates_skipped += 1
            return
        self._last_visual_signature = signature
        self.visual_updates_sent += 1
        self._send_script("updateVisualState", payload)

    def _open_api_key_settings(self) -> None:
        dialog = _GoogleTilesSettingsDialog(
            self.google_tiles_api_key,
            self.renderer_mode,
            self.active_renderer,
            self.visual_quality_mode,
            str(self._visual_quality_payload.get("name", "quality")),
            self._memory_status,
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.apply_settings({
            "google_tiles_api_key": dialog.api_key.text().strip(),
            "renderer_mode": dialog.renderer_mode.currentData(),
            "visual_quality_mode": dialog.quality_mode.currentData(),
            "force_renderer_probe": dialog.renderer_mode.currentData() == "auto",
        })

    def export_settings(self) -> dict:
        return {
            "google_tiles_api_key": self.google_tiles_api_key,
            "visual_quality_mode": self.visual_quality_mode,
            "renderer_mode": self.renderer_mode,
        }

    def apply_settings(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            raise ValueError("Visual settings must be an object")
        previous_key = self.google_tiles_api_key
        previous_renderer_mode = self.renderer_mode
        self.google_tiles_api_key = str(
            settings.get("google_tiles_api_key", self.google_tiles_api_key)
        ).strip()
        quality_mode = normalize_visual_quality_mode(
            settings.get("visual_quality_mode", self.visual_quality_mode)
        )
        self.renderer_mode = normalize_visual_renderer_mode(
            settings.get("renderer_mode", self.renderer_mode)
        )
        self._force_renderer_probe = bool(settings.get("force_renderer_probe", False))
        data = _stored_visual_settings(self.settings_path)
        data.update(self.export_settings())
        data["visual_quality_mode"] = quality_mode
        if self._force_renderer_probe:
            data.pop("_auto_renderer", None)
            data.pop("_auto_renderer_reason", None)
        self._write_visual_settings(data)
        if quality_mode != self.visual_quality_mode:
            self.visual_quality_mode = quality_mode
            self._quality_controller.set_mode(quality_mode)
            self._refresh_visual_quality(force=True)
        if (
            self.google_tiles_api_key != previous_key
            or self.renderer_mode != previous_renderer_mode
            or self._force_renderer_probe
        ):
            self.loaded = False
            self._last_visual_signature = None
            self._load_renderer_page()

    def _write_visual_settings(self, data: dict) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.settings_path)

    def _toggle_fullscreen(self) -> None:
        if not self.isWindow():
            self.fullscreen_requested.emit()
            return
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _leave_fullscreen(self) -> None:
        if self.isWindow() and self.isFullScreen():
            self.showNormal()

    def _refresh_visual_quality(self, *, force: bool = False, memory: SystemMemoryStatus | None = None) -> None:
        self._memory_status = memory if memory is not None else cached_memory_status()
        profile = self._quality_controller.update(self._memory_status, force=force)
        payload = profile.payload(self.visual_quality_mode, self._memory_status)
        if payload == self._visual_quality_payload:
            return
        self._visual_quality_payload = payload
        self._send_script("configureVisualQuality", payload)

    def _send_script(self, function_name: str, payload: dict) -> None:
        if not self.loaded or self.view is None:
            return
        if not self._page_active:
            self._pending_scripts[function_name] = payload
            return
        if function_name in self._scripts_in_flight:
            self._pending_scripts[function_name] = payload
            return
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        script = f"if (window.{function_name}) window.{function_name}({encoded});"
        self._scripts_in_flight.add(function_name)
        generation = self._script_generation

        def completed(_result, name=function_name) -> None:
            if generation != self._script_generation:
                return
            self._scripts_in_flight.discard(name)
            pending = self._pending_scripts.pop(name, None)
            if pending is not None and not self._closed:
                self._send_script(name, pending)

        self.view.page().runJavaScript(script, completed)

    def request_diagnostics(self, callback) -> None:
        if not self.loaded or self.view is None:
            callback(None)
            return

        def decoded(value) -> None:
            try:
                callback(json.loads(value) if isinstance(value, str) else value)
            except (TypeError, ValueError, json.JSONDecodeError):
                callback(None)

        self.view.page().runJavaScript(
            "window.getVisualDiagnostics ? JSON.stringify(window.getVisualDiagnostics()) : 'null';",
            decoded,
        )

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.memory_page is not None:
            self.memory_page.close()
        self.timer.stop()
        self.quality_timer.stop()
        self._script_generation += 1
        self._renderer_probe_generation += 1
        self._cancel_renderer_probe()
        self._scripts_in_flight.clear()
        self._pending_scripts.clear()
        self.manager.close()
        if self.view is not None:
            self.view.native_input_filter.disable()
            self.view.stop()
            page = self.view.page()
            if page is not None:
                page.setWebChannel(None)
            self.view.deleteLater()
            self.view = None
