# Release Validation - 2.3.2

Recorded 30 October 2026. This is a simulation/test application, not certified
navigation equipment. Passing these checks is not exhaustive platform qualification.

## Source And Native Gates

The local full source gate passed 1,213 V2 tests; three PowerShell execution
cases were skipped on macOS. All 60 protected Track comparisons were within
tolerance. The deterministic performance gate and retained-memory checks over
30 simulated minutes passed. This does not represent a real-time GPU, serial
or multi-hour desktop soak.

224 focused checks covered Generic XDR empty-unit parsing, signed boundaries,
invalid inputs, fragmented TCP, actual UDP/TCP loopback delivery, input-age
indications, UI selections and settings/configuration/Start-up round trips.
No external serial controller was connected. An additional 67 build-tool checks
covered the stable AppImage-tool pin and related build behavior.

All native packages use `a66a1e6474a87e57442225cf5bbf02376dc5bb30`:

| Runner | Selected attempt | Passed | Skipped | Failures/errors |
| --- | --- | --- | --- | --- |
| Windows x64 | 1 | 1,188 | 28 | 0 |
| Ubuntu 26.04 amd64 | 1 | 1,216 | 0 | 0 |
| Ubuntu 22.04 amd64 | 1 | 1,216 | 0 | 0 |
| macOS arm64 | 2 | 1,216 | 0 | 0 |

See [Actions run 41059852134](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/actions/runs/41059852134)
and [package provenance](NATIVE_BUILD_PROVENANCE.md). macOS first failed a timing
sample (115.482 ms against 100 ms); the unchanged rerun passed at maximum
20.688 ms. The initial failure's cause was not established. No gate was weakened.
Ubuntu 22.04 also passed the bundled ELF GLIBC 2.35 check.

## Returned Packages And Documentation

- All ten native files passed the supplied artifact checksums.
- Packaged versions, credential scans, bundled notices and per-platform
  dependency inventories were verified. Packages are unchanged after testing.
- The author reports working native builds. Prior platform baselines include
  Windows 11, Ubuntu 26.04.1 and Apple-silicon macOS. A 22.04-built application
  has been desktop-tested on 26.04, not manually on a 22.04 desktop.
- The 53-page English Word/PDF guide was rendered and checked, including its
  41 figure captions, table of contents, page layout and Generic XDR guidance.
- Public source archives contain the full current MPL source without private
  history. Publication-only documentation is newer than the native build source.

Checksums establish file identity, not an antivirus verdict. Microsoft's
completed final review found the exact 2.3.2 Windows EXE **Not malware** and
reported the detection removed. See the hash and scope in
[provenance](NATIVE_BUILD_PROVENANCE.md#exact-windows-file-review).
Unsigned installer reputation, SmartScreen, Smart App Control and Gatekeeper
remain separate; follow [installation guidance](../INSTALLATION.md).

## Remaining Qualification Limits

No claim is made for every GPU/driver, precision trackpad, physical serial
controller, low-memory VM configuration, prolonged online provider session or
multi-hour hardware soak. S57 portrayal remains experimental. Live/model/chart
data quality and third-party terms remain outside this validation.
Review output endpoints before connecting real equipment.

Historical results are preserved in [2.3.1 validation](VALIDATION_2.3.1.md).
