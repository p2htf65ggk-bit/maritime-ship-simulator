# SPDX-License-Identifier: MPL-2.0
import os
import math

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QInputDevice
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import SetControlMode, SetExternalRudderInputType
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.ui.forward_view import _ForwardViewGestureMixin
from maritime_simulator.ui.mode_panels import ModeControlStack, CompactModeControls
from ui.style import apply_global_ui_style
from ui.web_input import NativePinchZoom, mouse_wheel_delta
from ui import web_input
from ui.widgets import map_widget
from ui.widgets.map_widget import _MapInputFilter, _map_scroll_kind, _map_wheel_zoom_steps, _trackpad_pan_pixels
from ui.widgets.slider import ResetSlider
from ui.windows_scroll import WindowsScrollSource


class Wheel:
    def __init__(self, y=8, *, pixel=False, touchpad=False, phase=False, ctrl=False,
                 angle=None, source=None, device=None, x=0, stamp=42):
        self.y, self.pixel, self.touchpad, self.phase_active, self.ctrl = y, pixel, touchpad, phase, ctrl
        self.angle, self._source, self._device = angle, source, device
        self.x = x
        self.stamp = stamp
        self.accepted = False

    def type(self): return QEvent.Type.Wheel
    def pixelDelta(self): return QPoint(self.x, self.y) if self.pixel else QPoint()
    def angleDelta(self): return QPoint(0 if self.pixel else self.x, self.angle if self.angle is not None else (0 if self.pixel else self.y))
    def modifiers(self): return Qt.ControlModifier if self.ctrl else Qt.NoModifier
    def device(self): return self._device or WheelDevice(self.touchpad)
    def phase(self):
        if isinstance(self.phase_active, Qt.ScrollPhase): return self.phase_active
        return Qt.ScrollUpdate if self.phase_active else Qt.NoScrollPhase
    def source(self):
        if self._source is not None: return self._source
        return Qt.MouseEventSynthesizedBySystem if self.pixel and self.phase_active else Qt.MouseEventNotSynthesized
    def accept(self): self.accepted = True
    def timestamp(self): return self.stamp

class WheelDevice:
    def __init__(self, touchpad): self.touchpad = touchpad
    def type(self): return QInputDevice.DeviceType.TouchPad if self.touchpad else QInputDevice.DeviceType.Mouse
    def systemId(self): return 1 if self.touchpad else 2


class NativeGesture:
    def __init__(self, gesture, value=0, fingers=2):
        self.gesture, self.amount, self.fingers = gesture, value, fingers
        self.accepted = False
    def type(self): return QEvent.Type.NativeGesture
    def gestureType(self): return self.gesture
    def value(self): return self.amount
    def fingerCount(self): return self.fingers
    def accept(self): self.accepted = True


@pytest.mark.parametrize("scales", [
    [.9, .75, .5], [1.25, 1.5, 2], [1.5, 2, 1, .75, .5],
    [1 - i / 10000 for i in range(1, 501)],
    [1 + i / 10000 for i in range(1, 501)],
])
def test_wayland_native_pinch_uses_absolute_scale_and_keeps_small_updates(scales):
    pinch = NativePinchZoom(wayland=True)
    assert pinch.handle(NativeGesture(Qt.BeginNativeGesture)) == (True, None)
    previous, total, product = 1.0, 0.0, 1.0
    for scale in scales:
        handled, steps = pinch.handle(NativeGesture(Qt.ZoomNativeGesture, scale - previous))
        assert handled
        assert steps == pytest.approx(math.log2(scale / previous), abs=1e-14)
        assert steps != 0
        total += steps
        product *= pinch.scale_ratio
        previous = scale
    assert total == pytest.approx(math.log2(scales[-1]), abs=1e-12)
    assert product == pytest.approx(scales[-1])
    assert pinch.handle(NativeGesture(Qt.EndNativeGesture)) == (True, None)
    assert not pinch.active
    assert pinch.update(-.5) == pytest.approx(-1)


@pytest.mark.parametrize("value", [None, "invalid", float("nan"), float("inf"), -float("inf"), -1, -2])
def test_wayland_native_pinch_ignores_invalid_scale_without_poisoning_next_update(value):
    pinch = NativePinchZoom(wayland=True)
    assert pinch.update(value) == 0
    assert pinch.update(-.5) == pytest.approx(-1)


def test_non_wayland_native_pinch_keeps_existing_cocoa_values():
    pinch = NativePinchZoom(wayland=False)
    assert pinch.handle(NativeGesture(Qt.BeginNativeGesture)) == (False, None)
    for value in [.01, -.01, .5, -.5]:
        assert pinch.handle(NativeGesture(Qt.ZoomNativeGesture, value)) == (True, value)
    assert pinch.handle(NativeGesture(Qt.EndNativeGesture)) == (False, None)


def test_wayland_pinch_owns_interleaved_pan_rotation_but_not_three_finger_swipe():
    pinch = NativePinchZoom(wayland=True)
    assert pinch.handle(NativeGesture(Qt.BeginNativeGesture, fingers=3)) == (False, None)
    assert pinch.handle(NativeGesture(Qt.PanNativeGesture, fingers=3)) == (False, None)
    assert pinch.handle(NativeGesture(Qt.EndNativeGesture, fingers=3)) == (False, None)
    pinch.handle(NativeGesture(Qt.BeginNativeGesture))
    assert pinch.handle(NativeGesture(Qt.PanNativeGesture)) == (True, None)
    assert pinch.handle(NativeGesture(Qt.RotateNativeGesture)) == (True, None)
    pinch.handle(NativeGesture(Qt.EndNativeGesture))
    assert pinch.handle(NativeGesture(Qt.PanNativeGesture)) == (False, None)


def test_wayland_pinch_routes_outward_first_resets_and_suppresses_duplicate_wheel(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(web_input.sys, "platform", "linux")
    monkeypatch.setattr(web_input.QGuiApplication, "platformName", lambda: "wayland")

    class View(QWidget, _ForwardViewGestureMixin):
        def __init__(self):
            super().__init__()
            self.scripts = []
        def page(self): return self
        def runJavaScript(self, script): self.scripts.append(script)

    view = View()
    router = _MapInputFilter(view)
    try:
        for handle, function in [(lambda event: router.eventFilter(view, event), "zoomMapByNativeGesture"),
                                 (view._handle_native_camera_input, "applyNativeCameraScale")]:
            view.scripts.clear()
            for event in [NativeGesture(Qt.BeginNativeGesture), NativeGesture(Qt.ZoomNativeGesture, -.5),
                          Wheel(-120, ctrl=True), NativeGesture(Qt.PanNativeGesture),
                          NativeGesture(Qt.EndNativeGesture), NativeGesture(Qt.BeginNativeGesture),
                          NativeGesture(Qt.ZoomNativeGesture, -.5), NativeGesture(Qt.EndNativeGesture)]:
                assert handle(event) and event.accepted
            assert len(view.scripts) == 2
            assert all(function in script for script in view.scripts)
            argument = "-1.00000000" if function == "zoomMapByNativeGesture" else "0.5"
            assert all(f"{function}({argument})" in script for script in view.scripts)
    finally:
        router.disable()
        view.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("platform,backend,event,expected", [
    ("linux", "wayland", Wheel(120, touchpad=True), "zoom"),
    ("linux", "wayland-egl", Wheel(-15, touchpad=True), "zoom"),
    ("linux", "wayland", Wheel(10, pixel=True, phase=True), "pan"),
    ("linux", "wayland", Wheel(2, pixel=True, source=Qt.MouseEventSynthesizedBySystem), "pan"),
    ("linux", "xcb", Wheel(120), "zoom"),
    ("linux", "xcb", Wheel(3, pixel=True, x=1, angle=15), "zoom"),
    ("linux", "xcb", Wheel(8, touchpad=True), "pan"),
    ("linux", "xcb", Wheel(8, pixel=True, touchpad=True), "pan"),
    ("win32", "windows", Wheel(120), "zoom"),
    ("win32", "windows", Wheel(2, pixel=True, angle=120), "zoom"),
    ("win32", "windows", Wheel(8, pixel=True, angle=24), "pan"),
    ("win32", "windows", Wheel(8, pixel=True, x=4, angle=24), "pan"),
    ("win32", "windows", Wheel(120, touchpad=True), "pan"),
    ("win32", "windows", Wheel(0, x=16), "pan"),
])
def test_2d_scroll_platform_profiles(monkeypatch, platform, backend, event, expected):
    monkeypatch.setattr(map_widget.sys, "platform", platform)
    monkeypatch.setattr(web_input.QGuiApplication, "platformName", lambda: backend)
    assert _map_scroll_kind(event) == expected


def test_windows_unphased_vertical_touchpad_preserves_pixel_pan_distance(monkeypatch):
    monkeypatch.setattr(map_widget.sys, "platform", "win32")
    event = Wheel(18, pixel=True, angle=54)
    assert _map_scroll_kind(event) == "pan"
    assert _trackpad_pan_pixels(event) == (0, -18)


def test_wayland_unphased_continuous_pan_preserves_pixels(monkeypatch):
    monkeypatch.setattr(map_widget.sys, "platform", "linux")
    monkeypatch.setattr(web_input.QGuiApplication, "platformName", lambda: "wayland")
    event = Wheel(18, pixel=True, angle=216, source=Qt.MouseEventSynthesizedBySystem)
    assert _map_scroll_kind(event) == "pan"
    assert _trackpad_pan_pixels(event) == (0, -18)


class NativeFilterHost:
    def __init__(self): self.installed = []
    def installNativeEventFilter(self, observer): self.installed.append(observer)
    def removeNativeEventFilter(self, observer): self.installed.remove(observer)


@pytest.mark.parametrize("device,kind", [(0x02, "zoom"), (0x10, "pan"), (0x00, None)])
def test_windows_native_source_retains_identity_lost_by_qt(device, kind):
    host = NativeFilterHost()
    observer = WindowsScrollSource(host, lambda _address: (0x020A, 42, 120), lambda: device)
    try:
        assert observer.nativeEventFilter(b"windows_generic_MSG", 1) == (False, 0)
        assert observer.kind_for(Wheel(120, stamp=41)) is None
        assert observer.kind_for(Wheel(60)) is None
        assert observer.kind_for(Wheel(120)) == kind
        assert observer.kind_for(Wheel(120)) is None
    finally:
        observer.close()
    assert not host.installed
    observer.close()


def test_windows_native_scroll_records_are_bounded_expire_and_ignore_unrelated_input(monkeypatch):
    import ui.windows_scroll as windows_scroll
    now = [10.0]
    monkeypatch.setattr(windows_scroll.time, "monotonic", lambda: now[0])
    host = NativeFilterHost()
    message = [0x020E, 42, -120]
    observer = WindowsScrollSource(host, lambda _address: tuple(message), lambda: 0x10)
    try:
        assert observer.nativeEventFilter(b"other", 1) == (False, 0)
        assert not observer._pending
        message[0] = 0x0200
        observer.nativeEventFilter(b"windows_generic_MSG", 1)
        assert not observer._pending
        message[0] = 0x020E
        for _ in range(1000):
            observer.nativeEventFilter(b"windows_generic_MSG", 1)
        assert len(observer._pending) == 64
        assert observer.kind_for(Wheel(0, x=120)) == "pan"
        now[0] += .3
        assert observer.kind_for(Wheel(0, x=120)) is None
        assert not observer._pending
    finally:
        observer.close()


def test_windows_native_source_failure_falls_back_without_eating_event():
    def failed(): raise OSError("Input source unavailable")
    host = NativeFilterHost()
    observer = WindowsScrollSource(host, lambda _address: (0x020A, 42, 120), failed)
    try:
        assert observer.nativeEventFilter(b"windows_generic_MSG", 1) == (False, 0)
        assert observer.kind_for(Wheel(120)) is None
    finally:
        observer.close()


def test_windows_native_legacy_touchpad_wheel_and_mouse_get_different_actions():
    app = QApplication.instance() or QApplication([])
    host = NativeFilterHost()
    source = [0x10]
    observer = WindowsScrollSource(host, lambda _address: (0x020A, 42, 120), lambda: source[0])
    view = QWidget()
    scripts = []
    view.page = lambda: view
    view.runJavaScript = scripts.append
    router = _MapInputFilter(view)
    router._windows_scroll = observer
    try:
        for device in (0x10, 0x02):
            source[0] = device
            observer.nativeEventFilter(b"windows_generic_MSG", 1)
            assert router.eventFilter(view, Wheel(120))
        assert "panMapByTrackpad(0.0, -15.0)" in scripts[0]
        assert "zoomMapByNativeGesture(1.00000000)" in scripts[1]
    finally:
        router.disable()
        assert not host.installed
        view.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("platform,backend,device", [
    ("linux", "wayland", WheelDevice(True)),
    ("linux", "xcb", WheelDevice(False)),
    ("win32", "windows", WheelDevice(False)),
])
def test_2d_platform_wheel_pan_and_pinch_are_routed_once(monkeypatch, platform, backend, device):
    app = QApplication.instance() or QApplication([])
    view = QWidget()
    scripts = []
    view.page = lambda: view
    view.runJavaScript = scripts.append
    router = _MapInputFilter(view)
    monkeypatch.setattr(map_widget.sys, "platform", platform)
    monkeypatch.setattr(web_input.QGuiApplication, "platformName", lambda: backend)
    try:
        wheel = Wheel(120, device=device)
        pad = Wheel(18, pixel=True, angle=54, touchpad=platform != "win32",
                    source=Qt.MouseEventSynthesizedBySystem)
        pinch = Wheel(18, pixel=True, ctrl=True)
        for event in (wheel, pad, wheel, pinch):
            assert router.eventFilter(view, event) and event.accepted
        assert len(scripts) == 4
        assert "zoomMapByNativeGesture(1.00000000)" in scripts[0]
        assert "panMapByTrackpad(0, -18)" in scripts[1]
        assert "zoomMapByNativeGesture(1.00000000)" in scripts[2]
        assert "zoomMapByNativeGesture(0.15000000)" in scripts[3]
    finally:
        router.disable()
        view.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("y,pixel", [(1, False), (8, False), (120, False), (-120, False)])
def test_mouse_wheel_is_explicit_zoom_even_for_small_steps(y, pixel):
    assert mouse_wheel_delta(Wheel(y, pixel=pixel)) == -y


@pytest.mark.parametrize("platform,backend,expected", [
    ("darwin", "cocoa", -3), ("linux", "xcb", -3), ("win32", "windows", None),
])
def test_pixel_only_scroll_without_native_source_uses_platform_policy(monkeypatch, platform, backend, expected):
    monkeypatch.setattr(web_input.sys, "platform", platform)
    monkeypatch.setattr(web_input.QGuiApplication, "platformName", lambda: backend)
    assert mouse_wheel_delta(Wheel(3, pixel=True)) == expected


@pytest.mark.parametrize("kwargs", [{"touchpad": True, "phase": True, "source": Qt.MouseEventSynthesizedBySystem}, {"pixel": True, "phase": True}, {"ctrl": True}])
def test_native_wheel_classifier_leaves_trackpad_and_pinch_unchanged(kwargs):
    assert mouse_wheel_delta(Wheel(**kwargs)) is None


@pytest.mark.parametrize("pixels", [(0, 2), (0, 60), (20, 0), (20, 30), (0, -60)])
def test_3d_wayland_trackpad_pans_from_first_event_in_both_axes(monkeypatch, pixels):
    monkeypatch.setattr(map_widget.sys, "platform", "linux")
    monkeypatch.setattr(web_input.QGuiApplication, "platformName", lambda: "wayland")
    app = QApplication.instance() or QApplication([])

    class View(QWidget, _ForwardViewGestureMixin):
        def __init__(self):
            super().__init__()
            self.scripts = []
        def page(self): return self
        def runJavaScript(self, script): self.scripts.append(script)

    view = View()
    try:
        x, y = pixels
        pad = Wheel(y, x=x, pixel=True, angle=y * 12, phase=True,
                    source=Qt.MouseEventSynthesizedBySystem)
        assert view._handle_native_camera_input(pad) and pad.accepted
        assert view.scripts == [f"if (window.applyNativeCameraPan) window.applyNativeCameraPan({-x}, {-y});"]
        wheel = Wheel(120, touchpad=True)
        assert view._handle_native_camera_input(wheel) and wheel.accepted
        assert "applyMouseWheelZoom(-120.00000000)" in view.scripts[-1]
    finally:
        view.deleteLater()
        app.processEvents()


def test_2d_and_3d_filters_consume_mouse_wheel_once_without_panning():
    app = QApplication.instance() or QApplication([])

    class View(QWidget, _ForwardViewGestureMixin):
        def __init__(self):
            super().__init__()
            self.scripts = []
        def page(self): return self
        def runJavaScript(self, script): self.scripts.append(script)

    view = View()
    input_filter = _MapInputFilter(view)
    try:
        event = Wheel()
        assert input_filter.eventFilter(view, event) and event.accepted
        assert len(view.scripts) == 1 and "zoomMapByNativeGesture(0.06666667)" in view.scripts[0]
        view.scripts.clear()
        event = Wheel()
        assert view._handle_native_camera_input(event) and event.accepted
        assert view.scripts == ["if (window.applyMouseWheelZoom) window.applyMouseWheelZoom(-8.00000000);"]
        for kwargs in ({"pixel": True, "touchpad": True, "phase": True}, {"pixel": True, "phase": True}):
            input_filter._scroll_kind = None
            assert input_filter.eventFilter(view, Wheel(**kwargs))
            assert "panMapByTrackpad" in view.scripts[-1]
            assert view._handle_native_camera_input(Wheel(**kwargs))
            assert "applyNativeCameraPan" in view.scripts[-1]
        assert len(view.scripts) == 5
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def test_2d_scroll_session_keeps_wheel_zoom_when_scroll_phase_changes():
    app = QApplication.instance() or QApplication([])

    class View(QWidget):
        def __init__(self):
            super().__init__()
            self.scripts = []
        def page(self): return self
        def runJavaScript(self, script): self.scripts.append(script)

    view = View()
    input_filter = _MapInputFilter(view)
    try:
        for event in (Wheel(120), Wheel(8, pixel=True, phase=True), Wheel(3, pixel=True, phase=True)):
            assert input_filter.eventFilter(view, event) and event.accepted
        assert len(view.scripts) == 3
        assert all("zoomMapByNativeGesture" in script for script in view.scripts)
        pad = Wheel(18, pixel=True, touchpad=True, phase=True)
        assert input_filter.eventFilter(view, pad)
        assert "panMapByTrackpad" in view.scripts[-1]
    finally:
        view.deleteLater()
        app.processEvents()


def test_cocoa_shared_device_does_not_turn_wheel_into_trackpad(monkeypatch):
    monkeypatch.setattr(map_widget.sys, "platform", "darwin")
    device = WheelDevice(True)
    pad = Wheel(0, pixel=True, phase=True, angle=1, device=device)
    wheel = Wheel(2, pixel=True, phase=True, angle=120, device=device,
                  source=Qt.MouseEventNotSynthesized)
    assert _map_scroll_kind(pad) == "pan"
    assert _trackpad_pan_pixels(pad) == (0, -.5)
    assert _map_scroll_kind(wheel) == "zoom"
    assert _map_wheel_zoom_steps(wheel, mouse_wheel=True) == 1
    assert _map_wheel_zoom_steps(wheel) == pytest.approx(2 / 120)
    smoothed_wheel = Wheel(3, pixel=True, phase=False, angle=6, device=device,
                           source=Qt.MouseEventSynthesizedBySystem)
    assert _map_scroll_kind(smoothed_wheel) == "zoom"


def test_cocoa_switches_between_mouse_and_pad_on_same_device_without_delay(monkeypatch):
    monkeypatch.setattr(map_widget.sys, "platform", "darwin")
    app = QApplication.instance() or QApplication([])

    class View(QWidget):
        def __init__(self):
            super().__init__()
            self.scripts = []
        def page(self): return self
        def runJavaScript(self, script): self.scripts.append(script)

    device = WheelDevice(True)
    view = View()
    router = _MapInputFilter(view)
    try:
        events = [
            Wheel(0, pixel=True, phase=Qt.ScrollBegin, angle=1, device=device),
            Wheel(2, pixel=True, phase=True, angle=120, device=device, source=Qt.MouseEventNotSynthesized),
            Wheel(0, pixel=True, phase=Qt.ScrollBegin, angle=1, device=device),
        ]
        for event in events:
            assert router.eventFilter(view, event) and event.accepted
        assert "panMapByTrackpad(0.0, -0.5)" in view.scripts[0]
        assert "zoomMapByNativeGesture(1.00000000)" in view.scripts[1]
        assert "panMapByTrackpad(0.0, -0.5)" in view.scripts[2]
    finally:
        view.deleteLater()
        app.processEvents()


def test_global_slider_reset_uses_drag_signals_and_respects_disabled_controls():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    host = QWidget()
    layout = QVBoxLayout(host)
    slider = ResetSlider(Qt.Horizontal)
    slider.setRange(-100, 100)
    slider.setTracking(False)
    slider.setValue(30)
    layout.addWidget(slider)
    positive = ResetSlider(Qt.Horizontal)
    positive.setRange(1, 600)
    positive.setValue(170)
    layout.addWidget(positive)
    host.show()
    app.processEvents()
    moves, releases = [], []
    slider.sliderMoved.connect(moves.append)
    slider.sliderReleased.connect(lambda: releases.append(slider.value()))
    try:
        QTest.mouseDClick(slider, Qt.LeftButton)
        assert slider.value() == 0 and moves == [0] and len(releases) == 1
        positive.setValue(400)
        QTest.mouseDClick(positive, Qt.LeftButton)
        assert positive.value() == 170
        slider.setValue(45)
        slider.setEnabled(False)
        QTest.mouseDClick(slider, Qt.LeftButton)
        assert slider.value() == 45
    finally:
        host.close()
        host.deleteLater()
        app.processEvents()


def test_rudder_and_engine_double_click_reset_reaches_the_model():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    service = SimulatorService()
    stack = ModeControlStack(service)
    compact = CompactModeControls(service)
    stack.command_submitted.connect(service.submit)
    compact.command_submitted.connect(service.submit)
    stack.resize(400, 1000)
    stack.show()
    compact.show()
    try:
        for mode in (ControlMode.SHIP, ControlMode.EXTERNAL_RUDDER):
            service.submit(SetControlMode(mode))
            service.submit(SetExternalRudderInputType("MANUAL"))
            service.advance_wall_time(.1)
            stack.set_mode(mode)
            stack.update_snapshot(service.snapshot())
            slider = stack._ship_rudder if mode is ControlMode.SHIP else stack._external_manual_rudder
            slider.setValue(-150)
            service.advance_wall_time(.1)
            assert service.snapshot().rudder_order_starboard_deg == -15
            QTest.mouseDClick(slider, Qt.LeftButton)
            service.advance_wall_time(.1)
            assert service.snapshot().rudder_order_starboard_deg == 0
        compact.update_snapshot(service.snapshot())
        compact.rudder.slider.setValue(180)
        QTest.mouseDClick(compact.rudder.slider, Qt.LeftButton)
        service.advance_wall_time(.1)
        assert service.snapshot().rudder_order_starboard_deg == 0
        telegraph = stack._engine_fields[ControlMode.EXTERNAL_RUDDER]
        telegraph.select_index(0, emit=True)
        service.advance_wall_time(.1)
        assert service.snapshot().speed_order_kn > 0
        QTest.mouseDClick(telegraph.slider, Qt.LeftButton)
        service.advance_wall_time(.1)
        assert service.snapshot().speed_order_kn == 0
    finally:
        stack.close()
        compact.close()
        stack.deleteLater()
        compact.deleteLater()
        service.close()
        app.processEvents()
