# SPDX-License-Identifier: MPL-2.0
import json

import pytest

from maritime_simulator.application.startup_profile import (
    STARTUP_GROUPS,
    StartupProfileStore,
)


def _configuration(*, mode="ship", speed=12.0, wind=8.0, route_name="A"):
    return {
        "format": "maritime-ship-simulator-v2-configuration",
        "format_version": 1,
        "app_state": {
            "route": [{"name": route_name}],
            "route_end_action": "Stop",
            "use_planned_speed": False,
            "use_planned_turns": True,
            "control_turn_mode": "fixed_rot",
            "control_turn_rate_deg_min": 10.0,
            "control_turn_radius_nm": 0.1,
            "simulation": {
                "control_mode": mode,
                "vessel_profile_id": "crew-transfer-vessel",
                "time_scale": 1.0,
                "position": {
                    "latitude_deg": 54.123456,
                    "longitude_deg": 10.654321,
                    "heading_deg": 90.0,
                },
                "control": {"speed_order_kn": speed, "rudder_order_deg": 0.0},
                "environment": {"wind_speed_kn": wind},
                "sensor_values": {"heading_deg": 90.0},
                "sensor_coupling": {"cog_follows_heading": True},
                "auxiliary_sensors": {"depth_m": 18.0},
                "sensor_noise": {"heading_deg": [False, 0.0]},
            },
            "ais": {
                "mode": "generated",
                "output_filter_mode": "all",
                "output_min_sog_kn": 0.5,
            },
            "fault_injection": {
                "configured_faults": [],
                "activate_on_startup": False,
            },
        },
        "protocol_output": {"enabled": speed > 0.0},
        "external_rudder_input": {"input_type": "ROR"},
        "instrument_cluster": {"widgets": ["heading"]},
        "advanced_output": {"protocols": [{"cycle_active": False}]},
        "advanced_ports": [],
        "vessel_profiles": [
            {
                "profile_id": "crew-transfer-vessel",
                "display_name": "Crew Transfer Vessel",
                "length_m": 27.0,
                "max_speed_kn": 35.0,
            }
        ],
    }


def test_startup_groups_default_to_last_used_and_round_trip(tmp_path):
    path = tmp_path / "startup.json"
    store = StartupProfileStore(path)
    assert all(store.mode(group) == "last" for group in STARTUP_GROUPS)
    store.capture("mode", _configuration(mode="track"))
    store.capture("route", _configuration(route_name="FIXED"))

    restored = StartupProfileStore(path)
    assert restored.mode("mode") == "fixed"
    assert restored.mode("route") == "fixed"
    assert (
        restored.resolve(
            _configuration(mode="sensor", route_name="LAST"), _configuration()
        )["app_state"]["route"][0]["name"]
        == "FIXED"
    )


def test_startup_resolve_combines_fixed_groups_with_last_used(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    fixed = _configuration(mode="track", speed=21.0, wind=25.0, route_name="FIXED")
    store.capture("mode", fixed)
    store.capture("environment", fixed)
    last = _configuration(mode="sensor", speed=6.0, wind=2.0, route_name="LAST")

    resolved = store.resolve(last, _configuration())
    assert resolved["app_state"]["simulation"]["control_mode"] == "track"
    assert resolved["app_state"]["simulation"]["environment"]["wind_speed_kn"] == 25.0
    assert resolved["app_state"]["simulation"]["control"]["speed_order_kn"] == 6.0
    assert resolved["app_state"]["route"][0]["name"] == "LAST"


def test_vessel_models_are_an_independent_startup_group(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    fixed = _configuration()
    fixed["app_state"]["simulation"]["vessel_profile_id"] = "fixed-vessel"
    fixed["vessel_profiles"] = [
        {
            "profile_id": "fixed-vessel",
            "display_name": "Fixed Demonstrator",
            "length_m": 88.0,
        }
    ]
    store.capture("vessel_models", fixed)

    last = _configuration(mode="sensor")
    resolved = store.resolve(last, _configuration())

    assert resolved["app_state"]["simulation"]["control_mode"] == "sensor"
    assert resolved["app_state"]["simulation"]["vessel_profile_id"] == "fixed-vessel"
    assert resolved["vessel_profiles"][0]["display_name"] == "Fixed Demonstrator"


def test_version_two_startup_profile_migrates_vessel_models_from_mode(tmp_path):
    old = StartupProfileStore(tmp_path / "old.json")
    old.capture("mode", _configuration(mode="track"))
    payload = old.to_payload()
    payload["format_version"] = 2
    vessel_fragment = old.group_fragment("vessel_models", _configuration())
    payload["groups"].pop("vessel_models")
    payload["groups"]["mode"]["fixed"]["app_state"]["simulation"][
        "vessel_profile_id"
    ] = vessel_fragment["app_state"]["simulation"]["vessel_profile_id"]
    payload["groups"]["mode"]["fixed"]["vessel_profiles"] = vessel_fragment[
        "vessel_profiles"
    ]

    restored = StartupProfileStore(tmp_path / "restored.json")
    restored.apply_payload(payload)

    assert restored.mode("vessel_models") == "fixed"
    assert restored.fixed_group("vessel_models") == vessel_fragment


def test_noise_startup_group_preserves_each_channel_cycle_time(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    fixed = _configuration()
    fixed["app_state"]["simulation"]["sensor_noise"] = {
        "heading": [True, 2.5, 3.2],
        "depth": [False, 5.0, 60.0],
    }
    store.capture("noise", fixed)

    resolved = store.resolve(_configuration(), _configuration())
    assert resolved["app_state"]["simulation"]["sensor_noise"] == {
        "heading": [True, 2.5, 3.2],
        "depth": [False, 5.0, 60.0],
    }


def test_ais_startup_group_preserves_output_filter_and_threshold(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    fixed = _configuration()
    fixed["app_state"]["ais"].update({
        "output_filter_mode": "sog_dependent",
        "output_min_sog_kn": 1.3,
    })
    store.capture("ais", fixed)

    resolved = store.resolve(_configuration(), _configuration())
    assert resolved["app_state"]["ais"]["output_filter_mode"] == "sog_dependent"
    assert resolved["app_state"]["ais"]["output_min_sog_kn"] == pytest.approx(1.3)


def test_fault_injection_startup_group_preserves_selection_but_not_activation(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    fixed = _configuration()
    fixed["app_state"]["fault_injection"] = {
        "configured_faults": ["gnss_jamming", "packet_loss"],
        "activate_on_startup": False,
    }
    store.capture("fault_injection", fixed)

    resolved = store.resolve(_configuration(), _configuration())
    assert resolved["app_state"]["fault_injection"] == {
        "configured_faults": ["gnss_jamming", "packet_loss"],
        "activate_on_startup": False,
    }


def test_last_saved_snapshot_is_portable_and_can_be_updated_per_group(tmp_path):
    source = StartupProfileStore(tmp_path / "source.json")
    original = _configuration(speed=6.0, wind=2.0)
    source.capture_all_last_saved(original)

    changed = _configuration(speed=21.0, wind=25.0)
    source.capture_last_saved("control", changed)
    assert (
        source.last_saved_configuration["app_state"]["simulation"]["control"][
            "speed_order_kn"
        ]
        == 21.0
    )
    assert (
        source.last_saved_configuration["app_state"]["simulation"]["environment"][
            "wind_speed_kn"
        ]
        == 2.0
    )

    exported = tmp_path / "startup-setup.json"
    source.export_to(exported)
    restored = StartupProfileStore(tmp_path / "restored.json")
    restored.import_from(exported)
    assert restored.last_saved_configuration == source.last_saved_configuration


def test_fixed_position_heading_is_authoritative_over_sensor_snapshot(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    fixed = _configuration()
    fixed["app_state"]["simulation"]["position"]["heading_deg"] = 271.0
    store.capture("position_heading", fixed)

    last = _configuration()
    last["app_state"]["simulation"]["position"]["heading_deg"] = 90.0
    last["app_state"]["simulation"]["sensor_values"]["heading_deg"] = 90.0
    resolved = store.resolve(last, _configuration())

    assert resolved["app_state"]["simulation"]["position"]["heading_deg"] == 271.0
    assert resolved["app_state"]["simulation"]["sensor_values"]["heading_deg"] == 271.0


def test_capture_all_does_not_silently_change_startup_sources(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    current = _configuration()

    store.capture_all_fixed(current)
    assert all(store.mode(group) == "last" for group in STARTUP_GROUPS)

    store.set_all_modes("fixed")
    assert all(store.mode(group) == "fixed" for group in STARTUP_GROUPS)
    store.capture_all_last_saved(current)
    assert all(store.mode(group) == "fixed" for group in STARTUP_GROUPS)


def test_map_group_is_independent_and_fixed_and_last_saved_survive_reload(tmp_path):
    path = tmp_path / "startup.json"
    store = StartupProfileStore(path)
    fixed = _configuration()
    fixed["offline"] = {"enabled": False, "map_source": "s57", "map_file": "demo.mbtiles",
                        "water_file": "demo.mbtiles", "enc_layers": ["COALNE", "LNDARE"], "enc_base": True, "enc_cells": ["US4NJ1DG"]}
    store.capture_fixed("map", fixed, select=True)
    last = _configuration()
    last["offline"] = {"enabled": True, "map_source": "online", "map_file": "other.mbtiles",
                       "water_file": "water.mbtiles", "enc_layers": None, "enc_base": False}
    store.capture_all_last_saved(last)
    restored = StartupProfileStore(path)
    resolved = restored.resolve(last, _configuration())
    assert resolved["offline"] == {**fixed["offline"], "enabled": True, "water_file": "water.mbtiles",
                                  "enc_safety_contour_m": 30.0, "enc_safety_depth_m": 30.0}
    restored.set_mode("map", "last")
    assert StartupProfileStore(path).resolve(fixed, _configuration())["offline"] == last["offline"]
    restored.capture_fixed("offline", fixed, select=True)
    resolved = StartupProfileStore(path).resolve(last, _configuration())
    assert not resolved["offline"]["enabled"] and resolved["offline"]["water_file"] == "demo.mbtiles"
    assert resolved["offline"]["map_source"] == "online" and not resolved["offline"]["enc_base"]


def test_version_four_offline_map_selection_migrates_without_loss(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    payload = store.to_payload()
    payload["format_version"] = 4
    payload["groups"].pop("map")
    values = {"enabled": True, "map_source": "s57", "map_file": "harbour.mbtiles", "water_file": "mask.mbtiles",
              "enc_layers": ["DEPARE", "COALNE"]}
    payload["groups"]["offline"] = {"mode": "fixed", "fixed": {"offline": values}}
    store.apply_payload(payload)
    assert store.mode("offline") == store.mode("map") == "fixed"
    assert store.fixed_group("offline") == {"offline": {"enabled": True, "water_file": "mask.mbtiles"}}
    assert store.fixed_group("map") == {"offline": {"map_source": "s57", "map_file": "harbour.mbtiles",
                                                  "enc_layers": ["DEPARE", "COALNE"], "enc_base": False, "enc_cells": None,
                                                  "enc_safety_contour_m": 30.0, "enc_safety_depth_m": 30.0}}
    store.save()
    resolved = StartupProfileStore(store.path).resolve(None, _configuration())
    assert resolved["offline"] == {**values, "enc_base": False, "enc_cells": None,
                                  "enc_safety_contour_m": 30.0, "enc_safety_depth_m": 30.0}


def test_version_one_profile_migrates_fixed_sensor_heading(tmp_path):
    old = StartupProfileStore(tmp_path / "old.json")
    old.capture("sensors", _configuration())
    payload = old.to_payload()
    payload["format_version"] = 1
    payload.pop("last_saved", None)
    payload["groups"].pop("position_heading", None)
    old_values = payload["groups"]["sensors"]["fixed"]["app_state"][
        "simulation"
    ].setdefault("sensor_values", {})
    old_values["heading_deg"] = 90.0

    restored = StartupProfileStore(tmp_path / "restored.json")
    restored.apply_payload(payload)
    assert restored.mode("position_heading") == "fixed"
    position = restored.fixed_group("position_heading")["app_state"]["simulation"][
        "position"
    ]
    assert position["heading_deg"] == 90.0


def test_startup_profile_embeds_rtz_and_imports_portably(tmp_path):
    source = StartupProfileStore(tmp_path / "source.json")
    source.attach_route(
        name="demo.rtz",
        rtz_text="<route version='1.0'/>",
        route_configuration=_configuration(route_name="DEMO"),
    )
    exported = tmp_path / "portable-startup.json"
    source.export_to(exported)

    target = StartupProfileStore(tmp_path / "target.json")
    target.import_from(exported)
    assert target.route_attachment == {
        "name": "demo.rtz",
        "rtz": "<route version='1.0'/>",
    }
    assert target.mode("route") == "fixed"


def test_fixed_mode_without_snapshot_is_rejected(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    with pytest.raises(ValueError, match="No fixed startup snapshot"):
        store.set_mode("control", "fixed")


def test_invalid_import_does_not_replace_existing_store(tmp_path):
    store = StartupProfileStore(tmp_path / "startup.json")
    store.capture("mode", _configuration(mode="track"))
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"format": "wrong"}), encoding="utf-8")
    with pytest.raises(ValueError):
        store.import_from(invalid)
    assert store.mode("mode") == "fixed"
