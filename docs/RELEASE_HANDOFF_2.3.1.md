# Release handoff 2.3.1

The author approved 2.3.1 as the latest unsigned production release on
26 October 2026. Source, native downloads, English User Guide and SHA-256
manifests are versioned together. Earlier releases remain accessible unchanged.

Windows 11 x64 and Ubuntu 26.04.1 LTS amd64 are author-confirmed build hosts.
macOS packages target Apple silicon. Additional Ubuntu 22.04 compatibility
packages were approved on 27 October: built/automatically checked on 22.04,
then desktop-tested by the author on Ubuntu 26.04. No manual 22.04 desktop
acceptance is claimed. Existing binaries and the original release tag remain unchanged.

Application code matches the qualified native-build source. Final build/test
and documentation corrections do not require new native packages. See
[NATIVE_BUILD_PROVENANCE.md](NATIVE_BUILD_PROVENANCE.md).

Before redistributing, verify the exact asset hashes, retain all software/data
notices and provide the corresponding source/library access described in
[LIBRARY_REPLACEMENT.md](LIBRARY_REPLACEMENT.md). Do not bundle operator
configuration or provider credentials. Upgrade testing should back up settings
and verify outputs before connecting equipment.

The generic [User Guide](manual/Maritime_Ship_Simulator_User_Guide_2.3.1.pdf) and
[first-start guide](FIRST_START.md) cover operation. Platform and long-running
test limits are recorded in [VALIDATION.md](VALIDATION.md).
