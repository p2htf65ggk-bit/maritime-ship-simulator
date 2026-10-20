# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from ui.widgets.slider import ResetSlider
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from maritime_simulator.domain.commands import SetAuxiliarySensors, SetSensorNoise, SetSensorValues
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.domain.state import (
    DEFAULT_SENSOR_NOISE_PERIOD_S,
    SimulationSnapshot,
    sensor_noise_settings,
)


EDITOR_WIDTH = 92
SLIDER_MINIMUM_WIDTH = 72


def _spin(minimum: float, maximum: float, decimals: int, suffix: str = "") -> QDoubleSpinBox:
    field = QDoubleSpinBox()
    field.setRange(minimum, maximum)
    field.setDecimals(decimals)
    field.setKeyboardTracking(False)
    field.setSuffix(suffix)
    field.setFixedWidth(EDITOR_WIDTH)
    return field


def _configure_form(form: QFormLayout) -> None:
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignTop)
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(6)
    form.setContentsMargins(4, 6, 4, 6)


def _slider_editor(field: QDoubleSpinBox, callback, scale: int = 10) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    slider = ResetSlider(Qt.Horizontal)
    slider.setRange(round(field.minimum() * scale), round(field.maximum() * scale))
    slider.setMinimumWidth(SLIDER_MINIMUM_WIDTH)

    def slider_changed(value: int) -> None:
        field.blockSignals(True)
        field.setValue(value / scale)
        field.blockSignals(False)
        callback()

    slider.valueChanged.connect(slider_changed)
    field.valueChanged.connect(lambda value: slider.setValue(round(value * scale)))
    field.editingFinished.connect(callback)
    field._paired_slider = slider
    layout.addWidget(slider, 1)
    layout.addWidget(field)
    return container


class AdvancedSensorsPanel(QWidget):
    """V1-compatible additional sensors without taking ownership of model state."""

    command_submitted = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        rudder = QGroupBox("Steering sensors")
        rudder_form = QFormLayout(rudder)
        _configure_form(rudder_form)
        self.dual_rudder = QCheckBox("Second rudder active")
        self.dual_rudder.toggled.connect(
            lambda active: self.command_submitted.emit(SetAuxiliarySensors(dual_rudder=active))
        )
        rudder_form.addRow(self.dual_rudder)
        self.rudder_fields: dict[str, QDoubleSpinBox] = {}
        for name, label in (
            ("rudder_order_starboard_deg", "STBD order"),
            ("rudder_response_starboard_deg", "STBD response / RSA"),
            ("rudder_order_port_deg", "PORT order"),
            ("rudder_response_port_deg", "PORT response / RSA"),
        ):
            field = _spin(-45.0, 45.0, 1, "°")
            self.rudder_fields[name] = field
            rudder_form.addRow(label, _slider_editor(field, lambda key=name: self._rudder_changed(key)))
        root.addWidget(rudder)

        motion = QGroupBox("Additional motion sensors")
        motion_form = QFormLayout(motion)
        _configure_form(motion_form)
        self.motion_fields: dict[str, QDoubleSpinBox] = {}
        for name, label in (
            ("pitch_deg", "Pitch"), ("roll_deg", "Roll"),
            ("transverse_water_kn", "Transversal STW"),
            ("transverse_ground_kn", "Transversal SOG"),
            ("forward_transverse_water_kn", "Forward transversal STW"),
            ("forward_transverse_ground_kn", "Forward transversal SOG"),
            ("aft_transverse_water_kn", "Aft transversal STW"),
            ("aft_transverse_ground_kn", "Aft transversal SOG"),
        ):
            suffix = "°" if name in {"pitch_deg", "roll_deg"} else " kn"
            field = _spin(-30.0, 30.0, 2, suffix)
            self.motion_fields[name] = field
            field.editingFinished.connect(lambda key=name: self._auxiliary_changed(key))
            motion_form.addRow(label, field)
        root.addWidget(motion)

        meteo = QGroupBox("Manual MDA / MTW sensors")
        meteo_form = QFormLayout(meteo)
        _configure_form(meteo_form)
        self.meteo_fields: dict[str, QDoubleSpinBox] = {}
        definitions = (
            ("barometric_pressure_bar", "Pressure", 0.8, 1.2, 3, " bar"),
            ("air_temperature_c", "Air temperature", -60.0, 80.0, 1, " °C"),
            ("water_temperature_c", "Water temperature", -5.0, 50.0, 1, " °C"),
            ("relative_humidity_percent", "Relative humidity", 0.0, 100.0, 1, " %"),
            ("absolute_humidity_percent", "Absolute humidity", 0.0, 100.0, 1, " %"),
            ("dew_point_c", "Dew point", -60.0, 60.0, 1, " °C"),
        )
        for name, label, minimum, maximum, decimals, suffix in definitions:
            field = _spin(minimum, maximum, decimals, suffix)
            self.meteo_fields[name] = field
            field.editingFinished.connect(lambda key=name: self._auxiliary_changed(key))
            meteo_form.addRow(label, field)
        root.addWidget(meteo)

        propulsion = QGroupBox("Propulsion and thruster sensors")
        propulsion_form = QFormLayout(propulsion)
        _configure_form(propulsion_form)
        self.aux_fields: dict[str, QDoubleSpinBox] = {}
        for name, label, minimum, maximum, suffix in (
            ("water_distance_nm", "Water distance", 0.0, 999999.0, " NM"),
            ("ground_distance_nm", "Ground distance", 0.0, 999999.0, " NM"),
            ("propeller_lever_demand_percent", "Propeller lever demand", -100.0, 100.0, " %"),
            ("thruster_rpm_demand_percent", "Thruster RPM demand", -100.0, 100.0, " %"),
            ("thruster_pitch_demand_percent", "Thruster pitch demand", -100.0, 100.0, " %"),
            ("thruster_azimuth_demand_deg", "Thruster azimuth demand", 0.0, 359.9, "°"),
            ("thruster_rpm_response_percent", "Thruster RPM response", -100.0, 100.0, " %"),
            ("thruster_pitch_response_percent", "Thruster pitch response", -100.0, 100.0, " %"),
            ("thruster_azimuth_response_deg", "Thruster azimuth response", 0.0, 359.9, "°"),
        ):
            field = _spin(minimum, maximum, 1, suffix)
            self.aux_fields[name] = field
            field.editingFinished.connect(lambda key=name: self._auxiliary_changed(key))
            propulsion_form.addRow(label, field)
        self.thruster_number = QSpinBox()
        self.thruster_number.setRange(1, 99)
        self.thruster_number.valueChanged.connect(
            lambda value: not self._updating and self.command_submitted.emit(SetAuxiliarySensors(thruster_number=value))
        )
        propulsion_form.addRow("Thruster number", self.thruster_number)
        root.addWidget(propulsion)

        root.addStretch(1)

    def update_snapshot(self, snapshot: SimulationSnapshot) -> None:
        self._updating = True
        try:
            manual = snapshot.control_mode is ControlMode.SENSOR
            self.dual_rudder.blockSignals(True)
            self.dual_rudder.setChecked(snapshot.dual_rudder)
            self.dual_rudder.blockSignals(False)
            topology_editable = snapshot.control_mode is not ControlMode.EXTERNAL_RUDDER
            self.dual_rudder.setEnabled(topology_editable)
            self.dual_rudder.setToolTip(
                "Detected from the active external ROR/RSA source in External Rudder mode."
                if not topology_editable else
                "Enable a separate PORT rudder order and response."
            )
            rudder_values = {
                "rudder_order_starboard_deg": snapshot.rudder_order_starboard_deg,
                "rudder_response_starboard_deg": snapshot.rudder_response_starboard_deg,
                "rudder_order_port_deg": snapshot.rudder_order_port_deg,
                "rudder_response_port_deg": snapshot.rudder_response_port_deg,
            }
            for name, field in self.rudder_fields.items():
                editable = manual and ("port" not in name or snapshot.dual_rudder)
                field.setEnabled(editable)
                slider = getattr(field, "_paired_slider", None)
                if slider is not None:
                    slider.setEnabled(editable)
                field.setToolTip("Generated by the active vessel model in this mode." if not editable else "")
                self._set_field(field, rudder_values[name])
            motion_values = {
                name: getattr(snapshot, name)
                for name in self.motion_fields
            }
            for name, field in self.motion_fields.items():
                editable = manual or name in {"pitch_deg", "roll_deg"}
                field.setEnabled(editable)
                field.setToolTip("Generated by the active vessel model in this mode." if not editable else "")
                self._set_field(field, motion_values[name])
            for collection in (self.meteo_fields, self.aux_fields):
                for name, field in collection.items():
                    if name == "propeller_lever_demand_percent" or name.startswith("thruster_"):
                        field.setEnabled(manual)
                        field.setToolTip("Manual sensor test value." if manual else "Generated by the engine / bow-thruster model.")
                    self._set_field(field, getattr(snapshot, name))
            self.thruster_number.setEnabled(manual)
            self.thruster_number.blockSignals(True)
            self.thruster_number.setValue(snapshot.thruster_number)
            self.thruster_number.blockSignals(False)
        finally:
            self._updating = False

    @staticmethod
    def _set_field(field: QDoubleSpinBox, value: float) -> None:
        if field.hasFocus():
            return
        field.blockSignals(True)
        field.setValue(float(value))
        field.blockSignals(False)
        slider = getattr(field, "_paired_slider", None)
        if slider is not None and not slider.isSliderDown():
            slider.blockSignals(True)
            slider.setValue(round(float(value) * 10.0))
            slider.blockSignals(False)

    def _rudder_changed(self, name: str) -> None:
        if not self._updating:
            self.command_submitted.emit(SetSensorValues(**{name: self.rudder_fields[name].value()}))

    def _auxiliary_changed(self, name: str) -> None:
        if self._updating:
            return
        field = self.motion_fields.get(name) or self.meteo_fields.get(name) or self.aux_fields.get(name)
        self.command_submitted.emit(SetAuxiliarySensors(**{name: field.value()}))



NOISE_DEFINITIONS = (
    ("position", "Position", 1000.0, " m"),
    ("heading", "Heading", 30.0, "°"),
    ("cog", "COG", 30.0, "°"),
    ("sog", "SOG", 20.0, " kn"),
    ("stw", "STW", 20.0, " kn"),
    ("rot", "Rate of turn", 60.0, " °/min"),
    ("xte", "Cross-track error", 1.0, " NM"),
    ("wind_direction", "Wind direction (true + relative)", 45.0, "°"),
    ("wind_speed", "Wind speed (true + relative)", 30.0, " kn"),
    ("current_direction", "Current direction", 45.0, "°"),
    ("current_speed", "Current speed", 10.0, " kn"),
    ("rudder_order_starboard", "STBD rudder order", 15.0, "°"),
    ("rudder_response_starboard", "STBD rudder response / RSA", 15.0, "°"),
    ("rudder_order_port", "PORT rudder order", 15.0, "°"),
    ("rudder_response_port", "PORT rudder response / RSA", 15.0, "°"),
    ("longitudinal_water", "Longitudinal STW", 10.0, " kn"),
    ("transverse_water", "Transversal STW", 10.0, " kn"),
    ("transverse_ground", "Transversal SOG", 10.0, " kn"),
    ("forward_transverse_water", "Forward transversal STW", 10.0, " kn"),
    ("forward_transverse_ground", "Forward transversal SOG", 10.0, " kn"),
    ("aft_transverse_water", "Aft transversal STW", 10.0, " kn"),
    ("aft_transverse_ground", "Aft transversal SOG", 10.0, " kn"),
    ("depth", "Depth", 100.0, " m"),
    ("pressure", "Barometric pressure", 0.10, " bar"),
    ("air_temperature", "Air temperature", 20.0, " °C"),
    ("water_temperature", "Water temperature", 20.0, " °C"),
    ("humidity", "Humidity (relative + absolute)", 30.0, " %"),
    ("dew_point", "Dew point", 20.0, " °C"),
    ("pitch", "Pitch", 10.0, "°"),
    ("roll", "Roll", 10.0, "°"),
    ("water_distance", "Water distance", 5.0, " NM"),
    ("ground_distance", "Ground distance", 5.0, " NM"),
    ("propeller_lever", "Propeller lever demand", 30.0, " %"),
    ("thruster_rpm_demand", "Thruster RPM demand", 30.0, " %"),
    ("thruster_pitch_demand", "Thruster pitch demand", 30.0, " %"),
    ("thruster_azimuth_demand", "Thruster azimuth demand", 45.0, "°"),
    ("thruster_rpm_response", "Thruster RPM response", 30.0, " %"),
    ("thruster_pitch_response", "Thruster pitch response", 30.0, " %"),
    ("thruster_azimuth_response", "Thruster azimuth response", 45.0, "°"),
)


class SensorNoisePanel(QWidget):
    """Projection-only noise controls for every numeric protocol sensor value."""

    command_submitted = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        note = QLabel(
            "Noise is applied only to protocol sensor snapshots. It does not feed back into vessel dynamics."
        )
        note.setObjectName("secondaryText")
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("On"), 0, 0)
        grid.addWidget(QLabel("Sensor value"), 0, 1)
        grid.addWidget(QLabel("Amplitude"), 0, 2)
        grid.addWidget(QLabel("Cycle time"), 0, 3)
        self.noise_fields: dict[str, tuple[QCheckBox, QDoubleSpinBox]] = {}
        self.noise_period_fields: dict[str, tuple[QSlider, QLabel]] = {}
        for row_index, (name, label, maximum, suffix) in enumerate(NOISE_DEFINITIONS, start=1):
            enabled = QCheckBox()
            enabled.setAccessibleName(f"Enable {label} noise")
            enabled.setToolTip(f"Enable projection-only noise for {label}.")
            amplitude = _spin(0.0, maximum, 3 if maximum <= 1.0 else 2, suffix)
            period_editor = QWidget()
            period_layout = QHBoxLayout(period_editor)
            period_layout.setContentsMargins(0, 0, 0, 0)
            period_layout.setSpacing(5)
            period = ResetSlider(Qt.Horizontal)
            period.setRange(1, 600)
            period.setValue(round(DEFAULT_SENSOR_NOISE_PERIOD_S * 10.0))
            period.setProperty("sliderResetValue", period.value())
            period.setMinimumWidth(72)
            period.setMaximumWidth(118)
            period.setAccessibleName(f"{label} noise cycle time")
            period.setToolTip(
                "Duration of one complete noise cycle, from 0.1 to 60.0 seconds."
            )
            period_value = QLabel(f"{period.value() / 10.0:.1f} s")
            period_value.setObjectName("secondaryText")
            period_value.setFixedWidth(46)
            period_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            period_layout.addWidget(period, 1)
            period_layout.addWidget(period_value)
            enabled.toggled.connect(
                lambda active, key=name: self._noise_changed(key, enabled=active)
            )
            amplitude.valueChanged.connect(
                lambda _value, key=name, field=amplitude: self._noise_changed(
                    key,
                    amplitude=field.value(),
                )
            )
            period.valueChanged.connect(
                lambda value, key=name, display=period_value: self._noise_period_changed(
                    key,
                    value,
                    display,
                )
            )
            label_widget = QLabel(label)
            label_widget.setWordWrap(True)
            label_widget.setMinimumWidth(0)
            label_widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            grid.addWidget(enabled, row_index, 0, Qt.AlignCenter)
            grid.addWidget(label_widget, row_index, 1)
            grid.addWidget(amplitude, row_index, 2)
            grid.addWidget(period_editor, row_index, 3)
            self.noise_fields[name] = enabled, amplitude
            self.noise_period_fields[name] = period, period_value
        layout.addLayout(grid)
        layout.addStretch(1)

    def update_snapshot(self, snapshot: SimulationSnapshot) -> None:
        self._updating = True
        try:
            values = dict(snapshot.sensor_noise)
            for name, (enabled, amplitude) in self.noise_fields.items():
                legacy = {
                    "wind_direction": ("true_wind_direction", "relative_wind_direction"),
                    "wind_speed": ("true_wind_speed", "relative_wind_speed"),
                    "humidity": ("relative_humidity", "absolute_humidity"),
                }.get(name, ())
                settings = values.get(
                    name,
                    next(
                        (
                            values[key]
                            for key in legacy
                            if key in values and sensor_noise_settings(values[key])[0]
                        ),
                        None,
                    ),
                )
                active, value, period_s = sensor_noise_settings(settings)
                enabled.blockSignals(True)
                enabled.setChecked(bool(active))
                if not amplitude.hasFocus():
                    amplitude.blockSignals(True)
                    amplitude.setValue(float(value))
                    amplitude.blockSignals(False)
                enabled.blockSignals(False)
                period, period_value = self.noise_period_fields[name]
                period.blockSignals(True)
                period.setValue(round(period_s * 10.0))
                period.blockSignals(False)
                period_value.setText(f"{period_s:.1f} s")
        finally:
            self._updating = False

    def _noise_changed(self, name: str, *, enabled=None, amplitude=None) -> None:
        if not self._updating:
            self.command_submitted.emit(
                SetSensorNoise(name=name, enabled=enabled, amplitude=amplitude)
            )

    def _noise_period_changed(self, name: str, value: int, display: QLabel) -> None:
        period_s = value / 10.0
        display.setText(f"{period_s:.1f} s")
        if not self._updating:
            self.command_submitted.emit(SetSensorNoise(name=name, period_s=period_s))
