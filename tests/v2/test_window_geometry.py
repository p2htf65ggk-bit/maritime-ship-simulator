# SPDX-License-Identifier: MPL-2.0
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

import pytest
from PySide6.QtCore import QObject, QRect, QSize, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from maritime_simulator.ui import window_geometry
from maritime_simulator.ui.window_geometry import (
    WindowGeometryGuard,
    fitted_window_frame,
    reachable_window,
    usable_screen_area,
)


class _Screen(QObject):
    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)

    def __init__(self, area):
        super().__init__()
        self.area = area

    def availableGeometry(self):
        return self.area


@pytest.fixture
def guarded_window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    screen = app.primaryScreen()
    areas = {screen: QRect(0, 30, 1920, 1170)}
    monkeypatch.setattr(window_geometry, "usable_screen_area", lambda item: QRect(areas.get(item, QRect())))
    window = QWidget()
    window.setWindowTitle("KVM geometry test")
    window.setGeometry(100, 100, 960, 640)
    guard = WindowGeometryGuard(window)
    guard._screens = lambda: list(areas)
    window.show()
    app.processEvents()
    guard._timer.stop()
    yield app, window, guard, areas, screen
    guard.close()
    window.close()
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("area", [QRect(), QRect(0, 0, 1, 1), QRect(0, 0, 320, 200)])
def test_placeholder_screen_is_not_used_for_window_placement(area):
    assert not usable_screen_area(_Screen(area)).isValid()
    assert not usable_screen_area(None).isValid()


def test_real_screen_and_negative_virtual_desktop_coordinates_are_supported():
    area = QRect(-1920, -100, 1920, 1080)
    assert usable_screen_area(_Screen(area)) == area
    fitted = fitted_window_frame(QRect(-5000, -3000, 2400, 1400), area)
    assert area.contains(fitted)
    assert fitted.size() == QSize(1888, 1048)
    assert reachable_window(fitted, area)
    assert not reachable_window(QRect(50, 50, 900, 600), area)


def test_kvm_start_without_display_recovers_when_monitor_returns(guarded_window):
    app, window, guard, areas, screen = guarded_window
    areas.clear()
    window.setGeometry(-5000, -3000, 1440, 860)
    guard._recover_windows()
    assert guard._pending_initial_fit
    assert window.isVisible()
    areas[screen] = QRect(0, 30, 1920, 1170)
    guard._recover_windows()
    assert not guard._pending_initial_fit
    assert areas[screen].contains(window.frameGeometry())
    assert window.size() == QSize(1500, 920)
    assert window.minimumSize() == QSize(960, 640)


def test_tiny_startup_geometry_gets_useful_size_on_real_display(guarded_window):
    app, window, guard, areas, screen = guarded_window
    areas[screen] = QRect()
    window.setGeometry(0, 0, 320, 200)
    guard._recover_windows()
    areas[screen] = QRect(0, 30, 1280, 690)  # Logical desktop at 150% scaling.
    guard._recover_windows()
    assert areas[screen].contains(window.frameGeometry())
    assert window.width() > 1000
    assert window.height() > 600
    assert window.minimumHeight() <= window.height()


def test_disconnected_monitor_recovers_secondary_windows_but_not_hidden_ones(guarded_window):
    app, window, guard, areas, screen = guarded_window
    visible = QDialog(window)
    visible.setGeometry(2200, 100, 700, 500)
    visible.show()
    hidden = QDialog(window)
    hidden.setGeometry(2200, 100, 700, 500)
    app.processEvents()
    before = hidden.geometry()
    guard._recover_windows()
    assert areas[screen].contains(visible.frameGeometry())
    assert hidden.geometry() == before
    assert not hidden.isVisible()
    visible.close()


def test_healthy_secondary_monitor_position_is_preserved(guarded_window):
    app, window, guard, areas, screen = guarded_window
    areas[screen] = QRect(-1920, 30, 1920, 1170)
    window.setGeometry(-1700, 100, 1000, 700)
    before = window.geometry()
    activations = []
    window.activateWindow = lambda: activations.append(True)
    guard._recover_windows()
    assert window.geometry() == before
    assert not activations


def test_minimized_window_is_not_restored_or_focused_by_monitor_recovery(guarded_window):
    app, window, guard, areas, screen = guarded_window
    window.setGeometry(-5000, 100, 900, 600)
    window.showMinimized()
    app.processEvents()
    before = window.geometry()
    guard._pending_initial_fit = True
    guard._recover_windows()
    assert window.isMinimized()
    assert window.geometry() == before
    assert guard._pending_initial_fit
    window.showNormal()
    guard._recover_windows()
    assert areas[screen].contains(window.frameGeometry())


def test_offscreen_fullscreen_window_keeps_its_window_state(guarded_window):
    app, window, guard, areas, screen = guarded_window
    window.showFullScreen()
    app.processEvents()
    areas[screen] = QRect(2000, 30, 1280, 690)
    guard._recover_windows()
    assert window.isFullScreen()


def test_fullscreen_uses_full_screen_not_the_work_area(guarded_window):
    app, window, guard, areas, screen = guarded_window
    areas[screen] = screen.geometry().adjusted(0, 40, 0, 0)
    window.showFullScreen()
    app.processEvents()
    normal_calls = []
    window.showNormal = lambda: normal_calls.append(True)
    guard._recover_windows()
    assert not normal_calls
    assert window.isFullScreen()


def test_topology_events_are_coalesced_and_closed_guard_stops_checking(guarded_window):
    app, window, guard, areas, screen = guarded_window
    calls = []
    guard._recover_windows = lambda: calls.append(True)
    for _ in range(20):
        guard._schedule_check()
    QTest.qWait(350)
    assert len(calls) == 1
    guard.close()
    guard._schedule_check()
    QTest.qWait(350)
    assert len(calls) == 1
    assert not guard._timer.isActive()
    assert not guard._poll.isActive()


def test_poll_catches_a_missed_kvm_signal_without_repeated_geometry_work(guarded_window):
    app, window, guard, areas, screen = guarded_window
    guard._fingerprint = guard._screen_fingerprint()
    guard._check_topology()
    assert not guard._timer.isActive()
    areas[screen] = QRect(0, 30, 1280, 690)
    guard._check_topology()
    assert guard._timer.isActive()


def test_main_window_installs_and_closes_its_geometry_guard(tmp_path):
    from maritime_simulator.ui.main_window import V2MainWindow

    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    assert window.window_geometry_guard.owner is window
    assert window.window_geometry_guard._poll.isActive()
    window.close()
    assert window.window_geometry_guard._closed
    assert not window.window_geometry_guard._poll.isActive()
    window.deleteLater()
    app.processEvents()
