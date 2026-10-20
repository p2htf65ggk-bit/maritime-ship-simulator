# SPDX-License-Identifier: MPL-2.0
from dataclasses import replace
from datetime import datetime, timezone
import math

import pytest

from maritime_simulator.application.configuration import ProtocolConfigurationStore
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import SetEnvironment, SetRudderOrder, SetSensorNoise
from maritime_simulator.domain.enums import TransportKind
from maritime_simulator.protocols.iec61162 import (
    IEC61162Codec,
    IEC61162Error,
    REGULAR_PROTOCOLS,
    encode_ais_position_report,
    encode_ais_static_voyage_report,
    encode_protocol,
    encode_protocol_sentences,
)
from maritime_simulator.protocols.scheduler import ProtocolSchedule, SentenceScheduler
from maritime_simulator.protocols.sensors import SensorProjector
from maritime_simulator.protocols.settings import SensorProtocolSettings


def test_sensor_projection_and_ror_round_trip_use_only_snapshot_values():
    service = SimulatorService()
    service.submit(SetRudderOrder(-14.5))
    service.advance_wall_time(0.1)
    sensor = SensorProjector().project(service.snapshot())
    sentence = encode_protocol("ROR", sensor, datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    decoded = IEC61162Codec.decode_ror(sentence)
    assert decoded.starboard_or_single_deg == -14.5
    assert decoded.starboard_valid is True


def test_invalid_checksum_is_rejected():
    with pytest.raises(IEC61162Error, match="Checksum mismatch"):
        IEC61162Codec.decode_ror("$AGROR,10.0,A,,,B,,,,*00")


def test_rsa_decoder_accepts_dual_rudder_response_sentence():
    sentence = IEC61162Codec.frame("HCRSA,12.5,A,-7.0,A")
    decoded = IEC61162Codec.decode_rsa(sentence)
    assert decoded.starboard_or_single_deg == 12.5
    assert decoded.port_deg == -7.0
    assert decoded.starboard_valid is True
    assert decoded.port_valid is True


def test_rsa_decoder_accepts_valid_port_when_starboard_field_is_empty():
    decoded = IEC61162Codec.decode_rsa("$AGRSA,,V,-0.0,A*52")
    assert decoded.starboard_or_single_deg is None
    assert decoded.starboard_valid is False
    assert decoded.port_deg == pytest.approx(0.0)
    assert decoded.port_valid is True


def test_rsa_output_distinguishes_single_and_dual_rudder_topology():
    service = SimulatorService()
    sensor = replace(
        SensorProjector().project(service.snapshot()),
        rudder_response_starboard_deg=8.0,
        rudder_response_port_deg=-6.0,
        dual_rudder=False,
    )
    single = IEC61162Codec.decode_rsa(
        encode_protocol("RSA", sensor, datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    )
    assert single.starboard_or_single_deg == 8.0
    assert single.port_deg is None
    assert single.port_valid is None

    dual = IEC61162Codec.decode_rsa(
        encode_protocol("RSA", replace(sensor, dual_rudder=True), datetime.now(timezone.utc))
    )
    assert dual.starboard_or_single_deg == 8.0
    assert dual.port_deg == -6.0
    assert dual.port_valid is True
    service.close()


def test_sensor_level_pushes_down_and_protocol_change_only_affects_child():
    settings = SensorProtocolSettings(("GGA", "RMC", "VTG"))
    settings.update_sensor(host="239.192.192.1", port=4097, rate_hz=2.0)
    assert all(settings.get_protocol(name).host == "239.192.192.1" for name in settings.protocols())
    assert settings.aggregate("port") == 4097

    settings.update_protocol("VTG", port=5000)
    assert settings.get_protocol("GGA").port == 4097
    assert settings.get_protocol("VTG").port == 5000
    assert settings.aggregate("port") is None


def test_scheduler_keeps_each_protocol_rate_independent_and_evenly_timed():
    scheduler = SentenceScheduler(monotonic=lambda: 0.0)
    scheduler.configure(ProtocolSchedule("gga", "GGA", 2.0, "udp"), now_s=0.0)
    scheduler.configure(ProtocolSchedule("rot", "ROT", 5.0, "udp"), now_s=0.0)
    assert {item.protocol for item in scheduler.due(0.0)} == {"GGA", "ROT"}
    assert [item.protocol for item in scheduler.due(0.2)] == ["ROT"]
    assert [item.protocol for item in scheduler.due(0.4)] == ["ROT"]
    assert {item.protocol for item in scheduler.due(0.5)} == {"GGA"}
    assert [item.protocol for item in scheduler.due(0.6)] == ["ROT"]


def test_every_regular_scalar_protocol_is_valid_and_within_iec_length_limit():
    sensor = SensorProjector().project(SimulatorService().snapshot())
    sensor = replace(
        sensor,
        heading_deg=359.9,
        cog_deg=359.9,
        sog_kn=60.0,
        stw_kn=60.0,
        true_wind_from_deg=359.9,
        true_wind_speed_kn=120.0,
        relative_wind_from_bow_deg=359.9,
        relative_wind_speed_kn=120.0,
    )
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    for protocol in (item for item in REGULAR_PROTOCOLS if item not in {"VDM", "VDO"}):
        sentence = encode_protocol(protocol, sensor, now)
        decoded = IEC61162Codec.decode(sentence)
        assert decoded.formatter == ("XDR" if protocol.startswith("XDR_") else protocol)
        assert len(sentence) + 2 <= 82


def test_dtm_reports_wgs84_as_local_and_reference_datum():
    sensor = SensorProjector().project(SimulatorService().snapshot())
    decoded = IEC61162Codec.decode(encode_protocol("DTM", sensor))
    assert decoded.talker == "GP"
    assert decoded.formatter == "DTM"
    assert decoded.fields == ("W84", "", "0.0000", "N", "0.0000", "E", "0.0", "W84")


def test_vbw_includes_stern_transverse_water_and_ground_speed_pairs():
    sensor = replace(
        SensorProjector().project(SimulatorService().snapshot()),
        stw_kn=12.1,
        transverse_water_kn=0.2,
        sog_kn=12.3,
        transverse_ground_kn=0.3,
        aft_transverse_water_kn=-0.4,
        aft_transverse_ground_kn=0.5,
    )
    decoded = IEC61162Codec.decode(encode_protocol("VBW", sensor))
    assert decoded.fields == (
        "12.1", "0.2", "A",
        "12.3", "0.3", "A",
        "-0.4", "A",
        "0.5", "A",
    )


def test_mwv_schedule_payload_contains_relative_and_true_theoretical_sentences():
    sensor = replace(
        SensorProjector().project(SimulatorService().snapshot()),
        heading_deg=350.0,
        relative_wind_from_bow_deg=183.0,
        relative_wind_speed_kn=2.9,
        true_wind_from_deg=10.0,
        true_wind_speed_kn=6.2,
    )
    relative, theoretical = (
        IEC61162Codec.decode(sentence)
        for sentence in encode_protocol_sentences("MWV", sensor)
    )
    assert relative.fields == ("183.0", "R", "2.9", "N", "A")
    assert theoretical.fields == ("20.0", "T", "6.2", "N", "A")


def test_standard_xdr_environment_sentences_are_individual_and_show_their_ids():
    sensor = replace(
        SensorProjector().project(SimulatorService().snapshot()),
        xdr_air_temperature_c=21.4,
        xdr_relative_humidity_percent=76.5,
        xdr_dew_point_c=12.3,
        xdr_surface_pressure_bar=1.023,
        air_temperature_c=-4.0,
        barometric_pressure_bar=0.900,
    )
    expected = (
        ("XDR_AIR_TEMPERATURE", ("C", "21.4", "C", "Air#0")),
        ("XDR_RELATIVE_HUMIDITY", ("H", "76.5", "P", "Air#1")),
        ("XDR_DEW_POINT", ("W", "12.3", "C", "Air#2")),
        ("XDR_SURFACE_PRESSURE", ("P", "1.023", "B", "Baro#0")),
    )
    for protocol, fields in expected:
        decoded = IEC61162Codec.decode(encode_protocol(protocol, sensor))
        assert decoded.formatter == "XDR"
        assert decoded.fields == fields
        assert len(decoded.raw) + 2 <= 82


def test_xdr_environment_has_its_own_disabled_output_section():
    store = ProtocolConfigurationStore()
    protocols = store.sensors["XDR Environment"].protocols()
    assert protocols == (
        "XDR_AIR_TEMPERATURE",
        "XDR_RELATIVE_HUMIDITY",
        "XDR_DEW_POINT",
        "XDR_SURFACE_PRESSURE",
    )
    for protocol in protocols:
        assert store.sensor_for_protocol(protocol) == "XDR Environment"
        for transport in TransportKind:
            assert not store.sensors["XDR Environment"].get_protocol(
                protocol, transport
            ).enabled


def test_directional_protocols_normalize_rounded_360_to_zero():
    sensor = replace(
        SensorProjector().project(SimulatorService().snapshot()),
        heading_deg=359.99,
        cog_deg=359.99,
        true_wind_from_deg=359.99,
        relative_wind_from_bow_deg=359.99,
        current_set_deg=359.99,
    )
    for protocol in ("HDT", "HDG", "HDM", "THS", "VHW", "RMC", "VTG", "MWV", "MWD", "MDA", "VDR"):
        decoded = IEC61162Codec.decode(encode_protocol(protocol, sensor))
        assert "360.0" not in decoded.fields, protocol


def test_ais_position_report_is_valid_iec_vdm_sentence():
    sentence = encode_ais_position_report(
        {
            "mmsi": "211123456",
            "lat": 54.1234,
            "lon": 10.4321,
            "speed": 12.3,
            "course": 271.2,
            "heading": 270,
            "nav_status": "under way",
        }
    )
    decoded = IEC61162Codec.decode(sentence)
    assert decoded.talker == "AI"
    assert decoded.formatter == "VDM"
    assert len(sentence) + 2 <= 82


def test_ais_own_ship_position_report_is_valid_iec_vdo_sentence():
    sentence = encode_ais_position_report(
        {
            "mmsi": 211000001,
            "lat": 54.1234,
            "lon": 10.4321,
            "speed": 12.3,
            "course": 271.2,
            "heading": 270,
            "nav_status": "under way",
        },
        formatter="VDO",
    )
    decoded = IEC61162Codec.decode(sentence)
    assert decoded.talker == "AI"
    assert decoded.formatter == "VDO"
    assert len(sentence) + 2 <= 82


def test_ais_position_report_carries_the_current_utc_second_when_supplied():
    sentence = encode_ais_position_report(
        {
            "mmsi": 211000001,
            "lat": 54.1234,
            "lon": 10.4321,
            "speed": 12.3,
            "course": 271.2,
            "heading": 270,
        },
        utc_second=37,
    )
    payload = IEC61162Codec.decode(sentence).fields[4]
    bits = "".join(
        format((ord(char) - 48) - (8 if ord(char) - 48 > 40 else 0), "06b")
        for char in payload
    )
    assert int(bits[137:143], 2) == 37


def test_ais_static_voyage_report_carries_name_callsign_and_dimensions():
    sentences = encode_ais_static_voyage_report({
        "mmsi": 211000001,
        "name": "TEST VESSEL",
        "callsign": "DTEST",
        "ship_type": "Cargo",
        "length": 123.0,
        "beam": 21.0,
        "draught": 6.4,
        "destination": "KIEL",
    }, sequence_id=4)
    decoded = tuple(IEC61162Codec.decode(sentence) for sentence in sentences)
    assert [item.fields[:4] for item in decoded] == [
        ("2", "1", "4", "A"),
        ("2", "2", "4", "A"),
    ]
    assert all(len(sentence) + 2 <= 82 for sentence in sentences)
    payload = "".join(item.fields[4] for item in decoded)
    bits = "".join(
        format((ord(char) - 48) - (8 if ord(char) - 48 > 40 else 0), "06b")
        for char in payload
    )
    assert int(bits[:6], 2) == 5
    assert _decode_ais_text(bits[70:112]) == "DTEST"
    assert _decode_ais_text(bits[112:232]) == "TEST VESSEL"


def _decode_ais_text(bits: str) -> str:
    alphabet = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"
    text = "".join(alphabet[int(bits[index:index + 6], 2)] for index in range(0, len(bits), 6))
    return text.rstrip("@ ")


def test_prc_retains_lever_rpm_pitch_location_and_shaft_fields():
    sensor = replace(
        SensorProjector().project(SimulatorService().snapshot()),
        propeller_lever_demand_percent=-50.0,
    )
    decoded = IEC61162Codec.decode(encode_protocol("PRC", sensor))
    assert decoded.fields == ("-50.0", "A", "50.0", "P", "-50.0", "P", "B", "0")


def test_sensor_pushdown_rejects_invalid_output_configuration_atomically():
    settings = SensorProtocolSettings(("GGA", "RMC"))
    with pytest.raises(ValueError, match="port"):
        settings.update_sensor(port=70000)
    assert settings.get_protocol("GGA").port == 4096
    assert settings.get_protocol("RMC").port == 4096


def test_individual_noise_controls_reach_protocol_projection_only():
    service = SimulatorService()
    service.submit(SetEnvironment(current_speed_kn=5.0))
    service.advance_wall_time(0.0)
    baseline = service.snapshot()
    service.submit(SetSensorNoise("current_speed", enabled=True, amplitude=2.0))
    service.submit(SetSensorNoise("rudder_response_starboard", enabled=True, amplitude=3.0))
    service.advance_wall_time(0.0)
    projected = SensorProjector().project(service.snapshot())
    assert projected.current_drift_kn != baseline.current_speed_kn
    assert projected.rudder_response_starboard_deg != baseline.rudder_response_starboard_deg
    unchanged = service.snapshot()
    assert unchanged.current_speed_kn == baseline.current_speed_kn
    assert unchanged.rudder_response_starboard_deg == baseline.rudder_response_starboard_deg


def test_each_noise_channel_uses_its_configured_cycle_time_and_accepts_legacy_pairs():
    service = SimulatorService()
    base = replace(service.snapshot(), simulation_time_s=1.0, heading_deg=90.0)
    fast = replace(base, sensor_noise=(("heading", (True, 2.0, 4.0)),))
    legacy = replace(base, sensor_noise=(("heading", (True, 2.0)),))

    fast_heading = SensorProjector().project(fast).heading_deg
    legacy_heading = SensorProjector().project(legacy).heading_deg

    assert fast_heading == pytest.approx(92.0)
    assert legacy_heading == pytest.approx(90.0 + 2.0 * math.sin(0.37))


def test_linked_wind_and_humidity_noise_use_one_shared_error_sample():
    service = SimulatorService()
    service.submit(SetSensorNoise("wind_direction", enabled=True, amplitude=8.0))
    service.submit(SetSensorNoise("wind_speed", enabled=True, amplitude=3.0))
    service.submit(SetSensorNoise("humidity", enabled=True, amplitude=4.0))
    service.advance_wall_time(0.1)
    snapshot = service.snapshot()
    projected = SensorProjector().project(snapshot)
    assert (projected.true_wind_from_deg - snapshot.true_wind_from_deg) % 360.0 == pytest.approx(
        (projected.relative_wind_from_bow_deg - snapshot.relative_wind_from_bow_deg) % 360.0
    )
    assert projected.true_wind_speed_kn - snapshot.true_wind_speed_kn == pytest.approx(
        projected.relative_wind_speed_kn - snapshot.relative_wind_speed_kn
    )
    assert projected.relative_humidity_percent - snapshot.relative_humidity_percent == pytest.approx(
        projected.absolute_humidity_percent - snapshot.absolute_humidity_percent
    )


def test_mwv_theoretical_uses_the_same_direction_and_speed_noise_as_other_wind_data():
    service = SimulatorService()
    service.advance_wall_time(0.1)
    clean_sensor = SensorProjector().project(service.snapshot())

    service.submit(SetSensorNoise("wind_direction", enabled=True, amplitude=8.0))
    service.submit(SetSensorNoise("wind_speed", enabled=True, amplitude=3.0))
    service.advance_wall_time(0.0)
    noisy_sensor = SensorProjector().project(service.snapshot())

    clean_relative, clean_theoretical = (
        IEC61162Codec.decode(sentence)
        for sentence in encode_protocol_sentences("MWV", clean_sensor)
    )
    noisy_relative, noisy_theoretical = (
        IEC61162Codec.decode(sentence)
        for sentence in encode_protocol_sentences("MWV", noisy_sensor)
    )
    clean_mwd = IEC61162Codec.decode(encode_protocol("MWD", clean_sensor))
    noisy_mwd = IEC61162Codec.decode(encode_protocol("MWD", noisy_sensor))

    def signed_delta(noisy: str, clean: str) -> float:
        return (float(noisy) - float(clean) + 180.0) % 360.0 - 180.0

    mwd_direction_noise = signed_delta(noisy_mwd.fields[0], clean_mwd.fields[0])
    assert signed_delta(noisy_relative.fields[0], clean_relative.fields[0]) == pytest.approx(
        mwd_direction_noise,
        abs=0.1,
    )
    assert signed_delta(noisy_theoretical.fields[0], clean_theoretical.fields[0]) == pytest.approx(
        mwd_direction_noise,
        abs=0.1,
    )
    mwd_speed_noise = float(noisy_mwd.fields[4]) - float(clean_mwd.fields[4])
    assert float(noisy_relative.fields[2]) - float(clean_relative.fields[2]) == pytest.approx(
        mwd_speed_noise,
        abs=0.1,
    )
    assert float(noisy_theoretical.fields[2]) - float(clean_theoretical.fields[2]) == pytest.approx(
        mwd_speed_noise,
        abs=0.1,
    )
