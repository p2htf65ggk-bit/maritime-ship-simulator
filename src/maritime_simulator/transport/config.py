# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
import ipaddress

from maritime_simulator.domain.enums import UdpMode


@dataclass(frozen=True, slots=True)
class UdpEndpoint:
    host: str
    port: int
    mode: UdpMode = UdpMode.AUTO
    multicast_ttl: int = 1
    interface_ip: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("UDP port must be in [1, 65535]")
        if not 0 <= self.multicast_ttl <= 255:
            raise ValueError("Multicast TTL must be in [0, 255]")
        if not self.host:
            raise ValueError("UDP destination host is required")
        if self.interface_ip:
            _validate_ipv4(self.interface_ip, "UDP interface IP")
        if self.mode is UdpMode.MULTICAST and not _is_multicast(self.host):
            raise ValueError("Explicit multicast mode requires a multicast group address")


@dataclass(frozen=True, slots=True)
class UdpInputEndpoint:
    host: str
    port: int
    multicast_group: str = ""
    interface_ip: str = "0.0.0.0"

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("UDP input port must be in [1, 65535]")
        if self.interface_ip:
            _validate_ipv4(self.interface_ip, "UDP input interface IP")
        if self.multicast_group and not _is_multicast(self.multicast_group):
            raise ValueError("UDP multicast group must be an IPv4 multicast address")


@dataclass(frozen=True, slots=True)
class TcpEndpoint:
    host: str
    port: int
    connect_timeout_s: float = 1.0
    queue_limit: int = 4096
    server: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("TCP port must be in [1, 65535]")
        if not self.host:
            raise ValueError("TCP host or bind address is required")
        if self.connect_timeout_s <= 0.0:
            raise ValueError("TCP connect timeout must be positive")


@dataclass(frozen=True, slots=True)
class TcpInputEndpoint:
    host: str
    port: int
    server: bool = False
    connect_timeout_s: float = 1.0

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("TCP input port must be in [1, 65535]")


@dataclass(frozen=True, slots=True)
class SerialEndpoint:
    device: str
    baud: int = 4800
    parity: str = "N"
    stop_bits: int = 1
    queue_limit: int = 4096

    def __post_init__(self) -> None:
        if not self.device:
            raise ValueError("Serial device is required")
        if self.baud <= 0:
            raise ValueError("Serial baud rate must be positive")
        if self.parity not in {"N", "E", "O"}:
            raise ValueError("Serial parity must be N, E or O")
        if self.stop_bits not in {1, 2}:
            raise ValueError("Serial stop bits must be 1 or 2")


Endpoint = UdpEndpoint | TcpEndpoint | SerialEndpoint
InputEndpoint = UdpInputEndpoint | TcpInputEndpoint | SerialEndpoint


def resolve_udp_mode(endpoint: UdpEndpoint) -> UdpMode:
    if endpoint.mode is not UdpMode.AUTO:
        return endpoint.mode
    try:
        address = ipaddress.ip_address(endpoint.host)
    except ValueError:
        return UdpMode.UNICAST
    if address.is_multicast:
        return UdpMode.MULTICAST
    if endpoint.host == "255.255.255.255" or (
        isinstance(address, ipaddress.IPv4Address) and endpoint.host.endswith(".255")
    ):
        return UdpMode.BROADCAST
    return UdpMode.UNICAST


def _validate_ipv4(value: str, label: str) -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if not isinstance(address, ipaddress.IPv4Address):
        raise ValueError(f"{label} must be IPv4")


def _is_multicast(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and address.is_multicast
