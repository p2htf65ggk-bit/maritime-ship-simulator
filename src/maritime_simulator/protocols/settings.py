# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from maritime_simulator.domain.enums import TransportKind, UdpMode


@dataclass(frozen=True, slots=True)
class ProtocolOutputSettings:
    enabled: bool = True
    transport: TransportKind = TransportKind.UDP
    host: str = "239.192.192.0"
    port: int = 4096
    rate_hz: float = 1.0
    udp_mode: UdpMode = UdpMode.AUTO
    multicast_ttl: int = 1
    interface_ip: str = ""
    serial_device: str = ""
    baud: int = 4800
    parity: str = "N"
    stop_bits: int = 1
    tcp_server: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.transport, TransportKind):
            raise ValueError("transport must be a TransportKind")
        if not isinstance(self.udp_mode, UdpMode):
            raise ValueError("udp_mode must be a UdpMode")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be in [1, 65535]")
        if self.rate_hz <= 0.0:
            raise ValueError("rate_hz must be positive")
        if not 0 <= self.multicast_ttl <= 255:
            raise ValueError("multicast_ttl must be in [0, 255]")
        if self.baud <= 0:
            raise ValueError("baud must be positive")
        if self.parity not in {"N", "E", "O"}:
            raise ValueError("parity must be N, E or O")
        if self.stop_bits not in {1, 2}:
            raise ValueError("stop_bits must be 1 or 2")


class SensorProtocolSettings:
    """Store one independent output configuration per protocol and transport."""

    def __init__(self, protocol_names: Iterable[str], initial: ProtocolOutputSettings | None = None):
        names = tuple(protocol_names)
        base = initial or ProtocolOutputSettings()
        self._children = {
            TransportKind.UDP: {
                name: replace(base, transport=TransportKind.UDP)
                for name in names
            },
            TransportKind.TCP: {
                name: replace(
                    base,
                    enabled=False,
                    transport=TransportKind.TCP,
                    host="127.0.0.1",
                    tcp_server=False,
                )
                for name in names
            },
            TransportKind.SERIAL: {
                name: replace(
                    base,
                    enabled=False,
                    transport=TransportKind.SERIAL,
                    serial_device="",
                )
                for name in names
            },
        }

    def protocols(self) -> tuple[str, ...]:
        return tuple(self._children[TransportKind.UDP])

    def get_protocol(
        self,
        protocol: str,
        output_transport: TransportKind = TransportKind.UDP,
    ) -> ProtocolOutputSettings:
        return self._children[TransportKind(output_transport)][protocol]

    def update_sensor(
        self,
        *,
        output_transport: TransportKind = TransportKind.UDP,
        **changes: Any,
    ) -> None:
        transport = TransportKind(output_transport)
        self._validate_fields(changes)
        self._validate_transport_change(transport, changes)
        updated = {
            name: replace(settings, **changes)
            for name, settings in self._children[transport].items()
        }
        self._children[transport] = updated

    def update_protocol(
        self,
        protocol: str,
        *,
        output_transport: TransportKind = TransportKind.UDP,
        **changes: Any,
    ) -> None:
        transport = TransportKind(output_transport)
        self._validate_fields(changes)
        self._validate_transport_change(transport, changes)
        self._children[transport][protocol] = replace(
            self._children[transport][protocol],
            **changes,
        )

    def aggregate(
        self,
        field_name: str,
        output_transport: TransportKind = TransportKind.UDP,
    ) -> Any | None:
        if field_name not in ProtocolOutputSettings.__dataclass_fields__:
            raise ValueError(f"Unknown output setting: {field_name}")
        values = {
            getattr(item, field_name)
            for item in self._children[TransportKind(output_transport)].values()
        }
        return next(iter(values)) if len(values) == 1 else None

    def configured_outputs(self):
        for transport in TransportKind:
            for protocol, settings in self._children[transport].items():
                yield protocol, transport, settings

    @staticmethod
    def _validate_fields(changes: dict[str, Any]) -> None:
        unknown = set(changes) - set(ProtocolOutputSettings.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown output settings: {', '.join(sorted(unknown))}")

    @staticmethod
    def _validate_transport_change(transport: TransportKind, changes: dict[str, Any]) -> None:
        selected = changes.get("transport", transport)
        if TransportKind(selected) is not transport:
            raise ValueError("An output path cannot change its transport type")
