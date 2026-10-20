#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from maritime_simulator import __version__

SPEC = ROOT / "ship-simulator-v2.spec"


def _configured_path(environment_name: str, default: str) -> Path:
    configured = os.environ.get(environment_name)
    if not configured:
        return ROOT / default
    path = Path(configured).expanduser()
    return path if path.is_absolute() else ROOT / path


def _release_signing_mode() -> str:
    mode = os.environ.get("RELEASE_SIGNING", "unsigned").strip().lower()
    if mode not in {"unsigned", "signed"}:
        raise SystemExit("RELEASE_SIGNING must be either 'unsigned' or 'signed'.")
    return mode


DIST = _configured_path("MARITIME_SIMULATOR_V2_DIST_DIR", "dist-v2")
BUILD = _configured_path("MARITIME_SIMULATOR_V2_BUILD_DIR", "build-v2")
APP_NAME = "Ship Simulator V2"
RELEASE = _configured_path("MARITIME_SIMULATOR_V2_RELEASE_DIR", "release-v2")
REQUIRED_RESOURCES = (
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "LICENSES/Leaflet-BSD-2-Clause.txt",
    "LICENSES/ODbL-1.0.txt",
    "FREEWARE_LICENSE.txt",
    "assets/app_icon.png",
    "ui/web/map.html",
    "ui/web/s57_portrayal.js",
    "assets/geodata/osm-simplified-land-polygons-3857.zip",
    "assets/geodata/README.txt",
    "ui/web/leaflet/leaflet.js",
    "ui/web/leaflet/leaflet.css",
    "ui/web/forward_view/cesium.html",
    "ui/web/forward_view/cesium_simple_view.css",
    "ui/web/forward_view/cesium_simple_view.js",
    "ui/web/forward_view/google_maps_3d.html",
    "ui/web/forward_view/google_maps_3d.css",
    "ui/web/forward_view/google_maps_3d.js",
    "routes/default_route.rtz",
    "scenarios/test_scenario.json",
    "maritime_simulator/resources/factory_vessel_profiles.json",
    "maritime_simulator/resources/advanced_protocol_catalog.json",
    "pyogrio/gdal_data/s57objectclasses.csv",
    "pyogrio/gdal_data/s57attributes.csv",
)
FORBIDDEN_RELEASE_NAMES = {".DS_Store", ".git", "__pycache__", ".pytest_cache"}


def _make_writable(function, path, _excinfo):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    function(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_release_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        unwanted = []
        for name in archive.namelist():
            parts = tuple(part for part in Path(name).parts if part not in ("", "."))
            if (
                "__MACOSX" in parts
                or any(part in FORBIDDEN_RELEASE_NAMES for part in parts)
                or any(part.startswith("._") for part in parts)
                or any(part.endswith(".pyc") for part in parts)
            ):
                unwanted.append(name)
    if unwanted:
        raise SystemExit(
            "V2 release archive contains forbidden metadata or generated files:\n"
            + "\n".join(unwanted[:50])
        )


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    _prepare_tree_for_removal(path)
    last_error = None
    for _ in range(5):
        try:
            try:
                shutil.rmtree(path, onexc=_make_writable)
            except TypeError:
                shutil.rmtree(path, onerror=_make_writable)
            return
        except OSError as error:
            last_error = error
            _prepare_tree_for_removal(path)
            time.sleep(0.25)
    raise RuntimeError(f"Could not remove stale V2 build directory {path}: {last_error}")


def _prepare_tree_for_removal(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["cmd.exe", "/c", "attrib", "-R", "-S", "-H", str(path / "*"), "/S", "/D"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    for candidate in (path, *path.rglob("*")):
        try:
            os.chmod(candidate, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        except OSError:
            pass


def _resource_root() -> tuple[Path, Path]:
    if sys.platform == "darwin":
        app = DIST / f"{APP_NAME}.app"
        return app, app / "Contents" / "Resources"
    app = DIST / APP_NAME
    return app, app / "_internal"


def _clear_macos_metadata(path: Path) -> None:
    subprocess.run(["xattr", "-cr", str(path)], check=False)
    dot_clean = shutil.which("dot_clean")
    if dot_clean:
        subprocess.run([dot_clean, "-m", str(path)], check=False)


def _prepare_qt_framework_versions(framework: Path) -> None:
    versions = framework / "Versions"
    if not versions.is_dir():
        raise SystemExit(f"Invalid Qt framework: missing {versions}. Reinstall PySide6 in the build venv.")
    entries = [path for path in versions.iterdir() if path.name != "Current"]
    valid = [path for path in entries if (path / framework.stem).is_file()]
    if len(valid) != 1:
        raise SystemExit(
            f"Expected one Qt framework binary version in {versions}, found {len(valid)}. "
            "Recreate the build venv or reinstall PySide6 before retrying."
        )
    # PyInstaller interprets every directory in Versions as a framework version.
    # An empty leftover such as Resources can redirect WebEngine's helper bundle.
    for path in entries:
        if path in valid:
            continue
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise SystemExit(
                f"Unexpected Qt framework version entry: {path}. "
                "No files were deleted. Recreate the build venv or reinstall PySide6."
            )
        try:
            path.rmdir()
        except OSError as error:
            raise SystemExit(
                f"Cannot remove empty Qt framework leftover {path}: {error}. "
                "Recreate the build venv before retrying."
            ) from error
        print(f"Removed empty Qt framework version leftover: {path}")


def _prepare_macos_resources() -> None:
    if sys.platform != "darwin":
        return
    import PySide6

    _prepare_qt_framework_versions(
        Path(PySide6.__file__).parent / "Qt/lib/QtWebEngineCore.framework"
    )
    for relative in ("assets", "ui/web", "routes", "scenarios", "src/maritime_simulator/resources"):
        path = ROOT / relative
        if path.exists():
            _clear_macos_metadata(path)


def _finalize_macos_bundle() -> None:
    if sys.platform != "darwin":
        return
    app = DIST / f"{APP_NAME}.app"
    # Documents may be managed by macOS File Provider, which can reattach
    # FinderInfo while codesign is still walking a large Qt bundle. Sign and
    # verify outside that tree, then place the verified bundle back in dist-v2.
    with tempfile.TemporaryDirectory(prefix="ship-simulator-v2-signature-") as temp_dir:
        staged = Path(temp_dir) / app.name
        subprocess.run(
            ["ditto", "--norsrc", "--noextattr", str(app), str(staged)],
            check=True,
        )
        _clear_macos_metadata(staged)
        signed_release = _release_signing_mode() == "signed"
        signing_identity = os.environ.get("MACOS_SIGN_IDENTITY", "").strip()
        if signed_release and not signing_identity:
            raise SystemExit("RELEASE_SIGNING=signed requires MACOS_SIGN_IDENTITY.")
        sign_command = ["codesign", "--force", "--all-architectures", "--deep", "--timestamp"]
        if signed_release:
            sign_command.extend(("--options", "runtime"))
            entitlements = ROOT / "scripts" / "macos-v2-entitlements.plist"
            if not entitlements.is_file():
                raise SystemExit(f"Missing macOS production entitlements: {entitlements}")
            sign_command.extend(("--entitlements", str(entitlements), "--sign", signing_identity))
        else:
            sign_command.extend(("--sign", "-"))
        sign_command.append(str(staged))
        subprocess.run(sign_command, check=True)
        _clear_macos_metadata(staged)
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(staged)],
            check=True,
        )
        release_dir = RELEASE / "macos"
        release_dir.mkdir(parents=True, exist_ok=True)
        archive_name = f"Maritime-Ship-Simulator-V2-{__version__}-macos.zip"
        temporary_archive = Path(temp_dir) / archive_name
        archive_environment = os.environ.copy()
        archive_environment["COPYFILE_DISABLE"] = "1"
        subprocess.run(
            [
                "ditto",
                "--norsrc",
                "--noextattr",
                "-c",
                "-k",
                "--keepParent",
                str(staged),
                str(temporary_archive),
            ],
            env=archive_environment,
            check=True,
        )
        _validate_release_archive(temporary_archive)
        verify_dir = Path(temp_dir) / "archive-verification"
        verify_dir.mkdir()
        subprocess.run(["ditto", "-x", "-k", str(temporary_archive), str(verify_dir)], check=True)
        extracted = verify_dir / staged.name
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(extracted)],
            check=True,
        )
        release_archive = release_dir / archive_name
        shutil.copyfile(temporary_archive, release_archive)
        digest = _sha256(release_archive)
        (release_dir / "SHA256SUMS").write_text(
            f"{digest}  {archive_name}\n",
            encoding="ascii",
        )
        _remove_tree(app)
        subprocess.run(
            ["ditto", "--norsrc", "--noextattr", str(staged), str(app)],
            check=True,
        )
    signature_kind = "Developer ID" if _release_signing_mode() == "signed" else "ad-hoc"
    print(
        f"{signature_kind}-signed and verified macOS bundle and release archive "
        f"for {__version__} using metadata-free staging."
    )


def _validate() -> None:
    from scripts.audit_qt_bundle import audit_bundle

    app, resources = _resource_root()
    try:
        audit_bundle(app)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    executable = (
        app / "Contents" / "MacOS" / APP_NAME
        if sys.platform == "darwin"
        else app / f"{APP_NAME}{'.exe' if sys.platform == 'win32' else ''}"
    )
    expected = [app, executable, *(resources / item for item in REQUIRED_RESOURCES)]
    missing = [str(path) for path in expected if not path.exists()]
    if missing:
        raise SystemExit("V2 build validation failed; missing:\n" + "\n".join(missing))
    unwanted = [
        str(path)
        for path in app.rglob("*")
        if (
            path.name in FORBIDDEN_RELEASE_NAMES
            or path.name.startswith("._")
            or path.suffix == ".pyc"
        )
    ]
    if unwanted:
        raise SystemExit("V2 build contains forbidden release files:\n" + "\n".join(unwanted[:50]))
    runtime_pattern = (
        "**/Python.framework/Versions/*/Python"
        if sys.platform == "darwin"
        else "**/python3*.dll"
        if sys.platform == "win32"
        else "**/libpython3*.so*"
    )
    if not any(app.glob(runtime_pattern)):
        raise SystemExit("V2 build validation failed; bundled Python runtime was not found.")
    if not any(path.name.startswith("PySide6") for path in app.rglob("*")):
        raise SystemExit("V2 build validation failed; bundled PySide6 runtime was not found.")


def main() -> int:
    for required in (SPEC, ROOT / "run_v2.py", ROOT / "src" / "maritime_simulator"):
        if not required.exists():
            raise SystemExit(f"Missing V2 build input: {required}")
    _prepare_macos_resources()
    for path in (DIST, BUILD):
        _remove_tree(path)
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYINSTALLER_CONFIG_DIR", str(ROOT / ".pyinstaller-v2"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD),
            str(SPEC),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    _finalize_macos_bundle()
    _validate()
    print(f"Validated {APP_NAME} build in {DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
