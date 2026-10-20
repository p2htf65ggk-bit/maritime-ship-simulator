# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from maritime_simulator.application.fault_injection import (
    FAULT_SCENARIOS,
    FaultInjectionController,
)


_GROUPS = (
    ("Position and motion sensors", ("gnss_", "heading_", "speed_", "rot_")),
    ("Other sensors", ("rudder_", "depth_", "wind_")),
    ("Transmission faults", ("gnss_output_", "packet_", "checksum_", "output_")),
)


class FaultInjectionPanel(QWidget):
    state_changed = Signal()

    def __init__(self, controller: FaultInjectionController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._checks: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        intro = QLabel(
            "Faults alter displayed sensor values and protocol output only. "
            "Vessel dynamics and the authoritative simulation state remain unchanged."
        )
        intro.setObjectName("secondaryText")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        descriptions = {fault_id: (label, description) for fault_id, label, description in FAULT_SCENARIOS}
        assigned: set[str] = set()
        for title, prefixes in _GROUPS:
            group = QGroupBox(title)
            grid = QGridLayout(group)
            grid.setColumnStretch(0, 1)
            row = 0
            for fault_id, (label, description) in descriptions.items():
                if fault_id in assigned or not fault_id.startswith(prefixes):
                    continue
                assigned.add(fault_id)
                check = QCheckBox(label)
                check.setToolTip(description)
                check.toggled.connect(
                    lambda active, selected=fault_id: self._set_fault(selected, active)
                )
                grid.addWidget(check, row, 0)
                self._checks[fault_id] = check
                row += 1
            layout.addWidget(group)

        actions = QHBoxLayout()
        self.activate_saved_button = QPushButton("Activate Saved Selection")
        self.activate_saved_button.clicked.connect(self.activate_saved)
        actions.addWidget(self.activate_saved_button)
        self.clear_button = QPushButton("Clear All Faults")
        self.clear_button.clicked.connect(self.clear_all)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.status = QLabel("No active faults")
        self.status.setObjectName("dataReadout")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.status)
        self.saved_status = QLabel("")
        self.saved_status.setObjectName("secondaryText")
        self.saved_status.setWordWrap(True)
        layout.addWidget(self.saved_status)
        layout.addStretch(1)
        self.refresh()

    def activate_saved(self) -> None:
        self.controller.activate_configured()
        active = set(self.controller.active_faults())
        for fault_id, check in self._checks.items():
            check.blockSignals(True)
            check.setChecked(fault_id in active)
            check.blockSignals(False)
        self.refresh()
        self.state_changed.emit()

    def clear_all(self) -> None:
        self.controller.clear()
        for check in self._checks.values():
            check.blockSignals(True)
            check.setChecked(False)
            check.blockSignals(False)
        self.refresh()
        self.state_changed.emit()

    def export_state(self) -> dict:
        return self.controller.export_state()

    def apply_state(self, state: dict | None) -> None:
        self.controller.apply_state(state)
        for check in self._checks.values():
            check.blockSignals(True)
            check.setChecked(False)
            check.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        active = self.controller.active_faults()
        labels = {fault_id: label for fault_id, label, _description in FAULT_SCENARIOS}
        if active:
            self.status.setText("FAULT ACTIVE\n" + "\n".join(labels[item] for item in active))
            self.status.setProperty("faultActive", True)
        else:
            self.status.setText("No active faults")
            self.status.setProperty("faultActive", False)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        configured = self.controller.configured_faults()
        self.saved_status.setText(
            f"Saved scenario selection: {len(configured)} (inactive after restart or configuration load)"
            if configured
            else "Saved scenario selection: none"
        )
        self.clear_button.setEnabled(bool(active))
        self.activate_saved_button.setEnabled(bool(configured) and set(active) != set(configured))

    def _set_fault(self, fault_id: str, active: bool) -> None:
        self.controller.set_active(fault_id, active)
        self.refresh()
        self.state_changed.emit()
