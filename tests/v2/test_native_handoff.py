# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.verify_native_handoff import expected_assets, portable_inventory

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("platform,count", [("windows", 2), ("macos", 2), ("ubuntu", 3), ("ubuntu-22.04-amd64", 3)])
def test_handoff_requires_all_platform_packages(platform, count):
    names = expected_assets(platform, "2.3.0")
    assert len(names) == count
    assert all("2.3.0-" + platform in name for name in names)


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_portable_inventory_checks_notices_without_extracting(tmp_path, separator):
    path = tmp_path / "portable.zip"
    inventory = json.dumps({"dependencies": ["sample"], "chromium": "test"}).encode()
    source = (ROOT / "LICENSES/upstream-sources.json").read_bytes()
    checksums = "\n".join(f"{hashlib.sha256(blob).hexdigest()}  {name}" for name, blob in (
        ("dependency-inventory.json", inventory), ("upstream-sources.json", source)))
    with zipfile.ZipFile(path, "w") as archive:
        prefix = "App/_internal/THIRD_PARTY_LICENSES/".replace("/", separator)
        archive.writestr(prefix + "dependency-inventory.json", inventory)
        archive.writestr(prefix + "upstream-sources.json", source)
        archive.writestr(prefix + "SHA256SUMS", checksums)
    assert portable_inventory(path)["chromium"] == "test"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("names", [
    ["App/notice", "App\\notice"], ["..\\notice"], ["C:\\notice"], ["/notice"],
])
def test_portable_inventory_rejects_ambiguous_or_unsafe_windows_paths(tmp_path, names):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "invalid")
    with pytest.raises(ValueError, match="portable archive path"):
        portable_inventory(path)


def test_old_portable_without_notices_is_not_accepted(tmp_path):
    path = tmp_path / "old.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("App/README.txt", "old")
    with pytest.raises(ValueError, match="exactly one dependency inventory"):
        portable_inventory(path)
