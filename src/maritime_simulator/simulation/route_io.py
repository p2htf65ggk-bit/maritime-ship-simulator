# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from maritime_simulator.domain.state import RouteWaypoint


def load_rtz(path: Path) -> tuple[RouteWaypoint, ...]:
    root = ET.parse(path).getroot()
    waypoint_elements = root.findall(".//{*}waypoints/{*}waypoint")
    speeds: dict[str, float] = {}
    for element in root.findall(".//{*}sheduleElement"):
        waypoint_id = element.attrib.get("waypointId", "")
        try:
            speeds[waypoint_id] = float(element.attrib["speed"])
        except (KeyError, ValueError):
            continue

    result = []
    for index, element in enumerate(waypoint_elements, 1):
        position = element.find("{*}position")
        if position is None:
            continue
        try:
            latitude = float(position.attrib["lat"])
            longitude = float(position.attrib["lon"])
        except (KeyError, ValueError):
            continue
        radius_text = element.attrib.get("radius", "")
        try:
            radius = float(radius_text) if radius_text else None
        except ValueError:
            radius = None
        waypoint_id = element.attrib.get("id", "")
        result.append(
            RouteWaypoint(
                name=element.attrib.get("name") or waypoint_id or str(index),
                latitude_deg=latitude,
                longitude_deg=longitude,
                speed_kn=speeds.get(waypoint_id, 10.0),
                turn_rate_deg_min=None if radius and radius > 0.0 else 10.0,
                turn_radius_nm=radius if radius and radius > 0.0 else None,
                turn_mode="fixed_radius" if radius and radius > 0.0 else "fixed_rot",
            )
        )
    if len(result) < 2:
        raise ValueError(f"RTZ route contains fewer than two usable waypoints: {path}")
    return tuple(result)
