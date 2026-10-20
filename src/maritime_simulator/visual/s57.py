# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from contextlib import closing, ExitStack
from collections import OrderedDict
import csv
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
from threading import RLock
import time
from urllib import request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile

# Initialize the geometry package before UI and chart workers can import it concurrently.
from shapely.geometry import GeometryCollection, box

from .geodata import verified_ssl_context


CATALOG_URL = "https://www.charts.noaa.gov/ENCs/ENCProdCat.xml"
MAX_DOWNLOAD = 128 * 1024**2
MAX_IMPORT_BYTES = 512 * 1024**2
MAX_CELLS = 20_000
MAX_SOURCE_FILES = 250_000
MAX_FEATURES = 500_000
MAX_LAYER_SELECTION = 512
DISPLAY_LIMIT = 8000
DISPLAY_SECONDS = 2.0
COVERAGE_CACHE_BYTES = 8 * 1024**2
COVERAGE_INPUT_BYTES = 4 * 1024**2
COVERAGE_FEATURE_LIMIT = 4096
ATTRIBUTE_NAMES = ("OBJNAM", "INFORM", "DRVAL1", "DRVAL2", "VALDCO", "DEPTH", "VALSOU", "WATLEV",
                   "COLOUR", "COLPAT", "BOYSHP", "BCNSHP", "CATLAM", "CATCAM", "TOPSHP",
                   "CATLIT", "LITCHR", "SIGGRP", "SIGPER", "HEIGHT", "VALNMR", "SCAMIN",
                   "ORIENT", "TRAFIC", "RESTRN", "CATREA", "CATACH", "CATMPA", "CATFIF",
                   "CATCBL", "CATPIP", "VERCLR", "VERCSA", "VERCCL", "VERCOP",
                   "SECTR1", "SECTR2", "LITVIS", "CATWRK", "CATOBS", "EXPSOU", "QUASOU",
                   "CATLMK", "FUNCTN", "CATMOR", "CATSIL", "CONVIS", "CATPYL", "CATCRN",
                   "CATHAF", "ELEVAT", "CATVEG", "BUISHP", "CATFNC", "CATSCF", "CATDIS",
                   "CATSIW", "CATSIT", "CATWAT", "NATSUR", "NATQUA", "CURVEL")
SAFETY_CONTOUR_M = 30.0
HAZARD_LAYERS = frozenset({"WRECKS", "UWTROC", "OBSTRN"})


def layer_selection(value):
    """One contract for settings, exported profiles and viewport requests."""
    if value is None:
        return None
    if (not isinstance(value, (list, tuple)) or len(value) > MAX_LAYER_SELECTION
            or any(not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,16}", code) for code in value)):
        raise ValueError("Invalid S57 layer selection")
    return tuple(sorted(set(value)))


def safety_value(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("S57 safety depth/contour must be between 0 and 1000 m") from None
    if not math.isfinite(value) or not 0 <= value <= 1000:
        raise ValueError("S57 safety depth/contour must be between 0 and 1000 m")
    return round(value, 1)
# IMO MSC.232(82), Appendix 2: background/coast, safety contour and isolated dangers.
# Aids, routing systems and soundings belong to Standard/Other, not Display Base.
BASE_LAYERS = frozenset({"LNDARE", "COALNE", "SLCONS", "DEPARE", "DRGARE", "DEPCNT",
                         "OBSTRN", "UWTROC", "WRECKS", "BRIDGE", "CBLOHD", "PIPOHD",
                         "CONVYR", "OFSPLF", "PILPNT", "BOYISD", "BCNISD"})


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError("S57 operation cancelled; existing charts were kept")


def noaa_url(url):
    parsed = urlsplit(str(url))
    if (parsed.scheme != "https" or parsed.hostname not in {"charts.noaa.gov", "www.charts.noaa.gov"}
            or parsed.port not in {None, 443} or parsed.username or parsed.password
            or not re.fullmatch(r"/ENCs/(?:US[A-Z0-9]{6}\.zip|ENCProdCat\.xml)", parsed.path)
            or parsed.query or parsed.fragment):
        raise ValueError("Only official NOAA ENC download addresses are accepted")
    return str(url)


def download(url, target, *, cancel=None, progress=None, limit=MAX_DOWNLOAD, client=None):
    if client is not None:
        return client.download(url, target, cancel=cancel, progress=progress, limit=limit)
    req = request.Request(noaa_url(url), headers={"User-Agent": "Maritime-Ship-Simulator/ENC"})
    with request.urlopen(req, timeout=15, context=verified_ssl_context()) as response:
        noaa_url(response.geturl())
        total = int(response.headers.get("Content-Length", 0))
        if total > limit:
            raise ValueError("NOAA download exceeds the safety size limit")
        size = 0
        with Path(target).open("wb") as writer:
            while True:
                check_cancel(cancel)
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ValueError("NOAA download exceeds the safety size limit")
                writer.write(chunk)
                if progress:
                    progress(size, total, "Downloading NOAA")
        if total and size != total:
            raise ValueError("Incomplete NOAA download; please retry")


def parse_catalog(path, *, cancel=None):
    records = []
    for _, cell in ET.iterparse(path, events=("end",)):
        if cell.tag != "cell":
            continue
        check_cancel(cancel)
        try:
            if cell.findtext("status", "").lower() != "active":
                continue
            code = cell.findtext("name", "")
            if not re.fullmatch(r"US[A-Z0-9]{6}", code):
                continue
            url = noaa_url(cell.findtext("zipfile_location", ""))
            points = [(float(v.findtext("long")), float(v.findtext("lat"))) for v in cell.findall("cov/panel/vertex")]
            points = [(lon, lat) for lon, lat in points if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90]
            bounds = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)] if points else None
            records.append(dict(code=code, name=cell.findtext("lname", code), url=url,
                                scale=int(cell.findtext("cscale", "0")), date=cell.findtext("isdt", ""),
                                edition=cell.findtext("edtn", ""), update=cell.findtext("updn", ""),
                                size=int(cell.findtext("zipfile_size", "0")), bounds=bounds))
        except (ValueError, TypeError):
            continue
        finally:
            cell.clear()
    if not records:
        raise ValueError("NOAA returned no usable active ENC cells")
    return records


def load_catalog(directory, *, refresh=False, cancel=None, progress=None):
    check_cancel(cancel)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / "noaa-catalog.json"
    if cache.is_file() and not refresh and cache.stat().st_size < 8 * 1024**2:
        try:
            records = json.loads(cache.read_text(encoding="utf-8"))
            required = {"code", "name", "url", "scale", "date", "edition", "update", "size", "bounds"}
            if isinstance(records, list) and 0 < len(records) <= 20_000 and all(
                isinstance(row, dict) and required <= row.keys()
                and re.fullmatch(r"US[A-Z0-9]{6}", row["code"])
                and noaa_url(row["url"]).endswith("/" + row["code"] + ".zip")
                and all(isinstance(row[key], str) for key in ("name", "date", "edition", "update"))
                and all(isinstance(row[key], int) and row[key] >= 0 for key in ("scale", "size"))
                for row in records
            ):
                return records
        except (ValueError, KeyError, TypeError):
            pass
    with tempfile.TemporaryDirectory(prefix="noaa-catalog-", dir=directory) as work:
        xml = Path(work) / "catalog.xml"
        download(CATALOG_URL, xml, cancel=cancel, progress=progress, limit=32 * 1024**2)
        records = parse_catalog(xml, cancel=cancel)
        temporary = Path(work) / "catalog.json"
        temporary.write_text(json.dumps(records), encoding="utf-8")
        check_cancel(cancel)
        temporary.replace(cache)
    return records


@lru_cache(maxsize=1)
def layer_descriptions():
    import pyogrio
    with (Path(pyogrio.get_gdal_data_path()) / "s57objectclasses.csv").open(encoding="utf-8") as reader:
        return {row["Acronym"]: row["ObjectClass"] for row in csv.DictReader(reader)}


def _import_sources(sources, cancel):
    seen = set()
    codes = set()
    for source in sources:
        source = Path(source)
        check_cancel(cancel)
        if not source.exists():
            raise ValueError(f"S57 source is unavailable: {source}. Reconnect the drive or choose an existing file/folder.")
        if source.is_symlink():
            raise ValueError("Choose an actual S57 file/folder, not a symbolic link")
        if not source.is_dir():
            candidates = [source]
        else:
            candidates, examined, compiled = [], 0, False
            def scan_error(error):
                raise ValueError(f"Cannot read S57 folder: {error}") from error
            for directory, folders, files in os.walk(source, onerror=scan_error, followlinks=False):
                check_cancel(cancel)
                folders[:] = sorted(name for name in folders if not name.startswith(".") and name != "__MACOSX"
                                     and not (Path(directory) / name).is_symlink())
                for name in sorted(files):
                    examined += 1
                    if examined > MAX_SOURCE_FILES:
                        raise ValueError("S57 folder contains too many files; choose a smaller chart folder")
                    path = Path(directory) / name
                    if name.startswith(".") or path.is_symlink():
                        continue
                    compiled |= path.suffix.lower() in {".7cb", ".7cr"}
                    if path.suffix.lower() in {".000", ".zip"}:
                        candidates.append(path)
            if not candidates:
                explanation = (" SevenCs .7CB/.7CR compiled charts are not S57 exchange cells; obtain the original unencrypted .000 exchange set."
                               if compiled else " Choose a folder containing unencrypted .000 cells or exchange-set ZIPs.")
                raise ValueError("No importable S57 files found in the selected folder." + explanation)
            # NOAA's full download is often kept beside its unpacked ENC_ROOT.
            # Prefer unpacked cells in a folder; explicit file selections stay strict.
            loose_codes = {path.stem.upper() for path in candidates if path.suffix == ".000"}
            filtered = []
            for path in candidates:
                if path.suffix.lower() == ".zip":
                    with zipfile.ZipFile(path) as archive:
                        bases = [item for item in _archive_members(archive) if PurePosixPath(item.filename).suffix == ".000"]
                        archive_codes = [PurePosixPath(item.filename).stem.upper() for item in bases]
                        if len(set(archive_codes)) != len(archive_codes):
                            raise ValueError("Duplicate S57 cell in ZIP; choose one edition/exchange set per cell")
                        if not bases or set(archive_codes) <= loose_codes:
                            continue
                filtered.append(path)
            candidates = filtered
        for path in candidates:
            check_cancel(cancel)
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                if path.suffix == ".000":
                    cell_codes = [path.stem.upper()]
                elif path.suffix.lower() == ".zip":
                    with zipfile.ZipFile(path) as archive:
                        cell_codes = [PurePosixPath(item.filename).stem.upper() for item in _archive_members(archive)
                                      if PurePosixPath(item.filename).suffix == ".000"]
                else:
                    cell_codes = []
                for code in cell_codes:
                    if code in codes:
                        raise ValueError(f"Duplicate S57 cell {code}; choose one edition/exchange set per cell")
                    codes.add(code)
                    if len(codes) > MAX_CELLS:
                        raise ValueError(f"Choose up to {MAX_CELLS:,} S57 cells per import")
                yield path


@dataclass(frozen=True)
class _ImportCell:
    source: Path
    code: str
    files: tuple


def _archive_members(archive):
    if len(archive.infolist()) > MAX_SOURCE_FILES:
        raise ValueError("Too many files in the S57 exchange set")
    members = []
    for item in archive.infolist():
        # ZipInfo normalizes backslashes on Windows and truncates NULs; validate the raw name.
        name = item.orig_filename
        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name
                or any(":" in part for part in path.parts) or stat.S_ISLNK(item.external_attr >> 16)):
            raise ValueError("Unsafe path in S57 exchange set")
        if (not item.is_dir() and re.fullmatch(r"\.[0-9]{3}", path.suffix)
                and not any(part.startswith(".") or part == "__MACOSX" for part in path.parts)):
            members.append(item)
    return members


def plan_import_cells(sources, cancel=None):
    cells, siblings = [], {}
    for source in _import_sources(sources, cancel):
        check_cancel(cancel)
        if source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source) as archive:
                groups = {}
                for item in _archive_members(archive):
                    path = PurePosixPath(item.filename)
                    groups.setdefault((path.parent, path.stem), []).append(item)
                for (_parent, stem), files in sorted(groups.items()):
                    if any(PurePosixPath(item.filename).suffix == ".000" for item in files):
                        cells.append(_ImportCell(source, stem.upper(), tuple(files)))
        elif source.suffix == ".000":
            if source.parent not in siblings:
                groups = {}
                for path in source.parent.iterdir():
                    if re.fullmatch(r"\.[0-9]{3}", path.suffix) and not path.name.startswith("."):
                        groups.setdefault(path.stem, []).append(path)
                siblings[source.parent] = groups
            cells.append(_ImportCell(source, source.stem.upper(), tuple(siblings[source.parent][source.stem])))
        else:
            raise ValueError("Choose an unencrypted S57 .000 cell or ZIP exchange set (not S63)")
    if not cells or len(cells) > MAX_CELLS:
        raise ValueError(f"Choose 1 to {MAX_CELLS:,} unencrypted S57 base cells (.000), with their adjacent updates")
    codes = set()
    for cell in cells:
        check_cancel(cancel)
        if not re.fullmatch(r"[A-Z0-9_-]{1,32}", cell.code):
            raise ValueError("Invalid S57 cell identifier")
        if cell.code in codes:
            raise ValueError(f"Duplicate S57 cell {cell.code}; choose one edition/exchange set per cell")
        codes.add(cell.code)
        sizes, updates = [], []
        for file in cell.files:
            if isinstance(file, zipfile.ZipInfo):
                sizes.append(file.file_size)
                updates.append(int(PurePosixPath(file.filename).suffix[1:]))
            else:
                if file.is_symlink():
                    raise ValueError(f"Symbolic link in S57 cell/update set: {file.name}")
                sizes.append(file.stat().st_size)
                updates.append(int(file.suffix[1:]))
        updates.sort()
        if len(updates) != len(set(updates)):
            raise ValueError("Duplicate cell/update file in S57 exchange set")
        if updates != list(range(max(updates) + 1)):
            raise ValueError(f"Missing update file for {cell.code}; import a complete exchange set")
        if sum(sizes) > MAX_IMPORT_BYTES:
            raise ValueError(f"S57 cell {cell.code} exceeds 512 MB including updates")
    return cells


def staged_import_cells(cells, work, cancel=None):
    # Reuse a ZIP handle for its entire catalogue; unpack only the current cell.
    with ExitStack() as stack:
        archive, current_source = None, None
        for cell in cells:
            check_cancel(cancel)
            required = sum(file.file_size if isinstance(file, zipfile.ZipInfo) else file.stat().st_size for file in cell.files)
            if shutil.disk_usage(work).free < required + 128 * 1024**2:
                raise ValueError("Not enough free disk space to stage the next S57 cell; free space and retry. Completed cells were kept.")
            if cell.source != current_source:
                stack.close()
                current_source = cell.source
                archive = stack.enter_context(zipfile.ZipFile(cell.source)) if cell.source.suffix.lower() == ".zip" else None
            destination = Path(work) / "cell"
            destination.mkdir()
            try:
                total = 0
                for file in cell.files:
                    check_cancel(cancel)
                    name = PurePosixPath(file.filename).name if archive else file.name
                    with (archive.open(file) if archive else file.open("rb")) as reader, (destination / name).open("wb") as writer:
                        while chunk := reader.read(256 * 1024):
                            check_cancel(cancel)
                            total += len(chunk)
                            if total > MAX_IMPORT_BYTES:
                                raise ValueError(f"S57 cell {cell.code} exceeds 512 MB including updates")
                            writer.write(chunk)
                yield next(path for path in destination.iterdir() if path.suffix == ".000")
            finally:
                shutil.rmtree(destination)


class _DepthDisplay:
    def __init__(self, db, charts, safety_contour, safety_depth):
        self.safety_contour, self.safety_depth = safety_contour, safety_depth
        # Choose the next deeper contour per cell, never a shallower substitute.
        self.contours = {}
        placeholders = ",".join("?" for _ in charts)
        # Depth-area boundaries also encode available contours; dredged minima and
        # isolated soundings do not. Numeric type checks avoid inventing a zero.
        for attribute, layers in (("VALDCO", "'DEPCNT'"), ("DRVAL1", "'DEPARE'"), ("DRVAL2", "'DEPARE'")):
            field = f"json_extract(properties,'$.{attribute}')"
            query = (f"SELECT chart,min({field}) FROM features WHERE chart IN ({placeholders}) "
                     f"AND layer IN ({layers}) AND json_type(properties,'$.{attribute}') IN ('integer','real') "
                     f"AND {field}>=? GROUP BY chart")
            for chart, contour in db.execute(query, (*charts, safety_contour)):
                self.contours[chart] = min(contour, self.contours.get(chart, math.inf))
        self.water = {}
        self.trees = {}
        self.depth_areas = {}
        self.context_trees = {}

    def annotate(self, props, shape):
        layer, chart = props["layer"], props["chart"]
        if layer in {"DEPARE", "DRGARE"}:
            depth = props.get("DRVAL1")
            contour = self.contours.get(chart, self.safety_contour)
            self.depth_areas.setdefault(chart, []).append(shape)
            if isinstance(depth, (int, float)) and depth >= contour:
                self.water.setdefault(chart, []).append(shape)
            props["base_shallow"] = depth is None or depth < contour
            props["effective_safety_contour_m"] = self.contours.get(chart)
        if layer == "DEPCNT":
            contour = self.contours.get(chart)
            props["safety_contour"] = contour is not None and props.get("VALDCO") == contour
            props["effective_safety_contour_m"] = contour
        if layer == "SOUNDG":
            props["shallow_sounding"] = props.get("DEPTH", math.inf) <= self.safety_depth
        if layer in HAZARD_LAYERS:
            depth = props.get("VALSOU", props.get("DRVAL1"))
            props["dangerous"] = depth is None or depth <= self.safety_depth
            safe_water = self.in_safe_water(chart, shape)
            # EXPSOU=1 with no measured depth is not an isolated shoal in a safe depth area.
            props["isolated_danger"] = bool(safe_water and props["dangerous"]
                and not (depth is None and props.get("EXPSOU") == 1))
            from shapely import STRtree
            if chart not in self.context_trees:
                self.context_trees[chart] = STRtree(self.depth_areas.get(chart, []))
            props["water_context_known"] = bool(len(self.context_trees[chart].query(shape, predicate="intersects")))

    def in_safe_water(self, chart, shape):
        from shapely import STRtree
        if chart not in self.trees:
            self.trees[chart] = STRtree(self.water.get(chart, []))
        return bool(len(self.trees[chart].query(shape, predicate="intersects")))

    def includes(self, props, shape):
        layer = props["layer"]
        if layer in {"LNDARE", "COALNE", "SLCONS"}:
            return True
        if layer in {"DEPARE", "DRGARE"}:
            maximum = props.get("DRVAL2")
            return not isinstance(maximum, (int, float)) or maximum >= 0
        if layer == "DEPCNT":
            return props["safety_contour"]
        if layer not in BASE_LAYERS:
            return False
        if layer in HAZARD_LAYERS and props.get("WATLEV") not in {1, 2}:
            depth = props.get("VALSOU", props.get("DRVAL1"))
            if depth is not None and depth > self.safety_depth:
                return False
            if depth is None and props.get("EXPSOU") == 1:
                return False
        return self.in_safe_water(props["chart"], shape)


class S57Store:
    """Imported ENC geometry, indexed on disk; GDAL runs only during import."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "charts.sqlite3"
        self._coverage_cache = OrderedDict()
        self._coverage_cache_limit = COVERAGE_CACHE_BYTES
        self._coverage_signature = None
        self._coverage_lock = RLock()

    def cache_revision(self):
        signature = []
        for path in (self.path, Path(str(self.path) + "-wal")):
            try:
                info = path.stat()
                signature.append((info.st_size, info.st_mtime_ns) if path == self.path or info.st_size else None)
            except FileNotFoundError:
                signature.append(None)
        return tuple(signature)

    def _coverage_revision(self):
        signature = self.cache_revision()
        with self._coverage_lock:
            if signature != self._coverage_signature:
                self._coverage_cache.clear()
                self._coverage_signature = signature

    def water_surfaces(self, bounds):
        """Raw local surfaces for avoidance, independent of portrayal/SCAMIN settings."""
        from shapely import from_wkb, make_valid, to_wkb, union_all
        if not self.path.is_file():
            return []
        west, south, east, north = bounds
        layers = ('DEPARE', 'DRGARE', 'RIVERS', 'CANALS', 'LAKARE', 'DOCARE', 'LNDARE', 'SLCONS')
        deadline = time.monotonic() + 1.5
        candidates = ("SELECT DISTINCT c.scale,c.code FROM extent e "
                 "CROSS JOIN features f ON f.id=e.id JOIN charts c ON c.code=f.chart "
                 "WHERE e.west<=? AND e.east>=? AND e.south<=? AND e.north>=? AND c.enabled=1 "
                 "AND f.layer IN (" + ','.join('?' for _ in layers) + ")")
        query = ("SELECT f.layer,f.geometry,f.properties FROM features f INDEXED BY features_chart "
                 "CROSS JOIN extent e ON e.id=f.id WHERE f.chart=? "
                 "AND e.west<=? AND e.east>=? AND e.south<=? AND e.north>=? "
                 "AND f.layer IN (" + ','.join('?' for _ in layers) + ") LIMIT 2049")
        viewport = box(west, south, east, north)
        covered = GeometryCollection()
        surfaces, size, count = [], 0, 0
        with closing(self.connect()) as db:
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 2000)
            priority = sorted((scale or math.inf, code) for scale, code in
                              db.execute(candidates, (east, west, north, south, *layers)))
            has_coverage = db.execute("SELECT 1 FROM sqlite_master WHERE name='chart_coverage'").fetchone()
            for _scale, code in priority:
                # Ignore subpixel round-trip slivers at exact tile boundaries.
                if covered.covers(viewport) or viewport.difference(covered).area <= viewport.area * 1e-10:
                    break
                cell_surfaces = []
                for layer, geometry, properties in db.execute(query, (code, east, west, north, south, *layers)):
                    size += len(geometry)
                    count += 1
                    if count > 2048 or size > 16 * 1024**2 or time.monotonic() >= deadline:
                        raise ValueError("S57 water-mask tile exceeds its bounded work budget")
                    shape = from_wkb(geometry)
                    if not shape.is_valid:
                        shape = make_valid(shape)
                    shape = shape.intersection(viewport)
                    if shape.is_empty or shape.geom_type not in {'Polygon', 'MultiPolygon'}:
                        continue
                    water = layer not in {'LNDARE', 'SLCONS'}
                    if layer in {'DEPARE', 'DRGARE'}:
                        depth = json.loads(properties).get('DRVAL2')
                        water = not isinstance(depth, (int, float)) or depth > 0
                    cell_surfaces.append((water, shape))
                stored = db.execute("SELECT geometry FROM chart_coverage WHERE code=?", (code,)).fetchone() if has_coverage else None
                footprint = from_wkb(stored[0]) if stored else union_all([shape for _, shape in cell_surfaces])
                if not footprint.is_valid:
                    footprint = make_valid(footprint)
                footprint = footprint.intersection(viewport)
                region = footprint.difference(covered)
                # Finest available coverage owns the pixel; coarse charts fill gaps only.
                for water, shape in sorted(cell_surfaces, key=lambda item: not item[0]):
                    clipped = shape.intersection(region)
                    if not clipped.is_empty:
                        surfaces.append((water, to_wkb(clipped)))
                covered = covered.union(footprint)
        return surfaces

    def _legacy_coverage(self, db, code, zoom, viewport, tolerance, deadline):
        from shapely import from_wkb, to_wkb, union_all, make_valid
        key = (code, zoom, viewport.bounds)
        with self._coverage_lock:
            if key in self._coverage_cache:
                data = self._coverage_cache[key]
                self._coverage_cache.move_to_end(key)
                return from_wkb(data) if data is not None else None
        # Older indexes lack M_COVR. Only actual surface polygons may hide a coarser cell.
        shapes, size = [], 0
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 2000)
        try:
            west, south, east, north = viewport.bounds
            rows = db.execute("SELECT f.geometry FROM features f INDEXED BY features_chart "
                              "CROSS JOIN extent e ON e.id=f.id WHERE f.chart=? "
                              "AND layer IN ('LNDARE','DEPARE','DRGARE') "
                              "AND e.west<=? AND e.east>=? AND e.south<=? AND e.north>=? LIMIT ?",
                              (code, east, west, north, south, COVERAGE_FEATURE_LIMIT + 1))
            for index, (data,) in enumerate(rows):
                size += len(data)
                if index >= COVERAGE_FEATURE_LIMIT or size > COVERAGE_INPUT_BYTES or time.monotonic() >= deadline:
                    return None
                shape = from_wkb(data)
                if shape.geom_type not in {"Polygon", "MultiPolygon"}:
                    continue
                if not shape.is_valid:
                    shape = make_valid(shape)
                shapes.append(shape.intersection(viewport).simplify(tolerance / 8, preserve_topology=True))
            data = to_wkb(union_all(shapes))
        except sqlite3.OperationalError:
            if time.monotonic() < deadline:
                raise
            return None
        finally:
            db.set_progress_handler(None, 0)
        with self._coverage_lock:
            self._coverage_cache[key] = data
            while len(self._coverage_cache) > 32 or sum(len(v) for v in self._coverage_cache.values() if v is not None) > self._coverage_cache_limit:
                self._coverage_cache.popitem(last=False)
        return from_wkb(data)

    def apply_memory_budget(self, maximum_bytes):
        self._coverage_cache_limit = max(0, int(maximum_bytes))
        if not self._coverage_lock.acquire(blocking=False):
            return {}
        try:
            size = sum(len(v) for v in self._coverage_cache.values() if v is not None)
            while self._coverage_cache and size > self._coverage_cache_limit:
                _, removed = self._coverage_cache.popitem(last=False)
                size -= len(removed) if removed is not None else 0
            return {"coverage": size}
        finally:
            self._coverage_lock.release()

    def connect(self, *, write=False):
        if write:
            self.directory.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=1)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS charts(code TEXT PRIMARY KEY, name TEXT, source TEXT,
                    edition TEXT, update_no TEXT, date TEXT, scale INTEGER, bounds TEXT, enabled INTEGER DEFAULT 1);
                CREATE TABLE IF NOT EXISTS features(id INTEGER PRIMARY KEY, chart TEXT REFERENCES charts(code),
                    layer TEXT, geometry BLOB, properties TEXT);
                CREATE INDEX IF NOT EXISTS features_chart ON features(chart);
                CREATE INDEX IF NOT EXISTS features_contours ON features(chart) WHERE layer='DEPCNT';
                CREATE TABLE IF NOT EXISTS chart_stats(code TEXT PRIMARY KEY REFERENCES charts(code) ON DELETE CASCADE,
                    content_bytes INTEGER, feature_count INTEGER);
                CREATE TABLE IF NOT EXISTS chart_layers(code TEXT REFERENCES charts(code) ON DELETE CASCADE,
                    layer TEXT, feature_count INTEGER, PRIMARY KEY(code,layer));
                CREATE TABLE IF NOT EXISTS chart_coverage(code TEXT PRIMARY KEY REFERENCES charts(code) ON DELETE CASCADE,
                    geometry BLOB);
                CREATE VIRTUAL TABLE IF NOT EXISTS extent USING rtree(id, west, east, south, north);
            """)
            return db
        return sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.5)

    def records(self, code=None):
        if not self.path.is_file():
            return []
        with closing(self.connect()) as db:
            stats = db.execute("SELECT 1 FROM sqlite_master WHERE name='chart_stats'").fetchone()
            query = ("SELECT c.*,s.content_bytes,s.feature_count FROM charts c LEFT JOIN chart_stats s ON c.code=s.code"
                     if stats else "SELECT c.*,NULL,NULL FROM charts c")
            query += " WHERE c.code=?" if code is not None else ""
            query += " ORDER BY c.scale DESC,c.code"
            return [dict(code=r[0], name=r[1], source=r[2], edition=r[3], update=r[4], date=r[5],
                         scale=r[6], bounds=json.loads(r[7]), enabled=bool(r[8]), content_bytes=r[9], feature_count=r[10])
                    for r in db.execute(query, (code,) if code is not None else ())]

    def layers(self):
        if not self.path.is_file():
            return {}
        with closing(self.connect()) as db:
            if not db.execute("SELECT 1 FROM charts WHERE enabled=1 LIMIT 1").fetchone():
                return {}
            has_stats = db.execute("SELECT 1 FROM sqlite_master WHERE name='chart_layers'").fetchone()
            if has_stats and not db.execute(
                "SELECT 1 FROM charts c WHERE enabled=1 AND NOT EXISTS "
                "(SELECT 1 FROM chart_layers l WHERE l.code=c.code) LIMIT 1"
            ).fetchone():
                return dict(db.execute("SELECT layer,sum(feature_count) FROM chart_layers "
                                       "JOIN charts ON chart_layers.code=charts.code WHERE enabled=1 GROUP BY layer ORDER BY layer"))
            # Older indexes have no cheap layer totals; never scan their entire geometry on Qt.
            return {code: None for code in layer_descriptions()
                    if re.fullmatch(r"[A-Za-z0-9_]{1,16}", code) and not code.upper().startswith(("M_", "C_"))}

    def has_enabled_cells(self):
        if not self.path.is_file():
            return False
        with closing(self.connect()) as db:
            return db.execute("SELECT 1 FROM charts WHERE enabled=1 LIMIT 1").fetchone() is not None

    def set_enabled(self, code, enabled):
        with closing(self.connect(write=True)) as db, db:
            db.execute("UPDATE charts SET enabled=? WHERE code=?", (int(enabled), code))

    def set_enabled_many(self, codes, enabled, *, cancel=None):
        with closing(self.connect(write=True)) as db, db:
            for code in codes:
                check_cancel(cancel)
                db.execute("UPDATE charts SET enabled=? WHERE code=? AND enabled!=?", (int(enabled), code, int(enabled)))
            check_cancel(cancel)

    def enabled_cells(self):
        if not self.path.is_file():
            return ()
        with closing(self.connect()) as db:
            return tuple(row[0] for row in db.execute("SELECT code FROM charts WHERE enabled=1 ORDER BY code"))

    def set_enabled_cells(self, codes):
        if not self.path.is_file():
            return
        selected = set(codes)
        with closing(self.connect(write=True)) as db, db:
            updates = [(int(code in selected), code) for code, enabled in db.execute("SELECT code,enabled FROM charts")
                       if bool(enabled) != (code in selected)]
            db.executemany("UPDATE charts SET enabled=? WHERE code=?", updates)

    def delete(self, codes, *, cancel=None):
        with closing(self.connect(write=True)) as db, db:
            for code in codes:
                check_cancel(cancel)
                self._remove_cell(db, code)
                db.execute("DELETE FROM charts WHERE code=?", (code,))
            check_cancel(cancel)

    @staticmethod
    def _remove_cell(db, code):
        db.execute("DELETE FROM extent WHERE id IN (SELECT id FROM features WHERE chart=?)", (code,))
        db.execute("DELETE FROM features WHERE chart=?", (code,))
        db.execute("DELETE FROM chart_stats WHERE code=?", (code,))
        db.execute("DELETE FROM chart_layers WHERE code=?", (code,))
        db.execute("DELETE FROM chart_coverage WHERE code=?", (code,))

    def import_files(self, sources, *, cancel=None, progress=None, names=None, origin="Local import"):
        import pyogrio
        from pyogrio.raw import read
        from shapely import from_wkb, to_wkb
        if "S57" not in pyogrio.list_drivers():
            raise ValueError("The bundled GDAL library has no S57 driver; reinstall pyogrio")
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="enc-import-", dir=self.directory) as work:
            if progress:
                progress(0, 0, "Scanning S57 files and folders...")
            cells = plan_import_cells(sources, cancel)
            with closing(self.connect(write=True)) as db, db, closing(staged_import_cells(cells, Path(work), cancel)) as staged:
                for cell_index, cell in enumerate(staged):
                    code = cell.stem.upper()
                    if not re.fullmatch(r"[A-Z0-9_-]{1,32}", code):
                        raise ValueError("Invalid S57 cell identifier")
                    check_cancel(cancel)
                    info, _, _, values = read(cell, layer="DSID", read_geometry=False, UPDATES="APPLY")
                    meta = {key: value[0] for key, value in zip(info["fields"], values) if len(value)}
                    self._remove_cell(db, code)
                    # Reject accidental rollback of an already imported cell.
                    edition = str(meta.get("DSID_EDTN", ""))
                    update = str(meta.get("DSID_UPDN", ""))
                    old = db.execute("SELECT edition,update_no,enabled FROM charts WHERE code=?", (code,)).fetchone()
                    if old and edition.isdigit() and update.isdigit() and old[0].isdigit() and old[1].isdigit() and (int(edition), int(update)) < (int(old[0]), int(old[1])):
                        raise ValueError(f"{code}: the imported cell is older than the installed edition/update")
                    db.execute("INSERT OR REPLACE INTO charts VALUES(?,?,?,?,?,?,?,?,?)",
                               (code, (names or {}).get(code, code), origin, edition, update,
                                str(meta.get("DSID_ISDT", "")), int(meta.get("DSPM_CSCL", 0)), "[]", old[2] if old else 1))
                    count, content_bytes, bounds = 0, 0, [180., 90., -180., -90.]
                    layer_counts = {}
                    coverage, excluded = [], []
                    for layer, geometry_type in pyogrio.list_layers(cell):
                        if layer == "M_COVR" and geometry_type:
                            info, _, geometries, values = read(cell, layer=layer, UPDATES="APPLY")
                            categories = next((column for key, column in zip(info["fields"], values) if key == "CATCOV"), ())
                            for wkb, category in zip(geometries if geometries is not None else (), categories):
                                if wkb is None or category not in (1, 2):
                                    continue
                                shape = from_wkb(wkb)
                                if not shape.is_valid:
                                    from shapely import make_valid
                                    shape = make_valid(shape)
                                (coverage if category == 1 else excluded).append(shape)
                            continue
                        if not geometry_type or layer == "DSID" or layer.startswith("M_"):
                            continue
                        offset = 0
                        while True:
                            check_cancel(cancel)
                            info, _, geometries, values = read(cell, layer=layer, skip_features=offset, max_features=2048,
                                                             UPDATES="APPLY", SPLIT_MULTIPOINT="ON", ADD_SOUNDG_DEPTH="ON")
                            if geometries is None or len(geometries) == 0:
                                break
                            fields = [(str(key), column) for key, column in zip(info["fields"], values) if key in ATTRIBUTE_NAMES]
                            for index, wkb in enumerate(geometries):
                                if wkb is None:
                                    continue
                                shape = from_wkb(wkb)
                                if shape.is_empty:
                                    continue
                                west, south, east, north = shape.bounds
                                if not all(math.isfinite(v) for v in shape.bounds) or west < -180 or east > 180 or south < -90 or north > 90:
                                    raise ValueError(f"Invalid geographic geometry in {code}")
                                properties = {"layer": layer, "chart": code}
                                for key, column in fields:
                                    value = column[index]
                                    if hasattr(value, "tolist"):
                                        value = value.tolist()
                                    if isinstance(value, list):
                                        properties[key] = [v for v in value[:16] if isinstance(v, (str, int, float)) and not (isinstance(v, float) and not math.isfinite(v))]
                                    if isinstance(value, (str, int, float)) and not (isinstance(value, float) and not math.isfinite(value)):
                                        properties[key] = value[:300] if isinstance(value, str) else value
                                geometry, attributes = to_wkb(shape), json.dumps(properties)
                                content_bytes += len(geometry) + len(attributes.encode("utf-8"))
                                fid = db.execute("INSERT INTO features(chart,layer,geometry,properties) VALUES(?,?,?,?)",
                                                 (code, layer, geometry, attributes)).lastrowid
                                db.execute("INSERT INTO extent VALUES(?,?,?,?,?)", (fid, west, east, south, north))
                                bounds = [min(bounds[0], west), min(bounds[1], south), max(bounds[2], east), max(bounds[3], north)]
                                count += 1
                                layer_counts[layer] = layer_counts.get(layer, 0) + 1
                                if count > MAX_FEATURES:
                                    raise ValueError("S57 cell exceeds 500,000 features")
                            offset += len(geometries)
                            if shutil.disk_usage(self.directory).free < 128 * 1024**2:
                                raise ValueError("Not enough free disk space for the S57 chart index; free space or delete unused cells and retry")
                            if progress:
                                progress(cell_index, len(cells), f"Importing {code}: {count:,} features")
                    if not count:
                        raise ValueError(f"{code} has no displayable S57 geometry")
                    db.execute("UPDATE charts SET bounds=? WHERE code=?", (json.dumps(bounds), code))
                    db.execute("INSERT OR REPLACE INTO chart_stats VALUES(?,?,?)", (code, content_bytes, count))
                    db.executemany("INSERT INTO chart_layers VALUES(?,?,?)",
                                   [(code, layer, total) for layer, total in layer_counts.items()])
                    if coverage:
                        from shapely import union_all
                        shape = union_all(coverage).difference(union_all(excluded))
                        db.execute("INSERT INTO chart_coverage VALUES(?,?)", (code, to_wkb(shape)))
                check_cancel(cancel)
        return [cell.code for cell in cells]

    def features(self, bounds, zoom, layers=None, *, base=False,
                 safety_contour=SAFETY_CONTOUR_M, safety_depth=SAFETY_CONTOUR_M):
        from shapely import from_wkb, to_geojson, GEOSException
        west, south, east, north = bounds
        if not all(math.isfinite(v) for v in bounds) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90) or not 0 <= zoom <= 22:
            raise ValueError("Invalid S57 viewport")
        safety_contour, safety_depth = safety_value(safety_contour), safety_value(safety_depth)
        layers = layer_selection(layers)
        result = {"type": "FeatureCollection", "features": [], "limited": False}
        if not self.path.is_file() or (layers == () and not base):
            return result
        if base:
            layers = tuple(sorted(BASE_LAYERS))
            result.update(display="base")
        result.update(safety_contour_m=safety_contour, safety_depth_m=safety_depth)
        where = " AND layer IN (" + ",".join("?" for _ in layers) + ")" if layers else ""
        if zoom < 13:
            where += " AND layer!='SOUNDG'"
        viewport = box(west, south, east, north)
        tolerance = 360 / (256 * 2**zoom)
        display_scale = 559082264 * math.cos(math.radians((south + north) / 2)) / 2**zoom
        minimum_scale = min(1_000_000, display_scale / 4)
        result.update(minimum_chart_scale=round(minimum_scale), chart_count=0)
        # Continental views use the coherent world backdrop, not isolated ENC cell fragments.
        if zoom < 7:
            result["overview"] = True
            return result
        deadline = time.monotonic() + DISPLAY_SECONDS
        self._coverage_revision()
        with closing(self.connect()) as db:
            candidates = []
            for code, scale, raw_bounds in db.execute("SELECT code,scale,bounds FROM charts WHERE enabled=1 AND (scale=0 OR scale>=?)", (minimum_scale,)):
                cell_bounds = json.loads(raw_bounds)
                if len(cell_bounds) == 4 and cell_bounds[0] <= east and cell_bounds[2] >= west and cell_bounds[1] <= north and cell_bounds[3] >= south:
                    candidates.append((scale or math.inf, code, cell_bounds))
            stored_coverage = {}
            if candidates and db.execute("SELECT 1 FROM sqlite_master WHERE name='chart_coverage'").fetchone():
                stored_coverage = dict(db.execute("SELECT code,geometry FROM chart_coverage WHERE code IN (" +
                                      ",".join("?" for _ in candidates) + ")", [code for _, code, _ in candidates]))
            # The most detailed enabled cell owns an area; coarser cells only fill uncovered gaps.
            charts, regions, covered = [], {}, GeometryCollection()
            priority = sorted(candidates)
            for index, (_, code, cell_bounds) in enumerate(priority):
                if time.monotonic() >= deadline:
                    result.update(limited=True, retryable=True)
                    break
                try:
                    # A legacy background cell that cannot hide another cell needs no costly footprint union.
                    overlaps_next = any(b[0] < cell_bounds[2] and b[2] > cell_bounds[0]
                                        and b[1] < cell_bounds[3] and b[3] > cell_bounds[1]
                                        for _, _, b in priority[index + 1:])
                    shape = (from_wkb(stored_coverage[code]) if code in stored_coverage else
                             self._legacy_coverage(db, code, zoom, viewport, tolerance, deadline) if overlaps_next else
                             box(*cell_bounds))
                    owns_area = shape is not None and not shape.is_empty
                    if shape is None:
                        result["limited"] = True
                        if time.monotonic() >= deadline:
                            result["retryable"] = True
                    if not owns_area:
                        shape = box(*cell_bounds)
                    if not shape.is_valid:
                        from shapely import make_valid
                        shape = make_valid(shape)
                    shape = shape.intersection(viewport).simplify(tolerance, preserve_topology=True)
                    region = shape.difference(covered)
                except GEOSException:
                    result["limited"] = True
                    continue
                if not region.is_empty:
                    charts.append(code)
                    regions[code] = region
                    if owns_area:
                        covered = covered.union(shape)
            result["chart_count"] = len(charts)
            if not charts:
                return result
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 2000)
            limit = min(DISPLAY_LIMIT, 1500 if zoom < 10 else 4000)
            # Start with the chosen charts, then intersect indexed extents. Sort IDs, not large WKB blobs.
            selection = ("SELECT f.id,c.scale,ROW_NUMBER() OVER (PARTITION BY f.chart ORDER BY "
                         "CASE WHEN layer IN ('LNDARE','COALNE','SLCONS') THEN 0 "
                         "WHEN layer IN ('DEPARE','DRGARE') THEN 1 ELSE 2 END,f.id) AS chart_rank "
                         "FROM features f INDEXED BY features_chart "
                              "CROSS JOIN extent e ON e.id=f.id JOIN charts c ON c.code=f.chart "
                              "WHERE f.chart IN (" + ",".join("?" for _ in charts) + ") "
                              "AND e.west<=? AND e.east>=? AND e.south<=? AND e.north>=?" + where)
            query = ("WITH ranked AS (" + selection + "), visible AS "
                     "(SELECT id FROM ranked ORDER BY chart_rank,scale DESC,id LIMIT ?) "
                     "SELECT f.id,f.geometry,f.properties FROM visible v CROSS JOIN features f ON f.id=v.id")
            try:
                display = _DepthDisplay(db, charts, safety_contour, safety_depth)
                result["effective_safety_contours"] = {chart: display.contours.get(chart) for chart in charts}
                rows = list(db.execute(query, (*charts, east, west, north, south, *(layers or ()), limit + 1)))
            except sqlite3.OperationalError:
                if time.monotonic() < deadline:
                    raise
                result.update(limited=True, retryable=True)
                return result
            finally:
                db.set_progress_handler(None, 0)
            def paint_order(row):
                layer = json.loads(row[2])["layer"]
                return (0 if layer in {"DEPARE", "RIVERS", "CANALS", "LAKARE", "DOCARE", "LOKBSN"}
                        else 1 if layer == "DRGARE" else 2 if layer in {"LNDARE", "BUAARE"}
                        else 4 if layer in {"COALNE", "SLCONS", "DEPCNT"} else 5 if layer == "SOUNDG"
                        else 6 if layer.startswith(("BOY", "BCN")) or layer == "LIGHTS" else 3)
            rows.sort(key=paint_order)
            if layers and HAZARD_LAYERS.intersection(layers) and not {"DEPARE", "DRGARE"}.issubset(layers):
                # Layer visibility must not change hazard classification. Read only nearby depth context.
                context_query = ("SELECT f.chart,f.geometry,f.properties FROM features f INDEXED BY features_chart "
                    "CROSS JOIN extent e ON e.id=f.id WHERE f.chart IN (" + ",".join("?" for _ in charts) + ") "
                    "AND f.layer IN ('DEPARE','DRGARE') AND e.west<=? AND e.east>=? AND e.south<=? AND e.north>=? LIMIT 513")
                context_bytes = 0
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 2000)
                try:
                    for index, (chart, wkb, props_json) in enumerate(db.execute(context_query, (*charts, east, west, north, south))):
                        context_bytes += len(wkb)
                        if index >= 512 or context_bytes > 2 * 1024**2 or time.monotonic() >= deadline:
                            result["limited"] = True
                            if time.monotonic() >= deadline:
                                result["retryable"] = True
                            break
                        try:
                            shape = from_wkb(wkb)
                            if shape.is_valid:
                                display.annotate(json.loads(props_json), shape.intersection(regions[chart]))
                        except GEOSException:
                            result["limited"] = True
                except sqlite3.OperationalError:
                    if time.monotonic() < deadline:
                        raise
                    result.update(limited=True, retryable=True)
                finally:
                    db.set_progress_handler(None, 0)
            size = 0
            for row_index, (fid, wkb, properties) in enumerate(rows):
                if row_index >= limit or time.monotonic() >= deadline:
                    result["limited"] = True
                    if time.monotonic() >= deadline:
                        result["retryable"] = True
                    break
                props = json.loads(properties)
                if props.get("SCAMIN", 0) > 0 and display_scale > props["SCAMIN"] * 1.5:
                    continue
                if props["layer"] == "SOUNDG" and zoom < 13:
                    continue
                try:
                    shape = from_wkb(wkb)
                    if not shape.is_valid:
                        from shapely import make_valid
                        shape = make_valid(shape)
                    shape = shape.intersection(regions[props["chart"]])
                    if shape.is_empty:
                        continue
                    if zoom < 10 and shape.geom_type in {"Point", "MultiPoint"}:
                        continue
                    display.annotate(props, shape)
                    if base and not display.includes(props, shape):
                        continue
                    if shape.geom_type not in {"Point", "MultiPoint"}:
                        shape = shape.simplify(tolerance * (2 if zoom < 10 else 1), preserve_topology=True)
                except GEOSException:
                    result["limited"] = True
                    continue
                if shape.is_empty:
                    continue
                if props["layer"] in {"TSSLPT", "DWRTPT", "ACHARE", "RESARE", "CTNARE", "PRCARE", "FSHGRD", "FSHZNE", "MIPARE", "DMPGRD"} and shape.geom_type in {"Polygon", "MultiPolygon"}:
                    surface = max(shape.geoms, key=lambda item: item.area) if shape.geom_type == "MultiPolygon" else shape
                    point = surface.representative_point()
                    props["symbol_position"] = [point.x, point.y]
                geometry = to_geojson(shape)
                size += len(geometry) + len(properties)
                if size > 3 * 1024**2:
                    result["limited"] = True
                    break
                result["features"].append(dict(type="Feature", id=fid, geometry=json.loads(geometry), properties=props))
        _combine_lights(result["features"])
        return result


def _combine_lights(features):
    # Same encoded position only: nearby but unrelated aids must never be merged.
    aids = {}
    for feature in features:
        p, geometry = feature["properties"], feature["geometry"]
        if geometry["type"] == "Point" and p["layer"].startswith(("BOY", "BCN")):
            aids[(p["chart"], *geometry["coordinates"][:2])] = p
    for feature in features:
        p, geometry = feature["properties"], feature["geometry"]
        if p["layer"] != "LIGHTS" or geometry["type"] != "Point":
            continue
        aid = aids.get((p["chart"], *geometry["coordinates"][:2]))
        if aid is not None and len(aid.get("lights", [])) < 8:
            aid.setdefault("lights", []).append({key: value for key, value in p.items() if key in {
                "CATLIT", "COLOUR", "LITCHR", "SIGGRP", "SIGPER", "VALNMR", "SECTR1", "SECTR2", "LITVIS", "ORIENT"}})
            p["combined_light"] = True
