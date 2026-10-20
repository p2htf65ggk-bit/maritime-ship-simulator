# Source snapshot qualification - 2.3.0

This is the MPL 2.0 public-source baseline, with fresh Git history and a private
draft release. macOS native packaging is prepared; Windows and both Ubuntu
build groups are still pending. Nothing is public yet. See
[validation](VALIDATION.md) and the [release handoff](RELEASE_HANDOFF_2.3.0.md).

Required source checks are the credential/hygiene audit, compile/UI smoke, full
V2 test suite, 60 Track fixture comparisons and performance/retained-memory gates.
These tests use the pinned existing environment; a clean dependency installation
and native Windows/Ubuntu/macOS packaging still need their own target checks.

Before distributing binaries, run the relevant native build script, verify
`RELEASE_INFO.txt` and `SHA256SUMS`, test portable relocation, dialogs, offline
map import and configured network/serial outputs. Test real Google/AIS provider
authentication and GPU interaction without recording credentials. Do not infer
multi-hour native hardware stability from short deterministic fixture runs.

S57 portrayal is experimental, not certified ECDIS/S-52. Offline data is subject
to its original licensing and accuracy limits. This software is for simulation,
training and test environments, not real navigation.
