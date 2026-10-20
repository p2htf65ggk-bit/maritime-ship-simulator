# SPDX-License-Identifier: MPL-2.0
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHIP_SIMULATOR_HEADLESS_MAP", "1")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel

from maritime_simulator.domain.commands import SetControlMode, SetBowThrusterOrder, SetExternalActuatorPolicy, ExternalActuatorOrder, SetExternalRudderInputType
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.ui.main_window import V2MainWindow
from ui.style import apply_global_ui_style


def test_controls_follow_external_orders_and_unlock_after_loss(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    window = V2MainWindow(config_dir=tmp_path)
    now = [100.0]
    window.service._monotonic = lambda: now[0]
    try:
        window.timer.stop()
        window.show()
        window.service.submit(SetControlMode(ControlMode.SHIP))
        window.service.submit(SetExternalActuatorPolicy("bow_thruster", "TRC"))
        window.service.submit(SetExternalActuatorPolicy("engine", "PRC"))
        window.service.submit(ExternalActuatorOrder("bow_thruster", "TRC", -65, 100))
        window.service.submit(ExternalActuatorOrder("engine", "PRC", 40, 100))
        window.service.advance_wall_time(.5)
        window._refresh(force=True)
        app.processEvents()
        bow = window.mode_controls._bow_fields[ControlMode.SHIP]
        telegraph = window.mode_controls._engine_fields[ControlMode.SHIP]
        assert bow.slider.value() == -65
        assert not bow.slider.isEnabled()
        assert not telegraph.isEnabled()
        assert telegraph.currentData() == window.service.snapshot().speed_order_kn
        assert telegraph.slider.value() == 560  # 40% ahead of the central Stop position.
        assert bow.y() > telegraph.y()
        window._open_instruments()
        window.instrument_cluster.refresh()
        instrument = window.instrument_cluster.instruments["bow_thruster"]
        assert instrument.order == -65
        assert -65 < instrument.response < 0
        if output := os.environ.get("BOW_THRUSTER_UI_CAPTURE"):
            Path(output).mkdir(parents=True, exist_ok=True)
            instrument.grab().save(str(Path(output) / "instrument.png"))
        now[0] += 2.6
        window.service.advance_wall_time(.1)
        window._refresh(force=True)
        assert bow.slider.isEnabled()
        assert bow.slider.value() == 0
        assert telegraph.isEnabled()
        bow.slider.setValue(30)
        window.service.advance_wall_time(.1)
        window._refresh(force=True)
        assert window.service.snapshot().bow_thruster_order_percent == 30
        QTest.mouseDClick(bow.slider, Qt.LeftButton)
        window.service.advance_wall_time(.1)
        assert window.service.snapshot().bow_thruster_order_percent == 0
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_external_input_tabs_units_and_configuration_startup_roundtrip(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    window = V2MainWindow(config_dir=tmp_path)
    try:
        window.timer.stop()
        window.resize(1280, 800)
        window.show()
        window._open_settings()
        dialog = window.settings_dialog
        dialog.tabs.setCurrentIndex(dialog._input_tab_index)
        assert dialog.tabs.tabText(dialog._input_tab_index) == "External Input"
        assert [dialog.input_groups.tabText(i) for i in range(3)] == ["Rudder", "Bow Thruster", "Engine Telegraph"]
        for actuator, primary, identifier in (("bow_thruster", "TRC", "BowThrust#0"), ("engine", "PRC", "EngTele#0")):
            panel = dialog.actuator_input_panels[actuator]
            assert panel.xdr_fields["xdr_id"].text() == identifier
            type_field = panel.xdr_fields["xdr_type"]
            unit_field = panel.xdr_fields["xdr_unit"]
            assert isinstance(type_field, QComboBox) and not type_field.isEditable()
            assert isinstance(unit_field, QComboBox) and not unit_field.isEditable()
            assert [type_field.itemData(i) for i in range(type_field.count())] == ["A", "G"]
            assert type_field.currentData() == "A"
            assert [unit_field.itemData(i) for i in range(unit_field.count())] == ["P"]
            assert panel.number.value() == 1
            assert panel.tabs.tabText(0).startswith(primary)
            assert panel.policy.findData("MANUAL") >= 0
        editor = dialog.profile_editor
        profile_id = editor.profile.currentData()
        initial = window.service.profiles.get(profile_id).bow_thruster_power_kw
        editor.thruster_power_unit.setCurrentText("MW")
        assert editor._fields["bow_thruster_power_kw"].value() == pytest.approx(initial / 1000)
        editor._fields["bow_thruster_power_kw"].setValue(.75)
        editor._save_field("bow_thruster_power_kw")
        assert window.service.profiles.get(profile_id).bow_thruster_power_kw == 750
        editor.thruster_power_unit.setCurrentText("kW")
        assert editor._fields["bow_thruster_power_kw"].value() == 750
        thruster_values = {
            "bow_thruster_power_kw": 750,
            "bow_thruster_rated_rpm": 410,
            "bow_thruster_ramp_s": 7,
            "bow_thruster_half_effect_speed_kn": 4,
            "bow_thruster_thrust_kn_per_kw": .15,
            "bow_thruster_fraction_from_bow": .06,
            "displacement_t": 18000,
        }
        window.service.profiles.update_profile(profile_id, **thruster_values)
        window.input_runtime.stop()
        window.input_runtime.update(
            input_type="PRIO_XDR_ORDER_RSA", on_loss="FREEZE",
            xdr_order={"xdr_type": "G", "xdr_unit": "P", "xdr_id": "Steering#7", "port_id": "Steering#8"},
            xdr_response={"xdr_type": "G", "xdr_unit": "P", "xdr_id": "Actual#7", "port_id": "Actual#8"},
            bow_thruster={"policy": "PRIO_TRC_XDR", "on_loss": "FREEZE", "xdr_id": "Bow#7", "xdr_type": "G"},
            engine={"policy": "XDR", "on_loss": "ZERO"},
        )
        window.service.submit(SetControlMode(ControlMode.SHIP))
        window.service.submit(ExternalActuatorOrder("bow_thruster", "TRC", 27, window.service._monotonic()))
        window.service.advance_wall_time(.1)
        payload = window._configuration_payload()
        assert payload["app_state"]["simulation"]["control"]["bow_thruster_percent"] == 27
        window.startup_profile.capture_all_fixed(payload)
        fixed = window.startup_profile.fixed_groups["external_rudder_input"]["external_rudder_input"]
        assert fixed == payload["external_rudder_input"]
        saved_profile = next(item for item in payload["vessel_profiles"] if item["profile_id"] == profile_id)
        fixed_profile = next(item for item in window.startup_profile.fixed_groups["vessel_models"]["vessel_profiles"] if item["profile_id"] == profile_id)
        for key, value in thruster_values.items():
            assert saved_profile[key] == fixed_profile[key] == value
        assert fixed["bow_thruster"]["xdr_id"] == "Bow#7"
        assert fixed["bow_thruster"]["xdr_type"] == "G"
        assert fixed["bow_thruster"]["on_loss"] == "FREEZE"
        assert fixed["on_loss"] == "FREEZE"
        assert fixed["xdr_order"]["port_id"] == "Steering#8"
        assert fixed["xdr_response"]["port_id"] == "Actual#8"
        assert fixed["engine"]["on_loss"] == "ZERO"
        window._apply_configuration_payload(payload)
        window.service.advance_wall_time(.1)
        assert window.input_runtime.settings.engine.policy == "XDR"
        assert window.input_runtime.settings.bow_thruster.xdr_type == "G"
        assert window.input_runtime.settings.input_type == "PRIO_XDR_ORDER_RSA"
        assert window.input_runtime.settings.xdr_order.xdr_unit == "P"
        assert window.input_runtime.settings.xdr_order.xdr_id == "Steering#7"
        assert window.input_runtime.settings.on_loss == "FREEZE"
        assert window.input_runtime.settings.xdr_response.xdr_id == "Actual#7"
        for key, value in thruster_values.items():
            assert getattr(window.service.profiles.get(profile_id), key) == value
        dialog._load_input_fields()
        bow_panel = dialog.actuator_input_panels["bow_thruster"]
        assert bow_panel.xdr_fields["xdr_type"].currentData() == "G"
        bow_panel.xdr_fields["xdr_type"].setCurrentIndex(0)
        assert window.input_runtime.settings.bow_thruster.xdr_type == "A"
        bow_panel.xdr_fields["xdr_type"].setCurrentIndex(1)
        assert window.input_runtime.settings.bow_thruster.xdr_type == "G"
        assert bow_panel.xdr_fields["xdr_unit"].currentData() == "P"
        window.service.submit(SetExternalRudderInputType("MANUAL"))
        window.service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
        window.service.advance_wall_time(.1)
        window._refresh(force=True)
        app.processEvents()
        external_page = window.mode_controls._external_manual_rudder.parentWidget()
        assert any(label.text() == "Rudder" and label.y() < window.mode_controls._external_manual_rudder.y()
                   for label in external_page.findChildren(QLabel))
        # Optional repeatable native-widget screenshots for visual review.
        if output := os.environ.get("BOW_THRUSTER_UI_CAPTURE"):
            root = Path(output)
            root.mkdir(parents=True, exist_ok=True)
            for group in (0, 1, 2):
                dialog.input_groups.setCurrentIndex(group)
                if group:
                    dialog.actuator_input_panels["bow_thruster" if group == 1 else "engine"].tabs.setCurrentIndex(1)
                app.processEvents()
                dialog.grab().save(str(root / f"external-{group}.png"))
            dialog.hide()
            window.window_geometry_guard.close()
            window.resize(1280, 800)
            window._refresh(force=True)
            app.processEvents()
            window.grab().save(str(root / "controls.png"))
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
