# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import socket
from threading import Event, RLock, Thread, current_thread
import time
from typing import Callable

from PySide6.QtCore import QIODevice, QThread, QTimer
from PySide6.QtSerialPort import QSerialPort

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import (
    ExternalRudderOrder,
    ExternalRudderResponse,
    SetExternalRudderInputType,
)
from maritime_simulator.domain.enums import ExternalRudderControlPolicy
from maritime_simulator.protocols.iec61162 import IEC61162Codec, IEC61162Error

from .config import InputEndpoint, SerialEndpoint, TcpInputEndpoint, UdpInputEndpoint


SentenceCallback = Callable[[str, str], None]
ErrorCallback = Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class InputSourceStatus:
    source_id: str
    running: bool
    connected: bool
    sentences_received: int
    last_error: str
    last_sentence_age_s: float | None


@dataclass(frozen=True, slots=True)
class RudderInputDetection:
    input_type: str
    dual_rudder: bool
    accepted_for_control: bool
    available_sides: tuple[str, ...] = ()


@dataclass(slots=True)
class _MutableStatus:
    running: bool = False
    connected: bool = False
    sentences_received: int = 0
    last_error: str = ""
    last_sentence_monotonic_s: float | None = None


class _SerialInputThread(QThread):
    """Run one QSerialPort on a Qt event loop without blocking the Python GIL."""

    _MAX_PENDING_BYTES = 8192

    def __init__(
        self,
        manager: "IECInputManager",
        source_id: str,
        endpoint: SerialEndpoint,
        status: _MutableStatus,
    ):
        super().__init__()
        self._manager = manager
        self._source_id = source_id
        self._endpoint = endpoint
        self._status = status
        self._stop_requested = Event()
        self.setObjectName(f"iec-serial-{source_id}")

    def request_stop(self) -> None:
        self._stop_requested.set()

    def run(self) -> None:
        handle = QSerialPort()
        handle.setPortName(self._endpoint.device)
        handle.setBaudRate(self._endpoint.baud)
        handle.setDataBits(QSerialPort.Data8)
        handle.setParity(
            {
                "E": QSerialPort.EvenParity,
                "O": QSerialPort.OddParity,
            }.get(self._endpoint.parity, QSerialPort.NoParity)
        )
        handle.setStopBits(
            QSerialPort.TwoStop
            if self._endpoint.stop_bits == 2
            else QSerialPort.OneStop
        )
        handle.setFlowControl(QSerialPort.NoFlowControl)
        pending = bytearray()

        def stop_if_requested() -> None:
            if not self._stop_requested.is_set() and not self._manager._stop.is_set():
                return
            if handle.isOpen():
                handle.close()
            self.quit()

        def attempt_open() -> None:
            if self._stop_requested.is_set() or self._manager._stop.is_set():
                stop_if_requested()
                return
            if handle.isOpen():
                return
            if handle.open(QIODevice.ReadOnly):
                self._status.connected = True
                self._status.last_error = ""
                return
            self._status.connected = False
            self._manager._record_error(
                self._source_id,
                self._status,
                handle.errorString() or "Unable to open serial input",
            )

        def port_error(error) -> None:
            if error in {QSerialPort.NoError, QSerialPort.TimeoutError}:
                return
            if self._stop_requested.is_set() or self._manager._stop.is_set():
                return
            self._status.connected = False
            self._manager._record_error(
                self._source_id,
                self._status,
                handle.errorString() or f"Serial input error {getattr(error, 'name', error)}",
            )
            if handle.isOpen():
                handle.close()

        def read_available() -> None:
            chunk = bytes(handle.readAll())
            if not chunk:
                return
            pending.extend(chunk)
            if len(pending) > self._MAX_PENDING_BYTES and b"\r" not in pending and b"\n" not in pending:
                pending.clear()
                self._manager._record_error(
                    self._source_id,
                    self._status,
                    "Serial input exceeded the bounded line buffer; malformed data was discarded",
                )
                return
            normalized = bytes(pending).replace(b"\r", b"\n")
            lines = normalized.split(b"\n")
            pending.clear()
            pending.extend(lines.pop())
            for line in lines:
                if line.strip():
                    self._manager._emit_payload(self._source_id, line, self._status)

        handle.readyRead.connect(read_available)
        handle.errorOccurred.connect(port_error)
        reconnect_timer = QTimer()
        reconnect_timer.setInterval(500)
        reconnect_timer.timeout.connect(attempt_open)
        stop_timer = QTimer()
        stop_timer.setInterval(50)
        stop_timer.timeout.connect(stop_if_requested)
        self._status.running = True
        reconnect_timer.start()
        stop_timer.start()
        attempt_open()
        try:
            if not self._stop_requested.is_set() and not self._manager._stop.is_set():
                self.exec()
        finally:
            reconnect_timer.stop()
            stop_timer.stop()
            if handle.isOpen():
                handle.close()
            self._status.connected = False
            self._status.running = False


class IECInputManager:
    """Own all IEC input threads and provide deterministic start/stop lifecycle."""

    _MAX_PENDING_BYTES = 8192

    def __init__(
        self,
        on_sentence: SentenceCallback,
        on_error: ErrorCallback | None = None,
        *,
        monotonic=time.monotonic,
    ):
        self._on_sentence = on_sentence
        self._on_error = on_error or (lambda _source, _error: None)
        self._sources: dict[str, InputEndpoint] = {}
        self._status: dict[str, _MutableStatus] = {}
        self._threads: dict[str, Thread] = {}
        self._serial_threads: dict[str, _SerialInputThread] = {}
        self._sockets: set[socket.socket] = set()
        self._stop = Event()
        self._lock = RLock()
        self._started = False
        self._monotonic = monotonic

    def add_source(self, source_id: str, endpoint: InputEndpoint) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("Cannot add an input source after start")
            if source_id in self._sources:
                raise ValueError(f"Input source already exists: {source_id}")
            self._sources[source_id] = endpoint
            self._status[source_id] = _MutableStatus()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if any(thread.is_alive() for thread in self._threads.values()) or any(
                thread.isRunning() for thread in self._serial_threads.values()
            ):
                raise RuntimeError("Previous input workers are still stopping; retry shortly")
            self._threads.clear()
            self._serial_threads.clear()
            self._started = True
            self._stop.clear()
            for source_id, endpoint in self._sources.items():
                if isinstance(endpoint, SerialEndpoint):
                    thread = _SerialInputThread(
                        self,
                        source_id,
                        endpoint,
                        self._status[source_id],
                    )
                    self._serial_threads[source_id] = thread
                    thread.start()
                    continue
                thread = Thread(
                    target=self._run_source,
                    args=(source_id, endpoint),
                    name=f"iec-input-{source_id}",
                    daemon=True,
                )
                self._threads[source_id] = thread
                thread.start()

    def close(self, timeout_s: float = 3.0) -> None:
        with self._lock:
            if not self._started:
                return
            self._stop.set()
            handles = tuple(self._sockets)
            threads = tuple(self._threads.values())
            serial_threads = tuple(self._serial_threads.values())
            for thread in serial_threads:
                thread.request_stop()
        # Interrupt idle recv/accept calls before waiting for their owners.
        for handle in handles:
            try:
                handle.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            handle.close()
        deadline = time.monotonic() + timeout_s
        for thread in threads:
            if thread is not current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))
        for thread in serial_threads:
            remaining_ms = max(0, round((deadline - time.monotonic()) * 1000.0))
            if not thread.wait(remaining_ms):
                thread.quit()
                thread.wait(1000)
        with self._lock:
            self._threads = {key: thread for key, thread in self._threads.items() if thread.is_alive()}
            self._serial_threads = {key: thread for key, thread in self._serial_threads.items() if thread.isRunning()}
            self._started = False

    @contextmanager
    def _network_socket(self, handle):
        try:
            with self._lock:
                if self._stop.is_set():
                    raise OSError("Input is stopping")
                self._sockets.add(handle)
            yield handle
        finally:
            handle.close()
            with self._lock:
                self._sockets.discard(handle)

    def statuses(self) -> tuple[InputSourceStatus, ...]:
        with self._lock:
            now = self._monotonic()
            return tuple(
                InputSourceStatus(
                    source_id=source_id,
                    running=value.running,
                    connected=value.connected,
                    sentences_received=value.sentences_received,
                    last_error=value.last_error,
                    last_sentence_age_s=(
                        None
                        if value.last_sentence_monotonic_s is None
                        else max(0.0, now - value.last_sentence_monotonic_s)
                    ),
                )
                for source_id, value in self._status.items()
            )

    def _run_source(self, source_id: str, endpoint: InputEndpoint) -> None:
        status = self._status[source_id]
        status.running = True
        try:
            if isinstance(endpoint, UdpInputEndpoint):
                self._run_udp(source_id, endpoint, status)
            elif isinstance(endpoint, TcpInputEndpoint):
                self._run_tcp(source_id, endpoint, status)
            else:
                raise TypeError(f"Unsupported input endpoint: {type(endpoint).__name__}")
        except Exception as error:
            if not self._stop.is_set():
                self._record_error(source_id, status, error)
        finally:
            status.connected = False
            status.running = False

    def _run_udp(self, source_id: str, endpoint: UdpInputEndpoint, status: _MutableStatus) -> None:
        handle = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        handle.settimeout(0.25)
        with self._network_socket(handle):
            handle.bind((endpoint.host, endpoint.port))
            if endpoint.multicast_group:
                membership = socket.inet_aton(endpoint.multicast_group) + socket.inet_aton(endpoint.interface_ip)
                handle.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            status.connected = True
            try:
                while not self._stop.is_set():
                    try:
                        payload, _ = handle.recvfrom(65_535)
                    except socket.timeout:
                        continue
                    self._emit_payload(source_id, payload, status)
            finally:
                if endpoint.multicast_group:
                    try:
                        membership = socket.inet_aton(endpoint.multicast_group) + socket.inet_aton(endpoint.interface_ip)
                        handle.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, membership)
                    except OSError:
                        pass

    def _run_tcp(self, source_id: str, endpoint: TcpInputEndpoint, status: _MutableStatus) -> None:
        if endpoint.server:
            self._run_tcp_server(source_id, endpoint, status)
        else:
            self._run_tcp_client(source_id, endpoint, status)

    def _run_tcp_server(self, source_id: str, endpoint: TcpInputEndpoint, status: _MutableStatus) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with self._network_socket(listener):
            listener.bind((endpoint.host, endpoint.port))
            listener.listen(2)
            listener.settimeout(0.25)
            while not self._stop.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                with self._network_socket(connection):
                    connection.settimeout(0.25)
                    status.connected = True
                    status.last_error = ""
                    try:
                        self._receive_stream(source_id, connection, status)
                    except OSError as error:
                        if not self._stop.is_set():
                            self._record_error(source_id, status, error)
                    finally:
                        status.connected = False

    def _run_tcp_client(self, source_id: str, endpoint: TcpInputEndpoint, status: _MutableStatus) -> None:
        delay = 0.1
        while not self._stop.is_set():
            try:
                with self._network_socket(socket.create_connection(
                    (endpoint.host, endpoint.port),
                    timeout=endpoint.connect_timeout_s,
                )) as connection:
                    connection.settimeout(0.25)
                    status.connected = True
                    status.last_error = ""
                    delay = 0.1
                    self._receive_stream(source_id, connection, status)
            except OSError as error:
                if not self._stop.is_set():
                    self._record_error(source_id, status, error)
            finally:
                status.connected = False
            self._stop.wait(delay)
            delay = min(2.0, delay * 2.0)

    def _receive_stream(self, source_id: str, connection: socket.socket, status: _MutableStatus) -> None:
        pending = bytearray()
        discarding = False
        while not self._stop.is_set():
            try:
                chunk = connection.recv(4096)
            except socket.timeout:
                continue
            if not chunk or self._stop.is_set():
                return
            parts = chunk.replace(b"\r", b"\n").split(b"\n")
            for index, part in enumerate(parts):
                if not discarding:
                    if len(pending) + len(part) > self._MAX_PENDING_BYTES:
                        pending.clear()
                        discarding = True
                        self._record_error(source_id, status,
                            "TCP input exceeded the bounded line buffer; malformed data was discarded")
                    else:
                        pending.extend(part)
                if index < len(parts) - 1:
                    if pending and not discarding:
                        self._emit_payload(source_id, bytes(pending), status)
                    pending.clear()
                    discarding = False

    def _emit_payload(self, source_id: str, payload: bytes, status: _MutableStatus) -> None:
        for raw in payload.decode("ascii", errors="replace").replace("\r", "\n").split("\n"):
            sentence = raw.strip()
            if not sentence:
                continue
            with self._lock:
                if self._stop.is_set():
                    return
                status.sentences_received += 1
                status.last_sentence_monotonic_s = self._monotonic()
                self._on_sentence(source_id, sentence)

    def _record_error(self, source_id: str, status: _MutableStatus, error: object) -> None:
        message = str(error)
        with self._lock:
            if self._stop.is_set() or status.last_error == message:
                return
            status.last_error = message
            self._on_error(source_id, status.last_error)


class RORInputBridge:
    """Validate the configured ROR/RSA input and submit timestamped commands."""

    def __init__(
        self,
        simulator: SimulatorService,
        *,
        monotonic=time.monotonic,
        on_error: ErrorCallback | None = None,
        input_type: str = "ROR",
    ):
        self.simulator = simulator
        self._monotonic = monotonic
        self._on_error = on_error or (lambda _source, _error: None)
        self.input_policy = ExternalRudderControlPolicy.parse(input_type)
        self.simulator.submit(SetExternalRudderInputType(self.input_policy.value))

    def on_sentence(self, source_id: str, raw: str) -> RudderInputDetection | None:
        try:
            decoded = IEC61162Codec.decode(raw)
            if decoded.formatter not in {"ROR", "RSA"}:
                return None
            if decoded.formatter == "ROR":
                order = IEC61162Codec.decode_ror(raw)
                starboard_valid = order.starboard_valid and order.starboard_or_single_deg is not None
                port_valid = order.port_valid is True and order.port_deg is not None
                if not starboard_valid and not port_valid:
                    raise IEC61162Error("ROR reports an invalid rudder order status")
                primary = order.starboard_or_single_deg if starboard_valid else order.port_deg
                assert primary is not None
                command = ExternalRudderOrder(
                    starboard_or_single_deg=primary,
                    port_deg=order.port_deg if starboard_valid and port_valid else None,
                    received_monotonic_s=self._monotonic(),
                    source=f"{source_id}:{order.talker}ROR",
                )
                available_sides = tuple(
                    side for side, available in (("STBD", starboard_valid), ("PORT", port_valid)) if available
                )
            else:
                response = IEC61162Codec.decode_rsa(raw)
                starboard_valid = response.starboard_valid and response.starboard_or_single_deg is not None
                port_valid = response.port_valid is True and response.port_deg is not None
                if not starboard_valid and not port_valid:
                    raise IEC61162Error("RSA reports an invalid rudder angle status")
                primary = response.starboard_or_single_deg if starboard_valid else response.port_deg
                assert primary is not None
                command = ExternalRudderResponse(
                    starboard_or_single_deg=primary,
                    port_deg=response.port_deg if starboard_valid and port_valid else None,
                    received_monotonic_s=self._monotonic(),
                    source=f"{source_id}:{response.talker}RSA",
                )
                available_sides = tuple(
                    side for side, available in (("STBD", starboard_valid), ("PORT", port_valid)) if available
                )
            accepted = decoded.formatter in self.input_policy.sources
            # Both channels remain observable. Arbitration decides which layer
            # owns the model; the non-selected ROR can still drive order display.
            self.simulator.submit(command)
            return RudderInputDetection(
                decoded.formatter,
                len(available_sides) == 2,
                accepted,
                available_sides,
            )
        except IEC61162Error as error:
            self._on_error(source_id, str(error))
            return None
