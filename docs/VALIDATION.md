# Developer source validation

The initial public baseline is version 2.3.0, with original code under MPL 2.0.
This snapshot omits private integration documents, old applications/history,
machine-specific route fallbacks, credentials and downloaded chart libraries.

## Current source checks

On 19 October 2026, the development tree passed 956 tests covering V2 and the
source/publication tooling, all 60 Track golden-master cases, credential/resource
checks, compilation and isolated offscreen startup. The existing pinned macOS
arm64 Python 3.13.7 environment was used, not a fresh installation.

New tests cover Rudder XDR degrees/percent over real UDP and TCP loopback,
all distinct rudder main/fallback pairs, automatic main recovery, Manual/Freeze/
Zero loss behavior, coherent dual rudders, invalid values and full configuration/
Start-up preservation. UI checks cover collapsed source summaries, dynamic port
panels, aligned rudder/bow controls and Keyboard control at the bottom.
Follow-up checks cover native mouse-wheel routing, slider resets (including
engine Stop), every bow-thruster vessel parameter in Start-up/configuration,
and real browser two-button mouse chords in both orders. Pinch and trackpad
panning were regression-tested with provider test doubles, not live API keys.
A Cocoa/QtWebEngine map smoke test passed after removing a temporary global
Python event filter; slider reset now handles only its own widget events.
The subsequent 2D-specific correction passed 138 focused tests, native Qt wheel
and mouse sequences on the actual Chromium render widget, and browser checks
for both button orders with Follow Own Ship on/off. Single right-click, pinch,
trackpad pan, detach/restore and existing map editing checks remained intact.
Native generated events are not a qualification of every physical input device.
The later macOS correction passed 140 focused tests plus native embedded/detached
map and browser checks. It handles Cocoa's shared mouse/TouchPad descriptor,
normalizes physical wheel angles, preserves half-pixel pan input and coalesces
pan rendering. Real Logi Options+ hardware remains unqualified; utility-generated
trackpad-identical events cannot be distinguished solely by Qt event metadata.
On 20 October the author confirmed the corrected wheel/trackpad behavior on his
Mac with Logi Options+. This is operator confirmation, not a general driver matrix.

The unchanged performance and retained-memory gates also passed, including
30 simulated minutes. This is not a multi-hour native hardware soak test or a
measurement of live Google/GPU behavior. Local loopback tests are not serial
hardware or external controller acceptance tests.

The curated export adds SPDX notices and GitHub contact links, removes private
route metadata and preserves all 82 simulator-readable waypoints/parameters.
SOURCE_SHA256SUMS and archive checksums identify exact snapshot contents.

## Fractional display scaling follow-up on 21 October 2026

The Windows recording showed an oversized blank native HTML select popup at
150% display scaling. Map-source and offline-detail selectors now use compact
in-page menus positioned in logical pixels; Windows scaling and map gestures
are unchanged. Temporary offline menus explicitly release their listeners.

All 15 browser cases passed: device ratios 1, 1.25, 1.5, 1.75 and 2 at three
viewport sizes, including keyboard/disabled choices and repeated menu disposal.
Actual QtWebEngine mouse/bridge checks passed on macOS at simulated 125/150%
scales and the native screen scale, without a second native popup window.
The native map wheel/trackpad, both mouse chords, detach/restore and editing
regression also passed. These are not a native Windows display-scale test.

The rebuilt macOS package passed 957 tests in 88.56 seconds, all 60 Track
baselines, performance/retained-memory gates and packaged startup, dialog,
S57 import, relocation, signature, DMG and handoff checks. The existing pinned
environment was used. Source/native packages must include map_select.js;
previous Windows/Ubuntu builds need rebuilding from this corrected snapshot.
Confirm the popup on the affected Windows computer at 100, 125 and 150% before
publication. The guide and simulator/protocol behavior are unchanged.

## Required before public binaries

Run scripts/prebuild_v2.py --profile full and the native build script on each
target OS. Test startup, relocation, dialogs, offline maps/ENCs, controller loss/
recovery, real serial/network output and provider/GPU interaction. Review the
exact bundled dependencies, full notices, corresponding-source access and
library replacement information. Excluding unused Qt modules is not a complete
binary compliance audit. The repository is now prepared privately and a draft
2.3.0 release exists. Source/docs and library-source assets have been uploaded
privately; this is not a public release. See the current
[release handoff](RELEASE_HANDOFF_2.3.0.md) for remaining platform acceptance.

## Packaging follow-up on 20 October 2026

Native builds now collect and checksum full installed dependency notices,
including transitive frozen Python modules, version-verified Qt/PySide archives
and Chromium notices. The Qt notice archive is checked against the official
6.11.0 source hash; referenced attribution license files are retained. Matching
upstream source archives and library-replacement instructions accompany binaries.
Unused pytest/Pygments/IPython runtime collection is excluded.

The macOS arm64 unsigned build was rebuilt with these packaging changes. Its
full deterministic gate, native/offscreen startup, dialogs, S57 fixture import,
ZIP relocation, ad-hoc bundle integrity and DMG verification pass. It uses the
existing pinned Python 3.13.7/PySide6 6.11.0 environment. This is not an Intel Mac,
Windows or Ubuntu qualification or a fresh-dependency-installation claim.
The final packaging gate passed 954 V2 tests and all 60 Track baseline cases;
the focused license/handoff/tooling checks passed 47 tests, including GDAL builds
without an optional GEOS linkage. This build-only follow-up leaves the generated
macOS notices unchanged. Upstream Qt/PySide
notices contain 3,576/33 files with all declared LicenseFile references resolved.

Windows and both supported Ubuntu build groups still need their native build,
installation and brief operator acceptance. The source/API test suite and Mac
checks do not establish long-duration, low-RAM or physical-device performance on
those systems. No independent legal certification is claimed.

Release labels do not certify navigation fitness. S57 portrayal remains
experimental. Maintenance is best effort, without promised response times.
