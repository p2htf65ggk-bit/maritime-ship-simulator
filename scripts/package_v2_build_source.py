#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import stat
import sys
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ROOT_FILES = (
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "FREEWARE_LICENSE.txt",
    "README.md",
    "build_v2.py",
    "pytest.ini",
    "requirements-v2-release.txt",
    "run_v2.py",
    "ship-simulator-v2.spec",
)
SOURCE_DIRS = (
    ".github",
    "LICENSES",
    "assets",
    "docs",
    "routes",
    "scenarios",
    "scripts",
    "src",
    "tests/v2",
    "tests/fixtures",
    "ui",
)
FORBIDDEN_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "build",
    "build-v2",
    "dist",
    "dist-v2",
    "release",
    "release-v2",
    "venv",
    "venv-v2-linux",
    "venv-v2-windows",
}
EXCLUDED_FILES = {
    Path("docs/v2_prebuild_report 2.md"),
}


def _included_files() -> list[Path]:
    files = [ROOT / relative for relative in ROOT_FILES]
    for directory in SOURCE_DIRS:
        files.extend(path for path in (ROOT / directory).rglob("*") if path.is_file())
    selected = []
    for path in files:
        relative = path.relative_to(ROOT)
        if not path.exists():
            raise SystemExit(f"Missing required build-source file: {relative}")
        if (
            any(part in FORBIDDEN_PARTS or part.startswith("venv-") for part in relative.parts)
            or relative in EXCLUDED_FILES
            or path.name == ".DS_Store"
            or path.name.startswith("._")
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        selected.append(path)
    return sorted(set(selected), key=lambda item: item.as_posix())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(files: list[Path]) -> bytes:
    lines = [f"{_sha256(path)}  {path.relative_to(ROOT).as_posix()}" for path in files]
    return ("\n".join(lines) + "\n").encode("ascii")


def _zip_info(name: str, mode: int = 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def build_archives(output_dir: Path, *, force: bool = False) -> tuple[Path, Path]:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from maritime_simulator import __version__
    from check_v2_release_credentials import audit

    files = _included_files()
    credential_findings = audit(files)
    if credential_findings:
        raise SystemExit(
            "Build-source credential audit failed:\n"
            + "\n".join(f"- {finding}" for finding in credential_findings)
        )
    manifest = _manifest(files)
    prefix = "ship-simulator-v2-build-source"
    base_name = f"Maritime-Ship-Simulator-V2-{__version__}-build-source"
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{base_name}.zip"
    tar_path = output_dir / f"{base_name}.tar.gz"

    existing = [path for path in (zip_path, tar_path) if path.exists()]
    if existing and not force:
        if not sys.stdin.isatty():
            raise SystemExit("Build-source artifacts already exist; rerun with --force to replace them.")
        answer = input("Build-source artifacts already exist. Replace them? [y/N] ").strip().lower()
        if answer not in {"y", "yes", "j", "ja"}:
            raise SystemExit("Existing build-source artifacts were not replaced.")

    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
            archive.writestr(_zip_info(f"{prefix}/{relative}", mode), path.read_bytes())
        archive.writestr(_zip_info(f"{prefix}/BUILD_SOURCE_SHA256SUMS"), manifest)

    with tarfile.open(tar_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            archive.add(path, arcname=f"{prefix}/{path.relative_to(ROOT).as_posix()}", recursive=False)
        info = tarfile.TarInfo(f"{prefix}/BUILD_SOURCE_SHA256SUMS")
        info.size = len(manifest)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(manifest))

    credential_findings = audit([zip_path, tar_path])
    if credential_findings:
        raise SystemExit(
            "Generated build-source credential audit failed:\n"
            + "\n".join(f"- {finding}" for finding in credential_findings)
        )

    checksums = output_dir / "BUILD_SOURCE_SHA256SUMS"
    checksums.write_text(
        f"{_sha256(zip_path)}  {zip_path.name}\n{_sha256(tar_path)}  {tar_path.name}\n",
        encoding="ascii",
    )
    return zip_path, tar_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create clean V2 build-source archives for target-OS release machines")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release-v2" / "source")
    parser.add_argument("--force", action="store_true", help="Replace existing generated source archives without prompting")
    args = parser.parse_args()
    zip_path, tar_path = build_archives(args.output_dir.resolve(), force=args.force)
    print(zip_path)
    print(tar_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
