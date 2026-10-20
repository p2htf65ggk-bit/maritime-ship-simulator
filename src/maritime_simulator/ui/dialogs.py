# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)


def reserve_label_lines(label: QLabel, lines: int, *, padding: int = 0) -> None:
    """Keep dynamic helper text from shifting neighbouring controls."""
    height = label.fontMetrics().lineSpacing() * max(1, int(lines)) + max(0, int(padding))
    label.setFixedHeight(height)
    label.setAlignment(Qt.AlignLeft | Qt.AlignTop)


def text_button_minimum_size(
    button: QPushButton,
    *,
    horizontal_padding: int = 28,
    vertical_padding: int = 18,
) -> QSize:
    """Return a platform-font-aware minimum for a text button."""
    metrics = button.fontMetrics()
    text = button.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
    return QSize(
        metrics.horizontalAdvance(text) + max(0, int(horizontal_padding)),
        metrics.height() + max(0, int(vertical_padding)),
    )


def ensure_text_button_fits(
    button: QPushButton,
    *,
    horizontal_padding: int = 28,
    vertical_padding: int = 18,
) -> QSize:
    """Keep button text visible with Linux and fractional Windows scaling."""
    required = text_button_minimum_size(
        button,
        horizontal_padding=horizontal_padding,
        vertical_padding=vertical_padding,
    )
    button.setMinimumSize(
        max(button.minimumWidth(), required.width()),
        max(button.minimumHeight(), required.height()),
    )
    button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return required


def fit_dialog_to_available_screen(dialog: QDialog, *, margin: int = 48) -> None:
    """Clamp an application dialog to the current screen's logical geometry."""
    layout = dialog.layout()
    if layout is not None:
        layout.activate()
    screen = dialog.screen() or QApplication.primaryScreen()
    if screen is None:
        return
    available = screen.availableGeometry()
    maximum_width = max(360, available.width() - margin)
    maximum_height = max(280, available.height() - margin)
    hint = dialog.sizeHint().expandedTo(dialog.minimumSizeHint())
    current = dialog.size()
    dialog.setMinimumSize(
        min(dialog.minimumWidth(), maximum_width),
        min(dialog.minimumHeight(), maximum_height),
    )
    dialog.resize(
        min(max(current.width(), min(hint.width(), maximum_width)), maximum_width),
        min(max(current.height(), min(hint.height(), maximum_height)), maximum_height),
    )


def force_opaque_surface(widget: QDialog) -> None:
    """Give every application-owned dialog a real backing surface on Linux."""
    widget.setProperty("opaqueSurface", True)
    widget.setAttribute(Qt.WA_TranslucentBackground, False)
    widget.setAttribute(Qt.WA_NoSystemBackground, False)
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setWindowOpacity(1.0)
    palette = widget.palette()
    palette.setColor(QPalette.Window, QColor("#101419"))
    palette.setColor(QPalette.Base, QColor("#151b22"))
    widget.setPalette(palette)
    widget.setAutoFillBackground(True)


class OpaqueDialog(QDialog):
    """V2 dialog with an explicit native backing surface on every platform."""

    def __init__(self, parent=None, flags=Qt.WindowFlags()):
        super().__init__(parent, flags)
        force_opaque_surface(self)


class OpaqueMessageBox(QMessageBox):
    """Non-static QMessageBox that cannot inherit a translucent Linux surface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        force_opaque_surface(self)


def ask_question(
    parent,
    title: str,
    text: str,
    *,
    buttons=QMessageBox.Yes | QMessageBox.No,
    default=QMessageBox.No,
    confirm_label=None,
    cancel_label=None,
):
    box = OpaqueMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(buttons)
    if confirm_label is not None and box.button(QMessageBox.Yes) is not None:
        box.button(QMessageBox.Yes).setText(confirm_label)
    if cancel_label is not None and box.button(QMessageBox.No) is not None:
        box.button(QMessageBox.No).setText(cancel_label)
    box.setDefaultButton(default)
    box.exec()
    return box.standardButton(box.clickedButton())


def show_warning(parent, title: str, text: str) -> None:
    box = OpaqueMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Ok)
    box.exec()


def show_critical(parent, title: str, text: str) -> None:
    box = OpaqueMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Ok)
    box.exec()


def request_text(parent, title: str, label: str) -> tuple[str, bool]:
    dialog = QInputDialog(parent)
    force_opaque_surface(dialog)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    accepted = dialog.exec() == QDialog.Accepted
    return dialog.textValue(), accepted


class CloseConfirmationDialog(OpaqueDialog):
    """Opaque, platform-neutral close confirmation used by the main window."""

    def __init__(self, parent=None, *, has_unsaved_changes: bool):
        super().__init__(parent, Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setObjectName("closeConfirmationDialog")
        self.setWindowTitle("Close Maritime Ship Simulator V2")
        self.setWindowModality(Qt.WindowModal)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.selection: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("Close Maritime Ship Simulator V2?")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        message = QLabel(
            "The simulator configuration has changed. Save it before closing?"
            if has_unsaved_changes
            else "The current configuration is already saved. Close the application?"
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        details = QLabel(
            "Save updates the last configuration. Save As creates an additional configuration file."
        )
        details.setObjectName("secondaryText")
        details.setWordWrap(True)
        details.setVisible(has_unsaved_changes)
        layout.addWidget(details)

        self.button_grid = QGridLayout()
        self.button_grid.setContentsMargins(0, 4, 0, 0)
        self.button_grid.setHorizontalSpacing(10)
        self.button_grid.setVerticalSpacing(8)
        self.close_button = QPushButton(
            "Close without saving" if has_unsaved_changes else "Close"
        )
        self.cancel_button = QPushButton("Cancel")
        self.save_button = None
        self.save_as_button = None
        if has_unsaved_changes:
            self.save_button = QPushButton("Save")
            self.save_as_button = QPushButton("Save As...")
            self.save_button.setDefault(True)
            self.save_button.clicked.connect(lambda: self._finish("save"))
            self.save_as_button.clicked.connect(lambda: self._finish("save_as"))
        else:
            self.close_button.setDefault(True)
        self._buttons = tuple(button for button in (
            self.close_button,
            self.cancel_button,
            self.save_button,
            self.save_as_button,
        ) if button is not None)
        self.close_button.clicked.connect(lambda: self._finish("close"))
        self.cancel_button.clicked.connect(self.reject)
        layout.addLayout(self.button_grid)
        self._arrange_buttons()

    def _arrange_buttons(self) -> None:
        required = [ensure_text_button_fits(button) for button in self._buttons]
        widest = max(
            (
                max(size.width(), button.minimumWidth())
                for button, size in zip(self._buttons, required)
            ),
            default=170,
        )
        tallest = max((size.height() for size in required), default=36)
        for button in self._buttons:
            button.setMinimumHeight(tallest)

        layout_margins = self.layout().contentsMargins()
        two_column_width = (
            layout_margins.left()
            + layout_margins.right()
            + widest * 2
            + self.button_grid.horizontalSpacing()
        )
        screen = self.screen() or QApplication.primaryScreen()
        screen_width = (
            max(360, screen.availableGeometry().width() - 48)
            if screen is not None
            else max(420, self.width())
        )
        requested_width = min(max(1, self.width()), screen_width)
        columns = 2 if two_column_width <= requested_width else 1

        while self.button_grid.count():
            self.button_grid.takeAt(0)
        order = (
            (self.save_button, self.save_as_button, self.close_button, self.cancel_button)
            if self.save_button is not None
            else (self.close_button, self.cancel_button)
        )
        visible_buttons = [button for button in order if button is not None]
        for index, button in enumerate(visible_buttons):
            row, column = divmod(index, columns)
            self.button_grid.addWidget(button, row, column)
        for column in range(columns):
            self.button_grid.setColumnStretch(column, 1)
        target_width = (
            two_column_width
            if columns == 2
            else layout_margins.left() + layout_margins.right() + widest
        )
        base_width = min(420, screen_width)
        self.setMinimumWidth(max(base_width, min(requested_width, target_width)))
        self.layout().activate()

    def showEvent(self, event: QShowEvent) -> None:
        self._arrange_buttons()
        fit_dialog_to_available_screen(self)
        super().showEvent(event)

    def _finish(self, selection: str) -> None:
        self.selection = selection
        self.accept()

    def reject(self) -> None:
        self.selection = None
        super().reject()

    @classmethod
    def request(cls, parent=None, *, has_unsaved_changes: bool) -> str | None:
        dialog = cls(parent, has_unsaved_changes=has_unsaved_changes)
        dialog.exec()
        return dialog.selection
