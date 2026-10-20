#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Extract upstream notices without unpacking or vendoring the Qt source tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


SOURCES = {
    "qt": {
        "version": "6.11.0",
        "filename": "qt-everywhere-src-6.11.0.tar.xz",
        "sha256": "acf3b3db04c9e5d0820e8324b097320388954c297cee83d2bd698789234f68a4",
        "url": "https://download.qt.io/official_releases/qt/6.11/6.11.0/single/qt-everywhere-src-6.11.0.tar.xz",
    },
    "pyside": {
        "version": "6.11.0",
        "filename": "pyside-setup-everywhere-src-6.11.0.tar.xz",
        "sha256": "48d5c44d7c3ed861055d5491486e6a220ef5006573cae01a5fae3fb69d786336",
        "url": "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.0-src/pyside-setup-everywhere-src-6.11.0.tar.xz",
    },
}


def is_notice(name: str) -> bool:
    path = PurePosixPath(name)
    basename = path.name.lower()
    return (
        any(word in basename for word in ("license", "licence", "copying", "notice", "copyright"))
        or basename.startswith(("lgpl-", "gpl-"))
        or basename.endswith(("_license.txt", "-license.txt", ".license", ".spdx"))
        or basename in {"qt_attribution.json", "readme.chromium"}
        or "LICENSES" in path.parts
    )


def prepare(archives: dict[str, Path], output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    result = {"schema": 1, "sources": SOURCES, "archives": {}}
    for component, source in archives.items():
        expected = SOURCES[component]
        with source.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected["sha256"]:
            raise ValueError(f"Unverified {component} source: expected {expected['sha256']}, got {actual}")
        target = output / f"{component}-{expected['version']}-notices.zip"
        entries = 0
        written = set()
        referenced = set()
        with tarfile.open(source, "r|xz") as archive, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as notices:
            for member in archive:
                path = PurePosixPath(member.name)
                if not member.isfile() or not is_notice(member.name):
                    continue
                if path.is_absolute() or ".." in path.parts or member.size > 32 * 1024 * 1024:
                    raise ValueError(f"Unsafe upstream notice: {member.name}")
                stream = archive.extractfile(member)
                assert stream is not None
                info = zipfile.ZipInfo(member.name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                content = stream.read()
                notices.writestr(info, content)
                written.add(member.name)
                if path.name == "qt_attribution.json":
                    try:
                        data = json.loads(content)
                    except ValueError:
                        data = []
                    for item in data if isinstance(data, list) else [data]:
                        files = item.get("LicenseFile", "")
                        for filename in [files] if isinstance(files, str) else files:
                            if filename:
                                referenced.add(posixpath.normpath(posixpath.join(str(path.parent), filename)))
                entries += 1
            missing = referenced - written
            if missing:
                with tarfile.open(source, "r|xz") as additional:
                    for member in additional:
                        if member.isfile() and member.name in missing:
                            stream = additional.extractfile(member)
                            assert stream is not None
                            info = zipfile.ZipInfo(member.name, date_time=(2026, 1, 1, 0, 0, 0))
                            info.compress_type = zipfile.ZIP_DEFLATED
                            notices.writestr(info, stream.read())
                            written.add(member.name)
                            entries += 1
            unresolved = referenced - written
            if unresolved:
                raise ValueError("Unresolved upstream license references:\n" + "\n".join(sorted(unresolved)))
        if entries < 10:
            raise ValueError(f"Incomplete upstream notice archive: {target}")
        with target.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        result["archives"][component] = {"filename": target.name, "sha256": checksum, "entries": entries}
    (output / "upstream-notices.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qt-source", type=Path, required=True)
    parser.add_argument("--pyside-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("LICENSES/upstream"))
    args = parser.parse_args()
    print(json.dumps(prepare({"qt": args.qt_source, "pyside": args.pyside_source}, args.output), indent=2))


if __name__ == "__main__":
    main()
