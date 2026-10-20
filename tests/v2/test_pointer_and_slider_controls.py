# SPDX-License-Identifier: MPL-2.0
import os

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
from ui.web_input import mouse_wheel_delta
from ui.widgets import map_widget
from ui.widgets.map_widget import _MapInputFilter, _map_scroll_kind, _map_wheel_zoom_steps, _trackpad_pan_pixels
from ui.widgets.slider import ResetSlider


class Wheel:
    def __init__(self, y=8, *, pixel=False, touchpad=False, phase=False, ctrl=False,
                 angle=None, source=None, device=None):
        self.y, self.pixel, self.touchpad, self.phase_active, self.ctrl = y, pixel, touchpad, phase, ctrl
        self.angle, self._source, self._device = angle, source, device
        self.accepted = False

    def type(self): return QEvent.Type.Wheel
    def pixelDelta(self): return QPoint(0, self.y if self.pixel else 0)
    def angleDelta(self): return QPoint(0, self.angle if self.angle is not None else (0 if self.pixel else self.y))
    def modifiers(self): return Qt.ControlModifier if self.ctrl else Qt.NoModifier
    def device(self): return self._device or WheelDevice(self.touchpad)
    def phase(self):
        if isinstance(self.phase_active, Qt.ScrollPhase): return self.phase_active
        return Qt.ScrollUpdate if self.phase_active else Qt.NoScrollPhase
    def source(self):
        if self._source is not None: return self._source
        return Qt.MouseEventSynthesizedBySystem if self.pixel and self.phase_active else Qt.MouseEventNotSynthesized
    def accept(self): self.accepted = True

class WheelDevice:
    def __init__(self, touchpad): self.touchpad = touchpad
    def type(self): return QInputDevice.DeviceType.TouchPad if self.touchpad else QInputDevice.DeviceType.Mouse
    def systemId(self): return 1 if self.touchpad else 2


@pytest.mark.parametrize("y,pixel", [(1, False), (8, False), (120, False), (-120, False), (3, True)])
def test_mouse_wheel_is_explicit_zoom_even_for_small_steps(y, pixel):
    assert mouse_wheel_delta(Wheel(y, pixel=pixel)) == -y


@pytest.mark.parametrize("kwargs", [{"touchpad": True}, {"pixel": True, "phase": True}, {"ctrl": True}])
def test_native_wheel_classifier_leaves_trackpad_and_pinch_unchanged(kwargs):
    assert mouse_wheel_delta(Wheel(**kwargs)) is None


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
            assert not view._handle_native_camera_input(Wheel(**kwargs))
        assert len(view.scripts) == 3
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
