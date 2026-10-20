# SPDX-License-Identifier: MPL-2.0
from pathlib import Path

from scripts.check_linux_abi import build_report, parse_glibc_versions, version_tuple


def test_parse_glibc_versions_ignores_private_and_other_abi_names():
    text = "GLIBC_2.34 GLIBC_PRIVATE GLIBCXX_3.4.30 GLIBC_2.35 GLIBC_2.34"
    assert parse_glibc_versions(text) == {"2.34", "2.35"}


def test_linux_abi_report_accepts_ubuntu_2204_baseline(tmp_path):
    results = {
        tmp_path / "app": {"2.17", "2.34"},
        tmp_path / "libQt.so": {"2.35"},
    }
    report, offenders = build_report(tmp_path, results, "2.35")
    assert offenders == []
    assert "Highest required GLIBC version: 2.35" in report
    assert "Result: PASS" in report


def test_linux_abi_report_rejects_glibc_238_for_ubuntu_2204(tmp_path):
    offender = Path(tmp_path / "Ship Simulator V2")
    report, offenders = build_report(tmp_path, {offender: {"2.35", "2.38"}}, "2.35")
    assert offenders == [(offender, "2.38")]
    assert "GLIBC_2.38" in report
    assert "Result: FAIL" in report


def test_version_comparison_is_numeric_not_lexical():
    assert version_tuple("2.38") > version_tuple("2.9")
