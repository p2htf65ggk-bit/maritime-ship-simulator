#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Check in-page map choices through actual Qt mouse events at the current DPI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import QApplication

from ui.webengine_profile import set_webengine_offline
from ui.widgets.map_widget import MapWidget


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('artifacts/map-select-qt'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([])
    app.setApplicationName('Map menu DPI QA')
    with tempfile.TemporaryDirectory(prefix='map-menu-dpi-') as directory:
        profile = QWebEngineProfile.defaultProfile()
        profile.setPersistentStoragePath(str(Path(directory) / 'storage'))
        profile.setCachePath(str(Path(directory) / 'cache'))
        set_webengine_offline(True)
        widget = MapWidget()
        widget.resize(720, 480)
        widget.show()

        def js(script):
            result = []
            loop = QEventLoop()
            widget.view.page().runJavaScript(script, lambda value: (result.append(value), loop.quit()))
            QTimer.singleShot(4000, loop.quit)
            loop.exec()
            if not result:
                raise RuntimeError('Qt JavaScript callback timed out')
            return result[0]

        deadline = time.monotonic() + 15
        while not widget.loaded:
            if time.monotonic() > deadline:
                raise RuntimeError('Map did not load')
            app.processEvents()
            time.sleep(.01)
        assert js('window.mapReady')
        js("document.querySelector('#mapSource option[value=protomaps]').disabled=false; true")
        commands = []
        widget.map_command.connect(commands.append)
        visible_before = sum(window.isVisible() for window in app.topLevelWidgets())

        def click(selector):
            point = json.loads(js(f"JSON.stringify((()=>{{const r=document.querySelector({json.dumps(selector)}).getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}};}})())"))
            target = widget.view.focusProxy() or widget.view
            position = target.mapFrom(widget.view, QPoint(round(point['x']), round(point['y'])))
            QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, position)
            QTest.qWait(120)

        click('#mapSourceTrigger')
        assert js("document.getElementById('mapSourceTrigger').getAttribute('aria-expanded') === 'true'")
        assert sum(window.isVisible() for window in app.topLevelWidgets()) == visible_before
        geometry = json.loads(js("JSON.stringify((()=>{const m=document.getElementById('mapSourceTriggerOptions'),r=m.getBoundingClientRect(),last=m.lastElementChild.getBoundingClientRect();return {width:r.width,height:r.height,unused:r.bottom-last.bottom,dpr:devicePixelRatio,hidden:document.getElementById('mapSource').hidden};})())"))
        assert geometry['hidden'] and abs(geometry['width'] - 190) < 1
        assert geometry['height'] < 110 and 0 <= geometry['unused'] <= 5
        assert abs(geometry['dpr'] - widget.view.devicePixelRatioF()) < .05
        assert widget.view.grab().save(str(args.output / 'source-menu.png'))
        click('#mapSourceTriggerOptions [data-value=protomaps]')
        assert commands == [{'action': 'map_source', 'source': 'protomaps'}], commands
        geometry['qt_device_pixel_ratio'] = widget.view.devicePixelRatioF()
        geometry['platform'] = app.platformName()
        (args.output / 'results.json').write_text(json.dumps(geometry, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(geometry))
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


if __name__ == '__main__':
    main()
