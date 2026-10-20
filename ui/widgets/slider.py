# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSlider


class ResetSlider(QSlider):
    """Double-click for neutral, or an explicitly configured reset value."""

    def showEvent(self, event):
        if self.property("sliderResetValue") is None and not self.minimum() <= 0 <= self.maximum():
            self.setProperty("sliderResetValue", self.value())
        super().showEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton or not self.isEnabled():
            super().mouseDoubleClickEvent(event)
            return
        reset = self.property("sliderResetValue")
        reset = max(self.minimum(), min(self.maximum(), int(reset or 0)))
        if self.sliderPosition() != reset:
            # Use normal drag signals, including sliders that commit on release.
            self.setSliderDown(True)
            self.setSliderPosition(reset)
            self.setSliderDown(False)
        event.accept()
