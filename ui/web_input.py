# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputDevice


def mouse_wheel_delta(event):
    """Keep physical wheel steps out of browser trackpad heuristics."""
    if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
        return None
    try:
        if event.device().type() == QInputDevice.DeviceType.TouchPad:
            return None
        if event.phase() != Qt.ScrollPhase.NoScrollPhase:
            return None
    except (AttributeError, RuntimeError):
        return None
    pixel = event.pixelDelta()
    if not pixel.isNull():
        if pixel.x():
            return None
        return -float(pixel.y()) if pixel.y() else None
    angle = event.angleDelta()
    return -float(angle.y()) if angle.y() else None
