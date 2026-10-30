# Release qualification 2.3.2

**30 October XDR correction:** all four unsigned native builds passed. The empty
Unit selection for G is covered by parser, transport, UI and persistence tests.
The author reports working builds. Microsoft's completed final review found the
exact Windows EXE Not malware and removed the detection. The 2.3.1 release is
preserved. See [current notes](v2_release_notes_2.3.2.md),
[validation](VALIDATION.md) and [provenance](NATIVE_BUILD_PROVENANCE.md).

## Published 2.3.1 Baseline

**29 October HUD refresh:** all four selected builds were author-tested and
approved for publication. Windows and both Ubuntu builds use `103693e`; macOS
uses `d5a25d6`, which adds only a timer-test correction and its validation record.
The publication adds documentation-only updates and fresh 3D screenshots.
Tested native packages are unchanged. See [2.3.1 validation](VALIDATION_2.3.1.md)
and [2.3.1 provenance](NATIVE_BUILD_PROVENANCE_2.3.1.md).

The following is the historical qualification summary of the earlier snapshot.

Version 2.3.1 is the author-approved unsigned production release. Windows 11 x64,
Ubuntu 26.04.1 LTS amd64 and macOS Apple-silicon packages are the identified
targets. Additional amd64 compatibility packages were built on Ubuntu 22.04,
passed the automated gates and GLIBC 2.35 check, and were manually tested by
the author on Ubuntu 26.04. A 22.04 desktop test is not claimed.

The full source gate passed: 1,156 V2 tests, 60 protected Track cases and the
deterministic performance/retained-memory gate. Returned native packages passed
hash, version, credential and dependency-notice checks. The author reports
successful Windows/Ubuntu builds; macOS also passed native packaged smokes.

See the [validation record](VALIDATION_2.3.1.md), [native build provenance](NATIVE_BUILD_PROVENANCE_2.3.1.md)
and [2.3.1 changes](v2_release_notes_2.3.1.md). These results do not certify real
navigation, every native GPU/input/serial configuration or multi-hour stability.
S57 portrayal remains experimental. Provider/data terms apply separately.
