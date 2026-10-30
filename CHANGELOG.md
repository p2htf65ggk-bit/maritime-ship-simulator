# Releases

## 2.3.2 Preparation

- Generic XDR control input accepts an explicitly selected empty Unit field,
  in addition to G/P and A/P. The range is -100 to +100 percent inclusive.
- Bow Thruster, Engine Telegraph and Rudder XDR Order/Response use consistent
  dropdowns, validation, percentage conversion and configuration/Start-up storage.
- Empty measurements, malformed groups, duplicate IDs, non-finite values and
  out-of-range commands remain rejected. Existing default/saved filters are retained.
- Regression coverage includes real local UDP/TCP input and settings round trips.

Unsigned native builds and operator acceptance are pending. No 2.3.2 release has
been published. See [changes and checks](docs/v2_release_notes_2.3.2.md).

## 2.3.1

- Refreshed on 29 October 2026 after author testing of all four native builds.
  New translucent 3D instrument HUD, compact heading tape, SOG/STW selection,
  red reverse speed, true/apparent wind, current SET/drift and rolling DBK history.
  Bottom-docked actions, CAM off by default and updated FWD/3D presets.
- Fresh live-Google screenshots and an updated illustrated guide, including
  Windows SmartScreen and macOS first-start instructions. Packages remain unsigned.
- Chart-based depth from installed S57 cells, then GEBCO online, then the held
  manual value. Vessel Draft is subtracted once with the transducer at keel level.
- Independent Manual/Online - Open-Meteo wind, surface-current and atmospheric
  sources, with last-value fallback, automatic recovery and saved configuration.
- Compact responsive Environment controls and cyan bow-thruster response in
  both directions. Real-time depth-query limits adapt to simulation acceleration.
- Windows build transcripts, per-step logs, JUnit reports and correct installed
  font discovery for offscreen tests. Debian prerelease version conversion fixed.
- Updated English illustrated User Guide, source/data notices and public docs.

Unsigned packages for Windows 11 x64, Ubuntu 26.04.1 LTS amd64 and macOS Apple
silicon. See [changes](docs/v2_release_notes_2.3.1.md),
[validation](docs/VALIDATION.md) and [build provenance](docs/NATIVE_BUILD_PROVENANCE.md).

## 2.3.0

Initial public baseline under MPL 2.0. Tunnel bow-thruster dynamics, independent
external rudder/engine/thruster inputs, Main/Fallback sources and configurable
Manual/Freeze/Zero loss actions. Rudder XDR supports degrees/percent; propulsion
XDR supports A/P and legacy G/P. Input panels fit visible content and source
options collapse to a summary. Ship Control aligns rudder and bow controls below
the telegraph, with Keyboard control at the bottom.

Offline Protomaps, experimental S57/NOAA management, synthetic encounters,
fault injection and online 3D views are included. One English User Guide covers
operation. Equipment-specific integration documents and old history are excluded.
Native builds and bundled dependency notices need platform qualification before
publication. See the release notes and validation record.

The final Windows build includes physical mouse-wheel versus trackpad-source
tracking, a shared pan/zoom source lease for 2D/3D views, and stable route-row
selection during pinch gestures. The author confirmed the Windows 11/Dell
trackpad correction. Prepared Linux binaries target Ubuntu 26.04.1 LTS amd64;
macOS and Ubuntu keep the previously checked baseline, with its exact source
provided separately. See [build provenance](docs/NATIVE_BUILD_PROVENANCE.md).
