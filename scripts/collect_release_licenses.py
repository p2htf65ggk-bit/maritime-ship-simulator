#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Collect the build environment's runtime dependency notices before freezing."""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import sys
import sysconfig

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOTS = ("PySide6", "certifi", "websockets", "pmtiles", "mapbox-vector-tile", "pyogrio")
QT_DISTRIBUTIONS = {"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6"}


def runtime_distributions(roots=ROOTS):
    pending = list(roots)
    found = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in found:
            continue
        distribution = metadata.distribution(name)
        found[name] = distribution
        for value in distribution.requires or ():
            dependency = Requirement(value)
            if dependency.marker is None or dependency.marker.evaluate({"extra": ""}):
                pending.append(dependency.name)
    # Only the bootloader ships, not all of PyInstaller's build-time dependencies.
    found["pyinstaller"] = metadata.distribution("pyinstaller")
    return found


def notice_files(distribution):
    for item in distribution.files or ():
        name = PurePosixPath(str(item)).name.lower()
        if any(word in name for word in ("license", "licence", "copying", "notice", "copyright")):
            path = Path(distribution.locate_file(item))
            if path.is_file():
                yield item, path


def collect(root: Path, output: Path, *, modules=()) -> dict:
    from PySide6.QtCore import qVersion
    from PySide6.QtWebEngineCore import qWebEngineChromiumVersion

    upstream = root / "LICENSES/upstream"
    manifest = json.loads((upstream / "upstream-notices.json").read_text(encoding="utf-8"))
    versions = {"qt": qVersion(), "pyside": metadata.version("PySide6")}
    for component, version in versions.items():
        if manifest["sources"][component]["version"] != version:
            raise ValueError(f"{component} {version} needs matching upstream notices; regenerate LICENSES/upstream")
        item = manifest["archives"][component]
        with (upstream / item["filename"]).open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum != item["sha256"]:
            raise ValueError(f"Changed upstream notices: {component}")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(upstream, output / "upstream")
    shutil.copy2(root / "docs/LIBRARY_REPLACEMENT.md", output / "LIBRARY_REPLACEMENT.md")
    result = {
        "schema": 1, "system": platform.system(), "machine": platform.machine(),
        "python": platform.python_version(), "qt": versions["qt"],
        "chromium": qWebEngineChromiumVersion(), "dependencies": [],
    }
    import pyogrio
    import shapely
    result["gdal"] = pyogrio.__gdal_version_string__
    result["geos_shapely"] = shapely.geos_version_string
    result["geos_gdal"] = ".".join(map(str, pyogrio.__gdal_geos_version__))
    source_manifest = json.loads((root / "LICENSES/upstream-sources.json").read_text(encoding="utf-8"))
    supplied_geos = {item["version"] for item in source_manifest["sources"] if item["component"] == "GEOS"}
    if not {result["geos_shapely"], result["geos_gdal"]} <= supplied_geos:
        raise ValueError("The build's GEOS version needs a matching source archive in LICENSES/upstream-sources.json")
    shutil.copy2(root / "LICENSES/upstream-sources.json", output / "upstream-sources.json")
    missing = []
    extra_distributions = set()
    if modules:
        installed = metadata.packages_distributions()
        for module in modules:
            extra_distributions.update(installed.get(module.split(".", 1)[0], ()))
    for name, distribution in sorted(runtime_distributions((*ROOTS, *sorted(extra_distributions))).items()):
        target = output / "python-packages" / f"{name}-{distribution.version}"
        copied = []
        for item, source in notice_files(distribution):
            parts = PurePosixPath(str(item)).parts
            if ".." in parts or PurePosixPath(str(item)).is_absolute():
                raise ValueError(f"Unsafe license record in {name}: {item}")
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied.append(str(destination.relative_to(output).as_posix()))
        if not copied and name not in QT_DISTRIBUTIONS:
            fallback = root / "LICENSES/python-packages" / f"{name}-{distribution.version}.txt"
            if fallback.is_file():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(fallback, target / "LICENSE.txt")
                copied.append(str((target / "LICENSE.txt").relative_to(output).as_posix()))
            else:
                missing.append(f"{name}=={distribution.version}")
        result["dependencies"].append({
            "name": name, "version": distribution.version, "notices": copied,
            "metadata_license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
            "upstream_notices": name in QT_DISTRIBUTIONS,
        })
    python_license = next((path for path in (
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE",
        root / "LICENSES/Python-PSF.txt",
    ) if path.is_file()), None)
    if python_license is None:
        missing.append(f"Python {platform.python_version()} LICENSE.txt (check your Python installation)")
    else:
        shutil.copyfile(python_license, output / "Python-LICENSE.txt")
    if missing:
        raise ValueError("Missing complete dependency notices:\n" + "\n".join(missing))
    (output / "dependency-inventory.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    lines = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            lines.append(f"{digest}  {path.relative_to(output).as_posix()}")
    (output / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def validate(output: Path) -> None:
    inventory = json.loads((output / "dependency-inventory.json").read_text(encoding="utf-8"))
    if not inventory.get("dependencies") or not inventory.get("chromium"):
        raise ValueError("Incomplete native dependency inventory")
    for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Invalid notice checksum path: {name}")
        with output.joinpath(*path.parts).open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != actual:
            raise ValueError(f"Changed bundled dependency notice: {name}")
    for name in ("Python-LICENSE.txt", "LIBRARY_REPLACEMENT.md", "upstream-sources.json"):
        if not (output / name).is_file():
            raise ValueError(f"Missing bundled dependency notice: {name}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(collect(root, root / "build-v2/notices"), indent=2))
