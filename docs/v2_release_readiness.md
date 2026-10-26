# Release qualification 2.3.1

Version 2.3.1 is the author-approved unsigned production release. Windows 11 x64,
Ubuntu 26.04.1 LTS amd64 and macOS Apple-silicon packages are the identified
targets. No prebuilt Ubuntu 22.04 package is provided.

The full source gate passed: 1,156 V2 tests, 60 protected Track cases and the
deterministic performance/retained-memory gate. Returned native packages passed
hash, version, credential and dependency-notice checks. The author reports
successful Windows/Ubuntu builds; macOS also passed native packaged smokes.

See the [validation record](VALIDATION.md), [native build provenance](NATIVE_BUILD_PROVENANCE.md)
and [2.3.1 changes](v2_release_notes_2.3.1.md). These results do not certify real
navigation, every native GPU/input/serial configuration or multi-hour stability.
S57 portrayal remains experimental. Provider/data terms apply separately.
