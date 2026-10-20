# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import os
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "linux-v2-runtime-launcher.sh"


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX launcher test")
def test_linux_launcher_starts_relocatable_application_without_python_or_bash(tmp_path):
    app_dir = tmp_path / "folder with spaces" / "Maritime Ship Simulator V2"
    app_dir.mkdir(parents=True)
    _executable(app_dir / "Ship Simulator V2", "#!/bin/sh\nprintf 'started:%s' \"$1\"\n")
    result = subprocess.run(
        ("/bin/sh", str(LAUNCHER), str(app_dir), "usb path"),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "started:usb path"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX launcher test")
def test_linux_launcher_reports_missing_library_before_start(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    marker = tmp_path / "application-started"
    _executable(app_dir / "Ship Simulator V2", f"#!/bin/sh\ntouch '{marker}'\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(fake_bin / "ldd", "#!/bin/sh\nprintf 'libxcb-cursor.so.0 => not found\\n'\n")
    dialog_marker = tmp_path / "modal-dialog-opened"
    _executable(fake_bin / "zenity", f"#!/bin/sh\ntouch '{dialog_marker}'\nexit 1\n")
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    environment["DISPLAY"] = ":99"
    environment["CI"] = "1"
    result = subprocess.run(
        ("/bin/sh", str(LAUNCHER), str(app_dir)),
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 127
    assert "Required component" not in result.stderr
    assert "required Ubuntu system libraries" in result.stderr
    assert "libxcb-cursor.so.0" in result.stderr
    assert not marker.exists()
    assert not dialog_marker.exists()
