#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Read operator packages through private clones; exercise native large-catalogue UI."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import faulthandler
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication
from maritime_simulator.visual.offline import OfflineSettings
from maritime_simulator.visual.s57 import S57Store
from maritime_simulator.ui.main_window import V2MainWindow
from maritime_simulator.visual.water_mask import OSMNavigableWaterMask
from ui.style import apply_global_ui_style


def clone(source, target):
    result = subprocess.run(['cp', '-c', str(source), str(target)], capture_output=True) if sys.platform == 'darwin' else None
    if result is None or result.returncode:
        shutil.copyfile(source, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--maps', type=Path, required=True)
    parser.add_argument('--enc', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    originals = [args.maps, args.enc / 'charts.sqlite3']
    signatures = [(path.stat().st_size, path.stat().st_mtime_ns) for path in originals]
    finished = Event()
    def watchdog():
        started = time.monotonic()
        while not finished.wait(.1):
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            mib = peak / (1024**2 if sys.platform == 'darwin' else 1024)
            if mib > 2048 or time.monotonic() - started > 30:
                print(f'Native UI watchdog: {mib:.1f} MiB; {time.monotonic() - started:.1f} s', flush=True)
                faulthandler.dump_traceback()
                os._exit(3)  # Only this isolated test process; never the operator's application.
    app = QApplication([])
    apply_global_ui_style(app)
    QAccessible.setActive(True)
    def pump(seconds=.2):
        stop = time.monotonic() + seconds
        while time.monotonic() < stop:
            app.processEvents()
            time.sleep(.005)
    result = {'platform': QApplication.platformName(), 'accessibilityActive': True}
    with tempfile.TemporaryDirectory(prefix='mss-offline-settings-') as work:
        work = Path(work)
        directory = work / 'offline-packages'
        (directory / 's57').mkdir(parents=True)
        clone(args.maps, directory / 'offline-areas.mbtiles')
        clone(originals[1], directory / 's57/charts.sqlite3')
        # Exercise the real Settings window, with no Internet or operator output paths.
        initialize_mask = OSMNavigableWaterMask.__init__
        def offline_mask(self, *args, **kwargs):
            kwargs['allow_network'] = False
            initialize_mask(self, *args, **kwargs)
        OSMNavigableWaterMask.__init__ = offline_mask
        print('Creating isolated main window', flush=True)
        window = V2MainWindow(config_dir=work)
        print('Main window ready', flush=True)
        window.timer.stop()
        window.output_runtime.stop()
        store = S57Store(directory / 's57')
        settings = OfflineSettings(True, 'offline-areas.mbtiles', 'offline-areas.mbtiles', 's57', enc_cells=store.enabled_cells())
        window._apply_offline_settings(settings)
        print('S57 source ready', flush=True)
        with ThreadPoolExecutor(max_workers=1) as pool:
            query = pool.submit(store.features, (-74.085,40.67,-74.015,40.72),15)
            Thread(target=watchdog, daemon=True).start()
            timer = QTimer()
            gaps, last = [], [time.monotonic()]
            def heartbeat():
                now = time.monotonic()
                gaps.append(now - last[0])
                last[0] = now
            timer.timeout.connect(heartbeat)
            timer.start(10)
            started = time.monotonic()
            window._open_settings()
            print('Settings created', flush=True)
            dialog = window.settings_dialog
            tabs = dialog.tabs
            panel = dialog.offline_panel
            app.processEvents()
            result['firstSettingsMs'] = round((time.monotonic() - started) * 1000,3)
            assert not panel.s57._loaded and panel.s57.table.model().rowCount() == 0
            pump()
            changes, resets = [], []
            panel.s57.table.model().dataChanged.connect(lambda *_args: changes.append(1))
            panel.s57.table.model().modelReset.connect(lambda: resets.append(1))
            started = time.monotonic()
            tabs.setCurrentWidget(panel)
            panel.map_tabs.setCurrentWidget(panel.s57)
            print('S57 table created', flush=True)
            app.processEvents()
            result['firstS57Ms'] = round((time.monotonic() - started) * 1000,3)
            result['rows'] = panel.s57.table.model().rowCount()
            assert result['rows'] == len(store.records())
            assert resets == [1] and not changes
            pump()
            for name, call in [('selectAll',lambda: panel.s57._select_visible(True)),
                               ('clearSelection',lambda: panel.s57._select_visible(False)),
                               ('refresh',panel.s57.refresh)]:
                started = time.monotonic()
                call()
                app.processEvents()
                result[name + 'Ms'] = round((time.monotonic() - started) * 1000,3)
                pump()
            result['queryFeatures'] = len(query.result()['features'])
            result['maximumHeartbeatGapMs'] = round(max(gaps) * 1000,3)
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            result['peakProcessMiB'] = round(peak / (1024**2 if sys.platform == 'darwin' else 1024),1)
            dialog.grab().save(str(args.report.with_suffix('.png')))
            timer.stop()
            panel.shutdown()
            dialog.close()
            window._configuration_baseline = window._configuration_payload()
            window._interactive_close_confirmation_enabled = lambda: False
            window.close()
            finished.set()
    result['sourceUntouched'] = signatures == [(path.stat().st_size,path.stat().st_mtime_ns) for path in originals]
    assert result['sourceUntouched']
    args.report.write_text(json.dumps(result,indent=2) + '\n',encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
