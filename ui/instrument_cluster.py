# SPDX-License-Identifier: MPL-2.0
import math
import time
from datetime import datetime

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, QSize
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from ui.style import with_tooltip_style


BG = QColor("#071018")
SURFACE = QColor("#111d26")
EDGE = QColor("#4b6471")
SCALE = QColor("#9fb5bf")
TEXT = QColor("#f4fbff")
MUTED = QColor("#c5d2d8")
ACCENT = QColor("#2f9fc3")
SECONDARY = QColor("#d8edf5")
MIN_INSTRUMENT_FONT_PT = 9


def instrument_font(point_size, weight=QFont.Normal):
    return QFont("Arial", max(MIN_INSTRUMENT_FONT_PT, int(point_size)), weight)


class FramedInstrument(QWidget):
    title = ""

    def _frame(self, painter):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(EDGE, 1))
        painter.setBrush(SURFACE)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 4, 4)
        painter.setPen(MUTED)
        painter.setFont(instrument_font(9, QFont.Bold))
        painter.drawText(10, 8, self.width() - 20, 18, Qt.AlignLeft | Qt.AlignVCenter, self.title)


class BowThrusterInstrument(FramedInstrument):
    title = "Bow Thruster"

    def __init__(self):
        super().__init__()
        self.order = self.response = 0.0
        self.efficiency = 1.0
        self.setMinimumSize(160, 160)

    def set_values(self, order, response, efficiency):
        self.order, self.response, self.efficiency = float(order), float(response), float(efficiency)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        bar = QRectF(24, self.height() / 2.0 - 5, self.width() - 48, 18)
        center = bar.center().x()
        actual_x = center + bar.width() / 2.0 * max(-100, min(100, self.response)) / 100.0
        order_x = center + bar.width() / 2.0 * max(-100, min(100, self.order)) / 100.0
        painter.setPen(QPen(EDGE, 1))
        painter.setBrush(QColor("#0b151e"))
        painter.drawRoundedRect(bar, 3, 3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(ACCENT if self.response >= 0 else SECONDARY)
        painter.drawRect(QRectF(min(center, actual_x), bar.top(), abs(actual_x - center), bar.height()))
        painter.setPen(QPen(SCALE, 1))
        painter.drawLine(QPointF(center, bar.top() - 4), QPointF(center, bar.bottom() + 4))
        painter.setFont(instrument_font(9, QFont.Bold))
        painter.setPen(MUTED)
        painter.drawText(QRectF(bar.left() - 5, bar.bottom() + 8, 55, 20), Qt.AlignLeft, "PORT")
        painter.drawText(QRectF(bar.right() - 50, bar.bottom() + 8, 55, 20), Qt.AlignRight, "STBD")
        painter.setPen(QPen(QColor("#ffcc66"), 1))
        painter.setBrush(QColor("#ffcc66"))
        painter.drawPolygon(QPolygonF([QPointF(order_x, bar.top() - 2),
                                      QPointF(order_x - 6, bar.top() - 13), QPointF(order_x + 6, bar.top() - 13)]))
        painter.setPen(TEXT)
        painter.setFont(instrument_font(15, QFont.Bold))
        painter.drawText(self.rect().adjusted(0, 0, 0, -7), Qt.AlignBottom | Qt.AlignCenter, f"{self.response:+.1f}%")


class HeadingWindInstrument(FramedInstrument):
    def __init__(self, title, wind=False):
        super().__init__()
        self.title = title
        self.wind = wind
        self.direction = 0.0
        self.speed = 0.0
        self.setMinimumSize(160, 160)

    def set_values(self, direction, speed=0.0):
        self.direction = float(direction) % 360.0
        self.speed = float(speed)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        center = QPointF(self.width() / 2, self.height() / 2 + 12)
        radius = min(self.width(), self.height()) * 0.34
        painter.setPen(QPen(QColor("#38505c"), 2))
        painter.setBrush(QColor("#0b151e"))
        painter.drawEllipse(center, radius, radius)
        for degree in range(0, 360, 15):
            angle = math.radians(degree - 90)
            major = degree % 45 == 0
            painter.setPen(QPen(SCALE if major else QColor("#58717d"), 2 if major else 1))
            length = 12 if major else 6
            painter.drawLine(
                QPointF(center.x() + math.cos(angle) * (radius - length), center.y() + math.sin(angle) * (radius - length)),
                QPointF(center.x() + math.cos(angle) * radius, center.y() + math.sin(angle) * radius),
            )
        angle = math.radians(self.direction - 90)
        if self.wind:
            tip = QPointF(center.x() + math.cos(angle) * 18, center.y() + math.sin(angle) * 18)
            left = QPointF(center.x() + math.cos(angle + 0.20) * (radius - 14), center.y() + math.sin(angle + 0.20) * (radius - 14))
            right = QPointF(center.x() + math.cos(angle - 0.20) * (radius - 14), center.y() + math.sin(angle - 0.20) * (radius - 14))
        else:
            tip = QPointF(center.x() + math.cos(angle) * (radius - 16), center.y() + math.sin(angle) * (radius - 16))
            left = QPointF(center.x() + math.cos(angle + 2.5) * 15, center.y() + math.sin(angle + 2.5) * 15)
            right = QPointF(center.x() + math.cos(angle - 2.5) * 15, center.y() + math.sin(angle - 2.5) * 15)
        painter.setPen(QPen(ACCENT, 2))
        painter.setBrush(ACCENT)
        painter.drawPolygon(QPolygonF([tip, left, center, right] if not self.wind else [tip, left, right]))
        painter.setPen(TEXT)
        painter.setFont(instrument_font(19, QFont.Bold))
        value = f"{self.direction:05.1f}°"
        if self.wind:
            value += f"\n{self.speed:04.1f} kn"
        painter.drawText(self.rect().adjusted(0, 36, 0, -12), Qt.AlignCenter, value)


class SpeedComboInstrument(FramedInstrument):
    title = "Speed"

    def __init__(self):
        super().__init__()
        self.sog = 0.0
        self.stw = 0.0
        self.setMinimumSize(170, 170)

    def set_values(self, sog, stw):
        self.sog = float(sog)
        self.stw = float(stw)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        rect = self.rect().adjusted(18, 42, -18, -34)
        painter.setPen(QPen(QColor("#405762"), 8, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(rect.left(), rect.center().y(), rect.right(), rect.center().y())
        for value in range(-10, 41, 10):
            x = self._x_for_speed(rect, value)
            painter.setPen(QPen(SCALE, 1))
            painter.drawLine(x, rect.center().y() - 18, x, rect.center().y() + 18)
            painter.setPen(MUTED)
            painter.setFont(instrument_font(8))
            painter.drawText(x - 18, rect.center().y() + 24, 36, 16, Qt.AlignCenter, str(value))
        self._draw_marker(painter, rect, self.sog, ACCENT, "SOG", -20)
        self._draw_marker(painter, rect, self.stw, SECONDARY, "STW", 28)

    def _x_for_speed(self, rect, speed):
        ratio = (max(-10.0, min(40.0, speed)) + 10.0) / 50.0
        return rect.left() + rect.width() * ratio

    def _draw_marker(self, painter, rect, speed, color, label, y_offset):
        x = self._x_for_speed(rect, speed)
        y = rect.center().y() + y_offset
        tip_y = y + (12 if y_offset < 0 else -12)
        painter.setPen(QPen(color, 2))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([
            QPointF(x, tip_y),
            QPointF(x - 8, y),
            QPointF(x + 8, y),
        ]))
        painter.setPen(TEXT)
        painter.setFont(instrument_font(10, QFont.Bold))
        text_y = y - 34 if y_offset < 0 else y + 14
        painter.drawText(int(x - 42), int(text_y), 84, 20, Qt.AlignCenter, f"{label} {speed:.1f}")


class RudderInstrument(FramedInstrument):
    title = "Rudder Angle"

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.order = 0.0
        self.setMinimumSize(170, 170)

    def set_value(self, value):
        self.set_values(value, self.order)

    def set_values(self, value, order=None):
        self.value = max(-45.0, min(45.0, float(value)))
        self.order = max(-45.0, min(45.0, float(self.value if order is None else order)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        pivot = QPointF(self.width() / 2, self.height() * 0.30)
        length = min(self.width() * 0.28, self.height() * 0.32)
        scale_radius = length + 14
        scale_path = QPainterPath()
        for index, value in enumerate(range(-45, 46, 3)):
            angle = math.radians(90.0 + value)
            point = QPointF(pivot.x() + math.cos(angle) * scale_radius, pivot.y() + math.sin(angle) * scale_radius)
            if index == 0:
                scale_path.moveTo(point)
            else:
                scale_path.lineTo(point)
        painter.setPen(QPen(QColor("#405762"), 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawPath(scale_path)
        for value in (-45, -30, -15, 0, 15, 30, 45):
            angle = math.radians(90.0 + value)
            outer = QPointF(pivot.x() + math.cos(angle) * scale_radius, pivot.y() + math.sin(angle) * scale_radius)
            inner = QPointF(pivot.x() + math.cos(angle) * (scale_radius - 12), pivot.y() + math.sin(angle) * (scale_radius - 12))
            painter.setPen(QPen(SCALE, 1))
            painter.drawLine(inner, outer)
        self._draw_order_marker(painter, pivot, scale_radius)
        angle = math.radians(90.0 - self.value)
        tip = QPointF(pivot.x() + math.cos(angle) * length, pivot.y() + math.sin(angle) * length)
        painter.setPen(QPen(ACCENT, 4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(pivot, tip)
        painter.setPen(QPen(QColor("#0b151e"), 1))
        painter.setBrush(ACCENT)
        painter.drawEllipse(pivot, 6, 6)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(13, QFont.Bold))
        painter.drawText(0, self.height() - 36, self.width(), 18, Qt.AlignCenter, f"ACT {self.value:+.1f}°")
        painter.setPen(MUTED)
        painter.setFont(instrument_font(9, QFont.Bold))
        painter.drawText(0, self.height() - 19, self.width(), 16, Qt.AlignCenter, f"ORD {self.order:+.1f}°")

    def _draw_order_marker(self, painter, pivot, scale_radius):
        angle = math.radians(90.0 - self.order)
        radial = QPointF(math.cos(angle), math.sin(angle))
        tangent = QPointF(-math.sin(angle), math.cos(angle))
        inner = QPointF(pivot.x() + radial.x() * (scale_radius + 2), pivot.y() + radial.y() * (scale_radius + 2))
        outer = QPointF(pivot.x() + radial.x() * (scale_radius + 18), pivot.y() + radial.y() * (scale_radius + 18))
        painter.setPen(QPen(QColor("#ffcc66"), 1))
        painter.setBrush(QColor("#ffcc66"))
        painter.drawPolygon(QPolygonF([
            inner,
            QPointF(outer.x() + tangent.x() * 7, outer.y() + tangent.y() * 7),
            QPointF(outer.x() - tangent.x() * 7, outer.y() - tangent.y() * 7),
        ]))


class EngineBarInstrument(FramedInstrument):
    title = "Engine Order"

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.minimum = -10.0
        self.maximum = 35.0
        self.steps = []
        self.setMinimumSize(170, 170)

    def set_value(self, value, minimum=-10.0, maximum=35.0):
        self.value = float(value)
        self.minimum = minimum
        self.maximum = maximum
        self.update()

    def set_steps(self, steps):
        self.steps = list(steps)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        rect = self.rect().adjusted(58, 38, -30, -30)
        painter.setPen(QPen(QColor("#405762"), 1))
        painter.setBrush(QColor("#0b151e"))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setFont(instrument_font(6, QFont.Bold))
        last_label_y = -999
        for label, speed in self.steps:
            y = self._y(rect, speed)
            painter.setPen(QPen(SCALE, 1))
            painter.drawLine(rect.left() - 7, y, rect.left(), y)
            if abs(y - last_label_y) < 15:
                continue
            last_label_y = y
            short = label.replace("Ahead", "A").replace("Astern", "S").replace("Dead Slow", "D/S").replace("Slow", "SL").replace("Half", "H").replace("Full", "F").replace("Stop", "STOP")
            painter.setPen(MUTED)
            painter.drawText(3, int(y - 7), rect.left() - 12, 14, Qt.AlignRight | Qt.AlignVCenter, short)
        zero_y = self._y(rect, 0.0)
        value_y = self._y(rect, self.value)
        fill_rect = rect.normalized()
        fill_rect.setTop(min(zero_y, value_y))
        fill_rect.setBottom(max(zero_y, value_y))
        painter.setBrush(ACCENT)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(fill_rect, 3, 3)
        painter.setPen(QPen(SCALE, 1))
        painter.drawLine(rect.left() - 8, zero_y, rect.right() + 8, zero_y)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(13, QFont.Bold))
        painter.drawText(self.rect().adjusted(0, 0, 0, -6), Qt.AlignBottom | Qt.AlignCenter, f"{self.value:+.1f} kn")

    def _y(self, rect, value):
        ratio = (max(self.minimum, min(self.maximum, value)) - self.minimum) / max(0.001, self.maximum - self.minimum)
        return rect.bottom() - rect.height() * ratio


class ROTDotInstrument(FramedInstrument):
    title = "Rate of Turn"
    RING_SPEED_FACTOR = 1.40

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.phase = 0.0
        self._last_phase_time = time.monotonic()
        self.setMinimumSize(180, 180)

    def set_value(self, value):
        self.value = max(-60.0, min(60.0, float(value)))
        now = time.monotonic()
        dt = min(2.0, max(0.0, now - self._last_phase_time))
        self._last_phase_time = now
        self.phase = (self.phase + self.value * self.RING_SPEED_FACTOR * dt) % 360.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        center = QPointF(self.width() / 2, self.height() / 2 + 10)
        radius = min(self.width(), self.height()) * 0.28
        painter.setPen(QPen(QColor("#405762"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, radius, radius)
        for index in range(7):
            angle = math.radians(self.phase + index * (360.0 / 7.0) - 90.0)
            dot = QPointF(center.x() + math.cos(angle) * radius, center.y() + math.sin(angle) * radius)
            color = QColor(ACCENT)
            painter.setBrush(color)
            painter.setPen(QPen(color, 1))
            painter.drawEllipse(dot, 9, 9)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(15, QFont.Bold))
        painter.drawText(self.rect().adjusted(0, 28, 0, -8), Qt.AlignCenter, f"{self.value:+.1f}°/min")


class TimeDateInstrument(FramedInstrument):
    title = "Time / Date"

    def __init__(self):
        super().__init__()
        self.now = datetime.now()
        self.setMinimumSize(170, 170)

    def set_now(self, now):
        self.now = now
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(24, QFont.Bold))
        painter.drawText(self.rect().adjusted(0, 28, 0, -34), Qt.AlignCenter, self.now.strftime("%H:%M:%S"))
        painter.setPen(MUTED)
        painter.setFont(instrument_font(12, QFont.Bold))
        painter.drawText(self.rect().adjusted(0, 78, 0, 0), Qt.AlignCenter, self.now.strftime("%d.%m.%Y"))


class DepthTraceInstrument(FramedInstrument):
    title = "Depth"

    def __init__(self):
        super().__init__()
        self.samples = []
        self.setMinimumSize(170, 170)

    def add_sample(self, depth):
        now = datetime.now().timestamp()
        self.samples.append((now, float(depth)))
        cutoff = now - 360.0
        self.samples = [(t, d) for t, d in self.samples if t >= cutoff]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        rect = self.rect().adjusted(18, 38, -18, -24)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(QPen(QColor("#304550"), 1))
        painter.setBrush(QColor("#0b151e"))
        painter.drawRect(rect)
        for i in range(1, 6):
            y = round(rect.top() + rect.height() * i / 6)
            painter.drawLine(rect.left(), y, rect.right(), y)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if len(self.samples) >= 2:
            now = datetime.now().timestamp()
            depths = [d for _, d in self.samples]
            low = max(0.0, min(depths) - 2.0)
            high = max(low + 1.0, max(depths) + 2.0)
            path = QPainterPath()
            for index, (t, depth) in enumerate(self.samples):
                x = rect.right() - rect.width() * max(0.0, min(1.0, (now - t) / 360.0))
                y = rect.top() + rect.height() * (depth - low) / (high - low)
                if index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(ACCENT, 2))
            painter.drawPath(path)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(13, QFont.Bold))
        current = self.samples[-1][1] if self.samples else 0.0
        painter.drawText(rect, Qt.AlignTop | Qt.AlignRight, f"{current:.1f} m")


class DataTile(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("instrumentTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignCenter)
        self.value = QLabel("--")
        self.value.setAlignment(Qt.AlignCenter)
        self.value.setStyleSheet("font-size: 24px; font-weight: bold; color: #ffffff;")
        layout.addWidget(self.title)
        layout.addWidget(self.value)

    def set_value(self, text):
        self.value.setText(text)


class PropulsionInstrument(FramedInstrument):
    title = "Propulsion"

    def __init__(self):
        super().__init__()
        self.lever = 0.0
        self.setMinimumSize(160, 160)

    def set_value(self, value):
        self.lever = max(-100.0, min(100.0, float(value)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        bar = self.rect().adjusted(74, 38, -74, -34)
        painter.setPen(QPen(QColor("#405762"), 1))
        painter.setBrush(QColor("#0b151e"))
        painter.drawRoundedRect(bar, 4, 4)
        zero_y = bar.center().y()
        value_y = self._y(bar, self.lever)
        fill = bar.normalized()
        fill.setTop(min(zero_y, value_y))
        fill.setBottom(max(zero_y, value_y))
        painter.setPen(Qt.NoPen)
        painter.setBrush(ACCENT if self.lever >= 0 else SECONDARY)
        painter.drawRoundedRect(fill, 3, 3)
        painter.setPen(QPen(SCALE, 1))
        painter.drawLine(bar.left() - 8, zero_y, bar.right() + 8, zero_y)
        painter.setFont(instrument_font(8, QFont.Bold))
        painter.setPen(MUTED)
        painter.drawText(10, bar.top() - 2, 58, 18, Qt.AlignRight, "AHEAD")
        painter.drawText(10, bar.bottom() - 14, 58, 18, Qt.AlignRight, "ASTERN")
        painter.setPen(TEXT)
        painter.setFont(instrument_font(15, QFont.Bold))
        painter.drawText(self.rect().adjusted(0, 0, 0, -7), Qt.AlignBottom | Qt.AlignCenter, f"{self.lever:+.1f}%")

    def _y(self, rect, value):
        ratio = (value + 100.0) / 200.0
        return rect.bottom() - rect.height() * ratio


class ThrusterInstrument(FramedInstrument):
    def __init__(self, title):
        super().__init__()
        self.title = title
        self.rpm = 0.0
        self.pitch = 0.0
        self.azimuth = 0.0
        self.setMinimumSize(160, 160)

    def set_values(self, rpm, pitch, azimuth):
        self.rpm = max(-100.0, min(100.0, float(rpm)))
        self.pitch = max(-100.0, min(100.0, float(pitch)))
        self.azimuth = float(azimuth) % 360.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        center = QPointF(62, self.height() / 2 + 10)
        radius = min(46, max(30, self.height() * 0.23))
        painter.setPen(QPen(QColor("#405762"), 2))
        painter.setBrush(QColor("#0b151e"))
        painter.drawEllipse(center, radius, radius)
        angle = math.radians(self.azimuth - 90.0)
        tip = QPointF(center.x() + math.cos(angle) * (radius - 8), center.y() + math.sin(angle) * (radius - 8))
        left = QPointF(center.x() + math.cos(angle + 2.55) * 12, center.y() + math.sin(angle + 2.55) * 12)
        right = QPointF(center.x() + math.cos(angle - 2.55) * 12, center.y() + math.sin(angle - 2.55) * 12)
        painter.setPen(QPen(ACCENT, 2))
        painter.setBrush(ACCENT)
        painter.drawPolygon(QPolygonF([tip, left, right]))
        painter.setPen(MUTED)
        painter.setFont(instrument_font(8, QFont.Bold))
        painter.drawText(22, int(center.y() + radius + 4), 80, 16, Qt.AlignCenter, f"{self.azimuth:03.0f}°")
        self._draw_bar(painter, "RPM", self.rpm, 118, 62, ACCENT)
        self._draw_bar(painter, "PITCH", self.pitch, 118, 104, SECONDARY)

    def _draw_bar(self, painter, label, value, x, y, color):
        width = max(56, self.width() - x - 18)
        painter.setPen(MUTED)
        painter.setFont(instrument_font(8, QFont.Bold))
        painter.drawText(x, y - 18, width, 14, Qt.AlignLeft, label)
        painter.setPen(QPen(QColor("#405762"), 1))
        painter.setBrush(QColor("#0b151e"))
        painter.drawRoundedRect(x, y, width, 10, 3, 3)
        zero = x + width / 2
        end = x + width * ((value + 100.0) / 200.0)
        fill_left = min(zero, end)
        fill_width = abs(end - zero)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(int(fill_left), y, int(fill_width), 10, 3, 3)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(9, QFont.Bold))
        painter.drawText(x, y + 13, width, 16, Qt.AlignRight, f"{value:+.1f}%")


class RudderOrderInstrument(FramedInstrument):
    title = "Rudder Order"

    def __init__(self):
        super().__init__()
        self.order = 0.0
        self.response = 0.0
        self.setMinimumSize(210, 170)

    def set_values(self, order, response):
        self.order = max(-45.0, min(45.0, float(order)))
        self.response = max(-45.0, min(45.0, float(response)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        pivot = QPointF(self.width() / 2, self.height() * 0.34)
        radius = min(self.width() * 0.30, self.height() * 0.30)
        path = QPainterPath()
        for index, value in enumerate(range(-45, 46, 3)):
            angle = math.radians(90.0 + value)
            point = QPointF(pivot.x() + math.cos(angle) * radius, pivot.y() + math.sin(angle) * radius)
            path.moveTo(point) if index == 0 else path.lineTo(point)
        painter.setPen(QPen(QColor("#405762"), 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawPath(path)
        self._draw_pointer(painter, pivot, radius - 5, self.response, SECONDARY, 3)
        self._draw_pointer(painter, pivot, radius + 11, self.order, ACCENT, 4)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(13, QFont.Bold))
        painter.drawText(self.rect().adjusted(0, 0, 0, -8), Qt.AlignBottom | Qt.AlignCenter, f"ORD {self.order:+.1f}°  ACT {self.response:+.1f}°")

    def _draw_pointer(self, painter, pivot, length, value, color, width):
        angle = math.radians(90.0 - value)
        tip = QPointF(pivot.x() + math.cos(angle) * length, pivot.y() + math.sin(angle) * length)
        painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(pivot, tip)
        painter.setBrush(color)
        painter.drawEllipse(tip, 4, 4)


class VBWTransversalInstrument(FramedInstrument):
    title = "Transversal Speed"

    def __init__(self):
        super().__init__()
        self.water = 0.0
        self.ground = 0.0
        self.setMinimumSize(160, 160)

    def set_values(self, water, ground):
        self.water = max(-20.0, min(20.0, float(water)))
        self.ground = max(-20.0, min(20.0, float(ground)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        self._draw_axis(painter, "WATER", self.water, 54, ACCENT)
        self._draw_axis(painter, "GROUND", self.ground, 100, SECONDARY)

    def _draw_axis(self, painter, label, value, y, color):
        rect = self.rect().adjusted(18, y, -18, -(self.height() - y - 12))
        painter.setPen(MUTED)
        painter.setFont(instrument_font(8, QFont.Bold))
        painter.drawText(rect.left(), y - 18, rect.width(), 14, Qt.AlignLeft, label)
        painter.setPen(QPen(QColor("#405762"), 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(rect.left(), y, rect.right(), y)
        zero = rect.center().x()
        end = rect.left() + rect.width() * ((value + 20.0) / 40.0)
        painter.setPen(QPen(color, 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(zero, y), QPointF(end, y))
        painter.setPen(QPen(SCALE, 1))
        painter.drawLine(zero, y - 10, zero, y + 10)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(10, QFont.Bold))
        painter.drawText(rect.left(), y + 11, rect.width(), 18, Qt.AlignRight, f"{value:+.1f} kn")


class DockingSpeedInstrument(FramedInstrument):
    title = "Docking Speed"

    def __init__(self):
        super().__init__()
        self.values = {
            "stw_long": 0.0,
            "stw_fwd": 0.0,
            "stw_aft": 0.0,
            "sog_long": 0.0,
            "sog_fwd": 0.0,
            "sog_aft": 0.0,
        }
        self.mode = "stw"
        self.setMinimumSize(170, 170)

    def set_values(self, stw_long, stw_fwd, stw_aft, sog_long, sog_fwd, sog_aft):
        self.values = {
            "stw_long": float(stw_long),
            "stw_fwd": float(stw_fwd),
            "stw_aft": float(stw_aft),
            "sog_long": float(sog_long),
            "sog_fwd": float(sog_fwd),
            "sog_aft": float(sog_aft),
        }
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.mode = "sog" if self.mode == "stw" else "stw"
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        painter.setPen(MUTED)
        painter.setFont(instrument_font(8, QFont.Bold))
        painter.drawText(10, 26, self.width() - 20, 14, Qt.AlignCenter, f"{self.mode.upper()} (click)")

        body = QRectF(24, 48, self.width() - 48, self.height() - 72)
        ship_w = 56.0
        ship_h = max(78.0, body.height() - 4.0)
        ship = QRectF(body.center().x() - ship_w / 2, body.center().y() - ship_h / 2, ship_w, ship_h)
        side_top_y = ship.top() + 18.0
        radius = ship_w / 2.0

        outline = QPainterPath()
        outline.moveTo(ship.left(), ship.bottom())
        outline.lineTo(ship.left(), side_top_y)
        outline.arcTo(QRectF(ship.center().x() - radius, side_top_y - radius, radius * 2, radius * 2), 180.0, -180.0)
        outline.lineTo(ship.right(), ship.bottom())
        outline.lineTo(ship.left(), ship.bottom())
        painter.setPen(QPen(QColor("#5f7b88"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(outline)

        if self.mode == "stw":
            long_v = self.values["stw_long"]
            fwd_v = self.values["stw_fwd"]
            aft_v = self.values["stw_aft"]
            accent = ACCENT
        else:
            long_v = self.values["sog_long"]
            fwd_v = self.values["sog_fwd"]
            aft_v = self.values["sog_aft"]
            accent = SECONDARY

        label_x = ship.center().x() - 21
        fwd_y = ship.top() + 24
        mid_y = ship.center().y() - 1
        aft_y = ship.bottom() - 30
        transversal_color = QColor("#f7c372")
        self._draw_transversal_value(painter, "FWD", fwd_v, QPointF(label_x, fwd_y), ship, transversal_color)
        self._draw_longitudinal_value(painter, "LON", long_v, QPointF(label_x, mid_y), ship, accent)
        self._draw_transversal_value(painter, "AFT", aft_v, QPointF(label_x, aft_y), ship, transversal_color)

    def _draw_transversal_value(self, painter, label, value, origin, ship_rect, color):
        painter.setPen(color)
        painter.setFont(instrument_font(8, QFont.Bold))
        painter.drawText(int(origin.x()), int(origin.y()) - 10, 42, 10, Qt.AlignCenter, label)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(9, QFont.Bold))
        painter.drawText(int(origin.x()), int(origin.y()), 42, 14, Qt.AlignCenter, f"{value:+.1f}")
        # Negative transversal (port/backboard) draws left of outline; positive draws right.
        left_anchor = QPointF(ship_rect.left() - 13, origin.y() + 6)
        right_anchor = QPointF(ship_rect.right() + 13, origin.y() + 6)
        port_side = value < 0.0
        self._draw_horizontal_arrow(
            painter,
            left_anchor if port_side else right_anchor,
            point_left=port_side,
            magnitude=abs(value),
            color=color,
        )

    def _draw_longitudinal_value(self, painter, label, value, origin, ship_rect, color):
        painter.setPen(color)
        painter.setFont(instrument_font(8, QFont.Bold))
        painter.drawText(int(origin.x()), int(origin.y()) - 10, 42, 10, Qt.AlignCenter, label)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(9, QFont.Bold))
        painter.drawText(int(origin.x()), int(origin.y()), 42, 14, Qt.AlignCenter, f"{value:+.1f}")
        anchor = QPointF(ship_rect.right() + 13, origin.y() + 6)
        self._draw_vertical_arrow(
            painter,
            anchor,
            point_up=value >= 0.0,
            magnitude=abs(value),
            color=color,
        )

    def _draw_horizontal_arrow(self, painter, center, point_left, magnitude, color):
        strength = max(0.0, min(1.0, abs(float(magnitude)) / 6.0))
        length = 8 + 10 * strength
        direction = -1 if point_left else 1
        start = QPointF(center.x() - direction * length, center.y())
        tip = QPointF(center.x() + direction * length, center.y())
        painter.setPen(QPen(color, 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(start, tip)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([
            tip,
            QPointF(tip.x() - direction * 6, tip.y() - 3.5),
            QPointF(tip.x() - direction * 6, tip.y() + 3.5),
        ]))

    def _draw_vertical_arrow(self, painter, center, point_up, magnitude, color):
        strength = max(0.0, min(1.0, abs(float(magnitude)) / 6.0))
        length = 8 + 10 * strength
        direction = -1 if point_up else 1
        start = QPointF(center.x(), center.y() - direction * length)
        tip = QPointF(center.x(), center.y() + direction * length)
        painter.setPen(QPen(color, 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(start, tip)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([
            tip,
            QPointF(tip.x() - 3.5, tip.y() - direction * 6),
            QPointF(tip.x() + 3.5, tip.y() - direction * 6),
        ]))


class DualRudderInstrument(FramedInstrument):
    title = "Dual Rudder"

    def __init__(self):
        super().__init__()
        self.stbd_order = 0.0
        self.stbd_response = 0.0
        self.port_order = 0.0
        self.port_response = 0.0
        self.enabled = False
        self.setMinimumSize(170, 170)

    def set_values(self, enabled, stbd_order, stbd_response, port_order, port_response):
        self.enabled = bool(enabled)
        self.stbd_order = max(-45.0, min(45.0, float(stbd_order)))
        self.stbd_response = max(-45.0, min(45.0, float(stbd_response)))
        self.port_order = max(-45.0, min(45.0, float(port_order)))
        self.port_response = max(-45.0, min(45.0, float(port_response)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        status_color = QColor("#77d8a8") if self.enabled else QColor("#9fb0be")
        painter.setPen(status_color)
        painter.setFont(instrument_font(8, QFont.Bold))
        painter.drawText(10, 26, self.width() - 20, 14, Qt.AlignCenter, "ACTIVE" if self.enabled else "INACTIVE")
        left_rect = QRectF(8, 38, self.width() * 0.5 - 12, self.height() - 62)
        right_rect = QRectF(self.width() * 0.5 + 4, 38, self.width() * 0.5 - 12, self.height() - 62)
        self._draw_rudder_mini(painter, left_rect, "PORT", self.port_order, self.port_response)
        self._draw_rudder_mini(painter, right_rect, "STBD", self.stbd_order, self.stbd_response)

    def _draw_rudder_mini(self, painter, rect, label, order, response, shown_order=None, shown_response=None):
        pivot = QPointF(rect.center().x(), rect.top() + rect.height() * 0.30)
        length = min(rect.width() * 0.32, rect.height() * 0.28)
        scale_radius = length + 10
        scale_path = QPainterPath()
        for index, value in enumerate(range(-45, 46, 3)):
            angle = math.radians(90.0 + value)
            point = QPointF(pivot.x() + math.cos(angle) * scale_radius, pivot.y() + math.sin(angle) * scale_radius)
            if index == 0:
                scale_path.moveTo(point)
            else:
                scale_path.lineTo(point)
        painter.setPen(QPen(QColor("#405762"), 4, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(scale_path)
        self._draw_order_marker(painter, pivot, scale_radius, order)
        angle = math.radians(90.0 - response)
        tip = QPointF(pivot.x() + math.cos(angle) * length, pivot.y() + math.sin(angle) * length)
        painter.setPen(QPen(ACCENT, 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(pivot, tip)
        painter.setPen(QPen(QColor("#0b151e"), 1))
        painter.setBrush(ACCENT)
        painter.drawEllipse(pivot, 4.5, 4.5)
        painter.setPen(MUTED)
        painter.setFont(instrument_font(7, QFont.Bold))
        painter.drawText(rect.toRect().adjusted(0, 2, 0, -2), Qt.AlignTop | Qt.AlignHCenter, label)
        painter.setPen(TEXT)
        painter.setFont(instrument_font(8, QFont.Bold))
        shown_response = response if shown_response is None else shown_response
        shown_order = order if shown_order is None else shown_order
        painter.drawText(rect.toRect().adjusted(0, 0, 0, -20), Qt.AlignBottom | Qt.AlignHCenter, f"ACT {shown_response:+.1f}°")
        painter.setPen(QColor("#ffcc66"))
        painter.setFont(instrument_font(7, QFont.Bold))
        painter.drawText(rect.toRect().adjusted(0, 0, 0, -6), Qt.AlignBottom | Qt.AlignHCenter, f"ORD {shown_order:+.1f}°")

    def _draw_order_marker(self, painter, pivot, scale_radius, order):
        angle = math.radians(90.0 - order)
        radial = QPointF(math.cos(angle), math.sin(angle))
        tangent = QPointF(-math.sin(angle), math.cos(angle))
        inner = QPointF(pivot.x() + radial.x() * (scale_radius + 1), pivot.y() + radial.y() * (scale_radius + 1))
        outer = QPointF(pivot.x() + radial.x() * (scale_radius + 11), pivot.y() + radial.y() * (scale_radius + 11))
        painter.setPen(QPen(QColor("#ffcc66"), 1))
        painter.setBrush(QColor("#ffcc66"))
        painter.drawPolygon(QPolygonF([
            inner,
            QPointF(outer.x() + tangent.x() * 5, outer.y() + tangent.y() * 5),
            QPointF(outer.x() - tangent.x() * 5, outer.y() - tangent.y() * 5),
        ]))


class AISListInstrument(FramedInstrument):
    title = "AIS Targets"

    def __init__(self):
        super().__init__()
        self.targets = []
        self.setMinimumSize(160, 160)

    def set_targets(self, targets):
        self.targets = list(targets)[:7]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self._frame(painter)
        painter.setFont(instrument_font(9, QFont.Bold))
        painter.setPen(TEXT)
        y = 38
        if not self.targets:
            painter.drawText(10, y, self.width() - 20, 20, Qt.AlignLeft, "No targets")
            return
        painter.setPen(MUTED)
        painter.setFont(instrument_font(8, QFont.Bold))
        painter.drawText(10, 28, self.width() - 92, 14, Qt.AlignLeft, "NAME")
        painter.drawText(self.width() - 88, 28, 45, 14, Qt.AlignRight, "RNG")
        painter.drawText(self.width() - 40, 28, 32, 14, Qt.AlignRight, "BRG")
        painter.setFont(instrument_font(9, QFont.Bold))
        for target in self.targets:
            name = str(target.get("name", "AIS"))[:12]
            distance = target.get("distance_nm")
            bearing = target.get("bearing")
            painter.setPen(TEXT)
            painter.drawText(10, y, self.width() - 98, 18, Qt.AlignLeft, name)
            painter.drawText(self.width() - 90, y, 48, 18, Qt.AlignRight, "--" if distance is None else f"{float(distance):.1f}")
            painter.drawText(self.width() - 40, y, 32, 18, Qt.AlignRight, "--" if bearing is None else f"{float(bearing):03.0f}")
            y += 20


class InstrumentTile(QFrame):
    def __init__(self, key, instrument, cluster):
        super().__init__()
        self.key = key
        self.instrument = instrument
        self.cluster = cluster
        self._press_pos = QPoint()
        self._dragging = False
        self.setObjectName("instrumentWrapper")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(instrument)

        self.remove_btn = QPushButton("×", self)
        self.remove_btn.setToolTip("Remove instrument")
        self.remove_btn.setFixedSize(26, 26)
        self.remove_btn.setStyleSheet(with_tooltip_style("font-size: 16px; font-weight: bold; padding: 0px; border: 1px solid #8aa3af; border-radius: 13px;"))
        self.remove_btn.clicked.connect(lambda: self.cluster.remove_instrument(self.key))
        self.remove_btn.hide()

        self.long_press = QTimer(self)
        self.long_press.setSingleShot(True)
        self.long_press.setInterval(650)
        self.long_press.timeout.connect(self.cluster.enable_edit_mode)

    def sizeHint(self):
        return QSize(180, 180)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.remove_btn.move(self.width() - self.remove_btn.width() - 4, 4)

    def set_edit_mode(self, enabled):
        self.remove_btn.setVisible(enabled)
        self.setCursor(Qt.OpenHandCursor if enabled else Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self.long_press.start()
            if self.cluster.edit_mode:
                self._dragging = True
                self.raise_()
                self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self.cluster.edit_mode:
            self.cluster.drag_tile(self, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.long_press.stop()
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            self.cluster.drop_tile(self, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class InstrumentCluster(QFrame):
    TILE_MIN = 170
    TILE_MAX = 230

    def __init__(self, sim, ais=None):
        super().__init__()
        self.setObjectName("panel")
        self.sim = sim
        self.ais = ais
        self.edit_mode = False
        self._drag_key = None
        self._drag_origin = None
        self.instruments = {}
        self.tiles = {}
        self._last_grid_metrics = None
        self.items = [
            "hdg", "cog", "rot", "engine",
            "speed", "wind", "rudder", "depth",
            "current", "ais", "time",
        ]
        self.setFocusPolicy(Qt.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.addStretch(1)
        self.add_btn = QPushButton("+")
        self.add_btn.setToolTip("Add instrument")
        self.add_btn.setFixedSize(32, 32)
        self.add_btn.setStyleSheet(with_tooltip_style("font-size: 20px; font-weight: bold; padding: 0px; border: 1px solid #8aa3af; border-radius: 16px;"))
        self.add_btn.clicked.connect(self._show_add_menu)
        self.add_btn.hide()
        toolbar.addWidget(self.add_btn)
        root.addLayout(toolbar)

        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        root.addLayout(self.grid)
        root.addStretch(1)
        self._build_tiles()
        self._rebuild_grid()

        self.setStyleSheet(with_tooltip_style("""
            InstrumentCluster, QFrame#panel {
                background: #071018;
            }
            QFrame#instrumentWrapper {
                background: transparent;
                border: 1px solid transparent;
            }
            QFrame#instrumentWrapper[editing="true"] {
                border: 1px dashed #5f7f90;
                border-radius: 5px;
            }
        """))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def _build_tiles(self):
        factories = {
            "hdg": lambda: HeadingWindInstrument("Heading"),
            "cog": lambda: HeadingWindInstrument("Course over Ground"),
            "wind": lambda: HeadingWindInstrument("True Wind", wind=True),
            "current": lambda: HeadingWindInstrument("Current", wind=True),
            "speed": SpeedComboInstrument,
            "engine": EngineBarInstrument,
            "bow_thruster": BowThrusterInstrument,
            "rot": ROTDotInstrument,
            "rudder": RudderInstrument,
            "time": TimeDateInstrument,
            "depth": DepthTraceInstrument,
            "ais": AISListInstrument,
            "propulsion": PropulsionInstrument,
            "rudder_order": RudderOrderInstrument,
            "vbw_transversal": VBWTransversalInstrument,
            "docking_speed": DockingSpeedInstrument,
            "dual_rudder": DualRudderInstrument,
        }
        for key, factory in factories.items():
            instrument = factory()
            tile = InstrumentTile(key, instrument, self)
            self.instruments[key] = instrument
            self.tiles[key] = tile

    def _rebuild_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        columns, tile_size = self._grid_metrics()
        row = 0
        column = 0
        for key in self.items:
            tile = self.tiles[key]
            tile.setFixedSize(tile_size, tile_size)
            if column >= columns:
                row += 1
                column = 0
            self.grid.addWidget(tile, row, column)
            column += 1
        for i in range(columns):
            self.grid.setColumnStretch(i, 1)
        self._apply_edit_visuals()

    def _grid_metrics(self):
        margins = self.layout().contentsMargins()
        available = max(self.TILE_MIN, self.width() - margins.left() - margins.right())
        spacing = self.grid.spacing()
        columns = max(1, min(len(self.items), (available + spacing) // (self.TILE_MIN + spacing)))
        tile_size = (available - spacing * (columns - 1)) // columns
        tile_size = max(self.TILE_MIN, min(self.TILE_MAX, tile_size))
        return int(columns), int(tile_size)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        metrics = self._grid_metrics()
        if metrics != self._last_grid_metrics:
            self._last_grid_metrics = metrics
            self._rebuild_grid()

    def enable_edit_mode(self):
        if self.edit_mode:
            return
        self.edit_mode = True
        self.setFocus(Qt.OtherFocusReason)
        self.add_btn.show()
        self._apply_edit_visuals()

    def disable_edit_mode(self):
        if not self.edit_mode:
            return
        self.edit_mode = False
        self.add_btn.hide()
        self._apply_edit_visuals()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape and self.edit_mode:
            self.disable_edit_mode()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if self.edit_mode and event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is None or child is self or child is self.add_btn:
                self.disable_edit_mode()
                event.accept()
                return
        super().mousePressEvent(event)

    def _apply_edit_visuals(self):
        for tile in self.tiles.values():
            tile.setProperty("editing", self.edit_mode)
            tile.style().unpolish(tile)
            tile.style().polish(tile)
            tile.set_edit_mode(self.edit_mode and tile.key in self.items)

    def remove_instrument(self, key):
        if key in self.items and len(self.items) > 1:
            self.items.remove(key)
            self._rebuild_grid()

    def _show_add_menu(self):
        menu = QMenu(self)
        labels = {
            "hdg": "Heading",
            "cog": "Course over ground",
            "rot": "Rate of turn",
            "engine": "Engine order",
            "bow_thruster": "Bow thruster",
            "speed": "SOG / STW",
            "wind": "True wind",
            "current": "Current",
            "rudder": "Rudder",
            "depth": "Depth",
            "ais": "AIS targets",
            "propulsion": "Propulsion",
            "vbw_transversal": "Transversal speed",
            "docking_speed": "Docking speed",
            "dual_rudder": "Dual rudder",
            "time": "Time / Date",
        }
        missing = [key for key in labels if key not in self.items]
        if not missing:
            menu.addAction("All instruments are visible").setEnabled(False)
        for key in missing:
            action = menu.addAction(labels[key])
            action.triggered.connect(lambda checked=False, instrument_key=key: self.add_instrument(instrument_key))
        menu.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))

    def add_instrument(self, key):
        if key not in self.items and key in self.tiles:
            self.items.append(key)
            self._rebuild_grid()

    def export_state(self):
        return {"items": [key for key in self.items if key in self.tiles]}

    def apply_state(self, state):
        items = state.get("items") if isinstance(state, dict) else None
        if not isinstance(items, list):
            return
        valid = []
        for key in items:
            if key in self.tiles and key not in valid:
                valid.append(key)
        if valid:
            self.items = valid
            self._rebuild_grid()

    def reset_to_defaults(self):
        self.items = [
            "hdg", "cog", "rot", "engine",
            "speed", "wind", "rudder", "depth",
            "current", "ais", "time",
        ]
        self._rebuild_grid()

    def drag_tile(self, tile, global_pos):
        if tile.key not in self.items:
            return
        local = self.mapFromGlobal(global_pos)
        target_index = self._index_for_position(local)
        current_index = self.items.index(tile.key)
        if target_index != current_index and 0 <= target_index < len(self.items):
            self.items.pop(current_index)
            self.items.insert(target_index, tile.key)
            self._rebuild_grid()

    def drop_tile(self, tile, global_pos):
        self.drag_tile(tile, global_pos)

    def _index_for_position(self, pos):
        centers = []
        for index, key in enumerate(self.items):
            tile = self.tiles[key]
            centers.append((index, (tile.geometry().center() - pos).manhattanLength()))
        if not centers:
            return 0
        return min(centers, key=lambda item: item[1])[0]

    def refresh(self):
        sensor = getattr(self.sim, "sensor", {})
        if "hdg" in self.instruments:
            self.instruments["hdg"].set_values(sensor.get("heading", self.sim.heading))
        if "cog" in self.instruments:
            self.instruments["cog"].set_values(sensor.get("cog", self.sim.cog))
        if "wind" in self.instruments:
            self.instruments["wind"].set_values(
                sensor.get("wind_direction", getattr(self.sim, "wind_direction", 0.0)),
                sensor.get("wind_speed", getattr(self.sim, "wind_speed", 0.0)),
            )
        if "current" in self.instruments:
            self.instruments["current"].set_values(
                sensor.get("current_direction", getattr(self.sim, "current_direction", 0.0)),
                sensor.get("current_drift", getattr(self.sim, "current_speed", 0.0)),
            )
        if "speed" in self.instruments:
            self.instruments["speed"].set_values(sensor.get("sog", self.sim.sog), sensor.get("stw", self.sim.stw))
        if "engine" in self.instruments:
            self.instruments["engine"].set_value(self.sim.target_speed, self.sim.engine_steps[0][1], self.sim.engine_steps[-1][1])
            self.instruments["engine"].set_steps(getattr(self.sim, "engine_steps", []))
        if "rot" in self.instruments:
            self.instruments["rot"].set_value(sensor.get("rot", self.sim.rate_of_turn))
        if "rudder" in self.instruments:
            self.instruments["rudder"].set_values(
                self.sim.rudder,
                getattr(self.sim, "rudder_order", self.sim.rudder),
            )
        if "time" in self.instruments:
            self.instruments["time"].set_now(datetime.now())
        if "depth" in self.instruments:
            self.instruments["depth"].add_sample(sensor.get("depth", self.sim.depth))
        if "ais" in self.instruments:
            self.instruments["ais"].set_targets(self._nearest_ais_targets())
        if "propulsion" in self.instruments:
            self.instruments["propulsion"].set_value(getattr(self.sim, "prc_lever_demand", 0.0))
        if "bow_thruster" in self.instruments:
            self.instruments["bow_thruster"].set_values(
                getattr(self.sim, "bow_thruster_order", 0.0),
                getattr(self.sim, "bow_thruster_response", 0.0),
                getattr(self.sim, "bow_thruster_efficiency", 1.0),
            )
        if "rudder_order" in self.instruments:
            self.instruments["rudder_order"].set_values(
                getattr(self.sim, "rudder_order", 0.0),
                getattr(self.sim, "rudder_response", self.sim.rudder),
            )
        if "vbw_transversal" in self.instruments:
            self.instruments["vbw_transversal"].set_values(
                sensor.get("vbw_transverse_water", 0.0),
                sensor.get("vbw_transverse_ground", 0.0),
            )
        if "docking_speed" in self.instruments:
            self.instruments["docking_speed"].set_values(
                sensor.get("stw", self.sim.stw),
                sensor.get("vbw_fwd_transverse_water", 0.0),
                sensor.get("vbw_aft_transverse_water", 0.0),
                sensor.get("sog", self.sim.sog),
                sensor.get("vbw_fwd_transverse_ground", 0.0),
                sensor.get("vbw_aft_transverse_ground", 0.0),
            )
        if "dual_rudder" in self.instruments:
            self.instruments["dual_rudder"].set_values(
                bool(getattr(self.sim, "additional_sensors", {}).get("RUDDER2", False)),
                sensor.get("rudder_order", getattr(self.sim, "rudder_order", 0.0)),
                sensor.get("rudder_response", getattr(self.sim, "rudder_response", self.sim.rudder)),
                sensor.get("rudder_order_port", getattr(self.sim, "rudder_order_port", getattr(self.sim, "rudder_order", 0.0))),
                sensor.get("rudder_response_port", getattr(self.sim, "rudder_response_port", getattr(self.sim, "rudder_response", self.sim.rudder))),
            )

    def _nearest_ais_targets(self):
        targets = []
        if self.ais:
            for target in self.ais.get_map_targets():
                enriched = dict(target)
                enriched["bearing"] = self.sim._bearing(
                    self.sim.lat,
                    self.sim.lon,
                    enriched.get("lat", self.sim.lat),
                    enriched.get("lon", self.sim.lon),
                )
                if "distance_nm" not in enriched:
                    enriched["distance_nm"] = self.sim._distance_nm(
                        self.sim.lat,
                        self.sim.lon,
                        enriched.get("lat", self.sim.lat),
                        enriched.get("lon", self.sim.lon),
                    )
                targets.append(enriched)
        targets.sort(key=lambda target: target.get("distance_nm", 999999.0))
        return targets
