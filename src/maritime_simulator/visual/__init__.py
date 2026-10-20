# SPDX-License-Identifier: MPL-2.0
"""Optional snapshot-driven visual simulator support."""

from .coordinates import LocalFrame, local_offset_m, range_bearing_m
from .geodata import OverpassGeodataProvider
from .models import GeoLine, GeoObject, GeoPolygon, GeographicScene, VisualSettings
from .quality import (
    AdaptiveVisualQualityController,
    VISUAL_QUALITY_MODES,
    VISUAL_QUALITY_PROFILES,
    VisualQualityProfile,
    normalize_visual_quality_mode,
)
from .world import VisualWorldManager

__all__ = [
    "GeoLine",
    "GeoObject",
    "GeoPolygon",
    "GeographicScene",
    "LocalFrame",
    "OverpassGeodataProvider",
    "AdaptiveVisualQualityController",
    "VISUAL_QUALITY_MODES",
    "VISUAL_QUALITY_PROFILES",
    "VisualSettings",
    "VisualQualityProfile",
    "VisualWorldManager",
    "local_offset_m",
    "range_bearing_m",
    "normalize_visual_quality_mode",
]
