# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import QEasingCurve, QSize, QVariantAnimation
from PySide6.QtWidgets import QFrame


class StatusStrip(QFrame):
    """Redistribute readouts continuously when the Track-only cell changes."""

    def __init__(self):
        super().__init__()
        self.cells = []
        self.track_cell = None
        self._track_visible = False
        self._fraction = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(180)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.animation.valueChanged.connect(self._animate)
        self.animation.finished.connect(self._layout_cells)

    def add_cell(self, cell, *, weight=1, track_only=False):
        cell.setParent(self)
        self.cells.append((cell, weight))
        if track_only:
            self.track_cell = cell
            cell.hide()
        self.updateGeometry()
        self._layout_cells()

    def minimumSizeHint(self):
        height = max((cell.minimumSizeHint().height() for cell, _ in self.cells), default=0)
        return QSize(0, height + 10)

    def sizeHint(self):
        width = sum(max(0, cell.minimumSizeHint().width()) for cell, _ in self.cells)
        return QSize(width + 16, self.minimumSizeHint().height())

    def set_track_visible(self, visible):
        if visible == self._track_visible:
            return
        self._track_visible = visible
        self.animation.stop()
        self.animation.setStartValue(self._fraction)
        self.animation.setEndValue(1.0 if visible else 0.0)
        self.animation.start()

    def _animate(self, value):
        self._fraction = float(value)
        self._layout_cells()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_cells()

    def _layout_cells(self):
        if not self.cells:
            return
        weights = [weight * (self._fraction if cell is self.track_cell else 1) for cell, weight in self.cells]
        minimums = [max(0, cell.sizeHint().width()) * (self._fraction if cell is self.track_cell else 1) for cell, _ in self.cells]
        available = max(0, self.width() - 16)
        total_minimum = sum(minimums)
        extra = max(0, available - total_minimum)
        x = 8.0
        for (cell, _), weight, minimum in zip(self.cells, weights, minimums):
            width = (minimum + extra * weight / max(1, sum(weights))) if total_minimum <= available else available * minimum / max(1, total_minimum)
            right = x + width
            cell.setGeometry(round(x), 5, max(0, round(right) - round(x)), max(0, self.height() - 10))
            if cell is self.track_cell:
                cell.setVisible(self._fraction > 0.001)
            x = right
