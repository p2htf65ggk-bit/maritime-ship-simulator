# Maritime Ship Simulator

[Download 2.3.1](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/releases/tag/v2.3.1) |
[User Guide (PDF)](docs/manual/Maritime_Ship_Simulator_User_Guide_2.3.1.pdf) |
[User Guide (Word)](docs/manual/Maritime_Ship_Simulator_User_Guide_2.3.1.docx) |
[Installation](INSTALLATION.md) |
[First start](docs/FIRST_START.md) |
[Releases](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/releases) |
[Report a bug](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/issues/new/choose)

Current source snapshot: **2.3.1**, licensed under the **Mozilla Public License 2.0** for the
project's original code. Third-party software and chart data retain their own
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

A free, open-source desktop ship and sensor simulator for training, demonstrations, integration
and test benches. It generates AIS and IEC 61162/NMEA-style data through UDP,
TCP and serial interfaces. It also provides route following, synthetic traffic,
fault injection, offline maps/ENCs and optional online Google 3D views.

Useful for bench tests and demonstrations with steering controls, autopilots,
track-control systems, ECDIS and compatible radar equipment. Integration depends
on the equipment's supported sentences, inputs and configuration; it is not
an equipment certification or a guarantee of compatibility with every system.

**Not certified navigation equipment. Do not use it for real navigation or as
a safety-critical source of vessel, traffic, chart or collision information.**

## Downloads

Version **2.3.1** is the latest stable release. Get application installers from
the [release page](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/releases/tag/v2.3.1),
not GitHub's green Code button. End users do not need Python or build tools.

| Platform | Application packages | Compatibility notes |
| --- | --- | --- |
| Windows x64 | Setup EXE or portable ZIP | Author-tested on Windows 11 |
| Ubuntu 26.04.1 LTS amd64 | DEB, AppImage or portable tar.gz | Built/tested on the named release, not an older-Ubuntu compatibility package |
| Ubuntu 22.04 compatibility, amd64 | DEB, AppImage or portable tar.gz, labelled `ubuntu-22.04-amd64` | Built and automatically checked on 22.04; desktop-tested by the author on Ubuntu 26.04, not on a 22.04 desktop |
| macOS Apple silicon | DMG or application ZIP | Native arm64; Intel Macs are not qualified |

Packages are **unsigned**. Read the [installation notes](INSTALLATION.md) and
verify `SHA256SUMS` from the release. The User Guide is available as PDF and Word.
No Google/AIS keys or personal operator configuration are included.

### New in 2.3.1

- Chart-based depth prefers installed S57 cells, then GEBCO online, then the
  last effective manual depth. Vessel Draft is subtracted once; manual depth
  already means depth below keel/transducer. The transducer is at keel level.
- Wind, surface current and atmospheric data independently select Manual or
  Online - Open-Meteo, with last-value fallback and automatic recovery.
- A compact Environment panel places wind/current side by side when space permits.
  Bow-thruster instrument response is cyan in both directions.
- Accelerated depth sampling is bounded in real time. Offline Mode uses local
  S57 depth or the held manual value and pauses online environmental feeds.
- Windows builds retain automatic error/test logs and use installed fonts for
  offscreen checks; Ubuntu prerelease version handling is corrected.

Software use remains governed by MPL 2.0. Open-Meteo's free endpoint is for
non-commercial use only; commercial service access requires appropriate
provider credentials. GEBCO needs no API key. These are model/chart samples,
not live onboard measurements or a navigation-safety service.
See the [2.3.1 changes](docs/v2_release_notes_2.3.1.md) and
[Environment instructions](docs/v2_user_guide.md).

## Capabilities

| Area | Included |
| --- | --- |
| Ship handling | Vessel profiles, engine telegraph, rudder, speed-dependent tunnel bow thruster |
| Control modes | Manual sensor values, Ship Control, Track Control and external inputs |
| External control | Independent rudder, engine and thruster sources with fallback behavior |
| Traffic | Generated, live and editable synthetic AIS; encounter scenarios |
| Protocols | IEC 61162/NMEA-style output through UDP, TCP and serial interfaces |
| Maps | Online 2D maps, offline Protomaps areas and experimental S57/NOAA ENCs |
| Visual view | Optional online Google 3D maps with simulator overlays |
| Test scenarios | Sensor noise, fault injection, environment values and saved configurations |
| Environmental sources | Manual or chart-based depth; optional online wind, current and weather |

Local simulation and local protocol output do not require Internet or a Google
API key. Download/import detailed charts before going offline. See the
[offline workflow](docs/v2_user_guide.md) and [provider privacy notes](PRIVACY.md).

## In use

### Google 3D harbour view

![Manhattan harbour in Google 3D with own ship, synthetic AIS and navigation overlays](docs/images/google-3d-harbour.png)

### Track Control with OpenSeaMap

![Track Control with the planned route, own ship and synthetic traffic on OpenSeaMap](docs/images/track-openseamap.png)

### Navigation and propulsion instruments

![Heading, course, speed, engine, bow thruster, rudder, wind, depth and AIS instruments](docs/images/instruments.png)

### Editable synthetic traffic

![Synthetic AIS targets and encounter controls beside an experimental S57 chart](docs/images/synthetic-traffic.png)

These are actual application captures with synthetic demonstration targets,
not a live traffic picture. Map imagery/data belongs to its indicated providers
(Google Maps; OpenStreetMap/OpenSeaMap contributors; NOAA), not to the project.
Provider attribution is retained in the images. S57 portrayal is experimental.

## Releases and support

The public project is maintained by **Pascal Göllnitz**. Version labels are not
a claim of exhaustive platform/hardware qualification; see
[validation](docs/VALIDATION.md) and the release notes.
This source tree is **2.3.1**, extending the public baseline with chart-based depth
and online environmental sources.
Installers and portable packages are attached to
[release 2.3.1](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/releases/tag/v2.3.1).
The binary downloads cover Windows x64, macOS Apple silicon and
**Ubuntu 26.04.1 LTS amd64**. Additional **Ubuntu 22.04 compatibility packages**
were added on 27 October 2026. They passed the full automated build gates and
GLIBC 2.35 check on a 22.04 GitHub runner. The author tested this build on Ubuntu
26.04; a manual 22.04 desktop test is not claimed. See
[package selection](INSTALLATION.md#ubuntu) and the
[self-build path](INSTALLATION.md#ubuntu-2204-self-build).

For application downloads rather than development, see
[Installation](INSTALLATION.md), [known limitations](KNOWN_LIMITATIONS.md) and
[release licensing](docs/RELEASE_LICENSING.md). The
[native build record](docs/NATIVE_BUILD_PROVENANCE.md) identifies the sources
for each package. All three platforms contain the 2.3.1 application; the final
source also includes build/test/documentation-only corrections added after
some native builds. Application code remains equivalent. Native checks are
described with their limits;
older private packages are not substitutes for these identified builds.

Maintenance is on a best-effort basis. Issues and pull requests are welcome,
but response times, acceptance of contributions and future releases are not
guaranteed. No paid support or service-level agreement is implied.

## Get started

Use Python **3.12 or newer**; Python 3.13 is the reference development version.
Run commands from the extracted project root. Git is optional for running or
building this snapshot. Internet is needed to install dependencies unless an
appropriate local wheelhouse is available.

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-v2-release.txt
.venv/bin/python run_v2.py
```

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-v2-release.txt
.\.venv\Scripts\python.exe run_v2.py
```

On Linux, Qt needs system display/OpenGL libraries. The Ubuntu release scripts
and runtime launcher contain dependency checks. Python packages alone cannot
provide a working desktop display server.

This is a source-tree application, not a published PyPI package. An editable
`pip install -e .` is not required or supported by this snapshot.

## Where to start in the code

| Location | Responsibility |
| --- | --- |
| `run_v2.py` | Application startup and diagnostics |
| `src/maritime_simulator/domain/` | State, snapshots, commands and vessel profiles |
| `src/maritime_simulator/application/` | Command ownership, AIS and configuration |
| `src/maritime_simulator/simulation/` | Clock, dynamics, steering, routes and prediction |
| `src/maritime_simulator/protocols/` | Sensor projection, sentence encoding and scheduling |
| `src/maritime_simulator/transport/` | UDP/TCP/serial and online input adapters |
| `src/maritime_simulator/visual/` | Offline maps, S57, water masks and display payloads |
| `src/maritime_simulator/ui/` | Current desktop UI and lifecycle-managed workers |
| `ui/` | Small, still-required shared module set and browser resources |
| `tests/v2/`, `tests/fixtures/` | Current regression suite and test data |
| `scripts/` | Build, validation, benchmark and browser verification tools |

The root `ui/` folder is intentional. Track Control still uses `ui.simulation`
behind an adapter; the map and instrument renderer are also shared modules.
They are current dependencies, not a second runnable application. Do not delete
or rename them without updating their consumers and tests. See
[architecture](docs/v2_architecture.md).

## Test and build

```bash
.venv/bin/python scripts/prebuild_v2.py --profile quick
.venv/bin/python scripts/prebuild_v2.py --profile full
```

On Windows, substitute `.\.venv\Scripts\python.exe`. The full gate runs the V2
tests, 60 checked-in Track baseline cases and performance/retained-memory checks.
Tests use temporary configurations and local loopback transports. Hardware
serial, real provider authentication and native GPU behavior need separate tests.

[Testing and development](CONTRIBUTING.md) explains focused checks and optional
browser tooling. [Build instructions](docs/v2_release_scripts.md) cover Windows,
macOS, Ubuntu and an optional Ubuntu 22.04 self-build. Build natively
on each target OS; the scripts default to unsigned packages and recognize RC
versions as prereleases.

## Configuration and offline operation

Operator settings do not belong in this source tree. Defaults are stored under:

- Windows: `%APPDATA%\Ship Simulator\v2`
- macOS: `~/Library/Application Support/Ship Simulator/v2`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/Ship Simulator/v2`

`MARITIME_SIMULATOR_V2_CONFIG_DIR` overrides that location, which is useful for
an isolated development profile. The source contains no Google/AIS/provider keys,
saved operator settings or downloaded chart library. Documentation screenshots
show attributed provider imagery; no Google tile dataset or offline imagery
cache is included.

Local simulation and local output work without Internet. The bundled schematic
coastline is available offline. Detailed map areas and ENC cells must first be
downloaded/imported through Settings. Google 3D and new Live AIS/weather data are
online services with separate credentials, terms and possible charges.

See the [User Guide](docs/v2_user_guide.md), [3D notes](docs/v2_1_forward_view.md)
and [data/licensing inventory](THIRD_PARTY_NOTICES.md).

## Snapshot contents

Only the current application, its required shared code, data and development
tools are included. Repository history begins with the clean 2.3.0 snapshot;
earlier private development history has not been imported. Application binaries,
virtual environments and installation-specific integration guides are not part
of this repository.
`DEVELOPER_SOURCE_MANIFEST.json` records the original exported scope and reviewed
export-only adjustments. `SOURCE_SHA256SUMS` covers the reviewed tracked files.
The release archive regenerates that manifest and adds `RELEASE_SNAPSHOT.json`
with its exact source commit. Later source, packaging and documentation changes
are recorded in Git.
See [source provenance](docs/SOURCE_PROVENANCE.md) to verify the baseline.

The MPL 2.0 license is in `LICENSE`. `FREEWARE_LICENSE.txt` contains the same MPL
text under the filename expected by existing application packaging code. It
does not introduce separate Freeware restrictions. Original source files carry
`SPDX-License-Identifier: MPL-2.0`; upstream code/data keeps its own notices.

MPL permits commercial use and combination with separate proprietary modules.
When distributing modified MPL-covered code, make its source available to
recipients under MPL and retain the required notices. Separate files containing
no MPL-covered code may remain proprietary. Private/internal modifications do
not require publication. See the full license and
[Mozilla FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/); this summary does not
replace those terms or the licenses of dependencies.

Optional support: [Maritime Knowledge Bites](https://mybook.to/MaritimeKnowledgeBites).
