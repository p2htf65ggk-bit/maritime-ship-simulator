# SPDX-License-Identifier: MPL-2.0
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QCloseEvent, QFont, QImage
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from maritime_simulator.application.ais import AISRepository
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.transport.live_ais import LiveAISClient
from maritime_simulator.ui.dialogs import CloseConfirmationDialog, OpaqueDialog, OpaqueMessageBox
from maritime_simulator.ui.live_ais import AISSettingsDialog
from maritime_simulator.ui.main_window import V2MainWindow
from ui.style import apply_global_ui_style


def _app():
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    return app


def test_global_style_makes_secondary_dialogs_explicitly_opaque():
    app = _app()
    dialog = OpaqueDialog()
    dialog.resize(320, 180)
    dialog.show()
    app.processEvents()

    assert not dialog.testAttribute(Qt.WA_TranslucentBackground)
    assert not dialog.testAttribute(Qt.WA_NoSystemBackground)
    assert dialog.windowOpacity() == 1.0
    assert dialog.palette().window().color().alpha() == 255

    image = QImage(dialog.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    dialog.render(image)
    assert image.pixelColor(2, 2).alpha() == 255
    dialog.close()


def test_application_message_boxes_have_an_opaque_backing_surface():
    app = _app()
    box = OpaqueMessageBox()
    box.setText("Opaque reset confirmation")
    box.resize(360, 180)
    box.show()
    app.processEvents()
    image = QImage(box.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    box.render(image)
    assert box.palette().window().color().alpha() == 255
    assert image.pixelColor(2, 2).alpha() == 255
    box.close()


def test_global_style_does_not_install_a_python_application_event_filter():
    app = _app()
    assert not hasattr(app, "_shipsim_dialog_surface_filter")


def test_live_ais_dialog_reopen_keeps_one_button_set_and_opaque_surface(tmp_path):
    app = _app()
    service = SimulatorService()
    client = LiveAISClient(AISRepository(), service.snapshot)
    dialog = AISSettingsDialog(client, tmp_path / "live_ais.json")
    button_box = dialog.findChild(QDialogButtonBox)
    original_buttons = tuple(button_box.buttons())
    assert len(original_buttons) == 2

    for width, height in ((600, 390), (720, 480), (520, 360)):
        dialog.resize(width, height)
        dialog.show()
        app.processEvents()
        dialog.hide()
        app.processEvents()

    assert tuple(button_box.buttons()) == original_buttons
    assert dialog.palette().window().color().alpha() == 255
    dialog.deleteLater()
    client.deleteLater()
    service.close()


def test_close_confirmation_is_opaque_and_available_on_every_close_attempt():
    app = _app()
    dialogs = (
        CloseConfirmationDialog(has_unsaved_changes=True),
        CloseConfirmationDialog(has_unsaved_changes=False),
        CloseConfirmationDialog(has_unsaved_changes=False),
    )
    for dialog in dialogs:
        dialog.resize(560, 210)
        dialog.show()
        app.processEvents()
        image = QImage(dialog.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        dialog.render(image)
        assert image.pixelColor(2, 2).alpha() == 255
        assert dialog.palette().window().color().alpha() == 255
        assert dialog.close_button is not None
        assert dialog.cancel_button is not None
        dialog.reject()

    assert dialogs[0].save_button is not None
    assert dialogs[0].save_as_button is not None
    assert dialogs[1].save_button is None
    assert dialogs[2].save_button is None


def test_close_confirmation_buttons_fit_on_small_high_dpi_screens():
    app = _app()
    dialog = CloseConfirmationDialog(has_unsaved_changes=True)
    font = QFont(dialog.font())
    font.setPointSize(16)
    # Approximate the wider Windows Segoe UI metrics even when this regression
    # runs on another build host.
    font.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
    dialog.setFont(font)
    dialog.resize(440, dialog.sizeHint().height())
    dialog.show()
    app.processEvents()

    assert dialog.minimumWidth() <= 440
    for button in (
        dialog.close_button,
        dialog.cancel_button,
        dialog.save_button,
        dialog.save_as_button,
    ):
        assert button is not None
        required_width = button.fontMetrics().horizontalAdvance(button.text()) + 28
        required_height = button.fontMetrics().height() + 18
        assert button.width() >= required_width
        assert button.height() >= required_height
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    dialog.reject()


def test_main_window_requests_close_confirmation_again_after_cancel(tmp_path):
    _app()
    window = V2MainWindow(SimulatorService(), config_dir=tmp_path)
    window.timer.stop()
    decisions = iter((None, None, "close"))
    requests = []
    window._interactive_close_confirmation_enabled = lambda: True
    window._request_close_confirmation = (
        lambda has_changes: requests.append(has_changes) or next(decisions)
    )

    for _ in range(2):
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert not window._close_dialog_active

    final_event = QCloseEvent()
    window.closeEvent(final_event)
    assert final_event.isAccepted()
    assert len(requests) == 3


def test_automated_smoke_flag_disables_only_the_interactive_close_prompt(monkeypatch, tmp_path):
    import run_v2

    source = Path(run_v2.__file__).read_text(encoding="utf-8")
    assert '"--smoke-test" in sys.argv or "--dialog-smoke-test" in sys.argv' in source
    assert "window._interactive_close_confirmation_enabled = lambda: False" in source
