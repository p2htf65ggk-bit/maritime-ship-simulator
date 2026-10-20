# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from enum import Enum

from .external_control import RUDDER_SOURCES, ordered_sources


class ControlMode(str, Enum):
    SENSOR = "sensor"
    SHIP = "ship"
    TRACK = "track"
    EXTERNAL_RUDDER = "external_rudder"
    HEADING_RESERVED = "heading_reserved"


class CommandSource(str, Enum):
    USER = "user"
    TRACK_CONTROLLER = "track_controller"
    HEADING_CONTROLLER = "heading_controller"
    EXTERNAL_ROR = "external_ror"
    EXTERNAL_RSA = "external_rsa"
    EXTERNAL_PROPULSION = "external_propulsion"
    ROUTE_END = "route_end"
    ROUTE_PLAN = "route_plan"
    SAFETY = "safety"


class InputValidity(str, Enum):
    UNAVAILABLE = "unavailable"
    VALID = "valid"
    INVALID = "invalid"
    STALE = "stale"


class ExternalRudderControlPolicy(str, Enum):
    MANUAL = "MANUAL"
    ROR = "ROR"
    RSA = "RSA"
    PREFER_RSA = "PRIO_RSA_ROR"
    PREFER_ROR = "PRIO_ROR_RSA"
    XDR_ORDER = "XDR_ORDER"
    XDR_RESPONSE = "XDR_RESPONSE"
    ROR_XDR_ORDER = "PRIO_ROR_XDR_ORDER"
    ROR_XDR_RESPONSE = "PRIO_ROR_XDR_RESPONSE"
    RSA_XDR_ORDER = "PRIO_RSA_XDR_ORDER"
    RSA_XDR_RESPONSE = "PRIO_RSA_XDR_RESPONSE"
    XDR_ORDER_ROR = "PRIO_XDR_ORDER_ROR"
    XDR_ORDER_RSA = "PRIO_XDR_ORDER_RSA"
    XDR_ORDER_RESPONSE = "PRIO_XDR_ORDER_XDR_RESPONSE"
    XDR_RESPONSE_ROR = "PRIO_XDR_RESPONSE_ROR"
    XDR_RESPONSE_RSA = "PRIO_XDR_RESPONSE_RSA"
    XDR_RESPONSE_ORDER = "PRIO_XDR_RESPONSE_XDR_ORDER"

    @property
    def sources(self) -> tuple[str, ...]:
        return ordered_sources(self.value, RUDDER_SOURCES)

    @property
    def preferred_source(self) -> str:
        return self.sources[0] if self.sources else "MANUAL"

    @property
    def allows_fallback(self) -> bool:
        return len(self.sources) == 2

    @classmethod
    def parse(cls, value: object) -> "ExternalRudderControlPolicy":
        normalized = str(value).strip().upper().replace(" ", "_")
        aliases = {
            "PREFER_RSA": cls.PREFER_RSA,
            "PRIORITY_RSA_ROR": cls.PREFER_RSA,
            "PREFER_ROR": cls.PREFER_ROR,
            "PRIORITY_ROR_RSA": cls.PREFER_ROR,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError as error:
            raise ValueError(
                "External rudder control source must be Manual, ROR, RSA, "
                "XDR_ORDER, XDR_RESPONSE or a valid main/fallback pair"
            ) from error


class TransportKind(str, Enum):
    UDP = "udp"
    TCP = "tcp"
    SERIAL = "serial"


class UdpMode(str, Enum):
    AUTO = "auto"
    UNICAST = "unicast"
    BROADCAST = "broadcast"
    MULTICAST = "multicast"
