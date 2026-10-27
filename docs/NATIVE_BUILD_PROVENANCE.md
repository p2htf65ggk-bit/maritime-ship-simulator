# Native build provenance 2.3.1

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
