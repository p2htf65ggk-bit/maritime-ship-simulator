# Native builds - 2.3.1

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

Each Windows run automatically creates `artifacts/build-logs/windows-.../`.
Its `build.log` records console output, Windows/PowerShell/Python/dependency
versions, the current step and complete error location/stack. `checks/` contains
per-gate output and `v2-tests.xml`. The log location is printed before work starts
and after failure. `V2_BUILD_LOG_DIR` selects another parent directory; an
unwritable source folder uses a temporary-directory fallback.

Attach a reviewed copy of that log folder for build problems instead of
photographs of truncated output. The script does not dump environment variables
or operator configurations, but local paths or external-tool output can still
be sensitive. Windows offscreen checks use installed system fonts, respecting
an existing `QT_QPA_FONTDIR` override. This changes checks, not runtime UI scaling.

## Ubuntu

```bash
bash scripts/build-v2-ubuntu.sh
```

Output: portable tar.gz, AppImage and `.deb` under the relevant `release-v2/`
platform directory. The `.deb` declares Qt/XCB runtime dependencies. Apt needs
internet or a prepared local package source if those system libraries are
missing. Installing the finished application does not require a Python Venv.

Before building, the script checks whether curl is needed to obtain appimagetool.
An explicit, installed or cached tool keeps offline builds independent of curl.
If curl is required but missing, the script offers to install curl and
ca-certificates through APT before the long build starts.

The selected Python must also have its matching venv/ensurepip package. For
Python 3.14 this is python3.14-venv, not an arbitrary older Python's venv package.
The script offers installation with sudo where needed and validates pip inside
the actual build environment. An otherwise healthy environment missing pip is
repaired with ensurepip rather than deleted. Missing pip is not reported as a
network/DNS problem. Noninteractive builds fail early with manual instructions
unless package installation was explicitly approved. These are build-time
requirements; application users do not need curl, pip or Python venv tooling.

The 2.3.1 Linux packages were built on the author-confirmed Ubuntu 26.04.1 LTS
amd64 computer. That identifies the build/test host, not older-GLIBC compatibility.
The manual GitHub build workflow also includes a separate, initially experimental
22.04 compatibility build. This does not replace the published 2.3.1 downloads.
Debian prerelease versions preserve a
literal `~` separator across Bash versions; do not work around failed tests.

### Ubuntu 22.04 self-build

The compatibility wrapper remains available for users building on Ubuntu 22.04
itself, including a 22.04 VM:

```bash
bash scripts/build-v2-ubuntu-22.04.sh
```

This path is not natively qualified for the 2.3.1 release. It requires Python
3.12+ (the system Python 3.10 is insufficient for pinned dependencies), binutils
and matching build/runtime dependencies. Review repository/package-install
prompts rather than replacing system Python or GLIBC. Internet or preinstalled
prerequisites and a Python-version-matched wheelhouse are needed.

Ubuntu 22.04 packages must be built on that GLIBC baseline and pass the bundled
ELF ABI gate. A binary built on newer Ubuntu cannot be made compatible simply
by renaming it. The wrapper can prepare a suitable Python interpreter when the
distribution's default is too old; review the package/repository prompts.

A 22.04 amd64 build can serve newer Ubuntu machines when tested there as well;
two separate Ubuntu builds are not automatically required. CPU architectures
still need separate packages. Validate every intended target system locally.

## GitHub Actions test builds

Open **Actions > Unsigned native builds > Run workflow** on GitHub. The selected
branch is built on Windows x64, Ubuntu 26.04 amd64, Ubuntu 22.04 amd64 and macOS
Apple Silicon. The workflow uses Python 3.13.15 and the existing full release
gates. Ubuntu 22.04 must additionally pass the GLIBC 2.35 ABI check. A successful
cloud build is not a hardware or desktop-compatibility certification.

The jobs use read-only repository permissions and require no provider API keys
or signing certificates. GitHub supplies its short-lived token for the public
AppImage-tool download; no personal token needs to be added. Actions are pinned
to commit IDs and the downloaded AppImage tool is verified by SHA-256. Windows
uses the runner's Inno Setup 6 compiler and records its version in the log.

Download `unsigned-test-<target>-...` from the completed workflow run. These
artifacts contain the normal platform packages and SHA-256 manifests, and expire
after seven days. Diagnostic artifacts are uploaded even after a failed build,
include an allowlisted provenance report, and expire after fourteen days. Build
logs remain available in the workflow view subject to repository retention.

The workflow does not publish releases, move release tags, replace existing
downloads, or install the generated application on a user's computer. Test a
downloaded package on each intended desktop before manually publishing it.
macOS bundles retain ad-hoc signatures; none of these builds has a verified
publisher signature. GitHub-hosted builds do not prevent antivirus false
positives or Smart App Control/Gatekeeper trust warnings.

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

Standalone `prebuild_v2.py` also saves per-gate output and JUnit results under
its printed diagnostic directory. Use `--log-dir PATH` to choose it explicitly.
