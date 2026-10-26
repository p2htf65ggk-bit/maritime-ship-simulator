# Native build provenance 2.3.1

This release contains independently built unsigned applications. Version 2.3.0
remains available separately; no 2.3.0 application is renamed as 2.3.1.

| Package | Confirmed target | Embedded Python | Qt / PySide | Verification |
| --- | --- | --- | --- | --- |
| Windows setup EXE and portable ZIP | Windows 11 x64 | 3.13.15 | 6.11.0 | Author reports native build complete; package hashes, frozen version and notices checked |
| Ubuntu DEB, AppImage and portable tar.gz | Ubuntu 26.04.1 LTS amd64 | 3.14.4 | 6.11.0 | Author reports native build complete and application working; package hashes, frozen version and notices checked |
| macOS DMG and application ZIP | Apple silicon arm64 | 3.13.7 | 6.11.0 | Native/package startup, dialogs, S57 import, relocation, ad-hoc signature and DMG checks passed |

Ubuntu compatibility is not implied for older GLIBC baselines. The 22.04 wrapper
remains a self-build option, without native 22.04 qualification for 2.3.1.
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

## Verification boundaries

Frozen-code inspection identifies version 2.3.1 and 108 first-party/shared
modules in every portable package. Inspection decompresses code without running
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
