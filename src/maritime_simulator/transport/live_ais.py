# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import replace
import json
import math
import time
from urllib.parse import urlencode

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from maritime_simulator.application.ais import AISRepository, AISTarget
from maritime_simulator.transport.aisstream import (
    AISStreamSubscription,
    AISStreamWorker,
    AISStreamWorkerState,
)


class LiveAISClient(QObject):
    """Provider-aware asynchronous live-AIS client."""

    status_changed = Signal(str)
    diagnostics_changed = Signal(object)
    _aisstream_message_received = Signal(str)
    _aisstream_state_received = Signal(object)

    _NO_DATA_WARNING_S = 15.0
    _TARGET_RETENTION_S = 360.0
    AISSTREAM = "aisstream"
    BARENTSWATCH = "barentswatch"

    def __init__(self, repository: AISRepository, snapshot, parent=None, *, aisstream_worker_factory=None):
        super().__init__(parent)
        self.repository = repository
        self.snapshot = snapshot
        self.provider = self.AISSTREAM
        self.offline = False
        self.api_key = ""
        self.barentswatch_client_id = ""
        self.barentswatch_client_secret = ""
        self.subscription_range_nm = 250.0
        self.worldwide_debug = False
        self.status = "Disconnected"
        self.message_count = 0
        self.parsed_count = 0
        self.discarded_count = 0
        self.invalid_json_count = 0
        self.server_error = ""
        self.subscription_sent = False
        self._connected_at: float | None = None
        self._last_frame_at: float | None = None
        self._last_target_at: float | None = None
        self._last_pong_ms: int | None = None
        self._compression_enabled: bool | None = None
        self._last_message_type = ""
        self._last_boxes: list = []
        self._scope_center: tuple[float, float] | None = None
        self._last_seen: dict[int, float] = {}
        self._static_metadata: dict[int, dict] = {}
        self._transport_state = "Disconnected"
        self._stream_buffer = bytearray()
        self._network_reply: QNetworkReply | None = None
        self._token_reply: QNetworkReply | None = None
        self.network = QNetworkAccessManager(self)
        self._aisstream_message_received.connect(self._message, Qt.ConnectionType.QueuedConnection)
        self._aisstream_state_received.connect(self._aisstream_state_changed, Qt.ConnectionType.QueuedConnection)
        worker_factory = aisstream_worker_factory or AISStreamWorker
        self._aisstream = worker_factory(
            on_message=self._aisstream_message_received.emit,
            on_state=self._aisstream_state_received.emit,
        )
        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.setInterval(1000)
        self.watchdog_timer.timeout.connect(self._watchdog)
        self.prune_timer = QTimer(self)
        self.prune_timer.setInterval(30000)
        self.prune_timer.timeout.connect(self._prune)
        self.prune_timer.start()
        self.scope_refresh_timer = QTimer(self)
        self.scope_refresh_timer.setSingleShot(True)
        self.scope_refresh_timer.setInterval(200)
        self.scope_refresh_timer.timeout.connect(self._refresh_scope_now)

    def connect_live(self) -> None:
        if self.offline:
            self._set_status("Offline: Live AIS paused; select Generated or Synthetic traffic")
            return
        if self.provider == self.AISSTREAM and not self.api_key.strip():
            self._set_status("API key required")
            return
        if self.provider == self.BARENTSWATCH and (
            not self.barentswatch_client_id.strip() or not self.barentswatch_client_secret.strip()
        ):
            self._set_status("BarentsWatch client ID and client secret are required")
            return
        self.disconnect_live()
        self.message_count = 0
        self.parsed_count = 0
        self.discarded_count = 0
        self.invalid_json_count = 0
        self.server_error = ""
        self.subscription_sent = False
        self._connected_at = None
        self._last_frame_at = None
        self._last_target_at = None
        self._last_pong_ms = None
        self._compression_enabled = None
        self._last_message_type = ""
        self._scope_center = None
        self._stream_buffer.clear()
        self._static_metadata.clear()
        self.watchdog_timer.start()
        if self.provider == self.BARENTSWATCH:
            self._transport_state = "Authenticating"
            self._connected_at = time.monotonic()
            self._set_status("Authenticating with the Kystverket / BarentsWatch AIS service...")
            self._request_barentswatch_token()
        else:
            self.provider = self.AISSTREAM
            self._transport_state = "Connecting"
            self._set_status("Connecting to aisstream.io...")
            self._aisstream.start(self._aisstream_subscription())

    def disconnect_live(self) -> None:
        self.watchdog_timer.stop()
        self.scope_refresh_timer.stop()
        self._aisstream.stop()
        for reply in (self._network_reply, self._token_reply):
            if reply is not None and reply.isRunning():
                reply.abort()
        self._network_reply = None
        self._token_reply = None

        self._transport_state = "Disconnected"

    def set_offline(self, enabled: bool) -> None:
        changed = self.offline != bool(enabled)
        self.offline = bool(enabled)
        if self.offline:
            self.disconnect_live()
            self._set_status("Offline: Live AIS paused; select Generated or Synthetic traffic")
        elif changed and self.repository.mode == "live":
            self.connect_live()

    def own_ship_position_changed(self) -> None:
        """Refresh a local live-AIS scope after an explicit own-ship seek/move."""
        if self.offline or self.repository.mode != "live" or self.worldwide_debug:
            return
        self.scope_refresh_timer.start()

    def _request_barentswatch_token(self) -> None:
        request = QNetworkRequest(QUrl("https://id.barentswatch.no/connect/token"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/x-www-form-urlencoded")
        body = urlencode({
            "client_id": self.barentswatch_client_id.strip(),
            "client_secret": self.barentswatch_client_secret.strip(),
            "scope": "ais",
            "grant_type": "client_credentials",
        }).encode("utf-8")
        reply = self.network.post(request, QByteArray(body))
        self._token_reply = reply
        reply.finished.connect(lambda current=reply: self._barentswatch_token_finished(current))

    def _barentswatch_token_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._token_reply:
            reply.deleteLater()
            return
        self._token_reply = None
        raw = bytes(reply.readAll())
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.server_error = reply.errorString()
            self._transport_state = "Error"
            self._set_status(f"BarentsWatch authentication failed: {self.server_error}")
            reply.deleteLater()
            return
        try:
            token = str(json.loads(raw.decode("utf-8"))["access_token"])
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            self._transport_state = "Error"
            self._set_status("BarentsWatch authentication returned no usable access token")
            reply.deleteLater()
            return
        reply.deleteLater()
        request = QNetworkRequest(QUrl("https://live.ais.barentswatch.no/v1/combined"))
        request.setRawHeader(b"Authorization", f"Bearer {token}".encode("utf-8"))
        request.setRawHeader(b"Accept", b"application/x-ndjson")
        stream = self.network.get(request)
        self._network_reply = stream
        self._transport_state = "Streaming"
        self.server_error = ""
        self.subscription_sent = True
        own_ship = self.snapshot()
        self._scope_center = (own_ship.latitude_deg, own_ship.longitude_deg)
        stream.readyRead.connect(lambda current=stream: self._barentswatch_ready_read(current))
        stream.finished.connect(lambda current=stream: self._barentswatch_finished(current))
        self._set_status("Kystverket / BarentsWatch stream connected; waiting for AIS reports...")

    def _barentswatch_ready_read(self, reply: QNetworkReply) -> None:
        if reply is not self._network_reply:
            return
        self._stream_buffer.extend(bytes(reply.readAll()))
        while b"\n" in self._stream_buffer:
            line, _, remainder = self._stream_buffer.partition(b"\n")
            self._stream_buffer = bytearray(remainder)
            if not line.strip():
                continue
            self.message_count += 1
            self._last_frame_at = time.monotonic()
            try:
                payload = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.invalid_json_count += 1
                self.discarded_count += 1
                continue
            target = self._target_from_barentswatch(payload)
            if target is None or not self._target_in_scope(target):
                self.discarded_count += 1
                continue
            self.repository.ingest_live((target,))
            now = time.monotonic()
            self._last_seen[target.mmsi] = now
            self._last_target_at = now
            self.parsed_count += 1
        self._set_status(
            f"Kystverket AIS active: {self.parsed_count} valid reports, "
            f"{len(self.repository.all_targets())} current targets"
        )

    def _barentswatch_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._network_reply:
            reply.deleteLater()
            return
        self._network_reply = None
        if self.provider == self.BARENTSWATCH:
            self._transport_state = "Disconnected"
            suffix = f": {reply.errorString()}" if reply.error() != QNetworkReply.NetworkError.NoError else ""
            self._set_status(f"Kystverket / BarentsWatch stream disconnected{suffix}")
        reply.deleteLater()

    def _aisstream_subscription(self) -> AISStreamSubscription:
        own_ship = self.snapshot()
        boxes = self._subscription_boxes(own_ship)
        self._scope_center = (own_ship.latitude_deg, own_ship.longitude_deg)
        self._last_boxes = boxes
        return AISStreamSubscription.create(
            self.api_key,
            boxes,
            (
                "PositionReport",
                "StandardClassBPositionReport",
                "ExtendedClassBPositionReport",
                "ShipStaticData",
                "StaticDataReport",
            ),
        )

    def _aisstream_state_changed(self, state: AISStreamWorkerState) -> None:
        if self.offline or self.provider != self.AISSTREAM:
            return
        previous_state = self._transport_state
        previous_subscription = self.subscription_sent
        self._transport_state = state.state
        if state.compression_enabled is not None:
            self._compression_enabled = bool(state.compression_enabled)
        if state.latency_ms is not None:
            self._last_pong_ms = int(state.latency_ms)
        if state.error:
            self.server_error = state.error
        if state.state == "Connected":
            if previous_state != "Connected" or self._connected_at is None:
                self._connected_at = time.monotonic()
                self.server_error = ""
            self.subscription_sent = bool(state.subscription_sent)
            if self.subscription_sent and not previous_subscription:
                compression = "compressed" if self._compression_enabled else "uncompressed"
                self._set_status(
                    f"aisstream.io {compression} WebSocket connected; subscription sent. "
                    "Waiting for the first AIS report..."
                )
            else:
                self._emit_diagnostics()
            return
        if state.state == "Reconnecting":
            self.subscription_sent = False
            detail = f": {state.error}" if state.error else ""
            self._set_status(f"aisstream.io connection interrupted; reconnecting automatically{detail}")
            return
        if state.state == "Connecting":
            self.subscription_sent = False
            self._set_status("Connecting to aisstream.io...")
            return
        if state.state == "Disconnected" and self.watchdog_timer.isActive():
            self.subscription_sent = False
            self._set_status("aisstream.io disconnected")
            return
        self._emit_diagnostics()

    def _message(self, message: str) -> None:
        if self.offline:
            return
        self.message_count += 1
        self._last_frame_at = time.monotonic()
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self.invalid_json_count += 1
            self.discarded_count += 1
            self._set_status("AIS frame received, but it is not valid JSON")
            return
        if isinstance(payload, dict) and payload.get("error"):
            self.server_error = str(payload["error"])
            self.discarded_count += 1
            self._set_status(f"AIS provider rejected the subscription: {self.server_error}")
            return
        metadata = payload.get("MetaData") or payload.get("Metadata") or {}
        body = payload.get("Message") or {}
        self._last_message_type = str(payload.get("MessageType") or next(iter(body), "unknown"))
        if self._last_message_type == "SubscriptionConfirmation":
            confirmation = body.get("SubscriptionConfirmation", body)
            if not isinstance(confirmation, dict):
                confirmation = {}
            compression = confirmation.get("CompressionEnabled", payload.get("CompressionEnabled"))
            if compression is not None:
                self._compression_enabled = self._boolean(compression)
            error = confirmation.get("Error") or confirmation.get("error")
            success = confirmation.get("Success", not bool(error))
            self.subscription_sent = bool(success)
            if error or success is False:
                self.server_error = str(error or "subscription rejected")
                self._set_status(f"AIS provider rejected the subscription: {self.server_error}")
            else:
                self.server_error = ""
                compression_text = "enabled" if self._compression_enabled else "not confirmed"
                self._set_status(
                    f"aisstream.io subscription confirmed; transport compression {compression_text}. "
                    "Waiting for AIS reports..."
                )
            return
        report = next((body.get(key) for key in (
            "PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport",
        ) if isinstance(body.get(key), dict)), {})
        static_report = next((body.get(key) for key in (
            "ShipStaticData", "StaticDataReport",
        ) if isinstance(body.get(key), dict)), {})
        raw_mmsi = metadata.get(
            "MMSI",
            report.get("UserID", static_report.get("UserID")),
        )
        try:
            mmsi = int(raw_mmsi)
        except (TypeError, ValueError):
            self.discarded_count += 1
            self._set_status("AIS report received without a valid MMSI")
            return
        if static_report:
            static_values = self._aisstream_static_values(static_report, metadata)
            self._static_metadata[mmsi] = {
                **self._static_metadata.get(mmsi, {}),
                **{key: value for key, value in static_values.items() if value not in (None, "")},
            }
            existing = self.repository.target(mmsi)
            if existing is not None:
                self.repository.ingest_live(
                    (replace(existing, **self._static_metadata[mmsi]),),
                    motion_update=False,
                )
            self._last_target_at = time.monotonic()
            self.parsed_count += 1
            self._set_status(
                f"Live AIS active: {self.parsed_count} valid reports, "
                f"{len(self.repository.all_targets())} current targets"
            )
            if not report:
                return
        latitude = self._number(metadata, report, keys=("latitude", "Latitude", "lat"))
        longitude = self._number(metadata, report, keys=("longitude", "Longitude", "lon"))
        if latitude is None or longitude is None:
            self.discarded_count += 1
            self._set_status(f"AIS frames are arriving, but no supported position was found ({self._last_message_type})")
            return
        if not (-90.0 <= float(latitude) <= 90.0 and -180.0 <= float(longitude) <= 180.0):
            self.discarded_count += 1
            self._set_status("AIS position received with invalid coordinates")
            return
        cog = self._number(report, metadata, keys=("Cog", "COG", "CourseOverGround")) or 0.0
        sog = self._number(report, metadata, keys=("Sog", "SOG", "SpeedOverGround")) or 0.0
        heading = self._number(report, metadata, keys=("TrueHeading", "Heading", "HDG"))
        static_values = dict(self._static_metadata.get(mmsi, {}))
        static_values.setdefault("name", str(metadata.get("ShipName") or "").strip())
        static_values.setdefault("callsign", str(metadata.get("CallSign") or "").strip())
        target = AISTarget(
            mmsi=mmsi,
            latitude_deg=float(latitude),
            longitude_deg=float(longitude),
            sog_kn=float(sog),
            cog_deg=float(cog) % 360.0,
            heading_deg=float(cog if heading is None or heading > 360.0 else heading) % 360.0,
            **static_values,
            last_update_simulation_s=self.snapshot().simulation_time_s,
        )
        self.repository.ingest_live((target,))
        self._last_seen[mmsi] = time.monotonic()
        self._last_target_at = self._last_seen[mmsi]
        self.parsed_count += 1
        self._set_status(
            f"Live AIS active: {self.parsed_count} valid reports, "
            f"{len(self.repository.all_targets())} current targets"
        )

    def _target_from_barentswatch(self, payload) -> AISTarget | None:
        if not isinstance(payload, dict):
            return None
        try:
            mmsi = int(payload.get("mmsi"))
            latitude = float(payload.get("latitude"))
            longitude = float(payload.get("longitude"))
        except (TypeError, ValueError):
            return None
        cog = self._number(payload, keys=("courseOverGround",)) or 0.0
        sog = self._number(payload, keys=("speedOverGround",)) or 0.0
        heading = self._number(payload, keys=("trueHeading",))
        draught = self._number(payload, keys=("draught",))
        return AISTarget(
            mmsi=mmsi,
            latitude_deg=latitude,
            longitude_deg=longitude,
            sog_kn=sog,
            cog_deg=cog % 360.0,
            heading_deg=(cog if heading is None or heading > 360.0 else heading) % 360.0,
            name=str(payload.get("name") or "").strip(),
            callsign=str(payload.get("callSign") or "").strip(),
            nav_status=str(payload.get("navigationalStatus") or ""),
            ship_type=str(payload.get("shipType") or ""),
            length_m=self._number(payload, keys=("shipLength",)),
            beam_m=self._number(payload, keys=("shipWidth",)),
            draught_m=None if draught is None else draught / 10.0,
            rot_deg_min=self._number(payload, keys=("rateOfTurn",)),
            last_update_simulation_s=self.snapshot().simulation_time_s,
        )

    def _aisstream_static_values(self, report: dict, metadata: dict) -> dict:
        report_a = report.get("ReportA") if isinstance(report.get("ReportA"), dict) else {}
        report_b = report.get("ReportB") if isinstance(report.get("ReportB"), dict) else {}
        raw_dimension = report.get("Dimension", report_b.get("Dimension"))
        dimension = raw_dimension if isinstance(raw_dimension, dict) else {}
        eta = report.get("Eta") if isinstance(report.get("Eta"), dict) else {}
        length = self._number(dimension, keys=("A", "ToBow"))
        stern = self._number(dimension, keys=("B", "ToStern"))
        beam_port = self._number(dimension, keys=("C", "ToPort"))
        beam_starboard = self._number(dimension, keys=("D", "ToStarboard"))
        draught = self._number(report, keys=("MaximumStaticDraught", "Draught"))
        if draught is not None and draught > 25.5:
            draught /= 10.0
        return {
            "name": str(report.get("Name") or report_a.get("Name") or metadata.get("ShipName") or "").strip(),
            "callsign": str(report.get("CallSign") or report_b.get("CallSign") or metadata.get("CallSign") or "").strip(),
            "ship_type": str(report.get("Type") or report_b.get("ShipType") or "").strip(),
            "length_m": None if length is None and stern is None else float(length or 0.0) + float(stern or 0.0),
            "beam_m": (
                None
                if beam_port is None and beam_starboard is None
                else float(beam_port or 0.0) + float(beam_starboard or 0.0)
            ),
            "draught_m": draught,
            "imo_number": self._integer(report.get("ImoNumber") or report.get("IMO")),
            "destination": str(report.get("Destination") or "").strip(),
            "eta_month": self._integer(eta.get("Month", report.get("EtaMonth"))),
            "eta_day": self._integer(eta.get("Day", report.get("EtaDay"))),
            "eta_hour": self._integer(eta.get("Hour", report.get("EtaHour"))),
            "eta_minute": self._integer(eta.get("Minute", report.get("EtaMinute"))),
        }

    @staticmethod
    def _integer(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _target_in_scope(self, target: AISTarget) -> bool:
        if self.worldwide_debug:
            return True
        own_ship = self.snapshot()
        phi1 = math.radians(own_ship.latitude_deg)
        phi2 = math.radians(target.latitude_deg)
        dphi = phi2 - phi1
        dlambda = math.radians(target.longitude_deg - own_ship.longitude_deg)
        haversine = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
        distance_nm = 3440.065 * 2.0 * math.atan2(math.sqrt(haversine), math.sqrt(max(0.0, 1.0 - haversine)))
        return distance_nm <= max(1.0, self.subscription_range_nm)

    def _watchdog(self) -> None:
        self._schedule_scope_refresh_if_needed()
        if self.provider != self.AISSTREAM:
            now = time.monotonic()
            connected_for = 0.0 if self._connected_at is None else now - self._connected_at
            if self._connected_at is None:
                self._connected_at = now
            if self.message_count == 0 and connected_for >= self._NO_DATA_WARNING_S:
                self._set_status(
                    f"{self.provider_display_name()} is reachable/starting, but no AIS reports have been received "
                    f"for {connected_for:.0f} s. Check credentials, provider status and the selected geographic scope."
                )
            else:
                self._emit_diagnostics()
            return
        if self._transport_state != "Connected":
            self._emit_diagnostics()
            return
        now = time.monotonic()
        if self.server_error:
            self._emit_diagnostics()
            return
        connected_for = 0.0 if self._connected_at is None else now - self._connected_at
        if self.message_count == 0 and connected_for >= self._NO_DATA_WARNING_S:
            if self.worldwide_debug and self._last_pong_ms is not None:
                self._set_status(
                    f"AIS provider is reachable ({self._last_pong_ms} ms), but delivered no data frames for "
                    f"{connected_for:.0f} s on a worldwide subscription. This indicates an upstream stream/data outage."
                )
            else:
                self._set_status(
                    f"No AIS data frames received for {connected_for:.0f} s. "
                    "The provider may be unavailable, the API key may not be accepted, or the selected area may have no traffic."
                )
        else:
            self._emit_diagnostics()

    @staticmethod
    def _number(*sources, keys) -> float | None:
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                try:
                    value = source.get(key)
                    if value is not None:
                        return float(value)
                except (TypeError, ValueError):
                    pass
        return None

    @staticmethod
    def _boolean(value) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
        return bool(value)

    def _prune(self) -> None:
        cutoff = time.monotonic() - self._TARGET_RETENTION_S
        keep = [target for target in self.repository.all_targets() if self._last_seen.get(target.mmsi, 0.0) >= cutoff]
        if self.repository.mode == "live":
            self.repository.ingest_live(keep, replace_all=True, motion_update=False)

    def _subscription_boxes(self, own_ship) -> list:
        if self.worldwide_debug:
            return [[[-90.0, -180.0], [90.0, 180.0]]]
        radius = max(1.0, self.subscription_range_nm)
        lat_span = min(85.0, radius / 60.0)
        lon_span = min(179.0, radius / (60.0 * max(0.2, math.cos(math.radians(own_ship.latitude_deg)))))
        return [[
            [max(-90.0, own_ship.latitude_deg - lat_span), max(-180.0, own_ship.longitude_deg - lon_span)],
            [min(90.0, own_ship.latitude_deg + lat_span), min(180.0, own_ship.longitude_deg + lon_span)],
        ]]

    def _schedule_scope_refresh_if_needed(self) -> None:
        if self.repository.mode != "live" or self.worldwide_debug or self._scope_center is None:
            return
        own_ship = self.snapshot()
        distance_nm = self._distance_nm(
            self._scope_center[0],
            self._scope_center[1],
            own_ship.latitude_deg,
            own_ship.longitude_deg,
        )
        threshold_nm = max(1.0, min(25.0, self.subscription_range_nm * 0.2))
        if distance_nm >= threshold_nm and not self.scope_refresh_timer.isActive():
            self.scope_refresh_timer.start()

    def _refresh_scope_now(self) -> None:
        if self.offline or self.repository.mode != "live" or self.worldwide_debug:
            return
        own_ship = self.snapshot()
        self._scope_center = (own_ship.latitude_deg, own_ship.longitude_deg)
        retained = tuple(target for target in self.repository.all_targets() if self._target_in_scope(target))
        self.repository.ingest_live(retained, replace_all=True, motion_update=False)
        if self.provider == self.BARENTSWATCH:
            self._emit_diagnostics()
            return
        if self._transport_state not in ("Connected", "Connecting", "Reconnecting"):
            return
        self._set_status("Own-ship position changed; renewing the aisstream.io area subscription...")
        self.subscription_sent = False
        self._aisstream.update_subscription(self._aisstream_subscription())

    @staticmethod
    def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = phi2 - phi1
        dlambda = math.radians(lon2 - lon1)
        value = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
        return 3440.065 * 2.0 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1.0 - value)))

    def _set_status(self, value: str) -> None:
        self.status = value
        self.status_changed.emit(value)
        self._emit_diagnostics()

    def diagnostics(self) -> dict[str, object]:
        now = time.monotonic()
        live_report = self.repository.live_report_diagnostics()
        return {
            "provider": self.provider_display_name(),
            "socket": self._transport_state,
            "subscription": "Sent" if self.subscription_sent else "Not sent",
            "compression": (
                "N/A"
                if self.provider != self.AISSTREAM
                else
                "Enabled"
                if self._compression_enabled is True
                else "Disabled"
                if self._compression_enabled is False
                else "Unknown"
            ),
            "frames": self.message_count,
            "valid_reports": self.parsed_count,
            "discarded_frames": self.discarded_count,
            "targets": len(self.repository.all_targets()),
            "last_frame_age_s": None if self._last_frame_at is None else max(0.0, now - self._last_frame_at),
            "last_target_age_s": None if self._last_target_at is None else max(0.0, now - self._last_target_at),
            "ping_ms": self._last_pong_ms,
            "message_type": self._last_message_type or "--",
            "server_error": self.server_error,
            "scope": self._scope_description(),
            "dead_reckoning_age_s": self.repository.maximum_live_prediction_age_s(),
            **live_report,
            "scope_center": self._scope_center,
        }

    def provider_display_name(self) -> str:
        return {
            self.AISSTREAM: "aisstream.io",
            self.BARENTSWATCH: "Kystverket / BarentsWatch",
        }.get(self.provider, self.provider)

    def _scope_description(self) -> str:
        if self.provider == self.BARENTSWATCH:
            return (
                "All Norwegian coverage"
                if self.worldwide_debug
                else f"{self.subscription_range_nm:.0f} NM local filter around own ship"
            )
        return (
            "Worldwide subscription"
            if self.worldwide_debug
            else f"{self.subscription_range_nm:.0f} NM subscription around own ship"
        )

    def _emit_diagnostics(self) -> None:
        self.diagnostics_changed.emit(self.diagnostics())
