# SPDX-License-Identifier: MPL-2.0
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from scripts.ci_build_metadata import collect_metadata
from scripts.ci_build_matrix import build_matrix
from scripts.package_v2_build_source import _included_files


ROOT = Path(__file__).resolve().parents[2]


def test_native_workflow_has_four_explicit_unsigned_targets_and_no_publication():
    workflow = (ROOT / ".github/workflows/native-builds.yml").read_text()
    assert {item["target"] for item in build_matrix()["include"]} == {
        "windows-x64", "ubuntu-26.04-amd64", "ubuntu-22.04-amd64", "macos-arm64",
    }
    assert "fromJSON(needs.select.outputs.matrix)" in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: read" in workflow
    assert "RELEASE_SIGNING: unsigned" in workflow
    assert "persist-credentials: false" in workflow
    assert "fail-fast: false" in workflow
    assert "if: always()" in workflow
    assert "secrets." not in workflow
    assert "contents: write" not in workflow
    assert "gh release" not in workflow
    assert "VersionInfo.FileVersion" not in workflow
    assert "Compiler engine version 6" in workflow
    for action in re.findall(r"uses: (\S+)", workflow):
        assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", action)


def test_native_scripts_respect_the_ci_python_selection():
    windows = (ROOT / "scripts/build-v2-windows.ps1").read_text()
    selection = windows.split("function Select-Python {", 1)[1]
    assert selection.index("if ($env:PYTHON_BIN)") < selection.index("Get-Command py")
    assert "throw \"PYTHON_BIN does not identify" in selection
    ubuntu = (ROOT / "scripts/build-v2-ubuntu-22.04.sh").read_text()
    assert 'PYTHON_BIN="${PYTHON_BIN:-$(find_python || true)}"' in ubuntu
    assert 'MAX_GLIBC_VERSION=2.35' in ubuntu


def test_build_source_archives_keep_the_workflow_required_by_tests():
    assert ROOT / ".github/workflows/native-builds.yml" in _included_files()
    assert ROOT / ".github/native-build-targets.json" in _included_files()


@pytest.mark.parametrize("target", [
    "windows-x64", "ubuntu-26.04-amd64", "ubuntu-22.04-amd64", "macos-arm64",
])
def test_single_platform_matrix_contains_only_the_requested_target(target):
    selected = build_matrix(target)["include"]
    assert len(selected) == 1
    assert selected[0]["target"] == target


def test_unknown_build_target_is_rejected():
    with pytest.raises(ValueError, match="Unknown native build target"):
        build_matrix("unknown-or-injected-target")


def test_matrix_command_appends_a_github_output_without_replacing_other_outputs(tmp_path):
    output = tmp_path / "outputs"
    output.write_text("existing=value\n")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci_build_matrix.py")],
        env={**os.environ, "GITHUB_OUTPUT": str(output), "BUILD_TARGET": "ubuntu-22.04-amd64"},
        capture_output=True, text=True, check=True,
    )
    matrix = json.loads(result.stdout)
    assert output.read_text() == "existing=value\nmatrix=" + json.dumps(matrix) + "\n"
    assert len(matrix["include"]) == 1
    assert matrix["include"][0]["runner"] == "ubuntu-22.04"


def test_build_metadata_uses_an_allowlist_not_the_entire_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GH_TOKEN", "test-token-not-for-artifacts")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-for-artifacts")
    monkeypatch.setenv("BUILD_RESULT", "failure")
    report = collect_metadata("ubuntu-22.04-amd64")
    assert report["source_commit"] == "a" * 40
    assert report["build_result"] == "failure"
    assert report["release_published"] is False
    assert "test-token-not-for-artifacts" not in json.dumps(report)
    assert "test-key-not-for-artifacts" not in json.dumps(report)


def test_build_metadata_command_writes_valid_json(tmp_path):
    output = tmp_path / "nested/build-info.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci_build_metadata.py"),
         "--target", "test-target", "--output", str(output)],
        capture_output=True, text=True, check=True,
        env={**os.environ, "BUILD_RESULT": "success"},
    )
    report = json.loads(output.read_text())
    assert report == json.loads(result.stdout)
    assert report["target"] == "test-target"
    assert report["build_result"] == "success"
