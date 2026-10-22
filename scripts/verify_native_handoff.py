#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Verify a native build folder without installing or publishing anything."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from maritime_simulator import __version__
from scripts.check_v2_release_credentials import audit


def expected_assets(platform: str, version: str) -> list[str]:
    prefix = f"Maritime-Ship-Simulator-V2-{version}-{platform}"
    if platform == "macos":
        return [prefix + ".zip", prefix + ".dmg"]
    if platform == "windows":
        return [prefix + "-setup.exe", prefix + "-portable.zip"]
    return [prefix + "-installer.deb", prefix + ".AppImage", prefix + "-portable.tar.gz"]


def portable_inventory(path: Path) -> dict:
    archive = zipfile.ZipFile(path) if path.suffix == ".zip" else tarfile.open(path)
    try:
        names = archive.namelist() if isinstance(archive, zipfile.ZipFile) else archive.getnames()
        original_names = {}
        for name in names:
            normalized = name.replace("\\", "/")
            parts = PurePosixPath(normalized)
            if parts.is_absolute() or ".." in parts.parts or re.match(r"^[A-Za-z]:", normalized):
                raise ValueError("Unsafe portable archive path")
            if normalized in original_names:
                raise ValueError("Duplicate normalized portable archive path")
            original_names[normalized] = name
        names = list(original_names)
        candidates = [name for name in names if name.endswith("/THIRD_PARTY_LICENSES/dependency-inventory.json")]
        if len(candidates) != 1:
            raise ValueError("Portable package must contain exactly one dependency inventory")
        prefix = candidates[0].rsplit("/", 1)[0] + "/"

        def read(name):
            if isinstance(archive, zipfile.ZipFile):
                return archive.read(original_names[name])
            member = archive.getmember(original_names[name])
            if not member.isfile():
                raise ValueError(f"Notice is not a regular archive file: {name}")
            stream = archive.extractfile(member)
            assert stream is not None
            return stream.read()

        inventory = json.loads(read(candidates[0]))
        checksums = read(prefix + "SHA256SUMS").decode("utf-8").splitlines()
        if not checksums or not inventory.get("dependencies") or not inventory.get("chromium"):
            raise ValueError("Incomplete bundled dependency inventory")
        for line in checksums:
            digest, relative = line.split("  ", 1)
            parts = PurePosixPath(relative.replace("\\", "/"))
            if parts.is_absolute() or ".." in parts.parts or re.match(r"^[A-Za-z]:", relative):
                raise ValueError("Unsafe notice manifest path")
            if hashlib.sha256(read(prefix + parts.as_posix())).hexdigest() != digest:
                raise ValueError(f"Changed bundled dependency notice: {relative}")
        sources = json.loads(read(prefix + "upstream-sources.json"))
        required = json.loads((ROOT / "LICENSES/upstream-sources.json").read_text())
        if sources != required:
            raise ValueError("Native package source inventory differs from this release source")
        return inventory
    finally:
        archive.close()


def verify(directory: Path, platform: str, version: str) -> dict:
    filenames = expected_assets(platform, version)
    info = (directory / "RELEASE_INFO.txt").read_text(encoding="utf-8-sig")
    if f"Maritime Ship Simulator V2 {version}\n" not in info or "Release type: unsigned production" not in info:
        raise ValueError("Expected matching unsigned production RELEASE_INFO.txt")
    manifest = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+\*?([^/\\]+)", line)
        if not match or match[2] in {".", ".."} or match[2] in manifest:
            raise ValueError("Invalid or duplicate SHA256SUMS entry")
        manifest[match[2]] = match[1].lower()
    results = []
    for name in [*filenames, "RELEASE_INFO.txt"]:
        path = directory / name
        if path.is_symlink():
            raise ValueError(f"Native handoff must contain regular files: {name}")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if manifest.get(name) != actual:
            raise ValueError(f"Missing or incorrect artifact checksum: {name}")
        results.append({"filename": name, "sha256": actual, "size_bytes": path.stat().st_size})
    findings = audit([directory / name for name in filenames])
    if findings:
        raise ValueError("Credential audit failed: " + "; ".join(findings))
    portable = next(directory / name for name in filenames if name.endswith((".zip", ".tar.gz")))
    inventory = portable_inventory(portable)
    expected_system = {"macos": "Darwin", "windows": "Windows"}.get(platform, "Linux")
    if inventory["system"] != expected_system:
        raise ValueError("Native package platform does not match the requested handoff")
    return {
        "version": version, "platform": platform, "checksums": "passed",
        "credentials": "passed", "bundled_notices": "passed", "assets": results,
        "dependencies": inventory,
        "manual_platform_acceptance": "still required; this verifier does not install or run the package",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--platform", choices=("macos", "windows", "ubuntu", "ubuntu-22.04-amd64"), required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.directory, args.platform, __version__)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, tarfile.TarError) as error:
        print(f"Native handoff verification failed: {error}", file=sys.stderr)
        return 1
    text = json.dumps(result, indent=2) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
