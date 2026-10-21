# SPDX-License-Identifier: MPL-2.0
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(os.name == "nt" or BASH is None, reason="Ubuntu shell behavior is exercised on POSIX")


def _function(name):
    source = (ROOT / "scripts/build-v2-ubuntu.sh").read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{.*?^\}}", source, re.MULTILINE | re.DOTALL)
    assert match is not None, name
    return match.group()


def _run(tmp_path, source, **variables):
    env = os.environ.copy()
    env.update(ROOT_DIR=str(tmp_path), CALLS=str(tmp_path / "calls.log"), **variables)
    return subprocess.run([BASH, "-c", "set -euo pipefail\n" + source],
                          env=env, text=True, capture_output=True, check=False)


MOCKS = """
HAS_CURL=0
HAS_VENV_SUPPORT=0
uname() { printf 'x86_64\\n'; }
command() {
  if [[ "$1" == "-v" ]]; then
    case "$2" in
      curl) [[ "$HAS_CURL" == 1 || "$CASE" == curl-present ]]; return ;;
      appimagetool) [[ "$CASE" == path-tool ]]; return ;;
      apt-get) [[ "$CASE" != no-apt ]]; return ;;
      sudo) [[ "$CASE" != no-sudo ]]; return ;;
    esac
  fi
  builtin command "$@"
}
confirm_operation() { [[ "$CASE" != decline ]]; }
run() {
  printf '%s\\n' "$*" >> "$CALLS"
  [[ "$CASE" != apt-failure ]] || return 1
  if [[ "$*" == *"install -y curl ca-certificates"* && "$CASE" != still-missing ]]; then HAS_CURL=1; fi
  if [[ "$*" == *"install -y python"* && "$CASE" != still-missing ]]; then HAS_VENV_SUPPORT=1; fi
  return 0
}
fake_python() { [[ "$HAS_VENV_SUPPORT" == 1 || "$CASE" == support-present ]]; }
"""


@pytest.mark.parametrize("case,ok,installs", [
    ("curl-present", True, False), ("cached-tool", True, False),
    ("path-tool", True, False), ("custom-tool", True, False), ("skip-appimage", True, False),
    ("install", True, True), ("decline", False, False), ("no-apt", False, False),
    ("no-sudo", False, False), ("apt-failure", False, True), ("still-missing", False, True),
])
def test_appimage_download_prerequisites(tmp_path, case, ok, installs):
    if case == "cached-tool":
        tool = tmp_path / ".tools/appimagetool-x86_64.AppImage"
        tool.parent.mkdir()
        tool.write_bytes(b"locally supplied tool")
        tool.chmod(0o755)
    result = _run(tmp_path, MOCKS + _function("install_build_packages") + "\n" +
                  _function("ensure_appimage_download_prerequisites") +
                  "\nensure_appimage_download_prerequisites\n",
                  CASE=case, APPIMAGETOOL_BIN="custom-tool" if case == "custom-tool" else "",
                  SKIP_APPIMAGE="1" if case == "skip-appimage" else "0")
    assert (result.returncode == 0) == ok, result.stderr
    calls = tmp_path / "calls.log"
    assert calls.exists() == installs
    if not ok:
        assert "curl is required" in result.stderr and "sudo apt install" in result.stderr
    if installs and case != "apt-failure":
        assert "apt-get install -y curl ca-certificates" in calls.read_text()


@pytest.mark.parametrize("minor", [12, 13, 14])
@pytest.mark.parametrize("case,ok", [("support-present", True), ("install", True),
                                  ("decline", False), ("still-missing", False)])
def test_venv_support_uses_selected_python_package(tmp_path, minor, case, ok):
    package = f"python3.{minor}-venv"
    result = _run(tmp_path, MOCKS + _function("install_build_packages") + "\n" +
                  _function("ensure_venv_support") + "\nensure_venv_support\n",
                  CASE=case, PYTHON_BIN="fake_python", PYTHON_VENV_PACKAGE=package)
    assert (result.returncode == 0) == ok, result.stderr
    calls = tmp_path / "calls.log"
    if case in {"install", "still-missing"}:
        assert f"apt-get install -y {package}" in calls.read_text()
    else:
        assert not calls.exists()
    if not ok:
        assert f"sudo apt install {package}" in result.stderr


def test_partial_venv_repairs_pip_without_deleting_existing_files(tmp_path):
    venv = tmp_path / "partial-venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    sentinel = venv / "preserve-existing-files"
    sentinel.write_text("keep")
    python = venv / "bin/python"
    assert subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True).returncode != 0
    source = (ROOT / "scripts/build-v2-ubuntu.sh").read_text()
    repair = source.split("if ! venv_healthy; then", 1)[1].split("dependencies_healthy() {", 1)[0]
    harness = """
run() { printf '%s\\n' "$*" >> "$CALLS"; "$@"; }
step() { :; }
confirm_operation() { echo "Unexpected destructive/install prompt" >&2; return 1; }
install_build_packages() { echo "Unexpected apt call" >&2; return 1; }
"""
    harness += "\n".join(_function(name) for name in
                         ("ensure_venv_support", "venv_interpreter_healthy", "venv_healthy"))
    harness += "\nif ! venv_healthy; then" + repair
    variables = dict(PYTHON_BIN=sys.executable, PYTHON_TAG=f"py{sys.version_info.major}{sys.version_info.minor}",
                     MIN_PYTHON_MINOR="12", PYTHON_VENV_PACKAGE=f"python{sys.version_info.major}.{sys.version_info.minor}-venv",
                     VENV_DIR=str(venv), VENV_PYTHON=str(python))
    result = _run(tmp_path, harness, **variables)
    assert result.returncode == 0, result.stderr
    assert sentinel.read_text() == "keep"
    calls = tmp_path / "calls.log"
    assert "-m ensurepip --upgrade" in calls.read_text()
    assert subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True).returncode == 0
    calls.unlink()
    result = _run(tmp_path, harness, **variables)
    assert result.returncode == 0 and not calls.exists(), result.stderr


def test_prerequisite_checks_precede_build_and_pip_dependency_download():
    source = (ROOT / "scripts/build-v2-ubuntu.sh").read_text()
    assert source.index("\nensure_appimage_download_prerequisites\n") < source.index('step "Running the complete V2 release gate"')
    assert source.index("if ! venv_healthy; then") < source.index("Installing pinned V2 release dependencies")
    assert '"${VENV_PYTHON}" -m pip --version' in _function("venv_healthy")

