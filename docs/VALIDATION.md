# Release validation 2.3.1

## Current Refresh 29 October 2026

The author confirmed successful testing of all four final builds and approved
publication. Automated runner checks and desktop acceptance are distinguished below.

| Target | Selected run | V2 test result |
| --- | --- | --- |
| Windows x64 | `40854925666` | 1,150 passed, 28 skipped, no failures |
| Ubuntu 26.04 amd64 | `40854925666` | 1,178 passed, no failures |
| Ubuntu 22.04 amd64 | `40854925666` | 1,178 passed, no failures |
| macOS arm64 | `40857875675` | 1,179 passed, no failures |

The selected jobs passed the existing native gates, including protected Track
comparisons, performance/retained-memory checks, packaged startup, dependency
notices and credentials. Ubuntu 22.04 passed the GLIBC 2.35 ABI gate. The macOS
retry changed only a timer test and its record, not application or packaging code.
The failed first macOS job does not invalidate the other successful jobs.

Before native dispatch the final branch passed 1,175 tests locally, with three
PowerShell execution tests skipped. A browser fixture passed six viewport sizes
and 72 SOG/STW sign/state cases. New Google 3D/FWD screenshots use real provider
scenery, retained attribution and synthetic targets; the map reported a steady
loaded view before capture. They are not live traffic observations.

The updated Word/PDF guide has new HUD screenshots and first-start instructions.
Contents, captions, page bounds and rendered pages are checked before upload.
Selected package bytes are compared with their runner manifests; final release
checksums and source credentials are audited before publication. The later
security-text expansion is external; tested binaries remain unchanged.

The author desktop-tested the 22.04 build on Ubuntu 26.04, not a 22.04 desktop.
Tests are not exhaustive GPU, DPI, peripheral, serial, network-equipment or
multi-hour/8-GB-with-VM qualification. Packages are unsigned and Smart App Control
compatibility is not claimed. Stable is the author's distribution choice, not
certified navigation or complete S52 portrayal. See [current provenance](NATIVE_BUILD_PROVENANCE.md).

## Historical 26 And 27 October Record

The checks below identify the superseded downloads, not the current refresh.

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

### Ubuntu 22.04 compatibility addition

27 October 2026: the author approved the additional Ubuntu 22.04 amd64 packages
after testing the 22.04-built application on Ubuntu 26.04. No manual 22.04
desktop test is claimed. The existing release version/tag and native downloads
remain unchanged.

The Ubuntu 22.04 runner job passed 1,165 tests in 142.03 seconds, 60 Track
comparisons, performance/retained-memory checks, packaged startup/dialog and
relocated portable startup checks, credential audits and the GLIBC 2.35 gate.
Its exact source revision and successful job are linked in
[native provenance](NATIVE_BUILD_PROVENANCE.md#additional-ubuntu-2204-build).
The downloaded artifact, inner package checksums and bundled notice/source
manifests were verified before publication. The refreshed 51-page Word/PDF
guide distinguishes build baseline from the reported desktop test.

### Original release build corrections

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
