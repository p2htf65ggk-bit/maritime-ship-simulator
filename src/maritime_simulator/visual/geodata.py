# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import replace
import json
import math
import os
from pathlib import Path
import re
import ssl
import tempfile
import time
from typing import Any
from urllib import parse, request

import certifi

from .models import GeoLine, GeoObject, GeoPolygon, GeographicScene


DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_OVERPASS_URLS = (
    DEFAULT_OVERPASS_URL,
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)


def verified_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


class OverpassGeodataProvider:
    """Cached OSM/OpenSeaMap loader with no dependency on the renderer or Qt."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        endpoint: str | None = None,
        cell_degrees: float = 0.01,
        timeout_s: float = 15.0,
    ):
        self.cache_dir = Path(cache_dir)
        configured_endpoint = endpoint or os.environ.get("MARITIME_SIM_OVERPASS_URL", "").strip()
        self.endpoints = (configured_endpoint,) if configured_endpoint else DEFAULT_OVERPASS_URLS
        self.endpoint = self.endpoints[0]
        self.cell_degrees = max(0.01, float(cell_degrees))
        self.timeout_s = max(1.0, float(timeout_s))
        self._endpoint_retry_after: dict[str, float] = {}

    @staticmethod
    def coverage_radius_nm(requested_nm: float) -> float:
        requested = min(12.0, max(2.0, float(requested_nm)))
        return next(radius for radius in (2.0, 4.0, 8.0, 12.0) if requested <= radius)

    def _area_definition(
        self, latitude_deg: float, longitude_deg: float, radius_nm: float
    ) -> tuple[float, float, float, float, int, int, float]:
        coverage = self.coverage_radius_nm(radius_nm)
        lat_step = max(self.cell_degrees, coverage / 60.0 * 0.45)
        lat_index = math.floor((float(latitude_deg) + 90.0) / lat_step)
        center_lat = lat_index * lat_step - 90.0 + lat_step / 2.0
        lon_step = lat_step / max(0.2, abs(math.cos(math.radians(center_lat))))
        lon_index = math.floor((float(longitude_deg) + 180.0) / lon_step)
        center_lon = lon_index * lon_step - 180.0 + lon_step / 2.0
        half_lat = coverage / 60.0 * 1.30
        half_lon = half_lat / max(0.2, abs(math.cos(math.radians(center_lat))))
        return center_lat, center_lon, half_lat, half_lon, lat_index, lon_index, coverage

    def area_key(self, latitude_deg: float, longitude_deg: float, radius_nm: float = 12.0) -> str:
        _, _, _, _, lat_index, lon_index, coverage = self._area_definition(
            latitude_deg, longitude_deg, radius_nm
        )
        return f"osm-v6-{coverage:.0f}nm-{lat_index}-{lon_index}"

    def area_bounds(
        self, latitude_deg: float, longitude_deg: float, radius_nm: float = 12.0
    ) -> tuple[float, float, float, float]:
        center_lat, center_lon, half_lat, half_lon, _, _, _ = self._area_definition(
            latitude_deg, longitude_deg, radius_nm
        )
        return (
            max(-90.0, center_lat - half_lat),
            max(-180.0, center_lon - half_lon),
            min(90.0, center_lat + half_lat),
            min(180.0, center_lon + half_lon),
        )

    def load(
        self,
        latitude_deg: float,
        longitude_deg: float,
        *,
        radius_nm: float = 12.0,
        force: bool = False,
    ) -> GeographicScene:
        scene_id = self.area_key(latitude_deg, longitude_deg, radius_nm)
        cache_path = self.cache_dir / f"{scene_id}.json"
        if cache_path.is_file() and not force:
            try:
                scene = GeographicScene.from_dict(json.loads(cache_path.read_text(encoding="utf-8")))
                return replace(scene, source_status="Cached OSM/OpenSeaMap data")
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                pass

        south, west, north, east = self.area_bounds(latitude_deg, longitude_deg, radius_nm)
        reference_latitude = (south + north) / 2.0
        reference_longitude = (west + east) / 2.0
        detail_half_lat = min(1.5, self.coverage_radius_nm(radius_nm)) / 60.0
        detail_half_lon = detail_half_lat / max(
            0.2, abs(math.cos(math.radians(reference_latitude)))
        )
        detail_bounds = (
            reference_latitude - detail_half_lat,
            reference_longitude - detail_half_lon,
            reference_latitude + detail_half_lat,
            reference_longitude + detail_half_lon,
        )
        landcover_half_lat = min(4.0, self.coverage_radius_nm(radius_nm)) / 60.0
        landcover_half_lon = landcover_half_lat / max(
            0.2, abs(math.cos(math.radians(reference_latitude)))
        )
        landcover_bounds = (
            reference_latitude - landcover_half_lat,
            reference_longitude - landcover_half_lon,
            reference_latitude + landcover_half_lat,
            reference_longitude + landcover_half_lon,
        )
        try:
            raw, layer_notes = self._fetch_scene_payload(
                south,
                west,
                north,
                east,
                detail_bounds,
                landcover_bounds,
            )
            scene = parse_overpass_scene(
                raw,
                scene_id=scene_id,
                reference_latitude_deg=reference_latitude,
                reference_longitude_deg=reference_longitude,
            )
            if layer_notes:
                scene = replace(
                    scene,
                    source_status=f"{scene.source_status}; {'; '.join(layer_notes)}",
                )
            self._write_cache(cache_path, scene)
            return scene
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            if cache_path.is_file():
                try:
                    scene = GeographicScene.from_dict(json.loads(cache_path.read_text(encoding="utf-8")))
                    return replace(scene, source_status=f"Stale cache; network unavailable: {error}")
                except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                    pass
            return GeographicScene(
                scene_id=scene_id,
                reference_latitude_deg=reference_latitude,
                reference_longitude_deg=reference_longitude,
                source_status=f"Water-only fallback; geographic data unavailable: {error}",
            )

    def _fetch(self, query: str) -> dict[str, Any]:
        errors = []
        now = time.monotonic()
        endpoints = [
            endpoint for endpoint in self.endpoints
            if self._endpoint_retry_after.get(endpoint, 0.0) <= now
        ]
        if not endpoints:
            endpoints = [min(
                self.endpoints,
                key=lambda endpoint: self._endpoint_retry_after.get(endpoint, 0.0),
            )]
        for endpoint in endpoints:
            try:
                return self._fetch_endpoint(endpoint, query)
            except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"{endpoint}: {error}")
                if isinstance(error, (OSError, TimeoutError, json.JSONDecodeError)):
                    self._endpoint_retry_after[endpoint] = time.monotonic() + 60.0
        raise OSError("All OpenStreetMap endpoints failed: " + "; ".join(errors))

    def _fetch_endpoint(self, endpoint: str, query: str) -> dict[str, Any]:
        body = parse.urlencode({"data": query}).encode("utf-8")
        http_request = request.Request(
            endpoint,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "User-Agent": "MaritimeShipSimulatorV2/2.1 visual-prototype",
            },
            method="POST",
        )
        with request.urlopen(
            http_request,
            timeout=self.timeout_s,
            context=verified_ssl_context(),
        ) as response:
            payload = response.read(20 * 1024 * 1024 + 1)
        if len(payload) > 20 * 1024 * 1024:
            raise ValueError("OpenStreetMap response exceeded 20 MB")
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict) or not isinstance(decoded.get("elements"), list):
            raise ValueError("OpenStreetMap returned an invalid response")
        return decoded

    def _fetch_scene_payload(
        self,
        south: float,
        west: float,
        north: float,
        east: float,
        detail_bounds: tuple[float, float, float, float],
        landcover_bounds: tuple[float, float, float, float],
    ) -> tuple[dict[str, Any], list[str]]:
        layers = self._layer_queries(
            south, west, north, east, detail_bounds, landcover_bounds
        )
        merged: dict[tuple[str, int | str], dict[str, Any]] = {}
        notes: list[str] = ["cached layer-ready OSM data"]
        successful_layers = 0

        for layer_name, query, reduced_query in layers:
            payload = None
            try:
                payload = self._fetch(query)
            except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
                if reduced_query is not None:
                    try:
                        payload = self._fetch(reduced_query)
                        notes.append(f"{layer_name} limited to local detail area")
                    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as reduced_error:
                        notes.append(f"{layer_name} unavailable")
                        error = reduced_error
                else:
                    notes.append(f"{layer_name} unavailable")
                if payload is None:
                    continue

            successful_layers += 1
            for element in self._merge_elements((payload,)):
                key = (str(element.get("type", "object")), element.get("id", id(element)))
                merged[key] = element

        if successful_layers == 0:
            raise OSError("All OpenStreetMap data layers failed")
        return {"elements": list(merged.values())}, notes

    @staticmethod
    def _combined_query(layers: tuple[tuple[str, str, str | None], ...]) -> str:
        selectors: list[str] = []
        for _, query, _ in layers:
            start = query.find("(\n")
            end = query.rfind("\n);")
            if start >= 0 and end > start:
                selectors.append(query[start + 2:end])
        return "[out:json][timeout:25];\n(\n" + "\n".join(selectors) + "\n);\nout body geom;"

    @staticmethod
    def _merge_elements(payloads: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
        merged: dict[tuple[str, int | str], dict[str, Any]] = {}
        for payload in payloads:
            for element in payload.get("elements", ()):
                if not isinstance(element, dict):
                    continue
                key = (str(element.get("type", "object")), element.get("id", id(element)))
                previous = merged.get(key)
                if previous is None or len(element.get("geometry", ())) > len(previous.get("geometry", ())):
                    merged[key] = element
        return list(merged.values())

    @staticmethod
    def _layer_queries(
        south: float,
        west: float,
        north: float,
        east: float,
        detail_bounds: tuple[float, float, float, float],
        landcover_bounds: tuple[float, float, float, float],
    ) -> tuple[tuple[str, str, str | None], ...]:
        bbox = f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"
        detail_bbox = ",".join(f"{value:.7f}" for value in detail_bounds)
        landcover_bbox = ",".join(f"{value:.7f}" for value in landcover_bounds)
        terrain = f"""[out:json][timeout:20];
(
  way[\"natural\"=\"coastline\"]({bbox});
  way[\"natural\"=\"land\"]({bbox});
  way[\"place\"=\"islet\"]({bbox});
  way[\"natural\"=\"water\"]({landcover_bbox});
  way[\"waterway\"=\"riverbank\"]({landcover_bbox});
  way[\"water\"=\"basin\"]({bbox});
  way[\"waterway\"=\"dock\"]({bbox});
  way[\"natural\"=\"wood\"]({landcover_bbox});
  way[\"natural\"~\"scrub|heath|grassland|beach\"]({landcover_bbox});
  way[\"landuse\"~\"industrial|port|harbour|residential|commercial|retail|forest|grass|meadow|recreation_ground|farmland|farmyard|orchard|vineyard\"]({landcover_bbox});
  way[\"leisure\"=\"park\"]({landcover_bbox});
);
out body geom;"""
        reduced_terrain = terrain.replace(f"({bbox})", f"({detail_bbox})").replace(
            f"({landcover_bbox})", f"({detail_bbox})"
        )
        water_relations = f"""[out:json][timeout:20];
(
  relation["natural"="water"]({landcover_bbox});
  relation["water"="basin"]({landcover_bbox});
  relation["waterway"~"dock|riverbank"]({landcover_bbox});
);
out body geom;"""
        reduced_water_relations = water_relations.replace(
            f"({landcover_bbox})", f"({detail_bbox})"
        )
        navigation = f"""[out:json][timeout:20];
(
  node[\"seamark:type\"]({bbox});
  way[\"seamark:type\"]({bbox});
);
out body geom;"""
        infrastructure = f"""[out:json][timeout:20];
(
  way[\"highway\"~\"motorway|trunk|primary|secondary|tertiary|residential|unclassified\"]({detail_bbox});
  way[\"bridge\"]({detail_bbox});
  way[\"building\"]({detail_bbox});
  way[\"man_made\"~\"quay|pier|breakwater|crane|lighthouse|storage_tank\"]({detail_bbox});
  node[\"man_made\"~\"crane|lighthouse\"]({detail_bbox});
);
out body geom;"""
        return (
            ("terrain", terrain, reduced_terrain),
            ("water surface mask", water_relations, reduced_water_relations),
            ("navigation marks", navigation, None),
            ("local infrastructure", infrastructure, None),
        )

    def _write_cache(self, path: Path, scene: GeographicScene) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=self.cache_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(scene.to_dict(), handle, ensure_ascii=True, separators=(",", ":"))
            os.replace(temporary_name, path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def parse_overpass_scene(
    payload: dict[str, Any],
    *,
    scene_id: str,
    reference_latitude_deg: float,
    reference_longitude_deg: float,
) -> GeographicScene:
    polygons: list[GeoPolygon] = []
    lines: list[GeoLine] = []
    objects: list[GeoObject] = []
    for element in payload.get("elements", ()):
        if not isinstance(element, dict):
            continue
        tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
        element_id = f"{element.get('type', 'object')}-{element.get('id', len(polygons) + len(lines) + len(objects))}"
        source = "OpenSeaMap" if "seamark:type" in tags else "OpenStreetMap"
        name = str(tags.get("name", tags.get("seamark:name", "")))
        coordinates = _coordinates(element)

        if element.get("type") == "relation" and _is_water_feature(tags):
            kind = _water_kind(tags)
            for ring_index, ring in enumerate(_relation_outer_rings(element)):
                polygons.append(GeoPolygon(
                    f"{element_id}-outer-{ring_index}",
                    kind,
                    tuple(ring),
                    0.2,
                    source,
                    name,
                ))
            continue

        if "seamark:type" in tags or tags.get("man_made") == "lighthouse":
            point = _representative_point(element, coordinates)
            if point is not None:
                kind = str(tags.get("seamark:type", "lighthouse")).replace("_", "-")
                objects.append(GeoObject(
                    feature_id=element_id,
                    kind=kind,
                    latitude_deg=point[0],
                    longitude_deg=point[1],
                    source=source,
                    name=name,
                    colour=str(tags.get("seamark:buoy_lateral:colour", tags.get("seamark:light:colour", tags.get("colour", "")))),
                    shape=str(tags.get("seamark:buoy_lateral:shape", tags.get("seamark:buoy_cardinal:shape", tags.get("shape", "")))),
                    light_character=_light_character(tags),
                ))
            continue

        man_made = str(tags.get("man_made", ""))
        if man_made == "crane":
            point = _representative_point(element, coordinates)
            if point is not None:
                objects.append(GeoObject(element_id, "crane", point[0], point[1], source, name))
            continue

        closed = len(coordinates) >= 4 and coordinates[0] == coordinates[-1]
        if "building" in tags and closed:
            polygons.append(GeoPolygon(
                element_id,
                "building",
                tuple(coordinates),
                _height_m(tags, default=9.0),
                source,
                name,
            ))
        elif man_made == "storage_tank" and closed:
            polygons.append(GeoPolygon(
                element_id,
                "storage-tank",
                tuple(coordinates),
                _height_m(tags, default=12.0),
                source,
                name,
            ))
        elif (
            tags.get("natural") == "land"
            or tags.get("place") == "islet"
            or tags.get("landuse") in {
                "industrial", "port", "harbour", "residential", "commercial",
                "retail", "farmland", "farmyard", "orchard", "vineyard",
            }
        ) and closed:
            landuse = str(tags.get("landuse", ""))
            if landuse in {"industrial", "port", "harbour"}:
                kind = "industrial"
            elif landuse in {"residential"}:
                kind = "urban"
            elif landuse in {"commercial", "retail"}:
                kind = "commercial"
            elif landuse in {"farmland", "farmyard", "orchard", "vineyard"}:
                kind = "farmland"
            else:
                kind = "land"
            polygons.append(GeoPolygon(element_id, kind, tuple(coordinates), 1.4, source, name))
        elif (
            tags.get("natural") in {"wood", "scrub", "heath", "grassland", "beach"}
            or tags.get("landuse") in {"forest", "grass", "meadow", "recreation_ground"}
            or tags.get("leisure") == "park"
        ) and closed:
            natural = str(tags.get("natural", ""))
            kind = "forest" if natural in {"wood", "scrub"} or tags.get("landuse") == "forest" else "grass"
            if natural == "beach":
                kind = "beach"
            polygons.append(GeoPolygon(element_id, kind, tuple(coordinates), 1.2, source, name))
        elif _is_water_feature(tags) and closed:
            kind = _water_kind(tags)
            polygons.append(GeoPolygon(element_id, kind, tuple(coordinates), 0.2, source, name))
        elif man_made in {"pier", "quay", "breakwater"}:
            if closed and man_made == "pier":
                polygons.append(GeoPolygon(element_id, man_made, tuple(coordinates), 1.5, source, name))
            elif len(coordinates) >= 2:
                width = 12.0 if man_made == "breakwater" else 7.0
                lines.append(GeoLine(element_id, man_made, tuple(coordinates), width, 2.0, source, name))
        elif tags.get("natural") == "coastline" and len(coordinates) >= 2:
            lines.append(GeoLine(element_id, "coastline", tuple(coordinates), 45.0, 0.8, source, name))
        elif str(tags.get("bridge", "")).lower() not in {"", "no"} and len(coordinates) >= 2:
            lines.append(GeoLine(
                element_id,
                "bridge",
                tuple(coordinates),
                _road_width_m(tags),
                5.5,
                source,
                name,
            ))
        elif "highway" in tags and len(coordinates) >= 2:
            kind = "bridge" if str(tags.get("bridge", "")).lower() not in {"", "no"} else "road"
            lines.append(GeoLine(
                element_id,
                kind,
                tuple(coordinates),
                _road_width_m(tags),
                5.5 if kind == "bridge" else 0.35,
                source,
                name,
            ))

    status = (
        f"Live OSM/OpenSeaMap: {len(polygons)} areas, {len(lines)} lines, {len(objects)} objects"
    )
    return GeographicScene(
        scene_id=scene_id,
        reference_latitude_deg=reference_latitude_deg,
        reference_longitude_deg=reference_longitude_deg,
        polygons=tuple(polygons),
        lines=tuple(lines),
        objects=tuple(objects),
        source_status=status,
    )


def _coordinates(element: dict[str, Any]) -> list[tuple[float, float]]:
    result = []
    for point in element.get("geometry", ()):
        if not isinstance(point, dict) or "lat" not in point or "lon" not in point:
            continue
        result.append((float(point["lat"]), float(point["lon"])))
    return result


def _is_water_feature(tags: dict[str, Any]) -> bool:
    return (
        tags.get("natural") == "water"
        or tags.get("water") == "basin"
        or tags.get("waterway") in {"dock", "riverbank"}
    )


def _water_kind(tags: dict[str, Any]) -> str:
    if tags.get("water") == "basin" or tags.get("waterway") == "dock":
        return "harbour-basin"
    return "water-area"


def _relation_outer_rings(element: dict[str, Any]) -> list[list[tuple[float, float]]]:
    segments = []
    for member in element.get("members", ()):
        if not isinstance(member, dict) or member.get("type") != "way":
            continue
        if str(member.get("role", "outer")) not in {"", "outer"}:
            continue
        coordinates = _coordinates(member)
        if len(coordinates) >= 2:
            segments.append(coordinates)

    rings: list[list[tuple[float, float]]] = []
    while segments:
        ring = segments.pop(0)
        connected = True
        while not _points_match(ring[0], ring[-1]) and connected:
            connected = False
            for index, segment in enumerate(segments):
                if _points_match(ring[-1], segment[0]):
                    ring.extend(segment[1:])
                elif _points_match(ring[-1], segment[-1]):
                    ring.extend(reversed(segment[:-1]))
                elif _points_match(ring[0], segment[-1]):
                    ring = segment[:-1] + ring
                elif _points_match(ring[0], segment[0]):
                    ring = list(reversed(segment[1:])) + ring
                else:
                    continue
                segments.pop(index)
                connected = True
                break
        if len(ring) >= 4 and _points_match(ring[0], ring[-1]):
            ring[-1] = ring[0]
            rings.append(ring)
    return rings


def _points_match(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return abs(left[0] - right[0]) <= 1e-7 and abs(left[1] - right[1]) <= 1e-7


def _representative_point(
    element: dict[str, Any], coordinates: list[tuple[float, float]]
) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    if not coordinates:
        return None
    return (
        sum(point[0] for point in coordinates) / len(coordinates),
        sum(point[1] for point in coordinates) / len(coordinates),
    )


def _height_m(tags: dict[str, Any], *, default: float) -> float:
    height = _number(tags.get("height"))
    if height is not None:
        return min(250.0, max(2.0, height))
    levels = _number(tags.get("building:levels"))
    return min(250.0, max(2.0, levels * 3.2)) if levels is not None else default


def _road_width_m(tags: dict[str, Any]) -> float:
    explicit = _number(tags.get("width"))
    if explicit is not None:
        return min(32.0, max(2.5, explicit))
    lanes = _number(tags.get("lanes"))
    if lanes is not None:
        return min(32.0, max(3.2, lanes * 3.2))
    highway = str(tags.get("highway", ""))
    if highway in {"motorway", "trunk", "primary"}:
        return 9.0
    if highway in {"secondary", "tertiary"}:
        return 7.0
    return 4.5


def _number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def _light_character(tags: dict[str, Any]) -> str:
    character = str(tags.get("seamark:light:character", ""))
    period = str(tags.get("seamark:light:period", ""))
    group = str(tags.get("seamark:light:group", ""))
    result = character
    if group:
        result += f"({group})"
    if period:
        result += f" {period}s"
    return result.strip()
