# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts import collect_release_licenses as licenses
from scripts.prepare_upstream_notices import is_notice, prepare

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_dependency_closure_ignores_optional_extras(monkeypatch):
    distributions = {
        "root": SimpleNamespace(requires=["needed>=1", "optional; extra == 'test'"]),
        "needed": SimpleNamespace(requires=["root"]),
        "pyinstaller": SimpleNamespace(requires=["build-only"]),
    }
    monkeypatch.setattr(licenses.metadata, "distribution", distributions.__getitem__)
    assert set(licenses.runtime_distributions(["root"])) == {"root", "needed", "pyinstaller"}


@pytest.mark.parametrize("name", ["LICENSE", "COPYING.LESSER", "NOTICE.txt", "dragon4_LICENSE.txt", "a/LICENSES/MIT.txt", "qt_attribution.json", "README.chromium"])
def test_upstream_notice_selection(name):
    assert is_notice(name)


def test_upstream_archive_must_match_official_checksum(tmp_path):
    source = tmp_path / "changed.tar.xz"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Unverified qt source"):
        prepare({"qt": source}, tmp_path / "output")


def test_prepared_qt_notices_include_lgpl_and_chromium():
    manifest = json.loads((ROOT / "LICENSES/upstream/upstream-notices.json").read_text())
    for component in ("qt", "pyside"):
        entry = manifest["archives"][component]
        path = ROOT / "LICENSES/upstream" / entry["filename"]
        assert hashlib.file_digest(path.open("rb"), "sha256").hexdigest() == entry["sha256"]
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            assert len(names) == entry["entries"]
            assert any(name.endswith("/LGPL-3.0-only.txt") for name in names)
            assert any(name.endswith("/GPL-3.0-only.txt") for name in names)
            if component == "qt":
                assert sum("/chromium/" in name for name in names) > 1000


def test_notice_integrity_rejects_changed_file(tmp_path):
    (tmp_path / "dependency-inventory.json").write_text(json.dumps({"dependencies": ["sample"], "chromium": "test"}))
    (tmp_path / "notice.txt").write_text("changed")
    (tmp_path / "SHA256SUMS").write_text("0" * 64 + "  notice.txt\n")
    with pytest.raises(ValueError, match="Changed bundled"):
        licenses.validate(tmp_path)


def test_notice_checksum_paths_cannot_escape(tmp_path):
    (tmp_path / "dependency-inventory.json").write_text(json.dumps({"dependencies": ["sample"], "chromium": "test"}))
    (tmp_path / "SHA256SUMS").write_text("0" * 64 + "  ../outside\n")
    with pytest.raises(ValueError, match="Invalid notice checksum path"):
        licenses.validate(tmp_path)


def test_all_native_builds_collect_notices_before_signing():
    builder = (ROOT / "build_v2.py").read_text()
    main = builder[builder.index("def main()") :]
    assert main.index("collect(ROOT, notices)") < main.index('"PyInstaller"')
    assert main.index("_validate()") < main.index("_finalize_macos_bundle()")
    assert 'validate_notices(resources / "THIRD_PARTY_LICENSES")' in builder
    spec = (ROOT / "ship-simulator-v2.spec").read_text()
    assert 'modules=[entry[0] for entry in a.pure]' in spec
    assert '"pytest", "_pytest", "pygments", "IPython"' in spec
