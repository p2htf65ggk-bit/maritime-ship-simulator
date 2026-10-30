# Maritime Ship Simulator V2 2.3.2

Released 30 October 2026 as an unsigned stable update for Windows x64, macOS
arm64, Ubuntu 26.04 amd64 and Ubuntu 22.04 amd64. The existing 2.3.1 release is
preserved. This correction adds Generic XDR control input with an empty Unit.

## Generic XDR Input

Type G can now use an explicitly selected `(null field)` Unit, as well as P.
Both control formats use signed percentages from -100 to +100 inclusive.
Zero is neutral; positive is bow/rudder to starboard or engine ahead.
For rudders, percentage is converted using the selected vessel's maximum angle.
This convention applies to configured control inputs, not arbitrary generic sensors.

Select Type G, Unit `(null field)` and the sender's ID under Settings > External
Input. Example payloads, before the normal sentence framing and checksum:

```text
GPXDR,G,40,,BowThrust#0
GPXDR,G,-20,,EngTele#0
GPXDR,G,-50,,RudderOrder#0
GPXDR,G,25,,Rudder#0
```

The double comma after the number is an empty Unit. A direct `G,,` instead
means an empty measurement: it does not become a zero command or refresh the
control's valid-data age. The input-age indication can show that field as empty.
The literal text `null` is not an empty Unit.

The selection is available for Bow Thruster, Engine Telegraph, Rudder XDR Order
and Rudder XDR Response. Type, Unit and ID still match exactly; selecting G/P
does not accept an empty Unit implicitly. Existing A/D, A/P and G/P settings and
default selections are unchanged. Configuration exports, local settings and
Start-up captures preserve the empty string.

## Validation And Release Boundary

224 focused local checks passed before the version preparation, including
actual loopback UDP/TCP delivery, fragmented TCP input, boundary validation,
status indications and configuration/Start-up/UI round trips. No external serial
controller was connected. The author reports working native builds; this is not
an exhaustive hardware, serial-controller or multi-hour desktop qualification.

The full local source gate then passed: 1,213 V2 tests passed and three PowerShell
execution cases were skipped on macOS. All 60 protected Track comparisons were
within tolerance. The deterministic performance gate, including 30 simulated
minutes of retained-memory checks, passed. This is not a real-time GPU/serial
soak or a replacement for native desktop acceptance.

No steering, propulsion dynamics, provider keys, network endpoint defaults,
3D rendering or offline-map behavior is redesigned by this correction.
The expanded Windows/macOS first-start help from the 2.3.1 documentation refresh
is also embedded in these builds.

All four native jobs in [Actions run 41059852134](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/actions/runs/41059852134)
passed. Windows passed 1,188 tests with 28 skips; macOS and each Ubuntu target
passed 1,216 tests. macOS passed on attempt 2 with identical source and performance
limits after attempt 1 exceeded a timing limit. The Ubuntu 22.04 build passed
the GLIBC 2.35 gate; no manual 22.04 desktop test is claimed.
See [validation](VALIDATION.md) and [exact package provenance](NATIVE_BUILD_PROVENANCE.md).

## Windows Defender Review

Microsoft completed the final review on 30 October 2026 with determination
**Not malware**. The analyst reported the detection removed. The exact reviewed
application EXE has SHA256
`d1d87b7504268843224391d032434bd8ce6c7837defeeca6b9b8f5c9447e2cfe`;
the tested packages are unchanged. Update Defender security intelligence before
retrying a previously detected file. This is an exact-file determination, not a
signature, universal malware guarantee or Smart App Control approval. Follow
[installation guidance](../INSTALLATION.md), not blanket security exclusions.

## Packaging

Ubuntu CI now uses the official stable appimagetool 1.9.1 artifact, pinned by
asset ID and SHA256, because the previous continuous artifact was deleted
upstream. Failed tool verification still stops the build.

All builds remain unsigned; Smart App Control can still block them. No credentials,
operator settings, chart databases or equipment-specific integration documentation
are included. Original project code remains MPL 2.0. For simulation, testing and
training only, not real navigation.

PDF/Word User Guides, complete history-free MPL developer-source archives,
checksums and build records are attached to the release. Matching unchanged
third-party library sources remain in [release 2.3.0](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/releases/tag/v2.3.0);
see [release licensing](RELEASE_LICENSING.md).
