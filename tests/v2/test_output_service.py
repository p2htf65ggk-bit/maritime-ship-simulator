# SPDX-License-Identifier: MPL-2.0
from datetime import datetime, timezone

from maritime_simulator.application.ais import AISRepository, AISTarget
from maritime_simulator.application.metrics import PerformanceMonitor
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.protocols.iec61162 import IEC61162Codec
from maritime_simulator.protocols.scheduler import ProtocolSchedule
from maritime_simulator.transport.config import UdpEndpoint
from maritime_simulator.transport.manager import ProtocolOutputService, RegisteredOutput


class RecordingOutputManager:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, endpoint, sentence):
        self.sent.append((endpoint, sentence))
        return True

    def close(self):
        self.closed = True


def test_output_service_schedules_protocols_independently_and_reports_actual_sends():
    manager = RecordingOutputManager()
    monitor = PerformanceMonitor(enabled=True)
    sent_events = []
    service = ProtocolOutputService(
        output_manager=manager,
        performance_monitor=monitor,
        now_factory=lambda: datetime(2026, 1, 2, tzinfo=timezone.utc),
        on_sent=lambda protocol, endpoint, sentence: sent_events.append((protocol, endpoint, sentence)),
    )
    endpoint = UdpEndpoint("127.0.0.1", 4096)
    service.register(RegisteredOutput(ProtocolSchedule("gga", "GGA", 1.0, "udp"), endpoint), now_s=0.0)
    service.register(RegisteredOutput(ProtocolSchedule("rot", "ROT", 5.0, "udp"), endpoint), now_s=0.0)
    snapshot = SimulatorService().snapshot()

    assert service.pump(snapshot, now_s=0.0) == 2
    assert service.pump(snapshot, now_s=0.2) == 1
    assert service.pump(snapshot, now_s=0.4) == 1
    assert [event[0] for event in sent_events] == ["GGA", "ROT", "ROT", "ROT"]
    assert monitor.snapshot()["counters"]["nmea_sentences"] == 4
    service.close()
    assert manager.closed


def test_twenty_hertz_output_schedule_is_exact_independently_of_os_wakeup_jitter():
    manager = RecordingOutputManager()
    service = ProtocolOutputService(output_manager=manager)
    simulation = SimulatorService()
    try:
        service.register(
            RegisteredOutput(
                ProtocolSchedule("heading", "HDT", 20.0, "udp"),
                UdpEndpoint("127.0.0.1", 4096),
            ),
            now_s=0.0,
        )
        snapshot = simulation.snapshot()
        assert service.pump(snapshot, now_s=0.0) == 1
        assert abs(service.next_delay_s(now_s=0.025) - 0.025) < 1e-9
        assert service.pump(snapshot, now_s=0.049) == 0
        assert service.pump(snapshot, now_s=0.05) == 1
        assert service.pump(snapshot, now_s=0.099) == 0
        assert service.pump(snapshot, now_s=0.10) == 1
        assert len(manager.sent) == 3
    finally:
        service.close()
        simulation.close()


def test_output_service_emits_relative_and_theoretical_mwv_at_one_configured_rate():
    manager = RecordingOutputManager()
    service = ProtocolOutputService(
        output_manager=manager,
        now_factory=lambda: datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    endpoint = UdpEndpoint("127.0.0.1", 4096)
    service.register(
        RegisteredOutput(ProtocolSchedule("wind", "MWV", 2.0, "udp"), endpoint),
        now_s=0.0,
    )
    snapshot = SimulatorService().snapshot()

    assert service.pump(snapshot, now_s=0.0) == 2
    assert service.pump(snapshot, now_s=0.49) == 0
    assert service.pump(snapshot, now_s=0.50) == 2
    references = [IEC61162Codec.decode(sentence).fields[1] for _, sentence in manager.sent]
    assert references == ["R", "T", "R", "T"]
    service.close()


def test_xdr_environment_outputs_have_independent_schedules():
    manager = RecordingOutputManager()
    service = ProtocolOutputService(output_manager=manager)
    endpoint = UdpEndpoint("127.0.0.1", 4096)
    service.register(
        RegisteredOutput(
            ProtocolSchedule("air", "XDR_AIR_TEMPERATURE", 1.0, "udp"), endpoint
        ),
        now_s=0.0,
    )
    service.register(
        RegisteredOutput(
            ProtocolSchedule("humidity", "XDR_RELATIVE_HUMIDITY", 2.0, "udp"), endpoint
        ),
        now_s=0.0,
    )
    snapshot = SimulatorService().snapshot()

    assert service.pump(snapshot, now_s=0.0) == 2
    assert service.pump(snapshot, now_s=0.5) == 1
    decoded = [IEC61162Codec.decode(sentence).fields for _, sentence in manager.sent]
    assert decoded[0][-1] == "Air#0"
    assert decoded[1][-1] == "Air#1"
    assert len(decoded) == 3
    service.close()


def test_ais_schedule_emits_position_each_cycle_and_static_data_when_changed():
    manager = RecordingOutputManager()
    visible = (
        {
            "mmsi": 211000001, "lat": 54.0, "lon": 10.0, "speed": 5.0, "course": 90.0,
            "name": "VISIBLE ONE", "callsign": "DTEST", "ship_type": "Cargo",
        },
        {"mmsi": 211000002, "lat": 54.1, "lon": 10.1, "speed": 6.0, "course": 180.0},
    )
    service = ProtocolOutputService(output_manager=manager, ais_targets=lambda: visible)
    endpoint = UdpEndpoint("127.0.0.1", 4096)
    service.register(RegisteredOutput(ProtocolSchedule("ais", "VDM", 1.0, "udp"), endpoint), now_s=0.0)
    snapshot = SimulatorService().snapshot()

    assert service.pump(snapshot, now_s=0.0) == 4
    assert service.pump(snapshot, now_s=0.5) == 0
    assert service.pump(snapshot, now_s=1.0) == 2
    assert all(sentence.startswith("!AIVDM") for _, sentence in manager.sent)
    service.close()


def test_ais_rebroadcast_refreshes_timestamp_without_a_new_provider_report():
    manager = RecordingOutputManager()
    clock = [datetime(2026, 1, 2, 12, 0, 5, tzinfo=timezone.utc)]
    target = ({
        "mmsi": 211000001,
        "lat": 54.0,
        "lon": 10.0,
        "speed": 5.0,
        "course": 90.0,
        "heading": 90.0,
    },)
    service = ProtocolOutputService(
        output_manager=manager,
        ais_targets=lambda: target,
        now_factory=lambda: clock[0],
    )
    endpoint = UdpEndpoint("127.0.0.1", 4096)
    service.register(
        RegisteredOutput(ProtocolSchedule("ais", "VDM", 1.0, "udp"), endpoint),
        now_s=0.0,
    )
    snapshot = SimulatorService().snapshot()

    assert service.pump(snapshot, now_s=0.0) == 1
    first = manager.sent[-1][1]
    clock[0] = datetime(2026, 1, 2, 12, 0, 6, tzinfo=timezone.utc)
    assert service.pump(snapshot, now_s=1.0) == 1
    second = manager.sent[-1][1]

    assert first != second
    assert _ais_utc_second(first) == 5
    assert _ais_utc_second(second) == 6
    service.close()


def _ais_utc_second(sentence: str) -> int:
    payload = IEC61162Codec.decode(sentence).fields[4]
    bits = "".join(
        format((ord(char) - 48) - (8 if ord(char) - 48 > 40 else 0), "06b")
        for char in payload
    )
    return int(bits[137:143], 2)


def test_paced_ais_output_spreads_positions_and_static_fragments_over_time():
    manager = RecordingOutputManager()
    visible = (
        {
            "mmsi": 211000001, "lat": 54.0, "lon": 10.0, "speed": 5.0, "course": 90.0,
            "name": "VISIBLE ONE", "callsign": "DTEST", "destination": "KIEL",
        },
        {
            "mmsi": 211000002, "lat": 54.1, "lon": 10.1, "speed": 6.0, "course": 180.0,
            "name": "VISIBLE TWO", "callsign": "DTEST2", "destination": "OSLO",
        },
    )
    service = ProtocolOutputService(output_manager=manager, ais_targets=lambda: visible, pace_ais=True)
    endpoint = UdpEndpoint("127.0.0.1", 4096)
    service.register(RegisteredOutput(ProtocolSchedule("ais", "VDM", 1.0, "udp"), endpoint), now_s=0.0)
    snapshot = SimulatorService().snapshot()

    assert service.pump(snapshot, now_s=0.0) == 1
    assert service.pump(snapshot, now_s=0.04) == 0
    assert service.pump(snapshot, now_s=0.05) == 1
    assert service.pump(snapshot, now_s=0.06) == 1
    assert service.pump(snapshot, now_s=0.14) == 0
    assert service.pump(snapshot, now_s=0.15) == 1
    assert service.pump(snapshot, now_s=0.16) == 1
    assert service.pump(snapshot, now_s=0.50) == 1
    assert len(manager.sent) == 6
    service.close()


def test_paced_ais_limit_change_discards_obsolete_queued_targets_immediately():
    manager = RecordingOutputManager()
    target_one = {
        "mmsi": 211000001, "lat": 54.0, "lon": 10.0, "speed": 5.0, "course": 90.0,
    }
    targets = [
        target_one,
        {"mmsi": 211000002, "lat": 54.1, "lon": 10.1, "speed": 6.0, "course": 180.0},
        {"mmsi": 211000003, "lat": 54.2, "lon": 10.2, "speed": 7.0, "course": 270.0},
    ]
    service = ProtocolOutputService(
        output_manager=manager,
        ais_targets=lambda: tuple(targets),
        pace_ais=True,
    )
    endpoint = UdpEndpoint("127.0.0.1", 4096)
    service.register(
        RegisteredOutput(ProtocolSchedule("ais", "VDM", 1.0, "udp"), endpoint),
        now_s=0.0,
    )
    snapshot = SimulatorService().snapshot()

    assert service.pump(snapshot, now_s=0.0) == 1
    first_sentence = manager.sent[0][1]
    targets[:] = [target_one]
    service.refresh_ais_selection(now_s=0.10)
    assert service.pump(snapshot, now_s=0.10) == 1
    assert manager.sent[-1][1] == first_sentence
    assert service.pump(snapshot, now_s=0.50) == 0
    assert len(manager.sent) == 2
    service.close()


def test_live_ais_visible_output_limit_bounds_actual_vdm_sentences():
    simulator = SimulatorService()
    own_ship = simulator.snapshot()
    repository = AISRepository(
        generated_target_count=2,
        active_range_nm=100.0,
        map_range_nm=100.0,
        map_target_limit=20,
    )
    repository.ingest_live(
        tuple(
            AISTarget(
                211000000 + index,
                own_ship.latitude_deg + index * 0.01,
                own_ship.longitude_deg,
                0.0,
                0.0,
                0.0,
            )
            for index in range(1, 6)
        ),
        replace_all=True,
    )
    manager = RecordingOutputManager()
    service = ProtocolOutputService(
        output_manager=manager,
        ais_targets=lambda: repository.output_targets(simulator.snapshot()),
    )
    endpoint = UdpEndpoint("127.0.0.1", 4096)
    service.register(
        RegisteredOutput(ProtocolSchedule("ais", "VDM", 1.0, "udp"), endpoint),
        now_s=0.0,
    )
    try:
        assert service.pump(own_ship, now_s=0.0) == 2
        repository.configure(target_count=1)
        service.refresh_ais_selection(now_s=0.1)
        assert service.pump(own_ship, now_s=0.1) == 1
        assert len(manager.sent) == 3
    finally:
        service.close()
        simulator.close()


def test_vdo_schedule_emits_own_ship_position_without_traffic_targets():
    manager = RecordingOutputManager()
    simulator = SimulatorService()
    service = ProtocolOutputService(output_manager=manager, ais_targets=lambda: ())
    endpoint = UdpEndpoint("127.0.0.1", 4096)
    service.register(RegisteredOutput(ProtocolSchedule("own-ais", "VDO", 2.0, "udp"), endpoint), now_s=0.0)
    try:
        assert service.pump(simulator.snapshot(), now_s=0.0) == 1
        sentence = manager.sent[0][1]
        decoded = IEC61162Codec.decode(sentence)
        assert decoded.talker == "AI"
        assert decoded.formatter == "VDO"
        assert service.pump(simulator.snapshot(), now_s=0.49) == 0
        assert service.pump(simulator.snapshot(), now_s=0.50) == 1
    finally:
        service.close()
        simulator.close()
