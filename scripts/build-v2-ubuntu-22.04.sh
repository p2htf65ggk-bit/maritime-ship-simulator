#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail

step() { echo; echo "==> $1"; }

noninteractive() {
  [[ "${NONINTERACTIVE:-0}" == "1" || -n "${CI:-}" || ! -t 0 ]]
}

confirm_operation() {
  local message=$1
  if [[ "${AUTO_APPROVE:-0}" == "1" ]]; then
    echo "AUTO_APPROVE=1: ${message}"
    return 0
  fi
  noninteractive && return 1
  local answer
  read -r -p "${message} [Y/n] " answer || return 1
  case "${answer,,}" in
    ''|y|yes|j|ja) return 0 ;;
    *) return 1 ;;
  esac
}

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -P "${SCRIPT_DIR}/.." && pwd -P)"
cd "${ROOT_DIR}"

[[ "$(uname -s)" == "Linux" ]] || { echo "ERROR: This script must run on Ubuntu 22.04." >&2; exit 1; }
[[ -r /etc/os-release ]] || { echo "ERROR: /etc/os-release is missing." >&2; exit 1; }
# shellcheck disable=SC1091
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
  echo "ERROR: This script creates the Ubuntu 22.04 compatibility release and must run on Ubuntu 22.04." >&2
  echo "Detected: ${ID:-unknown} ${VERSION_ID:-unknown}" >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64) RELEASE_ARCH=amd64 ;;
  aarch64|arm64) RELEASE_ARCH=arm64 ;;
  *) echo "ERROR: Unsupported Ubuntu 22.04 architecture: $(uname -m)" >&2; exit 1 ;;
esac

python_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if (3, 12) <= sys.version_info < (3, 15) else 1)' >/dev/null 2>&1
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.14 python3; do
    command -v "${candidate}" >/dev/null 2>&1 && python_supported "${candidate}" && {
      printf '%s\n' "${candidate}"
      return 0
    }
  done
  return 1
}

elevated_apt() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    apt-get "$@"
  else
    command -v sudo >/dev/null 2>&1 || { echo "ERROR: sudo is required for automatic package installation." >&2; return 1; }
    sudo apt-get "$@"
  fi
}

elevated_add_repository() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    add-apt-repository "$@"
  else
    sudo add-apt-repository "$@"
  fi
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  confirm_operation "Python 3.12+ is missing. Add the deadsnakes PPA and install Python 3.12 build support now?" || {
    echo "ERROR: Ubuntu 22.04 ships Python 3.10, but pinned V2 dependencies (including NumPy) require Python 3.12+." >&2
    echo "Install python3.12, python3.12-venv and binutils, then rerun this script." >&2
    exit 1
  }
  step "Installing the Ubuntu 22.04 Python 3.12 build toolchain"
  elevated_apt update
  elevated_apt install -y software-properties-common ca-certificates
  elevated_add_repository -y ppa:deadsnakes/ppa
  elevated_apt update
  elevated_apt install -y python3.12 python3.12-venv python3.12-dev binutils
  PYTHON_BIN="$(find_python || true)"
fi
[[ -n "${PYTHON_BIN}" ]] || { echo "ERROR: Python 3.12+ is still unavailable after installation." >&2; exit 1; }

if ! command -v readelf >/dev/null 2>&1; then
  confirm_operation "binutils/readelf is missing. Install it now for the GLIBC compatibility gate?" || {
    echo "ERROR: readelf is required to prove Ubuntu 22.04 compatibility." >&2
    exit 1
  }
  elevated_apt update
  elevated_apt install -y binutils
fi

step "Preparing the Ubuntu 22.04 compatibility build"
echo "Python: ${PYTHON_BIN}"
echo "Architecture: ${RELEASE_ARCH}"
echo "GLIBC baseline: 2.35"

export PYTHON_BIN
export MIN_PYTHON_MINOR=12
export PREFERRED_PYTHON_MINOR=12
export V2_VENV_DIR="${V2_VENV_DIR:-venv-v2-ubuntu-22.04-${RELEASE_ARCH}}"
export REQUIRED_UBUNTU_VERSION=22.04
export MAX_GLIBC_VERSION=2.35
export UBUNTU_RELEASE_TAG="ubuntu-22.04-${RELEASE_ARCH}"
export UBUNTU_RELEASE_DIR="release-v2/ubuntu-22.04-${RELEASE_ARCH}"
export UBUNTU_COMPAT_LABEL="Ubuntu 22.04 ${RELEASE_ARCH} (glibc <= 2.35)"

exec "${SCRIPT_DIR}/build-v2-ubuntu.sh" "$@"
