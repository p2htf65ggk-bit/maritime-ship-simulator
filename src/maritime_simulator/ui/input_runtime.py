# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path
import sys
from threading import RLock
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer

from maritime_simulator.application.service import SimulatorService
from maritime_simulator.domain.commands import SetExternalRudderInputType, SetExternalActuatorPolicy, ExternalActuatorOrder, ExternalRudderOrder, ExternalRudderResponse
from maritime_simulator.domain.external_control import actuator_sources, ACTUATOR_PROTOCOLS, RUDDER_SOURCES, input_loss_mode
from maritime_simulator.protocols.external_control import decode_actuator_percent, validate_xdr_filter, has_empty_actuator_value, decode_xdr_rudder, validate_rudder_xdr_filter
from maritime_simulator.domain.enums import ExternalRudderControlPolicy
from maritime_simulator.protocols.iec61162 import IEC61162Codec, IEC61162Error
from maritime_simulator.transport.config import SerialEndpoint, TcpInputEndpoint, UdpInputEndpoint
from maritime_simulator.transport.input_manager import (
    IECInputManager,
    InputSourceStatus,
    RORInputBridge,
    RudderInputDetection,
)


@dataclass(slots=True)
class RudderEndpointSettings:
    transport: str = "UDP"
    host: str = "0.0.0.0"
    port: int = 4096
    multicast_group: str = ""
    interface_ip: str = "0.0.0.0"
    tcp_server: bool = True
    serial_device: str = ""
    baud: int = 4800
    parity: str = "N"
    stop_bits: int = 1


@dataclass(slots=True)
class ActuatorInputSettings:
    policy: str = "MANUAL"
    primary: RudderEndpointSettings = field(default_factory=lambda: RudderEndpointSettings(port=4098))
    xdr: RudderEndpointSettings = field(default_factory=lambda: RudderEndpointSettings(port=4099))
    number: int = 1
    xdr_type: str = "A"
    xdr_unit: str = "P"
    xdr_id: str = "BowThrust#0"
    on_loss: str = "MANUAL"


@dataclass(slots=True)
class RudderXdrSettings:
    endpoint: RudderEndpointSettings = field(default_factory=lambda: RudderEndpointSettings(port=4102))
    xdr_type: str = "A"
    xdr_unit: str = "D"
    xdr_id: str = "RudderOrder#0"
    port_id: str = ""


@dataclass(slots=True)
class RorInputSettings:
    input_type: str = "ROR"
    ror: RudderEndpointSettings = field(default_factory=RudderEndpointSettings)
    rsa: RudderEndpointSettings = field(
        default_factory=lambda: RudderEndpointSettings(port=4097)
    )
    bow_thruster: ActuatorInputSettings = field(default_factory=ActuatorInputSettings)
    engine: ActuatorInputSettings = field(default_factory=lambda: ActuatorInputSettings(
        primary=RudderEndpointSettings(port=4100), xdr=RudderEndpointSettings(port=4101), xdr_id="EngTele#0"))
    on_loss: str = "ZERO"
    xdr_order: RudderXdrSettings = field(default_factory=RudderXdrSettings)
    xdr_response: RudderXdrSettings = field(default_factory=lambda: RudderXdrSettings(
        endpoint=RudderEndpointSettings(port=4103), xdr_id="Rudder#0"))


@dataclass(frozen=True, slots=True)
class InputPortEvent:
    sequence: int
    monotonic_s: float
    source: str
    formatter: str
    topology: str
    accepted_for_control: bool
    sentence: str


@dataclass(frozen=True, slots=True)
class DetectedRudderInput:
    input_type: str
    topology: str
    count: int
    last_seen_age_s: float


@dataclass(frozen=True, slots=True)
class RudderChannelStatus:
    input_type: str
    side: str
    last_seen_age_s: float | None
    state: str


@dataclass(frozen=True, slots=True)
class ActuatorChannelStatus:
    protocol: str
    last_seen_age_s: float | None
    state: str


def _signal_age_state(received_at, empty_at, now):
    age = None if received_at is None else max(0.0, now - received_at)
    empty_age = None if empty_at is None else max(0.0, now - empty_at)
    if empty_age is not None and empty_age <= 5.0 and (received_at is None or empty_at >= received_at):
        return age, "empty"
    if age is None or age > 30.0:
        return age, "alarm"
    return age, "delayed" if age > 5.0 else "ok"


class InputRuntime(QObject):
    CHANNELS = tuple((source, side) for source in RUDDER_SOURCES for side in ("PORT", "STBD"))

    def __init__(
        self,
        simulator: SimulatorService,
        path: Path,
        parent=None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        super().__init__(parent)
        self.simulator = simulator
        self.path = path
        self._monotonic = monotonic
        self.settings = self._load()
        self.simulator.submit(SetExternalRudderInputType(self.settings.input_type, on_loss=self.settings.on_loss))
        self._submit_actuator_policies()
        self._manager: IECInputManager | None = None
        self._closed = False
        self._errors: list[str] = []
        self._bridge: RORInputBridge | None = None
        self._event_lock = RLock()
        self._events: deque[InputPortEvent] = deque(maxlen=2500)
        self._event_sequence = 0
        self._detections: dict[str, tuple[str, int, float]] = {}
        self._channel_last_seen: dict[tuple[str, str], float] = {}
        self._channel_empty_seen: dict[tuple[str, str], float] = {}
        self._actuator_last_seen: dict[tuple[str, str], float] = {}
        self._actuator_empty_seen: dict[tuple[str, str], float] = {}
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(2000)
        self._health_timer.timeout.connect(self._recover_listener)
        self._health_timer.start()
        QTimer.singleShot(0, self.ensure_started)

    def is_running(self) -> bool:
        return self._manager is not None

    def is_listening(self) -> bool:
        statuses = self.statuses()
        return bool(statuses) and all(status.running for status in statuses)

    def update(self, **changes) -> None:
        endpoint_fields = set(RudderEndpointSettings.__dataclass_fields__)
        allowed = {"input_type", "on_loss", "ror", "rsa", "bow_thruster", "engine", "xdr_order", "xdr_response"} | endpoint_fields
        allowed |= {f"ror_{name}" for name in endpoint_fields}
        allowed |= {f"rsa_{name}" for name in endpoint_fields}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown external rudder input fields: {', '.join(sorted(unknown))}")

        previous = self.settings
        on_loss = input_loss_mode(changes.pop("on_loss", previous.on_loss))
        rudder_xdr = {name: self._rudder_xdr_settings(getattr(previous, name), changes.pop(name, None))
                      for name in ("xdr_order", "xdr_response")}
        actuator_changes = {name: changes.pop(name, None) for name in ACTUATOR_PROTOCOLS}
        policy = ExternalRudderControlPolicy.parse(
            changes.pop("input_type", previous.input_type)
        )
        input_type = policy.value
        endpoint_changes: dict[str, dict] = {"ror": {}, "rsa": {}}
        for channel in ("ror", "rsa"):
            raw = changes.pop(channel, None)
            if raw is not None:
                if isinstance(raw, RudderEndpointSettings):
                    endpoint_changes[channel].update(asdict(raw))
                elif isinstance(raw, dict):
                    endpoint_changes[channel].update(raw)
                else:
                    raise ValueError(f"{channel.upper()} input settings must be an object")
            prefix = f"{channel}_"
            for name in tuple(changes):
                if name.startswith(prefix):
                    endpoint_changes[channel][name.removeprefix(prefix)] = changes.pop(name)
        # Backwards compatibility: legacy flat settings update the selected channel.
        for name in tuple(changes):
            selected_channel = "rsa" if policy.preferred_source == "RSA" else "ror"
            endpoint_changes[selected_channel][name] = changes.pop(name)
        for channel, values in endpoint_changes.items():
            invalid = set(values) - endpoint_fields
            if invalid:
                raise ValueError(
                    f"Unknown {channel.upper()} endpoint fields: {', '.join(sorted(invalid))}"
                )
        candidate = RorInputSettings(
            input_type=input_type,
            ror=replace(previous.ror, **endpoint_changes["ror"]),
            rsa=replace(previous.rsa, **endpoint_changes["rsa"]),
            bow_thruster=self._actuator_settings(previous.bow_thruster, actuator_changes["bow_thruster"], "bow_thruster"),
            engine=self._actuator_settings(previous.engine, actuator_changes["engine"], "engine"),
            on_loss=on_loss,
            **rudder_xdr,
        )
        self._validate_rudder_xdr_channels(candidate)
        self.settings = candidate
        try:
            self._endpoint(candidate.ror, "ROR")
            self._endpoint(candidate.rsa, "RSA")
            self._save()
        except Exception:
            self.settings = previous
            raise
        reset = any(getattr(previous, key) != getattr(candidate, key)
                    for key in ("input_type", "on_loss", "ror", "rsa", "xdr_order", "xdr_response"))
        self.simulator.submit(SetExternalRudderInputType(self.settings.input_type, on_loss=self.settings.on_loss, reset=reset))
        self._submit_actuator_policies(previous)

    def _submit_actuator_policies(self, previous=None) -> None:
        for actuator in ACTUATOR_PROTOCOLS:
            settings = getattr(self.settings, actuator)
            reset = previous is not None and getattr(previous, actuator) != settings
            self.simulator.submit(SetExternalActuatorPolicy(actuator, settings.policy, reset=reset, on_loss=settings.on_loss))

    def _actuator_settings(self, previous, changes, actuator):
        values = asdict(changes) if isinstance(changes, ActuatorInputSettings) else dict(changes or {})
        for key in ("primary", "xdr"):
            if key in values:
                raw = values[key]
                values[key] = raw if isinstance(raw, RudderEndpointSettings) else replace(getattr(previous, key), **raw)
        candidate = replace(previous, **values)
        actuator_sources(actuator, candidate.policy)
        input_loss_mode(candidate.on_loss)
        if type(candidate.number) is not int or not 1 <= candidate.number <= 9:
            raise ValueError("Engine/thruster number must be between 1 and 9")
        validate_xdr_filter(candidate.xdr_type, candidate.xdr_unit, candidate.xdr_id)
        for endpoint in (candidate.primary, candidate.xdr):
            self._endpoint(endpoint, actuator)
        return candidate

    def _rudder_xdr_settings(self, previous, changes):
        values = asdict(changes) if isinstance(changes, RudderXdrSettings) else dict(changes or {})
        if "endpoint" in values:
            raw = values["endpoint"]
            values["endpoint"] = raw if isinstance(raw, RudderEndpointSettings) else replace(previous.endpoint, **raw)
        candidate = replace(previous, **values)
        validate_rudder_xdr_filter(candidate.xdr_type, candidate.xdr_unit, candidate.xdr_id, candidate.port_id)
        self._endpoint(candidate.endpoint, "XDR Rudder")
        return candidate

    @staticmethod
    def _validate_rudder_xdr_channels(settings):
        order, response = settings.xdr_order, settings.xdr_response
        if (order.endpoint == response.endpoint and (order.xdr_type, order.xdr_unit) == (response.xdr_type, response.xdr_unit)
            and ({order.xdr_id, order.port_id} - {""}) & ({response.xdr_id, response.port_id} - {""})):
            raise ValueError("Rudder order and response XDR IDs must differ on a shared endpoint")

    def update_endpoint(self, channel: str, **changes) -> None:
        kind = str(channel).upper()
        if kind not in {"ROR", "RSA"}:
            raise ValueError("External rudder channel must be ROR or RSA")
        self.update(**{kind.lower(): changes})

    def start(self) -> None:
        self.stop()
        with self._event_lock:
            self._errors.clear()
            self._events.clear()
            self._detections.clear()
            self._channel_last_seen.clear()
            self._channel_empty_seen.clear()
            self._actuator_last_seen.clear()
            self._actuator_empty_seen.clear()
        self._bridge = RORInputBridge(
            self.simulator,
            on_error=self._record_error,
            input_type=self.settings.input_type,
            monotonic=self._monotonic,
        )
        manager = IECInputManager(self._on_sentence, self._record_error, monotonic=self._monotonic)
        endpoints = {
            "external-ror": self._endpoint(self.settings.ror, "ROR"),
            "external-rsa": self._endpoint(self.settings.rsa, "RSA"),
            "external-xdr-order": self._endpoint(self.settings.xdr_order.endpoint, "XDR Order"),
            "external-xdr-response": self._endpoint(self.settings.xdr_response.endpoint, "XDR Response"),
        }
        for actuator, primary in ACTUATOR_PROTOCOLS.items():
            settings = getattr(self.settings, actuator)
            for name, endpoint in ((primary.lower(), settings.primary), ("xdr", settings.xdr)):
                endpoints[f"external-{actuator}-{name}"] = self._endpoint(endpoint, actuator)
        unique: dict[object, list[str]] = {}
        for source, endpoint in endpoints.items():
            unique.setdefault(endpoint, []).append(source)
        for endpoint, sources in unique.items():
            manager.add_source("+".join(sources), endpoint)
        manager.start()
        self._manager = manager

    def ensure_started(self) -> bool:
        if self._closed:
            return False
        if self.is_listening():
            return True
        if self._manager is not None:
            self.stop()
        try:
            self.start()
        except (OSError, RuntimeError, ValueError) as error:
            self._record_error("external-rudder", str(error))
            return False
        return True

    def restart(self) -> bool:
        self.stop()
        return self.ensure_started()

    def stop(self) -> None:
        if self._manager is not None:
            self._manager.close()
            self._manager = None
        self._bridge = None

    def status(self) -> InputSourceStatus | None:
        statuses = self.statuses()
        return statuses[0] if statuses else None

    def statuses(self) -> tuple[InputSourceStatus, ...]:
        return () if self._manager is None else self._manager.statuses()

    def last_error(self) -> str:
        with self._event_lock:
            return self._errors[-1] if self._errors else ""

    def events(self) -> tuple[InputPortEvent, ...]:
        with self._event_lock:
            return tuple(self._events)

    def clear_events(self) -> None:
        with self._event_lock:
            self._events.clear()

    def detections(self) -> tuple[DetectedRudderInput, ...]:
        now = self._monotonic()
        with self._event_lock:
            return tuple(
                DetectedRudderInput(kind, topology, count, max(0.0, now - last_seen))
                for kind, (topology, count, last_seen) in sorted(self._detections.items())
            )

    def channel_statuses(self) -> tuple[RudderChannelStatus, ...]:
        now = self._monotonic()
        with self._event_lock:
            statuses = []
            for input_type, side in self.CHANNELS:
                received_at = self._channel_last_seen.get((input_type, side))
                empty_at = self._channel_empty_seen.get((input_type, side))
                age, state = _signal_age_state(received_at, empty_at, now)
                statuses.append(RudderChannelStatus(input_type, side, age, state))
            return tuple(statuses)

    def actuator_channel_statuses(self, actuator: str) -> tuple[ActuatorChannelStatus, ...]:
        primary = ACTUATOR_PROTOCOLS[actuator]
        now = self._monotonic()
        with self._event_lock:
            return tuple(ActuatorChannelStatus(protocol, *_signal_age_state(
                self._actuator_last_seen.get((actuator, protocol)),
                self._actuator_empty_seen.get((actuator, protocol)), now))
                for protocol in (primary, "XDR"))

    def endpoint_description(self, channel: str | None = None) -> str:
        kind = str(
            channel
            or ExternalRudderControlPolicy.parse(self.settings.input_type).preferred_source
        ).upper()
        settings = self.settings.rsa if kind == "RSA" else self.settings.ror
        if kind in {"XDR_ORDER", "XDR_RESPONSE"}:
            settings = getattr(self.settings, kind.lower()).endpoint
        return self._describe_endpoint(settings)

    @staticmethod
    def _describe_endpoint(settings) -> str:
        transport = settings.transport.upper()
        if transport == "SERIAL":
            return f"Serial {settings.serial_device} @ {settings.baud} baud"
        role = "listen" if transport == "UDP" or settings.tcp_server else "connect"
        return f"{transport} {role} {settings.host}:{settings.port}"

    def monitor_description(self) -> str:
        parts = [f"{kind}: {self._describe_endpoint(getattr(self.settings, kind.lower()))}" for kind in ("ROR", "RSA")]
        parts.extend(f"{kind}: {self._describe_endpoint(getattr(self.settings, kind.lower()).endpoint)}"
                     for kind in ("XDR_ORDER", "XDR_RESPONSE"))
        for actuator, primary in ACTUATOR_PROTOCOLS.items():
            settings = getattr(self.settings, actuator)
            parts.extend((f"{actuator} {primary}: {self._describe_endpoint(settings.primary)}",
                          f"{actuator} XDR: {self._describe_endpoint(settings.xdr)}"))
        return " | ".join(parts)

    def close(self) -> None:
        self._closed = True
        self._health_timer.stop()
        self.stop()

    def _endpoint(self, settings: RudderEndpointSettings, kind: str):
        if not isinstance(settings.transport, str):
            raise ValueError("Input transport must be UDP, TCP or Serial")
        transport = settings.transport.upper()
        if transport == "UDP":
            return UdpInputEndpoint(
                host=settings.host,
                port=int(settings.port),
                multicast_group=settings.multicast_group,
                interface_ip=settings.interface_ip,
            )
        if transport == "TCP":
            return TcpInputEndpoint(
                host=settings.host,
                port=int(settings.port),
                server=bool(settings.tcp_server),
            )
        if transport == "SERIAL":
            if not settings.serial_device:
                raise ValueError(f"Serial device is required for {kind} input")
            return SerialEndpoint(
                device=settings.serial_device,
                baud=int(settings.baud),
                parity=settings.parity,
                stop_bits=int(settings.stop_bits),
            )
        raise ValueError("External rudder input transport must be UDP, TCP or Serial")

    def _load(self) -> RorInputSettings:
        serial_device = _default_serial_device()
        defaults = RorInputSettings(
            ror=RudderEndpointSettings(serial_device=serial_device),
            rsa=RudderEndpointSettings(port=4097, serial_device=serial_device),
        )
        for actuator in ACTUATOR_PROTOCOLS:
            settings = getattr(defaults, actuator)
            settings.primary.serial_device = settings.xdr.serial_device = serial_device
        defaults.xdr_order.endpoint.serial_device = defaults.xdr_response.endpoint.serial_device = serial_device
        if not self.path.exists():
            return defaults
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            input_type = ExternalRudderControlPolicy.parse(
                raw.get("input_type", "ROR")
            ).value
            if isinstance(raw.get("ror"), dict) or isinstance(raw.get("rsa"), dict):
                ror = replace(defaults.ror, **dict(raw.get("ror", {})))
                rsa = replace(defaults.rsa, **dict(raw.get("rsa", {})))
                self._endpoint(ror, "ROR")
                self._endpoint(rsa, "RSA")
                candidate = RorInputSettings(input_type=input_type, ror=ror, rsa=rsa,
                    bow_thruster=self._actuator_settings(defaults.bow_thruster, raw.get("bow_thruster"), "bow_thruster"),
                    engine=self._actuator_settings(defaults.engine, raw.get("engine"), "engine"),
                    on_loss=input_loss_mode(raw.get("on_loss", defaults.on_loss)),
                    xdr_order=self._rudder_xdr_settings(defaults.xdr_order, raw.get("xdr_order")),
                    xdr_response=self._rudder_xdr_settings(defaults.xdr_response, raw.get("xdr_response")))
                self._validate_rudder_xdr_channels(candidate)
                return candidate
            # Migrate the RC5 flat endpoint into the channel that controlled the model.
            legacy = {
                name: raw[name]
                for name in RudderEndpointSettings.__dataclass_fields__
                if name in raw
            }
            if ExternalRudderControlPolicy.parse(input_type).preferred_source == "RSA":
                return RorInputSettings(input_type=input_type, ror=defaults.ror, rsa=replace(defaults.rsa, **legacy))
            return RorInputSettings(input_type=input_type, ror=replace(defaults.ror, **legacy), rsa=defaults.rsa)
        except (OSError, TypeError, ValueError, AttributeError, json.JSONDecodeError):
            return defaults

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(self.settings), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def _record_error(self, source: str, error: str) -> None:
        with self._event_lock:
            entry = f"{source}: {error}"
            if self._errors and self._errors[-1] == entry:
                return
            self._errors.append(entry)
            del self._errors[:-20]

    def _recover_listener(self) -> None:
        statuses = self.statuses()
        if self._manager is None or any(not status.running for status in statuses):
            self.ensure_started()

    def _on_sentence(self, source: str, sentence: str) -> None:
        decoded = None
        try:
            decoded = IEC61162Codec.decode(sentence)
        except IEC61162Error:
            pass
        matched = self._on_actuator_sentence(source, decoded) if decoded is not None else ()
        rudder_matches = self._on_rudder_xdr(source, decoded) if decoded is not None else ()
        bridge = self._bridge
        native_channel = f"external-{decoded.formatter.lower()}" if decoded is not None else ""
        native_source = native_channel in source.split("+") or not source.startswith("external-")
        detection = bridge.on_sentence(source, sentence) if bridge is not None and native_source else None
        formatter = "---"
        topology = "--"
        accepted = False
        if decoded is not None and decoded.formatter in {"TRC", "PRC", "XDR"}:
            formatter = decoded.formatter
            topology = "/".join(matched) or "--"
            accepted = any(decoded.formatter in actuator_sources(name, getattr(self.settings, name).policy) for name in matched)
            if rudder_matches:
                topology = "/".join((*matched, *rudder_matches))
                policy = ExternalRudderControlPolicy.parse(self.settings.input_type)
                accepted = accepted or any(kind in policy.sources for kind in rudder_matches)
        if isinstance(detection, RudderInputDetection):
            formatter = detection.input_type
            topology = "Dual" if detection.dual_rudder else "Single"
            accepted = detection.accepted_for_control
        now = self._monotonic()
        with self._event_lock:
            if decoded is not None and decoded.formatter in {"ROR", "RSA"}:
                for side, field_index in (("STBD", 0), ("PORT", 2)):
                    field = decoded.fields[field_index].strip() if len(decoded.fields) > field_index else ""
                    if not field:
                        self._channel_empty_seen[(decoded.formatter, side)] = now
            self._event_sequence += 1
            self._events.append(InputPortEvent(
                sequence=self._event_sequence,
                monotonic_s=now,
                source=source,
                formatter=formatter,
                topology=topology,
                accepted_for_control=accepted,
                sentence=sentence,
            ))
            if isinstance(detection, RudderInputDetection):
                previous = self._detections.get(formatter)
                count = 1 if previous is None else previous[1] + 1
                self._detections[formatter] = (topology, count, now)
                for side in detection.available_sides:
                    self._channel_last_seen[(formatter, side)] = now

    def _on_rudder_xdr(self, source, decoded):
        if decoded.formatter != "XDR":
            return ()
        matched = []
        for kind, channel, command_type in (("XDR_ORDER", "external-xdr-order", ExternalRudderOrder),
                                             ("XDR_RESPONSE", "external-xdr-response", ExternalRudderResponse)):
            if channel not in source.split("+"):
                continue
            settings = getattr(self.settings, kind.lower())
            now = self._monotonic()
            fields = decoded.fields
            if len(fields) % 4 == 0:
                with self._event_lock:
                    for index in range(0, len(fields), 4):
                        if (fields[index], fields[index + 2]) != (settings.xdr_type, settings.xdr_unit):
                            continue
                        for side, identifier in (("STBD", settings.xdr_id), ("PORT", settings.port_id)):
                            if identifier and fields[index + 3] == identifier and not fields[index + 1].strip():
                                self._channel_empty_seen[(kind, side)] = now
            try:
                profile = self.simulator.profiles.get(self.simulator.snapshot().vessel_profile_id)
                value = decode_xdr_rudder(decoded, type_code=settings.xdr_type, unit=settings.xdr_unit,
                    identifier=settings.xdr_id, port_id=settings.port_id, max_rudder_deg=profile.max_rudder_deg)
                if value is None:
                    continue
                self.simulator.submit(command_type(value[0], value[1], now, source=source, protocol=kind))
                matched.append(kind)
                topology = "Dual" if value[1] is not None else "Single"
                with self._event_lock:
                    previous = self._detections.get(kind)
                    self._detections[kind] = (topology, 1 if previous is None else previous[1] + 1, now)
                    for side in ("STBD", "PORT") if value[1] is not None else ("STBD",):
                        self._channel_last_seen[(kind, side)] = now
                        self._channel_empty_seen.pop((kind, side), None)
            except (ValueError, TypeError, OverflowError) as error:
                self._record_error(source, str(error))
        return tuple(matched)

    def _on_actuator_sentence(self, source, decoded):
        matched = []
        for actuator, primary in ACTUATOR_PROTOCOLS.items():
            settings = getattr(self.settings, actuator)
            channel = "xdr" if decoded.formatter == "XDR" else primary.lower()
            if f"external-{actuator}-{channel}" not in source.split("+"):
                continue
            key = (actuator, decoded.formatter)
            if has_empty_actuator_value(decoded, actuator=actuator, number=settings.number,
                                       xdr_type=settings.xdr_type, xdr_unit=settings.xdr_unit, xdr_id=settings.xdr_id):
                with self._event_lock:
                    self._actuator_empty_seen[key] = self._monotonic()
            try:
                profile = self.simulator.profiles.get(self.simulator.snapshot().vessel_profile_id)
                value = decode_actuator_percent(decoded, actuator=actuator, number=settings.number,
                    rated_rpm=profile.bow_thruster_rated_rpm, xdr_type=settings.xdr_type,
                    xdr_unit=settings.xdr_unit, xdr_id=settings.xdr_id)
                if value is not None:
                    self.simulator.submit(ExternalActuatorOrder(actuator, decoded.formatter, value, self._monotonic()))
                    matched.append(actuator)
                    with self._event_lock:
                        self._actuator_last_seen[key] = self._monotonic()
                        self._actuator_empty_seen.pop(key, None)
            except (ValueError, TypeError, OverflowError) as error:
                self._record_error(source, str(error))
        return tuple(matched)


def _default_serial_device() -> str:
    if sys.platform == "win32":
        return "COM1"
    if sys.platform == "darwin":
        return "/dev/cu.usbserial"
    return "/dev/ttyUSB0"
