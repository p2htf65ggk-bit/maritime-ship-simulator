# Final 2.3.1 Validation Rebuild

Prepared 29 October 2026 at the author's request. Application/package version is
2.3.1, not a beta or RC. The author subsequently confirmed successful testing
of all four final builds and approved publication on 29 October 2026.

## What Is Included

- The new 3D HUD is enabled by default for Google Maps and Cesium: heading tape,
  Speed/Depth and Wind/Current instruments; AIS remains a separate map overlay.
- Negative selected speed is red; ASTERN is red, larger and bold. AHEAD keeps
  its previous style. The secondary SOG/STW value matches wind-direction size.
- FWD: elevation 9 degrees, vessel-dependent range reduced by 20 percent.
  3D: relative bearing -40 degrees, elevation 15 degrees, 1.4 times FWD range.
  TOP is unchanged. CAM starts off; view controls and API KEY use the bottom dock.
- In-app About and User Guide describe the instruments, wind/current direction
  references, controls, camera presets, current security limits and preparation date.
- The normal `Ship Simulator/v2` settings location is restored. HUD-beta settings
  under `hud-beta-1` are not silently imported. A configuration-directory environment
  override is still respected. Review saved startup/output settings when testing.
- No Google/AIS/Open-Meteo keys, operator profiles, downloaded charts or private
  integration documentation are included. Packages remain unsigned.

## Build And Publication Boundaries

The existing manual Actions workflow builds Windows x64, macOS arm64, Ubuntu
26.04 amd64 and Ubuntu 22.04 amd64. It has read-only repository permissions,
uploads test artifacts/diagnostics and does not create releases or tags.
Use the run ID, source SHA, build-info report and artifact checksums to distinguish
this rebuild from older 2.3.1 binaries. The version string alone is not sufficient.

The successful HUD-beta run on 27 October is historical evidence, not native
qualification of these new final packages. The author reports the beta HUD
working on all four targets. Final native acceptance is now complete. Current
VALIDATION.md and NATIVE_BUILD_PROVENANCE.md identify the selected refresh builds;
the earlier release records remain available as historical sections.

The initial final-validation run `40854925666` completed Windows and both Ubuntu
jobs successfully. Its macOS source gate stopped at a timer-dependent synthetic
AIS UI test: a fixed 200 ms sleep did not observe the pending retry in time.
The follow-up changes only that test and this record, not application code or
packaging scripts. The test now observes the real Qt timer signal with a bounded
deadline, tests 150/450 ms intervals and still requires exactly one target,
an empty pending queue, a stopped timer and no later duplicate timer event.
macOS is rebuilt separately; the already successful Windows/Ubuntu packages
remain the final acceptance packages for those platforms. Use each run's exact
source SHA and checksums rather than assuming all packages share one commit.

## Final Operator Check

Before dispatch, the final branch passed 1,175 V2 tests locally on macOS in
92.11 seconds. Three PowerShell-execution tests were skipped because PowerShell
was unavailable in that local test environment; the Windows runner must execute
its native gates. The selected source passed the credential audit. The final
HUD browser check passed six viewport sizes and 72 SOG/STW sign/state cases.
About and the in-app guide were rendered and inspected; narrow-guide layout is
also covered by the source tests. These are source checks, not new native-package
acceptance or a new live-provider/GPU soak.

Back up your normal configuration before installing over an existing application.
Confirm About shows Production release 2.3.1 and preparation date 29 October 2026.
Check the in-app guide, SOG/STW and T/A switches, reverse-speed colours, DBK,
current SET, CAM, HUD, NAMES, FWD/3D/TOP, zoom/pan and attribution clearance.
Check protocol output, saved startup behavior and memory recovery on the target
computers, including the normal profile rather than only a fresh beta profile.

Windows SmartScreen/UAC warnings and Smart App Control policy blocks are not
the same as Defender malware detection. The unsigned application and installer
temporary components may still be blocked by Smart App Control (error 4551).
No trusted-signing integration or security-policy bypass was added. Passing the
build gates is not a claim of Smart App Control compatibility or real navigation
certification. The author has approved publication. The external guide includes
later security-instruction expansions without changing the tested binaries.
