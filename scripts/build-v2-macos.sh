#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail

on_error() {
  local exit_code=$?
  echo "ERROR: build-v2-macos.sh failed at line $1 (exit ${exit_code})." >&2
  exit "${exit_code}"
}
trap 'on_error $LINENO' ERR

step() {
  echo
  echo "==> $1"
}

run() {
  echo "+ $*"
  "$@"
}

run_packaged() {
  # Frozen Python startup must not depend on a synced checkout's working directory.
  (
    cd "${WORK_ROOT}"
    run "$@"
  )
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

PRODUCTION_RELEASE="${PRODUCTION_RELEASE:-1}"
RELEASE_SIGNING="${RELEASE_SIGNING:-unsigned}"
MACOS_SIGN_IDENTITY="${MACOS_SIGN_IDENTITY:-}"
MACOS_NOTARY_PROFILE="${MACOS_NOTARY_PROFILE:-}"
export PRODUCTION_RELEASE RELEASE_SIGNING

# Prevent Finder metadata and AppleDouble sidecars from entering copied or
# archived release content, including projects stored on removable media.
export COPYFILE_DISABLE=1

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: This script must run on macOS." >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${V2_VENV_DIR:-venv}"
VENV_PYTHON="${VENV_DIR}/bin/python"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "ERROR: ${PYTHON_BIN} was not found. Install Python 3.12+ or set PYTHON_BIN." >&2
  exit 1
fi
if ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "ERROR: V2 requires Python 3.12 or newer." >&2
  exit 1
fi

if [[ ! -x "${VENV_PYTHON}" ]] || ! "${VENV_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
  step "Creating a healthy V2 virtual environment"
  rm -rf "${VENV_DIR}"
  run "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

if [[ "${SKIP_DEPENDENCY_INSTALL:-0}" != "1" ]]; then
  step "Installing pinned dependencies"
  run "${VENV_PYTHON}" -m pip install --disable-pip-version-check --no-cache-dir --retries 1 --upgrade pip
  run "${VENV_PYTHON}" -m pip install --disable-pip-version-check --no-cache-dir --retries 1 --upgrade-strategy only-if-needed -r requirements-v2-release.txt
fi

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
if [[ "${RELEASE_SIGNING}" == "signed" ]]; then
  [[ -n "${MACOS_SIGN_IDENTITY}" ]] || {
    echo "ERROR: RELEASE_SIGNING=signed requires MACOS_SIGN_IDENTITY." >&2
    exit 1
  }
  [[ -n "${MACOS_NOTARY_PROFILE}" ]] || {
    echo "ERROR: RELEASE_SIGNING=signed requires MACOS_NOTARY_PROFILE." >&2
    echo "Create one with: xcrun notarytool store-credentials <profile-name>" >&2
    exit 1
  }
  command -v xcrun >/dev/null 2>&1 || { echo "ERROR: xcrun is required for notarization." >&2; exit 1; }
  security find-identity -v -p codesigning | grep -F -- "${MACOS_SIGN_IDENTITY}" >/dev/null || {
    echo "ERROR: The configured Developer ID identity is not available in this keychain: ${MACOS_SIGN_IDENTITY}" >&2
    exit 1
  }
  step "Validating macOS notarization credentials"
  run xcrun notarytool history --keychain-profile "${MACOS_NOTARY_PROFILE}"
elif [[ "${PRODUCTION_RELEASE}" == "1" ]]; then
  echo "NOTICE: Building an unsigned production release. macOS Gatekeeper will require explicit user approval." >&2
fi

step "Running the complete V2 release gate"
run "${VENV_PYTHON}" scripts/prebuild_v2.py --profile full

WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/maritime-v2-build.XXXXXX")"
cleanup() {
  if [[ "${KEEP_BUILD_WORK:-0}" != "1" ]]; then rm -rf "${WORK_ROOT}"; fi
}
trap cleanup EXIT
export MARITIME_SIMULATOR_V2_DIST_DIR="${WORK_ROOT}/dist-v2"
export MARITIME_SIMULATOR_V2_BUILD_DIR="${WORK_ROOT}/build-v2"
export PYINSTALLER_CONFIG_DIR="${WORK_ROOT}/pyinstaller-config"

step "Building the V2 macOS application"
run "${VENV_PYTHON}" build_v2.py

RELEASE_DIR="release-v2/macos"
ZIP_PATH="${RELEASE_DIR}/Maritime-Ship-Simulator-V2-${VERSION}-macos.zip"
DMG_PATH="${RELEASE_DIR}/Maritime-Ship-Simulator-V2-${VERSION}-macos.dmg"
APP_PATH="${MARITIME_SIMULATOR_V2_DIST_DIR}/Ship Simulator V2.app"

[[ -d "${APP_PATH}" ]] || { echo "ERROR: Missing ${APP_PATH}" >&2; exit 1; }
[[ -s "${ZIP_PATH}" ]] || { echo "ERROR: Missing ${ZIP_PATH}" >&2; exit 1; }

step "Auditing packaged application and archive for embedded credentials"
run "${VENV_PYTHON}" scripts/check_v2_release_credentials.py "${APP_PATH}" "${ZIP_PATH}"

if [[ "${RELEASE_SIGNING}" == "signed" ]]; then
  step "Notarizing and stapling the Developer ID-signed application"
  run xcrun notarytool submit "${ZIP_PATH}" --keychain-profile "${MACOS_NOTARY_PROFILE}" --wait
  run xcrun stapler staple "${APP_PATH}"
  run xcrun stapler validate "${APP_PATH}"
  run codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
  run spctl --assess --type execute --verbose=2 "${APP_PATH}"

  # Recreate the public ZIP after stapling so the offline ticket is included.
  rm -f "${ZIP_PATH}"
  run ditto --norsrc --noextattr -c -k --keepParent "${APP_PATH}" "${ZIP_PATH}"
  run "${VENV_PYTHON}" -c \
    'from pathlib import Path; from build_v2 import _validate_release_archive; _validate_release_archive(Path(__import__("sys").argv[1]))' \
    "${ZIP_PATH}"
fi

step "Running packaged offscreen startup smoke"
run_packaged env QT_QPA_PLATFORM=offscreen SHIP_SIMULATOR_HEADLESS_MAP=1 \
  MARITIME_SIMULATOR_V2_CONFIG_DIR="${WORK_ROOT}/offscreen-smoke-config" \
  SHIP_SIMULATOR_S57_SMOKE_CELL="${ROOT_DIR}/tests/fixtures/s57/US4NJ1DG.zip" \
  "${APP_PATH}/Contents/MacOS/Ship Simulator V2" --smoke-test
step "Running packaged LaunchServices startup smoke"
run_packaged open -W -n "${APP_PATH}" \
  --env "MARITIME_SIMULATOR_V2_CONFIG_DIR=${WORK_ROOT}/native-smoke-config" \
  --args --smoke-test
step "Running packaged dialog repaint smoke"
run_packaged env QT_QPA_PLATFORM=offscreen SHIP_SIMULATOR_HEADLESS_MAP=1 \
  MARITIME_SIMULATOR_V2_CONFIG_DIR="${WORK_ROOT}/dialog-smoke-config" \
  "${APP_PATH}/Contents/MacOS/Ship Simulator V2" --dialog-smoke-test

step "Creating relocatable DMG from the verified ZIP"
STAGE_DIR="${WORK_ROOT}/dmg-stage"
mkdir -p "${STAGE_DIR}"
run ditto -x -k "${ZIP_PATH}" "${STAGE_DIR}"
run codesign --verify --deep --strict "${STAGE_DIR}/Ship Simulator V2.app"
step "Running relocated application smoke from the extracted release ZIP"
run_packaged env QT_QPA_PLATFORM=offscreen SHIP_SIMULATOR_HEADLESS_MAP=1 \
  MARITIME_SIMULATOR_V2_CONFIG_DIR="${WORK_ROOT}/relocated-smoke-config" \
  SHIP_SIMULATOR_S57_SMOKE_CELL="${ROOT_DIR}/tests/fixtures/s57/US4NJ1DG.zip" \
  "${STAGE_DIR}/Ship Simulator V2.app/Contents/MacOS/Ship Simulator V2" --smoke-test
rm -f "${DMG_PATH}"
if ! hdiutil create -volname "Maritime Ship Simulator V2" -srcfolder "${STAGE_DIR}/Ship Simulator V2.app" -ov -format UDZO "${DMG_PATH}"; then
  echo "Warning: compressed DMG creation failed; retrying with makehybrid." >&2
  rm -f "${DMG_PATH}"
  run hdiutil makehybrid -hfs -hfs-volume-name "Maritime Ship Simulator V2" \
    -o "${DMG_PATH}" "${STAGE_DIR}/Ship Simulator V2.app"
fi
[[ -s "${DMG_PATH}" ]] || { echo "ERROR: DMG was not created." >&2; exit 1; }

if [[ "${RELEASE_SIGNING}" == "signed" ]]; then
  step "Signing, notarizing and stapling the production DMG"
  run codesign --force --timestamp --sign "${MACOS_SIGN_IDENTITY}" "${DMG_PATH}"
  run codesign --verify --strict --verbose=2 "${DMG_PATH}"
  run xcrun notarytool submit "${DMG_PATH}" --keychain-profile "${MACOS_NOTARY_PROFILE}" --wait
  run xcrun stapler staple "${DMG_PATH}"
  run xcrun stapler validate "${DMG_PATH}"
fi
run hdiutil verify "${DMG_PATH}"

RELEASE_INFO="${RELEASE_DIR}/RELEASE_INFO.txt"
if [[ "${RELEASE_SIGNING}" == "signed" ]]; then
  printf 'Maritime Ship Simulator V2 %s\nRelease type: signed production\n' "${VERSION}" > "${RELEASE_INFO}"
elif [[ "${PRODUCTION_RELEASE}" == "1" ]]; then
  printf 'Maritime Ship Simulator V2 %s\nRelease type: unsigned production\nEmbedded credentials: none; provider credentials are supplied by the operator at runtime.\nPlatform trust warning: macOS Gatekeeper approval may be required.\n' "${VERSION}" > "${RELEASE_INFO}"
else
  printf 'Maritime Ship Simulator V2 %s\nRelease type: unsigned release candidate\nPlatform trust warning: macOS Gatekeeper approval may be required.\n' "${VERSION}" > "${RELEASE_INFO}"
fi

step "Writing SHA-256 manifest"
(
  cd "${RELEASE_DIR}"
  shasum -a 256 "$(basename "${ZIP_PATH}")" "$(basename "${DMG_PATH}")" "$(basename "${RELEASE_INFO}")" > SHA256SUMS
)

step "macOS V2 release completed"
ls -lah "${RELEASE_DIR}"
