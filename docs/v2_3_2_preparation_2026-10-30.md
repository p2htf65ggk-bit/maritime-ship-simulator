# 2.3.2 Source And Native Build Preparation

Prepared 30 October 2026 from the published 2.3.1 source at
`726c69c20346962c300db802ea4328acb5aa7cd2`. No public release or tag is created by
this preparation; 2.3.1 stays published and unchanged.

The change is limited to Generic XDR null-unit control compatibility, its UI,
documentation, regression tests and version metadata. Unit remains an explicit
filter. -100 and +100 are accepted; values outside that range are rejected.
Empty measurements are not control commands. Existing default/saved filters,
source ownership and simulation dynamics are unchanged.

## Local Checks

- 224 focused checks for XDR, UI, configuration/Start-up and protocols/transports.
- Full source gate: 1,213 V2 tests passed, three skipped because PowerShell is
  unavailable on this macOS development environment. No failures or errors.
- 60/60 Track golden-master comparisons within tolerance.
- Performance/retained-memory gates passed, including 30 simulated minutes.
- Credential/resource/compile and offscreen UI checks passed.
- Updated English Word/PDF guide: 53 pages, 88 contents entries, 41 figures,
  consistent captions, current footers and no private integration content.

Tests use temporary profiles and local loopback sockets. Actual controller,
hardware serial, native GPU and long real-time acceptance remain separate.

## Native Targets

The unsigned Actions workflow is used for Windows x64,
macOS Apple silicon, Ubuntu 26.04 amd64 and Ubuntu 22.04 amd64. It runs native
prebuild gates, packages and startup checks, then uploads test artifacts and logs.
It does not publish a GitHub release. Exact run/revision records are kept with
the local 2.3.2 handoff after dispatch.

The first run exposed a deleted upstream continuous AppImage tool asset (HTTP
404), before either Ubuntu application build started. The workflow now pins the
official stable appimagetool 1.9.1 asset and its SHA256 instead. The strict hash
check remains mandatory; application/runtime behavior is unaffected.

Ubuntu 22.04 is a build/ABI baseline; a desktop test on another Ubuntu version
does not count as a 22.04 desktop test. Packages remain unsigned and may be
blocked by platform protection, including Smart App Control. No credentials,
operator profiles, chart databases or private integration material are included.
