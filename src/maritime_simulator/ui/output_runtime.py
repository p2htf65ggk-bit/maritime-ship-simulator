# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from threading import Event, RLock, Thread
import time

from PySide6.QtCore import QObject, QTimer

from maritime_simulator.application.ais import AISRepository
from maritime_simulator.application.fault_injection import FaultInjectionController
from maritime_simulator.application.configuration import ProtocolConfigurationStore
from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.enums import TransportKind, UdpMode
from maritime_simulator.domain.state import SimulationSnapshot
from maritime_simulator.protocols.scheduler import ProtocolSchedule
from maritime_simulator.protocols.sensors import SensorProjector
from maritime_simulator.transport.config import Endpoint, SerialEndpoint, TcpEndpoint, UdpEndpoint
from maritime_simulator.transport.manager import OutputManager, ProtocolOutputService, RegisteredOutput

from .protocol_output_dialog import OutputEvent


OUTPUT_WORKER_MAX_WAIT_S = 0.05


@dataclass(frozen=True, slots=True)
class _WindowsWorkerTiming:
    winmm: object | None = None
    timer_period_active: bool = False
    priority_elevated: bool = False


def _configure_output_worker_timing(platform: str | None = None) -> _WindowsWorkerTiming:
    """Improve scheduler wake-up consistency without making Windows support mandatory."""
    if not (platform or sys.platform).startswith("win"):
        return _WindowsWorkerTiming()
    try:
        import ctypes

        winmm = ctypes.windll.winmm
        timer_period_active = winmm.timeBeginPeriod(1) == 0
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThread.restype = ctypes.c_void_p
        kernel32.SetThreadPriority.argtypes = (ctypes.c_void_p, ctypes.c_int)
        priority_elevated = bool(kernel32.SetThreadPriority(kernel32.GetCurrentThread(), 1))
        return _WindowsWorkerTiming(winmm, timer_period_active, priority_elevated)
    except (AttributeError, OSError, TypeError, ValueError):
        return _WindowsWorkerTiming()


def _restore_output_worker_timing(timing: _WindowsWorkerTiming) -> None:
    if timing.timer_period_active and timing.winmm is not None:
        try:
            timing.winmm.timeEndPeriod(1)
        except (AttributeError, OSError):
            pass


@dataclass(frozen=True, slots=True)
class _ConfiguredEndpoint:
    label: str
    endpoint: Endpoint


class OutputRuntime(QObject):
    """Qt lifecycle adapter around the UI-free output scheduler/transports."""

    def __init__(
        self,
        simulation: SimulatorService,
        ais: AISRepository,
        configuration: ProtocolConfigurationStore,
        advanced_ports_path: Path | None = None,
        parent=None,
        *,
        fault_injector: FaultInjectionController | None = None,
    ):
        super().__init__(parent)
        self.simulation = simulation
        self.ais = ais
        self.configuration = configuration
        self.fault_injector = fault_injector
        self._advanced_ports_path = advanced_ports_path
        self._advanced_endpoints: dict[str, Endpoint] = {}
        self._service: ProtocolOutputService | None = None
        self._manual_output = OutputManager()
        self._events: deque[OutputEvent] = deque(maxlen=5000)
        self._events_lock = RLock()
        self._service_lock = RLock()
        self._snapshot_lock = RLock()
        self._latest_snapshot = simulation.snapshot()
        self._worker_stop = Event()
        self._worker_wake = Event()
        self._sequence = 0
        self._last_error = ""
        self._last_pump_s: float | None = None
        self._maximum_pump_gap_s = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._wake_worker)
        self._load_advanced_ports()
        self._worker = Thread(target=self._pump_loop, name="protocol-output-scheduler", daemon=True)
        self._worker.start()

    def is_running(self) -> bool:
        with self._service_lock:
            return self._service is not None

    def start(self) -> None:
        self.stop()
        self._last_pump_s = None
        self._maximum_pump_gap_s = 0.0
        service = ProtocolOutputService(
            ais_targets=lambda: self.ais.output_targets(self.latest_snapshot()),
            on_sent=self._record,
            performance_monitor=self.simulation.performance_monitor,
            pace_ais=True,
            projector=SensorProjector(self.fault_injector),
            fault_injector=self.fault_injector,
        )
        for path, _sensor, protocol, settings in self.configuration.configured_outputs():
            if not settings.enabled:
                continue
            endpoint = _endpoint(settings)
            schedule_id = f"{path.path_id}:{protocol}:{_endpoint_label(endpoint)}"
            service.register(
                RegisteredOutput(
                    schedule=ProtocolSchedule(
                        schedule_id=schedule_id,
                        protocol=protocol,
                        rate_hz=settings.rate_hz,
                        output_id=_endpoint_label(endpoint),
                    ),
                    endpoint=endpoint,
                ),
                now_s=time.monotonic(),
            )
        for endpoint in self._advanced_endpoints.values():
            service.output_manager.prepare(endpoint)
        with self._service_lock:
            self._service = service
        self.timer.start()
        self._worker_wake.set()

    def stop(self) -> None:
        self.timer.stop()
        with self._service_lock:
            service, self._service = self._service, None
        self._worker_wake.set()
        if service is not None:
            service.close()

    def restart_if_running(self) -> None:
        if self.is_running():
            self.start()

    def refresh_ais_selection(self) -> None:
        """Apply AIS range/count changes without restarting active transports."""
        with self._service_lock:
            if self._service is not None:
                self._service.refresh_ais_selection(now_s=time.monotonic())
        self._worker_wake.set()

    def update_snapshot(self, snapshot: SimulationSnapshot) -> None:
        """Publish an immutable simulation snapshot without blocking output I/O."""
        with self._snapshot_lock:
            changed = snapshot.revision != self._latest_snapshot.revision
            self._latest_snapshot = snapshot
        if changed:
            self._worker_wake.set()

    def latest_snapshot(self) -> SimulationSnapshot:
        with self._snapshot_lock:
            return self._latest_snapshot

    def configured_ports(self) -> tuple[str, ...]:
        return tuple(item.label for item in self._configured_endpoints())

    def add_advanced_port(self, settings: dict) -> str:
        endpoint = _advanced_endpoint(settings)
        label = _endpoint_label(endpoint)
        self._advanced_endpoints[label] = endpoint
        self._save_advanced_ports()
        with self._service_lock:
            if self._service is not None:
                self._service.output_manager.prepare(endpoint)
        return label

    def remove_advanced_port(self, label: str) -> None:
        endpoint = self._advanced_endpoints.pop(str(label), None)
        self._save_advanced_ports()
        if endpoint is not None and not self._regular_endpoint_is_enabled(endpoint):
            with self._service_lock:
                if self._service is not None:
                    self._service.output_manager.release(endpoint)
            self._manual_output.release(endpoint)

    def advanced_ports_payload(self) -> list[dict]:
        return [_endpoint_payload(endpoint) for endpoint in self._advanced_endpoints.values()]

    def apply_advanced_ports_payload(self, values) -> None:
        endpoints: dict[str, Endpoint] = {}
        for value in values or ():
            endpoint = _advanced_endpoint(dict(value))
            endpoints[_endpoint_label(endpoint)] = endpoint
        self._advanced_endpoints = endpoints
        self._save_advanced_ports()

    def events(self) -> tuple[OutputEvent, ...]:
        with self._events_lock:
            return tuple(self._events)

    def clear_events(self) -> None:
        with self._events_lock:
            self._events.clear()

    def send_advanced(self, port_label: str, sentence: str) -> None:
        configured = {item.label: item.endpoint for item in self._configured_endpoints()}
        endpoint = configured.get(port_label)
        if endpoint is None:
            raise ValueError(f"Output port is no longer configured: {port_label}")
        with self._service_lock:
            manager = self._service.output_manager if self._service is not None else self._manual_output
            if manager.send(endpoint, sentence):
                protocol = sentence[3:6] if len(sentence) >= 6 else "ADV"
                self._record(protocol, endpoint, sentence)

    def serial_load_percent(self, port_label: str) -> float | None:
        configured = {item.label: item.endpoint for item in self._configured_endpoints()}
        endpoint = configured.get(port_label)
        if not isinstance(endpoint, SerialEndpoint):
            return None
        bits_per_second = 0.0
        for _path, _sensor, _protocol, settings in self.configuration.configured_outputs():
            if settings.enabled and _endpoint_label(_endpoint(settings)) == port_label:
                bits_per_second += settings.rate_hz * 82.0 * 10.0
        return bits_per_second / endpoint.baud * 100.0

    def measured_serial_loads(self, snapshot: dict) -> dict[str, float]:
        rates = snapshot.get("recent", {}).get("rates", {})
        return {
            item.label: float(rates.get(f"serial_bits.{_endpoint_label(item.endpoint)}", 0)) / item.endpoint.baud * 100
            for item in self._configured_endpoints()
            if isinstance(item.endpoint, SerialEndpoint)
        }

    def close(self) -> None:
        self.stop()
        self._worker_stop.set()
        self._worker_wake.set()
        self._worker.join(2.0)
        self._manual_output.close()

    def _configured_endpoints(self) -> tuple[_ConfiguredEndpoint, ...]:
        endpoints: dict[str, Endpoint] = dict(self._advanced_endpoints)
        for _path, _sensor, _protocol, settings in self.configuration.configured_outputs():
            if settings.enabled:
                endpoint = _endpoint(settings)
                endpoints.setdefault(_endpoint_label(endpoint), endpoint)
        return tuple(_ConfiguredEndpoint(label, endpoint) for label, endpoint in sorted(endpoints.items()))

    def _regular_endpoint_is_enabled(self, endpoint: Endpoint) -> bool:
        return any(
            settings.enabled and _endpoint(settings) == endpoint
            for _path, _sensor, _protocol, settings in self.configuration.configured_outputs()
        )

    def _load_advanced_ports(self) -> None:
        if self._advanced_ports_path is None or not self._advanced_ports_path.exists():
            return
        try:
            values = json.loads(self._advanced_ports_path.read_text(encoding="utf-8"))
            self.apply_advanced_ports_payload(values)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._advanced_endpoints = {}

    def _save_advanced_ports(self) -> None:
        if self._advanced_ports_path is None:
            return
        self._advanced_ports_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._advanced_ports_path.with_suffix(self._advanced_ports_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.advanced_ports_payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._advanced_ports_path)

    def _wake_worker(self) -> None:
        self._worker_wake.set()

    def _pump_loop(self) -> None:
        timing = _configure_output_worker_timing()
        monitor = self.simulation.performance_monitor
        monitor.set_gauge("output_worker_priority_elevated", timing.priority_elevated)
        monitor.set_gauge("output_worker_timer_period_ms", 1 if timing.timer_period_active else None)
        try:
            while not self._worker_stop.is_set():
                delay_s = OUTPUT_WORKER_MAX_WAIT_S
                with self._service_lock:
                    service = self._service
                    if service is not None:
                        try:
                            now = time.monotonic()
                            if self._last_pump_s is not None:
                                gap_s = max(0.0, now - self._last_pump_s)
                                self._maximum_pump_gap_s = max(self._maximum_pump_gap_s, gap_s)
                                monitor.set_gauge("output_scheduler_gap_ms", gap_s * 1000.0)
                                monitor.set_gauge(
                                    "output_scheduler_max_gap_ms",
                                    self._maximum_pump_gap_s * 1000.0,
                                )
                            self._last_pump_s = now
                            with monitor.measure("output_pump"):
                                service.pump(self.latest_snapshot(), now_s=now)
                            self._last_error = ""
                            delay_s = service.next_delay_s(now_s=time.monotonic())
                        except (OSError, RuntimeError, ValueError) as error:
                            self._last_error = str(error)
                            monitor.increment("network_send_errors")
                            delay_s = OUTPUT_WORKER_MAX_WAIT_S
                self._worker_wake.wait(max(0.001, min(OUTPUT_WORKER_MAX_WAIT_S, delay_s)))
                self._worker_wake.clear()
        finally:
            _restore_output_worker_timing(timing)

    def _record(self, protocol: str, endpoint: Endpoint, sentence: str) -> None:
        if isinstance(endpoint, SerialEndpoint):
            bits_per_character = 1 + 8 + (endpoint.parity != "N") + endpoint.stop_bits
            self.simulation.performance_monitor.increment(
                f"serial_bits.{_endpoint_label(endpoint)}",
                (len(sentence.rstrip("\r\n")) + 2) * bits_per_character,
            )
        with self._events_lock:
            self._sequence += 1
            self._events.append(
                OutputEvent(
                    sequence=self._sequence,
                    monotonic_s=time.monotonic(),
                    port=_endpoint_label(endpoint),
                    protocol=protocol,
                    sentence=sentence,
                )
            )


def _endpoint(settings) -> Endpoint:
    if settings.transport is TransportKind.UDP:
        return UdpEndpoint(
            host=settings.host,
            port=settings.port,
            mode=settings.udp_mode,
            multicast_ttl=settings.multicast_ttl,
            interface_ip=settings.interface_ip,
        )
    if settings.transport is TransportKind.TCP:
        return TcpEndpoint(host=settings.host, port=settings.port, server=settings.tcp_server)
    return SerialEndpoint(
        device=settings.serial_device,
        baud=settings.baud,
        parity=settings.parity,
        stop_bits=settings.stop_bits,
    )


def _endpoint_label(endpoint: Endpoint) -> str:
    if isinstance(endpoint, UdpEndpoint):
        return f"UDP  {endpoint.host}:{endpoint.port}"
    if isinstance(endpoint, TcpEndpoint):
        if endpoint.server:
            return f"TCP Server  {endpoint.host}:{endpoint.port}"
        return f"TCP  {endpoint.host}:{endpoint.port}"
    return f"Serial  {endpoint.device}  {endpoint.baud}{endpoint.parity}{endpoint.stop_bits}"


def _advanced_endpoint(settings: dict) -> Endpoint:
    transport = str(settings.get("transport", "UDP")).upper()
    if transport == "UDP":
        return UdpEndpoint(
            host=str(settings.get("host", "127.0.0.1")).strip(),
            port=int(settings.get("port", 4096)),
            mode=UdpMode(str(settings.get("mode", "auto")).lower()),
            multicast_ttl=int(settings.get("multicast_ttl", 1)),
            interface_ip=str(settings.get("interface_ip", "")).strip(),
        )
    if transport == "TCP":
        return TcpEndpoint(
            host=str(settings.get("host", "127.0.0.1")).strip(),
            port=int(settings.get("port", 4096)),
            server=bool(settings.get("server", False)),
        )
    if transport == "SERIAL":
        return SerialEndpoint(
            device=str(settings.get("device", "")).strip(),
            baud=int(settings.get("baud", 4800)),
            parity=str(settings.get("parity", "N")).upper(),
            stop_bits=int(settings.get("stop_bits", 1)),
        )
    raise ValueError("Advanced output transport must be UDP, TCP or Serial")


def _endpoint_payload(endpoint: Endpoint) -> dict:
    if isinstance(endpoint, UdpEndpoint):
        return {
            "transport": "UDP", "host": endpoint.host, "port": endpoint.port,
            "mode": endpoint.mode.value, "multicast_ttl": endpoint.multicast_ttl,
            "interface_ip": endpoint.interface_ip,
        }
    if isinstance(endpoint, TcpEndpoint):
        return {
            "transport": "TCP", "host": endpoint.host, "port": endpoint.port,
            "server": endpoint.server,
        }
    return {
        "transport": "Serial", "device": endpoint.device, "baud": endpoint.baud,
        "parity": endpoint.parity, "stop_bits": endpoint.stop_bits,
    }
