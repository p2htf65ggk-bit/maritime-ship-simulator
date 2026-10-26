# Release validation 2.3.1

26 October 2026. The author approved this unsigned production release and
confirmed the same Windows 11 and Ubuntu 26.04.1 LTS build computers used for 2.3.0.
This record distinguishes automated checks from operator reports.

## Completed checks

- Final public source: 1,156 V2 tests passed on macOS in 92.00 seconds,
  including executable PowerShell 7.6.9 tests of logging and failure handling.
- Focused Windows-diagnostics, Environment and public-packaging checks:
  125 passed. No release gate was bypassed to resolve the Windows font issue.
- All 60 protected Track comparisons stayed within tolerance.
- Deterministic performance and retained-memory checks passed, including
  1x/30x, Track, 10-500 AIS targets and headless UI. The memory case advances
  30 simulated minutes; it is not a multi-hour real-time GPU soak.
- Windows x64 and Ubuntu amd64 native builds completed according to the author.
  Their supplied installers/portable packages passed static version, SHA-256,
  credential, archive and exact bundled dependency-notice verification.
- macOS arm64 ZIP/DMG passed packaged/native startup, dialog repaint, real S57
  import, portable relocation, strict ad-hoc signature and DMG integrity checks.
- Frozen application inspection identified 2.3.1 and 108 application/shared
  modules in each platform's portable package. No credential findings,
  obsolete ship-model assets or excluded optional Qt module families were found.
- Each native package carries its own dependency inventory. Qt/PySide 6.11.0,
  GDAL 3.12.4 and GEOS 3.14.1 match the supplied notice/source inventory.
- The English 51-page User Guide has 40 sequential figures and 21 tables.
  Caption alignment, page bounds and visual layout were checked. The Environment
  chapter and screenshot describe the new source selectors and fallback behavior.
- The curated public source and guide exclude operator keys/configurations and
  equipment-specific installation documentation. Download hashes and public
  links are verified as part of publication.

## Build corrections

The Windows offscreen plugin uses a FreeType font database instead of the
normal Windows font backend. It was searching an absent PySide6 font directory,
making two Environment layout tests report artificial horizontal overflow.
The build now supplies installed Windows fonts and respects a valid
`QT_QPA_FONTDIR` override. The installed application's fonts/scaling did not change.

Windows builds retain timestamped transcripts, per-gate output and JUnit reports.
PowerShell execution tests cover early errors, stdout/stderr, native exit codes,
stale-exit reset and avoiding an environment-variable dump. These tests ran on
macOS PowerShell 7.6.9; the author subsequently confirmed the native Windows build.

The Ubuntu Bash prerelease conversion now preserves a literal Debian `~`.
This corrects RC/beta tests and packaging without changing final version 2.3.1.

## Scope and remaining limits

User-reported native build completion is not exhaustive equipment or hardware
qualification. Static inspection on macOS does not run Windows/Linux packages.
Native GPU/provider authentication, every DPI/trackpad/driver combination,
physical serial devices and representative long-running/8-GB-with-VM operation
remain installation-specific acceptance tasks.

Packages are unsigned. macOS ad-hoc signatures are not Developer ID signing or
notarization; SHA-256 does not certify that a program is malware-free. A stable
release label is the author's distribution choice, not a claim of certified
navigation, complete S52 portrayal or universal compatibility.

See [native provenance](NATIVE_BUILD_PROVENANCE.md), [known limitations](../KNOWN_LIMITATIONS.md)
and [library-source/replacement information](LIBRARY_REPLACEMENT.md).
