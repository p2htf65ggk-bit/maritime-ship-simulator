# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from queue import Empty, Full, Queue
import socket
from threading import Event, Lock, Thread
import time

from .config import TcpEndpoint


class PersistentTcpSender:
    """Bounded, reconnecting TCP sender whose network work never blocks the UI."""

    def __init__(self, endpoint: TcpEndpoint):
        self.endpoint = endpoint
        self._queue: Queue[bytes | None] = Queue(maxsize=endpoint.queue_limit)
        self._stop = Event()
        self._socket: socket.socket | None = None
        self._socket_lock = Lock()
        self._last_error = ""
        self._connected = False
        self._thread = Thread(target=self._run, name=f"tcp-output-{endpoint.host}:{endpoint.port}", daemon=True)
        self._thread.start()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        return self._last_error

    def send(self, payload: bytes) -> bool:
        if self._stop.is_set():
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except Full:
            self._last_error = "TCP output queue is full"
            return False

    def wait_until_idle(self, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        return self._queue.unfinished_tasks == 0

    def close(self, timeout_s: float = 2.0) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._close_socket()
        self._thread.join(timeout_s)

    def _run(self) -> None:
        reconnect_delay_s = 0.05
        while not self._stop.is_set():
            try:
                payload = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                if payload is None:
                    return
                while not self._stop.is_set():
                    try:
                        connection = self._ensure_connection()
                        connection.sendall(payload)
                        reconnect_delay_s = 0.05
                        break
                    except OSError as error:
                        self._last_error = str(error)
                        self._connected = False
                        self._close_socket()
                        if self._stop.wait(reconnect_delay_s):
                            break
                        reconnect_delay_s = min(2.0, reconnect_delay_s * 2.0)
            finally:
                self._queue.task_done()

    def _ensure_connection(self) -> socket.socket:
        with self._socket_lock:
            if self._socket is None:
                self._socket = socket.create_connection(
                    (self.endpoint.host, self.endpoint.port),
                    timeout=self.endpoint.connect_timeout_s,
                )
                _configure_tcp_connection(self._socket)
                # A half-open peer must not hold the only output worker forever.
                # A bounded write lets the existing retry loop close and reconnect.
                self._socket.settimeout(max(0.25, self.endpoint.connect_timeout_s))
                self._connected = True
                self._last_error = ""
            return self._socket

    def _close_socket(self) -> None:
        with self._socket_lock:
            handle, self._socket = self._socket, None
        if handle is not None:
            try:
                handle.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                handle.close()
            except OSError:
                pass


class TcpServerSender:
    """Non-blocking multi-client TCP output server."""

    def __init__(self, endpoint: TcpEndpoint):
        if not endpoint.server:
            raise ValueError("TcpServerSender requires a server endpoint")
        self.endpoint = endpoint
        self._queue: Queue[bytes | None] = Queue(maxsize=endpoint.queue_limit)
        self._stop = Event()
        self._listener: socket.socket | None = None
        self._listener_lock = Lock()
        self._clients: set[_TcpClientWriter] = set()
        self._clients_lock = Lock()
        self._last_error = ""
        self._listening = False
        self._thread = Thread(
            target=self._run,
            name=f"tcp-output-server-{endpoint.host}:{endpoint.port}",
            daemon=True,
        )
        self._thread.start()

    @property
    def connected(self) -> bool:
        return self.client_count > 0

    @property
    def listening(self) -> bool:
        return self._listening

    @property
    def client_count(self) -> int:
        with self._clients_lock:
            return sum(client.connected for client in self._clients)

    @property
    def last_error(self) -> str:
        return self._last_error

    def send(self, payload: bytes) -> bool:
        if self._stop.is_set():
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except Full:
            self._last_error = "TCP server output queue is full"
            return False

    def wait_until_listening(self, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while not self._listening and not self._last_error and time.monotonic() < deadline:
            time.sleep(0.005)
        return self._listening

    def close(self, timeout_s: float = 2.0) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._close_listener()
        self._thread.join(timeout_s)
        self._close_clients()

    def _run(self) -> None:
        retry_delay_s = 0.1
        while not self._stop.is_set():
            try:
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((self.endpoint.host, self.endpoint.port))
                listener.listen(min(socket.SOMAXCONN, 64))
                listener.setblocking(False)
                with self._listener_lock:
                    self._listener = listener
                self._listening = True
                self._last_error = ""
                retry_delay_s = 0.1
                self._serve(listener)
            except OSError as error:
                if not self._stop.is_set():
                    self._last_error = str(error)
            finally:
                self._listening = False
                self._close_listener()
            if not self._stop.wait(retry_delay_s):
                retry_delay_s = min(2.0, retry_delay_s * 2.0)

    def _serve(self, listener: socket.socket) -> None:
        while not self._stop.is_set():
            while not self._stop.is_set():
                try:
                    connection, _address = listener.accept()
                except BlockingIOError:
                    break
                _configure_tcp_connection(connection)
                writer = _TcpClientWriter(connection, self.endpoint.queue_limit)
                with self._clients_lock:
                    self._clients.add(writer)
            self._dispatch_pending()
            self._prune_clients()
            self._stop.wait(0.01)

    def _dispatch_pending(self, maximum: int = 128) -> None:
        for _ in range(maximum):
            try:
                payload = self._queue.get_nowait()
            except Empty:
                return
            try:
                if payload is None:
                    return
                with self._clients_lock:
                    clients = tuple(self._clients)
                for client in clients:
                    client.send(payload)
            finally:
                self._queue.task_done()

    def _prune_clients(self) -> None:
        with self._clients_lock:
            stale = tuple(client for client in self._clients if not client.connected)
            for client in stale:
                self._clients.discard(client)
        for client in stale:
            client.close()

    def _close_clients(self) -> None:
        with self._clients_lock:
            clients = tuple(self._clients)
            self._clients.clear()
        for client in clients:
            client.close()

    def _close_listener(self) -> None:
        with self._listener_lock:
            listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass


class _TcpClientWriter:
    """Per-client writer used by TcpServerSender to isolate slow receivers."""

    def __init__(self, connection: socket.socket, queue_limit: int):
        self._connection = connection
        self._connection.settimeout(0.5)
        self._queue: Queue[bytes | None] = Queue(maxsize=queue_limit)
        self._stop = Event()
        self._connected = True
        self._thread = Thread(target=self._run, name="tcp-output-client", daemon=True)
        self._thread.start()

    @property
    def connected(self) -> bool:
        return self._connected and not self._stop.is_set()

    def send(self, payload: bytes) -> bool:
        if not self.connected:
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except Full:
            self._connected = False
            return False

    def close(self, timeout_s: float = 1.0) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        try:
            self._connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._connection.close()
        except OSError:
            pass
        self._thread.join(timeout_s)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    payload = self._queue.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    if payload is None:
                        return
                    self._connection.sendall(payload)
                finally:
                    self._queue.task_done()
        except OSError:
            pass
        finally:
            self._connected = False


def _configure_tcp_connection(connection: socket.socket) -> None:
    """Apply low-latency output and portable dead-peer detection where supported."""
    connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for option_name, value in (
        ("TCP_KEEPIDLE", 30),
        ("TCP_KEEPALIVE", 30),
        ("TCP_KEEPINTVL", 10),
        ("TCP_KEEPCNT", 3),
    ):
        option = getattr(socket, option_name, None)
        if option is None:
            continue
        try:
            connection.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError:
            pass
    windows_keepalive = getattr(socket, "SIO_KEEPALIVE_VALS", None)
    if windows_keepalive is not None and hasattr(connection, "ioctl"):
        try:
            connection.ioctl(windows_keepalive, (1, 30_000, 10_000))
        except OSError:
            pass
