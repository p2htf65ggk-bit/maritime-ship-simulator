# Third-party libraries and replacement

The application's original source is MPL 2.0. Its Qt/PySide and other libraries
retain their own terms. No license here prohibits modification, replacement or
reverse engineering of the application for debugging modifications to an
LGPL-covered library. There is no application-level signature enforcement or DRM.

## Included notices

Native packages contain `THIRD_PARTY_LICENSES/dependency-inventory.json`, the
exact installed Python package versions, full package notices, Python's license,
and verified upstream Qt/PySide notice archives. The Qt archive includes
Chromium license files and attribution metadata. It is deliberately broader than
the collected runtime; it does not mean every upstream Qt module is bundled.
The project build rejects unused Charts, Graphs, Data Visualization, Quick 3D
and Virtual Keyboard families. See `THIRD_PARTY_NOTICES.md` for chart/service terms.

## Sources

The 2.3.1 release provides the current simulator source. Its unchanged upstream
library sources remain available in the public
[2.3.0 library-source downloads](https://github.com/p2htf65ggk-bit/maritime-ship-simulator/releases/tag/v2.3.0):
Qt 6.11.0 (including WebEngine/Chromium), PySide/shiboken 6.11.0, GEOS 3.14.1
and certifi 2026.7.22. These exact library versions are shared by both releases;
the earlier source assets must remain accessible alongside the current binaries.
Source archive hashes and original
download locations are listed in `LICENSES/upstream-sources.json`. These are
separate downloads, not required to run the application. Retain these assets
when redistributing the corresponding binary release. Source links alone are
not described as a substitute for supplying required corresponding sources.

## Build and run with replacement libraries

The simplest supported route is to extract the simulator source, create a new
Python environment, install/build your compatible replacement library there,
and run `run_v2.py`. The pinned requirements describe the reference environment;
they are not a prohibition on local changes. Upstream build instructions:

- [Qt from source](https://doc.qt.io/qt-6.11/build-sources.html)
- [Qt for Python from source](https://doc.qt.io/qtforpython-6.11/building_from_source/index.html)
- [GEOS build instructions](https://libgeos.org/usage/download/)

For a new standalone application, build the appropriate wheel/framework for the
same CPU, Python ABI and Qt/PySide ABI, install it in the build environment, and
run the native script described in `docs/v2_release_scripts.md`. If the library
version changes, regenerate its notice bundle; the build checks version matches.
Keep the library source modifications and build instructions with your release.

## Replacing a library in an installed application

Work on a copy. Quit all simulator processes first. On Windows/Linux the
one-directory package keeps shared libraries under `_internal` (including
`PySide6` and its Qt libraries). Preserve the directory layout and use compatible
shared-library builds. Do not replace system libraries globally.

On macOS, the application bundle keeps frameworks under `Contents/Frameworks`
and resources under `Contents/Resources`. Changing a framework invalidates its
ad-hoc signature. For your own local replacement build, re-sign the copied
bundle with `codesign --force --deep --sign - "Ship Simulator V2.app"` and verify
it with `codesign --verify --deep --strict "Ship Simulator V2.app"`. This does not
create a Developer ID/notarization or remove macOS security requirements.
Rebuilding from source is preferable to manual binary surgery.

ABI-incompatible replacements may require rebuilding bindings and the
application. The project does not promise that arbitrary library versions work.
This document records technical distribution arrangements, not legal advice or
a claim of independent legal certification.
