# Native build provenance: 2.3.0

Package verification completed on 22 October 2026 for release `v2.3.0`.
All application packages are unsigned; original project code uses MPL 2.0.

## Package and source mapping

| Package family | Build/test platform | Application source |
| --- | --- | --- |
| Windows setup EXE and portable ZIP | Windows x64; author acceptance on Windows 11 / Dell P185G | `57cec00b7a7ad242bdd40980ff8290a04133a79e`, including the accepted Windows trackpad follow-up |
| Ubuntu DEB, AppImage and portable tar.gz | Ubuntu 26.04.1 LTS amd64, as confirmed by the author | Baseline `ba367908d25bc9ebbce20dee091854b852cc59fd` |
| macOS DMG and application ZIP | macOS Apple silicon | Baseline `ba367908d25bc9ebbce20dee091854b852cc59fd` |

The release source at `57cec00b7a7ad242bdd40980ff8290a04133a79e` changes runtime code only in `ui/windows_scroll.py`,
`ui/web_input.py`, `src/maritime_simulator/ui/forward_view.py` and
`src/maritime_simulator/ui/route_planner.py` relative to the baseline. These are
the accepted Windows input correction and the route-selection stabilization
used during pinch gestures. Other follow-up changes are tests, verification
tools and documentation. No video-only features were added to the application.
Publication-only documentation updates on `main` do not alter these application
sources, packaged files or corresponding-source archives. The `v2.3.0` tag and
normal developer-source downloads identify the release source above.

The release's normal developer-source ZIP/tar.gz contains the final source.
`Maritime-Ship-Simulator-2.3.0-macos-ubuntu-corresponding-source.zip` preserves
the exact baseline source, also available from its
[Git commit](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/tree/ba367908d25bc9ebbce20dee091854b852cc59fd).
Both source families include MPL notices and build instructions, without the
private development history, operator settings, keys or chart library.
The source archive's `RELEASE_SNAPSHOT.json` and `SOURCE_SHA256SUMS` identify
its exact contents. Final GitHub asset hashes are recorded in `SHA256SUMS`.

## Verification and acceptance

- Final clean-source gate: 1,064 V2 tests, all 60 Track baselines, resource,
  credential, compile, isolated startup and performance/retained-memory checks
  pass. The performance workload covers 30 simulated minutes, not a multi-hour
  hardware soak. Tests use the existing development environment.
- Supplied Windows and Ubuntu hashes, credential scans and bundled dependency
  notice manifests pass. Compressed application modules were separately checked
  for provider credentials; no saved runtime configurations or old GLB/GLTF
  vessel assets were found in the portable archives.
- A read-only PyInstaller inspection compares bytecode semantics against source
  compiled with the corresponding Python bytecode version. All 105 first-party
  modules match on Windows, and all 105 match the baseline on Ubuntu. Source
  paths and line tables are excluded from this comparison, not program logic.
- The DEB application binary has the same SHA-256 as the Ubuntu portable binary.
  Its declared dependencies include `libxcb-icccm4` and `libxcb-keysyms1`.
- All 16 bundled browser/default-data resources checked in each Windows and
  Ubuntu portable package match the identified baseline, including map menus,
  2D/3D renderers, the default route and factory vessel profiles.
- Windows/Ubuntu include Qt and PySide 6.11.0. The prepared Qt/PySide, GEOS and
  certifi source assets match the versions in the collected inventories.
- The author reports the Windows build and its corrected mouse/trackpad behavior
  working. Ubuntu mouse/trackpad behavior was also reported working. This is
  operator acceptance, not independent coverage of every device or scenario.
- The existing macOS qualification is retained; its earlier native startup,
  relocation, S57 import and package checks are recorded in `VALIDATION.md`.

## Limits

The Windows installer and Ubuntu AppImage have not been executed on the macOS
verification host. Their supplied hashes were checked; their portable siblings
were inspected. No claim is made that all installers, GPUs, serial adapters,
display configurations or multi-hour sessions have been independently tested.
There is no prebuilt or natively qualified Ubuntu 22.04 package in this release.

New native builds are not required solely to promote the already accepted input
fix or to update documentation and handoff verification tools. Keep the supplied
application files unchanged. Publication was authorized by the author on
22 October 2026; anonymous downloads and their checksums are checked separately.
