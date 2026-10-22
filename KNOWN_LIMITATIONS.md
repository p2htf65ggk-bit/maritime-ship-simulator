# Known limitations

## Simulation and protocols

- This is a training, demonstration and test tool, not certified navigation,
  conning, passage planning or collision-avoidance equipment.
- Vessel dynamics, turning/stopping performance, 3D heights and environmental
  response are simplified. Factory parameters are not vessel-trial data.
- The bow thruster is a reduced-order tunnel model, not azimuth/azipod propulsion.
  Thruster effectiveness depends on speed through water; the motor ramps and
  reverses over time. A zero power profile has no fitted bow thruster.
- Serial capacity depends on baud rate/framing; UDP/TCP sentence count is not a
  serial load indication. Monitor Freeze never stops the protocol output.
- Fault Injection must be explicitly enabled after restart/configuration load.
  Noise affects sensor projection, not the underlying true vessel motion.

## Charts, maps and offline operation

- S-57 is experimental: unencrypted ENC import and simplified S-52-oriented
  portrayal, not the complete IHO Presentation Library. S63 is unsupported.
- Depth colours and safety contours do not provide under-keel clearance or a
  grounding guarantee. Chart/source data accuracy remains outside the simulator.
- Avoid Land needs classified water for the traffic area. First preparation can
  take time; unresolved coverage is not confirmed water. Disable Avoid Land only
  when unrestricted movement across land is acceptable.
- Detailed offline map/ENC databases are not part of configuration JSON exports.
  Download/import them first and transfer chart data separately when necessary.
- Saved offline areas have bounded coverage/detail. The coarse world coastline
  outside coverage is an orientation aid. Google imagery is never in offline exports.
- Offline mode pauses Internet-backed runtime services but leaves local LAN and
  serial output usable. It is not an operating-system firewall or air-gap guarantee.

## Providers, graphics and memory

- Live AIS/provider report rates, names, geographic coverage, authentication and
  outages are not controlled by the simulator. A connection is not proof of data.
- Live position prediction is bounded to 300 seconds and targets are retained
  for up to six minutes without provider updates. Rapid local VDM output does
  not mean an equally current real-world upstream position.
- Google 3D requires the user's enabled API/account and Internet access. Static
  photogrammetric ships are not live targets; simulator overlays are separate.
- Adaptive memory/cache budgets are not hard caps on Qt, GPU or provider usage.
  An 8 GB system running a VM may need reduced quality and representative testing.
- Hidden views can unload under memory pressure and lose browser-only transient
  state. GPU/driver performance and multi-hour operation need native acceptance tests.

## Publication and platform tests

The release designation is not a claim that every device and
scenario was tested. New Windows/Ubuntu packages
have been supplied by the author, but static file checks on macOS cannot replace
native startup, graphics, serial or extended-running checks on those systems.
Read the actual release's validation results and per-platform source mapping.
Windows 11, the named Ubuntu release and macOS Apple-silicon packages are the
identified targets, not a claim of compatibility with every older OS release.
