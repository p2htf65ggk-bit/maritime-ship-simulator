# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget


_LOG = logging.getLogger("maritime_simulator.startup.window_geometry")


def usable_screen_area(screen) -> QRect:
    if screen is None:
        return QRect()
    try:
        area = screen.availableGeometry()
    except RuntimeError:
        return QRect()
    # X11 can report a 320 x 200 placeholder while the KVM has no display.
    return QRect(area) if area.width() >= 360 and area.height() >= 280 else QRect()


def reachable_window(frame: QRect, area: QRect) -> bool:
    if not frame.isValid() or not area.isValid():
        return False
    visible = frame.intersected(area)
    title = QRect(frame.x(), frame.y(), frame.width(), min(30, frame.height()))
    visible_title = title.intersected(area)
    return (
        visible.width() >= min(160, frame.width())
        and visible.height() >= min(80, frame.height())
        and visible_title.width() >= min(160, frame.width())
        and visible_title.height() >= min(20, title.height())
    )


def fitted_window_frame(frame: QRect, area: QRect) -> QRect:
    bounds = area.adjusted(16, 16, -16, -16)
    width = min(max(1, frame.width()), bounds.width())
    height = min(max(1, frame.height()), bounds.height())
    x = min(max(frame.x(), bounds.left()), bounds.right() - width + 1)
    y = min(max(frame.y(), bounds.top()), bounds.bottom() - height + 1)
    return QRect(x, y, width, height)


class WindowGeometryGuard(QObject):
    """Recover owned windows after display loss without moving healthy windows."""

    def __init__(self, owner: QWidget, *, pending_initial_fit: bool = False):
        super().__init__(owner)
        self.owner = owner
        self.app = QApplication.instance()
        self._closed = False
        self._pending_initial_fit = pending_initial_fit
        self._screen_connections = []
        self._application_connections = []
        self._settle_delays = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._settled_check)
        self._poll = QTimer(self)
        self._poll.setInterval(5000)
        self._poll.timeout.connect(self._check_topology)
        for signal in (self.app.screenAdded, self.app.screenRemoved, self.app.primaryScreenChanged):
            signal.connect(self._topology_changed)
            self._application_connections.append(signal)
        self._fingerprint = self._screen_fingerprint()
        self._connect_screens()
        owner.installEventFilter(self)
        self._poll.start()

    def _screens(self):
        return self.app.screens()

    def _screen_fingerprint(self):
        result = []
        for screen in self._screens():
            area = usable_screen_area(screen)
            result.append((id(screen), area.x(), area.y(), area.width(), area.height()))
        return tuple(result)

    def _connect_screens(self) -> None:
        for signal in self._screen_connections:
            try:
                signal.disconnect(self._schedule_check)
            except (RuntimeError, TypeError):
                pass
        self._screen_connections.clear()
        for screen in self._screens():
            for signal in (screen.geometryChanged, screen.availableGeometryChanged):
                signal.connect(self._schedule_check)
                self._screen_connections.append(signal)

    def _topology_changed(self, *_args) -> None:
        if self._closed:
            return
        self._connect_screens()
        self._schedule_check()

    def _check_topology(self) -> None:
        fingerprint = self._screen_fingerprint()
        if fingerprint != self._fingerprint:
            self._topology_changed()

    def _schedule_check(self, *_args) -> None:
        if not self._closed:
            # EDID, work-area and window-manager events need not arrive together.
            self._settle_delays = [1000, 3000]
            self._timer.start(250)

    def _settled_check(self) -> None:
        self._recover_windows()
        if self._settle_delays and not self._closed:
            self._timer.start(self._settle_delays.pop(0))

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Show, QEvent.WindowStateChange):
            self._schedule_check()
        return False

    def _owned_windows(self):
        for window in self.app.topLevelWidgets():
            if not window.isVisible() or window.isMinimized():
                continue
            if window.windowType() in (Qt.Popup, Qt.ToolTip):
                continue
            parent = window
            while parent is not None and parent is not self.owner:
                parent = parent.parentWidget()
            if parent is self.owner:
                yield window

    def _recover_windows(self) -> None:
        if self._closed:
            return
        screens = [(screen, usable_screen_area(screen)) for screen in self._screens()]
        screens = [(screen, area) for screen, area in screens if area.isValid()]
        self._fingerprint = self._screen_fingerprint()
        if not screens:
            if not self._pending_initial_fit:
                _LOG.info("No usable display; deferring window placement until a display returns")
            self._pending_initial_fit = True
            return
        active = self.app.activeModalWidget() or self.app.activeWindow()
        repaired = []
        for window in self._owned_windows():
            initial_fit = window is self.owner and self._pending_initial_fit
            if self._recover_window(window, screens, initial_fit=initial_fit):
                repaired.append(window)
            if window is self.owner:
                self._pending_initial_fit = False
        # Do not unminimize windows or activate them for an unrelated monitor change.
        foreground = active if active in repaired else self.owner if self.owner in repaired else None
        if foreground is self.owner and self.app.activeModalWidget() is not None:
            foreground = self.app.activeModalWidget()
        if foreground is not None:
            foreground.raise_()
            foreground.activateWindow()

    def _recover_window(self, window, screens, *, initial_fit: bool) -> bool:
        frame = window.frameGeometry()
        reachable = [
            (screen, area) for screen, area in screens
            if reachable_window(frame, screen.geometry() if window.isFullScreen() else area)
        ]
        screen, area = max(
            reachable or screens,
            key=lambda item: frame.intersected(item[1]).width() * frame.intersected(item[1]).height(),
        )
        stateful = window.isFullScreen() or window.isMaximized()
        if reachable and not initial_fit:
            if stateful or (frame.width() <= area.width() and frame.height() <= area.height()):
                return False
        state = window.windowState()
        if stateful:
            window.showNormal()
        geometry = window.geometry()
        frame = window.frameGeometry()
        left = max(0, geometry.x() - frame.x())
        top = max(0, geometry.y() - frame.y())
        extra_width = max(0, frame.width() - geometry.width())
        extra_height = max(0, frame.height() - geometry.height())
        if initial_fit:
            frame.setSize(QSize(1500 + extra_width, 920 + extra_height))
        fitted = fitted_window_frame(frame, area)
        client_size = QSize(max(1, fitted.width() - extra_width), max(1, fitted.height() - extra_height))
        minimum = QSize(960, 640) if window is self.owner else window.minimumSize()
        window.setMinimumSize(minimum.boundedTo(client_size))
        handle = window.windowHandle()
        if handle is not None and handle.screen() is not screen:
            handle.setScreen(screen)
        window.setGeometry(QRect(fitted.topLeft() + QPoint(left, top), client_size))
        if stateful:
            window.setWindowState(state)
        _LOG.info("Recovered window %r on display %r: %s", window.windowTitle(), screen.name(), window.geometry())
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._poll.stop()
        self.owner.removeEventFilter(self)
        for signal in self._application_connections:
            signal.disconnect(self._topology_changed)
        self._application_connections.clear()
        for signal in self._screen_connections:
            try:
                signal.disconnect(self._schedule_check)
            except (RuntimeError, TypeError):
                pass
        self._screen_connections.clear()
