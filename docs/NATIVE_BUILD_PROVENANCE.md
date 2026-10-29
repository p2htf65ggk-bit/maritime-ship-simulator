# Native build provenance 2.3.1

## Current Refresh 29 October 2026

The author tested all four selected native builds and approved publication.
These are the exact runner packages, not locally repacked applications. Compare
the current SHA256SUMS: the earlier downloads also used version 2.3.1, so the
version alone is not a build identifier.

| Packages | Build and test baseline | Source commit | Actions run |
| --- | --- | --- | --- |
| Windows setup EXE and portable ZIP | Windows x64 runner; author-tested on Windows 11 | `103693e9762563956c46b1322e565219984ead36` | [40854925666 Windows](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/actions/runs/40854925666/job/123383480330) |
| Ubuntu 26.04 amd64 DEB, AppImage and tar.gz | Ubuntu 26.04 runner; author-tested on 26.04.1 | same as Windows | [40854925666 Ubuntu 26.04](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/actions/runs/40854925666/job/123383480273) |
| Ubuntu 22.04 amd64 DEB, AppImage and tar.gz | Ubuntu 22.04 runner, GLIBC 2.35 limit; desktop-tested on 26.04 | same as Windows | [40854925666 Ubuntu 22.04](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/actions/runs/40854925666/job/123383480311) |
| macOS DMG and application ZIP | macOS 15 arm64 runner; Apple silicon | `d5a25d6edd7568418fb71e6fea735fea7f8e6fb8` | [40857875675 macOS](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/actions/runs/40857875675/job/123393406425) |

All four build reports identify Python 3.13.15, PyInstaller 6.17.0 and PySide6 /
Qt 6.11.0. Each package contains its own native dependency inventory; attached
inventories are extracted from those packages, not copied from another OS.
NATIVE_BUILD_RECORD.json records the exact runs, artifact IDs and checksums.

The first macOS job failed a source test before packaging: a fixed 200 ms wait
did not reliably observe a Qt timer. The retry changed only that test and its
validation record. It waits for the actual signal with a deadline, checks
150/450 ms intervals and requires exactly one target and no later duplicate.
Application, renderer and packaging code are identical between the two selected
build revisions. Successful Windows/Ubuntu packages were retained and all four
packages were subsequently author-tested.

### Publication Source And Documentation

The publication source adds documentation-only updates after the accepted builds:
fresh 3D screenshots, expanded first-start instructions and help-text tests.
The last Windows/macOS security-click-path paragraphs are in the external
Word/PDF guide and INSTALLATION.md; embedded help in the tested apps may be
shorter. No untested native rebuild was substituted just to expand that text.

The refreshed tag and developer archives identify the publication revision.
RELEASE_SNAPSHOT.json records its full commit ID, and SOURCE_SHA256SUMS covers
archive files. Use the exact build commits above to reproduce native artifacts;
do not assume the publication tag was their input.

The earlier v2.3.1 tag pointed to `828df491133e6030a9bc0cbf7512af24c4eb72e3`.
The author explicitly retained version 2.3.1 for this refresh. The earlier source
is preserved as `v2.3.1-before-hud`; the release notes disclose the replacement.
The former Ubuntu 22.04 addition used `6ae10a0b616bd80d5d86d4f7bc0a70e88cca1da9`,
not the refreshed compatibility build. Version 2.3.0 remains separate.

All packages remain unsigned. macOS ad-hoc signatures are not Developer ID
signing/notarization. SmartScreen/Gatekeeper approval may be required and Smart
App Control can still block unsigned components with error 4551. No manual
Ubuntu 22.04 desktop test, universal hardware compatibility or multi-hour GPU
soak is claimed. Hashes prove integrity, not malware-free software.

No provider credentials, operator profiles, chart databases or equipment-specific
integration guide are included. See [current validation](VALIDATION.md).

## Historical 26 And 27 October Record

The remaining sections describe the superseded downloads, not the current refresh.

This release contains independently built unsigned applications. Version 2.3.0
remains available separately; no 2.3.0 application is renamed as 2.3.1.

| Package | Confirmed target | Embedded Python | Qt / PySide | Verification |
| --- | --- | --- | --- | --- |
| Windows setup EXE and portable ZIP | Windows 11 x64 | 3.13.15 | 6.11.0 | Author reports native build complete; package hashes, frozen version and notices checked |
| Ubuntu DEB, AppImage and portable tar.gz | Ubuntu 26.04.1 LTS amd64 | 3.14.4 | 6.11.0 | Author reports native build complete and application working; package hashes, frozen version and notices checked |
| Additional Ubuntu 22.04 compatibility DEB, AppImage and portable tar.gz | Ubuntu 22.04 amd64 GitHub runner, GLIBC 2.35 | 3.13.15 | 6.11.0 | Full automated build checks on 22.04; author desktop-tested on Ubuntu 26.04, not a 22.04 desktop |
| macOS DMG and application ZIP | Apple silicon arm64 | 3.13.7 | 6.11.0 | Native/package startup, dialogs, S57 import, relocation, ad-hoc signature and DMG checks passed |

The original Ubuntu packages require the newer build host's GLIBC baseline.
The separately labelled `ubuntu-22.04-amd64` packages use the 22.04 baseline;
their ABI check reports a highest required GLIBC version of 2.35. A successful
automated runner check is not a full 22.04 desktop acceptance test.
Other Windows versions, Intel Macs and untested older macOS versions are not
qualified by this table.

## Source mapping

The `v2.3.1` tag identifies the public application source. The attached developer
archives contain that commit and its identifier in `RELEASE_SNAPSHOT.json`.
Each archive has a complete `SOURCE_SHA256SUMS`; the release-level `SHA256SUMS`
identifies the exact downloads.

The final public source preserves the application's runtime code from the
qualified build handoff. Some native builds preceded the last Ubuntu/Windows
build-script, test and documentation corrections. Those corrections do not
change the packaged application, simulation or runtime display scaling.
The source-data notice has a documentation-only relative-link correction.
No native rebuild is needed solely for these publication/build-check changes.

The exact final native-build handoff before publication documentation had
developer-source ZIP SHA-256:
`2c444c255ffbc2380ab295634d84fd337f3624cae1758fd8fdfdae6f1cf03108`.
This is a historical handoff identifier, not the final release archive's hash.
Use the release manifest for the final archive.

## Additional Ubuntu 22.04 build

Added to the existing release on 27 October 2026 after the author's approval.
No original application download was replaced and the `v2.3.1` tag was not moved.
Exact source: [6ae10a0b616bd80d5d86d4f7bc0a70e88cca1da9](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/tree/6ae10a0b616bd80d5d86d4f7bc0a70e88cca1da9).
The original attached developer-source archives still describe the original
release snapshot; use this exact revision to reproduce the compatibility build.

The `src`, `ui`, `run_v2.py`, `build_v2.py`, PyInstaller spec, pinned runtime
requirements and `routes` trees are identical to the original release source
`828df491133e6030a9bc0cbf7512af24c4eb72e3`. Intervening changes affect build/CI
tooling, tests and documentation, not application behavior.

[Ubuntu 22.04 job 122196983732](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/actions/runs/40506411483/job/122196983732)
completed successfully: 1,165 tests, 60 Track comparisons, performance/retained-memory
checks, packaged startup/dialog checks, credentials and ABI checks. The overall
matrix run is marked failed because its separate Windows job needed a later
test-only fix; that does not invalidate this successful Ubuntu job.

The archived Actions artifact `12736450326` has SHA-256
`a5d15ac775ae9017a93faf9bc8b2cb3a5f613f51967cc7381497eb0454dedcd8`.
Release-level `SHA256SUMS` identifies the extracted individual downloads.
The additional dependency inventory is `ubuntu-22.04-amd64-dependency-inventory.json`.
The author tested these packages on Ubuntu 26.04; no manual 22.04 desktop test
or exhaustive GPU/peripheral qualification is claimed.

## Verification boundaries

Frozen-code inspection of the original three platforms identifies version 2.3.1
and 108 first-party/shared modules in each portable package. Inspection decompresses code without running
the Windows or Ubuntu application on macOS. Ubuntu's Python 3.14 bytecode is
foreign to the audit interpreter; its version is identified without disassembly.

Windows and Ubuntu inventories were read from the returned native packages,
not copied from macOS. All use GDAL 3.12.4 and GEOS 3.14.1; other package details
are in the attached platform dependency inventories. Local package SHA-256
manifests, bundled notice manifests and the shared upstream-source hashes match.
Provider credentials, operator settings and downloaded chart databases are not
release assets.

The author confirmed the native build computers and requested publication as
Latest on 26 October 2026. These facts do not imply exhaustive GPU, serial,
network-equipment, display-scaling or multi-hour acceptance on every system.
See [validation](VALIDATION.md) for the completed checks and limitations.
