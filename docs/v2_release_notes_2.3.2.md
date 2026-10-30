# Maritime Ship Simulator V2 2.3.2

Prepared 30 October 2026. Unsigned native test builds are being prepared for
Windows x64, macOS arm64, Ubuntu 26.04 amd64 and Ubuntu 22.04 amd64. Publication
and native operator acceptance are pending; 2.3.1 remains the latest public release.

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
controller was connected. Full native packaging and desktop acceptance are separate
checks; results must be recorded before publication.

The full local source gate then passed: 1,213 V2 tests passed and three PowerShell
execution cases were skipped on macOS. All 60 protected Track comparisons were
within tolerance. The deterministic performance gate, including 30 simulated
minutes of retained-memory checks, passed. This is not a real-time GPU/serial
soak or a replacement for native desktop acceptance.

No steering, propulsion dynamics, provider keys, network endpoint defaults,
3D rendering or offline-map behavior is redesigned by this correction.
The expanded Windows/macOS first-start help from the 2.3.1 documentation refresh
is also part of this source and will be embedded in new builds.

All builds remain unsigned; Smart App Control can still block them. No credentials,
operator settings, chart databases or equipment-specific integration documentation
are included. Original project code remains MPL 2.0. For simulation, testing and
training only, not real navigation.
