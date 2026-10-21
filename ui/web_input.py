# SPDX-License-Identifier: MPL-2.0
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QInputDevice


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
