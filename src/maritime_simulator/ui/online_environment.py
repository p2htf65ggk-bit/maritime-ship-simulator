# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from maritime_simulator.transport.live_weather import validate_api_key
from .dialogs import OpaqueDialog


class OpenMeteoAccessDialog(OpaqueDialog):
    def __init__(self, api_key="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open-Meteo Access")
        self.resize(460, 240)
        layout = QVBoxLayout(self)
        note = QLabel("No key: free API for non-commercial use only.\n"
                      "Commercial use: enter your paid Open-Meteo API key.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.key = QLineEdit(api_key)
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setMaxLength(256)
        form.addRow("API key", self.key)
        layout.addLayout(form)
        links = QLabel('<a style="color:#4db8e5" href="https://open-meteo.com/en/pricing">API access</a> | '
                       '<a style="color:#4db8e5" href="https://open-meteo.com/en/terms">Terms</a>')
        links.setOpenExternalLinks(True)
        layout.addWidget(links)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        try:
            self.api_key = validate_api_key(self.key.text())
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()
