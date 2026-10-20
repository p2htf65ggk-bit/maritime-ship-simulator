# SPDX-License-Identifier: MPL-2.0
import os
import socket
import struct
import subprocess
import sys
import textwrap
from threading import Event, Thread
import time
from pathlib import Path
import pytest

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import SetControlMode
from maritime_simulator.domain.enums import UdpMode
from maritime_simulator.domain.enums import ControlMode
from maritime_simulator.protocols.iec61162 import IEC61162Codec
from maritime_simulator.protocols.scheduler import ProtocolSchedule
from maritime_simulator.transport.config import (
    TcpEndpoint,
    TcpInputEndpoint,
    SerialEndpoint,
    UdpEndpoint,
    UdpInputEndpoint,
    resolve_udp_mode,
)
from maritime_simulator.transport.input_manager import IECInputManager, RORInputBridge
from maritime_simulator.transport.manager import ProtocolOutputService, RegisteredOutput
from maritime_simulator.transport.tcp import PersistentTcpSender, TcpServerSender
from maritime_simulator.transport.udp import UdpSocketPool


def _force_tcp_reset(connection: socket.socket) -> None:
    linger_format = "hh" if sys.platform == "win32" else "ii"
    connection.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_LINGER,
        struct.pack(linger_format, 1, 0),
    )
    connection.close()


def _input_server(received):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    manager = IECInputManager(lambda _source, sentence: received.append(sentence))
    manager.add_source("tcp", TcpInputEndpoint("127.0.0.1", port, server=True))
    manager.start()
    return manager, port


def _connect_input(port):
    deadline = time.monotonic() + 2
    while True:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=0.25)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _wait_input(received, expected):
    deadline = time.monotonic() + 2
    while received != expected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert received == expected


def test_tcp_input_close_interrupts_idle_client_and_releases_port_for_restart():
    received = []
    manager, port = _input_server(received)
    connection = _connect_input(port)
    try:
        connection.sendall(b"$TEST,first\r\n")
        _wait_input(received, ["$TEST,first"])
        worker = manager._threads["tcp"]
        manager.close(timeout_s=1)
        assert not worker.is_alive() and not manager._threads and not manager._sockets
        assert not manager.statuses()[0].connected
        try:
            connection.sendall(b"$TEST,after-close\r\n")
        except OSError:
            pass
        assert received == ["$TEST,first"]
        manager.start()
        with _connect_input(port) as restarted:
            restarted.sendall(b"$TEST,restarted\r\n")
            _wait_input(received, ["$TEST,first", "$TEST,restarted"])
    finally:
        connection.close()
        manager.close()


def test_tcp_input_listener_survives_client_reset():
    received = []
    manager, port = _input_server(received)
    try:
        first = _connect_input(port)
        first.sendall(b"$TEST,first\r\n")
        _wait_input(received, ["$TEST,first"])
        _force_tcp_reset(first)
        with _connect_input(port) as second:
            second.sendall(b"$TEST,second\r\n")
            _wait_input(received, ["$TEST,first", "$TEST,second"])
        assert manager._threads["tcp"].is_alive()
    finally:
        manager.close()


def test_input_short_close_retains_worker_and_prevents_reusing_stop_event(monkeypatch):
    entered, release = Event(), Event()
    manager = IECInputManager(lambda *_: None)
    manager.add_source("tcp", TcpInputEndpoint("127.0.0.1", 12345))
    def blocked(*_):
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(manager, "_run_source", blocked)
    manager.start()
    try:
        assert entered.wait(1)
        manager.close(timeout_s=0)
        assert manager._threads["tcp"].is_alive() and manager._stop.is_set()
        with pytest.raises(RuntimeError, match="still stopping"):
            manager.start()
        assert manager._stop.is_set()
    finally:
        release.set()
        manager._threads["tcp"].join(1)
    manager.start()
    manager.close()


@pytest.mark.parametrize("chunks", [
    [b"$TEST,one\r\n$TEST,two\n"],
    [b"$TEST,", b"one\r", b"\n$TEST,two", b"\n"],
    [b"\r\n", b"$TEST,one\n\r$TEST,two\r"],
])
def test_tcp_input_frames_fragmented_cr_lf_sentences(chunks):
    received = []
    manager = IECInputManager(lambda _source, sentence: received.append(sentence))
    manager.add_source("tcp", TcpInputEndpoint("127.0.0.1", 12345))
    chunks = iter(chunks)
    class Stream:
        def recv(self, _size):
            return next(chunks, b"")
    manager._receive_stream("tcp", Stream(), manager._status["tcp"])
    assert received == ["$TEST,one", "$TEST,two"]


def test_tcp_input_discards_oversized_line_until_delimiter_with_bounded_memory():
    import tracemalloc
    received, errors = [], []
    manager = IECInputManager(lambda _source, sentence: received.append(sentence),
                              lambda _source, error: errors.append(error))
    manager.add_source("tcp", TcpInputEndpoint("127.0.0.1", 12345))
    class Stream:
        calls = 0
        def recv(self, _size):
            self.calls += 1
            if self.calls <= 1024:
                return b"x" * 4096
            return b"invalid-tail\n$TEST,recovered\r\n" if self.calls == 1025 else b""
    tracemalloc.start()
    try:
        manager._receive_stream("tcp", Stream(), manager._status["tcp"])
        assert tracemalloc.get_traced_memory()[1] < 256 * 1024
    finally:
        tracemalloc.stop()
    assert received == ["$TEST,recovered"]
    assert len(errors) == 1 and "bounded line buffer" in errors[0]


def test_tcp_input_does_not_emit_data_received_during_close():
    received = []
    manager = IECInputManager(lambda _source, sentence: received.append(sentence))
    manager.add_source("tcp", TcpInputEndpoint("127.0.0.1", 12345))
    class Stream:
        def recv(self, _size):
            manager._stop.set()
            return b"$TEST,too-late\n"
    manager._receive_stream("tcp", Stream(), manager._status["tcp"])
    assert not received


def test_udp_auto_mode_resolves_unicast_broadcast_and_multicast():
    assert resolve_udp_mode(UdpEndpoint("127.0.0.1", 4096)) is UdpMode.UNICAST
    assert resolve_udp_mode(UdpEndpoint("255.255.255.255", 4096)) is UdpMode.BROADCAST
    assert resolve_udp_mode(UdpEndpoint("239.192.192.1", 4096)) is UdpMode.MULTICAST


def test_udp_configuration_rejects_invalid_multicast_and_interface_combinations():
    with pytest.raises(ValueError, match="multicast"):
        UdpEndpoint("127.0.0.1", 4096, mode=UdpMode.MULTICAST)
    with pytest.raises(ValueError, match="interface"):
        UdpEndpoint("239.192.192.1", 4096, interface_ip="not-an-ip")
    with pytest.raises(ValueError, match="multicast"):
        UdpInputEndpoint("0.0.0.0", 4096, multicast_group="127.0.0.1")


def test_udp_pool_reuses_socket_and_closes_cleanly():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(1.0)
    endpoint = UdpEndpoint("127.0.0.1", receiver.getsockname()[1])
    pool = UdpSocketPool()
    try:
        pool.send(endpoint, b"one")
        pool.send(endpoint, b"two")
        assert receiver.recvfrom(32)[0] == b"one"
        assert receiver.recvfrom(32)[0] == b"two"
        assert len(pool._sockets) == 1
    finally:
        pool.close()
        pool.close()
        receiver.close()


def test_tcp_sender_keeps_one_connection_for_multiple_sentences():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2.0)
    received = bytearray()
    connection_count = []
    finished = Event()

    def server():
        try:
            connection, _ = listener.accept()
            connection_count.append(1)
            with connection:
                while len(received) < 6:
                    chunk = connection.recv(32)
                    if not chunk:
                        break
                    received.extend(chunk)
        finally:
            finished.set()

    thread = Thread(target=server, daemon=True)
    thread.start()
    sender = PersistentTcpSender(TcpEndpoint("127.0.0.1", listener.getsockname()[1]))
    try:
        assert sender.send(b"one")
        assert sender.send(b"two")
        assert finished.wait(2.0)
        assert bytes(received) == b"onetwo"
        assert len(connection_count) == 1
    finally:
        sender.close()
        listener.close()
        thread.join(1.0)


def test_tcp_client_output_reconnects_after_peer_reset():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    listener.settimeout(2.0)
    first_reset = Event()
    reconnected = Event()
    received = []

    def server():
        try:
            first, _ = listener.accept()
            first.settimeout(1.0)
            received.append(first.recv(32))
            _force_tcp_reset(first)
            first_reset.set()

            second, _ = listener.accept()
            with second:
                second.settimeout(1.0)
                received.append(second.recv(32))
            reconnected.set()
        finally:
            listener.close()

    thread = Thread(target=server, daemon=True)
    thread.start()
    sender = PersistentTcpSender(
        TcpEndpoint("127.0.0.1", listener.getsockname()[1], connect_timeout_s=0.25)
    )
    try:
        assert sender.send(b"first")
        assert first_reset.wait(1.5)
        time.sleep(0.05)
        assert sender.send(b"second")
        assert reconnected.wait(2.0), sender.last_error
        assert received == [b"first", b"second"]
        assert sender.connected
    finally:
        sender.close()
        thread.join(1.0)


def test_tcp_output_server_accepts_multiple_clients_without_blocking_each_other():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    sender = TcpServerSender(TcpEndpoint("127.0.0.1", port, server=True))
    clients = []
    try:
        assert sender.wait_until_listening(2.0), sender.last_error
        for _ in range(2):
            client = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            client.settimeout(1.0)
            clients.append(client)
        deadline = time.monotonic() + 1.0
        while sender.client_count < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sender.client_count == 2
        with sender._clients_lock:
            writers = tuple(sender._clients)
        assert all(
            bool(writer._connection.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE))
            for writer in writers
        )
        assert sender.send(b"$GPHDT,123.4,T*00\r\n")
        assert [client.recv(128) for client in clients] == [
            b"$GPHDT,123.4,T*00\r\n",
            b"$GPHDT,123.4,T*00\r\n",
        ]
    finally:
        for client in clients:
            client.close()
        sender.close()


def test_tcp_output_server_survives_repeated_client_reconnects():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    sender = TcpServerSender(TcpEndpoint("127.0.0.1", port, server=True))
    try:
        assert sender.wait_until_listening(2.0), sender.last_error
        for cycle in range(5):
            client = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            client.settimeout(1.0)
            deadline = time.monotonic() + 1.0
            while sender.client_count < 1 and time.monotonic() < deadline:
                time.sleep(0.005)
            assert sender.client_count == 1
            payload = f"$GPHDT,{cycle:03d}.0,T*00\r\n".encode("ascii")
            assert sender.send(payload)
            assert client.recv(128) == payload
            client.close()
            deadline = time.monotonic() + 1.0
            while sender.client_count and time.monotonic() < deadline:
                sender.send(b"$GPHDT,000.0,T*00\r\n")
                time.sleep(0.01)
            assert sender.client_count == 0
            deadline = time.monotonic() + 1.0
            while sender._queue.unfinished_tasks and time.monotonic() < deadline:
                time.sleep(0.005)
            assert sender._queue.unfinished_tasks == 0
            assert sender.listening
            assert not sender.last_error
    finally:
        sender.close()


def test_tcp_output_server_accepts_new_client_after_hard_reset():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    sender = TcpServerSender(TcpEndpoint("127.0.0.1", port, server=True))
    try:
        assert sender.wait_until_listening(2.0), sender.last_error
        first = socket.create_connection(("127.0.0.1", port), timeout=1.0)
        deadline = time.monotonic() + 1.0
        while sender.client_count < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert sender.client_count == 1
        _force_tcp_reset(first)

        # Exercise the failed writer so the server can prune it.
        deadline = time.monotonic() + 1.0
        while sender.client_count and time.monotonic() < deadline:
            sender.send(b"probe\r\n")
            time.sleep(0.01)
        assert sender.client_count == 0
        deadline = time.monotonic() + 2.0
        while sender._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        assert sender._queue.unfinished_tasks == 0

        second = socket.create_connection(("127.0.0.1", port), timeout=1.0)
        try:
            second.settimeout(1.0)
            deadline = time.monotonic() + 1.0
            while sender.client_count < 1 and time.monotonic() < deadline:
                time.sleep(0.005)
            assert sender.client_count == 1
            payload = b"$GPHDT,123.4,T*00\r\n"
            assert sender.send(payload)
            assert second.recv(128) == payload
            assert sender.listening
            assert not sender.last_error
        finally:
            second.close()
    finally:
        sender.close()
def test_tcp_output_server_listens_as_soon_as_schedule_is_registered():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    endpoint = TcpEndpoint("127.0.0.1", port, server=True)
    service = ProtocolOutputService()
    try:
        service.register(
            RegisteredOutput(ProtocolSchedule("ais-tcp", "VDM", 1.0, "tcp-server"), endpoint),
            now_s=0.0,
        )
        sender = service.output_manager._tcp[endpoint]
        assert isinstance(sender, TcpServerSender)
        assert sender.wait_until_listening(2.0), sender.last_error
    finally:
        service.close()


def test_udp_input_manager_receives_complete_sentences_and_stops():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    received = []
    ready = Event()

    def on_sentence(source, sentence):
        received.append((source, sentence))
        ready.set()

    manager = IECInputManager(on_sentence)
    manager.add_source("udp-ror", UdpInputEndpoint("127.0.0.1", port))
    manager.start()
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sentence = IEC61162Codec.frame("AGROR,12.0,A,,,B,,,,")
    try:
        for _ in range(10):
            sender.sendto((sentence + "\r\n").encode("ascii"), ("127.0.0.1", port))
            if ready.wait(0.1):
                break
        assert ready.is_set()
        assert received[0] == ("udp-ror", sentence)
    finally:
        sender.close()
        manager.close()
    assert all(not status.running for status in manager.statuses())


@pytest.mark.skipif(sys.platform == "win32", reason="Pseudo-terminal serial probe is POSIX-only")
def test_event_driven_serial_input_receives_a_sustained_stream_without_blocking():
    script = textwrap.dedent(
        """
        import os
        import pty
        import time
        from threading import Event

        from PySide6.QtCore import QCoreApplication, QTimer

        from maritime_simulator.protocols.iec61162 import IEC61162Codec
        from maritime_simulator.transport.config import SerialEndpoint
        from maritime_simulator.transport.input_manager import IECInputManager

        app = QCoreApplication([])
        master, slave = pty.openpty()
        device = os.ttyname(slave)
        received = []
        complete = Event()

        def on_sentence(source, sentence):
            received.append((source, sentence))
            if len(received) >= 40:
                complete.set()

        manager = IECInputManager(on_sentence)
        manager.add_source("serial-ror", SerialEndpoint(device, baud=4800))
        manager.start()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                status = manager.statuses()[0]
                if status.connected:
                    break
                time.sleep(0.01)
            else:
                raise AssertionError(manager.statuses()[0].last_error)

            heartbeats = []
            heartbeat = QTimer()
            heartbeat.setInterval(10)
            heartbeat.timeout.connect(lambda: heartbeats.append(time.monotonic()))
            heartbeat.start()
            QTimer.singleShot(500, app.quit)
            app.exec()
            heartbeat.stop()
            assert len(heartbeats) >= 20, len(heartbeats)

            sentence = IEC61162Codec.frame("AGROR,12.0,A,,,B,,,,")
            payload = ((sentence + "\\r\\n") * 40).encode("ascii")
            os.write(master, payload)
            assert complete.wait(2.0), len(received)
            assert len(received) == 40
            assert all(item == ("serial-ror", sentence) for item in received)
            status = manager.statuses()[0]
            assert status.sentences_received == 40
            assert status.last_sentence_age_s is not None
        finally:
            started = time.monotonic()
            manager.close(timeout_s=1.0)
            assert time.monotonic() - started < 1.0
            os.close(master)
            os.close(slave)
        """
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        (sys.executable, "-c", script),
        env=environment,
        capture_output=True,
        text=True,
        timeout=8.0,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_serial_input_uses_ready_read_instead_of_blocking_waits():
    source = Path("src/maritime_simulator/transport/input_manager.py").read_text(encoding="utf-8")
    assert "handle.readyRead.connect(read_available)" in source
    assert "class _SerialInputThread(QThread)" in source
    assert "waitForReadyRead" not in source


def test_ror_bridge_only_owns_rudder_in_external_mode_and_times_out():
    now = [20.0]
    service = SimulatorService(monotonic=lambda: now[0])
    errors = []
    bridge = RORInputBridge(service, monotonic=lambda: now[0], on_error=lambda source, error: errors.append((source, error)))
    bridge.on_sentence("udp-ror", IEC61162Codec.frame("AGROR,-11.5,A,,,B,,,,"))
    service.advance_wall_time(0.1)
    assert service.snapshot().rudder_order_starboard_deg == 0.0

    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    service.advance_wall_time(0.1)
    assert service.snapshot().rudder_order_starboard_deg == -11.5
    now[0] += 3.0
    service.advance_wall_time(0.1)
    assert service.snapshot().rudder_order_starboard_deg == 0.0
    assert not errors


def test_external_rudder_bridge_detects_and_applies_single_and_dual_ror():
    now = [30.0]
    service = SimulatorService(monotonic=lambda: now[0])
    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    bridge = RORInputBridge(service, monotonic=lambda: now[0], input_type="ROR")

    single = bridge.on_sentence("udp-rudder", IEC61162Codec.frame("AGROR,9.0,A,,,B,,,,"))
    assert single is not None and single.input_type == "ROR"
    assert single.dual_rudder is False and single.accepted_for_control is True
    service.advance_wall_time(0.1)
    snapshot = service.snapshot()
    assert snapshot.dual_rudder is False
    assert snapshot.rudder_order_starboard_deg == 9.0
    assert snapshot.rudder_order_port_deg == 9.0

    now[0] += 0.1
    dual = bridge.on_sentence("udp-rudder", IEC61162Codec.frame("AGROR,12.0,A,-7.0,A,B,,,,"))
    assert dual is not None and dual.dual_rudder is True
    service.advance_wall_time(0.1)
    snapshot = service.snapshot()
    assert snapshot.dual_rudder is True
    assert snapshot.rudder_order_starboard_deg == 12.0
    assert snapshot.rudder_order_port_deg == -7.0
    service.close()


def test_external_rudder_bridge_observes_other_type_and_applies_single_and_dual_rsa():
    now = [40.0]
    service = SimulatorService(monotonic=lambda: now[0])
    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    bridge = RORInputBridge(service, monotonic=lambda: now[0], input_type="RSA")

    observed = bridge.on_sentence("udp-rudder", IEC61162Codec.frame("AGROR,5.0,A,,,B,,,,"))
    assert observed is not None and observed.accepted_for_control is False

    single = bridge.on_sentence("udp-rudder", IEC61162Codec.frame("AGRSA,6.0,A,,"))
    assert single is not None and single.dual_rudder is False and single.accepted_for_control is True
    service.advance_wall_time(0.1)
    snapshot = service.snapshot()
    assert snapshot.dual_rudder is False
    assert snapshot.rudder_response_starboard_deg == 6.0
    assert snapshot.rudder_response_port_deg == 6.0

    now[0] += 0.1
    dual = bridge.on_sentence("udp-rudder", IEC61162Codec.frame("AGRSA,10.0,A,-4.0,A"))
    assert dual is not None and dual.dual_rudder is True
    service.advance_wall_time(0.1)
    snapshot = service.snapshot()
    assert snapshot.dual_rudder is True
    assert snapshot.rudder_response_starboard_deg == 10.0
    assert snapshot.rudder_response_port_deg == -4.0
    service.close()


def test_external_rsa_bridge_processes_real_port_only_capture():
    service = SimulatorService(monotonic=lambda: 50.0)
    service.submit(SetControlMode(ControlMode.EXTERNAL_RUDDER))
    bridge = RORInputBridge(service, monotonic=lambda: 50.0, input_type="RSA")

    detected = bridge.on_sentence("udp-rudder", "$AGRSA,,V,-0.0,A*52")

    assert detected is not None
    assert detected.available_sides == ("PORT",)
    assert detected.dual_rudder is False
    assert detected.accepted_for_control is True
    service.advance_wall_time(0.1)
    snapshot = service.snapshot()
    assert snapshot.rudder_response_starboard_deg == pytest.approx(0.0)
    assert snapshot.rudder_response_port_deg == pytest.approx(0.0)
    service.close()
