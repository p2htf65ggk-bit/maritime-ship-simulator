# SPDX-License-Identifier: MPL-2.0
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QTextBrowser

from maritime_simulator.ui.about_dialog import AboutV2Dialog
from maritime_simulator.ui.user_guide import UserGuideDialog
from ui.style import apply_global_ui_style


def test_about_identifies_final_hud_and_unsigned_limits():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    dialog = AboutV2Dialog(lambda: None)
    try:
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert "Production release 2.3.1" in labels
        text = dialog.findChild(QTextBrowser).toPlainText()
        for marker in ("29 October 2026", "heading tape", "set/drift", "Smart App Control"):
            assert marker in text
    finally:
        dialog.close()


def test_in_app_guide_matches_final_hud_and_api_location():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    dialog = UserGuideDialog()
    try:
        text = " ".join(dialog.findChild(QTextBrowser).toPlainText().split())
        for marker in (
            "2.3.1, updated 29 October 2026", "3D instruments and controls",
            "ASTERN is red and enlarged", "relative to the bow", "SET in degrees true",
            "CAM starts off", "9 degrees elevation", "bearing -40 degrees",
            "lower-left corner", "Error 4551", "portable ZIP does not avoid",
        ):
            assert marker in text
        assert "HUD list visible" not in text
        assert "upper-left corner to store the key" not in text
        for width, height in ((820, 720), (560, 480)):
            dialog.resize(width, height)
            dialog.show()
            app.processEvents()
            assert dialog.findChild(QTextBrowser).horizontalScrollBar().maximum() == 0
    finally:
        dialog.close()
