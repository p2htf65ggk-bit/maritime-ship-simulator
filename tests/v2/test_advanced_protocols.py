# SPDX-License-Identifier: MPL-2.0
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.protocols.advanced import (
    ADVANCED_PROTOCOLS,
    XDR_PRESETS,
    encode_advanced_protocol,
    supports_auto,
)
from maritime_simulator.protocols.iec61162 import IEC61162Codec, IEC61162Error
from maritime_simulator.protocols.sensors import SensorProjector


def sensor():
    return SensorProjector().project(SimulatorService().snapshot())


def test_v2_advanced_catalog_preserves_all_54_v1_protocol_definitions():
    assert len(ADVANCED_PROTOCOLS) == 54
    assert {"HRM", "HSS", "ROR", "TRC", "TRD", "HDT", "RMC"} <= set(ADVANCED_PROTOCOLS)


def test_advanced_auto_only_resolves_when_snapshot_has_reliable_value():
    snapshot = sensor()
    assert supports_auto("HDT", "heading", snapshot)
    assert not supports_auto("HDT", "ref", snapshot)
    sentence = encode_advanced_protocol(
        "GP",
        "HDT",
        {"heading": "AUTO", "ref": "T"},
        sensor=snapshot,
        now=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    decoded = IEC61162Codec.decode(sentence)
    assert decoded.fields == ("90.0", "T")


def test_advanced_mwv_auto_uses_reference_specific_projected_wind_values():
    snapshot = replace(
        sensor(),
        heading_deg=350.0,
        relative_wind_from_bow_deg=183.0,
        relative_wind_speed_kn=2.9,
        true_wind_from_deg=10.0,
        true_wind_speed_kn=6.2,
    )
    common = {"angle": "AUTO", "speed": "AUTO", "unit": "N", "status": "A"}
    relative = IEC61162Codec.decode(
        encode_advanced_protocol("WI", "MWV", {**common, "ref": "R"}, sensor=snapshot)
    )
    theoretical = IEC61162Codec.decode(
        encode_advanced_protocol("WI", "MWV", {**common, "ref": "T"}, sensor=snapshot)
    )
    assert relative.fields == ("183.0", "R", "2.9", "N", "A")
    assert theoretical.fields == ("20.0", "T", "6.2", "N", "A")


def test_advanced_encoder_rejects_invalid_choices_ranges_and_delimiters():
    with pytest.raises(IEC61162Error, match="one of"):
        encode_advanced_protocol("GP", "HDT", {"heading": "90", "ref": "M"})
    with pytest.raises(IEC61162Error, match="exceeds"):
        encode_advanced_protocol("TI", "ROT", {"rot": "999", "status": "A"})
    with pytest.raises(IEC61162Error, match="delimiter"):
        encode_advanced_protocol("GP", "TXT", {"total": 1, "number": 1, "identifier": 1, "text": "bad,text"})


def test_ror_and_hrm_cross_field_rules_are_enforced():
    ror_values = {
        "stbd_order": 5,
        "stbd_status": "A",
        "port_order": 5,
        "port_status": "",
        "location": "B",
        "bow_order": "",
        "bow_status": "",
        "center_order": "",
        "center_status": "",
    }
    with pytest.raises(IEC61162Error, match="port status"):
        encode_advanced_protocol("HC", "ROR", ror_values)

    hrm_values = {
        field.name: ""
        for field in ADVANCED_PROTOCOLS["HRM"].fields
        if field.field_type.endswith("_optional") or field.field_type == "text_optional"
    }
    hrm_values.update(
        {
            "heel_angle": 0,
            "roll_period": 10,
            "roll_amplitude_port": 1,
            "roll_amplitude_starboard": 1,
            "status": "A",
            "sentence_status": "C",
        }
    )
    with pytest.raises(IEC61162Error, match="HRM command"):
        encode_advanced_protocol("IN", "HRM", hrm_values)


def test_xdr_limits_units_to_transducer_type_and_allows_null_unit():
    pressure = {"type": "P", "value": "1.02", "unit": "B", "name": "BARO"}
    assert IEC61162Codec.decode(encode_advanced_protocol("WI", "XDR", pressure)).formatter == "XDR"
    with pytest.raises(IEC61162Error, match="invalid for type P"):
        encode_advanced_protocol("WI", "XDR", {**pressure, "unit": "V"})
    generic = {"type": "G", "value": "1", "unit": "", "name": "GENERIC"}
    decoded = IEC61162Codec.decode(encode_advanced_protocol("WI", "XDR", generic))
    assert decoded.fields[2] == ""
    generic.update(unit="P", value="-45")
    decoded = IEC61162Codec.decode(encode_advanced_protocol("WI", "XDR", generic))
    assert decoded.fields == ("G", "-45", "P", "GENERIC")
    with pytest.raises(IEC61162Error, match="invalid for type G"):
        encode_advanced_protocol("WI", "XDR", {**generic, "unit": "R"})


def test_recommended_xdr_presets_use_valid_iec_61162_1_2024_combinations():
    assert {preset.group for preset in XDR_PRESETS} >= {
        "Dew point", "Relative humidity", "Pressure", "Temperature", "Voltage"
    }
    assert all(preset.identifier.endswith("#0") for preset in XDR_PRESETS)
    for preset in XDR_PRESETS:
        values = {
            "type": preset.transducer_type,
            "value": "AUTO" if preset.model_linked else "1",
            "unit": preset.unit,
            "name": preset.identifier,
        }
        decoded = IEC61162Codec.decode(
            encode_advanced_protocol("WI", "XDR", values, sensor=sensor())
        )
        assert decoded.fields[0] == preset.transducer_type
        assert decoded.fields[2:] == (preset.unit, preset.identifier)


def test_xdr_auto_presets_follow_projected_sensor_and_environment_values():
    snapshot = replace(
        sensor(),
        xdr_air_temperature_c=21.4,
        water_temperature_c=7.2,
        xdr_relative_humidity_percent=76.5,
        xdr_dew_point_c=12.3,
        xdr_surface_pressure_bar=1.023,
        rudder_response_starboard_deg=-8.4,
        pitch_deg=1.2,
        roll_deg=-2.3,
    )
    cases = (
        ("W", "C", "Air#0", "12.3"),
        ("C", "C", "Air#0", "21.4"),
        ("C", "C", "Water#0", "7.2"),
        ("H", "P", "Air#0", "76.5"),
        ("P", "B", "Baro#0", "1.023"),
        ("A", "D", "Rudder#0", "-8.4"),
        ("A", "D", "Pitch#0", "1.2"),
        ("A", "D", "Roll#0", "-2.3"),
    )
    for transducer_type, unit, identifier, expected in cases:
        values = {
            "type": transducer_type,
            "value": "AUTO",
            "unit": unit,
            "name": identifier,
        }
        decoded = IEC61162Codec.decode(
            encode_advanced_protocol("WI", "XDR", values, sensor=snapshot)
        )
        assert decoded.fields == (transducer_type, expected, unit, identifier)
        assert supports_auto("XDR", "value", snapshot, values)
