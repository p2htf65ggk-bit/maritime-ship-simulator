# Native Build Provenance - 2.3.2

Published 30 October 2026. All native packages use source commit
[`a66a1e6474a87e57442225cf5bbf02376dc5bb30`](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/commit/a66a1e6474a87e57442225cf5bbf02376dc5bb30).
The public tag and history-free developer snapshot add publication documentation
only. Runtime code, packaged application resources and tested binaries are unchanged.

## Selected Runner Artifacts

[Actions run 41059852134](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/actions/runs/41059852134)
contains the selected builds. Artifact retention is temporary; the versioned
release downloads preserve the accepted packages separately.

| Target | Attempt | Artifact ID | Tests passed / skipped |
| --- | --- | --- | --- |
| Windows x64 | 1 | 12979316772 | 1,188 / 28 |
| Ubuntu 26.04 amd64 | 1 | 12979630723 | 1,216 / 0 |
| Ubuntu 22.04 amd64 | 1 | 12978318827 | 1,216 / 0 |
| macOS arm64 | 2 | 12981516219 | 1,216 / 0 |

macOS attempt 1 stopped before packaging because one performance sample was
115.482 ms against a 100 ms limit. The rerun used identical source and limits
and passed (maximum 20.688 ms, average 1.478 ms over 6,000 Track ticks). The
cause of the first timing failure was not established; no threshold was relaxed.
This is distinct from the earlier 2.3.1 timer-test correction.

All targets use Python 3.13.15, PySide6/Qt 6.11.0, PyInstaller 6.17.0,
GDAL 3.12.4 and GEOS 3.14.1. Each actual portable package includes its own
dependency inventory and license notices. Platform-scoped inventories,
RELEASE_INFO records, NATIVE_BUILD_RECORD.json and SHA256SUMS are attached to
the release. The Ubuntu 22.04 packages passed the GLIBC 2.35 gate; their
ABI_COMPATIBILITY record is included. They were not manually tested on a 22.04 desktop.

Ubuntu uses official stable appimagetool 1.9.1, asset 324406736, with SHA256
`ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0`.
The old continuous asset was removed upstream. Verification remains mandatory.

## Exact Windows File Review

Microsoft Security Intelligence completed submission
`584cee27-277f-4eb7-8b20-914a29d602e3` on 30 October 2026. Final determination:
**Not malware**. The analyst reported the detection removed.

The reviewed `Ship Simulator V2.exe` is 6,971,257 bytes with SHA256:

```text
d1d87b7504268843224391d032434bd8ce6c7837defeeca6b9b8f5c9447e2cfe
```

The executable was extracted from the verified portable package, and is
distributed unchanged in the accepted packages. This is an exact-file result,
not a code signature, an endorsement, a guarantee for all dependencies/future
builds, or Smart App Control approval. Packages remain unsigned (macOS ad-hoc
only). See [installation](../INSTALLATION.md#windows).

## Publication Checks And Limits

Native checksums, application version, bundled notices and credential scans
passed for all selected packages. The author reports working native builds.
Automated and package checks do not establish every GPU/input/serial combination,
physical-controller integration or multi-hour behavior. See [validation](VALIDATION.md).

Original source uses MPL 2.0. Third-party notices, inventories, matching source
links and replacement guidance remain available; see [release licensing](RELEASE_LICENSING.md).
No operator profiles, provider credentials, chart databases or private equipment
integration documentation are included.

The prior release remains separately documented in
[2.3.1 provenance](NATIVE_BUILD_PROVENANCE_2.3.1.md).
