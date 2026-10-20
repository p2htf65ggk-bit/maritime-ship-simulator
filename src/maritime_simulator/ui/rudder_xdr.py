# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from maritime_simulator.protocols.external_control import XDR_RUDDER_UNITS
from .rudder_endpoint import RudderEndpointEditor


class RudderXdrEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = True
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.type = QComboBox()
        self.type.addItem("A - Angular displacement", "A")
        self.type.addItem("G - Generic (legacy)", "G")
        self.type.currentIndexChanged.connect(self._type_changed)
        self.unit = QComboBox()
        self.unit.currentIndexChanged.connect(self._changed)
        self.identifier = QLineEdit()
        self.port_id = QLineEdit()
        self.port_id.setPlaceholderText("Empty for a single rudder")
        for field in (self.identifier, self.port_id):
            field.setMaxLength(32)
            field.editingFinished.connect(self._changed)
        form.addRow("Type", self.type)
        form.addRow("Unit", self.unit)
        form.addRow("Single / STBD ID", self.identifier)
        form.addRow("PORT ID (optional)", self.port_id)
        root.addLayout(form)
        hint = QLabel("Positive = starboard; negative = port. D uses degrees; P uses -100 to +100% of the "
                      "selected vessel's maximum rudder angle. For dual rudders, both IDs must be in the same sentence.")
        hint.setWordWrap(True)
        hint.setObjectName("secondaryText")
        root.addWidget(hint)
        self.endpoint = RudderEndpointEditor()
        self.endpoint.changed.connect(self._changed)
        root.addWidget(self.endpoint)
        self._update_units()
        self._loading = False

    def _update_units(self):
        previous = self.unit.currentData()
        with QSignalBlocker(self.unit):
            self.unit.clear()
            for value in XDR_RUDDER_UNITS[self.type.currentData()]:
                self.unit.addItem("D - Degrees" if value == "D" else "P - Percent of full range", value)
            self.unit.setCurrentIndex(max(0, self.unit.findData(previous)))

    def _type_changed(self):
        self._update_units()
        self._changed()

    def _changed(self, *_args):
        if not self._loading:
            self.changed.emit()

    def load(self, settings):
        self._loading = True
        try:
            self.type.setCurrentIndex(self.type.findData(settings.xdr_type))
            self._update_units()
            self.unit.setCurrentIndex(self.unit.findData(settings.xdr_unit))
            self.identifier.setText(settings.xdr_id)
            self.port_id.setText(settings.port_id)
            self.endpoint.load(settings.endpoint)
        finally:
            self._loading = False

    def values(self):
        return {"endpoint": self.endpoint.values(), "xdr_type": self.type.currentData(),
                "xdr_unit": self.unit.currentData(), "xdr_id": self.identifier.text().strip(),
                "port_id": self.port_id.text().strip()}
