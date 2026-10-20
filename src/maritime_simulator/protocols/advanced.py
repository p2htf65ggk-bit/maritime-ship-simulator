# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
import json
import math
import re
from types import MappingProxyType
from typing import Mapping

from .iec61162 import IEC61162Codec, IEC61162Error, _format_angle, _format_lat, _format_lon
from .sensors import SensorSnapshot


@dataclass(frozen=True, slots=True)
class AdvancedField:
    name: str
    field_type: str
    description: str
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True, slots=True)
class AdvancedProtocol:
    formatter: str
    name: str
    fields: tuple[AdvancedField, ...]


@dataclass(frozen=True, slots=True)
class XDRPreset:
    key: str
    group: str
    label: str
    transducer_type: str
    unit: str
    identifier: str
    model_linked: bool = False


_XDR_AUTO_IDENTIFIERS = {
    ("W", "AIR"),
    ("A", "HEEL"),
    ("A", "PITCH"),
    ("A", "ROLL"),
    ("A", "RUDDER"),
    ("C", "AIR"),
    ("C", "WATER"),
    ("H", "AIR"),
    ("P", "BARO"),
}


def _build_xdr_presets() -> tuple[XDRPreset, ...]:
    groups = (
        ("Electrical current", "I", "A", ("Battery", "Alternator", "Converter", "Inverter", "SolarCell", "WindGen")),
        ("Dew point", "W", "C", ("Air",)),
        ("Angular displacement", "A", "D", ("Engine", "Trim", "Heel", "Rudder", "Leeway", "Yaw", "Pitch", "Roll")),
        ("Linear displacement", "D", "M", ("Door", "Window", "Lift")),
        ("Flow rate", "R", "L", ("Fuel", "FreshWater", "WasteWater", "LiveWater", "Oil", "BlackWater", "BrineWater")),
        ("Fluid level", "E", "P", ("Fuel", "FreshWater", "WasteWater", "LiveWellWater", "Oil", "BlackWater", "BrineWater")),
        ("Force", "N", "N", ("Pulley", "Strain")),
        ("Frequency", "F", "H", ("Power",)),
        ("Absolute humidity", "B", "K", ("Air",)),
        ("Relative humidity", "H", "P", ("Air",)),
        ("Pressure", "P", "B", ("Baro", "Engine", "EngineBoost", "EngineCool", "EngineOil", "Fuel", "TransOil")),
        ("Salinity", "L", "S", ("Water",)),
        ("Switch / valve", "S", "b", ("Switch", "Valve")),
        ("Tachometer", "T", "R", ("Engine", "Prop")),
        ("Temperature", "C", "C", ("Air", "Coolant", "Engine", "EngineOil", "TransOil", "Water")),
        ("Voltage", "U", "V", ("Battery", "Alternator", "Converter", "Inverter", "SolarCell", "WindGen")),
        ("Volume", "V", "M", ("Air", "Fuel", "FreshWater", "WasteWater", "LiveWater", "Oil", "BlackWater", "BrineWater")),
    )
    presets = []
    for group, transducer_type, unit, identifiers in groups:
        for identifier in identifiers:
            key = f"{transducer_type}:{unit}:{identifier.lower()}"
            presets.append(
                XDRPreset(
                    key=key,
                    group=group,
                    label=f"{identifier}#X ({unit or 'null'}; default #0)",
                    transducer_type=transducer_type,
                    unit=unit,
                    identifier=f"{identifier}#0",
                    model_linked=(transducer_type, identifier.upper()) in _XDR_AUTO_IDENTIFIERS,
                )
            )
    return tuple(presets)


XDR_PRESETS = _build_xdr_presets()


def load_advanced_catalog() -> Mapping[str, AdvancedProtocol]:
    resource = files("maritime_simulator.resources").joinpath("advanced_protocol_catalog.json")
    raw = json.loads(resource.read_text(encoding="utf-8"))
    catalog = {}
    for formatter, definition in raw.items():
        catalog[formatter] = AdvancedProtocol(
            formatter=formatter,
            name=definition["name"],
            fields=tuple(
                AdvancedField(
                    name=item["name"],
                    field_type=item["type"],
                    description=item["description"],
                    minimum=item.get("minimum"),
                    maximum=item.get("maximum"),
                )
                for item in definition["fields"]
            ),
        )
    return MappingProxyType(catalog)


ADVANCED_PROTOCOLS = load_advanced_catalog()


def supports_auto(
    protocol: str,
    field_name: str,
    sensor: SensorSnapshot,
    context: Mapping[str, object] | None = None,
) -> bool:
    return _auto_value(
        protocol.upper(), field_name, sensor, datetime.now(timezone.utc), context
    ) is not None


def encode_advanced_protocol(
    talker: str,
    protocol: str,
    values: Mapping[str, object],
    *,
    sensor: SensorSnapshot | None = None,
    now: datetime | None = None,
) -> str:
    formatter = protocol.upper()
    definition = ADVANCED_PROTOCOLS.get(formatter)
    if definition is None:
        raise IEC61162Error(f"Unsupported advanced protocol: {protocol}")
    normalized_talker = talker.upper()
    if not re.fullmatch(r"[A-Z0-9]{2}", normalized_talker):
        raise IEC61162Error("Talker ID must contain exactly two uppercase letters/digits")
    unknown = set(values) - {field.name for field in definition.fields}
    if unknown:
        raise IEC61162Error(f"Unknown {formatter} fields: {', '.join(sorted(unknown))}")

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    encoded = []
    for field in definition.fields:
        raw = values.get(field.name, "")
        if isinstance(raw, str) and raw.strip().upper() == "AUTO":
            if sensor is None:
                raise IEC61162Error(f"AUTO for {formatter}.{field.name} requires a simulation snapshot")
            resolved = _auto_value(formatter, field.name, sensor, current, values)
            if resolved is None:
                raise IEC61162Error(f"AUTO is not available for {formatter}.{field.name}")
            raw = resolved
        encoded.append(_validate_field(field, raw))

    _validate_protocol_rules(formatter, definition, encoded)
    return IEC61162Codec.frame(f"{normalized_talker}{formatter}," + ",".join(encoded))


def _validate_field(field: AdvancedField, raw: object) -> str:
    value = "" if raw is None else str(raw).strip()
    field_type = field.field_type
    optional = field_type.endswith("_optional") or field_type == "text_optional"
    if not value:
        if optional or field_type.startswith("choice:") and "" in field_type.split(":", 1)[1].split(","):
            return ""
        raise IEC61162Error(f"{field.name} is required")
    if any(character in value for character in "$!*,\r\n"):
        raise IEC61162Error(f"{field.name} contains an IEC delimiter")

    if field_type.startswith("choice:"):
        choices = field_type.split(":", 1)[1].split(",")
        if value not in choices:
            raise IEC61162Error(f"{field.name} must be one of {choices}")
    elif field_type in {"int", "int_optional"}:
        try:
            number = int(value)
        except ValueError as error:
            raise IEC61162Error(f"{field.name} must be an integer") from error
        _validate_range(field, number)
        value = str(number)
    elif field_type in {"float", "float_optional"}:
        try:
            number = float(value)
        except ValueError as error:
            raise IEC61162Error(f"{field.name} must be numeric") from error
        if not math.isfinite(number):
            raise IEC61162Error(f"{field.name} must be finite")
        _validate_range(field, number)
    elif field_type in {"time", "time_hhmmss_optional"}:
        if not re.fullmatch(r"(?:[01]\d|2[0-3])[0-5]\d[0-5]\d(?:\.\d+)?", value):
            raise IEC61162Error(f"{field.name} must be UTC hhmmss[.ss]")
    elif field_type == "date":
        if not re.fullmatch(r"\d{6}", value):
            raise IEC61162Error(f"{field.name} must be ddmmyy")
    elif field_type == "lat":
        _validate_nmea_coordinate(value, latitude=True, field_name=field.name)
    elif field_type == "lon":
        _validate_nmea_coordinate(value, latitude=False, field_name=field.name)
    elif field_type not in {"text", "nmea_text", "text_optional"}:
        raise IEC61162Error(f"Unsupported field type {field_type!r} for {field.name}")
    return value


def _validate_range(field: AdvancedField, number: float) -> None:
    if field.minimum is not None and number < field.minimum:
        raise IEC61162Error(f"{field.name} is below {field.minimum}")
    if field.maximum is not None and number > field.maximum:
        raise IEC61162Error(f"{field.name} exceeds {field.maximum}")


def _validate_nmea_coordinate(value: str, *, latitude: bool, field_name: str) -> None:
    digits = 2 if latitude else 3
    if not re.fullmatch(rf"\d{{{digits}}}\d{{2}}(?:\.\d+)?", value):
        raise IEC61162Error(f"{field_name} has invalid NMEA coordinate format")
    degrees = int(value[:digits])
    minutes = float(value[digits:])
    maximum = 90 if latitude else 180
    if degrees > maximum or minutes >= 60.0 or degrees == maximum and minutes > 0.0:
        raise IEC61162Error(f"{field_name} is outside coordinate bounds")


def _validate_protocol_rules(formatter: str, definition: AdvancedProtocol, values: list[str]) -> None:
    by_name = {field.name: values[index] for index, field in enumerate(definition.fields)}
    if formatter == "ROR":
        if by_name.get("port_order") and not by_name.get("port_status"):
            raise IEC61162Error("ROR port order requires port status")
    if formatter == "HRM" and by_name.get("sentence_status") == "C":
        reset_fields = ("reset_time", "reset_day", "reset_month", "reset_year")
        if not any(by_name.get(name) for name in reset_fields) and not by_name.get("heel_alert_threshold"):
            raise IEC61162Error("HRM command requires reset data or heel alert threshold")
    if formatter == "XDR":
        allowed_units = {
            "A": {"D", "P"},
            "B": {"K"},
            "C": {"C", "K"},
            "D": {"M", "P"},
            "E": {"M", "P"},
            "F": {"H"},
            "G": {"", "P"},
            "H": {"P"},
            "I": {"A"},
            "L": {"S"},
            "N": {"N"},
            "P": {"B", "P"},
            "R": {"I", "L", "H"},
            "S": {"b", "p"},
            "T": {"R"},
            "U": {"V"},
            "V": {"M", "P"},
            "W": {"C"},
        }
        transducer_type = by_name.get("type", "")
        unit = by_name.get("unit", "")
        if unit not in allowed_units.get(transducer_type, set()):
            expected = ", ".join(sorted(allowed_units.get(transducer_type, ()))) or "null field"
            raise IEC61162Error(
                f"XDR unit {unit or 'null field'} is invalid for type {transducer_type}; expected {expected}"
            )


def _auto_value(
    formatter: str,
    name: str,
    sensor: SensorSnapshot,
    now: datetime,
    context: Mapping[str, object] | None = None,
) -> str | None:
    lat, ns = _format_lat(sensor.latitude_deg)
    lon, ew = _format_lon(sensor.longitude_deg)
    common = {
        "time": now.strftime("%H%M%S.00"),
        "date": now.strftime("%d%m%y"),
        "lat": lat,
        "ns": ns,
        "lon": lon,
        "ew": ew,
    }
    if name in common:
        return common[name]
    mwv_reference = str((context or {}).get("ref", "R")).strip().upper()
    if formatter == "MWV" and name == "angle":
        wind_angle = (
            sensor.theoretical_wind_from_bow_deg
            if mwv_reference == "T"
            else sensor.relative_wind_from_bow_deg
        )
        return _format_angle(wind_angle)
    if formatter == "MWV" and name == "speed":
        wind_speed = (
            sensor.theoretical_wind_speed_kn
            if mwv_reference == "T"
            else sensor.relative_wind_speed_kn
        )
        return f"{wind_speed:.1f}"
    if formatter == "XDR" and name == "value":
        transducer_type = str((context or {}).get("type", "")).strip().upper()
        identifier = str((context or {}).get("name", "")).split("#", 1)[0].strip().upper()
        xdr_values = {
            ("W", "AIR"): sensor.xdr_dew_point_c,
            ("A", "HEEL"): sensor.roll_deg,
            ("A", "PITCH"): sensor.pitch_deg,
            ("A", "ROLL"): sensor.roll_deg,
            ("A", "RUDDER"): sensor.rudder_response_starboard_deg,
            ("C", "AIR"): sensor.xdr_air_temperature_c,
            ("C", "WATER"): sensor.water_temperature_c,
            ("H", "AIR"): sensor.xdr_relative_humidity_percent,
            ("P", "BARO"): sensor.xdr_surface_pressure_bar,
        }
        result = xdr_values.get((transducer_type, identifier))
        if result is None:
            return None
        precision = 3 if (transducer_type, identifier) == ("P", "BARO") else 1
        return f"{float(result):.{precision}f}"
    values = {
        ("HDT", "heading"): sensor.heading_deg,
        ("HDG", "heading"): sensor.heading_deg,
        ("HDM", "heading"): sensor.heading_deg,
        ("THS", "heading"): sensor.heading_deg,
        ("ROT", "rot"): sensor.rot_deg_min,
        ("RMC", "sog"): sensor.sog_kn,
        ("RMC", "cog"): sensor.cog_deg,
        ("VTG", "course_true"): sensor.cog_deg,
        ("VTG", "speed_kn"): sensor.sog_kn,
        ("VHW", "hdg_t"): sensor.heading_deg,
        ("VHW", "stw_n"): sensor.stw_kn,
        ("VBW", "long_water"): sensor.longitudinal_water_kn,
        ("VBW", "trans_water"): sensor.transverse_water_kn,
        ("VBW", "long_ground"): sensor.sog_kn,
        ("VBW", "trans_ground"): sensor.transverse_ground_kn,
        ("VBW", "stern_water"): sensor.aft_transverse_water_kn,
        ("VBW", "stern_ground"): sensor.aft_transverse_ground_kn,
        ("VLW", "total"): sensor.water_distance_nm,
        ("VLW", "trip"): sensor.water_distance_nm,
        ("DBT", "metres"): sensor.depth_m,
        ("DPT", "depth"): sensor.depth_m,
        ("MWD", "true"): sensor.true_wind_from_deg,
        ("MWD", "kn"): sensor.true_wind_speed_kn,
        ("MTW", "temperature"): sensor.water_temperature_c,
        ("VDR", "set_true"): sensor.current_set_deg,
        ("VDR", "drift"): sensor.current_drift_kn,
        ("RSA", "starboard"): sensor.rudder_response_starboard_deg,
        ("RSA", "port"): sensor.rudder_response_port_deg,
        ("ROR", "stbd_order"): sensor.rudder_order_starboard_deg,
        ("ROR", "port_order"): sensor.rudder_order_port_deg,
        ("PRC", "lever"): sensor.propeller_lever_demand_percent,
        ("TRC", "number"): sensor.thruster_number,
        ("TRC", "rpm_demand"): sensor.thruster_rpm_demand_percent,
        ("TRC", "pitch_demand"): sensor.thruster_pitch_demand_percent,
        ("TRC", "azimuth"): sensor.thruster_azimuth_demand_deg,
        ("TRD", "number"): sensor.thruster_number,
        ("TRD", "rpm_response"): sensor.thruster_rpm_response_percent,
        ("TRD", "pitch_response"): sensor.thruster_pitch_response_percent,
        ("TRD", "azimuth_response"): sensor.thruster_azimuth_response_deg,
        ("HRM", "heel_angle"): sensor.roll_deg,
        ("XTE", "xte"): abs(sensor.xte_nm),
        ("XTR", "xte"): abs(sensor.xte_nm),
    }
    result = values.get((formatter, name))
    if result is None:
        return None
    if isinstance(result, int):
        return str(result)
    angle_fields = {
        ("HDT", "heading"), ("HDG", "heading"), ("HDM", "heading"),
        ("THS", "heading"), ("RMC", "cog"), ("VTG", "course_true"),
        ("VHW", "hdg_t"), ("MWV", "angle"), ("MWD", "true"),
        ("VDR", "set_true"), ("TRC", "azimuth"), ("TRD", "azimuth_response"),
    }
    if (formatter, name) in angle_fields:
        return _format_angle(float(result))
    return f"{float(result):.1f}"
