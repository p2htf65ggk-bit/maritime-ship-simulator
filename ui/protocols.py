# SPDX-License-Identifier: MPL-2.0
from datetime import timezone
import sys


SENSOR_PROTOCOLS = {
    "GNSS": ("GGA", "GLL", "GNS", "RMC", "VTG", "ZDA"),
    "Gyro": ("HDT", "HDG", "HDM", "ROT", "THS"),
    "LOG": ("VHW", "VBW", "VLW"),
    "Depth": ("DBT", "DPT"),
    "Wind": ("MWV", "MWD"),
    "Meteorological": ("MDA", "MTW"),
    "Current": ("VDR",),
    "Rudder": ("RSA", "ROR"),
    "AIS": ("VDM",),
    "Additional": ("PRC", "TRC", "TRD"),
}


def default_protocol_configs():
    configs = {}
    base_port = 5001
    serial_port = default_serial_port()
    multicast_group = "239.192.192.0"
    multicast_port = 4096
    for sensor, protocols in SENSOR_PROTOCOLS.items():
        for offset, protocol in enumerate(protocols):
            configs[protocol] = {
                "sensor": sensor,
                "enabled": True,
                "transport": "UDP",
                "host": multicast_group,
                "port": multicast_port,
                "device": serial_port,
                "com_port": serial_port,
                "baud": 4800,
                "parity": "N",
                "stop_bits": "1",
                "rate_hz": 1.0,
                "last_emit": 0.0,
            }
            configs[protocol]["outputs"] = {
                "UDP": {
                    "enabled": True,
                    "host": multicast_group,
                    "port": multicast_port,
                    "rate_hz": 1.0,
                    "last_emit": 0.0,
                },
                "TCP": {
                    "enabled": False,
                    "host": "127.0.0.1",
                    "port": base_port + offset,
                    "rate_hz": 1.0,
                    "last_emit": 0.0,
                },
                "Serial": {
                    "enabled": False,
                    "com_port": serial_port,
                    "device": serial_port,
                    "baud": 4800,
                    "parity": "N",
                    "stop_bits": "1",
                    "rate_hz": 1.0,
                    "last_emit": 0.0,
                },
            }
        base_port += 100
    return configs


def default_serial_port():
    if sys.platform.startswith("win"):
        return "COM1"
    if sys.platform == "darwin":
        return "/dev/cu.usbserial"
    return "/dev/ttyUSB0"


def protocol_port(config):
    transport = config.get("transport", "UDP")
    if transport in ("UDP", "TCP"):
        return f"{transport}:{config.get('host', '127.0.0.1')}:{config.get('port', 5000)}"
    return (
        f"Serial:{config.get('com_port') or config.get('device', default_serial_port())}:"
        f"{config.get('baud', 4800)}{config.get('parity', 'N')}{config.get('stop_bits', '1')}"
    )


def protocol_outputs(config):
    outputs = config.get("outputs")
    if outputs:
        base = {key: value for key, value in config.items() if key != "outputs"}
        result = []
        for transport, output in outputs.items():
            merged = dict(base)
            merged.update(output)
            merged["transport"] = transport
            result.append(merged)
        return result
    return [config]


def build_sentence(protocol, sim, now, checksum, ais=None):
    sensor = getattr(sim, "sensor", {})
    lat = sensor.get("lat", sim.lat)
    lon = sensor.get("lon", sim.lon)
    hdg = sensor.get("heading", sim.heading)
    cog = sensor.get("cog", getattr(sim, "cog", sim.heading))
    sog = sensor.get("sog", sim.speed)
    stw = sensor.get("stw", sim.stw)
    rot = sensor.get("rot", sim.rate_of_turn)
    depth = sensor.get("depth", sim.depth)
    wind_dir = sensor.get("true_wind_direction", sensor.get("wind_direction", getattr(sim, "true_wind_direction", getattr(sim, "wind_direction", 0.0))))
    wind_speed = sensor.get("true_wind_speed", sensor.get("wind_speed", getattr(sim, "true_wind_speed", getattr(sim, "wind_speed", 0.0))))
    rel_wind_angle = sensor.get("relative_wind_angle", wind_dir)
    rel_wind_speed = sensor.get("relative_wind_speed", wind_speed)
    bar = sensor.get("barometric_pressure", getattr(sim, "barometric_pressure", 1.013))
    air_temp = sensor.get("air_temperature", getattr(sim, "air_temperature", 15.0))
    water_temp = sensor.get("water_temperature", getattr(sim, "water_temperature", 10.0))
    current_set = sensor.get("current_set", getattr(sim, "current_set", 0.0))
    current_drift = sensor.get("current_drift", getattr(sim, "current_drift", 0.0))
    abs_humidity = sensor.get("absolute_humidity", getattr(sim, "absolute_humidity", 8.0))
    rel_humidity = sensor.get("relative_humidity", getattr(sim, "relative_humidity", 65.0))
    dew_point = sensor.get("dew_point", getattr(sim, "dew_point", 8.0))
    rudder = sensor.get("rudder", getattr(sim, "rudder", 0.0))
    rudder_order = sensor.get("rudder_order", getattr(sim, "rudder_order", rudder))
    rudder_response = sensor.get("rudder_response", getattr(sim, "rudder_response", rudder))
    rudder_order_port = sensor.get("rudder_order_port", getattr(sim, "rudder_order_port", rudder_order))
    rudder_response_port = sensor.get("rudder_response_port", getattr(sim, "rudder_response_port", rudder_response))
    transverse_water = sensor.get("vbw_transverse_water", getattr(sim, "vbw_transverse_water", 0.0))
    transverse_ground = sensor.get("vbw_transverse_ground", getattr(sim, "vbw_transverse_ground", 0.0))
    aft_transverse = sensor.get("vbw_aft_transverse_ground", getattr(sim, "vbw_aft_transverse_ground", 0.0))
    dual_rudder = bool(getattr(sim, "additional_sensors", {}).get("RUDDER2", False))
    vessel_model = getattr(sim, "vessel_model", {})
    max_speed = max(0.1, abs(vessel_model.get("max_speed", 35.0) if isinstance(vessel_model, dict) else 35.0))
    engine_percent = max(-100.0, min(100.0, getattr(sim, "target_speed", 0.0) / max_speed * 100.0))
    lever_demand = max(-100.0, min(100.0, sensor.get("prc_lever_demand", getattr(sim, "prc_lever_demand", engine_percent)) or engine_percent))
    thruster_number = int(sensor.get("thruster_number", getattr(sim, "thruster_number", 1)) or 1)
    thruster_rpm_demand = max(-100.0, min(100.0, sensor.get("thruster_rpm_demand", getattr(sim, "thruster_rpm_demand", 0.0))))
    thruster_pitch_demand = max(-100.0, min(100.0, sensor.get("thruster_pitch_demand", getattr(sim, "thruster_pitch_demand", 0.0))))
    thruster_azimuth_demand = sensor.get("thruster_azimuth_demand", getattr(sim, "thruster_azimuth_demand", 0.0)) % 360.0
    thruster_rpm_response = max(-100.0, min(100.0, sensor.get("thruster_rpm_response", getattr(sim, "thruster_rpm_response", 0.0))))
    thruster_pitch_response = max(-100.0, min(100.0, sensor.get("thruster_pitch_response", getattr(sim, "thruster_pitch_response", 0.0))))
    thruster_azimuth_response = sensor.get("thruster_azimuth_response", getattr(sim, "thruster_azimuth_response", 0.0)) % 360.0

    timestamp = now.astimezone(timezone.utc).strftime("%H%M%S")
    datestamp = now.astimezone(timezone.utc).strftime("%d%m%y")
    lat_text, ns = format_lat(lat)
    lon_text, ew = format_lon(lon)
    stw_kmh = stw * 1.852
    sog_kmh = sog * 1.852
    feet = depth * 3.28084
    fathoms = depth * 0.546807

    payloads = {
        "GGA": f"GPGGA,{timestamp}.00,{lat_text},{ns},{lon_text},{ew},1,08,0.9,0.0,M,0.0,M,,",
        "GLL": f"GPGLL,{lat_text},{ns},{lon_text},{ew},{timestamp}.00,A,A",
        "GNS": f"GNGNS,{timestamp}.00,{lat_text},{ns},{lon_text},{ew},AN,08,0.9,0.0,0.0,,",
        "RMC": f"GPRMC,{timestamp}.00,A,{lat_text},{ns},{lon_text},{ew},{sog:.1f},{cog:.1f},{datestamp},,,A",
        "VTG": f"GPVTG,{cog:.1f},T,,M,{sog:.1f},N,{sog_kmh:.1f},K,A",
        "ZDA": now.astimezone(timezone.utc).strftime("GPZDA,%H%M%S.00,%d,%m,%Y,00,00"),
        "HDT": f"GPHDT,{hdg:.1f},T",
        "HDG": f"HCHDG,{hdg:.1f},,,,",
        "HDM": f"HCHDM,{hdg:.1f},M",
        "ROT": f"TIROT,{rot:.1f},A",
        "THS": f"GPTHS,{hdg:.1f},A",
        "VHW": f"VWVHW,{hdg:.1f},T,,M,{stw:.1f},N,{stw_kmh:.1f},K",
        "VBW": f"VWVBW,{stw:.1f},{transverse_water:.1f},A,{sog:.1f},{transverse_ground:.1f},A,{aft_transverse:.1f},A",
        "VLW": f"VWVLW,0.0,N,0.0,N",
        "DBT": f"SDDBT,{feet:.1f},f,{depth:.1f},M,{fathoms:.1f},F",
        "DPT": f"SDDPT,{depth:.1f},0.0",
        "MWV": f"WIMWV,{rel_wind_angle:.1f},R,{rel_wind_speed:.1f},N,A",
        "MWD": f"WIMWD,{wind_dir:.1f},T,,M,{wind_speed:.1f},N,,M",
        "MDA": f"WIMDA,{bar / 0.0338639:.4f},I,{bar:.4f},B,{air_temp:.1f},C,{water_temp:.1f},C,{abs_humidity:.1f},{rel_humidity:.1f},{dew_point:.1f},C,{wind_dir:.1f},T,,M,{wind_speed:.1f},N,,M",
        "MTW": f"WIMTW,{water_temp:.1f},C",
        "VDR": f"VWVDR,{current_set:.1f},T,,M,{current_drift:.1f},N",
        "RSA": f"HCRSA,{rudder_response:.1f},A,{(rudder_response_port if dual_rudder else rudder_response):.1f},A",
        "ROR": (
            f"HCROR,{rudder_order:.1f},A,{rudder_order_port:.1f},A,B,,,,"
            if dual_rudder
            else f"HCROR,{rudder_order:.1f},A,,,B,,,,"
        ),
        "PRC": f"GPPRC,{lever_demand:.1f},A,{abs(engine_percent):.1f},P,{engine_percent:.1f},P,B,0",
        "TRC": f"HCTRC,{thruster_number},{thruster_rpm_demand:.1f},P,{thruster_pitch_demand:.1f},P,{thruster_azimuth_demand:.1f},B,R",
        "TRD": f"HCTRD,{thruster_number},{thruster_rpm_response:.1f},P,{thruster_pitch_response:.1f},P,{thruster_azimuth_response:.1f}",
    }
    if protocol == "VDM":
        return build_ais_vdm_sentences(ais)
    payload = payloads.get(protocol)
    return checksum(payload) if payload else ""


def format_lat(value):
    direction = "N" if value >= 0 else "S"
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60
    return f"{degrees:02d}{minutes:07.4f}", direction


def format_lon(value):
    direction = "E" if value >= 0 else "W"
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60
    return f"{degrees:03d}{minutes:07.4f}", direction


def build_ais_vdm_sentences(ais):
    if ais is None:
        return []
    targets = ais.get_output_targets()
    sentences = []
    for target in targets:
        sentence = build_ais_position_report(target)
        if sentence:
            sentences.append(sentence)
    return sentences


def build_ais_position_report(target):
    try:
        mmsi = int(str(target.get("mmsi", "")).strip())
        lat = float(target.get("lat"))
        lon = float(target.get("lon"))
    except (TypeError, ValueError):
        return ""
    if not (0 <= mmsi <= 999999999 and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return ""

    sog = max(0.0, min(102.2, float(target.get("speed", 0.0) or 0.0)))
    cog = float(target.get("course", 0.0) or 0.0) % 360.0
    heading = target.get("heading", cog)
    try:
        heading_value = int(round(float(heading))) % 360
    except (TypeError, ValueError):
        heading_value = 511
    nav_status = ais_nav_status_code(target.get("nav_status"))

    bits = ""
    bits += unsigned_bits(1, 6)  # Position report class A
    bits += unsigned_bits(0, 2)  # Repeat indicator
    bits += unsigned_bits(mmsi, 30)
    bits += unsigned_bits(nav_status, 4)
    bits += unsigned_bits(128, 8)  # ROT not available
    bits += unsigned_bits(int(round(sog * 10)), 10)
    bits += unsigned_bits(0, 1)  # Position accuracy low
    bits += signed_bits(int(round(lon * 600000)), 28)
    bits += signed_bits(int(round(lat * 600000)), 27)
    bits += unsigned_bits(int(round(cog * 10)) % 3600, 12)
    bits += unsigned_bits(heading_value, 9)
    bits += unsigned_bits(60, 6)  # Timestamp not available
    bits += unsigned_bits(0, 2)  # Manoeuvre indicator
    bits += unsigned_bits(0, 3)  # Spare
    bits += unsigned_bits(0, 1)  # RAIM
    bits += unsigned_bits(0, 19)  # Radio status

    payload, fill_bits = ais_sixbit_payload(bits)
    body = f"AIVDM,1,1,,A,{payload},{fill_bits}"
    return f"!{body}*{nmea_checksum(body)}"


def ais_nav_status_code(status):
    text = str(status or "").lower()
    if "anchor" in text:
        return 1
    if "not under command" in text:
        return 2
    if "restricted" in text:
        return 3
    if "constrained" in text:
        return 4
    if "moored" in text:
        return 5
    if "aground" in text:
        return 6
    if "fishing" in text:
        return 7
    if "sailing" in text:
        return 8
    return 0


def unsigned_bits(value, width):
    return format(int(value) & ((1 << width) - 1), f"0{width}b")


def signed_bits(value, width):
    value = int(value)
    if value < 0:
        value = (1 << width) + value
    return unsigned_bits(value, width)


def ais_sixbit_payload(bits):
    fill_bits = (6 - len(bits) % 6) % 6
    if fill_bits:
        bits += "0" * fill_bits
    chars = []
    for index in range(0, len(bits), 6):
        value = int(bits[index:index + 6], 2)
        code = value + 48
        if code > 87:
            code += 8
        chars.append(chr(code))
    return "".join(chars), fill_bits


def nmea_checksum(payload):
    checksum = 0
    for char in payload:
        checksum ^= ord(char)
    return f"{checksum:02X}"
