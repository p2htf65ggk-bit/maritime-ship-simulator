#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import argparse
from pathlib import Path
import platform
import re
import shutil
import subprocess


GLIBC_PATTERN = re.compile(r"\bGLIBC_(\d+(?:\.\d+)+)\b")


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def parse_glibc_versions(text: str) -> set[str]:
    return {match.group(1) for match in GLIBC_PATTERN.finditer(text)}


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def inspect_tree(root: Path, *, readelf: str) -> dict[Path, set[str]]:
    results: dict[Path, set[str]] = {}
    candidates = [root] if root.is_file() else root.rglob("*")
    for path in candidates:
        if not path.is_file() or not is_elf(path):
            continue
        result = subprocess.run(
            (readelf, "--version-info", "--wide", str(path)),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"readelf failed for {path}: {result.stderr.strip()}")
        results[path] = parse_glibc_versions(result.stdout)
    return results


def build_report(root: Path, results: dict[Path, set[str]], maximum: str) -> tuple[str, list[tuple[Path, str]]]:
    maximum_tuple = version_tuple(maximum)
    offenders: list[tuple[Path, str]] = []
    highest = "none"
    for path, versions in results.items():
        if versions:
            file_max = max(versions, key=version_tuple)
            if highest == "none" or version_tuple(file_max) > version_tuple(highest):
                highest = file_max
            if version_tuple(file_max) > maximum_tuple:
                offenders.append((path, file_max))
    status = "PASS" if not offenders else "FAIL"
    lines = [
        "Maritime Ship Simulator V2 Linux ABI compatibility report",
        f"Root: {root}",
        f"Architecture: {platform.machine()}",
        f"Host libc: {' '.join(part for part in platform.libc_ver() if part) or 'unknown'}",
        f"Maximum permitted GLIBC version: {maximum}",
        f"Highest required GLIBC version: {highest}",
        f"ELF files inspected: {len(results)}",
        f"Result: {status}",
    ]
    if offenders:
        lines.append("Files exceeding the permitted GLIBC baseline:")
        lines.extend(f"- {path}: GLIBC_{version}" for path, version in sorted(offenders))
    return "\n".join(lines) + "\n", offenders


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject Linux release trees requiring a newer GLIBC baseline")
    parser.add_argument("root", type=Path)
    parser.add_argument("--max-glibc", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.exists():
        raise SystemExit(f"ABI inspection root does not exist: {root}")
    readelf = shutil.which("readelf")
    if not readelf:
        raise SystemExit("readelf was not found; install binutils before running the ABI gate.")
    results = inspect_tree(root, readelf=readelf)
    if not results:
        raise SystemExit(f"No ELF files were found below {root}.")
    report, offenders = build_report(root, results, args.max_glibc)
    print(report, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="ascii")
    return 1 if offenders else 0


if __name__ == "__main__":
    raise SystemExit(main())
