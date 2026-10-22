# 2.3.0 release handoff

This release stays a private draft until the author approves publication.
Windows x64 and Ubuntu 26.04.1 LTS amd64 artifacts have been collected and
checked. The author reports the corrected Windows trackpad behavior working;
Ubuntu mouse/trackpad behavior was also reported working.
No Ubuntu 22.04 binary will be built or published for 2.3.0. Its self-build
wrapper remains in the source and is not a release-blocking requirement.
The tested Windows trackpad follow-up is now the final source snapshot.
Already accepted packages do not need rebuilding for this promotion or for
documentation/verification-only changes. See the exact per-platform
[source mapping and limits](NATIVE_BUILD_PROVENANCE.md). Do not substitute old
2.2.1/RC1 packages. No video-only features were added to the application.

## Windows and Ubuntu handoff

| Target | Command from the source root | Keep the entire output folder |
| --- | --- | --- |
| Windows | `powershell -ExecutionPolicy Bypass -File .\scripts\build-v2-windows.ps1` | `release-v2/windows` |
| Ubuntu 26.04.1 LTS amd64 | `bash scripts/build-v2-ubuntu.sh` | `release-v2/ubuntu` |

These are two artifact groups, not two individual files: Windows creates an
installer and portable ZIP; Ubuntu creates DEB, AppImage and portable
tar.gz. Keep `RELEASE_INFO.txt` and `SHA256SUMS` with each folder. Explicit `bash`
does not require `chmod`. Internet is normally needed for build dependencies.
The author confirmed the Ubuntu version above; the archive confirms x86_64.
Do not imply that newer-GLIBC packages
run on 22.04. The optional 22.04 self-build is documented in
[the build guide](v2_release_scripts.md#ubuntu-2204-self-build).

All scripts default to unsigned production and run the release test gate.
They now include the native environment's dependency notices. If a notice or
source-version check fails, preserve the log and report it; do not bypass it.
The large upstream source archives are already prepared separately and do not
need to be downloaded to compile the simulator.

## Brief acceptance on each target

- Install and start with a fresh test profile; About must show 2.3.0. Preserve
  existing operator settings before doing any upgrade test.
- Check settings/dialog layout at the laptop's recommended display scaling.
  On Windows, open the map-source and offline-area detail menus at 100, 125
  and 150%; no blank popup extension should appear below or beside the options.
- Run Sensor, Ship and Track modes; check rudder, engine and bow-thruster controls.
- Receive one local UDP/TCP output and test the external input/fallback path used
  by your equipment. A passing unit test does not qualify a real serial adapter.
- Disconnect Internet: start again, use an installed offline map, and add
  synthetic traffic with Avoid Land disabled. No provider key is required.
- Check 2D wheel/trackpad controls. If using Google 3D, enter your own key and
  test the actual GPU, gestures, detach/restore and sustained operation.
- Quit and restart; check settings restoration. Test KVM/display reconnect if used.

Use [the verifier](../scripts/verify_native_handoff.py) on each completed folder:

```bash
python scripts/verify_native_handoff.py release-v2/ubuntu --platform ubuntu --report ubuntu-handoff.json
```

Use the build environment's Python (`venv/bin/python` on Linux/macOS or
`venv\Scripts\python.exe` on Windows), with the matching folder/platform name.
It checks artifact hashes, version/signing metadata, credentials and bundled
notice integrity. It does not replace installation or hardware acceptance.

## Final maintainer steps

1. Keep the collected Windows/Ubuntu folders unchanged; macOS packages are also
   prepared. Their dependency inventories match the prepared upstream sources.
   Retain exact corresponding sources for the final Windows runtime and the
   earlier macOS/Ubuntu baseline, as described in the build record.
2. Add the checked native files to the existing private draft. Generate one
   final `SHA256SUMS` covering the exact release asset set, without name collisions
   between platform-local metadata files.
3. Verify GitHub asset digests, source commit, guide, notices, all download links
   and the final release notes. Retain the upstream-source downloads with binaries.
4. With explicit author approval, change repository visibility to public and
   publish the draft `v2.3.0` release against the reviewed source commit.
5. Check the public page/downloads without signing in. Only then post the prepared
   LinkedIn announcement with the four approved screenshots.

The current native verification and operator reports have explicit limits;
they are not comprehensive GPU, serial-hardware or multi-hour qualification.
Anonymous public-link checks remain pending until publication is approved.
