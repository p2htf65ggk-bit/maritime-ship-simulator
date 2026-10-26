# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from .dialogs import OpaqueDialog

from maritime_simulator import __version__


class AboutV2Dialog(OpaqueDialog):
    def __init__(self, open_performance, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Maritime Ship Simulator V2")
        self.resize(660, 520)
        layout = QVBoxLayout(self)
        title = QLabel("Maritime Ship Simulator V2")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        if "-dev" in __version__:
            release_label = "Development preview"
        elif "-rc" in __version__:
            release_label = "Release candidate"
        elif "-beta" in __version__:
            release_label = "Beta preview"
        else:
            release_label = "Production release"
        version = QLabel(f"{release_label} {__version__}")
        version.setObjectName("dataReadout")
        layout.addWidget(version)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml(
            "<p>Maritime Ship Simulator V2 is a deterministic simulator and IEC 61162 test-data tool for training, "
            "integration, verification and development environments.</p>"
            f"<p><b>Release:</b> {__version__}<br>"
            "<b>Version prepared:</b> 26 October 2026<br>"
            "<b>Copyright:</b> © 2026 Pascal Göllnitz.</p>"
            "<p><b>Contact</b><br>Pascal Göllnitz<br>"
            "<a href=\"https://github.com/p2htf65ggk-bit/maritime-ship-simulator/issues\">GitHub Issues</a></p>"
            "<p>This software is intended for simulation, training, integration and test use. It is not a certified navigation, "
            "conning or collision-avoidance system and must not be used as the sole source for safety-critical "
            "decisions. Detailed operating, model, network and troubleshooting information is available in the "
            "User Guide.</p>"
            "<p>S-57 charts and their simplified portrayal are experimental simulation aids, not a certified ECDIS.</p>"
            "<p>This version adds a tunnel bow-thruster model and independent external "
            "rudder, thruster and engine controls. Factory manoeuvring values are representative "
            "simulation assumptions, not calibrated vessel-trial data.</p>"
            "<p>Chart-based depth prefers installed S57 cells, with GEBCO as online fallback "
            "and the last manual depth when neither source is available, "
            "with the transducer at keel level. The global GEBCO grid is coarse, not detailed "
            "harbour bathymetry. No live tide correction is applied. "
            "Data: <a href=\"https://www.gebco.net/data-products/gebco-web-services/web-map-service\">"
            "GEBCO Compilation Group / BODC</a>. For simulation, not navigation.</p>"
            "<p>Online wind and surface current use <a href=\"https://open-meteo.com/\">Open-Meteo</a> "
            "model data, independently selectable with manual fallback. Marine current resolution is coarse. "
            "Free API access is non-commercial only; a paid customer API key can be configured "
            "under Environment for commercial use. Open-Meteo and its upstream providers retain their "
            "data rights and attribution requirements.</p>"
            "<p><b>Open source:</b> Original project code is licensed under the Mozilla Public License 2.0. "
            "See the bundled license and public source repository for terms. "
            "Third-party components and map data retain their own licenses.</p>"
            "<p>To support continued development, you can purchase "
            "<a href=\"https://mybook.to/MaritimeKnowledgeBites\">Maritime Knowledge Bites</a>.</p>"
        )
        layout.addWidget(text, 1)
        actions = QHBoxLayout()
        performance = QPushButton("Performance Monitor")
        performance.clicked.connect(open_performance)
        actions.addWidget(performance)
        actions.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)
