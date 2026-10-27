# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
from pathlib import Path
import sys


def bundled_resource_path(*parts: str) -> Path:
    """Resolve an immutable bundled resource in source and PyInstaller builds."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root, *parts)
    root = Path(__file__).resolve().parents[3]
    direct = root.joinpath(*parts)
    if direct.exists():
        return direct
    source_package = root.joinpath("src", *parts)
    return source_package if source_package.exists() else direct


def user_config_dir() -> Path:
    """Stable V2 user-data location, independent of source/install paths."""
    override = os.environ.get("MARITIME_SIMULATOR_V2_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    # This test branch must not overwrite the installed stable operator profile.
    return base / "Ship Simulator" / "v2" / "hud-beta-1"
