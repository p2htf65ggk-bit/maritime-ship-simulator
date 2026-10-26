# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import (
    SelectVesselProfile,
    SetAuxiliarySensors,
    SeekRoute,
    SetEnvironment,
    SetDepthSource,
    SetEnvironmentSource,
    SetRudderOrder,
    SetSensorValues,
    SetSensorCoupling,
    SetSpeedOrder,
    SetBowThrusterOrder,
    SetRouteOptions,
)
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.domain.state import SimulationSnapshot
from maritime_simulator.simulation.route_geometry import RouteGeometry, radius_from_rot, rot_from_radius
from ui.widgets.slider import ResetSlider

from .controls import CompassDial, CoordinateSpinBox, EngineTelegraphControl
from .bow_thruster import BowThrusterControl
from .dialogs import reserve_label_lines
from .control_sources import SOURCE_LABELS
from .online_environment import OpenMeteoAccessDialog


EDITOR_WIDTH = 116
SLIDER_MINIMUM_WIDTH = 104


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def _spin(minimum: float, maximum: float, decimals: int, suffix: str = "") -> QDoubleSpinBox:
    field = QDoubleSpinBox()
    field.setRange(minimum, maximum)
    field.setDecimals(decimals)
    field.setKeyboardTracking(False)
    if suffix:
        field.setSuffix(suffix)
    field.setFixedWidth(EDITOR_WIDTH)
    return field


def _configure_form(form: QFormLayout) -> None:
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignTop)
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(6)


def _slider_field(
    field: QDoubleSpinBox,
    *,
    scale: int,
    changed,
    stacked: bool = False,
) -> tuple[QWidget, QSlider]:
    """Pair a precise editor with a live slider without creating signal loops."""

    container = QWidget()
    row = QVBoxLayout(container) if stacked else QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    slider = ResetSlider(Qt.Horizontal)
    slider.setRange(round(field.minimum() * scale), round(field.maximum() * scale))
    slider.setMinimumWidth(SLIDER_MINIMUM_WIDTH)
    slider.setSingleStep(1)
    slider.setPageStep(max(1, round((field.maximum() - field.minimum()) * scale / 20.0)))

    def from_slider(value: int) -> None:
        field.blockSignals(True)
        field.setValue(value / scale)
        field.blockSignals(False)
        changed()

    def from_field(value: float) -> None:
        slider.blockSignals(True)
        slider.setValue(round(value * scale))
        slider.blockSignals(False)

    slider.valueChanged.connect(from_slider)
    field.valueChanged.connect(from_field)
    row.addWidget(slider, 1)
    if stacked:
        row.addWidget(field, 0, Qt.AlignHCenter)
    else:
        row.addWidget(field)
    return container, slider


class _EnvironmentSourceComboBox(QComboBox):
    """Keep full menu/accessibility labels while fitting the closed control."""

    def __init__(self):
        super().__init__()
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def display_text(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        rect = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self)
        text = self.currentText()
        if self.currentData() == "open-meteo" and self.fontMetrics().horizontalAdvance(text) > rect.width():
            return "Online"
        return text

    def showPopup(self):
        if not self.count():
            return
        width = max(self.fontMetrics().horizontalAdvance(self.itemText(i)) for i in range(self.count())) + 36
        self.view().setMinimumWidth(width)
        super().showPopup()

    def paintEvent(self, _event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = self.display_text()
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        painter.drawControl(QStyle.CE_ComboBoxLabel, option)


class ModeControlStack(QWidget):
    """Mode-specific command surface; no simulation state is owned here."""

    command_submitted = Signal(object)
    keyboard_control_changed = Signal(bool)

    _MODES = (
        ControlMode.SENSOR,
        ControlMode.SHIP,
        ControlMode.TRACK,
        ControlMode.EXTERNAL_RUDDER,
    )

    def __init__(self, service: SimulatorService, parent=None):
        super().__init__(parent)
        self.service = service
        self._pages: dict[ControlMode, QWidget] = {}
        self._profile_fields: dict[ControlMode, QComboBox] = {}
        self._engine_fields: dict[ControlMode, EngineTelegraphControl] = {}
        self._bow_fields = {}
        self._route_total_nm = 0.0
        self._updating_track_controls = False
        self._active_route = ()
        self._active_waypoint_index = 0
        self._pending_track_turn_mode: str | None = None
        self._track_turn_commit_timer = QTimer(self)
        self._track_turn_commit_timer.setSingleShot(True)
        self._track_turn_commit_timer.setInterval(120)
        self._track_turn_commit_timer.timeout.connect(self._commit_pending_track_turn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for mode, builder in (
            (ControlMode.SENSOR, self._build_sensor_page),
            (ControlMode.SHIP, self._build_ship_page),
            (ControlMode.TRACK, self._build_track_page),
            (ControlMode.EXTERNAL_RUDDER, self._build_external_page),
        ):
            page = builder()
            page.setVisible(mode is ControlMode.SENSOR)
            self._pages[mode] = page
            layout.addWidget(page)

    @property
    def sensor_fields(self) -> dict[str, QDoubleSpinBox]:
        return self._sensor_fields

    def set_mode(self, mode: ControlMode) -> None:
        for candidate, page in self._pages.items():
            page.setVisible(candidate is mode)

    def update_snapshot(self, snapshot: SimulationSnapshot) -> None:
        self.set_mode(snapshot.control_mode)
        self._update_profiles(snapshot)
        self._update_sensor_values(snapshot)
        self._update_model_feedback(snapshot)
        self._update_track(snapshot)

    def _page(self, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(_section(title))
        detail = QLabel(description)
        detail.setObjectName("secondaryText")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        return page, layout

    def _build_sensor_page(self) -> QWidget:
        page, layout = self._page(
            "Sensor Control",
            "Publish exact test values. Vessel dynamics do not overwrite these fields in this mode.",
        )
        form = QFormLayout()
        _configure_form(form)
        self._sensor_fields = {}
        self._sensor_sliders = {}
        definitions = (
            ("sog_kn", "SOG", -30.0, 80.0, 1, " kn"),
            ("stw_kn", "STW", -30.0, 80.0, 1, " kn"),
            ("rot_deg_min", "ROT", -120.0, 120.0, 1, " °/min"),
        )
        for key, label, latitude in (
            ("latitude_deg", "Latitude", True),
            ("longitude_deg", "Longitude", False),
        ):
            field = CoordinateSpinBox(latitude=latitude)
            field.editingFinished.connect(lambda name=key: self._sensor_changed(name))
            self._sensor_fields[key] = field
            form.addRow(label, field)
        for key, label, minimum, maximum, decimals, suffix in definitions:
            field = _spin(minimum, maximum, decimals, suffix)
            field.editingFinished.connect(lambda name=key: self._sensor_changed(name))
            self._sensor_fields[key] = field
            if key in {
                "sog_kn", "stw_kn", "rot_deg_min",
            }:
                editor, slider = _slider_field(
                    field,
                    scale=10,
                    changed=lambda name=key: self._sensor_changed(name),
                )
                self._sensor_sliders[key] = (slider, 10)
                form.addRow(label, editor)
            else:
                form.addRow(label, field)
        layout.addLayout(form)
        layout.addWidget(_section("Heading and Course"))
        dial_row = QHBoxLayout()
        self._heading_dial = CompassDial("HDG")
        self._cog_dial = CompassDial("COG")
        self._heading_dial.value_changed.connect(
            lambda value: self.command_submitted.emit(SetSensorValues(heading_deg=value))
        )
        self._cog_dial.value_changed.connect(
            lambda value: self.command_submitted.emit(SetSensorValues(cog_deg=value))
        )
        self._cog_dial.reset_requested.connect(self._reset_cog_offset)
        dial_row.addWidget(self._heading_dial)
        dial_row.addWidget(self._cog_dial)
        layout.addLayout(dial_row)
        coupling_row = QHBoxLayout()
        self._cog_follows_heading = QCheckBox("COG follows HDG")
        self._cog_follows_heading.setToolTip("Keep the current COG/HDG offset while Heading changes.")
        self._cog_follows_heading.toggled.connect(
            lambda active: self.command_submitted.emit(SetSensorCoupling(cog_follows_heading=active))
        )
        self._heading_follows_rot = QCheckBox("ROT drives HDG")
        self._heading_follows_rot.setToolTip("Integrate Heading from the manually selected rate of turn.")
        self._heading_follows_rot.toggled.connect(
            lambda active: self.command_submitted.emit(SetSensorCoupling(heading_follows_rot=active))
        )
        coupling_row.addWidget(self._cog_follows_heading)
        coupling_row.addWidget(self._heading_follows_rot)
        layout.addLayout(coupling_row)
        layout.addStretch(1)
        return page

    def _build_ship_page(self) -> QWidget:
        page, layout = self._page(
            "Ship Control",
            "Set engine and rudder orders. The selected vessel model produces motion and rudder response.",
        )
        layout.addWidget(_section("Vessel"))
        layout.addWidget(self._profile_combo(ControlMode.SHIP))
        layout.addWidget(_section("Engine Telegraph"))
        layout.addWidget(self._engine_combo(ControlMode.SHIP))
        self._ship_speed_feedback = QLabel("Speed order --")
        self._ship_speed_feedback.setObjectName("dataReadout")
        layout.addWidget(self._ship_speed_feedback)
        self._ship_rudder_label = _section("Rudder")
        layout.addWidget(self._ship_rudder_label)
        self._ship_rudder = ResetSlider(Qt.Horizontal)
        self._ship_rudder.setRange(-450, 450)
        self._ship_rudder.setToolTip("Rudder order. Double-click for midships (0 degrees).")
        self._ship_rudder.valueChanged.connect(self._ship_rudder_changed)
        self._ship_rudder_spin = _spin(-45.0, 45.0, 1, "°")
        self._ship_rudder_spin.editingFinished.connect(self._ship_rudder_spin_changed)
        rudder_row = QHBoxLayout()
        rudder_row.setContentsMargins(0, 0, 0, 0)
        rudder_row.setSpacing(6)
        self._ship_rudder.setMinimumWidth(SLIDER_MINIMUM_WIDTH)
        rudder_row.addWidget(self._ship_rudder, 1)
        rudder_row.addWidget(self._ship_rudder_spin)
        layout.addLayout(rudder_row)
        self._ship_rudder_feedback = QLabel("Response --")
        self._ship_rudder_feedback.setObjectName("dataReadout")
        layout.addWidget(self._ship_rudder_feedback)
        layout.addWidget(self._bow_control(ControlMode.SHIP))
        self.keyboard_control = QCheckBox("Keyboard control")
        self.keyboard_control.setToolTip("Use Up/Down for telegraph detents and Left/Right for one-degree rudder steps.")
        self.keyboard_control.toggled.connect(self.keyboard_control_changed.emit)
        layout.addWidget(self.keyboard_control)
        layout.addStretch(1)
        return page

    def _build_track_page(self) -> QWidget:
        page, layout = self._page(
            "Track Control",
            "Set the speed order and route position. The Track Controller owns rudder order and follows planned turns.",
        )
        layout.addWidget(_section("Vessel"))
        layout.addWidget(self._profile_combo(ControlMode.TRACK))
        layout.addWidget(_section("Engine Telegraph"))
        layout.addWidget(self._engine_combo(ControlMode.TRACK))
        layout.addWidget(self._bow_control(ControlMode.TRACK))
        layout.addWidget(_section("Turn Planning"))
        turn_form = QFormLayout()
        _configure_form(turn_form)
        self._track_rot = _spin(0.1, 120.0, 1, " °/min")
        self._track_rot.editingFinished.connect(lambda: self._track_turn_value_changed("fixed_rot"))
        track_rot_row, self._track_rot_slider = _slider_field(
            self._track_rot,
            scale=10,
            changed=lambda: self._queue_track_turn_value("fixed_rot"),
        )
        self._track_radius = _spin(0.01, 20.0, 2, " NM")
        self._track_radius.editingFinished.connect(lambda: self._track_turn_value_changed("fixed_radius"))
        track_radius_row, self._track_radius_slider = _slider_field(
            self._track_radius,
            scale=100,
            changed=lambda: self._queue_track_turn_value("fixed_radius"),
        )
        self._track_rot_slider.setProperty("sliderResetValue", 100)
        self._track_radius_slider.setProperty("sliderResetValue", 10)
        turn_form.addRow("Turn rate", track_rot_row)
        turn_form.addRow("Turn radius", track_radius_row)
        layout.addLayout(turn_form)
        self._track_rot.setToolTip("Edit or move this control to make Turn Rate the fixed planning value.")
        self._track_radius.setToolTip("Edit or move this control to make Turn Radius the fixed planning value.")
        self._track_rot_slider.setToolTip(self._track_rot.toolTip())
        self._track_radius_slider.setToolTip(self._track_radius.toolTip())
        self._track_value_source = QLabel("Effective turn source: Route Planning table")
        self._track_value_source.setObjectName("secondaryText")
        self._track_value_source.setWordWrap(True)
        self._track_value_source.setToolTip(
            "The Track Control sliders remain editable. They become effective when "
            "Use planned Turn Rate / Radius is cleared in Route Planning."
        )
        layout.addWidget(self._track_value_source)
        seek_section = _section("Route Position")
        seek_section.hide()
        layout.addWidget(seek_section)
        self._track_seek = ResetSlider(Qt.Horizontal)
        self._track_seek.setRange(0, 10000)
        self._track_seek.valueChanged.connect(self._track_seek_changed)
        self._track_seek_spin = _spin(0.0, 0.0, 2, " NM")
        self._track_seek_spin.valueChanged.connect(self._track_seek_spin_changed)
        seek_row = QHBoxLayout()
        seek_row.addWidget(self._track_seek, 1)
        seek_row.addWidget(self._track_seek_spin)
        seek_container = QWidget()
        seek_container.setLayout(seek_row)
        seek_container.hide()
        layout.addWidget(seek_container)
        self._track_seek_label = QLabel("0.00 / 0.00 NM")
        self._track_seek_label.setObjectName("dataReadout")
        self._track_seek_label.hide()
        layout.addWidget(self._track_seek_label)
        self._track_limits = QLabel("Model limits --")
        self._track_limits.setObjectName("secondaryText")
        self._track_limits.setWordWrap(True)
        layout.addWidget(self._track_limits)
        layout.addStretch(1)
        return page

    def _build_external_page(self) -> QWidget:
        page, layout = self._page(
            "External Control",
            "Select independent rudder, engine and bow-thruster sources in Settings - External Input. "
            "Manual controls are available when selected, or when the configured input-loss action is Manual.",
        )
        self._external_title = layout.itemAt(0).widget()
        layout.addWidget(_section("Vessel"))
        layout.addWidget(self._profile_combo(ControlMode.EXTERNAL_RUDDER))
        layout.addWidget(_section("Engine Telegraph"))
        layout.addWidget(self._engine_combo(ControlMode.EXTERNAL_RUDDER))
        layout.addWidget(self._bow_control(ControlMode.EXTERNAL_RUDDER))
        layout.addWidget(_section("Rudder"))
        self._external_manual_rudder = ResetSlider(Qt.Horizontal)
        self._external_manual_rudder.setRange(-450, 450)
        self._external_manual_rudder.setToolTip("Manual rudder order. Double-click for midships (0 degrees).")
        self._external_manual_rudder.valueChanged.connect(lambda value: self.command_submitted.emit(SetRudderOrder(value / 10.0)))
        layout.addWidget(self._external_manual_rudder)
        self._external_input_section = _section("ROR Input")
        layout.addWidget(self._external_input_section)
        self._external_feedback = QLabel("Unavailable")
        self._external_feedback.setObjectName("dataReadout")
        self._external_feedback.setWordWrap(True)
        reserve_label_lines(self._external_feedback, 3, padding=16)
        layout.addWidget(self._external_feedback)
        self._external_rudder_feedback = QLabel("Order --\nResponse --")
        self._external_rudder_feedback.setObjectName("dataReadout")
        layout.addWidget(self._external_rudder_feedback)
        layout.addStretch(1)
        return page

    def _profile_combo(self, mode: ControlMode) -> QComboBox:
        combo = QComboBox()
        for profile in self.service.profiles.all_profiles():
            combo.addItem(profile.display_name, profile.profile_id)
        combo.currentIndexChanged.connect(lambda _index, field=combo: self._profile_changed(field))
        self._profile_fields[mode] = combo
        return combo

    def _engine_combo(self, mode: ControlMode) -> EngineTelegraphControl:
        telegraph = EngineTelegraphControl()
        telegraph.speed_selected.connect(lambda speed, owner=mode: self._engine_changed(owner, speed))
        self._engine_fields[mode] = telegraph
        return telegraph

    def _bow_control(self, mode):
        control = BowThrusterControl()
        control.order_changed.connect(lambda value: self.command_submitted.emit(SetBowThrusterOrder(value)))
        self._bow_fields[mode] = control
        return control

    def _engine_changed(self, mode: ControlMode, speed: float) -> None:
        if mode is ControlMode.TRACK:
            self.command_submitted.emit(SetRouteOptions(use_planned_speed=False))
        self.command_submitted.emit(SetSpeedOrder(speed))

    def _profile_changed(self, combo: QComboBox) -> None:
        profile_id = combo.currentData()
        if profile_id:
            self.command_submitted.emit(SelectVesselProfile(str(profile_id)))
            self._populate_engine_fields(str(profile_id))

    def _populate_engine_fields(self, profile_id: str, selected_speed: float | None = None) -> None:
        profile = self.service.profiles.get(profile_id)
        for telegraph in self._engine_fields.values():
            current = selected_speed if selected_speed is not None else 0.0
            telegraph.set_steps(profile.engine_steps(), float(current))
        maximum = round(profile.max_rudder_deg * 10.0)
        self._ship_rudder.setRange(-maximum, maximum)
        self._ship_rudder_spin.setRange(-profile.max_rudder_deg, profile.max_rudder_deg)

    def refresh_profiles(self, selected_id: str | None = None) -> None:
        selected = selected_id or self.service.snapshot().vessel_profile_id
        profiles = self.service.profiles.all_profiles()
        for combo in self._profile_fields.values():
            combo.blockSignals(True)
            combo.clear()
            for profile in profiles:
                combo.addItem(profile.display_name, profile.profile_id)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self._populate_engine_fields(str(selected), self.service.snapshot().speed_order_kn)

    def _sensor_changed(self, name: str) -> None:
        self.command_submitted.emit(SetSensorValues(**{name: self._sensor_fields[name].value()}))

    def _reset_cog_offset(self) -> None:
        heading = self._heading_dial.value()
        self.command_submitted.emit(SetSensorValues(cog_deg=heading))

    def step_engine(self, direction: int) -> None:
        telegraph = self._engine_fields.get(ControlMode.SHIP)
        if telegraph is not None:
            telegraph.step(direction)

    def step_rudder(self, direction: int) -> None:
        if not self._ship_rudder.isEnabled():
            return
        profile = self.service.profiles.get(self.service.snapshot().vessel_profile_id)
        value = max(-profile.max_rudder_deg, min(profile.max_rudder_deg, self._ship_rudder_spin.value() + direction))
        self._ship_rudder_spin.setValue(value)
        self._ship_rudder_spin_changed()

    def _ship_rudder_changed(self, value: int) -> None:
        angle = value / 10.0
        self._ship_rudder_spin.blockSignals(True)
        self._ship_rudder_spin.setValue(angle)
        self._ship_rudder_spin.blockSignals(False)
        self.command_submitted.emit(SetRudderOrder(angle))

    def _ship_rudder_spin_changed(self) -> None:
        angle = self._ship_rudder_spin.value()
        self._ship_rudder.blockSignals(True)
        self._ship_rudder.setValue(round(angle * 10.0))
        self._ship_rudder.blockSignals(False)
        self.command_submitted.emit(SetRudderOrder(angle))

    def _track_seek_preview(self, value: int) -> float:
        progress = self._route_total_nm * value / 10000.0
        self._track_seek_label.setText(f"{progress:.2f} / {self._route_total_nm:.2f} NM")
        self._track_seek_spin.blockSignals(True)
        self._track_seek_spin.setValue(progress)
        self._track_seek_spin.blockSignals(False)
        return progress

    def _track_seek_changed(self, value: int) -> None:
        progress = self._track_seek_preview(value)
        self.command_submitted.emit(SeekRoute(progress))

    def _track_seek_spin_changed(self, progress: float) -> None:
        value = 0 if self._route_total_nm <= 0.0 else round(progress / self._route_total_nm * 10000.0)
        self._track_seek.blockSignals(True)
        self._track_seek.setValue(max(0, min(10000, value)))
        self._track_seek.blockSignals(False)
        self._track_seek_label.setText(f"{progress:.2f} / {self._route_total_nm:.2f} NM")
        self.command_submitted.emit(SeekRoute(progress))

    def _track_turn_value_changed(self, mode: str) -> None:
        self._track_turn_commit_timer.stop()
        self._pending_track_turn_mode = None
        if self._updating_track_controls:
            return
        self._set_track_control_mode_appearance(mode, planned=False)
        if mode == "fixed_radius":
            self.command_submitted.emit(
                SetRouteOptions(
                    use_planned_turns=False,
                    control_turn_mode="fixed_radius",
                    control_turn_radius_nm=self._track_radius.value(),
                )
            )
        else:
            self.command_submitted.emit(
                SetRouteOptions(
                    use_planned_turns=False,
                    control_turn_mode="fixed_rot",
                    control_turn_rate_deg_min=self._track_rot.value(),
                )
            )

    def _queue_track_turn_value(self, mode: str) -> None:
        """Coalesce slider movement so route geometry is not rebuilt per pixel."""
        if self._updating_track_controls:
            return
        self._set_track_control_mode_appearance(mode, planned=False)
        self._pending_track_turn_mode = mode
        self._track_turn_commit_timer.start()

    def _commit_pending_track_turn(self) -> None:
        mode = self._pending_track_turn_mode
        self._pending_track_turn_mode = None
        if mode is not None:
            self._track_turn_value_changed(mode)

    def _set_track_control_mode_appearance(self, mode: str, *, planned: bool = False) -> None:
        active_mode = "fixed_radius" if mode == "fixed_radius" else "fixed_rot"
        for widget, derived in (
            (self._track_rot, planned or active_mode == "fixed_radius"),
            (self._track_rot_slider, planned or active_mode == "fixed_radius"),
            (self._track_radius, planned or active_mode != "fixed_radius"),
            (self._track_radius_slider, planned or active_mode != "fixed_radius"),
        ):
            if widget.property("derived") != derived:
                widget.setProperty("derived", derived)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def _update_profiles(self, snapshot: SimulationSnapshot) -> None:
        expected_ids = [profile.profile_id for profile in self.service.profiles.all_profiles()]
        if any([combo.itemData(index) for index in range(combo.count())] != expected_ids for combo in self._profile_fields.values()):
            self.refresh_profiles(snapshot.vessel_profile_id)
        changed = False
        for combo in self._profile_fields.values():
            index = combo.findData(snapshot.vessel_profile_id)
            if index >= 0 and combo.currentIndex() != index:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
                changed = True
        if changed or any(not telegraph._steps for telegraph in self._engine_fields.values()):
            self._populate_engine_fields(snapshot.vessel_profile_id, snapshot.speed_order_kn)
        for mode, telegraph in self._engine_fields.items():
            if not snapshot.engine_manual_enabled or not telegraph.hasFocus():
                telegraph.set_speed(snapshot.speed_order_kn)
            telegraph.setEnabled(snapshot.engine_manual_enabled)
            telegraph.set_derived(mode is ControlMode.TRACK and snapshot.use_planned_speed)
        profile = self.service.profiles.get(snapshot.vessel_profile_id)
        for control in self._bow_fields.values():
            control.update_snapshot(snapshot, profile)
        manual_rudder = snapshot.external_rudder_manual_enabled
        self._external_manual_rudder.setVisible(manual_rudder)
        self._external_manual_rudder.setRange(-round(profile.max_rudder_deg * 10), round(profile.max_rudder_deg * 10))
        if not self._external_manual_rudder.isSliderDown():
            self._external_manual_rudder.blockSignals(True)
            self._external_manual_rudder.setValue(round(snapshot.rudder_order_starboard_deg * 10))
            self._external_manual_rudder.blockSignals(False)

    def _update_sensor_values(self, snapshot: SimulationSnapshot) -> None:
        values = {
            "latitude_deg": snapshot.latitude_deg,
            "longitude_deg": snapshot.longitude_deg,
            "sog_kn": snapshot.sog_kn,
            "stw_kn": snapshot.stw_kn,
            "rot_deg_min": snapshot.rate_of_turn_deg_min,
        }
        for name, value in values.items():
            field = self._sensor_fields[name]
            if not field.hasFocus():
                field.blockSignals(True)
                field.setValue(value)
                field.blockSignals(False)
                slider_data = self._sensor_sliders.get(name)
                if slider_data is not None:
                    slider, scale = slider_data
                    if not slider.isSliderDown():
                        slider.blockSignals(True)
                        slider.setValue(round(value * scale))
                        slider.blockSignals(False)
        self._heading_dial.set_value(snapshot.heading_deg)
        self._cog_dial.set_value(snapshot.cog_deg)
        self._cog_follows_heading.blockSignals(True)
        self._heading_follows_rot.blockSignals(True)
        self._cog_follows_heading.setChecked(snapshot.cog_follows_heading)
        self._heading_follows_rot.setChecked(snapshot.heading_follows_rot)
        self._cog_follows_heading.blockSignals(False)
        self._heading_follows_rot.blockSignals(False)

    def _update_model_feedback(self, snapshot: SimulationSnapshot) -> None:
        self._ship_speed_feedback.setText(
            f"Order {snapshot.speed_order_kn:+.1f} kn   |   STW {snapshot.stw_kn:+.1f} kn"
        )
        if not self._ship_rudder.isSliderDown():
            self._ship_rudder.blockSignals(True)
            self._ship_rudder.setValue(round(snapshot.rudder_order_starboard_deg * 10.0))
            self._ship_rudder.blockSignals(False)
            if not self._ship_rudder_spin.hasFocus():
                self._ship_rudder_spin.blockSignals(True)
                self._ship_rudder_spin.setValue(snapshot.rudder_order_starboard_deg)
                self._ship_rudder_spin.blockSignals(False)
        self._ship_rudder_feedback.setText(
            f"Response {snapshot.rudder_response_starboard_deg:+.1f}°   |   ROT {snapshot.rate_of_turn_deg_min:+.1f}°/min"
        )
        age = "--" if snapshot.external_rudder_age_s is None else f"{snapshot.external_rudder_age_s:.1f} s"
        kind = SOURCE_LABELS.get(snapshot.external_rudder_active_source, "") or (
            "Manual" if snapshot.external_rudder_input_type == "MANUAL" else "Waiting"
        )
        self._external_title.setText("External Control")
        self._external_input_section.setText(f"{kind} Input")
        self._external_feedback.setText(
            f"{kind}  {snapshot.external_rudder_validity.value.upper()}   |   age {age}"
        )
        self._external_rudder_feedback.setText(
            f"Order {snapshot.rudder_order_starboard_deg:+.1f}°\n"
            f"Response {snapshot.rudder_response_starboard_deg:+.1f}°"
        )

    def _update_track(self, snapshot: SimulationSnapshot) -> None:
        geometry = RouteGeometry(snapshot.route_waypoints)
        self._active_route = snapshot.route_waypoints
        self._active_waypoint_index = snapshot.next_waypoint_index
        self._route_total_nm = geometry.total_distance_nm
        self._track_seek_spin.blockSignals(True)
        self._track_seek_spin.setRange(0.0, self._route_total_nm)
        self._track_seek_spin.blockSignals(False)
        if not self._track_seek.isSliderDown():
            value = 0 if self._route_total_nm <= 0.0 else round(snapshot.route_progress_nm / self._route_total_nm * 10000.0)
            self._track_seek.blockSignals(True)
            self._track_seek.setValue(max(0, min(10000, value)))
            self._track_seek.blockSignals(False)
            self._track_seek_label.setText(
                f"{snapshot.route_progress_nm:.2f} / {self._route_total_nm:.2f} NM"
            )
            if not self._track_seek_spin.hasFocus():
                self._track_seek_spin.blockSignals(True)
                self._track_seek_spin.setValue(snapshot.route_progress_nm)
                self._track_seek_spin.blockSignals(False)
        profile = self.service.profiles.get(snapshot.vessel_profile_id)
        theoretical_min_radius = radius_from_rot(profile.max_speed_kn, profile.max_rot_deg_min)
        self._updating_track_controls = True
        try:
            self._track_rot.setRange(0.1, profile.max_rot_deg_min)
            self._track_rot_slider.setRange(1, max(1, round(profile.max_rot_deg_min * 10.0)))
            radius_max = max(20.0, theoretical_min_radius * 50.0)
            self._track_radius.setRange(theoretical_min_radius, radius_max)
            self._track_radius_slider.setRange(
                max(1, round(theoretical_min_radius * 100.0)),
                max(2, round(radius_max * 100.0)),
            )
            active_mode = "fixed_radius" if snapshot.control_turn_mode == "fixed_radius" else "fixed_rot"
            current_speed = abs(snapshot.sog_kn)
            if current_speed <= 0.05:
                current_speed = abs(snapshot.speed_order_kn)
            rot = snapshot.control_turn_rate_deg_min
            radius = max(theoretical_min_radius, snapshot.control_turn_radius_nm)
            if current_speed > 0.01:
                if active_mode == "fixed_radius":
                    rot = min(profile.max_rot_deg_min, rot_from_radius(current_speed, radius))
                else:
                    radius = max(theoretical_min_radius, radius_from_rot(current_speed, rot))
            for field, slider, value, scale in (
                (self._track_rot, self._track_rot_slider, rot, 10),
                (self._track_radius, self._track_radius_slider, radius, 100),
            ):
                pending_mode = "fixed_rot" if field is self._track_rot else "fixed_radius"
                if not field.hasFocus() and not slider.isSliderDown() and self._pending_track_turn_mode != pending_mode:
                    field.blockSignals(True)
                    field.setValue(value)
                    field.blockSignals(False)
                    slider.blockSignals(True)
                    slider.setValue(round(value * scale))
                    slider.blockSignals(False)
            self._set_track_control_mode_appearance(active_mode, planned=snapshot.use_planned_turns)
        finally:
            self._updating_track_controls = False
        self._track_limits.setText(
            f"Model limits: ROT {profile.max_rot_deg_min:.1f}°/min, minimum theoretical radius "
            f"{theoretical_min_radius:.2f} NM, rudder ±{profile.max_rudder_deg:.1f}°."
        )
        self._track_value_source.setText(
            "Effective turn source: Route Planning table"
            if snapshot.use_planned_turns
            else "Effective turn source: Track Control values"
        )


class EnvironmentPanel(QWidget):
    """Shared environment/time controls used by every control mode."""

    command_submitted = Signal(object)
    weather_source_changed = Signal(str)
    api_key_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.open_meteo_api_key = ""
        self.vector_grid = QGridLayout()
        self.vector_grid.setContentsMargins(0, 0, 0, 0)
        self.vector_grid.setSpacing(12)
        self._vector_columns = 1
        self._vector_viewport = None
        self._vector_headers = {}
        self._vector_speed_rows = {}
        self.wind_section = self._vector_section("wind", "Wind", "TRUE WIND FROM", 150)
        self.current_section = self._vector_section("current", "Current", "CURRENT FROM", 20)
        self.vector_grid.addWidget(self.wind_section, 0, 0)
        self.vector_grid.addWidget(self.current_section, 1, 0)
        self.vector_grid.setColumnStretch(0, 1)
        layout.addLayout(self.vector_grid)
        self.feedback = QLabel("")
        self.feedback.setObjectName("secondaryText")
        self.feedback.setToolTip("Apparent wind relative to the bow, calculated from true wind and vessel motion.")
        reserve_label_lines(self.feedback, 1)
        layout.addWidget(self.feedback)

        depth_header = QHBoxLayout()
        depth_header.addWidget(_section("Depth"))
        self.depth_source = QComboBox()
        self.depth_source.addItem("Manual", "manual")
        self.depth_source.addItem("Chart-based", "chart")
        self.depth_source.setToolTip(
            "Prefers installed S57 cells, then GEBCO online, then Manual. "
            "Offline Mode skips GEBCO. "
            "Keeps the last depth when data are unavailable and returns automatically."
        )
        self.depth_source.currentIndexChanged.connect(lambda: self.command_submitted.emit(
            SetDepthSource(str(self.depth_source.currentData() or "manual"))
        ))
        depth_header.addWidget(self.depth_source, 1)
        layout.addLayout(depth_header)
        self.depth = _spin(0.0, 12000.0, 1, " m")
        self.depth.setToolTip("Depth below keel/transducer. Chart depth subtracts the vessel's Draft.")
        self.depth.editingFinished.connect(self._depth_changed)
        depth_row, self.depth_slider = _slider_field(
            self.depth, scale=10, changed=self._depth_changed
        )
        layout.addWidget(depth_row)
        self.depth_status = QLabel("Manual depth below keel")
        self.depth_status.setObjectName("secondaryText")
        self.depth_status.setWordWrap(True)
        reserve_label_lines(self.depth_status, 1)
        layout.addWidget(self.depth_status)

        self.weather_header = QGridLayout()
        self.weather_header.setContentsMargins(0, 0, 0, 0)
        self.weather_header.setSpacing(6)
        self._weather_title = _section("Atmospheric data")
        self.weather_header.addWidget(self._weather_title, 0, 0, 1, 2)
        self._weather_inline = False
        self.weather_source = _EnvironmentSourceComboBox()
        self.weather_source.addItem("Manual", "manual")
        self.weather_source.addItem("Online - Open-Meteo", "open-meteo")
        self.weather_source.setToolTip(
            "Manual uses the XDR values below. Online retrieves air temperature, "
            "relative humidity, dew point and surface pressure for the current "
            "own-ship position. Free access is non-commercial only. MDA and MTW remain manual in Other."
        )
        self.weather_source.currentIndexChanged.connect(self._weather_source_changed)
        self.weather_header.addWidget(self.weather_source, 1, 0, 1, 3)
        self.online_access = QToolButton()
        self.online_access.setText("Open-Meteo access...")
        self.online_access.setAccessibleName("Open-Meteo access")
        self.online_access.setToolTip("Open-Meteo access: optional API key for wind, current and atmospheric data")
        self.online_access.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self.online_access.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.online_access.setFixedSize(28, 28)
        self.online_access.clicked.connect(self._open_online_access)
        self.weather_header.addWidget(self.online_access, 0, 2, Qt.AlignRight)
        self.weather_header.setColumnStretch(0, 1)
        layout.addLayout(self.weather_header)
        self.xdr_fields: dict[str, QDoubleSpinBox] = {}
        self.xdr_grid = QGridLayout()
        self.xdr_grid.setContentsMargins(0, 0, 0, 0)
        self.xdr_grid.setHorizontalSpacing(12)
        self.xdr_grid.setVerticalSpacing(8)
        for index, (name, label, minimum, maximum, decimals, suffix) in enumerate((
            ("xdr_air_temperature_c", "Air temperature", -60.0, 80.0, 1, " °C"),
            ("xdr_relative_humidity_percent", "Relative humidity", 0.0, 100.0, 1, " %"),
            ("xdr_dew_point_c", "Dew point", -60.0, 60.0, 1, " °C"),
            ("xdr_surface_pressure_bar", "Surface pressure", 0.8, 1.2, 3, " bar"),
        )):
            field = _spin(minimum, maximum, decimals, suffix)
            field.editingFinished.connect(self._xdr_environment_changed)
            self.xdr_fields[name] = field
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(3)
            caption = QLabel(label)
            caption.setWordWrap(True)
            caption.setBuddy(field)
            field.setAccessibleName(label)
            cell_layout.addWidget(caption)
            cell_layout.addWidget(field)
            self.xdr_grid.addWidget(cell, index//2, index%2)
        self.xdr_grid.setColumnStretch(0, 1)
        self.xdr_grid.setColumnStretch(1, 1)
        layout.addLayout(self.xdr_grid)
        self.weather_status = QLabel("Manual")
        self.weather_status.setObjectName("secondaryText")
        self.weather_status.setWordWrap(True)
        reserve_label_lines(self.weather_status, 1)
        layout.addWidget(self.weather_status)
        layout.addStretch(1)

    def _vector_section(self, channel, title, dial_title, maximum):
        section = QWidget()
        column = QVBoxLayout(section)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        header = QGridLayout()
        header.setSpacing(8)
        header.addWidget(_section(title), 0, 0)
        source = self._online_source(channel)
        header.addWidget(source, 0, 1)
        header.setColumnStretch(1, 1)
        self._vector_headers[channel] = header
        column.addLayout(header)
        direction = CompassDial(dial_title, compact=True)
        direction.setAccessibleName(f"{title} direction from")
        direction.value_changed.connect(lambda _value: self._environment_changed())
        column.addWidget(direction, 0, Qt.AlignHCenter)
        speed = _spin(0.0, maximum, 1, " kn")
        speed.setAccessibleName(f"{title} speed")
        speed.setToolTip(f"{title} speed")
        speed.editingFinished.connect(self._environment_changed)
        row, slider = _slider_field(speed, scale=10, changed=self._environment_changed, stacked=True)
        self._vector_speed_rows[channel] = row
        slider.setToolTip(f"{title} speed")
        slider.setAccessibleName(f"{title} speed")
        column.addWidget(row)
        status = self._source_status(column)
        for name, control in (("source", source), ("direction", direction), ("speed", speed),
                              ("speed_slider", slider), ("status", status)):
            setattr(self, f"{channel}_{name}", control)
        return section

    def _layout_vector_columns(self, width):
        margins = self.layout().contentsMargins()
        minimum = max(max(getattr(self, f"{channel}_source").minimumSizeHint().width(),
                          getattr(self, f"{channel}_direction").minimumWidth(),
                          self._vector_speed_rows[channel].minimumSizeHint().width())
                      for channel in ("wind", "current"))
        available = width - margins.left() - margins.right()
        columns = 2 if available >= 2*minimum + self.vector_grid.horizontalSpacing() else 1
        if columns != self._vector_columns:
            self._vector_columns = columns
            for channel, header in self._vector_headers.items():
                source = getattr(self, f"{channel}_source")
                header.addWidget(source, 1 if columns == 2 else 0, 0 if columns == 2 else 1,
                                 1, 2 if columns == 2 else 1)
                header.setColumnStretch(0, 1 if columns == 2 else 0)
                header.setColumnStretch(1, 0 if columns == 2 else 1)
            self.vector_grid.addWidget(self.current_section, 0 if columns == 2 else 1, 1 if columns == 2 else 0)
            self.vector_grid.setColumnStretch(1, 1 if columns == 2 else 0)
            self.updateGeometry()
        if hasattr(self, "weather_header"):
            needed = (self._weather_title.minimumSizeHint().width() + self.weather_source.minimumSizeHint().width()
                      + self.online_access.width() + 2*self.weather_header.horizontalSpacing())
            inline = available >= needed
            if inline != self._weather_inline:
                self._weather_inline = inline
                self.weather_header.addWidget(self._weather_title, 0, 0, 1, 1 if inline else 2)
                self.weather_header.addWidget(self.weather_source, 0 if inline else 1, 1 if inline else 0,
                                             1, 1 if inline else 3)
                self.weather_header.setColumnStretch(0, 0 if inline else 1)
                self.weather_header.setColumnStretch(1, 1 if inline else 0)
                self.updateGeometry()

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not self._vector_viewport:
            if self._vector_viewport is not None:
                self._vector_viewport.removeEventFilter(self)
            self._vector_viewport = parent
            if parent is not None:
                parent.installEventFilter(self)
        self._layout_vector_columns(parent.width() if parent is not None else self.width())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "vector_grid"):
            width = min(event.size().width(), self._vector_viewport.width()) if self._vector_viewport is not None else event.size().width()
            self._layout_vector_columns(width)

    def eventFilter(self, watched, event):
        # Observe the scroll viewport too: a two-column minimum must not prevent
        # the content from folding back when the surrounding pane gets narrower.
        if watched is self._vector_viewport and event.type() == QEvent.Resize:
            self._layout_vector_columns(event.size().width())
        return super().eventFilter(watched, event)

    def update_snapshot(self, snapshot: SimulationSnapshot) -> None:
        for channel in ("wind", "current"):
            source = getattr(self, f"{channel}_source")
            requested = getattr(snapshot, f"{channel}_source")
            active = getattr(snapshot, f"active_{channel}_source") == "open-meteo"
            source.blockSignals(True)
            source.setCurrentIndex(max(0, source.findData(requested)))
            source.blockSignals(False)
            for control in (getattr(self, f"{channel}_direction"), getattr(self, f"{channel}_speed"),
                            getattr(self, f"{channel}_speed_slider")):
                control.setEnabled(not active)
            getattr(self, f"{channel}_status").setText(
                "Open-Meteo" if active else "Manual fallback" if requested == "open-meteo" else "Manual"
            )
        self.depth_source.blockSignals(True)
        self.depth_source.setCurrentIndex(max(0, self.depth_source.findData(snapshot.depth_source)))
        self.depth_source.blockSignals(False)
        chart_active = snapshot.active_depth_source in {"gebco", "s57"}
        self.depth.setEnabled(not chart_active)
        self.depth_slider.setEnabled(not chart_active)
        if chart_active:
            label = "GEBCO" if snapshot.active_depth_source == "gebco" else "S57"
            self.depth_status.setText(f"{label} - depth below keel")
        else:
            self.depth_status.setText("Manual fallback"
                                      if snapshot.depth_source == "chart" else "Manual depth below keel")
        self.depth_status.setToolTip("Depth below keel/transducer. When chart depth is unavailable, the last depth is held.")
        for field, value in (
            (self.wind_speed, snapshot.wind_speed_kn),
            (self.current_speed, snapshot.current_speed_kn),
            (self.depth, snapshot.depth_m),
        ):
            if not field.hasFocus():
                field.blockSignals(True)
                field.setValue(value)
                field.blockSignals(False)
        for field, slider, value in (
            (self.wind_speed, self.wind_speed_slider, snapshot.wind_speed_kn),
            (self.current_speed, self.current_speed_slider, snapshot.current_speed_kn),
            (self.depth, self.depth_slider, snapshot.depth_m),
        ):
            if not field.hasFocus() and not slider.isSliderDown():
                slider.blockSignals(True)
                slider.setValue(round(value * 10.0))
                slider.blockSignals(False)
        self.wind_direction.set_value(snapshot.wind_from_deg)
        self.current_direction.set_value(snapshot.current_from_deg)
        for name, field in self.xdr_fields.items():
            if not field.hasFocus():
                field.blockSignals(True)
                field.setValue(float(getattr(snapshot, name)))
                field.blockSignals(False)
        self.feedback.setText(
            f"Apparent wind {snapshot.relative_wind_from_bow_deg:05.1f}° / {snapshot.relative_wind_speed_kn:.1f} kn"
        )

    def _environment_changed(self) -> None:
        self.command_submitted.emit(
            SetEnvironment(
                wind_from_deg=self.wind_direction.value(),
                wind_speed_kn=self.wind_speed.value(),
                current_from_deg=self.current_direction.value(),
                current_speed_kn=self.current_speed.value(),
            )
        )

    def _online_source(self, channel):
        source = _EnvironmentSourceComboBox()
        source.addItem("Manual", "manual")
        source.addItem("Online - Open-Meteo", "open-meteo")
        source.setToolTip("Open-Meteo at own ship; last values held on loss, automatic recovery. "
                          "Free API: non-commercial only. Customer key: Open-Meteo access."
                          + (" Coarse current data; limited harbour/river accuracy." if channel == "current" else ""))
        source.currentIndexChanged.connect(lambda: self.command_submitted.emit(
            SetEnvironmentSource(channel, str(source.currentData() or "manual"))
        ))
        return source

    @staticmethod
    def _source_status(layout):
        status = QLabel("Manual")
        status.setObjectName("secondaryText")
        status.setWordWrap(True)
        reserve_label_lines(status, 1)
        layout.addWidget(status)
        return status

    def _open_online_access(self):
        dialog = OpenMeteoAccessDialog(self.open_meteo_api_key, self)
        if dialog.exec() == QDialog.Accepted:
            self.api_key_changed.emit(dialog.api_key)

    def _depth_changed(self) -> None:
        self.command_submitted.emit(SetEnvironment(depth_m=self.depth.value()))

    def set_weather_source(self, source: str) -> None:
        index = self.weather_source.findData(str(source).strip().lower())
        self.weather_source.blockSignals(True)
        self.weather_source.setCurrentIndex(max(0, index))
        self.weather_source.blockSignals(False)
        self._update_weather_editability()

    def set_weather_status(self, status: str) -> None:
        message = str(status)
        self.weather_status.setToolTip(message)
        if message.startswith("Open-Meteo"):
            short = "Open-Meteo (cached)" if "cached" in message else "Open-Meteo"
        elif message.startswith("Offline"):
            short = "Offline - last values held"
        elif "unavailable" in message.lower():
            short = "Weather unavailable"
        elif message.startswith(("Loading", "Online")):
            short = "Loading online weather..."
        else:
            short = "Manual" if message.startswith("Manual") else "Weather status"
        self.weather_status.setText(short)

    def _weather_source_changed(self) -> None:
        self._update_weather_editability()
        self.weather_source_changed.emit(str(self.weather_source.currentData() or "manual"))

    def _update_weather_editability(self) -> None:
        live = self.weather_source.currentData() == "open-meteo"
        for field in self.xdr_fields.values():
            field.setEnabled(not live)
            field.setToolTip(
                "Updated from live weather at the current own-ship position."
                if live else "Manual value for standard XDR Environment output."
            )

    def _xdr_environment_changed(self) -> None:
        if self.weather_source.currentData() == "open-meteo":
            return
        self.command_submitted.emit(SetAuxiliarySensors(**{
            name: field.value() for name, field in self.xdr_fields.items()
        }))


class _RotatedLabel(QLabel):
    """A narrow 90-degree label used beside compact vertical controls."""

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        return QSize(size.height() + 4, size.width() + 4)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.translate(0, self.height())
        painter.rotate(-90)
        painter.drawText(QRect(0, 0, self.height(), self.width()), Qt.AlignCenter, self.text())


class _CompactVerticalControl(QWidget):
    value_changed = Signal(float)

    def __init__(self, title: str, minimum: float, maximum: float, scale: int, suffix: str, parent=None):
        super().__init__(parent)
        self.scale = max(1, int(scale))
        self.decimals = 0 if self.scale == 1 else 1 if self.scale == 10 else 2
        self.suffix = suffix
        # Keep the slider axis independent of the formatted value width.  The
        # compact panel used to let the value label resize this column, which
        # moved the handle by a few pixels for signs and two-digit values.
        self.setFixedWidth(54)
        root = QHBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 4)
        root.setSpacing(2)
        self.title = _RotatedLabel(title)
        self.title.setObjectName("statusHeader")
        self.title.setFixedWidth(16)
        root.addWidget(self.title)
        self.value_column = QWidget()
        self.value_column.setFixedWidth(32)
        column = QVBoxLayout(self.value_column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self.value = QLabel("--")
        self.value.setObjectName("compactValue")
        self.value.setAlignment(Qt.AlignCenter)
        self.value.setFixedWidth(32)
        column.addWidget(self.value)
        self.slider = ResetSlider(Qt.Vertical)
        if title == "RATE":
            self.slider.setProperty("sliderResetValue", 10 * self.scale)
        elif title == "RADIUS":
            self.slider.setProperty("sliderResetValue", round(0.1 * self.scale))
        self.slider.setMinimumHeight(86)
        self.slider.setFixedWidth(22)
        self.slider.valueChanged.connect(self._changed)
        column.addWidget(self.slider, 1, Qt.AlignHCenter)
        root.addWidget(self.value_column)
        self.set_range(minimum, maximum)

    def set_range(self, minimum: float, maximum: float) -> None:
        self.slider.setRange(round(minimum * self.scale), max(round(minimum * self.scale), round(maximum * self.scale)))

    def set_title(self, title: str) -> None:
        self.title.setText(title)

    def configure(self, title: str, minimum: float, maximum: float, scale: int, suffix: str) -> None:
        self.scale = max(1, int(scale))
        self.decimals = 0 if self.scale == 1 else 1 if self.scale == 10 else 2
        self.suffix = suffix
        self.set_title(title)
        self.set_range(minimum, maximum)

    def set_derived(self, derived: bool) -> None:
        for widget in (self, self.title, self.value, self.slider):
            if widget.property("derived") != bool(derived):
                widget.setProperty("derived", bool(derived))
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def set_value(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(float(value) * self.scale))
        self.slider.blockSignals(False)
        self._set_value_text(self.slider.value() / self.scale)

    def current_value(self) -> float:
        return self.slider.value() / self.scale

    def _changed(self, value: int) -> None:
        number = value / self.scale
        self._set_value_text(number)
        self.value_changed.emit(number)

    def _set_value_text(self, number: float) -> None:
        numeric = f"{number:.{self.decimals}f}"
        self.value.setText(numeric)
        self.value.setToolTip(f"{numeric}{self.suffix}")


class CompactModeControls(QWidget):
    """Narrow mode-specific command surface with unclipped vertical controls."""

    command_submitted = Signal(object)

    def __init__(self, service: SimulatorService, parent=None):
        super().__init__(parent)
        self.service = service
        self._updating = False
        self._track_mode = "fixed_rot"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 5)
        layout.setSpacing(3)
        self.mode_title = QLabel("SENSOR")
        self.mode_title.setObjectName("statusHeader")
        self.mode_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.mode_title)

        self.controls_host = QWidget()
        self.controls = QGridLayout(self.controls_host)
        self.controls.setContentsMargins(0, 0, 0, 0)
        self.controls.setHorizontalSpacing(2)
        self.controls.setVerticalSpacing(4)
        layout.addWidget(self.controls_host, 1)
        self.sensor_hdg = self._control("HDG", 0.0, 359.9, 10, "°", self._sensor_hdg_changed)
        self.sensor_rot = self._control("ROT", -120.0, 120.0, 10, "°", self._sensor_rot_changed)
        self.sensor_sog = self._control("SOG", -30.0, 80.0, 10, "kn", self._sensor_sog_changed)
        self.sensor_stw = self._control("STW", -30.0, 80.0, 10, "kn", self._sensor_stw_changed)
        self.engine = self._control("ENGINE", -30.0, 80.0, 10, "kn", self._engine_changed_compact)
        self.bow_thruster = BowThrusterControl()
        self.bow_thruster.order_changed.connect(lambda value: self.command_submitted.emit(SetBowThrusterOrder(value)))
        self.rudder = self._control("RUDDER", -45.0, 45.0, 10, "°", self._rudder_changed_compact)
        self.track_rot = self._control("RATE", 0.1, 120.0, 10, "°", self._track_rot_changed)
        self.track_radius = self._control("RADIUS", 0.01, 20.0, 100, "NM", self._track_radius_changed)
        self.primary = self.engine
        self.secondary = self.track_rot

    def _control(self, title, minimum, maximum, scale, suffix, changed) -> _CompactVerticalControl:
        control = _CompactVerticalControl(title, minimum, maximum, scale, suffix)
        control.value_changed.connect(changed)
        return control

    def _arrange(self, mode: ControlMode) -> None:
        while self.controls.count():
            self.controls.takeAt(0)
        for control in (
            self.sensor_hdg, self.sensor_rot, self.sensor_sog, self.sensor_stw,
            self.engine, self.rudder, self.track_rot, self.track_radius,
            self.bow_thruster,
        ):
            control.hide()
        if mode is ControlMode.SENSOR:
            rows = ((self.sensor_hdg, self.sensor_rot), (self.sensor_sog, self.sensor_stw))
        elif mode is ControlMode.SHIP:
            rows = ((self.engine,), (self.bow_thruster,), (self.rudder,))
        elif mode is ControlMode.TRACK:
            rows = ((self.engine,), (self.bow_thruster,), (self.track_rot, self.track_radius))
        else:
            rows = ((self.engine,), (self.bow_thruster,), (self.rudder,))
        for row, controls in enumerate(rows):
            for column, control in enumerate(controls):
                span = 2 if len(controls) == 1 else 1
                self.controls.addWidget(control, row, column, 1, span, Qt.AlignHCenter)
                control.show()

    def update_snapshot(self, snapshot: SimulationSnapshot) -> None:
        self._updating = True
        try:
            profile = self.service.profiles.get(snapshot.vessel_profile_id)
            mode = snapshot.control_mode
            self._arrange(mode)
            if mode is ControlMode.SENSOR:
                self.mode_title.setText("SENSOR")
                self.sensor_hdg.set_value(snapshot.heading_deg)
                self.sensor_rot.set_value(snapshot.rate_of_turn_deg_min)
                self.sensor_sog.set_value(snapshot.sog_kn)
                self.sensor_stw.set_value(snapshot.stw_kn)
            else:
                self.mode_title.setText(
                    "SHIP" if mode is ControlMode.SHIP else "TRACK" if mode is ControlMode.TRACK else "EXTERNAL"
                )
                speed_values = [float(value) for _label, value in profile.engine_steps()]
                self.engine.configure("ENGINE", min(speed_values), max(speed_values), 10, "kn")
                self.engine.set_value(snapshot.speed_order_kn)
                self.engine.setEnabled(snapshot.engine_manual_enabled)
                self.bow_thruster.update_snapshot(snapshot, profile)
                self.engine.set_derived(mode is ControlMode.TRACK and snapshot.use_planned_speed)
                if mode in {ControlMode.SHIP, ControlMode.EXTERNAL_RUDDER}:
                    self.rudder.configure("RUDDER", -profile.max_rudder_deg, profile.max_rudder_deg, 10, "°")
                    self.rudder.set_value(snapshot.rudder_order_starboard_deg)
                    self.rudder.setEnabled(mode is ControlMode.SHIP or snapshot.external_rudder_manual_enabled)
                elif mode is ControlMode.TRACK:
                    self._track_mode = "fixed_radius" if snapshot.control_turn_mode == "fixed_radius" else "fixed_rot"
                    minimum = radius_from_rot(profile.max_speed_kn, profile.max_rot_deg_min)
                    self.track_rot.configure("RATE", 0.1, profile.max_rot_deg_min, 10, "°")
                    self.track_radius.configure("RADIUS", minimum, max(20.0, minimum * 50.0), 100, "NM")
                    speed = abs(snapshot.sog_kn)
                    if speed <= 0.05:
                        speed = abs(snapshot.speed_order_kn)
                    rate = max(0.1, min(profile.max_rot_deg_min, snapshot.control_turn_rate_deg_min))
                    radius = max(minimum, snapshot.control_turn_radius_nm)
                    if speed > 0.01:
                        if self._track_mode == "fixed_radius":
                            rate = max(0.1, min(profile.max_rot_deg_min, rot_from_radius(speed, radius)))
                        else:
                            radius = max(minimum, radius_from_rot(speed, rate))
                    self.track_rot.set_value(rate)
                    self.track_radius.set_value(radius)
                    self.track_rot.set_derived(snapshot.use_planned_turns or self._track_mode == "fixed_radius")
                    self.track_radius.set_derived(snapshot.use_planned_turns or self._track_mode != "fixed_radius")
        finally:
            self._updating = False

    def _emit_sensor(self, **values) -> None:
        if not self._updating:
            self.command_submitted.emit(SetSensorValues(**values))

    def _sensor_hdg_changed(self, value: float) -> None: self._emit_sensor(heading_deg=value)
    def _sensor_rot_changed(self, value: float) -> None: self._emit_sensor(rot_deg_min=value)
    def _sensor_sog_changed(self, value: float) -> None: self._emit_sensor(sog_kn=value)
    def _sensor_stw_changed(self, value: float) -> None: self._emit_sensor(stw_kn=value)

    def _engine_changed_compact(self, value: float) -> None:
        if self._updating:
            return
        if self.service.snapshot().control_mode is ControlMode.TRACK:
            self.command_submitted.emit(SetRouteOptions(use_planned_speed=False))
        self.command_submitted.emit(SetSpeedOrder(value))

    def _rudder_changed_compact(self, value: float) -> None:
        if not self._updating:
            self.command_submitted.emit(SetRudderOrder(value))

    def _track_rot_changed(self, value: float) -> None:
        if not self._updating:
            self.command_submitted.emit(SetRouteOptions(
                use_planned_turns=False,
                control_turn_mode="fixed_rot",
                control_turn_rate_deg_min=value,
            ))

    def _track_radius_changed(self, value: float) -> None:
        if not self._updating:
            self.command_submitted.emit(SetRouteOptions(
                use_planned_turns=False,
                control_turn_mode="fixed_radius",
                control_turn_radius_nm=value,
            ))
