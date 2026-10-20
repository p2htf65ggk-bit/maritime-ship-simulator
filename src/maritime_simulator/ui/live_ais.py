# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QHeaderView,
    QPushButton,
    QStackedWidget,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from maritime_simulator.application.ais import AISRepository, WaterMaskPendingError
from maritime_simulator.transport.live_ais import LiveAISClient

from .dialogs import OpaqueDialog, reserve_label_lines, show_warning


DEFAULT_AISSTREAM_API_KEY = os.environ.get("MARITIME_SIM_AISSTREAM_API_KEY", "")
DEFAULT_BARENTSWATCH_CLIENT_ID = os.environ.get(
    "MARITIME_SIM_BARENTSWATCH_CLIENT_ID",
    "",
)
DEFAULT_BARENTSWATCH_CLIENT_SECRET = os.environ.get(
    "MARITIME_SIM_BARENTSWATCH_CLIENT_SECRET",
    "",
)
AISSTREAM_ACCOUNT_URL = "https://aisstream.io/account"
BARENTSWATCH_REGISTRATION_URL = "https://www.barentswatch.no/minside/"


class AISOutputFilterSwitch(QCheckBox):
    """Compact, accessible two-state switch for the AIS output filter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("")
        self.setFixedSize(46, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("AIS output filter")
        self.setToolTip("Switch between all AIS targets and SOG-dependent output filtering.")

    def sizeHint(self) -> QSize:
        return QSize(46, 24)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QRectF(1.0, 3.0, self.width() - 2.0, self.height() - 6.0)
        if not self.isEnabled():
            track_color = QColor("#354852")
            thumb_color = QColor("#84959d")
        elif self.isChecked():
            track_color = QColor("#11b8d6")
            thumb_color = QColor("#f5fbfd")
        else:
            track_color = QColor("#55717e")
            thumb_color = QColor("#f5fbfd")
        painter.setPen(Qt.NoPen)
        painter.setBrush(track_color)
        radius = track.height() / 2.0
        painter.drawRoundedRect(track, radius, radius)
        diameter = track.height() - 4.0
        thumb_x = track.right() - diameter - 2.0 if self.isChecked() else track.left() + 2.0
        painter.setBrush(thumb_color)
        painter.drawEllipse(QRectF(thumb_x, track.top() + 2.0, diameter, diameter))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#9fe8f4"), 1.0))
            painter.drawRoundedRect(track.adjusted(-0.5, -0.5, 0.5, 0.5), radius, radius)


def _linked_credential_field(editor: QLineEdit, url: str, text: str) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(editor, 1)
    link = QLabel(f'<a href="{url}">{text}</a>')
    link.setOpenExternalLinks(True)
    link.setTextInteractionFlags(Qt.TextBrowserInteraction)
    layout.addWidget(link)
    return row


class AISSettingsDialog(OpaqueDialog):
    settings_saved = Signal()

    def __init__(self, client: LiveAISClient, settings_path: Path, parent=None):
        super().__init__(parent)
        self.client = client
        self.settings_path = settings_path
        self.setWindowTitle("Live AIS Settings")
        self.resize(600, 390)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItem("aisstream.io", LiveAISClient.AISSTREAM)
        self.provider.addItem("Norwegian Coastal Administration (BarentsWatch)", LiveAISClient.BARENTSWATCH)
        self.provider.currentIndexChanged.connect(self._update_provider_fields)
        form.addRow("Provider", self.provider)
        self.api_key_label = QLabel("API key")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.api_key_row = _linked_credential_field(
            self.api_key,
            AISSTREAM_ACCOUNT_URL,
            "Get an aisstream.io key",
        )
        form.addRow(self.api_key_label, self.api_key_row)
        self.client_id_label = QLabel("Client ID")
        self.client_id = QLineEdit()
        form.addRow(self.client_id_label, self.client_id)
        self.client_secret_label = QLabel("Client secret")
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.client_secret_row = _linked_credential_field(
            self.client_secret,
            BARENTSWATCH_REGISTRATION_URL,
            "Register AIS credentials",
        )
        form.addRow(self.client_secret_label, self.client_secret_row)
        self.subscription_range = QDoubleSpinBox()
        self.subscription_range.setRange(1.0, 5000.0)
        self.subscription_range.setSuffix(" NM")
        self.subscription_range_label = QLabel("Subscription range")
        form.addRow(self.subscription_range_label, self.subscription_range)
        self.worldwide = QCheckBox("Worldwide subscription (diagnostics)")
        self.worldwide.setToolTip(
            "Request worldwide live AIS traffic instead of the own-ship range. "
            "This diagnostic mode can create very high network and CPU load."
        )
        form.addRow("", self.worldwide)
        layout.addLayout(form)
        self.note = QLabel()
        self.note.setObjectName("secondaryText")
        self.note.setWordWrap(True)
        reserve_label_lines(self.note, 5)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load()

    def _load(self) -> None:
        data = {}
        try:
            if self.settings_path.exists():
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        provider = str(data.get("provider", LiveAISClient.AISSTREAM))
        self.provider.setCurrentIndex(max(0, self.provider.findData(provider)))
        self.api_key.setText(str(data.get("api_key", DEFAULT_AISSTREAM_API_KEY)))
        self.client_id.setText(str(data.get("barentswatch_client_id") or DEFAULT_BARENTSWATCH_CLIENT_ID))
        self.client_secret.setText(str(data.get("barentswatch_client_secret") or DEFAULT_BARENTSWATCH_CLIENT_SECRET))
        self.subscription_range.setValue(float(data.get("subscription_range_nm", 250.0)))
        self.worldwide.setChecked(bool(data.get("worldwide_debug", False)))
        self._update_provider_fields()
        self._apply_client()

    def _save(self) -> None:
        self.save_settings()
        self.settings_saved.emit()
        self.accept()

    def save_settings(self) -> None:
        self._apply_client()
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.export_state(), indent=2) + "\n", encoding="utf-8")

    def export_state(self) -> dict:
        return {
            "provider": self.provider.currentData(),
            "api_key": self.api_key.text().strip(),
            "barentswatch_client_id": self.client_id.text().strip(),
            "barentswatch_client_secret": self.client_secret.text().strip(),
            "subscription_range_nm": self.subscription_range.value(),
            "worldwide_debug": self.worldwide.isChecked(),
        }

    def apply_state(self, state: dict) -> None:
        provider = str(state.get("provider", LiveAISClient.AISSTREAM))
        self.provider.setCurrentIndex(max(0, self.provider.findData(provider)))
        self.api_key.setText(str(state.get("api_key", DEFAULT_AISSTREAM_API_KEY)))
        self.client_id.setText(str(state.get("barentswatch_client_id") or DEFAULT_BARENTSWATCH_CLIENT_ID))
        self.client_secret.setText(str(state.get("barentswatch_client_secret") or DEFAULT_BARENTSWATCH_CLIENT_SECRET))
        self.subscription_range.setValue(float(state.get("subscription_range_nm", 250.0)))
        self.worldwide.setChecked(bool(state.get("worldwide_debug", False)))
        self._update_provider_fields()
        self._apply_client()

    def _apply_client(self) -> None:
        self.client.provider = str(self.provider.currentData() or LiveAISClient.AISSTREAM)
        self.client.api_key = self.api_key.text().strip()
        self.client.barentswatch_client_id = self.client_id.text().strip()
        self.client.barentswatch_client_secret = self.client_secret.text().strip()
        self.client.subscription_range_nm = self.subscription_range.value()
        self.client.worldwide_debug = self.worldwide.isChecked()

    def _update_provider_fields(self, *_args) -> None:
        provider = str(self.provider.currentData() or LiveAISClient.AISSTREAM)
        uses_api_key = provider == LiveAISClient.AISSTREAM
        uses_client = provider == LiveAISClient.BARENTSWATCH
        for widget in (self.api_key_label, self.api_key_row):
            widget.setVisible(uses_api_key)
        for widget in (self.client_id_label, self.client_id, self.client_secret_label, self.client_secret_row):
            widget.setVisible(uses_client)
        if provider == LiveAISClient.BARENTSWATCH:
            self.subscription_range_label.setText("Local display range")
            self.subscription_range.setToolTip(
                "Filter the Norwegian AIS stream locally to this radius around own ship. "
                "The provider itself remains limited to Norwegian coverage."
            )
            self.worldwide.setText("Show all Norwegian coverage")
            self.worldwide.setToolTip(
                "Disable the local own-ship range filter and show all targets delivered within the provider's "
                "Norwegian coverage area. This is not a worldwide AIS subscription."
            )
            text = (
                "Norwegian Coastal Administration AIS is accessed through the official BarentsWatch live stream. "
                "Range filtering is performed locally; Show all Norwegian coverage disables that local filter. "
                "Enter your client ID and secret here, or provide them through the "
                "MARITIME_SIM_BARENTSWATCH_CLIENT_ID and MARITIME_SIM_BARENTSWATCH_CLIENT_SECRET environment variables."
            )
        else:
            self.subscription_range_label.setText("Subscription range")
            self.subscription_range.setToolTip(
                "Request a geographic AISstream.io subscription around own ship using this radius."
            )
            self.worldwide.setText("Worldwide subscription (high load)")
            self.worldwide.setToolTip(
                "Request a global AISstream.io bounding box instead of the own-ship range. "
                "This can create very high network and CPU load."
            )
            text = (
                "aisstream.io uses an API key and an area subscription around own ship. Worldwide mode is intended "
                "only for diagnostics because it can create very high network and CPU load."
            )
        self.note.setText(text)


SYNTHETIC_TYPES = (
    "Pilot Vessel", "Tug", "Fishing", "Sailing", "Other", "Passenger", "Cargo", "Tanker",
)
SYNTHETIC_TYPE_DIMENSIONS = {
    "Pilot Vessel": (18.0, 6.0, 2.0),
    "Tug": (30.0, 10.0, 4.0),
    "Fishing": (28.0, 8.0, 3.5),
    "Sailing": (14.0, 4.0, 2.2),
    "Other": (75.0, 14.0, 5.0),
    "Passenger": (115.0, 20.0, 6.0),
    "Cargo": (140.0, 22.0, 7.5),
    "Tanker": (190.0, 32.0, 11.0),
}
ENCOUNTER_TYPES = (
    ("Head-on", "head_on"),
    ("Crossing from Starboard", "crossing_from_starboard"),
    ("Crossing from Port", "crossing_from_port"),
    ("Overtaking Own Ship", "overtaking_own_ship"),
    ("Own Ship Overtakes", "own_ship_overtakes"),
    ("Close Passing Port", "close_passing_port"),
    ("Close Passing Starboard", "close_passing_starboard"),
)


class SyntheticTargetDialog(OpaqueDialog):
    def __init__(self, *, values: dict | None = None, parent=None):
        super().__init__(parent)
        values = values or {}
        self.setWindowTitle("Synthetic AIS Target")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.ship_type = QComboBox()
        self.ship_type.addItems(SYNTHETIC_TYPES)
        selected_type = self.ship_type.findText(str(values.get("ship_type", "Cargo")))
        self.ship_type.setCurrentIndex(max(0, selected_type))
        self.bearing = _angle_editor(float(values.get("bearing_deg", 0.0)))
        self.bearing.setToolTip("Relative bearing from own-ship heading; 0 degrees is directly ahead.")
        self.range = QDoubleSpinBox()
        self.range.setRange(0.05, 500.0)
        self.range.setDecimals(2)
        self.range.setSuffix(" NM")
        self.range.setValue(float(values.get("range_nm", 2.0)))
        self.course = _angle_editor(float(values.get("course_deg", 0.0)))
        self.heading = _angle_editor(float(values.get("heading_deg", values.get("course_deg", 0.0))))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.0, 80.0)
        self.speed.setDecimals(1)
        self.speed.setSuffix(" kn")
        self.speed.setValue(float(values.get("speed_kn", 10.0)))
        self.name = QLineEdit(str(values.get("name", "")))
        self.name.setPlaceholderText("Generated automatically when empty")
        form.addRow("Type", self.ship_type)
        form.addRow("Relative bearing", self.bearing)
        form.addRow("Range", self.range)
        form.addRow("Course", self.course)
        form.addRow("Heading", self.heading)
        form.addRow("Speed", self.speed)
        form.addRow("Name (optional)", self.name)
        layout.addLayout(form)

        self.advanced_identity = QCheckBox("Advanced identity and dimensions")
        self.advanced_identity.setChecked(bool(values.get("advanced_identity", False)))
        layout.addWidget(self.advanced_identity)
        self.advanced_panel = QWidget()
        advanced_form = QFormLayout(self.advanced_panel)
        advanced_form.setContentsMargins(18, 0, 0, 0)
        self.mmsi = QSpinBox()
        self.mmsi.setRange(0, 999_999_999)
        self.mmsi.setSpecialValueText("Auto")
        self.mmsi.setValue(int(values.get("mmsi", 0) or 0))
        self.callsign = QLineEdit(str(values.get("callsign", "")))
        self.callsign.setPlaceholderText("Generated automatically when empty")
        dimensions = SYNTHETIC_TYPE_DIMENSIONS.get(self.ship_type.currentText(), (75.0, 14.0, 5.0))
        self.length = _dimension_editor(float(values.get("length_m") or dimensions[0]), " m", 1.0, 500.0)
        self.beam = _dimension_editor(float(values.get("beam_m") or dimensions[1]), " m", 1.0, 100.0)
        self.draught = _dimension_editor(float(values.get("draught_m") or dimensions[2]), " m", 0.1, 30.0)
        advanced_form.addRow("MMSI", self.mmsi)
        advanced_form.addRow("Call sign", self.callsign)
        advanced_form.addRow("Length", self.length)
        advanced_form.addRow("Beam", self.beam)
        advanced_form.addRow("Draught", self.draught)
        self.advanced_panel.setVisible(self.advanced_identity.isChecked())
        self.advanced_identity.toggled.connect(self.advanced_panel.setVisible)
        layout.addWidget(self.advanced_panel)
        note = QLabel(
            "MMSI, call sign and dimensions are generated automatically unless Advanced is enabled."
        )
        note.setObjectName("secondaryText")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        values = {
            "ship_type": self.ship_type.currentText(),
            "bearing_deg": self.bearing.value(),
            "range_nm": self.range.value(),
            "course_deg": self.course.value(),
            "heading_deg": self.heading.value(),
            "speed_kn": self.speed.value(),
            "name": self.name.text().strip(),
        }
        if self.advanced_identity.isChecked():
            if self.mmsi.value() > 0:
                values["mmsi"] = self.mmsi.value()
            values.update(
                callsign=self.callsign.text().strip(),
                length_m=self.length.value(),
                beam_m=self.beam.value(),
                draught_m=self.draught.value(),
            )
        return values


class EncounterDialog(OpaqueDialog):
    def __init__(
        self,
        *,
        own_speed_kn: float,
        suggested_tcpa_min: float = 10.0,
        parent=None,
    ):
        super().__init__(parent)
        self._own_speed_kn = max(0.0, float(own_speed_kn))
        self.setWindowTitle("Synthetic AIS Encounter")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.encounter = QComboBox()
        for label, value in ENCOUNTER_TYPES:
            self.encounter.addItem(label, value)
        self.ship_type = QComboBox()
        self.ship_type.addItems(SYNTHETIC_TYPES)
        self.ship_type.setCurrentText("Cargo")
        self.cpa = QDoubleSpinBox()
        self.cpa.setRange(0.0, 10.0)
        self.cpa.setDecimals(2)
        self.cpa.setSuffix(" NM")
        self.cpa.setValue(0.2)
        self.tcpa = QDoubleSpinBox()
        self.tcpa.setRange(0.5, 120.0)
        self.tcpa.setDecimals(1)
        self.tcpa.setSuffix(" min")
        self.tcpa.setValue(max(0.5, min(120.0, float(suggested_tcpa_min))))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.1, 80.0)
        self.speed.setDecimals(1)
        self.speed.setSuffix(" kn")
        self.encounter.currentIndexChanged.connect(self._apply_encounter_speed_suggestion)
        self._apply_encounter_speed_suggestion()
        form.addRow("Encounter", self.encounter)
        form.addRow("Target type", self.ship_type)
        form.addRow("CPA", self.cpa)
        form.addRow("TCPA", self.tcpa)
        form.addRow("Target speed", self.speed)
        layout.addLayout(form)
        note = QLabel(
            "The initial position and course are calculated from own-ship motion, CPA and TCPA. "
            "The target then proceeds independently."
        )
        note.setObjectName("secondaryText")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_encounter_speed_suggestion(self, *_args) -> None:
        encounter = str(self.encounter.currentData() or "")
        if encounter == "own_ship_overtakes":
            suggested = max(0.1, self._own_speed_kn - 2.0)
        elif encounter == "overtaking_own_ship":
            suggested = max(12.0, self._own_speed_kn + 2.0)
        else:
            suggested = max(12.0, self._own_speed_kn + 2.0)
        self.speed.setValue(suggested)

    def values(self) -> dict:
        return {
            "encounter": str(self.encounter.currentData()),
            "ship_type": self.ship_type.currentText(),
            "cpa_nm": self.cpa.value(),
            "tcpa_min": self.tcpa.value(),
            "target_speed_kn": self.speed.value(),
        }


class SyntheticTrafficPanel(QWidget):
    targets_changed = Signal()

    def __init__(self, repository: AISRepository, snapshot, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.snapshot = snapshot
        self._pending_creations = []
        self.creation_timer = QTimer(self)
        self.creation_timer.setInterval(150)
        self.creation_timer.timeout.connect(self._retry_creations)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        actions = QHBoxLayout()
        add_target = QPushButton("Add Target")
        add_target.clicked.connect(self.add_target)
        add_encounter = QPushButton("Add Encounter")
        add_encounter.clicked.connect(self.add_encounter)
        actions.addWidget(add_target)
        actions.addWidget(add_encounter)
        layout.addLayout(actions)
        self.creation_status = QLabel()
        self.creation_status.setObjectName("secondaryText")
        self.creation_status.setWordWrap(True)
        reserve_label_lines(self.creation_status, 2)
        layout.addWidget(self.creation_status)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(("Name", "Type", "BRG", "RNG", "COG", "HDG", "SOG"))
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.edit_selected)
        layout.addWidget(self.table)
        row_actions = QGridLayout()
        row_actions.setHorizontalSpacing(6)
        row_actions.setVerticalSpacing(6)
        for index, (text, callback) in enumerate((
            ("Edit", self.edit_selected),
            ("Duplicate", self.duplicate_selected),
            ("Remove", self.remove_selected),
            ("Clear All", self.clear_all),
        )):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row_actions.addWidget(button, index // 2, index % 2)
        row_actions.setColumnStretch(0, 1)
        row_actions.setColumnStretch(1, 1)
        layout.addLayout(row_actions)
        self.note = QLabel(
            "Right-click the 2D map to add a target. Drag a target to move it; drag its vector handle "
            "to change course, heading and speed together."
        )
        self.note.setObjectName("secondaryText")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.refresh()

    def add_target(self) -> None:
        own = self.snapshot()
        target_number = len(self.repository.synthetic_targets())
        dialog = SyntheticTargetDialog(
            values={
                "bearing_deg": (30.0 + target_number * 47.0) % 360.0,
                "range_nm": 2.0 + 0.6 * (target_number // 8),
                "course_deg": own.heading_deg,
                "heading_deg": own.heading_deg,
            },
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._create_target(dialog.values())

    def add_target_at(self, latitude_deg: float, longitude_deg: float) -> None:
        own = self.snapshot()
        bearing = _bearing_deg(own.latitude_deg, own.longitude_deg, latitude_deg, longitude_deg)
        distance = _distance_nm(own.latitude_deg, own.longitude_deg, latitude_deg, longitude_deg)
        dialog = SyntheticTargetDialog(
            values={
                "bearing_deg": (bearing - own.heading_deg) % 360.0,
                "range_nm": distance,
                "course_deg": own.heading_deg,
                "heading_deg": own.heading_deg,
            },
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        values.update(latitude_deg=float(latitude_deg), longitude_deg=float(longitude_deg))
        self._create_target(values)

    def add_encounter(self) -> None:
        own = self.snapshot()
        existing_count = len(self.repository.synthetic_targets())
        dialog = EncounterDialog(
            own_speed_kn=own.sog_kn,
            suggested_tcpa_min=min(120.0, 10.0 + 4.0 * existing_count),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._attempt_creation(lambda: self.repository.add_encounter(own, **values), "Encounter could not be created")

    def edit_selected(self, *_args) -> None:
        mmsi = self._selected_mmsi()
        if mmsi is None:
            return
        target = next((item for item in self.repository.synthetic_targets() if item.mmsi == mmsi), None)
        if target is None:
            return
        own = self.snapshot()
        bearing = _bearing_deg(own.latitude_deg, own.longitude_deg, target.latitude_deg, target.longitude_deg)
        dialog = SyntheticTargetDialog(
            values={
                "name": target.name,
                "ship_type": target.ship_type,
                "bearing_deg": (bearing - own.heading_deg) % 360.0,
                "range_nm": _distance_nm(own.latitude_deg, own.longitude_deg, target.latitude_deg, target.longitude_deg),
                "course_deg": target.cog_deg,
                "heading_deg": target.heading_deg,
                "speed_kn": target.sog_kn,
                "advanced_identity": True,
                "mmsi": target.mmsi,
                "callsign": target.callsign,
                "length_m": target.length_m,
                "beam_m": target.beam_m,
                "draught_m": target.draught_m,
            },
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        updated_mmsi = int(values.pop("mmsi", mmsi))
        latitude, longitude = _destination_point(
            own.latitude_deg,
            own.longitude_deg,
            own.heading_deg + values.pop("bearing_deg"),
            values.pop("range_nm"),
        )
        try:
            self.repository.update_synthetic_target(
                mmsi,
                latitude_deg=latitude,
                longitude_deg=longitude,
                new_mmsi=updated_mmsi,
                **values,
            )
        except (KeyError, TypeError, ValueError) as error:
            show_warning(self, "Target could not be updated", str(error))
            return
        self._changed()

    def duplicate_selected(self) -> None:
        mmsi = self._selected_mmsi()
        if mmsi is None:
            return
        try:
            self.repository.duplicate_synthetic_target(mmsi, self.snapshot())
        except (KeyError, ValueError) as error:
            show_warning(self, "Target could not be duplicated", str(error))
            return
        self._changed()

    def remove_selected(self) -> None:
        mmsi = self._selected_mmsi()
        if mmsi is None:
            return
        self.repository.remove_synthetic_target(mmsi)
        self._changed()

    def clear_all(self) -> None:
        self.creation_timer.stop()
        self._pending_creations.clear()
        self.creation_status.clear()
        self.repository.clear_synthetic_targets()
        self._changed()

    def move_target(self, mmsi: int, latitude_deg: float, longitude_deg: float) -> None:
        try:
            self.repository.update_synthetic_target(
                mmsi,
                latitude_deg=latitude_deg,
                longitude_deg=longitude_deg,
            )
        except (KeyError, ValueError) as error:
            show_warning(self, "Target could not be moved", str(error))
            return
        self._changed()

    def change_vector(self, mmsi: int, course_deg: float, speed_kn: float) -> None:
        try:
            self.repository.update_synthetic_target(
                mmsi,
                course_deg=course_deg,
                heading_deg=course_deg,
                speed_kn=speed_kn,
            )
        except (KeyError, ValueError) as error:
            show_warning(self, "Target vector could not be changed", str(error))
            return
        self._changed()

    def refresh(self) -> None:
        selected = self._selected_mmsi()
        own = self.snapshot()
        targets = self.repository.synthetic_targets()
        self.table.setRowCount(len(targets))
        selected_row = -1
        for row, target in enumerate(targets):
            bearing = (_bearing_deg(
                own.latitude_deg, own.longitude_deg, target.latitude_deg, target.longitude_deg
            ) - own.heading_deg) % 360.0
            values = (
                target.name,
                target.ship_type,
                f"{bearing:05.1f}°",
                f"{_distance_nm(own.latitude_deg, own.longitude_deg, target.latitude_deg, target.longitude_deg):.2f} NM",
                f"{target.cog_deg:05.1f}°",
                f"{target.heading_deg:05.1f}°",
                f"{target.sog_kn:.1f} kn",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, target.mmsi)
                self.table.setItem(row, column, item)
            if target.mmsi == selected:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)

    def _create_target(self, values: dict) -> None:
        own = self.snapshot()
        values = dict(values)
        self._attempt_creation(lambda: self.repository.add_synthetic_target(own, **values), "Target could not be created")

    def _attempt_creation(self, callback, title):
        try:
            callback()
        except WaterMaskPendingError:
            if len(self._pending_creations) >= 16:
                self.creation_status.setText("Water checks are busy. Please wait before adding more targets.")
                return
            self._pending_creations.append((callback, time.monotonic() + 8, self.repository.mode))
            self.creation_status.setText(f"Preparing water data for {len(self._pending_creations)} requested target(s)...")
            self.creation_timer.start()
            return
        except (KeyError, TypeError, ValueError) as error:
            show_warning(self, title, str(error))
            return
        self.creation_status.setText("Target added.")
        self._changed()

    def _retry_creations(self):
        remaining, added, failed = [], 0, []
        for callback, deadline, mode in self._pending_creations:
            if mode != self.repository.mode:
                failed.append("Request cancelled after changing AIS mode.")
                continue
            if time.monotonic() >= deadline:
                failed.append("Water check timed out. Check chart coverage and try again.")
                continue
            try:
                callback()
                added += 1
            except WaterMaskPendingError:
                remaining.append((callback, deadline, mode))
            except (KeyError, TypeError, ValueError) as error:
                failed.append(str(error))
        self._pending_creations = remaining
        if added:
            self._changed()
        if not remaining:
            self.creation_timer.stop()
        self.creation_status.setText(
            (f"Preparing water data for {len(remaining)} requested target(s)..." if remaining else "Target added." if added else "")
            + (f"\n{failed[-1]}" if failed else "")
        )

    def _selected_mmsi(self) -> int | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.UserRole)) if item is not None else None

    def _changed(self) -> None:
        self.refresh()
        self.targets_changed.emit()


def _angle_editor(value: float) -> QDoubleSpinBox:
    editor = QDoubleSpinBox()
    editor.setRange(0.0, 359.9)
    editor.setDecimals(1)
    editor.setWrapping(True)
    editor.setSuffix("°")
    editor.setValue(float(value) % 360.0)
    return editor


def _dimension_editor(value: float, suffix: str, minimum: float, maximum: float) -> QDoubleSpinBox:
    editor = QDoubleSpinBox()
    editor.setRange(minimum, maximum)
    editor.setDecimals(1)
    editor.setSuffix(suffix)
    editor.setValue(value)
    return editor


def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    first = math.radians(lat1)
    second = math.radians(lat2)
    d_latitude = math.radians(lat2 - lat1)
    d_longitude = math.radians(lon2 - lon1)
    value = math.sin(d_latitude / 2.0) ** 2 + math.cos(first) * math.cos(second) * math.sin(d_longitude / 2.0) ** 2
    return 3440.065 * 2.0 * math.asin(min(1.0, math.sqrt(value)))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    first = math.radians(lat1)
    second = math.radians(lat2)
    difference = math.radians(lon2 - lon1)
    y = math.sin(difference) * math.cos(second)
    x = math.cos(first) * math.sin(second) - math.sin(first) * math.cos(second) * math.cos(difference)
    return math.degrees(math.atan2(y, x)) % 360.0


def _destination_point(latitude: float, longitude: float, bearing_deg: float, distance_nm: float) -> tuple[float, float]:
    angular = float(distance_nm) / 3440.065
    bearing = math.radians(float(bearing_deg))
    first_latitude = math.radians(float(latitude))
    first_longitude = math.radians(float(longitude))
    second_latitude = math.asin(
        math.sin(first_latitude) * math.cos(angular)
        + math.cos(first_latitude) * math.sin(angular) * math.cos(bearing)
    )
    second_longitude = first_longitude + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(first_latitude),
        math.cos(angular) - math.sin(first_latitude) * math.sin(second_latitude),
    )
    return math.degrees(second_latitude), (math.degrees(second_longitude) + 540.0) % 360.0 - 180.0


class AISControlPanel(QWidget):
    output_selection_changed = Signal()

    def __init__(self, repository: AISRepository, snapshot, settings_path: Path, parent=None, *, water_status=None):
        super().__init__(parent)
        self.repository = repository
        self.snapshot = snapshot
        self.water_status = water_status
        self.client = LiveAISClient(repository, snapshot, self)
        self.settings_dialog = AISSettingsDialog(self.client, settings_path, self)
        self.settings_dialog.settings_saved.connect(self._settings_saved)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        modes = QHBoxLayout()
        self.generated = QPushButton("Generated")
        self.live = QPushButton("Live")
        self.synthetic = QPushButton("Synthetic")
        self.generated.setToolTip("Generated AIS")
        self.live.setToolTip("Live AIS")
        self.synthetic.setToolTip("Synthetic Traffic Generator")
        for button in (self.generated, self.live, self.synthetic):
            button.setCheckable(True)
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.generated)
        group.addButton(self.live)
        group.addButton(self.synthetic)
        self.generated.setChecked(True)
        self.generated.clicked.connect(self._use_generated)
        self.live.clicked.connect(self._use_live)
        self.synthetic.clicked.connect(self._use_synthetic)
        modes.addWidget(self.generated)
        modes.addWidget(self.live)
        modes.addWidget(self.synthetic)
        layout.addLayout(modes)
        self.avoid_land = QCheckBox("Avoid Land")
        self.avoid_land.setChecked(repository.avoid_land)
        self.avoid_land.setToolTip(
            "Use water-map data for Generated and Synthetic AIS placement and land avoidance. "
            "Switch off for offline operation or unrestricted targets; targets may cross land. "
            "This setting does not affect Live AIS."
        )
        self.avoid_land.toggled.connect(self._avoid_land_changed)
        layout.addWidget(self.avoid_land)
        self.mode_stack = QStackedWidget()
        self.regular_panel = QWidget()
        regular_layout = QVBoxLayout(self.regular_panel)
        regular_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.target_count = QSpinBox()
        self.target_count.setRange(0, 5000)
        self.target_count.setValue(repository.generated_target_count)
        self.active_range = QDoubleSpinBox()
        self.active_range.setRange(0.1, 5000.0)
        self.active_range.setSuffix(" NM")
        self.active_range.setValue(repository.active_range_nm)
        self.map_limit = QSpinBox()
        self.map_limit.setRange(1, 10000)
        self.map_limit.setValue(repository.map_target_limit)
        self.map_range = QDoubleSpinBox()
        self.map_range.setRange(0.1, 10000.0)
        self.map_range.setSuffix(" NM")
        self.map_range.setValue(repository.map_range_nm)
        self.output_filter_all_label = QLabel("All")
        self.output_filter_switch = AISOutputFilterSwitch()
        self.output_filter_sog_label = QLabel("SOG dependent")
        filter_row = QWidget()
        filter_layout = QHBoxLayout(filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(9)
        filter_layout.addWidget(self.output_filter_all_label)
        filter_layout.addWidget(self.output_filter_switch)
        filter_layout.addWidget(self.output_filter_sog_label)
        filter_layout.addStretch(1)
        self.output_filter_switch.setChecked(repository.output_filter_mode == "sog_dependent")
        self.output_filter_switch.toggled.connect(self._output_filter_changed)
        self.output_min_sog = QDoubleSpinBox()
        self.output_min_sog.setRange(0.0, 102.2)
        self.output_min_sog.setDecimals(1)
        self.output_min_sog.setSingleStep(0.1)
        self.output_min_sog.setSuffix(" kn")
        self.output_min_sog.setValue(repository.output_min_sog_kn)
        self.output_min_sog.setToolTip(
            "In SOG dependent mode, only AIS targets with SOG strictly greater "
            "than this value are sent."
        )
        self.output_min_sog.valueChanged.connect(self._output_filter_changed)
        for field in (self.target_count, self.active_range, self.map_range, self.map_limit):
            field.valueChanged.connect(self._limits_changed)
        form.addRow("Visible/output target limit", self.target_count)
        form.addRow("Active output range", self.active_range)
        form.addRow("Map display range", self.map_range)
        form.addRow("Map target limit", self.map_limit)
        form.addRow("AIS output filter", filter_row)
        form.addRow("Minimum SOG", self.output_min_sog)
        regular_layout.addLayout(form)
        buttons = QHBoxLayout()
        settings = QPushButton("Live AIS Settings")
        settings.clicked.connect(self.settings_dialog.exec)
        reconnect = QPushButton("Reconnect")
        reconnect.clicked.connect(self.client.connect_live)
        buttons.addWidget(settings)
        buttons.addWidget(reconnect)
        regular_layout.addLayout(buttons)
        self.status = QLabel("Generated AIS active")
        self.status.setObjectName("dataReadout")
        self.status.setWordWrap(True)
        reserve_label_lines(self.status, 3, padding=16)
        self.client.status_changed.connect(self._client_status_changed)
        regular_layout.addWidget(self.status)
        self.diagnostics = QLabel("Provider: aisstream.io | Transport: Disconnected | Subscription: Not sent | Frames: 0 | Valid: 0")
        self.diagnostics.setObjectName("secondaryText")
        self.diagnostics.setWordWrap(True)
        reserve_label_lines(self.diagnostics, 5)
        self.client.diagnostics_changed.connect(self._show_diagnostics)
        regular_layout.addWidget(self.diagnostics)
        hint = QLabel(
            "A connected socket only confirms the WebSocket handshake. The frame and valid-report counters "
            "below confirm whether the provider is actually delivering usable AIS positions."
        )
        hint.setObjectName("secondaryText")
        hint.setWordWrap(True)
        reserve_label_lines(hint, 4)
        regular_layout.addWidget(hint)
        regular_layout.addStretch(1)
        self.synthetic_panel = SyntheticTrafficPanel(repository, snapshot, self)
        self.synthetic_panel.targets_changed.connect(self._synthetic_targets_changed)
        self.mode_stack.addWidget(self.regular_panel)
        self.mode_stack.addWidget(self.synthetic_panel)
        layout.addWidget(self.mode_stack)
        self._sync_output_filter_controls()

    def _use_generated(self) -> None:
        self.avoid_land.setEnabled(True)
        self.client.disconnect_live()
        self.repository.use_generated(self.snapshot())
        self.mode_stack.setCurrentWidget(self.regular_panel)
        self.status.setText("Generated AIS active")
        self.output_selection_changed.emit()

    def _use_live(self) -> None:
        self.avoid_land.setEnabled(False)
        self.mode_stack.setCurrentWidget(self.regular_panel)
        self.repository.ingest_live((), replace_all=True)
        self.client.connect_live()
        self.output_selection_changed.emit()

    def _use_synthetic(self) -> None:
        self.avoid_land.setEnabled(True)
        self.client.disconnect_live()
        self.repository.use_synthetic(self.snapshot())
        self.mode_stack.setCurrentWidget(self.synthetic_panel)
        self.synthetic_panel.refresh()
        self.output_selection_changed.emit()

    def _synthetic_targets_changed(self) -> None:
        self.output_selection_changed.emit()

    def _avoid_land_changed(self, enabled: bool) -> None:
        self.repository.configure(avoid_land=enabled)
        self.repository.update_generated(self.snapshot())
        self.output_selection_changed.emit()

    def update_snapshot(self) -> None:
        if self.repository.mode == "synthetic":
            self.synthetic_panel.refresh()
        elif self.repository.mode == "generated":
            count = len(self.repository.all_targets())
            status = f"Generated AIS: {count}/{self.repository.generated_target_count} targets"
            if self.repository.avoid_land and self.water_status is not None:
                status += "\n" + self.water_status()
            if self.status.text() != status:
                self.status.setText(status)

    def _client_status_changed(self, status: str) -> None:
        if self.live.isChecked():
            self.status.setText(status)

    def _settings_saved(self) -> None:
        if self.live.isChecked():
            self.repository.ingest_live((), replace_all=True)
            self.client.connect_live()

    def _show_diagnostics(self, values: dict) -> None:
        frame_age = values.get("last_frame_age_s")
        target_age = values.get("last_target_age_s")
        prediction_age = float(values.get("dead_reckoning_age_s") or 0.0)
        oldest_provider_age = float(values.get("oldest_provider_report_age_s") or 0.0)
        maximum_provider_interval = float(values.get("maximum_provider_interval_s") or 0.0)
        frame_text = "never" if frame_age is None else f"{float(frame_age):.0f} s ago"
        target_text = "never" if target_age is None else f"{float(target_age):.0f} s ago"
        ping = values.get("ping_ms")
        ping_text = "--" if ping is None else f"{int(ping)} ms"
        self.diagnostics.setText(
            f"Provider: {values.get('provider', '--')}  |  Transport: {values.get('socket', '--')}  |  "
            f"Subscription: {values.get('subscription', '--')}  |  "
            f"Compression: {values.get('compression', '--')}\n"
            f"Scope: {values.get('scope', '--')}\n"
            f"Frames: {values.get('frames', 0)}  |  Valid: {values.get('valid_reports', 0)}  |  "
            f"Discarded: {values.get('discarded_frames', 0)}  |  Targets: {values.get('targets', 0)}\n"
            f"Last frame: {frame_text}  |  Last valid target: {target_text}  |  Ping: {ping_text}  |  "
            f"Type: {values.get('message_type', '--')}\n"
            f"Moving: {values.get('moving_targets', 0)}  |  Over 60 s old: "
            f"{values.get('stale_moving_targets', 0)}  |  Oldest real report: {oldest_provider_age:.0f} s  |  "
            f"Longest observed interval: {maximum_provider_interval:.0f} s  |  DR age: {prediction_age:.0f} s"
        )

    def _limits_changed(self) -> None:
        self.repository.configure(
            target_count=self.target_count.value(),
            active_range_nm=self.active_range.value(),
            map_range_nm=self.map_range.value(),
            map_target_limit=self.map_limit.value(),
        )
        self.output_selection_changed.emit()

    def _selected_output_filter_mode(self) -> str:
        return "sog_dependent" if self.output_filter_switch.isChecked() else "all"

    def _sync_output_filter_controls(self) -> None:
        sog_dependent = self._selected_output_filter_mode() == "sog_dependent"
        self.output_min_sog.setEnabled(sog_dependent)
        for label, active in (
            (self.output_filter_all_label, not sog_dependent),
            (self.output_filter_sog_label, sog_dependent),
        ):
            font = label.font()
            font.setBold(active)
            label.setFont(font)

    def _output_filter_changed(self, *_args) -> None:
        self._sync_output_filter_controls()
        self.repository.configure(
            output_filter_mode=self._selected_output_filter_mode(),
            output_min_sog_kn=self.output_min_sog.value(),
        )
        self.output_selection_changed.emit()

    def export_state(self) -> dict:
        return {
            "mode": self.repository.mode,
            "avoid_land": self.avoid_land.isChecked(),
            "target_count": self.target_count.value(),
            "active_range_nm": self.active_range.value(),
            "map_range_nm": self.map_range.value(),
            "map_target_limit": self.map_limit.value(),
            "output_filter_mode": self._selected_output_filter_mode(),
            "output_min_sog_kn": self.output_min_sog.value(),
            "synthetic_targets": self.repository.synthetic_payload(),
            "live": self.settings_dialog.export_state(),
        }

    def apply_state(self, state: dict) -> None:
        self.avoid_land.blockSignals(True)
        self.avoid_land.setChecked(bool(state.get("avoid_land", True)))
        self.avoid_land.blockSignals(False)
        self.repository.configure(avoid_land=self.avoid_land.isChecked())
        for field, value in (
            (self.target_count, int(state.get("target_count", 40))),
            (self.active_range, float(state.get("active_range_nm", 40.0))),
            (self.map_range, float(state.get("map_range_nm", 2500.0))),
            (self.map_limit, int(state.get("map_target_limit", 1000))),
        ):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)
        filter_mode = str(state.get("output_filter_mode", "all"))
        self.output_filter_switch.blockSignals(True)
        self.output_filter_switch.setChecked(filter_mode == "sog_dependent")
        self.output_filter_switch.blockSignals(False)
        self.output_min_sog.blockSignals(True)
        self.output_min_sog.setValue(float(state.get("output_min_sog_kn", 0.5)))
        self.output_min_sog.blockSignals(False)
        self._output_filter_changed()
        self.settings_dialog.apply_state(dict(state.get("live", {})))
        try:
            self.repository.apply_synthetic_payload(
                state.get("synthetic_targets", ()),
                self.snapshot(),
            )
        except (KeyError, TypeError, ValueError):
            self.repository.clear_synthetic_targets()
        self._limits_changed()
        mode = str(state.get("mode", "generated"))
        if mode == "live":
            self.live.setChecked(True)
            self._use_live()
        elif mode == "synthetic":
            self.synthetic.setChecked(True)
            self._use_synthetic()
        else:
            self.generated.setChecked(True)
            self._use_generated()

    def close(self) -> None:
        self.client.disconnect_live()
        self.synthetic_panel.creation_timer.stop()
        self.synthetic_panel._pending_creations.clear()
        super().close()
