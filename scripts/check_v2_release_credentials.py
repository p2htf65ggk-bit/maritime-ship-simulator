#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import argparse
from contextlib import closing
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_ENVIRONMENT_NAMES = (
    "MARITIME_SIM_GOOGLE_3D_TILES_API_KEY",
    "MARITIME_SIM_AISSTREAM_API_KEY",
    "MARITIME_SIM_BARENTSWATCH_CLIENT_ID",
    "MARITIME_SIM_BARENTSWATCH_CLIENT_SECRET",
)
SENSITIVE_KEYS = {
    "apikey",
    "googletilesapikey",
    "openmeteoapikey",
    "barentswatchclientid",
    "barentswatchclientsecret",
    "clientid",
    "clientsecret",
}
SENSITIVE_RUNTIME_FILES = {
    ".env",
    "app_state_v2.json",
    "last_configuration_v2.json",
    "live_ais_v2.json",
    "startup_profile_v2.json",
    "visual_settings_v2.json",
}
GOOGLE_API_KEY = re.compile(rb"AIza[0-9A-Za-z_-]{30,}")
NAMED_CREDENTIAL = re.compile(
    rb"(?i)(?:google(?:_3d_tiles)?_api_key|aisstream(?:_api)?_key|open_meteo_api_key|"
    rb"barentswatch_client_(?:id|secret))['\"]?\s*[:=]\s*['\"]([^'\"\s,;}]{8,})"
)
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")
TEXT_SUFFIXES = {
    ".bat",
    ".cfg",
    ".cmd",
    ".conf",
    ".css",
    ".csv",
    ".desktop",
    ".env",
    ".html",
    ".ini",
    ".iss",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".qml",
    ".rtz",
    ".service",
    ".sh",
    ".spec",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {
    "build_source_sha256sums",
    "dockerfile",
    "license",
    "makefile",
    "readme",
    "sha256sums",
}
PLACEHOLDERS = {
    "changeme",
    "example",
    "placeholder",
    "test-key",
    "your-key",
}


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _usable_secret(value: object) -> str:
    text = str(value or "").strip()
    if len(text) < 8 or text.lower() in PLACEHOLDERS:
        return ""
    return text


def _collect_json_credentials(value: object, label: str, result: dict[bytes, str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _normalized_key(str(key))
            if normalized in SENSITIVE_KEYS:
                secret = _usable_secret(child)
                if secret:
                    result[secret.encode("utf-8")] = f"local setting {label}:{key}"
            else:
                _collect_json_credentials(child, label, result)
    elif isinstance(value, list):
        for child in value:
            _collect_json_credentials(child, label, result)


def _default_config_dir() -> Path:
    override = os.environ.get("MARITIME_SIMULATOR_V2_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Ship Simulator" / "v2"


def _local_credential_fingerprints() -> dict[bytes, str]:
    result: dict[bytes, str] = {}
    for name in CREDENTIAL_ENVIRONMENT_NAMES:
        secret = _usable_secret(os.environ.get(name))
        if secret:
            result[secret.encode("utf-8")] = f"environment variable {name}"

    dotenv = ROOT / ".env"
    if dotenv.is_file():
        try:
            for raw_line in dotenv.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() not in CREDENTIAL_ENVIRONMENT_NAMES:
                    continue
                secret = _usable_secret(value.strip().strip("'\""))
                if secret:
                    result[secret.encode("utf-8")] = f"local .env entry {name.strip()}"
        except OSError:
            pass

    config_dir = _default_config_dir()
    for name in (
        "app_state_v2.json",
        "live_ais_v2.json",
        "visual_settings_v2.json",
        "startup_profile_v2.json",
        "last_configuration_v2.json",
    ):
        path = config_dir / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        _collect_json_credentials(data, name, result)
    return result


def _is_text_candidate(label: str) -> bool:
    member_name = label.rsplit("!", 1)[-1]
    path = Path(member_name)
    return path.suffix.lower() in TEXT_SUFFIXES or path.name.lower() in TEXT_FILENAMES


def _scan_stream(
    stream,
    label: str,
    fingerprints: dict[bytes, str],
    findings: list[str],
    *,
    scan_heuristic_patterns: bool,
) -> None:
    tail = b""
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        sample = tail + chunk
        if scan_heuristic_patterns:
            if GOOGLE_API_KEY.search(sample):
                findings.append(f"Google API-key pattern in {label}")
            for match in NAMED_CREDENTIAL.finditer(sample):
                candidate = match.group(1).decode("utf-8", errors="ignore")
                if _usable_secret(candidate):
                    findings.append(f"named provider credential in {label}")
        for fingerprint, source in fingerprints.items():
            if fingerprint in sample:
                findings.append(f"credential matching {source} in {label}")
        tail = sample[-1024:]


def _is_archive(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(ARCHIVE_SUFFIXES)


def _scan_archive_bytes(
    data: bytes,
    label: str,
    fingerprints: dict[bytes, str],
    findings: list[str],
    depth: int,
) -> bool:
    if depth > 2:
        return False
    source = io.BytesIO(data)
    try:
        if label.lower().endswith(".zip"):
            with zipfile.ZipFile(source) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    _scan_archive_member(archive.open(info), f"{label}!{info.filename}", fingerprints, findings, depth)
            return True
        if label.lower().endswith((".tar", ".tar.gz", ".tgz")):
            source.seek(0)
            with tarfile.open(fileobj=source, mode="r:*") as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    extracted = archive.extractfile(member)
                    if extracted is not None:
                        _scan_archive_member(extracted, f"{label}!{member.name}", fingerprints, findings, depth)
            return True
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return False
    return False


def _scan_archive_member(stream, label: str, fingerprints: dict[bytes, str], findings: list[str], depth: int) -> None:
    with closing(stream):
        name = Path(label.split("!")[-1]).name
        if name in SENSITIVE_RUNTIME_FILES:
            findings.append(f"runtime credential file included as {label}")
        if _is_archive(label):
            data = stream.read()
            if _scan_archive_bytes(data, label, fingerprints, findings, depth + 1):
                return
            _scan_stream(
                io.BytesIO(data),
                label,
                fingerprints,
                findings,
                scan_heuristic_patterns=_is_text_candidate(label),
            )
            return
        _scan_stream(
            stream,
            label,
            fingerprints,
            findings,
            scan_heuristic_patterns=_is_text_candidate(label),
        )


def _scan_path(path: Path, fingerprints: dict[bytes, str], findings: list[str]) -> None:
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file() and not child.is_symlink():
                _scan_path(child, fingerprints, findings)
        return
    if not path.is_file():
        findings.append(f"missing audit target {path}")
        return
    if path.name in SENSITIVE_RUNTIME_FILES:
        findings.append(f"runtime credential file included as {path}")
    if _is_archive(path.name):
        data = path.read_bytes()
        if _scan_archive_bytes(data, str(path), fingerprints, findings, 0):
            return
    with path.open("rb") as stream:
        _scan_stream(
            stream,
            str(path),
            fingerprints,
            findings,
            scan_heuristic_patterns=_is_text_candidate(str(path)),
        )


def _source_files() -> list[Path]:
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from package_v2_build_source import _included_files

    return _included_files()


def audit(paths: list[Path], *, source_tree: bool = False) -> list[str]:
    fingerprints = _local_credential_fingerprints()
    findings: list[str] = []
    targets = _source_files() if source_tree else paths
    for path in targets:
        _scan_path(path, fingerprints, findings)
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when V2 release inputs or artifacts contain API keys or user credential files"
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--source-tree", action="store_true", help="Audit exactly the files selected for build-source handoff")
    args = parser.parse_args()
    if not args.source_tree and not args.paths:
        parser.error("provide --source-tree or at least one path")
    findings = audit([path.resolve() for path in args.paths], source_tree=args.source_tree)
    if findings:
        print("Release credential audit failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    scope = "source tree" if args.source_tree else f"{len(args.paths)} artifact path(s)"
    print(f"Release credential audit passed: {scope}; no embedded credentials found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
