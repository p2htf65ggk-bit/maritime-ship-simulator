# SPDX-License-Identifier: MPL-2.0
import math
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QInputDevice


class NativePinchZoom:
    """Translate Qt's Wayland scale deltas into incremental logarithmic zoom."""

    def __init__(self, *, wayland=None):
        self.wayland = (sys.platform.startswith("linux")
                        and QGuiApplication.platformName().startswith("wayland")) if wayland is None else wayland
        self.reset()

    def reset(self):
        self.active = False
        self._scale = 1.0
        self.scale_ratio = 1.0

    def update(self, value):
        self.scale_ratio = 1.0
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if not math.isfinite(value):
            return 0.0
        if not self.wayland:
            return value
        # Qt Wayland forwards current_scale - previous_scale, with each
        # gesture starting at 1. Adding this directly to zoom biases zoom-out.
        scale = self._scale + value
        if not math.isfinite(scale) or scale <= 0:
            return 0.0
        ratio = scale / self._scale
        if not math.isfinite(ratio) or ratio <= 0:
            return 0.0
        self._scale = scale
        self.scale_ratio = ratio
        return math.log2(ratio)

    def handle(self, event):
        gesture = event.gestureType()
        if gesture == Qt.NativeGestureType.BeginNativeGesture:
            self.reset()
            if self.wayland and event.fingerCount() in (0, 2):
                self.active = True
                return True, None
        elif gesture == Qt.NativeGestureType.EndNativeGesture:
            handled = self.wayland and self.active
            self.reset()
            return handled, None
        elif gesture == Qt.NativeGestureType.ZoomNativeGesture:
            self.active = True
            return True, self.update(event.value())
        elif self.wayland and self.active and gesture in (
                Qt.NativeGestureType.PanNativeGesture, Qt.NativeGestureType.RotateNativeGesture):
            # A pinch may include these events; do not start a second browser
            # gesture while the simulator owns its stable-centre zoom.
            return True, None
        return False, None


def trackpad_pan_pixels(event):
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
    if (not is_touchpad and not has_scroll_phase and pixel_delta.x() == 0
            and scroll_kind(event) != "pan"):
        return None
    return -int(pixel_delta.x()), -int(pixel_delta.y())



def _cocoa_precise_scroll(event):
    try:
        return event.source() == Qt.MouseEventSource.MouseEventSynthesizedBySystem
    except (AttributeError, RuntimeError):
        return False



def scroll_kind(event):
    pixel, angle = event.pixelDelta(), event.angleDelta()
    phase = event.phase()
    try:
        source = event.source()
    except (AttributeError, RuntimeError):
        source = None
    if sys.platform == "darwin":
        # Cocoa can reuse a device marked TouchPad for a later physical wheel.
        # Trust per-event precision/phase before that shared device descriptor.
        if phase == Qt.ScrollPhase.NoScrollPhase:
            return "pan" if pixel.x() else "zoom"
        if source == Qt.MouseEventSource.MouseEventNotSynthesized:
            return "zoom"
    elif sys.platform.startswith("linux"):
        # Wayland's seat can share a device descriptor. Its per-event axis
        # source distinguishes a wheel (no pixels) from finger scrolling.
        if QGuiApplication.platformName().startswith("wayland"):
            if source == Qt.MouseEventSource.MouseEventNotSynthesized and pixel.isNull():
                return "zoom"
            if source == Qt.MouseEventSource.MouseEventSynthesizedBySystem:
                return "pan"
    try:
        if event.device().type() == QInputDevice.DeviceType.TouchPad:
            return "pan"
    except (AttributeError, RuntimeError):
        pass
    if sys.platform.startswith("win"):
        # Precision touchpad drivers can report a generic mouse with pixel
        # deltas and no scroll phase. A vertical swipe is still a pan.
        if angle.y() and abs(angle.y()) % 120 == 0:
            return "zoom"
        if not pixel.isNull() or angle.x():
            return "pan"
    elif sys.platform.startswith("linux") and phase == Qt.ScrollPhase.NoScrollPhase:
        # X11 mouse-driver pixel deltas are not reliable touchpad evidence.
        return "zoom"
    if pixel.isNull():
        return "zoom"
    if phase == Qt.ScrollPhase.NoScrollPhase:
        return "pan" if pixel.x() else "zoom"
    if angle.y() and abs(angle.y()) % 120 == 0:
        return "zoom"
    return "pan"



def mouse_wheel_delta(event):
    """Keep physical wheel steps out of browser trackpad heuristics."""
    if event.modifiers() & Qt.KeyboardModifier.ControlModifier or scroll_kind(event) != "zoom":
        return None
    angle = event.angleDelta()
    if angle.y():
        return -float(angle.y())
    pixel = event.pixelDelta()
    return -float(pixel.y()) if pixel.y() else None
