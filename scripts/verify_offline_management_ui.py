#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Capture offline management and check consistent horizontal progress bars."""
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QPushButton
from maritime_simulator.ui.offline_panel import OfflinePanel
from maritime_simulator.visual.offline import OfflineSettings
from ui.style import apply_global_ui_style


def green_bounds(image):
    points = [(x,y) for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x,y).name() == "#37a875"]
    assert points, "The shared green progress style must be visible"
    return min(x for x,y in points), min(y for x,y in points), max(x for x,y in points), max(y for x,y in points)


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/offline-management-ui-qa")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    apply_global_ui_style(app)
    results = []
    with tempfile.TemporaryDirectory(prefix="mss-offline-ui-") as directory:
        panel = OfflinePanel(lambda: OfflineSettings(enabled=True), lambda _: None, Path(directory), lambda: None, show_chart=lambda *_: None)
        try:
            panel.s57.store.import_files([ROOT / "tests/fixtures/s57/US4NJ1DG.zip"])
            panel.s57.refresh()
            panel.manager.expected = [dict(name="Harbour demonstration", bounds=[9.95,53.54,9.96,53.55],
                max_zoom=15,tile_count=250,content_bytes=20*1024**2)]
            panel.manager._populate()
            panel.manager.table.item(0,5).setData(Qt.UserRole, 50)
            panel.s57._cell_progress("US4NJ1DG","Downloading",50)
            for width in (1100,760):
                panel.resize(width,650)
                panel.show()
                for index, name in ((0,"protomaps"),(1,"s57")):
                    panel.map_tabs.setCurrentIndex(index)
                    app.processEvents()
                    widget = panel.manager if index == 0 else panel.s57
                    for button in widget.findChildren(QPushButton):
                        if button.isVisible() and not widget.table.isAncestorOf(button):
                            rect = button.rect().translated(button.mapTo(panel,QPoint(0,0)))
                            assert panel.rect().contains(rect), (name,width,button.text(),rect)
                    panel.grab().save(str(output / f"{name}-{width}.png"))
                    if width == 1100:
                        table = widget.table
                        table.horizontalScrollBar().setValue(0)
                        app.processEvents()
                        column = 5 if index == 0 else 6
                        cell = table.model().index(0, column)
                        table.scrollTo(cell)
                        app.processEvents()
                        image = table.viewport().grab(table.visualRect(cell)).toImage()
                        left,top,right,bottom = green_bounds(image)
                        assert right-left > bottom-top, "Progress must grow horizontally, not vertically"
                        assert .35 < (right-left)/image.width() < .65, "50% must fill roughly half the bar"
                        results.append(dict(tab=name,green_bounds=[left,top,right,bottom],bar_size=[image.width(),image.height()]))
                    if index == 1:
                        panel.s57.table.horizontalScrollBar().setValue(0)
            (output / "result.json").write_text(json.dumps(results,indent=2)+"\n",encoding="utf-8")
            print(json.dumps(results))
        finally:
            panel.shutdown()
            panel.hide()
            panel.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    main()
