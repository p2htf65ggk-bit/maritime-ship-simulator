# SPDX-License-Identifier: MPL-2.0
"""Reject optional Qt families that this Widgets/WebEngine application does not use."""
from pathlib import Path
import re


UNUSED_FAMILIES = ("Charts", "DataVisualization", "Graphs", "Quick3D", "VirtualKeyboard")
PATTERN = re.compile(r"(?:lib)?Qt(?:6)?(?:" + "|".join(UNUSED_FAMILIES) + r")", re.IGNORECASE)


def audit_paths(paths):
    return [str(path) for path in paths if any(PATTERN.match(part) for part in Path(path).parts)]


def filter_build_entries(entries):
    """Drop unused plugins and their native dependencies before COLLECT/BUNDLE."""
    return [entry for entry in entries if not audit_paths(entry[:2])]


def audit_bundle(root):
    findings = audit_paths(Path(root).rglob("*"))
    if findings:
        raise ValueError("Unused Qt components found in release; review collection hooks:\n" + "\n".join(findings[:30]))
