# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import heapq
import itertools
from threading import RLock
import time
from typing import Callable, TYPE_CHECKING

from maritime_simulator.domain.state import SimulationSnapshot
from maritime_simulator.application.metrics import PerformanceMonitor
from maritime_simulator.protocols.iec61162 import (
    encode_ais_position_report,
    encode_ais_static_voyage_report,
    encode_protocol_sentences,
)
from maritime_simulator.protocols.scheduler import ProtocolSchedule, SentenceScheduler
from maritime_simulator.protocols.sensors import SensorProjector

from .config import Endpoint, SerialEndpoint, TcpEndpoint, UdpEndpoint
from .serial import SerialSender
from .tcp import PersistentTcpSender, TcpServerSender
from .udp import UdpSocketPool

if TYPE_CHECKING:
    from maritime_simulator.application.fault_injection import FaultInjectionController


SIMULATED_OWN_SHIP_MMSI = 211_000_001


class OutputManager:
    def __init__(self):
        self._udp = UdpSocketPool()
        self._tcp: dict[TcpEndpoint, PersistentTcpSender | TcpServerSender] = {}
        self._serial: dict[SerialEndpoint, SerialSender] = {}
        self._lock = RLock()
        self._closed = False

    def send(self, endpoint: Endpoint, sentence: str) -> bool:
        payload = sentence.rstrip("\r\n").encode("ascii", errors="strict") + b"\r\n"
        with self._lock:
            if self._closed:
                raise RuntimeError("OutputManager is closed")
            if isinstance(endpoint, UdpEndpoint):
                self._udp.send(endpoint, payload)
                return True
            if isinstance(endpoint, TcpEndpoint):
                sender = self._tcp.get(endpoint)
                if sender is None:
                    sender = TcpServerSender(endpoint) if endpoint.server else PersistentTcpSender(endpoint)
                    self._tcp[endpoint] = sender
                return sender.send(payload)
            if isinstance(endpoint, SerialEndpoint):
                sender = self._serial.get(endpoint)
                if sender is None:
                    sender = SerialSender(endpoint)
                    self._serial[endpoint] = sender
                return sender.send(payload)
            raise TypeError(f"Unsupported endpoint: {type(endpoint).__name__}")

    def prepare(self, endpoint: Endpoint) -> None:
        """Start listeners that must exist before the first sentence is generated."""
        if not isinstance(endpoint, TcpEndpoint) or not endpoint.server:
            return
        with self._lock:
            if self._closed:
                raise RuntimeError("OutputManager is closed")
            if endpoint not in self._tcp:
                self._tcp[endpoint] = TcpServerSender(endpoint)

    def release(self, endpoint: Endpoint) -> None:
        sender = None
        with self._lock:
            if isinstance(endpoint, TcpEndpoint):
                sender = self._tcp.pop(endpoint, None)
            elif isinstance(endpoint, SerialEndpoint):
                sender = self._serial.pop(endpoint, None)
        if sender is not None:
            sender.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            tcp = tuple(self._tcp.values())
            serial = tuple(self._serial.values())
            self._tcp.clear()
            self._serial.clear()
        self._udp.close()
        for sender in tcp:
            sender.close()
        for sender in serial:
            sender.close()


@dataclass(frozen=True, slots=True)
class RegisteredOutput:
    schedule: ProtocolSchedule
    endpoint: Endpoint


@dataclass(frozen=True, slots=True)
class _PendingSentence:
    due_s: float
    sequence: int
    protocol: str
    endpoint: Endpoint
    sentence: str


class ProtocolOutputService:
    """Generate only due sentences and dispatch each protocol independently."""

    def __init__(
        self,
        output_manager: OutputManager | None = None,
        scheduler: SentenceScheduler | None = None,
        projector: SensorProjector | None = None,
        now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        ais_targets: Callable[[], tuple[dict, ...]] | None = None,
        on_sent: Callable[[str, Endpoint, str], None] | None = None,
        performance_monitor: PerformanceMonitor | None = None,
        pace_ais: bool = False,
        fault_injector: FaultInjectionController | None = None,
    ):
        self.output_manager = output_manager or OutputManager()
        self.scheduler = scheduler or SentenceScheduler()
        self.projector = projector or SensorProjector()
        self.now_factory = now_factory
        self.ais_targets = ais_targets or (lambda: ())
        self.on_sent = on_sent
        self.performance_monitor = performance_monitor or PerformanceMonitor()
        self.pace_ais = bool(pace_ais)
        self.fault_injector = fault_injector
        self._outputs: dict[str, RegisteredOutput] = {}
        self._ais_static_emitted: dict[tuple[str, int], tuple[tuple, float]] = {}
        self._ais_static_next_due: dict[str, float] = {}
        self._ais_sequence_id = 0
        self._pending_sentences: list[tuple[float, int, _PendingSentence]] = []
        self._pending_sequence = itertools.count()
        self._closed = False

    def register(self, registration: RegisteredOutput, *, now_s: float | None = None) -> None:
        self._outputs[registration.schedule.schedule_id] = registration
        self.scheduler.configure(registration.schedule, now_s=now_s)
        prepare = getattr(self.output_manager, "prepare", None)
        if callable(prepare):
            prepare(registration.endpoint)

    def unregister(self, schedule_id: str) -> None:
        self._outputs.pop(schedule_id, None)
        self.scheduler.remove(schedule_id)

    def refresh_ais_selection(self, *, now_s: float | None = None) -> None:
        """Discard obsolete paced VDM work and schedule the current selection now."""
        if self._closed:
            return
        timestamp = time.monotonic() if now_s is None else float(now_s)
        vdm_outputs = tuple(
            registration
            for registration in self._outputs.values()
            if registration.schedule.protocol.upper() == "VDM"
        )
        vdm_schedule_ids = {item.schedule.schedule_id for item in vdm_outputs}
        self._pending_sentences = [
            item
            for item in self._pending_sentences
            if item[2].protocol.upper() != "VDM"
        ]
        heapq.heapify(self._pending_sentences)
        self._ais_static_emitted = {
            key: value
            for key, value in self._ais_static_emitted.items()
            if key[0] not in vdm_schedule_ids
        }
        for schedule_id in vdm_schedule_ids:
            self._ais_static_next_due.pop(schedule_id, None)
        for registration in vdm_outputs:
            self.scheduler.configure(registration.schedule, now_s=timestamp, immediate=True)
        self.performance_monitor.set_gauge("pending_nmea_lines", len(self._pending_sentences))

    def pump(self, snapshot: SimulationSnapshot, *, now_s: float | None = None) -> int:
        if self._closed:
            raise RuntimeError("ProtocolOutputService is closed")
        monitor = self.performance_monitor
        sensor = self.projector.project(snapshot)
        emitted = 0
        timestamp = time.monotonic() if now_s is None else float(now_s)
        for schedule in self.scheduler.due(now_s):
            registration = self._outputs.get(schedule.schedule_id)
            if registration is None:
                continue
            with monitor.measure("protocol_generation"):
                if schedule.protocol.upper() == "VDM":
                    targets = self.ais_targets()
                    monitor.set_gauge("ais_output_targets", len(targets))
                    if self.pace_ais:
                        self._queue_ais_cycle(registration, targets, timestamp)
                        sentences = ()
                    else:
                        sentences = self._generate_ais_cycle(schedule, targets, timestamp)
                elif schedule.protocol.upper() == "VDO":
                    sentences = (
                        encode_ais_position_report(
                            _own_ship_ais_target(snapshot),
                            formatter="VDO",
                            utc_second=self.now_factory().second,
                        ),
                    )
                else:
                    sentences = encode_protocol_sentences(
                        schedule.protocol,
                        sensor,
                        self.now_factory(),
                    )
            for sentence in sentences:
                delay_s = self.fault_injector.transmission_delay_s() if self.fault_injector else 0.0
                if delay_s > 0.0:
                    self._queue_sentence(timestamp + delay_s, schedule.protocol, registration.endpoint, sentence)
                else:
                    emitted += self._dispatch(schedule.protocol, registration.endpoint, sentence)
        if self._pending_sentences:
            emitted += self._drain_pending(timestamp)
        return emitted

    def next_delay_s(self, *, now_s: float | None = None, default: float = 0.05) -> float:
        """Return the next scheduler/pacing deadline for an external pump worker."""
        timestamp = time.monotonic() if now_s is None else float(now_s)
        delay = self.scheduler.next_delay_s(timestamp, default=default)
        if self._pending_sentences:
            delay = min(delay, max(0.0, self._pending_sentences[0][0] - timestamp))
        return delay

    def _generate_ais_cycle(
        self,
        schedule: ProtocolSchedule,
        targets: tuple[dict, ...],
        timestamp: float,
    ) -> tuple[str, ...]:
        generated: list[str] = []
        utc_second = self.now_factory().second
        for target in targets:
            generated.append(
                encode_ais_position_report(target, utc_second=utc_second)
            )
            generated.extend(self._static_sentences_due(schedule, target, timestamp))
        return tuple(generated)

    def _queue_ais_cycle(
        self,
        registration: RegisteredOutput,
        targets: tuple[dict, ...],
        timestamp: float,
    ) -> None:
        schedule = registration.schedule
        target_count = len(targets)
        position_gap = schedule.interval_s / max(1, target_count)
        utc_second = self.now_factory().second
        for index, target in enumerate(targets):
            self._queue_sentence(
                timestamp + index * position_gap + (
                    self.fault_injector.transmission_delay_s() if self.fault_injector else 0.0
                ),
                schedule.protocol,
                registration.endpoint,
                encode_ais_position_report(target, utc_second=utc_second),
            )

        # Static/voyage reports are deliberately independent of the position train.
        # A multipart report is kept close together, while reports for different
        # targets are spread out to avoid the large first-cycle UDP burst.
        static_due = max(timestamp + 0.05, self._ais_static_next_due.get(schedule.schedule_id, timestamp))
        for target in targets:
            sentences = self._static_sentences_due(schedule, target, timestamp)
            if not sentences:
                continue
            for fragment_index, sentence in enumerate(sentences):
                self._queue_sentence(
                    static_due + fragment_index * 0.01 + (
                        self.fault_injector.transmission_delay_s() if self.fault_injector else 0.0
                    ),
                    schedule.protocol,
                    registration.endpoint,
                    sentence,
                )
            static_due += 0.10
        self._ais_static_next_due[schedule.schedule_id] = static_due

    def _static_sentences_due(
        self,
        schedule: ProtocolSchedule,
        target: dict,
        timestamp: float,
    ) -> tuple[str, ...]:
        mmsi = int(target["mmsi"])
        fingerprint = _ais_static_fingerprint(target)
        previous = self._ais_static_emitted.get((schedule.schedule_id, mmsi))
        if not fingerprint or not (
            previous is None or previous[0] != fingerprint or timestamp - previous[1] >= 360.0
        ):
            return ()
        sentences = encode_ais_static_voyage_report(target, sequence_id=self._ais_sequence_id)
        self._ais_sequence_id = (self._ais_sequence_id + 1) % 10
        self._ais_static_emitted[(schedule.schedule_id, mmsi)] = (fingerprint, timestamp)
        return sentences

    def _queue_sentence(self, due_s: float, protocol: str, endpoint: Endpoint, sentence: str) -> None:
        sequence = next(self._pending_sequence)
        pending = _PendingSentence(due_s, sequence, protocol, endpoint, sentence)
        heapq.heappush(self._pending_sentences, (due_s, sequence, pending))

    def _drain_pending(self, timestamp: float, *, maximum: int = 16) -> int:
        emitted = 0
        while self._pending_sentences and emitted < maximum:
            due_s, _, pending = self._pending_sentences[0]
            if due_s > timestamp + 1e-9:
                break
            heapq.heappop(self._pending_sentences)
            emitted += self._dispatch(pending.protocol, pending.endpoint, pending.sentence)
        self.performance_monitor.set_gauge("pending_nmea_lines", len(self._pending_sentences))
        return emitted

    def _dispatch(self, protocol: str, endpoint: Endpoint, sentence: str) -> int:
        if self.fault_injector is not None:
            sentence = self.fault_injector.transform_sentence(protocol, sentence)
            if sentence is None:
                self.performance_monitor.increment("fault_injection.dropped_sentences")
                return 0
        monitor = self.performance_monitor
        with monitor.measure("network_dispatch"):
            sent = self.output_manager.send(endpoint, sentence)
        if not sent:
            return 0
        monitor.increment("nmea_sentences")
        monitor.increment(f"nmea_sentences.{protocol.upper()}")
        monitor.increment(f"nmea_sentences.endpoint.{_endpoint_metric_id(endpoint)}")
        if self.on_sent is not None:
            self.on_sent(protocol, endpoint, sentence)
        return 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending_sentences.clear()
        self.output_manager.close()


def _endpoint_metric_id(endpoint: Endpoint) -> str:
    if isinstance(endpoint, TcpEndpoint):
        role = "server" if endpoint.server else "client"
        return f"tcp-{role}:{endpoint.host}:{endpoint.port}"
    if isinstance(endpoint, UdpEndpoint):
        return f"{type(endpoint).__name__.removesuffix('Endpoint').lower()}:{endpoint.host}:{endpoint.port}"
    return f"serial:{endpoint.device}:{endpoint.baud}"


def _own_ship_ais_target(snapshot: SimulationSnapshot) -> dict:
    """Project the simulated own ship into an AIS class-A position report."""
    return {
        "mmsi": SIMULATED_OWN_SHIP_MMSI,
        "lat": snapshot.latitude_deg,
        "lon": snapshot.longitude_deg,
        "speed": abs(snapshot.sog_kn),
        "course": snapshot.cog_deg,
        "heading": snapshot.heading_deg,
        "rot": snapshot.rate_of_turn_deg_min,
        "nav_status": "under way using engine" if abs(snapshot.sog_kn) > 0.1 else "moored",
    }


def _ais_static_fingerprint(target: dict) -> tuple:
    values = tuple(
        target.get(name)
        for name in (
            "name", "callsign", "ship_type", "length", "beam", "draught",
            "imo", "destination", "eta_month", "eta_day", "eta_hour", "eta_minute",
        )
    )
    return values if any(value not in (None, "", 0, 0.0) for value in values) else ()
