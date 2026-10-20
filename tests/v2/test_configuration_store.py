# SPDX-License-Identifier: MPL-2.0
import json

from maritime_simulator.application.configuration import ProtocolConfigurationStore
from maritime_simulator.domain.enums import TransportKind


def test_protocol_configuration_persists_pushdown_and_child_aggregation(tmp_path):
    path = tmp_path / "protocols.json"
    store = ProtocolConfigurationStore(path)
    assert "DTM" in store.sensors["GNSS"].protocols()
    store.update_sensor("GNSS", host="239.192.192.1", port=4097, rate_hz=2.0)
    assert all(
        store.sensors["GNSS"].get_protocol(protocol).host == "239.192.192.1"
        for protocol in store.sensors["GNSS"].protocols()
    )
    store.update_protocol("VTG", port=5000)
    assert store.sensors["GNSS"].aggregate("port") is None

    restored = ProtocolConfigurationStore(path)
    assert restored.sensors["GNSS"].get_protocol("GGA").port == 4097
    assert restored.sensors["GNSS"].get_protocol("VTG").port == 5000
    assert json.loads(path.read_text(encoding="utf-8"))["format_version"] == 3


def test_protocol_transports_are_independent_and_tcp_serial_default_off(tmp_path):
    path = tmp_path / "protocols.json"
    store = ProtocolConfigurationStore(path)
    ais = store.sensors["AIS"]
    assert ais.get_protocol("VDM", TransportKind.UDP).enabled is True
    assert ais.get_protocol("VDO", TransportKind.UDP).enabled is False
    assert ais.get_protocol("VDM", TransportKind.TCP).enabled is False
    assert ais.get_protocol("VDM", TransportKind.SERIAL).enabled is False

    store.update_protocol(
        "VDM",
        output_transport=TransportKind.TCP,
        enabled=True,
        host="0.0.0.0",
        port=14002,
        rate_hz=0.5,
        tcp_server=True,
    )
    restored = ProtocolConfigurationStore(path)
    assert restored.sensors["AIS"].get_protocol("VDM", TransportKind.UDP).rate_hz == 1.0
    tcp = restored.sensors["AIS"].get_protocol("VDM", TransportKind.TCP)
    assert (tcp.enabled, tcp.host, tcp.port, tcp.rate_hz, tcp.tcp_server) == (
        True, "0.0.0.0", 14002, 0.5, True
    )


def test_additional_output_paths_are_independent_persistent_and_default_off(tmp_path):
    path = tmp_path / "protocols.json"
    store = ProtocolConfigurationStore(path)
    extra = store.add_output_path(TransportKind.UDP)
    assert extra.label == "UDP 2"
    extra_sensors = store.sensors_for_path(extra.path_id)
    assert not any(
        settings.get_protocol(protocol, TransportKind.UDP).enabled
        for settings in extra_sensors.values()
        for protocol in settings.protocols()
    )

    store.update_sensor(
        "GNSS",
        output_transport=TransportKind.UDP,
        output_path_id=extra.path_id,
        enabled=True,
        host="239.192.192.10",
        port=14100,
        rate_hz=5.0,
    )
    assert store.sensors["GNSS"].get_protocol("GGA", TransportKind.UDP).port == 4096
    assert extra_sensors["GNSS"].get_protocol("GGA", TransportKind.UDP).port == 14100

    restored = ProtocolConfigurationStore(path)
    restored_path = restored.output_path(extra.path_id)
    restored_gnss = restored.sensors_for_path(restored_path.path_id)["GNSS"]
    assert restored_path == extra
    assert restored_gnss.get_protocol("GGA", TransportKind.UDP).host == "239.192.192.10"
    assert restored_gnss.get_protocol("VTG", TransportKind.UDP).rate_hz == 5.0

    restored.remove_output_path(extra.path_id)
    assert [item.path_id for item in restored.output_paths()] == ["udp", "tcp", "serial"]


def test_format_two_transport_configuration_migrates_to_primary_paths(tmp_path):
    path = tmp_path / "protocols.json"
    path.write_text(
        json.dumps({
            "format_version": 2,
            "protocols": {
                "GGA": {
                    "udp": {"enabled": True, "transport": "udp", "host": "192.0.2.20", "port": 5001},
                    "tcp": {"enabled": True, "transport": "tcp", "host": "192.0.2.21", "port": 5002},
                }
            },
        }),
        encoding="utf-8",
    )
    store = ProtocolConfigurationStore(path)
    assert store.sensors["GNSS"].get_protocol("GGA", TransportKind.UDP).host == "192.0.2.20"
    assert store.sensors["GNSS"].get_protocol("GGA", TransportKind.TCP).port == 5002
    assert [item.path_id for item in store.output_paths()] == ["udp", "tcp", "serial"]


def test_legacy_single_transport_configuration_is_migrated_without_duplication(tmp_path):
    path = tmp_path / "protocols.json"
    path.write_text(
        json.dumps({
            "format_version": 1,
            "protocols": {
                "GGA": {
                    "enabled": True,
                    "transport": "tcp",
                    "host": "192.0.2.10",
                    "port": 5000,
                    "rate_hz": 2.0,
                }
            },
        }),
        encoding="utf-8",
    )
    store = ProtocolConfigurationStore(path)
    gnss = store.sensors["GNSS"]
    assert gnss.get_protocol("GGA", TransportKind.UDP).enabled is False
    assert gnss.get_protocol("GGA", TransportKind.SERIAL).enabled is False
    tcp = gnss.get_protocol("GGA", TransportKind.TCP)
    assert (tcp.enabled, tcp.host, tcp.port, tcp.rate_hz) == (True, "192.0.2.10", 5000, 2.0)


def test_unknown_saved_protocol_is_ignored_for_forward_compatibility(tmp_path):
    path = tmp_path / "protocols.json"
    path.write_text('{"format_version": 1, "protocols": {"ZZZ": {}}}', encoding="utf-8")
    store = ProtocolConfigurationStore(path)
    assert store.sensors["GNSS"].get_protocol("GGA").port == 4096
