# Native builds - 2.3.0

Use `run_v2.py`, `build_v2.py` and the scripts containing `build-v2-`.
This snapshot contains no earlier application or release archive. Python 3.12+
and the pinned `requirements-v2-release.txt` are required; internet or a matching
wheelhouse is needed when dependencies are not already installed.

## macOS

```bash
bash scripts/build-v2-macos.sh
```

The default build environment is `venv`. If already using `.venv`, set
`V2_VENV_DIR=.venv`; reuse pinned dependencies with `SKIP_DEPENDENCY_INSTALL=1`
only when they are complete. Output: versioned app ZIP and DMG under
`release-v2/macos/`.

## Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build-v2-windows.ps1
```

Use Windows Python 3.12+, preferably 3.13, and Inno Setup 6. The script searches
per-user/system Inno installations even when PATH is not set; `INNO_ISCC` can
identify its compiler explicitly. Output: portable ZIP and installer EXE under
`release-v2/windows/`. The script can use a Python-version-matched wheelhouse;
see `scripts/prepare-v2-windows-wheelhouse.ps1`.

## Ubuntu

```bash
bash scripts/build-v2-ubuntu.sh
# Run this alternative on Ubuntu 22.04 itself for that compatibility baseline:
bash scripts/build-v2-ubuntu-22.04.sh
```

Output: portable tar.gz, AppImage and `.deb` under the relevant `release-v2/`
platform directory. The `.deb` declares Qt/XCB runtime dependencies. Apt needs
internet or a prepared local package source if those system libraries are
missing. Installing the finished application does not require a Python Venv.

Ubuntu 22.04 packages must be built on that GLIBC baseline and pass the bundled
ELF ABI gate. A binary built on newer Ubuntu cannot be made compatible simply
by renaming it. The wrapper can prepare a suitable Python interpreter when the
distribution's default is too old; review the package/repository prompts.

A 22.04 amd64 build can serve newer Ubuntu machines when tested there as well;
two separate Ubuntu builds are not automatically required. CPU architectures
still need separate packages. Validate both the oldest and newer target systems.

## Gates and signatures

All scripts default to unsigned production packaging and run the full tests,
Track comparison, performance checks, credential audits, packaged smokes and
SHA-256 generation. macOS uses ad-hoc internal bundle signatures, not Developer
ID/notarization. SmartScreen/Gatekeeper may require user approval. No signing
certificate or private key is included.

Each platform must be built/tested natively; PyInstaller is not a cross-compiler.
Test a fresh-path extracted package, UI dialogs, offline charts, local outputs
and available serial hardware before distributing it. Review third-party binary
notices/source obligations separately; the root MPL license covers original
project code, not every dependency in a frozen application.

Do not disable a failing release gate. `artifacts/performance-v2-build.log`
contains performance subprocess details; Unix signals and Windows process
statuses are reported separately.
