# HUD Preview 2.3.1-beta.hud.1

Unsigned test build, 27 October 2026, based on stable 2.3.1. This branch does not
replace the stable release, change its tag or publish GitHub Release assets.
Download the four platform artifacts from the corresponding GitHub Actions run.

## Test Packages

- Windows x64.
- Ubuntu 26.04 amd64.
- Ubuntu 22.04 amd64 compatibility build.
- macOS Apple silicon arm64.

Use the portable package to test alongside an installed stable application.
Installers use the existing application identity and may replace the installed
stable application. Packages are unsigned; operating-system security policies
may prevent launch. No API keys or operator configurations are bundled.

The HUD is enabled by default in these test packages. Settings and chart caches
are separate, under `Ship Simulator/v2/hud-beta-1` in the normal user-data parent
directory. Enter your Google API key through the 3D view's API KEY button.
Existing stable settings, charts and output endpoints are not automatically
copied or activated. An explicit `MARITIME_SIMULATOR_V2_CONFIG_DIR` override is
still respected. Setting `MARITIME_SIMULATOR_3D_HUD_BETA=0` restores the old HUD
for comparison, while retaining the separate beta profile.

## HUD Changes

- Compact semitransparent heading tape with HDG, COG and ROT.
- Speed and depth-below-keel instruments on the left; true/apparent wind and
  current set/drift on the right. AIS targets and vectors remain on the map.
- Larger bold wind-direction and current-set readouts, without colour changes.
- Instruments aligned at the top; API and view controls at the bottom with
  space reserved for the map provider's attribution.
- CAM sliders start hidden. FWD uses 9-degree elevation and a vessel-dependent
  range reduced by 20 percent. 3D uses -40-degree bearing, 15-degree elevation
  and 1.4 times the FWD range. TOP and navigation gestures retain their roles.
- Bounded depth history and reused heading ticks; no additional animation loop.

## Verification And Scope

The six-viewport browser check covers layout, readout size/weight, both wind
modes, units, current set conversion, preset restoration, bounded history and
zoom. Google/Cesium overlay regression checks use controlled fixtures; a local
QtWebEngine visual check also used real Google scenery. CI builds add native
source/package checks. None of those replaces manual testing of GPU rendering,
trackpads, high-DPI screens and long-running operation on the target computer.

Simulation dynamics, protocol scheduling and external-control behavior are not
redesigned. Chart and provider limitations of stable 2.3.1 remain applicable.
