# SPDX-License-Identifier: MPL-2.0
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QPushButton

from maritime_simulator.application.startup_profile import StartupProfileStore
from maritime_simulator.domain.commands import SetSpeedOrder
from maritime_simulator.ui.main_window import V2MainWindow, _advanced_has_active_cycles
from maritime_simulator.ui.startup_profile_panel import _details, _summary


def test_settings_exposes_grouped_startup_page(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        window._open_settings()
        dialog = window.settings_dialog
        assert dialog is not None
        assert dialog.tabs.tabText(0) == "Start-up"
        assert tuple(dialog.startup_panel._modes) == (
            "control",
            "position_heading",
            "environment",
            "ais",
            "sensors",
            "noise",
            "fault_injection",
            "mode",
            "offline",
            "map",
            "vessel_models",
            "protocol_output",
            "external_rudder_input",
            "instruments",
            "advanced",
            "route",
        )
        assert dialog.startup_panel.table.horizontalHeaderItem(2).text() == "Current"
        assert dialog.startup_panel.table.horizontalHeaderItem(3).text() == "Last Saved"
        assert not any(
            button.text() == "Choose RTZ..."
            for button in dialog.startup_panel.findChildren(QPushButton)
        )
        assert dialog.startup_panel._items["control"]["last"].font().bold()
        assert not dialog.startup_panel._items["control"]["current"].font().bold()
        buttons = dialog.startup_panel.action_buttons
        assert len(buttons) == 8
        positions = {
            dialog.startup_panel.action_grid.getItemPosition(
                dialog.startup_panel.action_grid.indexOf(button)
            )[:2]
            for button in buttons
        }
        assert positions == {
            (0, 0), (0, 1), (0, 2), (0, 3),
            (1, 0), (1, 1), (1, 2), (1, 3),
        }
        dialog.show()
        app.processEvents()
        assert max(button.width() for button in buttons) - min(button.width() for button in buttons) <= 1
        assert len({button.height() for button in buttons}) == 1
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_fixed_mode_combines_with_last_used_control_at_next_start(tmp_path):
    app = QApplication.instance() or QApplication([])
    first = V2MainWindow(config_dir=tmp_path)
    second = None
    try:
        first.timer.stop()
        fixed = first._configuration_payload()
        fixed["app_state"]["simulation"]["control_mode"] = "track"
        first.startup_profile.capture("mode", fixed)

        last = first._configuration_payload()
        last["app_state"]["simulation"]["control_mode"] = "sensor"
        last["app_state"]["simulation"]["control"]["speed_order_kn"] = 6.0
        last["app_state"]["use_planned_speed"] = False
        first.startup_profile.capture_all_last_saved(last)
        first.close()
        first.deleteLater()
        app.processEvents()

        second = V2MainWindow(config_dir=tmp_path)
        second.timer.stop()
        snapshot = second.service.snapshot()
        assert snapshot.control_mode.value == "track"
        assert snapshot.speed_order_kn == 6.0
    finally:
        if second is not None:
            second.close()
            second.deleteLater()
        app.processEvents()


def test_close_without_saving_preserves_separate_last_saved_and_fixed_values(tmp_path):
    app = QApplication.instance() or QApplication([])
    first = V2MainWindow(config_dir=tmp_path)
    second = None
    third = None
    try:
        first.timer.stop()
        last_saved = first._configuration_payload()
        last_saved["app_state"]["simulation"]["control_mode"] = "sensor"
        last_saved["app_state"]["simulation"]["control"]["speed_order_kn"] = 6.0
        last_saved["app_state"]["use_planned_speed"] = False
        fixed = first._configuration_payload()
        fixed["app_state"]["simulation"]["control_mode"] = "track"
        first.startup_profile.capture_all_last_saved(last_saved)
        first.startup_profile.capture("mode", fixed)
        first.close()
        first.deleteLater()
        app.processEvents()

        second = V2MainWindow(config_dir=tmp_path)
        second.timer.stop()
        assert second.service.snapshot().control_mode.value == "track"
        assert second.service.snapshot().speed_order_kn == 6.0

        second._submit_ui_command(SetSpeedOrder(12.0))
        second._interactive_close_confirmation_enabled = lambda: True
        second._request_close_confirmation = lambda _has_changes: "close"
        close_event = QCloseEvent()
        second.closeEvent(close_event)
        assert close_event.isAccepted()

        persisted = StartupProfileStore(tmp_path / "startup_profile_v2.json")
        assert persisted.mode("mode") == "fixed"
        assert (
            persisted.last_saved_configuration["app_state"]["simulation"]["control_mode"]
            == "sensor"
        )
        assert (
            persisted.last_saved_configuration["app_state"]["simulation"]["control"]["speed_order_kn"]
            == 6.0
        )

        third = V2MainWindow(config_dir=tmp_path)
        third.timer.stop()
        assert third.service.snapshot().control_mode.value == "track"
        assert third.service.snapshot().speed_order_kn == 6.0
    finally:
        for window in (third, second, first):
            if window is not None:
                window.close()
                window.deleteLater()
        app.processEvents()


def test_only_explicitly_active_advanced_cycles_are_detected_for_autostart():
    assert not _advanced_has_active_cycles({"protocols": [{"cycle_active": False}]})
    assert _advanced_has_active_cycles({"protocols": [{"cycle_active": True}]})
    assert _advanced_has_active_cycles({"free_text": [{"cycle_active": True}]})


def test_startup_summary_rounds_physical_values_to_one_decimal(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        payload = window.startup_profile.group_fragment(
            "control", window._configuration_payload()
        )
        payload["app_state"]["simulation"]["control"]["speed_order_kn"] = 12.345678
        text = _summary("control", payload)
        assert "12.3 kn" in text
        assert "12.345" not in text
        vessels = window.startup_profile.group_fragment(
            "vessel_models", window._configuration_payload()
        )
        vessel_text = _summary("vessel_models", vessels)
        assert window.service.snapshot().vessel_profile_id not in vessel_text
        assert "Crew Transfer Vessel" in vessel_text
        assert "customized/added model(s)" in vessel_text
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_general_tab_refreshes_changed_external_source_without_reopening(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        window._open_settings()
        panel = window.settings_dialog.startup_panel
        assert panel._items["external_rudder_input"]["current"].text() == "Rudder: ROR; Bow: MANUAL; Engine: MANUAL"

        window.input_runtime.settings.input_type = "RSA"
        panel.refresh_if_changed()
        assert panel._items["external_rudder_input"]["current"].text() == "Rudder: RSA; Bow: MANUAL; Engine: MANUAL"
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_startup_details_are_human_readable_and_hide_credentials():
    details = _details(
        "ais",
        {
            "app_state": {
                "ais": {
                    "mode": "live",
                    "live": {
                        "provider": "aisstream",
                        "subscription_range_nm": 250.1234,
                        "api_key": "must-not-appear",
                    },
                }
            }
        },
    )
    assert "Provider: aisstream" in details
    assert "Subscription range: 250.1 NM" in details
    assert len(details.splitlines()) <= 8
    assert "must-not-appear" not in details
    assert "api key" not in details.lower()
    assert "{" not in details
