# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: MPL-2.0

import sys
from pathlib import Path


ROOT = Path(globals().get("SPECPATH", Path.cwd())).resolve()
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from maritime_simulator import __version__


APP_NAME = "Ship Simulator V2"
APP_BUNDLE_ID = "de.pascalgollnitz.shipsimulator.v2"
BUNDLE_VERSION = __version__.split("-", 1)[0].split(".dev", 1)[0]


def data_tree(source, target):
    source_path = ROOT / source
    return [
        (str(path), str(Path(target) / path.relative_to(source_path).parent))
        for path in source_path.rglob("*")
        if (
            path.is_file()
            and path.name != ".DS_Store"
            and not path.name.startswith("._")
            and path.suffix != ".pyc"
            and "__pycache__" not in path.parts
            and ".git" not in path.parts
        )
    ]


datas = (
    data_tree("assets", "assets")
    + [(str(ROOT / name), ".") for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "FREEWARE_LICENSE.txt")]
    + data_tree("LICENSES", "LICENSES")
    + data_tree("ui/web", "ui/web")
    + data_tree("routes", "routes")
    + data_tree("scenarios", "scenarios")
    + data_tree("src/maritime_simulator/resources", "maritime_simulator/resources")
)

hiddenimports = [
    "maritime_simulator.simulation.track_adapter",
    "ui.simulation",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtSerialPort",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtPrintSupport",
    "PySide6.QtPositioning",
    "PySide6.QtOpenGL",
    "websockets.sync.client",
    "websockets.extensions.permessage_deflate",
    "pmtiles.reader",
    "mapbox_vector_tile",
    "pyogrio.raw",
]

a = Analysis(
    ["run_v2.py"],
    pathex=[str(ROOT), str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "scripts/pyinstaller-hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6." + module for module in (
        "QtCharts", "QtDataVisualization", "QtGraphs", "QtGraphsWidgets", "QtQuick3D", "QtVirtualKeyboard",
        "QtQml", "QtQuick", "QtQuickWidgets", "QtWebEngineQuick",
    )],
    noarchive=False,
    optimize=0,
)
# QtGui collects platform input plugins independently of Python module excludes.
from scripts.audit_qt_bundle import filter_build_entries
a.binaries = filter_build_entries(a.binaries)
a.datas = filter_build_entries(a.datas)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    exclude_binaries=True,
    icon=str(ROOT / "assets" / ("app_icon.ico" if sys.platform == "win32" else "app_icon.png")),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ROOT / "assets" / "app_icon.icns"),
        bundle_identifier=APP_BUNDLE_ID,
        info_plist={
            "CFBundleDisplayName": "Maritime Ship Simulator V2",
            "CFBundleName": APP_NAME,
            "CFBundleShortVersionString": BUNDLE_VERSION,
            "CFBundleVersion": BUNDLE_VERSION,
            "CFBundleGetInfoString": f"Maritime Ship Simulator V2 {__version__}",
            "NSHighResolutionCapable": "True",
        },
    )
