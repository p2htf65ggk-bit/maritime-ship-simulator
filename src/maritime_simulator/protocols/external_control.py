# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import math
import re

from .iec61162 import DecodedSentence, IEC61162Error


XDR_PERCENT_UNITS = {"A": ("P",), "G": ("P", "")}
XDR_RUDDER_UNITS = {"A": ("D", "P"), "G": ("P", "")}


def validate_xdr_filter(type_code: str, unit: str, identifier: str) -> None:
    if not isinstance(type_code, str) or re.fullmatch(r"[A-Z]", type_code) is None:
        raise ValueError("XDR Type must be one uppercase letter")
    if not isinstance(unit, str) or unit not in XDR_PERCENT_UNITS.get(type_code, ()):
        raise ValueError("External XDR controls support only A/P, G/P or G/(null field) (-100 to +100 percent)")
    _validate_identifier(identifier)


def _validate_identifier(identifier: str) -> None:
    if not isinstance(identifier, str) or re.fullmatch(r"[!-~]{1,32}", identifier) is None or any(c in identifier for c in ",$*!\\"):
        raise ValueError("XDR ID must be 1-32 printable ASCII characters without sentence delimiters")


def validate_rudder_xdr_filter(type_code: str, unit: str, identifier: str, port_id: str = "") -> None:
    if not isinstance(type_code, str) or not isinstance(unit, str) or unit not in XDR_RUDDER_UNITS.get(type_code, ()):
        raise ValueError("Rudder XDR supports A/D (degrees), A/P, G/P or G/(null field) (percent)")
    _validate_identifier(identifier)
    if port_id:
        _validate_identifier(port_id)
        if port_id == identifier:
            raise ValueError("Single/STBD and PORT XDR IDs must be different")
    elif not isinstance(port_id, str):
        raise ValueError("PORT XDR ID must be text or empty for a single rudder")


def decode_xdr_rudder(sentence: DecodedSentence, *, type_code: str, unit: str,
                      identifier: str, port_id: str = "", max_rudder_deg: float) -> tuple[float, float | None] | None:
    validate_rudder_xdr_filter(type_code, unit, identifier, port_id)
    fields = sentence.fields
    if sentence.formatter != "XDR":
        return None
    if not fields or len(fields) % 4:
        raise IEC61162Error("XDR requires complete Type, Value, Unit, ID groups")
    identifiers = (identifier, port_id) if port_id else (identifier,)
    matching = {}
    for index in range(0, len(fields), 4):
        if fields[index] == type_code and fields[index + 2] == unit and fields[index + 3] in identifiers:
            key = fields[index + 3]
            if key in matching:
                raise IEC61162Error("Ambiguous duplicate XDR rudder ID")
            matching[key] = _number(fields[index + 1])
    if not matching:
        return None
    if len(matching) != len(identifiers):
        raise IEC61162Error("Dual rudder XDR requires both configured IDs in the same sentence")
    limit = max_rudder_deg if unit == "D" else 100.0
    if not math.isfinite(max_rudder_deg) or max_rudder_deg <= 0:
        raise IEC61162Error("Rudder angle range must be finite and positive")
    if any(abs(value) > limit for value in matching.values()):
        raise IEC61162Error("XDR rudder value exceeds the configured angle/percent range")
    scale = 1.0 if unit == "D" else max_rudder_deg / 100.0
    return matching[identifier] * scale, matching[port_id] * scale if port_id else None


def _number(raw: str) -> float:
    try:
        number = float(raw)
    except (TypeError, ValueError) as error:
        raise IEC61162Error("Missing or invalid control value") from error
    if not math.isfinite(number):
        raise IEC61162Error("Control value must be finite")
    return number


def has_empty_actuator_value(sentence: DecodedSentence, *, actuator: str, number: int,
                             xdr_type: str, xdr_unit: str, xdr_id: str) -> bool:
    fields = sentence.fields
    if sentence.formatter == "XDR" and fields and len(fields) % 4 == 0:
        return any((fields[i], fields[i + 2], fields[i + 3]) == (xdr_type, xdr_unit, xdr_id)
                   and not fields[i + 1].strip() for i in range(0, len(fields), 4))
    if len(fields) == 8:
        if actuator == "bow_thruster" and sentence.formatter == "TRC":
            return fields[0] == str(number) and not fields[1].strip()
        if actuator == "engine" and sentence.formatter == "PRC":
            return fields[7] == str(number) and not fields[0].strip()
    return False


def decode_actuator_percent(sentence: DecodedSentence, *, actuator: str, number: int = 1,
                            rated_rpm: float = 300.0, xdr_type: str = "A", xdr_unit: str = "P",
                            xdr_id: str = "BowThrust#0") -> float | None:
    fields = sentence.fields
    if sentence.formatter == "XDR":
        try:
            validate_xdr_filter(xdr_type, xdr_unit, xdr_id)
        except ValueError as error:
            raise IEC61162Error(str(error)) from error
        if not fields or len(fields) % 4:
            raise IEC61162Error("XDR requires complete Type, Value, Unit, ID groups")
        values = [fields[index + 1] for index in range(0, len(fields), 4)
                  if (fields[index], fields[index + 2], fields[index + 3]) == (xdr_type, xdr_unit, xdr_id)]
        if not values:
            return None
        if len(values) != 1:
            raise IEC61162Error("Ambiguous duplicate XDR control ID")
        value = _number(values[0])
    elif sentence.formatter == "TRC" and actuator == "bow_thruster":
        if len(fields) != 8:
            raise IEC61162Error("TRC requires eight fields")
        if fields[0] != str(number):
            return None
        if fields[2] not in {"P", "R"} or fields[7] not in {"C", "R"}:
            raise IEC61162Error("TRC requires valid RPM mode P/R and sentence flag C/R")
        value = _number(fields[1])
        if fields[2] == "R":
            value = value / rated_rpm * 100.0
    elif sentence.formatter == "PRC" and actuator == "engine":
        if len(fields) != 8:
            raise IEC61162Error("PRC requires eight fields")
        if fields[7] != str(number):
            return None
        if fields[1] != "A":
            raise IEC61162Error("PRC lever demand is invalid")
        value = _number(fields[0])
    else:
        return None
    if not -100.0 <= value <= 100.0:
        raise IEC61162Error("Control value must be within -100 to +100 percent")
    return value
