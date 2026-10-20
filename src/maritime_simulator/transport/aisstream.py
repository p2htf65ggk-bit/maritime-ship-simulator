# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import json
import ssl
from threading import Event, RLock, Thread, current_thread
import time
from typing import Callable, Sequence

import certifi
from websockets.sync.client import connect


AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"


def verified_ssl_context() -> ssl.SSLContext:
    """Use system trust plus a bundled CA set in portable Python builds."""
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


@dataclass(frozen=True, slots=True)
class AISStreamSubscription:
    api_key: str
    bounding_boxes: tuple[tuple[tuple[float, float], tuple[float, float]], ...]
    message_types: tuple[str, ...]

    @classmethod
    def create(
        cls,
        api_key: str,
        bounding_boxes: Sequence[Sequence[Sequence[float]]],
        message_types: Sequence[str],
    ) -> AISStreamSubscription:
        boxes = tuple(
            (
                (float(box[0][0]), float(box[0][1])),
                (float(box[1][0]), float(box[1][1])),
            )
            for box in bounding_boxes
        )
        return cls(str(api_key).strip(), boxes, tuple(str(item) for item in message_types))

    def payload(self) -> dict[str, object]:
        return {
            "APIKey": self.api_key,
            "BoundingBoxes": [
                [[south, west], [north, east]]
                for ((south, west), (north, east)) in self.bounding_boxes
            ],
            "FilterMessageTypes": list(self.message_types),
        }


@dataclass(frozen=True, slots=True)
class AISStreamWorkerState:
    state: str
    subscription_sent: bool = False
    compression_enabled: bool | None = None
    latency_ms: int | None = None
    error: str = ""


class AISStreamWorker:
    """Compression-capable aisstream.io transport running outside the Qt thread."""

    _SUBSCRIPTION_INTERVAL_S = 1.05
    _RECEIVE_POLL_S = 0.20
    _PING_INTERVAL_S = 10.0
    _MAX_BACKOFF_S = 30.0

    def __init__(
        self,
        *,
        on_message: Callable[[str], None],
        on_state: Callable[[AISStreamWorkerState], None],
        connect_factory: Callable[..., object] = connect,
        ssl_context_factory: Callable[[], ssl.SSLContext] = verified_ssl_context,
        monotonic: Callable[[], float] = time.monotonic,
        url: str = AISSTREAM_URL,
    ):
        self._on_message = on_message
        self._on_state = on_state
        self._connect = connect_factory
        self._ssl_context_factory = ssl_context_factory
        self._monotonic = monotonic
        self._url = url
        self._lock = RLock()
        self._thread: Thread | None = None
        self._stop_event: Event | None = None
        self._connection = None
        self._subscription: AISStreamSubscription | None = None
        self._subscription_revision = 0
        self._generation = 0

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, subscription: AISStreamSubscription) -> None:
        self.stop()
        with self._lock:
            self._generation += 1
            generation = self._generation
            stop_event = Event()
            self._stop_event = stop_event
            self._subscription = subscription
            self._subscription_revision = 1
            thread = Thread(
                target=self._run,
                args=(generation, stop_event),
                name="aisstream-client",
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def update_subscription(self, subscription: AISStreamSubscription) -> None:
        with self._lock:
            self._subscription = subscription
            self._subscription_revision += 1

    def stop(self, *, join_timeout_s: float = 1.0) -> None:
        with self._lock:
            self._generation += 1
            stop_event = self._stop_event
            connection = self._connection
            thread = self._thread
            self._stop_event = None
            self._connection = None
            self._thread = None
        if stop_event is not None:
            stop_event.set()
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if thread is not None and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=max(0.0, float(join_timeout_s)))

    def _run(self, generation: int, stop_event: Event) -> None:
        backoff_s = 1.0
        first_attempt = True
        while not stop_event.is_set() and self._is_current(generation):
            state = "Connecting" if first_attempt else "Reconnecting"
            self._notify_state(generation, AISStreamWorkerState(state=state))
            first_attempt = False
            connection = None
            try:
                connection = self._connect(
                    self._url,
                    ssl=self._ssl_context_factory(),
                    compression="deflate",
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=1,
                    max_size=2 * 1024 * 1024,
                    max_queue=256,
                    # The worker owns and closes this connection explicitly so
                    # another thread can interrupt a blocking receive on stop.
                    legacy=True,
                )
                if stop_event.is_set() or not self._set_connection(generation, connection):
                    connection.close()
                    return
                compression_enabled = self._connection_uses_compression(connection)
                self._notify_state(
                    generation,
                    AISStreamWorkerState(
                        state="Connected",
                        compression_enabled=compression_enabled,
                    ),
                )
                backoff_s = 1.0
                self._stream(connection, generation, stop_event, compression_enabled)
            except Exception as exc:
                if not stop_event.is_set() and self._is_current(generation):
                    self._notify_state(
                        generation,
                        AISStreamWorkerState(state="Reconnecting", error=self._safe_error(exc)),
                    )
            finally:
                self._clear_connection(generation, connection)
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
            if stop_event.wait(backoff_s):
                break
            backoff_s = min(self._MAX_BACKOFF_S, backoff_s * 2.0)
        self._notify_state(generation, AISStreamWorkerState(state="Disconnected"))

    def _stream(self, connection, generation: int, stop_event: Event, compression_enabled: bool) -> None:
        sent_revision = 0
        last_subscription_at = -self._SUBSCRIPTION_INTERVAL_S
        next_ping_at = self._monotonic() + self._PING_INTERVAL_S
        ping_event = None
        ping_started_at = 0.0
        latency_ms = None

        while not stop_event.is_set() and self._is_current(generation):
            now = self._monotonic()
            subscription, revision = self._subscription_snapshot()
            if (
                subscription is not None and
                revision != sent_revision and
                (sent_revision == 0 or now - last_subscription_at >= self._SUBSCRIPTION_INTERVAL_S)
            ):
                connection.send(json.dumps(subscription.payload(), separators=(",", ":")))
                sent_revision = revision
                last_subscription_at = now
                self._notify_state(
                    generation,
                    AISStreamWorkerState(
                        state="Connected",
                        subscription_sent=True,
                        compression_enabled=compression_enabled,
                        latency_ms=latency_ms,
                    ),
                )

            if ping_event is not None and ping_event.is_set():
                latency_ms = max(0, int(round((now - ping_started_at) * 1000.0)))
                ping_event = None
                self._notify_state(
                    generation,
                    AISStreamWorkerState(
                        state="Connected",
                        subscription_sent=sent_revision > 0,
                        compression_enabled=compression_enabled,
                        latency_ms=latency_ms,
                    ),
                )
            if ping_event is None and now >= next_ping_at:
                ping_started_at = now
                ping_event = connection.ping()
                next_ping_at = now + self._PING_INTERVAL_S

            try:
                frame = connection.recv(timeout=self._RECEIVE_POLL_S, decode=True)
            except TimeoutError:
                continue
            if isinstance(frame, bytes):
                frame = frame.decode("utf-8")
            self._notify_message(generation, str(frame))

    def _subscription_snapshot(self) -> tuple[AISStreamSubscription | None, int]:
        with self._lock:
            return self._subscription, self._subscription_revision

    def _set_connection(self, generation: int, connection) -> bool:
        with self._lock:
            if generation != self._generation:
                return False
            self._connection = connection
            return True

    def _clear_connection(self, generation: int, connection) -> None:
        with self._lock:
            if generation == self._generation and self._connection is connection:
                self._connection = None

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def _notify_message(self, generation: int, message: str) -> None:
        if not self._is_current(generation):
            return
        try:
            self._on_message(message)
        except RuntimeError:
            pass

    def _notify_state(self, generation: int, state: AISStreamWorkerState) -> None:
        if not self._is_current(generation):
            return
        try:
            self._on_state(state)
        except RuntimeError:
            pass

    @staticmethod
    def _connection_uses_compression(connection) -> bool:
        protocol = getattr(connection, "protocol", None)
        extensions = getattr(protocol, "extensions", ()) or ()
        return any("permessage" in type(extension).__name__.lower() for extension in extensions)

    @staticmethod
    def _safe_error(error: Exception) -> str:
        text = str(error).strip()
        return text or type(error).__name__
