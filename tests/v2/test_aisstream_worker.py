# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import json
from queue import Empty, Queue
import ssl
import time

from maritime_simulator.transport.aisstream import (
    AISStreamSubscription,
    AISStreamWorker,
    verified_ssl_context,
)


class PerMessageDeflate:
    pass


class _Protocol:
    extensions = (PerMessageDeflate(),)


class _FakeConnection:
    def __init__(self):
        self.protocol = _Protocol()
        self.frames = Queue()
        self.sent: list[str] = []
        self.closed = False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout=None, decode=None):
        try:
            return self.frames.get(timeout=timeout)
        except Empty as exc:
            raise TimeoutError from exc

    def ping(self):
        from threading import Event

        event = Event()
        event.set()
        return event

    def close(self) -> None:
        self.closed = True


def _wait_for(predicate, timeout_s=1.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def _subscription(longitude: float) -> AISStreamSubscription:
    return AISStreamSubscription.create(
        "test-key",
        [[[50.0, longitude], [51.0, longitude + 1.0]]],
        ["PositionReport", "ShipStaticData"],
    )


def test_aisstream_worker_requests_compression_and_sends_subscription_immediately():
    connection = _FakeConnection()
    connect_calls = []
    states = []
    frames = []

    def connect_factory(url, **kwargs):
        connect_calls.append((url, kwargs))
        return connection

    worker = AISStreamWorker(
        on_message=frames.append,
        on_state=states.append,
        connect_factory=connect_factory,
    )
    connection.frames.put('{"MessageType":"SubscriptionConfirmation"}')
    worker.start(_subscription(3.0))
    try:
        _wait_for(lambda: bool(connection.sent) and bool(frames))
        assert connect_calls[0][1]["compression"] == "deflate"
        assert connect_calls[0][1]["legacy"] is True
        assert connect_calls[0][1]["ssl"].verify_mode == ssl.CERT_REQUIRED
        assert connect_calls[0][1]["ssl"].check_hostname is True
        assert json.loads(connection.sent[0])["APIKey"] == "test-key"
        assert any(state.compression_enabled is True for state in states)
        assert any(state.subscription_sent for state in states)
        assert frames == ['{"MessageType":"SubscriptionConfirmation"}']
    finally:
        worker.stop()
    assert connection.closed


def test_aisstream_ssl_context_has_portable_certificate_authorities():
    context = verified_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.get_ca_certs()


def test_aisstream_worker_updates_scope_on_same_connection_and_coalesces_latest_value():
    connection = _FakeConnection()
    worker = AISStreamWorker(
        on_message=lambda _message: None,
        on_state=lambda _state: None,
        connect_factory=lambda _url, **_kwargs: connection,
    )
    worker._SUBSCRIPTION_INTERVAL_S = 0.02
    worker.start(_subscription(3.0))
    try:
        _wait_for(lambda: len(connection.sent) == 1)
        worker.update_subscription(_subscription(4.0))
        worker.update_subscription(_subscription(5.0))
        _wait_for(lambda: len(connection.sent) == 2)
        assert json.loads(connection.sent[-1])["BoundingBoxes"][0][0][1] == 5.0
        assert len(connection.sent) == 2
    finally:
        worker.stop()
