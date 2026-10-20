# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


Coordinate = tuple[float, float]


@dataclass(frozen=True, slots=True)
class VisualSettings:
    camera_height_m: float = 15.0
    camera_longitudinal_m: float = 0.0
    camera_lateral_m: float = 0.0
    camera_pitch_deg: float = 0.0
    camera_roll_deg: float = 0.0
    waterline_height_m: float | None = None
    horizontal_fov_deg: float = 75.0
    visibility_nm: float = 12.0
    sea_state: float | None = None
    cloud_cover_percent: float = 35.0
    draw_distance_nm: float = 12.0
    show_own_ship_bow: bool = True


@dataclass(frozen=True, slots=True)
class GeoPolygon:
    feature_id: str
    kind: str
    coordinates: tuple[Coordinate, ...]
    height_m: float = 1.0
    source: str = "OpenStreetMap"
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.feature_id,
            "kind": self.kind,
            "coordinates": [list(point) for point in self.coordinates],
            "height_m": self.height_m,
            "source": self.source,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeoPolygon":
        return cls(
            feature_id=str(data["id"]),
            kind=str(data["kind"]),
            coordinates=tuple((float(lat), float(lon)) for lat, lon in data["coordinates"]),
            height_m=float(data.get("height_m", 1.0)),
            source=str(data.get("source", "OpenStreetMap")),
            name=str(data.get("name", "")),
        )


@dataclass(frozen=True, slots=True)
class GeoLine:
    feature_id: str
    kind: str
    coordinates: tuple[Coordinate, ...]
    width_m: float = 4.0
    height_m: float = 1.0
    source: str = "OpenStreetMap"
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.feature_id,
            "kind": self.kind,
            "coordinates": [list(point) for point in self.coordinates],
            "width_m": self.width_m,
            "height_m": self.height_m,
            "source": self.source,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeoLine":
        return cls(
            feature_id=str(data["id"]),
            kind=str(data["kind"]),
            coordinates=tuple((float(lat), float(lon)) for lat, lon in data["coordinates"]),
            width_m=float(data.get("width_m", 4.0)),
            height_m=float(data.get("height_m", 1.0)),
            source=str(data.get("source", "OpenStreetMap")),
            name=str(data.get("name", "")),
        )


@dataclass(frozen=True, slots=True)
class GeoObject:
    feature_id: str
    kind: str
    latitude_deg: float
    longitude_deg: float
    source: str
    name: str = ""
    colour: str = ""
    shape: str = ""
    light_character: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.feature_id,
            "kind": self.kind,
            "lat": self.latitude_deg,
            "lon": self.longitude_deg,
            "source": self.source,
            "name": self.name,
            "colour": self.colour,
            "shape": self.shape,
            "light_character": self.light_character,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeoObject":
        return cls(
            feature_id=str(data["id"]),
            kind=str(data["kind"]),
            latitude_deg=float(data["lat"]),
            longitude_deg=float(data["lon"]),
            source=str(data.get("source", "OpenStreetMap")),
            name=str(data.get("name", "")),
            colour=str(data.get("colour", "")),
            shape=str(data.get("shape", "")),
            light_character=str(data.get("light_character", "")),
        )


@dataclass(frozen=True, slots=True)
class GeographicScene:
    scene_id: str
    reference_latitude_deg: float
    reference_longitude_deg: float
    polygons: tuple[GeoPolygon, ...] = ()
    lines: tuple[GeoLine, ...] = ()
    objects: tuple[GeoObject, ...] = ()
    source_status: str = "Water-only fallback"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "reference": {
                "lat": self.reference_latitude_deg,
                "lon": self.reference_longitude_deg,
            },
            "polygons": [feature.to_dict() for feature in self.polygons],
            "lines": [feature.to_dict() for feature in self.lines],
            "objects": [feature.to_dict() for feature in self.objects],
            "source_status": self.source_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeographicScene":
        reference = data.get("reference", {})
        return cls(
            scene_id=str(data["scene_id"]),
            reference_latitude_deg=float(reference["lat"]),
            reference_longitude_deg=float(reference["lon"]),
            polygons=tuple(GeoPolygon.from_dict(item) for item in data.get("polygons", ())),
            lines=tuple(GeoLine.from_dict(item) for item in data.get("lines", ())),
            objects=tuple(GeoObject.from_dict(item) for item in data.get("objects", ())),
            source_status=str(data.get("source_status", "Cached geographic data")),
        )
