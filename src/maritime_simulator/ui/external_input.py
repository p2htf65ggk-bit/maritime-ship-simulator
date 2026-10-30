# SPDX-License-Identifier: MPL-2.0
from dataclasses import asdict

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QComboBox, QFormLayout, QGridLayout, QLabel, QLineEdit, QSpinBox, QTabWidget, QVBoxLayout, QWidget, QPushButton

from maritime_simulator.domain.external_control import ACTUATOR_PROTOCOLS
from maritime_simulator.protocols.external_control import XDR_PERCENT_UNITS
from .dialogs import show_warning
from .rudder_endpoint import RudderEndpointEditor
from .control_sources import ControlSourceSelector
from .content_tabs import ContentSizedTabs


class ActuatorInputPanel(QWidget):
    """Propulsion channels share the rudder endpoint editor and runtime."""

    def __init__(self, runtime, actuator, on_changed, show_monitor, parent=None):
        super().__init__(parent)
        self.runtime, self.actuator, self.on_changed = runtime, actuator, on_changed
        self._loading = True
        primary = ACTUATOR_PROTOCOLS[actuator]
        title = "Bow Thruster" if actuator == "bow_thruster" else "Engine Telegraph"
        root = QVBoxLayout(self)
        self.policy = QComboBox()
        for label, value in (("Manual", "MANUAL"), (primary, primary), ("XDR", "XDR"),
                             (f"Prio {primary}, XDR", f"PRIO_{primary}_XDR"),
                             (f"Prio XDR, {primary}", f"PRIO_XDR_{primary}")):
            self.policy.addItem(label, value)
        self.policy.setToolTip("Sources expire after 2.5 s. Priority returns on the first valid preferred sentence.")
        self.policy.currentIndexChanged.connect(self._save)
        self.policy.setParent(self)
        self.policy.hide()
        self.sources = ControlSourceSelector((primary, "XDR"))
        self.sources.changed.connect(self._sources_changed)
        root.addWidget(self.sources)
        self.tabs = ContentSizedTabs()
        self.editors = {}
        self.number = QSpinBox()
        self.number.setRange(1, 9)
        self.number.editingFinished.connect(self._save)
        self.xdr_fields = {}
        for channel in ("primary", "xdr"):
            page = QWidget()
            layout = QVBoxLayout(page)
            options = QFormLayout()
            if channel == "primary":
                options.addRow("Thruster number" if actuator == "bow_thruster" else "Engine / shaft number", self.number)
                text = ("Expects TRC number 1 by default; RPM demand, mode P (-100 to +100%) "
                        "or R (signed RPM, converted using the vessel's rated RPM). Accepts valid C/R reports. "
                        "Pitch and azimuth are not used. Positive = bow to starboard."
                        if actuator == "bow_thruster" else
                        "Expects PRC number 1 by default; lever demand position -100 to +100%, status A. "
                        "Positive = ahead; negative = astern. RPM and pitch fields are not used.")
            else:
                for key, label in (("xdr_type", "Type"), ("xdr_unit", "Unit"), ("xdr_id", "ID")):
                    if key == "xdr_id":
                        field = QLineEdit()
                        field.setMaxLength(32)
                        field.editingFinished.connect(self._save)
                    else:
                        field = QComboBox()
                        field.setMinimumContentsLength(12)
                        field.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                        if key == "xdr_type":
                            labels = {"A": "A - Angular displacement", "G": "G - Generic (legacy)"}
                            for value in XDR_PERCENT_UNITS:
                                field.addItem(labels[value], value)
                            field.currentIndexChanged.connect(self._xdr_type_changed)
                        else:
                            field.currentIndexChanged.connect(self._save)
                    self.xdr_fields[key] = field
                    options.addRow(label, field)
                self._update_xdr_units()
                text = "Matches Type, Unit and ID exactly. A/P is the default; G/P and G with a null (empty) Unit are supported. Both use -100 to +100% of full range; positive = " + (
                    "bow to starboard." if actuator == "bow_thruster" else "ahead.")
            layout.addLayout(options)
            hint = QLabel(text)
            hint.setWordWrap(True)
            hint.setObjectName("secondaryText")
            layout.addWidget(hint)
            editor = RudderEndpointEditor()
            editor.changed.connect(self._save)
            self.editors[channel] = editor
            layout.addWidget(editor)
            self.tabs.addTab(page, f"{primary if channel == 'primary' else 'XDR'} - {title}")
        root.addWidget(self.tabs)
        signal_title = QLabel(f"{title} input indications")
        signal_title.setObjectName("sectionTitle")
        root.addWidget(signal_title)
        self.input_lamps = {}
        signals = QGridLayout()
        signals.setHorizontalSpacing(10)
        signals.setVerticalSpacing(6)
        for index, protocol in enumerate((primary, "XDR")):
            lamp = QLabel()
            lamp.setObjectName("rudderInputLamp")
            lamp.setAccessibleName(f"{title} {protocol} input indication")
            signals.addWidget(lamp, 0, index * 3, Qt.AlignCenter)
            signals.addWidget(QLabel(protocol), 0, index * 3 + 1)
            signals.setColumnMinimumWidth(index * 3 + 2, 20)
            self.input_lamps[protocol] = lamp
        signals.setColumnStretch(6, 1)
        root.addLayout(signals)
        legend = QLabel("Green: valid 0-5 s | Grey: field empty | Yellow: >5-30 s | Red: >30 s or never received")
        legend.setObjectName("secondaryText")
        legend.setWordWrap(True)
        root.addWidget(legend)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("dataReadout")
        root.addWidget(self.status)
        timeout = QLabel("Main and fallback inputs expire after 2.5 s. Manual unlocks the control; Freeze holds the last "
                         "command; Zero orders zero. Main resumes on its next valid sentence.")
        timeout.setWordWrap(True)
        timeout.setObjectName("secondaryText")
        root.addWidget(timeout)
        monitor = QPushButton("View Port")
        monitor.clicked.connect(show_monitor)
        root.addWidget(monitor, 0, Qt.AlignLeft)
        root.addStretch(1)
        self.load()

    def load(self):
        self._loading = True
        try:
            settings = getattr(self.runtime.settings, self.actuator)
            self.policy.setCurrentIndex(max(0, self.policy.findData(settings.policy)))
            self.sources.load(settings.policy, settings.on_loss)
            self.number.setValue(settings.number)
            type_field = self.xdr_fields["xdr_type"]
            type_field.setCurrentIndex(type_field.findData(settings.xdr_type))
            self._update_xdr_units()
            for key, field in self.xdr_fields.items():
                value = getattr(settings, key)
                if isinstance(field, QComboBox):
                    field.setCurrentIndex(field.findData(value))
                else:
                    field.setText(value)
            for key, editor in self.editors.items():
                editor.load(getattr(settings, key))
        finally:
            self._loading = False
        self.refresh()

    def _update_xdr_units(self):
        unit_field = self.xdr_fields["xdr_unit"]
        previous = unit_field.currentData()
        with QSignalBlocker(unit_field):
            unit_field.clear()
            for value in XDR_PERCENT_UNITS.get(self.xdr_fields["xdr_type"].currentData(), ()):
                unit_field.addItem("P - Percent of full range" if value == "P" else "(null field) - Percent of full range", value)
            unit_field.setCurrentIndex(max(0, unit_field.findData(previous)))

    def _xdr_type_changed(self, *_args):
        self._update_xdr_units()
        self._save()

    def _sources_changed(self):
        policy, _loss = self.sources.values()
        with QSignalBlocker(self.policy):
            self.policy.setCurrentIndex(self.policy.findData(policy))
        self._save()

    def _save(self, *_args):
        if self._loading:
            return
        try:
            values = asdict(getattr(self.runtime.settings, self.actuator))
            values.update(policy=self.policy.currentData(), number=self.number.value())
            values["on_loss"] = self.sources.values()[1]
            values.update({key: editor.values() for key, editor in self.editors.items()})
            values.update({key: field.currentData() if isinstance(field, QComboBox) else field.text().strip()
                           for key, field in self.xdr_fields.items()})
            self.runtime.stop()
            self.runtime.update(**{self.actuator: values})
        except (OSError, TypeError, ValueError) as error:
            show_warning(self, "Invalid external input setting", str(error))
        finally:
            self.runtime.ensure_started()
            self.load()
        self.on_changed()

    def refresh(self):
        for channel in self.runtime.actuator_channel_statuses(self.actuator):
            lamp = self.input_lamps[channel.protocol]
            if lamp.property("signalState") != channel.state:
                lamp.setProperty("signalState", channel.state)
                lamp.style().unpolish(lamp)
                lamp.style().polish(lamp)
                lamp.update()
            age = "never received" if channel.last_seen_age_s is None else f"{channel.last_seen_age_s:.1f} s ago"
            if channel.state == "empty":
                age = "sentence received; field empty"
            lamp.setToolTip(f"{channel.protocol}: {age}")
        snapshot = self.runtime.simulator.snapshot()
        active = snapshot.bow_thruster_active_source if self.actuator == "bow_thruster" else snapshot.engine_active_source
        policy = getattr(self.runtime.settings, self.actuator).policy
        loss = getattr(self.runtime.settings, self.actuator).on_loss
        state = active or ("Manual" if policy == "MANUAL" else f"Waiting / timed out - {loss.title()}")
        self.status.setText(f"Active source: {state}")
        errors = [status.last_error for status in self.runtime.statuses()
                  if self.actuator in status.source_id and status.last_error]
        self.status.setToolTip("\n".join(errors))
