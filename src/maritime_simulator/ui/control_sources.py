# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QRadioButton, QToolButton, QVBoxLayout, QWidget

from maritime_simulator.domain.external_control import INPUT_LOSS_MODES, ordered_sources, source_policy


SOURCE_LABELS = {"ROR": "ROR", "RSA": "RSA", "XDR_ORDER": "XDR Order", "XDR_RESPONSE": "XDR Response",
                 "TRC": "TRC", "PRC": "PRC", "XDR": "XDR", "MANUAL": "Manual", "": "None"}


class ControlSourceSelector(QWidget):
    changed = Signal()

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self.setObjectName("controlSources")
        self.setStyleSheet("""
            QWidget#controlSources QRadioButton { spacing: 8px; padding: 2px 0; }
            QWidget#controlSources QRadioButton::indicator {
                width: 14px; height: 14px; border-radius: 8px;
                border: 1px solid #8199a5; background: #101b23;
            }
            QWidget#controlSources QRadioButton::indicator:checked {
                border-color: #94b4c0;
                background: qradialgradient(cx: 0.5, cy: 0.5, fx: 0.5, fy: 0.5, radius: 0.5,
                    stop: 0 #aacbd7, stop: 0.44 #aacbd7,
                    stop: 0.45 #101b23, stop: 1 #101b23);
            }
            QWidget#controlSources QRadioButton::indicator:hover { border-color: #e7fbff; }
            QWidget#controlSources QRadioButton::indicator:disabled {
                border-color: #46545c; background: #192127;
            }
        """)
        self.choices = tuple(choices)
        self._loading = True
        self.groups = {}
        self.buttons = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.toggle = QToolButton()
        self.toggle.setText("Control Sources")
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.RightArrow)
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName("Expand control source settings")
        self.toggle.setToolTip("Change main source, fallback source and input-loss action")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        header.addWidget(self.toggle, 0, Qt.AlignTop)
        header.addWidget(self.summary, 1)
        root.addLayout(header)
        self.options = QWidget()
        root.addWidget(self.options)
        grid = QGridLayout(self.options)
        grid.setContentsMargins(0, 4, 0, 4)
        self.toggle.toggled.connect(self._set_expanded)
        for column, (key, title, values) in enumerate((
            ("main", "Main Control Source", (*choices, "MANUAL")),
            ("fallback", "Fallback Control Source", ("", *choices)),
            ("loss", "On Input Loss", INPUT_LOSS_MODES),
        )):
            heading = QLabel(title)
            heading.setObjectName("sectionTitle")
            heading.setWordWrap(True)
            grid.addWidget(heading, 0, column)
            group = QButtonGroup(self)
            self.groups[key] = group
            self.buttons[key] = {}
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            for value in values:
                button = QRadioButton(SOURCE_LABELS.get(value, value.title()))
                button.setProperty("source", value)
                button.setAccessibleName(f"{title}: {button.text()}")
                group.addButton(button)
                self.buttons[key][value] = button
                layout.addWidget(button)
                button.toggled.connect(lambda checked: self._changed() if checked else None)
            layout.addStretch(1)
            grid.addWidget(panel, 1, column)
            grid.setColumnStretch(column, 1)
        self.load("MANUAL", "MANUAL")
        self._set_expanded(False)

    def _set_expanded(self, expanded):
        self.options.setVisible(expanded)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle.setAccessibleName("Collapse control source settings" if expanded else "Expand control source settings")
        self.updateGeometry()

    def _value(self, key):
        button = self.groups[key].checkedButton()
        return button.property("source") if button else ""

    def load(self, policy, on_loss):
        self._loading = True
        try:
            sources = ordered_sources(policy, self.choices)
            self.buttons["main"][sources[0] if sources else "MANUAL"].setChecked(True)
            self.buttons["fallback"][sources[1] if len(sources) == 2 else ""].setChecked(True)
            self.buttons["loss"][on_loss].setChecked(True)
            self._sync()
        finally:
            self._loading = False

    def _sync(self):
        main = self._value("main")
        if main == "MANUAL" or main == self._value("fallback"):
            self.buttons["fallback"][""].setChecked(True)
        for value, button in self.buttons["fallback"].items():
            button.setEnabled(main != "MANUAL" and value != main)
        for button in self.buttons["loss"].values():
            button.setEnabled(main != "MANUAL")
        summary = f"Main: {SOURCE_LABELS[main]}"
        if main != "MANUAL":
            summary += f"  |  Fallback: {SOURCE_LABELS[self._value('fallback')]}  |  Loss: {self._value('loss').title()}"
        self.summary.setText(summary)

    def _changed(self):
        if self._loading:
            return
        self._loading = True
        try:
            self._sync()
        finally:
            self._loading = False
        self.changed.emit()

    def values(self):
        return source_policy(self._value("main"), self._value("fallback"), self.choices), self._value("loss")
