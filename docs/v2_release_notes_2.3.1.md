# Maritime Ship Simulator V2 2.3.1

## HUD Refresh, 29 October

The author tested and approved all four refreshed 2.3.1 native builds for
publication on 29 October 2026. These replace the earlier 2.3.1 downloads.
Use the refreshed SHA256SUMS; the version string alone cannot identify the build. See the
[rebuild record](v2_3d_hud_final_validation_2026-10-29.md).

- New default 3D HUD: compact tape, Speed/Depth and true/apparent Wind/Current.
  AIS hulls, names and vectors remain on-map overlays.
- Negative selected speed and ASTERN are red; ASTERN and the secondary speed
  reference have larger bold readouts. AHEAD retains the existing style.
- FWD elevation is 9 degrees with reduced range. 3D uses -40 degrees bearing,
  15 degrees elevation and 1.4 times FWD range. CAM starts hidden.
- HUD elements sit at the top; API/view actions sit at the bottom clear of
  attribution. About and the in-app guide describe the new layout and controls.
- Standard V2 configuration storage is restored after the isolated HUD beta.
  No credentials are included. Windows packages remain unsigned; Smart App
  Control compatibility is not claimed and its policy is not bypassed.

The updated English Word/PDF guide contains fresh real-Google HUD screenshots
and precise Windows/macOS first-start instructions. These final security-text
expansions postdate the tested binaries; their embedded guide may be shorter.
No new untested application package was substituted for an approved build.

The original feature record below also applies to the refresh. Historical
build details are superseded by the current native-provenance record.

Unsigned production release, 26 October 2026. Windows 11 x64, Ubuntu 26.04.1 LTS
amd64 and macOS Apple-silicon packages are provided. See the validation and
native-provenance records for completed checks and qualification limits.

## Chart-Based Depth

- Environment > Depth selects Manual or Chart-based. Sources are preferred in
  this order: enabled installed S57 cells, GEBCO online, then the held manual depth.
  Local chart queries are independent of the displayed map source, layers and zoom.
- Nearby depth samples are interpolated. GEBCO uses numeric global-grid samples,
  not rendered map colours, and requires no API key. No global dataset is downloaded.
- The transducer is at keel level. The vessel model's editable Draft is subtracted
  once from chart water depth. Manual depth already means depth below keel/transducer.
  Instruments, DBT/DPT and advanced depth output share the resulting signal.
- If chart data disappear, the last effective depth becomes the manual fallback;
  chart operation resumes when data return. Explicit Manual stops automatic queries.
  Offline Mode uses local S57 only, then Manual, without GEBCO requests or cached GEBCO.
- Source and fallback values are saved in Environment Start-up and configuration exports.
  Existing configurations without a source remain Manual. Depth noise is independent
  of the held baseline and never changes bathymetry samples.
- Background queries have bounded caches, request spacing, time/size limits and
  error backoff. Pending results are invalidated on explicit position changes.
  Accelerated sampling allows expected travel during requests and uses coarser
  real-time sampling: 1 s below 10x, 2 s from 10x, 3 s from 30x, 5 s from 100x.
  Stationary checks remain 30 s. Protocol transmission rates are unchanged.

## Online Wind And Current

- Wind, Current and Atmospheric data independently select Manual or Online - Open-Meteo.
  Wind uses 10 m model wind; current uses surface model current. The latter is converted
  to the simulator's Current From convention. Apparent wind and drift still use the
  shared environment model, including Sensor Control and immediate manual changes.
- Missing data or Offline Mode retain the last valid values and unlock manual edits;
  requested Online sources recover automatically. Explicit Manual stops the feed.
- Sources and held values are saved in Environment Start-up and configuration exports.
  Requests are asynchronous and cached: normally ten minutes or five nautical miles
  of movement, with at least one minute between requests per feed and bounded retries.
- The Open-Meteo access icon beside Atmospheric data opens an optional customer API-key
  setting shared by the three feeds. The free endpoint is for non-commercial use only;
  commercial use requires suitable provider access. Exported configurations include
  an entered key and must be kept private. No credentials are included in the release.

## Compact Controls And Instruments

- Wind and Current fit side by side at normal sidebar width. Each speed slider sits
  above its numeric value. Very narrow panes reflow to one column. Atmospheric fields
  use a two-column grid; fonts and numeric editor sizes remain unchanged.
- Source captions may shorten to Online, while dropdown menus retain the full
  Online - Open-Meteo name. The sidebar can be widened and retains its width across
  collapse/expand. Bow-thruster actual response is cyan in both directions.

## Compatibility And Limits

- Ubuntu build tooling keeps the Debian prerelease separator `~` literal across
  Bash versions. The build gate tests the actual script assignment with a custom
  home path, preventing RC/beta versions from expanding to that directory.
  This correction does not change the application or final `2.3.1` package version.
- Windows builds automatically retain a timestamped console transcript, build
  environment details, error location/stack and per-gate logs/JUnit test results.
  Offscreen UI checks use installed Windows fonts rather than an empty internal
  PySide6 font directory. This build-only correction leaves runtime UI/scaling,
  simulation behavior and already successful Ubuntu/macOS applications unchanged.

Existing settings remain supported. Back up configuration before upgrading. Packages
are unsigned; Gatekeeper or SmartScreen can require explicit approval. macOS packages
target Apple silicon; Windows x64 and Ubuntu amd64 must be built and checked natively.
The refreshed downloads include Ubuntu 26.04 and Ubuntu 22.04 packages with
explicit baseline labels. The 22.04 packages were built and automatically
checked on a 22.04 runner, including GLIBC 2.35, then
desktop-tested by the author on Ubuntu 26.04. No manual 22.04 desktop test is
claimed. The source-build wrapper remains available. See the
[exact build/source record](NATIVE_BUILD_PROVENANCE.md).

For simulation, demonstrations and testing, not real navigation. S57 portrayal remains
experimental. GEBCO is coarse global bathymetry, not detailed harbour survey data;
Open-Meteo currents are coarse model values, not a local tidal-stream prediction.
No live tide correction, grounding guarantee or certified navigation safety is added.
Provider terms and map/data licenses apply separately from the MPL-2.0 software license.

No installer is qualified merely by passing source tests. Real GPU behavior, Windows
scaling/trackpad input, serial hardware and long-duration operation need target checks.
