# Source and application releases

Copyright (c) 2026 Pascal Göllnitz.

## Open source

Original project source in this repository is available under MPL 2.0. Its
required notices and full license are in `NOTICE` and `LICENSE`. Third-party
software/data is identified separately in `THIRD_PARTY_NOTICES.md`.

## Native applications

New native installers must match their source version and pass platform checks.
The public source export adds SPDX notices and GitHub contact links and removes
private installation-only route metadata/fallbacks. The manifest records those
adjustments; simulator-readable route parameters are preserved. Older private
Freeware packages are not public 2.3.0 assets. FREEWARE_LICENSE.txt is a retained
packaging path containing the same MPL text, not an additional restriction.

## Third-party dependencies

First-party license choices do not replace rights or obligations for Qt,
QtWebEngine/Chromium, GDAL, GEOS, Python, chart data and other dependencies.
Complete notices and any required corresponding-source access or library
replacement instructions must accompany the relevant binary distribution.
Unused Qt families are excluded and checked by the build, but that does not
certify the remaining bundled components. An upstream link alone is not a
completed compliance review. See
`THIRD_PARTY_NOTICES.md` and the version-specific publication audit.

## Support and safety

The project is maintained on a best-effort basis, without promised response
times or future releases. License notices do not certify fitness for navigation.
Use the application only for simulation, training, integration and testing.
