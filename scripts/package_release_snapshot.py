#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Make a reproducible, history-free source handoff from a reviewed Git commit."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def package(output: Path, revision: str = "HEAD") -> tuple[Path, Path]:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)

    commit = git("rev-parse", "--verify", revision + "^{commit}").decode().strip()
    names = git("ls-tree", "-r", "--name-only", commit).decode().splitlines()
    if "DEVELOPER_SOURCE_MANIFEST.json" not in names:
        raise ValueError("Run this only in the clean public-source repository, not the private development checkout")
    commit_date = git("show", "-s", "--format=%cI", commit).decode().strip()
    with tempfile.TemporaryDirectory(prefix="maritime-release-source-") as directory:
        staging = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", commit))) as archive:
            archive.extractall(staging, filter="data")
        from scripts.check_v2_release_credentials import audit
        findings = audit([staging])
        if findings:
            raise ValueError("Source snapshot credential audit failed:\n" + "\n".join(findings))
        module = ast.parse((staging / "src/maritime_simulator/__init__.py").read_text())
        version = next(ast.literal_eval(node.value) for node in module.body
                       if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets))
        metadata = {"version": version, "source_commit": commit, "commit_date": commit_date,
                    "history_included": False, "source_license": "MPL-2.0"}
        (staging / "RELEASE_SNAPSHOT.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        manifest = staging / "SOURCE_SHA256SUMS"
        lines = []
        files = sorted(path for path in staging.rglob("*") if path.is_file() and path != manifest)
        for path in files:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            lines.append(f"{digest}  {path.relative_to(staging).as_posix()}")
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        files.append(manifest)
        files.sort()
        prefix = f"maritime-ship-simulator-{version}"
        output.mkdir(parents=True, exist_ok=True)
        basename = f"Maritime-Ship-Simulator-{version}-MPL-2.0-developer-source"
        zip_path, tar_path = output / (basename + ".zip"), output / (basename + ".tar.gz")
        timestamp = int(datetime.fromisoformat(commit_date).timestamp())
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                info = zipfile.ZipInfo(f"{prefix}/{path.relative_to(staging).as_posix()}", date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | (0o755 if path.stat().st_mode & 0o111 else 0o644)) << 16
                archive.writestr(info, path.read_bytes())
        with tar_path.open("wb") as stream, gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in files:
                info = tarfile.TarInfo(f"{prefix}/{path.relative_to(staging).as_posix()}")
                info.size = path.stat().st_size
                info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                info.mtime = timestamp
                with path.open("rb") as source:
                    archive.addfile(info, source)
    return zip_path, tar_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", default="HEAD")
    args = parser.parse_args()
    for path in package(args.output, args.commit):
        print(path)
