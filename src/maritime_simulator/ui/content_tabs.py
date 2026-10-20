# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QSizePolicy, QStyle, QTabWidget


class ContentSizedTabs(QTabWidget):
    """Fit the visible page rather than reserving space for the tallest hidden tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.currentChanged.connect(self._page_changed)

    def _page_changed(self, _index):
        self.updateGeometry()
        parent = self.parentWidget()
        while parent is not None:
            if parent.layout() is not None:
                parent.layout().invalidate()
            parent.updateGeometry()
            parent = parent.parentWidget()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        page = self.currentWidget()
        frame = 2 * self.style().pixelMetric(QStyle.PM_DefaultFrameWidth, None, self)
        height = 0
        if page is not None:
            height = page.heightForWidth(max(1, width - frame)) if page.hasHeightForWidth() else -1
            if height < 0:
                height = page.sizeHint().height()
        return max(0, height) + self.tabBar().sizeHint().height() + frame + 4

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(hint.width(), self.heightForWidth(self.width() if self.width() > 100 else hint.width()))

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(hint.width(), self.heightForWidth(max(self.width(), hint.width())))
