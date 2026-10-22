# Software, data and service licenses

## Scope of MPL 2.0

The project's original Python/JavaScript/CSS, documentation and other original
source material are offered under the root MPL 2.0 license by Pascal Göllnitz.
The exceptions below retain their upstream terms. The source archive does not
vendor installed Python/Qt/GDAL environments or Google tile datasets. UI
screenshots in the documentation contain attributed Google/OSM/OpenSeaMap/NOAA
imagery or chart data, which is not relicensed under MPL.

`FREEWARE_LICENSE.txt` is an exact copy of the root MPL 2.0 license, retained only
because the existing PyInstaller scripts and application resources use that
filename. It introduces no additional first-party distribution restrictions.

## Material included in this source snapshot

| Material | License/notice | Origin |
| --- | --- | --- |
| `ui/web/leaflet/leaflet.js` and `leaflet.css` (1.9.4) | BSD-2-Clause; full text in `LICENSES/Leaflet-BSD-2-Clause.txt` | [Leaflet 1.9.4](https://github.com/Leaflet/Leaflet/tree/v1.9.4) |
| `assets/geodata/osm-simplified-land-polygons-3857.zip` | OpenStreetMap contributors, ODbL 1.0; not MPL | [OSM land polygons](https://osmdata.openstreetmap.de/data/land-polygons.html), [ODbL](https://opendatacommons.org/licenses/odbl/1-0/) |
| `tests/fixtures/s57/US4NJ1DG.zip` | NOAA exchange-set test data; retain its original contents and included notices | [NOAA ENC](https://www.nauticalcharts.noaa.gov/charts/noaa-enc.html), fixture README |

The coastline archive is provided unchanged with its source/date notice in
`assets/geodata/README.txt`. Database-derived data retains the ODbL; the MPL
software license does not relicense it. See `LICENSES/ODbL-1.0.txt`.
Imported user ENC/map data is not included and may have different permissions.

Copies of the Leaflet license, coastline license and this inventory are also
placed under `ui/web/leaflet/` and `assets/`, which the native packaging scripts
already collect. This keeps these source-data notices with newly built apps.

## Installed/build dependencies

The pinned package list is `requirements-v2-release.txt`. Those packages are
installed separately; their own notices and source/distribution obligations
continue to apply. Important upstream references include:

- [Python](https://docs.python.org/3/license.html).
- [Qt/PySide6](https://doc.qt.io/qtforpython-6/licenses.html) and
  [Qt WebEngine](https://doc.qt.io/qt-6/qtwebengine-licensing.html).
- [GDAL](https://gdal.org/en/stable/license.html) and
  [pyogrio](https://github.com/geopandas/pyogrio/blob/main/LICENSE).
- [Shapely](https://github.com/shapely/shapely/blob/main/LICENSE.txt),
  [GEOS](https://libgeos.org/), [NumPy](https://numpy.org/doc/stable/license.html).
- [websockets](https://github.com/python-websockets/websockets/blob/main/LICENSE),
  [certifi](https://github.com/certifi/python-certifi/blob/master/LICENSE).
- [PyInstaller](https://pyinstaller.org/en/stable/license.html), including its
  bootloader exception; [pytest](https://github.com/pytest-dev/pytest/blob/main/LICENSE).

Native builds include `THIRD_PARTY_LICENSES`, with full installed-package
license files, Python's license, version-verified Qt/PySide upstream license and
attribution archives (including Chromium), and a platform-specific inventory.
The build rejects missing notices and mismatched Qt/GEOS versions. Separate
upstream-source release assets and [library replacement instructions](../docs/LIBRARY_REPLACEMENT.md)
accompany the application. `LICENSES/upstream-sources.json` records their hashes.
The PMTiles 3.8.1 wheel omits its BSD license file; its official upstream text
is retained under `LICENSES/python-packages`. The Python PSF fallback is the
unmodified CPython v3.13.7 `LICENSE`, used only if the build installation omits it.

Before distributing compiled packages, review the complete notices required by
the exact libraries/frameworks shipped for that OS, including Qt's own bundled
third-party code. This source inventory is not a binary-license compliance audit
or a substitute for those full license texts.

The build excludes unused QML Python modules and optional Qt Charts, Data
Visualization, Graphs, Quick 3D and Virtual Keyboard families. A post-build check
rejects those unused families if they are still collected. No commercial Qt
entitlement is assumed. Review the exact native output's complete notices,
corresponding-source access and library replacement information before public
distribution. The first-party MPL label is not a binary compliance certification.

## External online services

Google Maps, aisstream.io, BarentsWatch, Open-Meteo, OpenStreetMap/OpenSeaMap tile
services and Protomaps data/services have separate authentication, usage, quota,
attribution and redistribution terms. MPL grants no rights to their imagery,
services, trademarks, accounts or user-imported chart libraries.

CesiumJS is loaded from its configured CDN rather than vendored here; its
[Apache-2.0 license](https://github.com/CesiumGS/cesium/blob/main/LICENSE.md)
applies separately. Keep map-provider attribution visible. The source does not
contain IEC/IHO standards publications and is not certified S-52/ECDIS portrayal.
