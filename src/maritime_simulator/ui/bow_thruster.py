# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget
from ui.widgets.slider import ResetSlider


class BowThrusterControl(QWidget):
    order_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.label = QLabel("Bow Thruster")
        self.label.setObjectName("sectionTitle")
        root.addWidget(self.label)
        self.slider = ResetSlider(Qt.Horizontal)
        self.slider.setRange(-100, 100)
        self.slider.setMinimumWidth(104)
        self.slider.setToolTip("Port -100% | Stop 0% | Starboard +100%. Double-click for zero.")
        self.slider.valueChanged.connect(lambda value: self.order_changed.emit(float(value)))
        self.spin = QSpinBox()
        self.spin.setRange(-100, 100)
        self.spin.setSuffix("%")
        self.spin.setKeyboardTracking(False)
        self.spin.setFixedWidth(116)
        self.spin.setAccessibleName("Bow thruster order percent")
        self.spin.editingFinished.connect(lambda: self.slider.setValue(self.spin.value()))
        self.slider.valueChanged.connect(self._slider_changed)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        root.addLayout(row)
        self.readout = QLabel("Order +0%  |  Actual +0%")
        self.readout.setObjectName("dataReadout")
        self.readout.setWordWrap(True)
        root.addWidget(self.readout)

    def _slider_changed(self, value):
        with QSignalBlocker(self.spin):
            self.spin.setValue(value)

    def update_snapshot(self, snapshot, profile):
        external = not snapshot.bow_thruster_manual_enabled
        self.slider.setEnabled(profile.bow_thruster_power_kw > 0.0 and not external)
        self.spin.setEnabled(self.slider.isEnabled())
        if external or not self.slider.isSliderDown():
            with QSignalBlocker(self.slider):
                self.slider.setValue(round(snapshot.bow_thruster_order_percent))
            if external or not self.spin.hasFocus():
                with QSignalBlocker(self.spin):
                    self.spin.setValue(round(snapshot.bow_thruster_order_percent))
        self.readout.setText(f"Order {snapshot.bow_thruster_order_percent:+.0f}%  |  Actual {snapshot.bow_thruster_response_percent:+.0f}%")
        self.readout.setToolTip(
            f"Order / actual RPM; effectiveness {snapshot.bow_thruster_efficiency:.0%}. "
            f"Source: {snapshot.bow_thruster_active_source or ('Manual' if not external else 'Input-loss action')}."
            if profile.bow_thruster_power_kw > 0.0 else "No bow thruster fitted (0 kW).")
