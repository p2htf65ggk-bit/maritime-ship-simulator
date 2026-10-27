# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

import build_v2
from maritime_simulator import __version__
from scripts.check_v2_release_credentials import audit
from scripts.package_v2_build_source import _included_files


ROOT = Path(__file__).resolve().parents[2]


def test_v2_production_version_and_documentation_are_consistent():
    assert __version__ == "2.3.1"
    for relative in (
        "README.md",
        "docs/v2_architecture.md",
        "docs/v2_user_guide.md",
        "docs/v2_1_forward_view.md",
    ):
        assert __version__ in (ROOT / relative).read_text(encoding="utf-8"), relative
    for relative in (
        "docs/v2_release_readiness.md",
        "docs/v2_release_scripts.md",
        f"docs/v2_release_notes_{__version__}.md",
    ):
        assert __version__ in (ROOT / relative).read_text(encoding="utf-8"), relative
    about = (ROOT / "src/maritime_simulator/ui/about_dialog.py").read_text(encoding="utf-8")
    assert 'release_label = "Production release"' in about
    assert "26 October 2026" in about
    release_notes = ROOT / f"docs/v2_release_notes_{__version__}.md"
    assert release_notes.is_file()
    assert __version__ in release_notes.read_text(encoding="utf-8")


@pytest.mark.parametrize("version,expected", [("2.3.1", "2.3.1"), ("2.3.1-rc1", "2.3.1~rc1"),
                                             ("2.3.1-beta.1", "2.3.1~beta.1")])
def test_debian_prerelease_versions_sort_before_the_final_version(version, expected):
    script = (ROOT / "scripts/build-v2-ubuntu.sh").read_text()
    assignments = re.findall(r"^\s*(DEB_VERSION=.+)$", script, re.MULTILINE)
    assert len(assignments) == 1
    assert assignments[0] == r'DEB_VERSION=${VERSION/-/\~}'
    bash = shutil.which("bash")
    if bash:
        result = subprocess.run(
            [bash, "-c", assignments[0] + '\nprintf "%s" "$DEB_VERSION"'],
            env={**os.environ, "VERSION": version, "HOME": "/tmp/build user"},
            capture_output=True, text=True, check=True,
        )
        assert result.stdout == expected


def test_beta_is_recognized_as_a_preview_by_all_native_build_entrypoints():
    for relative in ("scripts/build-v2-ubuntu.sh", "scripts/build-v2-macos.sh"):
        assert '"${VERSION}" == *-beta*' in (ROOT / relative).read_text()
    assert "'-(rc|beta)'" in (ROOT / "scripts/build-v2-windows.ps1").read_text()


def test_public_bundle_excludes_unused_qt_families_and_keeps_licenses():
    from scripts.audit_qt_bundle import audit_paths

    assert audit_paths(["PySide6/Qt6Charts.dll", "QtQuick3D.framework/QtQuick3D", "libQt6Graphs.so.6"])
    assert not audit_paths(["QtQuick.framework/QtQuick", "QtWebEngineCore.framework/QtWebEngineCore"])
    assert {"LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"} <= set(build_v2.REQUIRED_RESOURCES)
    assert (ROOT / "LICENSE").read_bytes() == (ROOT / "FREEWARE_LICENSE.txt").read_bytes()
    spec = (ROOT / "ship-simulator-v2.spec").read_text()
    assert '"QtQml", "QtQuick", "QtQuickWidgets", "QtWebEngineQuick"' in spec
    assert "a.binaries = filter_build_entries(a.binaries)" in spec
    assert "a.datas = filter_build_entries(a.datas)" in spec


def test_qt_collection_filter_removes_plugins_dependencies_and_renamed_entries():
    from scripts.audit_qt_bundle import filter_build_entries

    keep = [
        ("PySide6/Qt/plugins/platforms/libqcocoa.dylib", "/env/libqcocoa.dylib", "BINARY"),
        ("QtQuick.framework/QtQuick", "/env/QtQuick.framework/QtQuick", "BINARY"),
        ("QtWebEngineCore.dll", "/env/QtWebEngineCore.dll", "BINARY"),
    ]
    remove = [
        ("PySide6/Qt/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.dylib", "/env/plugin", "BINARY"),
        ("Qt6VirtualKeyboard.dll", "/env/Qt6VirtualKeyboard.dll", "BINARY"),
        ("libQt6VirtualKeyboardQml.so.6", "/env/libQt6VirtualKeyboardQml.so.6", "BINARY"),
        ("renamed.dat", "/env/QtVirtualKeyboard.framework/Resources/Info.plist", "DATA"),
    ]
    assert filter_build_entries(keep + remove) == keep
    assert filter_build_entries([]) == []


def test_only_active_forward_view_resources_are_bundled():
    required = set(build_v2.REQUIRED_RESOURCES)
    expected = {
        "ui/web/forward_view/cesium.html",
        "ui/web/forward_view/cesium_simple_view.css",
        "ui/web/forward_view/cesium_simple_view.js",
        "ui/web/forward_view/google_maps_3d.html",
        "ui/web/forward_view/google_maps_3d.css",
        "ui/web/forward_view/google_maps_3d.js",
    }
    assert expected <= required
    for relative in expected:
        path = ROOT / relative
        assert path.is_file(), relative
        assert path.stat().st_size > 0, relative
    assert not any(path.startswith("ui/web/forward_view/assets/") for path in required)
    assert not any(path.startswith("ui/web/forward_view/vendor/") for path in required)
    assert "ui/web/forward_view/index.html" not in required


def test_s57_release_dependencies_and_dictionaries_are_bundled():
    assert {"pyogrio/gdal_data/s57objectclasses.csv", "pyogrio/gdal_data/s57attributes.csv"} <= set(build_v2.REQUIRED_RESOURCES)
    spec = (ROOT / "ship-simulator-v2.spec").read_text(encoding="utf-8")
    assert '"pyogrio.raw"' in spec and "scripts/pyinstaller-hooks" in spec
    hook = (ROOT / "scripts/pyinstaller-hooks/hook-pyogrio.py").read_text(encoding="utf-8")
    assert 'collect_data_files("pyogrio")' in hook
    assert 'copy_metadata("pyogrio")' in hook
    assert 'collect_delvewheel_libs_directory("pyogrio"' in hook
    assert 'collect_submodules("pyogrio") + ["uuid"]' in hook


def test_v2_release_signing_mode_defaults_to_unsigned_and_rejects_unknown_values(monkeypatch):
    monkeypatch.delenv("RELEASE_SIGNING", raising=False)
    assert build_v2._release_signing_mode() == "unsigned"
    monkeypatch.setenv("RELEASE_SIGNING", "signed")
    assert build_v2._release_signing_mode() == "signed"
    monkeypatch.setenv("RELEASE_SIGNING", "unsupported")
    with pytest.raises(SystemExit, match="unsigned.*signed"):
        build_v2._release_signing_mode()


def test_v2_shell_release_scripts_have_valid_syntax_and_build_v2_only():
    bash = shutil.which("bash")
    for relative in (
        "scripts/build-v2-macos.sh",
        "scripts/build-v2-ubuntu.sh",
        "scripts/build-v2-ubuntu-22.04.sh",
    ):
        path = ROOT / relative
        content = path.read_text(encoding="utf-8")
        if bash:
            result = subprocess.run((bash, "-n", str(path)), capture_output=True, text=True, check=False)
            assert result.returncode == 0, result.stderr
        assert content.startswith("#!/usr/bin/env bash")
        if relative.endswith("22.04.sh"):
            continue
        assert "scripts/prebuild_v2.py --profile full" in content
        assert 'build_v2.py' in content
        assert "Maritime-Ship-Simulator-V2-${VERSION}" in content
        if relative.endswith("ubuntu.sh"):
            assert "validate_release_source" in content
            assert "Trash folder" in content
            assert "confirm_operation" in content
            assert "linux-v2-runtime-launcher.sh" in content
            assert '${UBUNTU_RELEASE_TAG}-installer.deb' in content
            assert "usr/share/applications/maritime-ship-simulator-v2.desktop" in content
            assert "usr/share/icons/hicolor/256x256/apps/maritime-ship-simulator-v2.png" in content
            assert "libxcb-icccm4" in content
            assert "libxcb-keysyms1" in content
            assert "Install and register the generated" in content
            assert "PORTABLE_SMOKE_DIR" in content
            assert 'dpkg-deb --field "${DEB}" Depends' in content


def test_ubuntu_2204_release_wrapper_enforces_compatible_build_baseline():
    wrapper = (ROOT / "scripts/build-v2-ubuntu-22.04.sh").read_text(encoding="utf-8")
    builder = (ROOT / "scripts/build-v2-ubuntu.sh").read_text(encoding="utf-8")
    assert 'VERSION_ID:-}" != "22.04"' in wrapper
    assert "python3.12" in wrapper
    assert "python3.11" not in wrapper
    assert "MIN_PYTHON_MINOR=12" in wrapper
    assert "MAX_GLIBC_VERSION=2.35" in wrapper
    assert 'UBUNTU_RELEASE_TAG="ubuntu-22.04-${RELEASE_ARCH}"' in wrapper
    assert 'UBUNTU_RELEASE_DIR="release-v2/ubuntu-22.04-${RELEASE_ARCH}"' in wrapper
    assert "scripts/check_linux_abi.py" in builder
    assert "ABI_COMPATIBILITY.txt" in builder
    assert "${UBUNTU_RELEASE_TAG}-portable.tar.gz" in builder


@pytest.mark.parametrize("version, supported", [
    ((3, 11, 10), False),
    ((3, 12, 0), True),
    ((3, 14, 0), True),
    ((3, 15, 0), False),
])
def test_ubuntu_2204_python_predicate_matches_release_dependencies(monkeypatch, version, supported):
    wrapper = (ROOT / "scripts/build-v2-ubuntu-22.04.sh").read_text(encoding="utf-8")
    predicate = re.search(r"python_supported\(\)\s*\{\s*\"\$1\" -c '([^']+)'", wrapper)
    assert predicate is not None
    monkeypatch.setattr(sys, "version_info", version)
    with pytest.raises(SystemExit) as result:
        exec(predicate.group(1), {})
    assert result.value.code == (0 if supported else 1)


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash is required to exercise the Ubuntu source guard")
def test_ubuntu_release_script_rejects_a_physically_trashed_source(tmp_path):
    root = tmp_path / ".local" / "share" / "Trash" / "files" / "ship-simulator"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/build-v2-ubuntu.sh", scripts / "build-v2-ubuntu.sh")
    environment = os.environ.copy()
    result = subprocess.run(
        (shutil.which("bash"), "-c",
         'uname() { printf "Linux\\n"; }\nsource "$1"',
         "test-source-guard", (scripts / "build-v2-ubuntu.sh").as_posix()),
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "physically running from a Trash folder" in result.stderr
    assert "open a new terminal" in result.stderr


def test_windows_v2_release_script_and_installer_are_self_consistent():
    script = (ROOT / "scripts/build-v2-windows.ps1").read_text(encoding="utf-8")
    wheelhouse = (ROOT / "scripts/prepare-v2-windows-wheelhouse.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "scripts/windows-v2-installer.iss").read_text(encoding="utf-8")
    assert "scripts\\prebuild_v2.py --profile full" in script
    assert "build_v2.py" in script
    assert "venv-v2-windows" in script
    assert "--without-scm-ignore-files" in script
    assert "requirements-v2-release.txt" in script
    assert "Test-V2Dependencies" in script
    assert "Test-PyPiDns" in script
    assert "wheelhouse-v2\\windows-$PythonTag" in script
    assert "cannot be resolved" in script
    assert "Confirm-Operation" in script
    assert "Assert-ReleaseSource" in script
    assert "--only-binary=:all:" in wheelhouse
    assert "requirements-v2-release.txt" in wheelhouse
    assert "Maritime-Ship-Simulator-V2-$version-windows-portable.zip" in script
    assert "Extracting and testing the portable ZIP from a fresh path" in script
    assert "Ship Simulator V2.exe" in installer
    assert "Maritime Ship Simulator V2" in installer
    assert "CloseApplications=yes" in installer
    assert "CloseApplicationsFilter=Ship Simulator V2.exe" in installer
    assert "RestartApplications=no" in installer
    assert "SetupLogging=yes" in installer


def test_v2_release_requirements_exclude_unneeded_developer_tools():
    requirements = {
        line.strip()
        for line in (ROOT / "requirements-v2-release.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert requirements == {
        "certifi==2026.7.22",
        "PyInstaller==6.17.0",
        "PySide6==6.11.0",
        "pytest==9.0.3",
        "websockets==17.1",
        "pmtiles==3.8.1",
        "mapbox-vector-tile==2.2.0",
        "protobuf==6.33.6",
        "pyclipper==1.4.0",
        "shapely==2.2.0",
        "numpy==2.5.3",
        "pyogrio==0.13.0",
    }
    assert not any(line.lower().startswith(("black", "flake8", "mypy")) for line in requirements)


def test_end_user_artifacts_do_not_require_developer_tools():
    windows_installer = (ROOT / "scripts/windows-v2-installer.iss").read_text(encoding="utf-8")
    linux_launcher = (ROOT / "scripts/linux-v2-runtime-launcher.sh").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in windows_installer
    assert "python" not in windows_installer.lower()
    assert "winget" not in windows_installer.lower()
    assert linux_launcher.startswith("#!/bin/sh")
    assert "#!/usr/bin/env bash" not in linux_launcher
    assert "Required component missing" in linux_launcher


def test_release_spec_excludes_generated_metadata_and_windowed_tracebacks():
    spec = (ROOT / "ship-simulator-v2.spec").read_text(encoding="utf-8")
    assert 'path.name.startswith("._")' in spec
    assert '"__pycache__" not in path.parts' in spec
    assert "disable_windowed_traceback=True" in spec


def test_linux_runtime_sets_desktop_identity_and_window_icon():
    launcher = (ROOT / "run_v2.py").read_text(encoding="utf-8")
    assert 'LINUX_DESKTOP_FILE_ID = "maritime-ship-simulator-v2"' in launcher
    assert "app.setDesktopFileName(LINUX_DESKTOP_FILE_ID)" in launcher
    assert "window.setWindowIcon(app.windowIcon())" in launcher


def test_build_source_packager_excludes_generated_and_appledouble_files():
    packager = (ROOT / "scripts/package_v2_build_source.py").read_text(encoding="utf-8")
    assert '"requirements-v2-release.txt"' in packager
    assert '"tests/v2"' in packager
    assert '"tests/fixtures"' in packager
    assert 'path.name.startswith("._")' in packager
    assert '"release-v2"' in packager
    assert 'Path("docs/v2_prebuild_report 2.md")' in packager
    assert "BUILD_SOURCE_SHA256SUMS" in packager
    selected = {path.relative_to(ROOT).as_posix() for path in _included_files()}
    assert "FREEWARE_LICENSE.txt" in selected
    assert "assets/geodata/osm-simplified-land-polygons-3857.zip" in selected
    assert ".env" not in selected
    assert "live_ais_v2.json" not in selected
    assert "visual_settings_v2.json" not in selected


def test_release_credential_audit_rejects_keys_and_runtime_settings(tmp_path, monkeypatch):
    clean = tmp_path / "clean.txt"
    clean.write_text("No release credential is stored here.\n", encoding="utf-8")
    assert audit([clean]) == []

    fake_google_key = tmp_path / "key.txt"
    fake_google_key.write_bytes(b"AIza" + (b"A" * 35))
    findings = audit([fake_google_key])
    assert any("Google API-key pattern" in finding for finding in findings)

    named_secret = tmp_path / "provider.txt"
    secret_value = "temporary-secret-value"
    named_secret.write_text(
        "barentswatch_client_" + f'secret="{secret_value}"\n',
        encoding="utf-8",
    )
    findings = audit([named_secret])
    assert any("named provider credential" in finding for finding in findings)
    result = subprocess.run(
        (sys.executable, str(ROOT / "scripts/check_v2_release_credentials.py"), str(named_secret)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert secret_value not in result.stderr

    settings = tmp_path / "live_ais_v2.json"
    settings.write_text("{}\n", encoding="utf-8")
    findings = audit([settings])
    assert any("runtime credential file" in finding for finding in findings)

    binary_resource = tmp_path / "qtwebengine_devtools_resources.debug.pak"
    binary_resource.write_bytes(b"\x00\xff" + b"AIza" + (b"A" * 35) + b"\x00\x81")
    assert audit([binary_resource]) == []

    local_secret = "operator-local-secret-12345"
    monkeypatch.setenv("MARITIME_SIM_AISSTREAM_API_KEY", local_secret)
    binary_resource.write_bytes(b"\x00\xff" + local_secret.encode("ascii") + b"\x00\x81")
    findings = audit([binary_resource])
    assert any("credential matching environment variable" in finding for finding in findings)


def test_release_credential_audit_is_mandatory_in_all_release_paths():
    prebuild = (ROOT / "scripts/prebuild_v2.py").read_text(encoding="utf-8")
    packager = (ROOT / "scripts/package_v2_build_source.py").read_text(encoding="utf-8")
    macos = (ROOT / "scripts/build-v2-macos.sh").read_text(encoding="utf-8")
    windows = (ROOT / "scripts/build-v2-windows.ps1").read_text(encoding="utf-8")
    ubuntu = (ROOT / "scripts/build-v2-ubuntu.sh").read_text(encoding="utf-8")
    assert "check_v2_release_credentials.py" in prebuild
    assert "from check_v2_release_credentials import audit" in packager
    for content in (macos, windows, ubuntu):
        assert "check_v2_release_credentials.py" in content
        assert "Embedded credentials: none" in content


def test_macos_release_archive_rejects_appledouble_metadata():
    builder = (ROOT / "build_v2.py").read_text(encoding="utf-8")
    script = (ROOT / "scripts/build-v2-macos.sh").read_text(encoding="utf-8")
    assert 'archive_environment["COPYFILE_DISABLE"] = "1"' in builder
    assert 'MARITIME_SIMULATOR_V2_RELEASE_DIR' in builder
    assert 'export COPYFILE_DISABLE=1' in script
    assert "--sequesterRsrc" not in builder
    assert '"__MACOSX" in parts' in builder
    assert 'part.startswith("._")' in builder
    assert "_validate_release_archive(temporary_archive)" in builder
    assert "Running relocated application smoke from the extracted release ZIP" in script


def test_qt_framework_preflight_removes_only_empty_version_leftovers(tmp_path):
    framework = tmp_path / "QtWebEngineCore.framework"
    version = framework / "Versions/A"
    version.mkdir(parents=True)
    binary = version / "QtWebEngineCore"
    binary.write_bytes(b"framework-binary")
    helpers = version / "Helpers"
    helpers.mkdir()
    (helpers / "helper").write_bytes(b"helper-binary")
    (framework / "Versions/Current").symlink_to("A", target_is_directory=True)
    leftover = framework / "Versions/Resources"
    leftover.mkdir()

    build_v2._prepare_qt_framework_versions(framework)

    assert not leftover.exists()
    assert binary.read_bytes() == b"framework-binary"
    assert (helpers / "helper").read_bytes() == b"helper-binary"
    assert (framework / "Versions/Current").readlink() == Path("A")
    build_v2._prepare_qt_framework_versions(framework)


def test_qt_framework_preflight_rejects_nonempty_unknown_version(tmp_path):
    framework = tmp_path / "QtWebEngineCore.framework"
    version = framework / "Versions/A"
    version.mkdir(parents=True)
    (version / "QtWebEngineCore").write_bytes(b"framework-binary")
    unknown = framework / "Versions/Resources"
    unknown.mkdir()
    retained = unknown / "operator-file"
    retained.write_bytes(b"keep")

    with pytest.raises(SystemExit, match="No files were deleted.*Recreate"):
        build_v2._prepare_qt_framework_versions(framework)
    assert retained.read_bytes() == b"keep"


def test_qt_framework_preflight_requires_unambiguous_binary_version(tmp_path):
    framework = tmp_path / "QtWebEngineCore.framework"
    (framework / "Versions/Resources").mkdir(parents=True)
    with pytest.raises(SystemExit, match="found 0"):
        build_v2._prepare_qt_framework_versions(framework)
    assert (framework / "Versions/Resources").is_dir()
    for name in ("A", "B"):
        version = framework / "Versions" / name
        version.mkdir()
        (version / "QtWebEngineCore").write_bytes(b"framework-binary")
    with pytest.raises(SystemExit, match="found 2"):
        build_v2._prepare_qt_framework_versions(framework)


def test_v2_unsigned_production_is_explicit_and_signed_mode_remains_fail_closed():
    builder = (ROOT / "build_v2.py").read_text(encoding="utf-8")
    macos = (ROOT / "scripts/build-v2-macos.sh").read_text(encoding="utf-8")
    windows = (ROOT / "scripts/build-v2-windows.ps1").read_text(encoding="utf-8")
    ubuntu = (ROOT / "scripts/build-v2-ubuntu.sh").read_text(encoding="utf-8")
    entitlements = ROOT / "scripts/macos-v2-entitlements.plist"

    assert entitlements.is_file()
    assert 'os.environ.get("RELEASE_SIGNING", "unsigned")' in builder
    assert "MACOS_SIGN_IDENTITY" in builder
    assert 'sign_command.extend(("--options", "runtime"))' in builder
    assert 'RELEASE_SIGNING="${RELEASE_SIGNING:-unsigned}"' in macos
    assert 'PRODUCTION_RELEASE="${PRODUCTION_RELEASE:-1}"' in macos
    assert "RELEASE_SIGNING=signed requires MACOS_SIGN_IDENTITY" in macos
    assert "MACOS_NOTARY_PROFILE" in macos
    assert "notarytool submit" in macos
    assert "stapler validate" in macos
    assert "unsigned production release" in macos
    assert "RELEASE_INFO.txt" in macos

    assert "$ProductionRelease" in windows
    assert "[string]::IsNullOrWhiteSpace($env:PRODUCTION_RELEASE)" in windows
    assert '$ReleaseSigning = if (' in windows
    assert '$SignedRelease = ($ReleaseSigning -eq "signed")' in windows
    assert "Assert-WindowsSigningCredentials" in windows
    assert "Sign-WindowsArtifact" in windows
    assert "signtool.exe" in windows
    assert "A production Windows release requires Inno Setup 6" in windows
    assert "if ($ProductionRelease)" in windows
    assert "if ($SignedRelease)" in windows
    assert "unsigned production release" in windows
    assert "RELEASE_INFO.txt" in windows

    assert 'PRODUCTION_RELEASE="${PRODUCTION_RELEASE:-1}"' in ubuntu
    assert 'RELEASE_SIGNING="${RELEASE_SIGNING:-unsigned}"' in ubuntu
    assert "GPG_SIGN_KEY" in ubuntu
    assert "--detach-sign" in ubuntu
    assert "gpg --batch --verify" in ubuntu
    assert "unsigned production release" in ubuntu
    assert "RELEASE_INFO.txt" in ubuntu
    for content in (macos, windows, ubuntu):
        assert "Release type: unsigned production" in content
        assert "Release type: unsigned release candidate" in content


def test_windows_release_finds_per_user_inno_without_requiring_path_changes():
    script = (ROOT / "scripts/build-v2-windows.ps1").read_text(encoding="utf-8")
    assert "$env:INNO_ISCC" in script
    assert '"Programs\\Inno Setup 6\\ISCC.exe"' in script
    assert '"HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall"' in script
    assert "Get-ExecutablePathFromRegistryValue" in script
    assert '@("DisplayIcon", "UninstallString")' in script
    assert '"Microsoft\\WinGet\\Packages"' in script
    assert 'Get-ChildItem -Path $package.FullName -File -Filter "ISCC.exe" -Recurse' in script
    assert 'Get-ChildItem -Path $searchRoot -File -Filter "ISCC.exe" -Recurse' in script
    assert "winget reports Inno Setup as installed, but its compiler path is not registered" in script
    assert "Request-InnoCompilerPath" in script
    assert "Using Inno Setup compiler: $iscc" in script
    assert "$iscc = Find-InnoCompiler" in script
    assert "winget reported a non-zero result" in script
