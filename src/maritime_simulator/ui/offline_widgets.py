# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QProgressBar, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionButton, QStyleOptionProgressBar


ACTION_HIGHLIGHT_ROLE = Qt.UserRole + 1


def map_progress_bar(parent=None):
    bar = QProgressBar(parent)
    bar.setOrientation(Qt.Horizontal)
    bar.setAlignment(Qt.AlignCenter)
    bar.setRange(0, 100)
    bar.setMinimumHeight(bar.fontMetrics().height() + 12)
    return bar


class MapCellDelegate(QStyledItemDelegate):
    action = Signal(int, bool)

    def __init__(self, parent=None, *, progress_column=6, action_column=7, delete_column=8):
        super().__init__(parent)
        self.progress_column, self.action_column, self.delete_column = progress_column, action_column, delete_column
        self.progress_style = map_progress_bar(parent)
        self.progress_style.hide()
        self.action_style = QPushButton(parent)
        self.action_style.setCheckable(True)
        self.action_style.hide()

    def paint(self, painter, option, index):
        # Paint only visible cells rather than keeping a widget for every row.
        if index.column() == self.progress_column:
            control = QStyleOptionProgressBar()
            self.progress_style.initStyleOption(control)
            control.rect = option.rect.adjusted(4, 5, -4, -5)
            control.state |= QStyle.State_Horizontal
            control.minimum, control.maximum = 0, 100
            control.progress = index.data(Qt.UserRole) or 0
            control.text, control.textVisible = f"{control.progress}%", True
            control.textAlignment = Qt.AlignCenter
            self.progress_style.style().drawControl(QStyle.CE_ProgressBar, control, painter, self.progress_style)
        elif index.column() in {self.action_column, self.delete_column}:
            control = QStyleOptionButton()
            self.action_style.setEnabled(bool(index.flags() & Qt.ItemIsEnabled))
            self.action_style.setChecked(bool(index.data(ACTION_HIGHLIGHT_ROLE)) and self.action_style.isEnabled())
            self.action_style.initStyleOption(control)
            control.rect = option.rect.adjusted(3, 3, -3, -3)
            control.state |= option.state & QStyle.State_MouseOver
            control.text = index.data() if index.column() == self.action_column else ""
            if index.column() == self.delete_column:
                control.icon = option.widget.style().standardIcon(QStyle.SP_TrashIcon)
                control.iconSize = option.decorationSize
            self.action_style.style().drawControl(QStyle.CE_PushButton, control, painter, self.action_style)
        else:
            super().paint(painter, option, index)

    def editorEvent(self, event, model, option, index):
        if index.column() in {self.action_column, self.delete_column} and index.flags() & Qt.ItemIsEnabled:
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self.action.emit(index.row(), index.column() == self.delete_column)
                return True
        return super().editorEvent(event, model, option, index)
