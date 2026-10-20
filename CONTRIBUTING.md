# Development

This snapshot is a normal source tree with no previous Git history. You may
initialize a new repository yourself. No repository creation, upload or hosting
is needed to run the application.

Contributions are welcome. Maintenance is on a best-effort basis: no response
time, acceptance of a pull request or future release is guaranteed. Please
discuss large changes before starting work, and keep requests reproducible.

## Environment

Follow the root README to create `.venv` and install the pinned
`requirements-v2-release.txt`. Use the same interpreter for the application,
tests and packaging. Qt, GDAL/pyogrio and numerical libraries are native
dependencies; do not copy a virtual environment between operating systems.

Use a separate configuration directory when testing potentially destructive
settings, chart imports or output connections. Never submit credentials, local
configuration exports, downloaded ENC libraries, raw network captures or logs
containing operator information.

## Design rules

- Submit commands through `SimulatorService`; the UI must not directly mutate
  authoritative simulation state.
- Pass immutable snapshots to output and visual consumers.
- Keep blocking network, serial, chart import and filesystem operations away
  from the Qt UI thread; preserve bounded queues and deterministic shutdown.
- Keep AIS display/output selection consistent through the shared repository.
- Preserve route geometry and the checked-in Track baselines unless a behavior
  change is intentional, reviewed and documented.
- Keep memory limits adaptive and retain offline operation without credentials.
- Keep changes scoped; move the shared root `ui/` modules only as a separate,
  test-backed refactor rather than while fixing an unrelated feature.

## Validation

Replace `.venv/bin/python` with `.\.venv\Scripts\python.exe` on Windows:

```bash
.venv/bin/python -m pytest -q tests/v2/test_protocols.py
.venv/bin/python -m pytest -q tests/v2/test_transport.py
.venv/bin/python -m pytest -q tests/v2/test_offline.py tests/v2/test_s57.py
.venv/bin/python scripts/prebuild_v2.py --profile full
```

The full gate requires no real provider credentials or Internet chart download.
Local UDP/TCP bindings must be permitted. Offscreen Qt may emit
`propagateSizeHints` warnings; inspect the actual assertion/exit status rather
than treating that warning as a build failure.

`tests/fixtures/v1_track_golden_master.json` is a current regression fixture,
not an old application checkout. Its 60 cases protect inherited Track behavior.
Do not regenerate it merely to make a failing comparison pass.
`generate_v1_golden_master.py` retains its historic name because the comparison
tool imports its scenario definitions; no old source archive is needed.

Optional browser verification uses an independently installed `playwright-core`
and Chrome/Chromium. Node/browser packages are not application dependencies:

```bash
PLAYWRIGHT_CORE_PATH=/path/to/playwright-core/index.mjs \
node scripts/verify_forward_view_web.mjs --api-settings-only
```

Other `verify_*_web.mjs` scripts exercise map and overlay fixtures. Some browser
checks load public third-party JS/CSS from their normal CDN. Those checks do not
replace real Windows/macOS/Linux GPU and provider-authentication tests.

## Changes and distribution

Add a focused regression, describe visible changes and list which OS and
hardware were actually tested. Keep the current version in
`src/maritime_simulator/__init__.py` consistent with documentation and packaging.

Only submit code you have the right to license. First-party contributions are
provided under MPL 2.0; you retain your copyright. Preserve existing MPL/SPDX
headers and upstream notices for imported third-party code/data. MPL does not change Qt, map, ENC,
provider-service or other third-party license requirements. See
`THIRD_PARTY_NOTICES.md` before distributing binaries.
