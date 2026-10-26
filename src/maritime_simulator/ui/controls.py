# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import math
import re

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QPolygonF, QValidator
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from ui.widgets.slider import ResetSlider


def format_coordinate(value: float, *, latitude: bool) -> str:
    """Format decimal degrees as the common marine degrees/minutes notation."""
    maximum = 90.0 if latitude else 180.0
    bounded = max(-maximum, min(maximum, float(value)))
    hemisphere = ("N" if bounded >= 0.0 else "S") if latitude else ("E" if bounded >= 0.0 else "W")
    absolute = abs(bounded)
    degrees = int(absolute)
    minutes = (absolute - degrees) * 60.0
    width = 2 if latitude else 3
    return f"{degrees:0{width}d}° {minutes:06.3f}' {hemisphere}"


def parse_coordinate(text: str, *, latitude: bool) -> float:
    """Accept degrees/minutes with hemisphere and signed decimal-degree input."""
    cleaned = text.strip().upper().replace(",", ".")
    maximum = 90.0 if latitude else 180.0
    try:
        decimal = float(cleaned)
    except ValueError:
        match = re.fullmatch(
            r"\s*(\d{1,3})(?:\s*[°D]\s*|\s+)(\d{1,2}(?:\.\d+)?)\s*['M]?\s*([NSEW])\s*",
            cleaned,
        )
        if match is None:
            raise ValueError("Use DD° MM.mmm' N/S or DDD° MM.mmm' E/W")
        degrees = float(match.group(1))
        minutes = float(match.group(2))
        hemisphere = match.group(3)
        expected = {"N", "S"} if latitude else {"E", "W"}
        if hemisphere not in expected or minutes >= 60.0:
            raise ValueError("Coordinate hemisphere or minutes are invalid")
        decimal = degrees + minutes / 60.0
        if hemisphere in {"S", "W"}:
            decimal = -decimal
    if not -maximum <= decimal <= maximum:
        raise ValueError(f"Coordinate must be within ±{maximum:g}°")
    return decimal


def format_controller_phase(value: str) -> str:
    """Present controller states as operator-facing labels."""
    normalized = str(value or "idle").strip().lower()
    labels = {
        "idle": "Idle",
        "straight_leg": "Straight Leg",
        "inbound_straight": "Inbound Straight",
        "planned_turn": "Planned Turn",
        "outbound_straight": "Outbound Straight",
        "route_edited": "Route Updated",
        "route_restart": "Route Restart",
        "completed": "Route Completed",
    }
    return labels.get(normalized, normalized.replace("_", " ").title())


class CoordinateSpinBox(QDoubleSpinBox):
    """Numeric coordinate editor with a nautical degrees/minutes presentation."""

    def __init__(self, *, latitude: bool, parent=None):
        super().__init__(parent)
        self.latitude = bool(latitude)
        maximum = 90.0 if latitude else 180.0
        self.setRange(-maximum, maximum)
        self.setDecimals(7)
        self.setKeyboardTracking(False)
        self.setMinimumWidth(154)
        self.setToolTip(
            "Enter latitude as DD° MM.mmm' N/S. Decimal degrees are also accepted."
            if latitude
            else "Enter longitude as DDD° MM.mmm' E/W. Decimal degrees are also accepted."
        )

    def textFromValue(self, value: float) -> str:
        return format_coordinate(value, latitude=self.latitude)

    def valueFromText(self, text: str) -> float:
        return parse_coordinate(text, latitude=self.latitude)

    def validate(self, text: str, position: int):
        if not text.strip():
            return QValidator.Intermediate, text, position
        try:
            parse_coordinate(text, latitude=self.latitude)
        except ValueError:
            if any(character in text.upper() for character in "NSEW°'"):
                return QValidator.Intermediate, text, position
            return QValidator.Invalid, text, position
        return QValidator.Acceptable, text, position


class CompassDial(QWidget):
    """Compact interactive 0..360 degree dial used by Sensor Control."""

    value_changed = Signal(float)
    reset_requested = Signal()

    def __init__(self, title: str, parent=None, *, compact: bool = False):
        super().__init__(parent)
        self.title = title
        self._compact = compact
        self._value = 0.0
        self._dragging = False
        self.setMinimumSize(132, 136 if compact else 150)
        self.setToolTip(f"Set {title} by dragging around the compass dial. Double-click to reset its coupling offset.")

    def value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        normalized = float(value) % 360.0
        if abs(normalized - self._value) < 0.01:
            return
        self._value = normalized
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._set_from_position(event.position())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._set_from_position(event.position())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.reset_requested.emit()

    def _set_from_position(self, position: QPointF) -> None:
        center, _radius = self._dial_geometry()
        dx = position.x() - center.x()
        dy = position.y() - center.y()
        self._value = math.degrees(math.atan2(dx, -dy)) % 360.0
        self.value_changed.emit(self._value)
        self.update()

    def _dial_geometry(self):
        if self._compact:
            available = max(1.0, self.height() - 54.0)
            return QPointF(self.width()/2.0, 24.0+available/2.0), min(self.width()*.40, available/2.0)
        return QPointF(self.width()/2.0, self.height()*.51), min(self.width()*.40, self.height()*.34)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        accent = QColor("#18b8dd")
        muted = QColor("#587583")
        text = QColor("#dbe8ee")
        center, radius = self._dial_geometry()

        painter.setPen(QPen(muted, 2.0))
        painter.drawEllipse(center, radius, radius)
        for degree in range(0, 360, 10):
            angle = math.radians(degree)
            outer = QPointF(center.x() + math.sin(angle) * radius, center.y() - math.cos(angle) * radius)
            length = 8.0 if degree % 30 == 0 else 4.0
            inner = QPointF(
                center.x() + math.sin(angle) * (radius - length),
                center.y() - math.cos(angle) * (radius - length),
            )
            painter.drawLine(inner, outer)

        angle = math.radians(self._value)
        tip = QPointF(center.x() + math.sin(angle) * (radius - 10), center.y() - math.cos(angle) * (radius - 10))
        painter.setPen(QPen(accent, 4.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(center, tip)
        painter.setBrush(accent)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, 5.0, 5.0)

        painter.setPen(text)
        painter.setFont(QFont(painter.font().family(), 9, QFont.DemiBold))
        painter.drawText(QRectF(0, 2, self.width(), 20), Qt.AlignCenter, self.title)
        painter.setFont(QFont(painter.font().family(), 11, QFont.Bold))
        painter.drawText(QRectF(0, self.height() - 28, self.width(), 24), Qt.AlignCenter, f"{self._value:05.1f}°")


class EngineTelegraphControl(QWidget):
    """Continuous telegraph order with selectable and visibly bracketed detents."""

    speed_selected = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps: tuple[tuple[str, float], ...] = ()
        self._buttons: list[QToolButton] = []
        self._selected_index = 0
        self._selected_speed = 0.0
        self._position_scale = 100
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self._labels_layout = QVBoxLayout()
        self._labels_layout.setSpacing(1)
        root.addLayout(self._labels_layout, 1)
        self.slider = ResetSlider(Qt.Vertical)
        self.slider.setMinimumWidth(28)
        self.slider.setToolTip("Move the engine telegraph. Full Ahead is at the top; Full Astern is at the bottom.")
        self.slider.valueChanged.connect(self._slider_changed)
        root.addWidget(self.slider)
        self.setMinimumHeight(248)

    def set_steps(self, steps: tuple[tuple[str, float], ...], selected_speed: float = 0.0) -> None:
        if steps != self._steps:
            while self._labels_layout.count():
                item = self._labels_layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            self._steps = tuple(steps)
            self._buttons = []
            for index, (label, speed) in enumerate(self._steps):
                button = QToolButton()
                button.setText(f"{label}  {speed:+.1f} kn")
                button.setToolButtonStyle(Qt.ToolButtonTextOnly)
                button.clicked.connect(lambda _checked=False, row=index: self.select_index(row, emit=True))
                self._labels_layout.addWidget(button)
                self._buttons.append(button)
            self.slider.setRange(0, max(0, len(self._steps) - 1) * self._position_scale)
            self.slider.setSingleStep(1)
            self.slider.setPageStep(self._position_scale)
            stop_index = next((index for index, (_label, speed) in enumerate(self._steps) if speed == 0.0), 0)
            self.slider.setProperty("sliderResetValue", stop_index * self._position_scale)
        self.set_speed(selected_speed)

    def set_derived(self, derived: bool) -> None:
        for widget in (self, self.slider, *self._buttons):
            if widget.property("derived") != bool(derived):
                widget.setProperty("derived", bool(derived))
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def set_speed(self, speed_kn: float) -> None:
        if not self._steps:
            return
        minimum = min(speed for _label, speed in self._steps)
        maximum = max(speed for _label, speed in self._steps)
        speed = max(minimum, min(maximum, float(speed_kn)))
        self._selected_speed = speed
        self._selected_index = min(range(len(self._steps)), key=lambda item: abs(self._steps[item][1] - speed))
        self.slider.blockSignals(True)
        self.slider.setValue(round(self._position_for_speed(speed)))
        self.slider.blockSignals(False)
        self._update_detent_highlights(speed)

    def select_index(self, index: int, *, emit: bool) -> None:
        if not self._steps:
            return
        index = max(0, min(len(self._steps) - 1, int(index)))
        self._selected_index = index
        self.set_speed(float(self._steps[index][1]))
        if emit:
            self.speed_selected.emit(float(self._steps[index][1]))

    def step(self, direction: int) -> None:
        """Move one detent; positive means toward Full Ahead."""
        if not self.isEnabled() or not self._steps:
            return
        ordered = sorted(enumerate(self._steps), key=lambda item: item[1][1])
        nearest_position = min(
            range(len(ordered)),
            key=lambda position: abs(ordered[position][1][1] - self._selected_speed),
        )
        target = max(0, min(len(ordered) - 1, nearest_position + int(direction)))
        self.select_index(ordered[target][0], emit=True)



    # Small read-only compatibility surface for existing UI probes.
    def count(self) -> int:
        return len(self._steps)

    def itemText(self, index: int) -> str:
        label, speed = self._steps[index]
        return f"{label}  {speed:+.1f} kn"

    def itemData(self, index: int) -> float:
        return float(self._steps[index][1])

    def currentData(self) -> float | None:
        return None if not self._steps else float(self._selected_speed)

    def _slider_changed(self, value: int) -> None:
        if not self._steps:
            return
        self._selected_speed = self._speed_for_position(float(value))
        self._selected_index = min(
            range(len(self._steps)),
            key=lambda item: abs(self._steps[item][1] - self._selected_speed),
        )
        self._update_detent_highlights(self._selected_speed)
        self.speed_selected.emit(self._selected_speed)

    def _position_for_speed(self, speed: float) -> float:
        """Map non-linear telegraph speeds onto evenly spaced detent positions."""
        if len(self._steps) < 2:
            return 0.0
        values = [float(value) for _label, value in self._steps]
        for index, (upper, lower) in enumerate(zip(values, values[1:])):
            high = max(upper, lower)
            low = min(upper, lower)
            if low - 1e-9 <= speed <= high + 1e-9:
                upper_position = (len(values) - 1 - index) * self._position_scale
                lower_position = upper_position - self._position_scale
                if abs(upper - lower) <= 1e-9:
                    return float(upper_position)
                fraction = (speed - lower) / (upper - lower)
                return lower_position + fraction * self._position_scale
        return float((len(values) - 1) * self._position_scale if speed >= values[0] else 0)

    def _speed_for_position(self, position: float) -> float:
        if len(self._steps) < 2:
            return float(self._steps[0][1]) if self._steps else 0.0
        maximum = (len(self._steps) - 1) * self._position_scale
        bounded = max(0.0, min(float(maximum), position))
        lower_index = min(len(self._steps) - 2, int((maximum - bounded) // self._position_scale))
        upper_position = (len(self._steps) - 1 - lower_index) * self._position_scale
        fraction = (upper_position - bounded) / self._position_scale
        upper_speed = float(self._steps[lower_index][1])
        lower_speed = float(self._steps[lower_index + 1][1])
        return upper_speed + (lower_speed - upper_speed) * fraction

    def _update_detent_highlights(self, speed: float) -> None:
        if not self._steps:
            return
        exact = [index for index, (_label, value) in enumerate(self._steps) if abs(value - speed) < 0.051]
        highlighted = set(exact)
        if not highlighted:
            lower = [(value, index) for index, (_label, value) in enumerate(self._steps) if value < speed]
            upper = [(value, index) for index, (_label, value) in enumerate(self._steps) if value > speed]
            if lower:
                highlighted.add(max(lower)[1])
            if upper:
                highlighted.add(min(upper)[1])
        for index, button in enumerate(self._buttons):
            font = button.font()
            font.setBold(index in highlighted)
            button.setFont(font)
            button.setProperty("telegraphBracket", index in highlighted)
            button.style().unpolish(button)
            button.style().polish(button)
