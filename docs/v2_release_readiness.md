# Release qualification 2.3.1

**29 October HUD refresh:** all four selected builds were author-tested and
approved for publication. Windows and both Ubuntu builds use `103693e`; macOS
uses `d5a25d6`, which adds only a timer-test correction and its validation record.
The publication adds documentation-only updates and fresh 3D screenshots.
Tested native packages are unchanged. See [current validation](VALIDATION.md)
and [exact provenance](NATIVE_BUILD_PROVENANCE.md).

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

See the [validation record](VALIDATION.md), [native build provenance](NATIVE_BUILD_PROVENANCE.md)
and [2.3.1 changes](v2_release_notes_2.3.1.md). These results do not certify real
navigation, every native GPU/input/serial configuration or multi-hour stability.
S57 portrayal remains experimental. Provider/data terms apply separately.
