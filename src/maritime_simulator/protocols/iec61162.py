# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math

from .sensors import SensorSnapshot


REGULAR_PROTOCOLS = (
    "DTM", "GGA", "GLL", "GNS", "RMC", "VTG", "ZDA",
    "HDT", "HDG", "HDM", "ROT", "THS",
    "VHW", "VBW", "VLW",
    "DBT", "DPT",
    "MWV", "MWD",
    "MDA", "MTW",
    "XDR_AIR_TEMPERATURE", "XDR_RELATIVE_HUMIDITY",
    "XDR_DEW_POINT", "XDR_SURFACE_PRESSURE",
    "VDR",
    "RSA", "ROR",
    "VDM", "VDO",
    "PRC", "TRC", "TRD",
)


class IEC61162Error(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecodedSentence:
    talker: str
    formatter: str
    fields: tuple[str, ...]
    raw: str


@dataclass(frozen=True, slots=True)
class DecodedRudderOrder:
    talker: str
    starboard_or_single_deg: float | None
    starboard_valid: bool
    port_deg: float | None
    port_valid: bool | None
    location: str | None
    raw: str


@dataclass(frozen=True, slots=True)
class DecodedRudderAngle:
    talker: str
    starboard_or_single_deg: float | None
    starboard_valid: bool
    port_deg: float | None
    port_valid: bool | None
    raw: str


class IEC61162Codec:
    @staticmethod
    def checksum(payload: str) -> int:
        value = 0
        for character in payload:
            value ^= ord(character)
        return value

    @classmethod
    def frame(cls, payload: str, *, delimiter: str = "$") -> str:
        if not payload or payload.startswith(("$", "!")):
            raise IEC61162Error("Payload must not include a start delimiter")
        if delimiter not in {"$", "!"}:
            raise IEC61162Error("Unsupported sentence delimiter")
        sentence = f"{delimiter}{payload}*{cls.checksum(payload):02X}"
        if len(sentence) + 2 > 82:
            raise IEC61162Error("IEC 61162-1 sentence exceeds 82 characters including CRLF")
        return sentence

    @classmethod
    def decode(cls, raw: str) -> DecodedSentence:
        sentence = raw.strip()
        if len(sentence) + 2 > 82:
            raise IEC61162Error("IEC 61162-1 sentence exceeds 82 characters including CRLF")
        if not sentence.startswith(("$", "!")):
            raise IEC61162Error("Sentence does not start with '$' or '!'")
        if "*" not in sentence:
            raise IEC61162Error("Sentence has no checksum delimiter")
        payload, supplied = sentence[1:].rsplit("*", 1)
        if len(supplied) != 2:
            raise IEC61162Error("Checksum must contain two hexadecimal digits")
        try:
            expected = int(supplied, 16)
        except ValueError as error:
            raise IEC61162Error("Checksum is not hexadecimal") from error
        if cls.checksum(payload) != expected:
            raise IEC61162Error("Checksum mismatch")
        parts = payload.split(",")
        identifier = parts[0]
        if len(identifier) < 5:
            raise IEC61162Error("Sentence identifier is incomplete")
        return DecodedSentence(
            talker=identifier[:2],
            formatter=identifier[2:],
            fields=tuple(parts[1:]),
            raw=sentence,
        )

    @classmethod
    def decode_ror(cls, raw: str) -> DecodedRudderOrder:
        sentence = cls.decode(raw)
        if sentence.formatter != "ROR":
            raise IEC61162Error(f"Expected ROR, received {sentence.formatter}")
        if len(sentence.fields) < 2:
            raise IEC61162Error("ROR requires rudder order and status")
        starboard = _optional_finite_float(sentence.fields[0], "starboard/single rudder order")
        starboard_valid = starboard is not None and sentence.fields[1] == "A"
        port = _optional_finite_float(sentence.fields[2] if len(sentence.fields) > 2 else "", "port rudder order")
        port_status = sentence.fields[3] if len(sentence.fields) > 3 else ""
        port_valid = None if port is None and not port_status else port_status == "A"
        location = sentence.fields[4] if len(sentence.fields) > 4 and sentence.fields[4] else None
        return DecodedRudderOrder(
            talker=sentence.talker,
            starboard_or_single_deg=starboard,
            starboard_valid=starboard_valid,
            port_deg=port,
            port_valid=port_valid,
            location=location,
            raw=sentence.raw,
        )

    @classmethod
    def decode_rsa(cls, raw: str) -> DecodedRudderAngle:
        sentence = cls.decode(raw)
        if sentence.formatter != "RSA":
            raise IEC61162Error(f"Expected RSA, received {sentence.formatter}")
        if len(sentence.fields) < 2:
            raise IEC61162Error("RSA requires rudder angle and status")
        starboard = _optional_finite_float(sentence.fields[0], "starboard/single rudder angle")
        starboard_valid = starboard is not None and sentence.fields[1] == "A"
        port = _optional_finite_float(
            sentence.fields[2] if len(sentence.fields) > 2 else "",
            "port rudder angle",
        )
        port_status = sentence.fields[3] if len(sentence.fields) > 3 else ""
        port_valid = None if port is None and not port_status else port_status == "A"
        return DecodedRudderAngle(
            talker=sentence.talker,
            starboard_or_single_deg=starboard,
            starboard_valid=starboard_valid,
            port_deg=port,
            port_valid=port_valid,
            raw=sentence.raw,
        )


def _finite_float(value: str, label: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise IEC61162Error(f"Invalid {label}") from error
    if not math.isfinite(number):
        raise IEC61162Error(f"Invalid {label}")
    return number


def _optional_finite_float(value: str, label: str) -> float | None:
    return None if value == "" else _finite_float(value, label)


def _format_lat(value: float) -> tuple[str, str]:
    direction = "N" if value >= 0 else "S"
    absolute = abs(value)
    degrees = int(absolute)
    minutes = (absolute - degrees) * 60.0
    return f"{degrees:02d}{minutes:07.4f}", direction


def _format_lon(value: float) -> tuple[str, str]:
    direction = "E" if value >= 0 else "W"
    absolute = abs(value)
    degrees = int(absolute)
    minutes = (absolute - degrees) * 60.0
    return f"{degrees:03d}{minutes:07.4f}", direction


def _format_angle(value: float, decimals: int = 1) -> str:
    """Round an angle without ever emitting the IEC-invalid upper endpoint 360."""
    normalized = round(float(value) % 360.0, decimals) % 360.0
    return f"{normalized:.{decimals}f}"


def encode_protocol(protocol: str, sensor: SensorSnapshot, now: datetime | None = None) -> str:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = current.strftime("%H%M%S")
    datestamp = current.strftime("%d%m%y")
    lat, ns = _format_lat(sensor.latitude_deg)
    lon, ew = _format_lon(sensor.longitude_deg)
    sog_kmh = sensor.sog_kn * 1.852
    stw_kmh = sensor.stw_kn * 1.852
    feet = sensor.depth_m * 3.28084
    fathoms = sensor.depth_m * 0.546807
    heading = _format_angle(sensor.heading_deg)
    cog = _format_angle(sensor.cog_deg)
    relative_wind = _format_angle(sensor.relative_wind_from_bow_deg)
    true_wind = _format_angle(sensor.true_wind_from_deg)
    current_set = _format_angle(sensor.current_set_deg)
    gnss_status = "A" if sensor.gnss_valid else "V"
    gnss_mode = "A" if sensor.gnss_valid else "N"
    gga_quality = "1" if sensor.gnss_valid else "0"
    gns_mode = "AN" if sensor.gnss_valid else "NN"
    satellites = "08" if sensor.gnss_valid else "00"
    thruster_demand_azimuth = _format_angle(sensor.thruster_azimuth_demand_deg)
    thruster_response_azimuth = _format_angle(sensor.thruster_azimuth_response_deg)
    payloads = {
        # Positions in V2 are WGS 84, so both the local and reference datum are W84
        # and all offsets are zero.
        "DTM": "GPDTM,W84,,0.0000,N,0.0000,E,0.0,W84",
        "GGA": f"GPGGA,{timestamp}.00,{lat},{ns},{lon},{ew},{gga_quality},{satellites},0.9,0.0,M,0.0,M,,",
        "GLL": f"GPGLL,{lat},{ns},{lon},{ew},{timestamp}.00,{gnss_status},{gnss_mode}",
        "GNS": f"GNGNS,{timestamp}.00,{lat},{ns},{lon},{ew},{gns_mode},{satellites},0.9,0.0,0.0,,",
        "RMC": f"GPRMC,{timestamp}.00,{gnss_status},{lat},{ns},{lon},{ew},{sensor.sog_kn:.1f},{cog},{datestamp},,,{gnss_mode}",
        "VTG": f"GPVTG,{cog},T,,M,{sensor.sog_kn:.1f},N,{sog_kmh:.1f},K,{gnss_mode}",
        "ZDA": current.strftime("GPZDA,%H%M%S.00,%d,%m,%Y,00,00"),
        "HDT": f"GPHDT,{heading},T",
        "HDG": f"HCHDG,{heading},,,,",
        "HDM": f"HCHDM,{heading},M",
        "ROT": f"TIROT,{sensor.rot_deg_min:.1f},A",
        "THS": f"GPTHS,{heading},A",
        "VHW": f"VWVHW,{heading},T,,M,{sensor.stw_kn:.1f},N,{stw_kmh:.1f},K",
        "VBW": (
            f"VWVBW,{sensor.stw_kn:.1f},{sensor.transverse_water_kn:.1f},A,"
            f"{sensor.sog_kn:.1f},{sensor.transverse_ground_kn:.1f},A,"
            f"{sensor.aft_transverse_water_kn:.1f},A,"
            f"{sensor.aft_transverse_ground_kn:.1f},A"
        ),
        "VLW": f"VWVLW,{sensor.water_distance_nm:.2f},N,{sensor.ground_distance_nm:.2f},N",
        "DBT": f"SDDBT,{feet:.1f},f,{sensor.depth_m:.1f},M,{fathoms:.1f},F",
        "DPT": f"SDDPT,{sensor.depth_m:.1f},0.0",
        "MWV": f"WIMWV,{relative_wind},R,{sensor.relative_wind_speed_kn:.1f},N,A",
        "MWD": f"WIMWD,{true_wind},T,,M,{sensor.true_wind_speed_kn:.1f},N,,M",
        "MDA": (
            f"WIMDA,{sensor.barometric_pressure_bar / 0.0338639:.2f},I,"
            f"{sensor.barometric_pressure_bar:.4f},B,{sensor.air_temperature_c:.1f},C,"
            f"{sensor.water_temperature_c:.1f},C,{sensor.absolute_humidity_percent:.1f},"
            f"{sensor.relative_humidity_percent:.1f},{sensor.dew_point_c:.1f},C,"
            f"{true_wind},T,,M,{sensor.true_wind_speed_kn:.1f},N,,M"
        ),
        "MTW": f"WIMTW,{sensor.water_temperature_c:.1f},C",
        "XDR_AIR_TEMPERATURE": (
            f"WIXDR,C,{sensor.xdr_air_temperature_c:.1f},C,Air#0"
        ),
        "XDR_RELATIVE_HUMIDITY": (
            f"WIXDR,H,{sensor.xdr_relative_humidity_percent:.1f},P,Air#1"
        ),
        "XDR_DEW_POINT": f"WIXDR,W,{sensor.xdr_dew_point_c:.1f},C,Air#2",
        "XDR_SURFACE_PRESSURE": (
            f"WIXDR,P,{sensor.xdr_surface_pressure_bar:.3f},B,Baro#0"
        ),
        "VDR": f"VWVDR,{current_set},T,,M,{sensor.current_drift_kn:.1f},N",
        "RSA": (
            f"HCRSA,{sensor.rudder_response_starboard_deg:.1f},A,{sensor.rudder_response_port_deg:.1f},A"
            if sensor.dual_rudder
            else f"HCRSA,{sensor.rudder_response_starboard_deg:.1f},A,,"
        ),
        "ROR": (
            f"HCROR,{sensor.rudder_order_starboard_deg:.1f},A,{sensor.rudder_order_port_deg:.1f},A,B,,,,"
            if sensor.dual_rudder
            else f"HCROR,{sensor.rudder_order_starboard_deg:.1f},A,,,B,,,,"
        ),
        "PRC": (
            f"GPPRC,{sensor.propeller_lever_demand_percent:.1f},A,"
            f"{abs(sensor.propeller_lever_demand_percent):.1f},P,"
            f"{sensor.propeller_lever_demand_percent:.1f},P,B,0"
        ),
        "TRC": (
            f"HCTRC,{sensor.thruster_number},{sensor.thruster_rpm_demand_percent:.1f},P,"
            f"{sensor.thruster_pitch_demand_percent:.1f},P,{thruster_demand_azimuth},B,R"
        ),
        "TRD": (
            f"HCTRD,{sensor.thruster_number},{sensor.thruster_rpm_response_percent:.1f},P,"
            f"{sensor.thruster_pitch_response_percent:.1f},P,{thruster_response_azimuth}"
        ),
    }
    payload = payloads.get(protocol.upper())
    if payload is None:
        raise IEC61162Error(f"V2 encoder not implemented for {protocol}")
    return IEC61162Codec.frame(payload)


def encode_protocol_sentences(
    protocol: str,
    sensor: SensorSnapshot,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Encode every sentence belonging to one scheduled protocol update."""
    primary = encode_protocol(protocol, sensor, now)
    if protocol.upper() != "MWV":
        return (primary,)
    theoretical_from_bow = _format_angle(sensor.theoretical_wind_from_bow_deg)
    theoretical = IEC61162Codec.frame(
        f"WIMWV,{theoretical_from_bow},T,{sensor.theoretical_wind_speed_kn:.1f},N,A"
    )
    return primary, theoretical


def encode_ais_position_report(
    target: dict,
    *,
    formatter: str = "VDM",
    utc_second: int = 60,
) -> str:
    formatter = str(formatter).upper()
    if formatter not in {"VDM", "VDO"}:
        raise IEC61162Error("AIS position formatter must be VDM or VDO")
    try:
        mmsi = int(str(target.get("mmsi", "")).strip())
        latitude = float(target.get("lat"))
        longitude = float(target.get("lon"))
    except (TypeError, ValueError) as error:
        raise IEC61162Error("AIS target has invalid identity or position") from error
    if not 0 <= mmsi <= 999_999_999 or not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise IEC61162Error("AIS target is outside valid bounds")
    sog = max(0.0, min(102.2, float(target.get("speed", 0.0) or 0.0)))
    cog = float(target.get("course", 0.0) or 0.0) % 360.0
    try:
        raw_heading = float(target.get("heading"))
        heading = int(round(raw_heading)) % 360 if 0.0 <= raw_heading < 360.0 else 511
    except (TypeError, ValueError):
        heading = 511
    try:
        second = int(utc_second)
    except (TypeError, ValueError) as error:
        raise IEC61162Error("AIS UTC second must be an integer") from error
    if not 0 <= second <= 63:
        raise IEC61162Error("AIS UTC second must be in [0, 63]")

    bits = ""
    bits += _unsigned_bits(1, 6)
    bits += _unsigned_bits(0, 2)
    bits += _unsigned_bits(mmsi, 30)
    bits += _unsigned_bits(_ais_nav_status(target.get("nav_status")), 4)
    bits += _unsigned_bits(128, 8)
    bits += _unsigned_bits(int(round(sog * 10.0)), 10)
    bits += _unsigned_bits(0, 1)
    bits += _signed_bits(int(round(longitude * 600_000)), 28)
    bits += _signed_bits(int(round(latitude * 600_000)), 27)
    bits += _unsigned_bits(int(round(cog * 10.0)) % 3600, 12)
    bits += _unsigned_bits(heading, 9)
    bits += _unsigned_bits(second, 6)
    bits += _unsigned_bits(0, 2)
    bits += _unsigned_bits(0, 3)
    bits += _unsigned_bits(0, 1)
    bits += _unsigned_bits(0, 19)
    payload, fill_bits = _ais_sixbit_payload(bits)
    return IEC61162Codec.frame(f"AI{formatter},1,1,,A,{payload},{fill_bits}", delimiter="!")


def encode_ais_static_voyage_report(target: dict, *, sequence_id: int = 0) -> tuple[str, str]:
    """Encode available target metadata as an AIS type 5 two-fragment VDM report."""
    try:
        mmsi = int(str(target.get("mmsi", "")).strip())
    except (TypeError, ValueError) as error:
        raise IEC61162Error("AIS target has an invalid MMSI") from error
    if not 0 <= mmsi <= 999_999_999:
        raise IEC61162Error("AIS MMSI must be in [0, 999999999]")
    length = max(0.0, float(target.get("length") or 0.0))
    beam = max(0.0, float(target.get("beam") or 0.0))
    to_bow = min(511, round(length * 0.6))
    to_stern = min(511, max(0, round(length) - to_bow))
    to_port = min(63, round(beam / 2.0))
    to_starboard = min(63, max(0, round(beam) - to_port))
    draught = max(0, min(255, round(float(target.get("draught") or 0.0) * 10.0)))
    imo = max(0, min(999_999_999, int(target.get("imo") or 0)))
    month = max(0, min(12, int(target.get("eta_month") or 0)))
    day = max(0, min(31, int(target.get("eta_day") or 0)))
    hour = max(0, min(24, int(target.get("eta_hour") if target.get("eta_hour") is not None else 24)))
    minute = max(0, min(60, int(target.get("eta_minute") if target.get("eta_minute") is not None else 60)))

    bits = ""
    bits += _unsigned_bits(5, 6)
    bits += _unsigned_bits(0, 2)
    bits += _unsigned_bits(mmsi, 30)
    bits += _unsigned_bits(0, 2)
    bits += _unsigned_bits(imo, 30)
    bits += _ais_text_bits(target.get("callsign"), 7)
    bits += _ais_text_bits(target.get("name"), 20)
    bits += _unsigned_bits(_ais_ship_type(target.get("ship_type")), 8)
    bits += _unsigned_bits(to_bow, 9)
    bits += _unsigned_bits(to_stern, 9)
    bits += _unsigned_bits(to_port, 6)
    bits += _unsigned_bits(to_starboard, 6)
    bits += _unsigned_bits(0, 4)
    bits += _unsigned_bits(month, 4)
    bits += _unsigned_bits(day, 5)
    bits += _unsigned_bits(hour, 5)
    bits += _unsigned_bits(minute, 6)
    bits += _unsigned_bits(draught, 8)
    bits += _ais_text_bits(target.get("destination"), 20)
    bits += _unsigned_bits(0, 1)
    bits += _unsigned_bits(0, 1)
    payload, fill_bits = _ais_sixbit_payload(bits)
    sequence = str(int(sequence_id) % 10)
    split = 60
    return (
        IEC61162Codec.frame(f"AIVDM,2,1,{sequence},A,{payload[:split]},0", delimiter="!"),
        IEC61162Codec.frame(f"AIVDM,2,2,{sequence},A,{payload[split:]},{fill_bits}", delimiter="!"),
    )


def _ais_nav_status(status: object) -> int:
    text = str(status or "").lower()
    for fragment, value in (
        ("anchor", 1),
        ("not under command", 2),
        ("restricted", 3),
        ("constrained", 4),
        ("moored", 5),
        ("aground", 6),
        ("fishing", 7),
        ("sailing", 8),
    ):
        if fragment in text:
            return value
    return 0


def _ais_ship_type(value: object) -> int:
    text = str(value or "").strip().lower()
    if text.isdigit():
        return max(0, min(99, int(text)))
    for fragment, code in (
        ("passenger", 60), ("cargo", 70), ("tanker", 80), ("tug", 52),
        ("fishing", 30), ("sailing", 36), ("pleasure", 37), ("pilot", 50),
        ("search", 51), ("dredg", 33), ("military", 35),
    ):
        if fragment in text:
            return code
    return 0


def _ais_text_bits(value: object, width: int) -> str:
    alphabet = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"
    text = str(value or "").upper()[:width].ljust(width, "@")
    return "".join(_unsigned_bits(alphabet.find(char) if char in alphabet else 32, 6) for char in text)


def _unsigned_bits(value: int, width: int) -> str:
    return format(int(value) & ((1 << width) - 1), f"0{width}b")


def _signed_bits(value: int, width: int) -> str:
    return _unsigned_bits((1 << width) + value if value < 0 else value, width)


def _ais_sixbit_payload(bits: str) -> tuple[str, int]:
    fill_bits = (6 - len(bits) % 6) % 6
    bits += "0" * fill_bits
    characters = []
    for index in range(0, len(bits), 6):
        value = int(bits[index:index + 6], 2)
        code = value + 48
        if code > 87:
            code += 8
        characters.append(chr(code))
    return "".join(characters), fill_bits
