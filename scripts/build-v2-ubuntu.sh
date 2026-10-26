#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail

MIN_PYTHON_MINOR="${MIN_PYTHON_MINOR:-12}"
PREFERRED_PYTHON_MINOR="${PREFERRED_PYTHON_MINOR:-13}"
PRODUCTION_RELEASE="${PRODUCTION_RELEASE:-1}"
RELEASE_SIGNING="${RELEASE_SIGNING:-unsigned}"
GPG_SIGN_KEY="${GPG_SIGN_KEY:-}"
UBUNTU_RELEASE_TAG="${UBUNTU_RELEASE_TAG:-ubuntu}"
UBUNTU_RELEASE_DIR="${UBUNTU_RELEASE_DIR:-release-v2/ubuntu}"
UBUNTU_COMPAT_LABEL="${UBUNTU_COMPAT_LABEL:-Ubuntu/Linux native build host}"
REQUIRED_UBUNTU_VERSION="${REQUIRED_UBUNTU_VERSION:-}"
MAX_GLIBC_VERSION="${MAX_GLIBC_VERSION:-}"

on_error() {
  local exit_code=$?
  echo "ERROR: build-v2-ubuntu.sh failed at line $1 (exit ${exit_code})." >&2
  exit "${exit_code}"
}
trap 'on_error $LINENO' ERR

step() { echo; echo "==> $1"; }
run() { echo "+ $*"; "$@"; }

noninteractive() {
  [[ "${NONINTERACTIVE:-0}" == "1" || -n "${CI:-}" || ! -t 0 ]]
}

confirm_operation() {
  local message=$1
  local default_yes=${2:-1}
  if [[ "${AUTO_APPROVE:-0}" == "1" ]]; then
    echo "AUTO_APPROVE=1: ${message}"
    return 0
  fi
  noninteractive && return 1
  local prompt="[Y/n]"
  [[ "${default_yes}" == "1" ]] || prompt="[y/N]"
  local answer
  while true; do
    read -r -p "${message} ${prompt} " answer || return 1
    answer="${answer,,}"
    if [[ -z "${answer}" ]]; then [[ "${default_yes}" == "1" ]]; return; fi
    case "${answer}" in
      y|yes|j|ja) return 0 ;;
      n|no|nein) return 1 ;;
      *) echo "Please answer yes or no." ;;
    esac
  done
}

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -P "${SCRIPT_DIR}/.." && pwd -P)"
cd "${ROOT_DIR}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: This script must run on Ubuntu/Linux." >&2
  exit 1
fi
case "${UBUNTU_RELEASE_TAG}" in
  *[!A-Za-z0-9._-]*|'') echo "ERROR: UBUNTU_RELEASE_TAG contains unsupported characters." >&2; exit 1 ;;
esac
if [[ -n "${REQUIRED_UBUNTU_VERSION}" ]]; then
  [[ -r /etc/os-release ]] || { echo "ERROR: /etc/os-release is required for this compatibility build." >&2; exit 1; }
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "${REQUIRED_UBUNTU_VERSION}" ]]; then
    echo "ERROR: This compatibility build requires Ubuntu ${REQUIRED_UBUNTU_VERSION}; detected ${ID:-unknown} ${VERSION_ID:-unknown}." >&2
    exit 1
  fi
fi

validate_release_source() {
  case "${ROOT_DIR}" in
    */.local/share/Trash/*|*/.Trash-*/*|*/Trash/*)
      echo "ERROR: This release script is physically running from a Trash folder:" >&2
      echo "  ${ROOT_DIR}" >&2
      echo "Restore or freshly extract the current project, open a new terminal in that directory, and rerun the script." >&2
      exit 2
      ;;
  esac
  local required
  for required in \
    requirements-v2-release.txt \
    scripts/prebuild_v2.py \
    scripts/linux-v2-runtime-launcher.sh \
    ship-simulator-v2.spec \
    src/maritime_simulator/__init__.py
  do
    [[ -f "${required}" ]] || {
      echo "ERROR: Incomplete or stale V2 release source; missing '${required}'." >&2
      echo "Extract a complete current source package and retry." >&2
      exit 2
    }
  done
  grep -q "required Ubuntu system libraries" scripts/linux-v2-runtime-launcher.sh || {
    echo "ERROR: Stale Linux runtime launcher detected. Use the current complete V2 source package." >&2
    exit 2
  }
  if find . -path './venv*' -prune -o -name '._*' -print -quit | grep -q .; then
    echo "INFO: macOS AppleDouble files (._*) were found and will be ignored by the build; no deletion is required."
  fi
}

validate_release_source

install_build_packages() {
  local prompt=$1
  shift
  command -v apt-get >/dev/null 2>&1 || return 1
  local -a elevate=()
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || return 1
    elevate=(sudo)
  fi
  confirm_operation "${prompt}" 1 || return 1
  run "${elevate[@]}" apt-get update &&
    run "${elevate[@]}" apt-get install -y "$@"
}

ensure_appimage_download_prerequisites() {
  [[ "${SKIP_APPIMAGE:-0}" != "1" ]] || return 0
  [[ -z "${APPIMAGETOOL_BIN:-}" ]] || return 0
  command -v appimagetool >/dev/null 2>&1 && return 0
  local tool_arch
  case "$(uname -m)" in
    x86_64) tool_arch=x86_64 ;;
    aarch64|arm64) tool_arch=aarch64 ;;
    *) echo "ERROR: No automatic appimagetool download for $(uname -m). Set APPIMAGETOOL_BIN." >&2; return 1 ;;
  esac
  [[ ! -s "${ROOT_DIR}/.tools/appimagetool-${tool_arch}.AppImage" ]] || return 0
  command -v curl >/dev/null 2>&1 && return 0

  if install_build_packages \
      "curl is needed to download appimagetool. Install curl and ca-certificates through apt before building?" \
      curl ca-certificates && command -v curl >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: curl is required to download appimagetool. Run 'sudo apt install curl ca-certificates', then rerun this build, or set APPIMAGETOOL_BIN to an existing tool." >&2
  return 1
}

ensure_appimage_download_prerequisites

python_supported() {
  "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, ${MIN_PYTHON_MINOR}) else 1)" >/dev/null 2>&1
}

detect_python() {
  local candidate
  for candidate in "python3.${PREFERRED_PYTHON_MINOR}" python3 python3.13 python3.12 python3.11 python3.14 python; do
    if command -v "${candidate}" >/dev/null 2>&1 && python_supported "${candidate}"; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

install_python() {
  command -v apt-get >/dev/null 2>&1 || return 1
  local -a elevate=()
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || return 1
    elevate=(sudo)
  fi
  confirm_operation "Python 3.${MIN_PYTHON_MINOR}+ is missing. Install Python/venv build prerequisites with apt now?" 1 || return 1
  step "Installing Python/venv prerequisites through apt"
  local -a packages=(python3 python3-venv python3-pip curl ca-certificates)
  local minor
  for minor in "${PREFERRED_PYTHON_MINOR}" "${MIN_PYTHON_MINOR}"; do
    if apt-cache show "python3.${minor}" >/dev/null 2>&1 && apt-cache show "python3.${minor}-venv" >/dev/null 2>&1; then
      packages=("python3.${minor}" "python3.${minor}-venv" python3-pip curl ca-certificates)
      break
    fi
  done
  run "${elevate[@]}" apt-get update
  run "${elevate[@]}" apt-get install -y "${packages[@]}"
}

PYTHON_BIN="${PYTHON_BIN:-$(detect_python || true)}"
if [[ -n "${PYTHON_BIN}" ]] && (! command -v "${PYTHON_BIN}" >/dev/null 2>&1 || ! python_supported "${PYTHON_BIN}"); then
  echo "ERROR: Configured PYTHON_BIN is unavailable or older than Python 3.${MIN_PYTHON_MINOR}: ${PYTHON_BIN}" >&2
  exit 1
fi
if [[ -z "${PYTHON_BIN}" ]]; then
  install_python || {
    echo "ERROR: Python 3.${MIN_PYTHON_MINOR}+ is required and automatic apt installation was unavailable." >&2
    exit 1
  }
  PYTHON_BIN="$(detect_python || true)"
fi
[[ -n "${PYTHON_BIN}" ]] || { echo "ERROR: No supported Python found after installation." >&2; exit 1; }

step "Using ${PYTHON_BIN}"
run "${PYTHON_BIN}" -c "import sys; print(sys.version)"
PYTHON_TAG="$(${PYTHON_BIN} -c 'import sys; print(f"py{sys.version_info.major}{sys.version_info.minor}")')"
PYTHON_VENV_PACKAGE="$(${PYTHON_BIN} -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}-venv")')"

ensure_venv_support() {
  "${PYTHON_BIN}" -c 'import venv, ensurepip' >/dev/null 2>&1 && return 0
  if install_build_packages \
      "The selected Python is missing venv/ensurepip support. Install ${PYTHON_VENV_PACKAGE} through apt now?" \
      "${PYTHON_VENV_PACKAGE}" && "${PYTHON_BIN}" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: The selected Python requires ${PYTHON_VENV_PACKAGE}. Run 'sudo apt install ${PYTHON_VENV_PACKAGE}', then rerun this build. This is not a network failure." >&2
  return 1
}

VENV_DIR="${V2_VENV_DIR:-venv-v2-linux}"
VENV_PYTHON="${VENV_DIR}/bin/python"
venv_interpreter_healthy() {
  [[ -x "${VENV_PYTHON}" ]] || return 1
  if command -v timeout >/dev/null 2>&1; then
    timeout 10s "${VENV_PYTHON}" -c "import sys; expected='${PYTHON_TAG}'; actual=f'py{sys.version_info.major}{sys.version_info.minor}'; raise SystemExit(0 if sys.version_info >= (3, ${MIN_PYTHON_MINOR}) and actual == expected else 1)" >/dev/null 2>&1
  else
    "${VENV_PYTHON}" -c "import sys; expected='${PYTHON_TAG}'; actual=f'py{sys.version_info.major}{sys.version_info.minor}'; raise SystemExit(0 if sys.version_info >= (3, ${MIN_PYTHON_MINOR}) and actual == expected else 1)" >/dev/null 2>&1
  fi
}

venv_healthy() {
  venv_interpreter_healthy || return 1
  if command -v timeout >/dev/null 2>&1; then
    timeout 10s "${VENV_PYTHON}" -m pip --version >/dev/null 2>&1
  else
    "${VENV_PYTHON}" -m pip --version >/dev/null 2>&1
  fi
}

if ! venv_healthy; then
  ensure_venv_support || exit 1
  if venv_interpreter_healthy; then
    step "Repairing missing pip in the existing V2 virtual environment"
    run "${VENV_PYTHON}" -m ensurepip --upgrade
  else
    if [[ -e "${VENV_DIR}" ]] && ! confirm_operation "The V2 virtual environment '${VENV_DIR}' is damaged, foreign, or uses another Python version. Delete and recreate it?" 1; then
      echo "ERROR: A usable V2 virtual environment is required. Move it manually or approve recreation." >&2
      exit 1
    fi
    step "Replacing missing, foreign or damaged V2 virtual environment"
    rm -rf "${VENV_DIR}"
    run "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  fi
fi
if ! venv_healthy; then
  echo "ERROR: ${VENV_PYTHON} is not usable with pip after environment setup. Install ${PYTHON_VENV_PACKAGE} and retry; no dependency download was attempted." >&2
  exit 1
fi

dependencies_healthy() {
  "${VENV_PYTHON}" -c "from importlib.metadata import version; from pathlib import Path; expected=dict(line.split('==', 1) for line in Path('requirements-v2-release.txt').read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')); raise SystemExit(0 if all(version(name) == wanted for name, wanted in expected.items()) else 1)" >/dev/null 2>&1 &&
    "${VENV_PYTHON}" -m pip check >/dev/null 2>&1
}

find_wheelhouse() {
  local arch candidate
  arch="$(uname -m)"
  for candidate in \
    "${V2_WHEELHOUSE:-}" \
    "wheelhouse-v2/ubuntu-${PYTHON_TAG}-${arch}" \
    "wheelhouse-v2/ubuntu"
  do
    [[ -n "${candidate}" && -d "${candidate}" ]] || continue
    compgen -G "${candidate}/*.whl" >/dev/null && { printf '%s\n' "${candidate}"; return 0; }
  done
  return 1
}

if dependencies_healthy; then
  step "Using the complete dependency set already installed in the V2 environment"
elif [[ "${SKIP_DEPENDENCY_INSTALL:-0}" == "1" ]]; then
  echo "ERROR: SKIP_DEPENDENCY_INSTALL=1 was requested, but the pinned V2 dependency set is incomplete." >&2
  exit 1
else
  WHEELHOUSE="$(find_wheelhouse || true)"
  if [[ -n "${WHEELHOUSE}" ]]; then
    step "Installing pinned V2 dependencies from offline wheelhouse: ${WHEELHOUSE}"
    run "${VENV_PYTHON}" -m pip install --disable-pip-version-check --no-index --find-links "${WHEELHOUSE}" --upgrade-strategy only-if-needed -r requirements-v2-release.txt
  else
    step "Installing pinned V2 release dependencies from PyPI"
    if ! "${VENV_PYTHON}" -m pip install --disable-pip-version-check --no-cache-dir --retries 1 --upgrade-strategy only-if-needed -r requirements-v2-release.txt; then
      if confirm_operation "Dependency installation failed. Check network/DNS/proxy settings, then retry now?" 0; then
        run "${VENV_PYTHON}" -m pip install --disable-pip-version-check --no-cache-dir --retries 1 --upgrade-strategy only-if-needed -r requirements-v2-release.txt
      else
        echo "ERROR: Dependencies are unavailable. Set V2_WHEELHOUSE to a matching local wheel directory or restore network access." >&2
        exit 1
      fi
    fi
  fi
fi
dependencies_healthy || { echo "ERROR: The pinned V2 dependency set is incomplete after installation." >&2; exit 1; }

VERSION="$(PYTHONPATH=src "${VENV_PYTHON}" -c 'from maritime_simulator import __version__; print(__version__)')"
if [[ "${VERSION}" == *-rc* || "${VERSION}" == *-beta* ]]; then
  PRODUCTION_RELEASE=0
  export PRODUCTION_RELEASE
fi
if [[ "${VERSION}" != *-* && "${PRODUCTION_RELEASE}" != "1" ]]; then
  echo "ERROR: Final version ${VERSION} requires PRODUCTION_RELEASE=1." >&2
  echo "Run the documented unsigned production command, or explicitly configure signed release credentials." >&2
  exit 1
fi
case "${RELEASE_SIGNING}" in
  unsigned|signed) ;;
  *) echo "ERROR: RELEASE_SIGNING must be 'unsigned' or 'signed'." >&2; exit 1 ;;
esac
if [[ "${RELEASE_SIGNING}" == "signed" && "${PRODUCTION_RELEASE}" != "1" ]]; then
  echo "ERROR: RELEASE_SIGNING=signed requires PRODUCTION_RELEASE=1." >&2
  exit 1
fi
if [[ "${PRODUCTION_RELEASE}" == "1" ]]; then
  [[ "${SKIP_APPIMAGE:-0}" != "1" ]] || { echo "ERROR: Production releases require the AppImage." >&2; exit 1; }
  [[ "${SKIP_DEB:-0}" != "1" ]] || { echo "ERROR: Production releases require the .deb installer." >&2; exit 1; }
fi
if [[ "${RELEASE_SIGNING}" == "signed" ]]; then
  command -v gpg >/dev/null 2>&1 || {
    echo "ERROR: RELEASE_SIGNING=signed requires GnuPG (gpg)." >&2
    exit 1
  }
  [[ -n "${GPG_SIGN_KEY}" ]] || {
    echo "ERROR: RELEASE_SIGNING=signed requires GPG_SIGN_KEY." >&2
    exit 1
  }
  gpg --batch --list-secret-keys "${GPG_SIGN_KEY}" >/dev/null 2>&1 || {
    echo "ERROR: The configured GPG signing key is not available: ${GPG_SIGN_KEY}" >&2
    exit 1
  }
elif [[ "${PRODUCTION_RELEASE}" == "1" ]]; then
  echo "NOTICE: Building an unsigned production release. SHA-256 integrity verification remains enabled." >&2
fi

export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:--p no:cacheprovider}"
step "Running the complete V2 release gate"
run "${VENV_PYTHON}" scripts/prebuild_v2.py --profile full

WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/maritime-v2-build.XXXXXX")"
cleanup() {
  if [[ "${KEEP_BUILD_WORK:-0}" != "1" ]]; then rm -rf "${WORK_ROOT}"; fi
}
trap cleanup EXIT
export MARITIME_SIMULATOR_V2_DIST_DIR="${WORK_ROOT}/dist-v2"
export MARITIME_SIMULATOR_V2_BUILD_DIR="${WORK_ROOT}/build-v2"

step "Building V2 in an isolated temporary directory"
run "${VENV_PYTHON}" build_v2.py

APP_DIR="${MARITIME_SIMULATOR_V2_DIST_DIR}/Ship Simulator V2"
APP_EXE="${APP_DIR}/Ship Simulator V2"
[[ -x "${APP_EXE}" ]] || { echo "ERROR: Missing built executable: ${APP_EXE}" >&2; exit 1; }
[[ -f "${APP_DIR}/_internal/assets/app_icon.png" ]] || { echo "ERROR: Bundled Linux icon is missing." >&2; exit 1; }
step "Auditing packaged application for embedded credentials"
run "${VENV_PYTHON}" scripts/check_v2_release_credentials.py "${APP_DIR}"

ABI_REPORT_WORK=""
if [[ -n "${MAX_GLIBC_VERSION}" ]]; then
  command -v readelf >/dev/null 2>&1 || {
    echo "ERROR: readelf is required for the Ubuntu compatibility ABI gate. Install binutils and retry." >&2
    exit 1
  }
  ABI_REPORT_WORK="${WORK_ROOT}/ABI_COMPATIBILITY.txt"
  step "Checking bundled ELF requirements against glibc ${MAX_GLIBC_VERSION}"
  run "${VENV_PYTHON}" scripts/check_linux_abi.py "${APP_DIR}" \
    --max-glibc "${MAX_GLIBC_VERSION}" --output "${ABI_REPORT_WORK}"
fi

install_runtime_prerequisites() {
  command -v apt-get >/dev/null 2>&1 || return 1
  local -a elevate=()
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || return 1
    elevate=(sudo)
  fi
  run "${elevate[@]}" apt-get update
  run "${elevate[@]}" apt-get install -y \
    libgl1 libnss3 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1
}

run_packaged_smoke() {
  local label=$1
  local argument=$2
  local output status
  step "${label}"
  if output="$(env QT_QPA_PLATFORM=offscreen SHIP_SIMULATOR_HEADLESS_MAP=1 \
      /bin/sh scripts/linux-v2-runtime-launcher.sh "${APP_DIR}" "${argument}" 2>&1)"; then
    [[ -n "${output}" ]] && printf '%s\n' "${output}"
    return 0
  else
    status=$?
  fi
  printf '%s\n' "${output}" >&2
  if [[ "${status}" -eq 127 ]] && confirm_operation "Required Ubuntu runtime libraries are missing. Install the common Qt runtime packages with apt and retry?" 1; then
    install_runtime_prerequisites || return "${status}"
    run env QT_QPA_PLATFORM=offscreen SHIP_SIMULATOR_HEADLESS_MAP=1 \
      /bin/sh scripts/linux-v2-runtime-launcher.sh "${APP_DIR}" "${argument}"
    return
  fi
  return "${status}"
}

run_packaged_smoke "Running packaged offscreen startup smoke" --smoke-test
step "Verifying real S57 import in the packaged application"
run env QT_QPA_PLATFORM=offscreen SHIP_SIMULATOR_HEADLESS_MAP=1 \
  SHIP_SIMULATOR_S57_SMOKE_CELL="${ROOT_DIR}/tests/fixtures/s57/US4NJ1DG.zip" \
  /bin/sh scripts/linux-v2-runtime-launcher.sh "${APP_DIR}" --smoke-test
run_packaged_smoke "Running packaged dialog repaint smoke" --dialog-smoke-test

RELEASE_DIR="${UBUNTU_RELEASE_DIR}"
mkdir -p "${RELEASE_DIR}"
PORTABLE="${RELEASE_DIR}/Maritime-Ship-Simulator-V2-${VERSION}-${UBUNTU_RELEASE_TAG}-portable.tar.gz"
APPIMAGE="${RELEASE_DIR}/Maritime-Ship-Simulator-V2-${VERSION}-${UBUNTU_RELEASE_TAG}.AppImage"
DEB="${RELEASE_DIR}/Maritime-Ship-Simulator-V2-${VERSION}-${UBUNTU_RELEASE_TAG}-installer.deb"
for artifact in "${PORTABLE}" "${APPIMAGE}" "${DEB}"; do
  if [[ -e "${artifact}" ]]; then
    confirm_operation "Release artifact '${artifact}' already exists. Replace it?" 1 || {
      echo "ERROR: Existing release artifact was not replaced: ${artifact}" >&2
      exit 1
    }
    rm -f "${artifact}"
  fi
done

step "Creating relocatable portable archive"
PORTABLE_STAGE="${WORK_ROOT}/portable/Maritime Ship Simulator V2"
mkdir -p "${PORTABLE_STAGE}"
cp -a "${APP_DIR}/." "${PORTABLE_STAGE}/"
cp scripts/linux-v2-runtime-launcher.sh "${WORK_ROOT}/portable/linux-v2-runtime-launcher.sh"
cat > "${WORK_ROOT}/portable/Start-Maritime-Ship-Simulator-V2.sh" <<'EOF'
#!/bin/sh
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${HERE}/linux-v2-runtime-launcher.sh" "${HERE}/Maritime Ship Simulator V2" "$@"
EOF
chmod +x "${WORK_ROOT}/portable/Start-Maritime-Ship-Simulator-V2.sh" \
  "${WORK_ROOT}/portable/linux-v2-runtime-launcher.sh"
run tar -C "${WORK_ROOT}/portable" -czf "${ROOT_DIR}/${PORTABLE}" .
[[ -s "${PORTABLE}" ]] || { echo "ERROR: Portable archive is empty." >&2; exit 1; }
run "${VENV_PYTHON}" scripts/check_v2_release_credentials.py "${PORTABLE}"
PORTABLE_SMOKE_DIR="${WORK_ROOT}/portable-smoke"
mkdir -p "${PORTABLE_SMOKE_DIR}"
run tar -xzf "${ROOT_DIR}/${PORTABLE}" -C "${PORTABLE_SMOKE_DIR}"
run env QT_QPA_PLATFORM=offscreen SHIP_SIMULATOR_HEADLESS_MAP=1 \
  /bin/sh "${PORTABLE_SMOKE_DIR}/Start-Maritime-Ship-Simulator-V2.sh" --smoke-test

if [[ "${SKIP_APPIMAGE:-0}" != "1" ]]; then
  APPIMAGETOOL="${APPIMAGETOOL_BIN:-$(command -v appimagetool || true)}"
  if [[ -z "${APPIMAGETOOL}" ]]; then
    MACHINE="$(uname -m)"
    case "${MACHINE}" in
      x86_64) TOOL_ARCH="x86_64" ;;
      aarch64|arm64) TOOL_ARCH="aarch64" ;;
      *) echo "ERROR: No automatic appimagetool download for ${MACHINE}. Set APPIMAGETOOL_BIN." >&2; exit 1 ;;
    esac
    TOOL_DIR="${ROOT_DIR}/.tools"
    APPIMAGETOOL="${TOOL_DIR}/appimagetool-${TOOL_ARCH}.AppImage"
    mkdir -p "${TOOL_DIR}"
    if [[ ! -s "${APPIMAGETOOL}" ]]; then
      confirm_operation "appimagetool is missing. Download it from the official AppImageKit release now?" 1 || {
        echo "ERROR: appimagetool is required unless SKIP_APPIMAGE=1 is set." >&2
        exit 1
      }
      step "Downloading appimagetool"
      run curl --fail --location --retry 2 \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${TOOL_ARCH}.AppImage" \
        --output "${APPIMAGETOOL}"
      chmod +x "${APPIMAGETOOL}"
    fi
  fi

  step "Creating AppImage"
  APPDIR="${WORK_ROOT}/MaritimeShipSimulatorV2.AppDir"
  mkdir -p "${APPDIR}/usr/lib/maritime-ship-simulator-v2" \
    "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
  cp -a "${APP_DIR}/." "${APPDIR}/usr/lib/maritime-ship-simulator-v2/"
  cp scripts/linux-v2-runtime-launcher.sh \
    "${APPDIR}/usr/lib/maritime-ship-simulator-v2/linux-v2-runtime-launcher.sh"
  chmod +x "${APPDIR}/usr/lib/maritime-ship-simulator-v2/linux-v2-runtime-launcher.sh"
  cp assets/app_icon.png "${APPDIR}/maritime-ship-simulator-v2.png"
  cp assets/app_icon.png "${APPDIR}/usr/share/icons/hicolor/256x256/apps/maritime-ship-simulator-v2.png"
  ln -s maritime-ship-simulator-v2.png "${APPDIR}/.DirIcon"
  cat > "${APPDIR}/AppRun" <<'EOF'
#!/bin/sh
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${HERE}/usr/lib/maritime-ship-simulator-v2/linux-v2-runtime-launcher.sh" \
  "${HERE}/usr/lib/maritime-ship-simulator-v2" "$@"
EOF
  chmod +x "${APPDIR}/AppRun"
  cat > "${APPDIR}/maritime-ship-simulator-v2.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Maritime Ship Simulator V2
Comment=Maritime navigation, sensor and track-control simulator
Exec=AppRun
Icon=maritime-ship-simulator-v2
Categories=Education;Science;Utility;
Terminal=false
StartupNotify=true
StartupWMClass=Ship Simulator V2
EOF
  mkdir -p "${APPDIR}/usr/share/applications"
  cp "${APPDIR}/maritime-ship-simulator-v2.desktop" \
    "${APPDIR}/usr/share/applications/maritime-ship-simulator-v2.desktop"
  run "${VENV_PYTHON}" scripts/check_v2_release_credentials.py "${APPDIR}"
  run env APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$(uname -m)" "${APPIMAGETOOL}" "${APPDIR}" "${ROOT_DIR}/${APPIMAGE}"
  chmod +x "${APPIMAGE}"
  [[ -s "${APPIMAGE}" ]] || { echo "ERROR: AppImage was not created." >&2; exit 1; }
  step "Running AppImage startup smoke"
  run env APPIMAGE_EXTRACT_AND_RUN=1 QT_QPA_PLATFORM=offscreen SHIP_SIMULATOR_HEADLESS_MAP=1 \
    "${APPIMAGE}" --smoke-test
else
  echo "SKIP_APPIMAGE=1: only the portable tar.gz was created."
fi

install_dpkg_tooling() {
  command -v apt-get >/dev/null 2>&1 || return 1
  local -a elevate=()
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || return 1
    elevate=(sudo)
  fi
  run "${elevate[@]}" apt-get update
  run "${elevate[@]}" apt-get install -y dpkg
}

install_generated_deb() {
  command -v apt-get >/dev/null 2>&1 || return 1
  local -a elevate=()
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || return 1
    elevate=(sudo)
  fi
  run "${elevate[@]}" apt-get install -y "${ROOT_DIR}/${DEB}"
}

if [[ "${SKIP_DEB:-0}" != "1" ]]; then
  if ! command -v dpkg-deb >/dev/null 2>&1; then
    confirm_operation "dpkg-deb is missing. Install the Ubuntu package tooling now?" 1 || {
      echo "ERROR: dpkg-deb is required unless SKIP_DEB=1 is set." >&2
      exit 1
    }
    install_dpkg_tooling || {
      echo "ERROR: Could not install dpkg-deb automatically." >&2
      exit 1
    }
  fi

  step "Creating Ubuntu desktop installer (.deb)"
  DEB_ROOT="${WORK_ROOT}/deb-root"
  DEB_APP_DIR="${DEB_ROOT}/opt/maritime-ship-simulator-v2"
  DEB_ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
  if [[ -z "${DEB_ARCH}" ]]; then
    case "$(uname -m)" in
      x86_64) DEB_ARCH=amd64 ;;
      aarch64|arm64) DEB_ARCH=arm64 ;;
      *) echo "ERROR: Unsupported Debian package architecture: $(uname -m)" >&2; exit 1 ;;
    esac
  fi
  # Keep Debian's prerelease separator literal on all supported Bash versions.
  DEB_VERSION=${VERSION/-/\~}
  mkdir -p \
    "${DEB_ROOT}/DEBIAN" \
    "${DEB_APP_DIR}" \
    "${DEB_ROOT}/usr/bin" \
    "${DEB_ROOT}/usr/share/applications" \
    "${DEB_ROOT}/usr/share/icons/hicolor/256x256/apps"
  cp -a "${APP_DIR}/." "${DEB_APP_DIR}/"
  cp scripts/linux-v2-runtime-launcher.sh "${DEB_APP_DIR}/linux-v2-runtime-launcher.sh"
  chmod +x "${DEB_APP_DIR}/linux-v2-runtime-launcher.sh"
  cat > "${DEB_ROOT}/usr/bin/maritime-ship-simulator-v2" <<'EOF'
#!/bin/sh
exec /opt/maritime-ship-simulator-v2/linux-v2-runtime-launcher.sh \
  /opt/maritime-ship-simulator-v2 "$@"
EOF
  chmod 0755 "${DEB_ROOT}/usr/bin/maritime-ship-simulator-v2"
  cp assets/app_icon.png \
    "${DEB_ROOT}/usr/share/icons/hicolor/256x256/apps/maritime-ship-simulator-v2.png"
  cat > "${DEB_ROOT}/usr/share/applications/maritime-ship-simulator-v2.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Maritime Ship Simulator V2
Comment=Maritime navigation, sensor and track-control simulator
Exec=/usr/bin/maritime-ship-simulator-v2
Icon=maritime-ship-simulator-v2
Categories=Education;Science;Utility;
Terminal=false
StartupNotify=true
StartupWMClass=Ship Simulator V2
EOF
  cat > "${DEB_ROOT}/DEBIAN/control" <<EOF
Package: maritime-ship-simulator-v2
Version: ${DEB_VERSION}
Section: science
Priority: optional
Architecture: ${DEB_ARCH}
Maintainer: Maritime Ship Simulator <noreply@localhost>
Depends: libgl1, libnss3, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1
Installed-Size: $(du -sk "${DEB_APP_DIR}" | awk '{print $1}')
Description: Maritime Ship Simulator V2
 Maritime navigation, sensor, protocol and track-control simulator.
EOF
  cat > "${DEB_ROOT}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
exit 0
EOF
  cat > "${DEB_ROOT}/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
exit 0
EOF
  chmod 0755 "${DEB_ROOT}/DEBIAN/postinst" "${DEB_ROOT}/DEBIAN/postrm"
  run "${VENV_PYTHON}" scripts/check_v2_release_credentials.py "${DEB_ROOT}"
  run dpkg-deb --root-owner-group --build "${DEB_ROOT}" "${ROOT_DIR}/${DEB}"
  [[ -s "${DEB}" ]] || { echo "ERROR: Ubuntu .deb installer was not created." >&2; exit 1; }
  DEB_DEPENDS="$(dpkg-deb --field "${DEB}" Depends)"
  for package in libgl1 libnss3 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1; do
    printf '%s\n' "${DEB_DEPENDS}" | grep -Eq "(^|, )[[:space:]]*${package}([[:space:](,]|$)" || {
      echo "ERROR: Ubuntu installer dependency metadata is missing ${package}." >&2
      exit 1
    }
  done
  dpkg-deb --contents "${DEB}" > "${WORK_ROOT}/deb-contents.txt"
  grep -q 'usr/share/applications/maritime-ship-simulator-v2.desktop' "${WORK_ROOT}/deb-contents.txt" || {
    echo "ERROR: Ubuntu installer is missing its desktop registration." >&2
    exit 1
  }
  grep -q 'usr/share/icons/hicolor/256x256/apps/maritime-ship-simulator-v2.png' "${WORK_ROOT}/deb-contents.txt" || {
    echo "ERROR: Ubuntu installer is missing its application icon." >&2
    exit 1
  }
  if confirm_operation "Install and register the generated Maritime Ship Simulator V2 package on this Ubuntu system now?" 1; then
    install_generated_deb || {
      echo "ERROR: The .deb was created but could not be installed automatically." >&2
      echo "Install it later with: sudo apt install ./${DEB}" >&2
      exit 1
    }
  fi
else
  echo "SKIP_DEB=1: no Ubuntu desktop installer was created."
fi

step "Writing SHA-256 manifest"
RELEASE_INFO="${RELEASE_DIR}/RELEASE_INFO.txt"
if [[ "${RELEASE_SIGNING}" == "signed" ]]; then
  printf 'Maritime Ship Simulator V2 %s\nRelease type: signed production\nCompatibility target: %s\n' "${VERSION}" "${UBUNTU_COMPAT_LABEL}" > "${RELEASE_INFO}"
elif [[ "${PRODUCTION_RELEASE}" == "1" ]]; then
  printf 'Maritime Ship Simulator V2 %s\nRelease type: unsigned production\nCompatibility target: %s\nEmbedded credentials: none; provider credentials are supplied by the operator at runtime.\nPlatform trust warning: package authenticity is not certified; verify SHA256SUMS.\n' "${VERSION}" "${UBUNTU_COMPAT_LABEL}" > "${RELEASE_INFO}"
else
  printf 'Maritime Ship Simulator V2 %s\nRelease type: unsigned release candidate\nCompatibility target: %s\nPlatform trust warning: package authenticity is not certified; verify SHA256SUMS.\n' "${VERSION}" "${UBUNTU_COMPAT_LABEL}" > "${RELEASE_INFO}"
fi
ABI_REPORT=""
if [[ -n "${ABI_REPORT_WORK}" ]]; then
  ABI_REPORT="${RELEASE_DIR}/ABI_COMPATIBILITY.txt"
  cp "${ABI_REPORT_WORK}" "${ABI_REPORT}"
fi
(
  cd "${RELEASE_DIR}"
  artifacts=("$(basename "${PORTABLE}")" "$(basename "${RELEASE_INFO}")")
  if [[ -s "$(basename "${APPIMAGE}")" ]]; then artifacts+=("$(basename "${APPIMAGE}")"); fi
  if [[ -s "$(basename "${DEB}")" ]]; then artifacts+=("$(basename "${DEB}")"); fi
  if [[ -n "${ABI_REPORT}" && -s "$(basename "${ABI_REPORT}")" ]]; then artifacts+=("$(basename "${ABI_REPORT}")"); fi
  sha256sum "${artifacts[@]}" > SHA256SUMS
)

if [[ "${RELEASE_SIGNING}" == "signed" ]]; then
  step "Signing and verifying the Ubuntu release manifest"
  rm -f "${RELEASE_DIR}/SHA256SUMS.asc"
  run gpg --batch --yes --armor --detach-sign --local-user "${GPG_SIGN_KEY}" \
    --output "${RELEASE_DIR}/SHA256SUMS.asc" "${RELEASE_DIR}/SHA256SUMS"
  run gpg --batch --verify "${RELEASE_DIR}/SHA256SUMS.asc" "${RELEASE_DIR}/SHA256SUMS"
else
  rm -f "${RELEASE_DIR}/SHA256SUMS.asc"
fi

step "Ubuntu V2 release completed"
ls -lah "${RELEASE_DIR}"
