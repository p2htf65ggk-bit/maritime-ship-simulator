# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHeaderView, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QStyle, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget

from maritime_simulator.visual.offline_areas import AREAS_FILE, LARGE_AREA_TILES, area_identity, area_tile_count, export_areas, import_areas, manage_areas, normalize_area, saved_areas, selection_tile_count
from .dialogs import ask_question, ensure_text_button_fits, reserve_label_lines
from .offline_widgets import MapCellDelegate
from .s57_panel import S57Panel


def _file_size(size):
    return f"{size / 1024**3:.1f} GB" if size >= 1024**3 else f"{size / 1024**2:.1f} MB" if size >= 1024**2 else f"{size / 1024:.1f} KB"


class OfflinePanel(QWidget):
    def __init__(self, current, changed, directory: Path, select_area, parent=None, *, show_chart=None, choose_layers=None):
        super().__init__(parent)
        self.current, self.changed, self.directory = current, changed, directory
        root = QVBoxLayout(self)
        self.enabled = QCheckBox("Offline mode")
        self.enabled.toggled.connect(self._toggle)
        root.addWidget(self.enabled)
        self.status = QLabel()
        self.status.setWordWrap(True)
        reserve_label_lines(self.status, 2)
        root.addWidget(self.status)
        self.manager = OfflineAreaManager(directory, self, select_area=select_area)
        self.manager.areas_updated.connect(self._managed)
        self.map_tabs = QTabWidget()
        self.map_tabs.addTab(self.manager, "Protomaps")
        self.s57 = S57Panel(directory, lambda: self.current().enabled, self, show_chart=show_chart, choose_layers=choose_layers,
                            defer_load=True)
        self.s57.charts_changed.connect(self._charts_changed)
        self.map_tabs.addTab(self.s57, "S57 / ENC")
        self.map_tabs.currentChanged.connect(lambda index: self.s57.refresh() if index == 1 else None)
        root.addWidget(self.map_tabs, 1)
        self.refresh()

    def refresh(self, *, include_charts=True):
        settings = self.current()
        self.enabled.blockSignals(True)
        self.enabled.setChecked(settings.enabled)
        self.enabled.blockSignals(False)
        areas = self.manager.expected if self.manager.job is not None else saved_areas(self.directory.resolve())
        missing = any(name and not (self.directory / name).is_file() for name in (settings.map_file, settings.water_file))
        self.status.setText(
            f"Saved areas: {len(areas)}"
            + ("\nSaved map package is missing. Download the area on this computer." if missing else "")
        )
        self.manager.refresh()
        if include_charts and self.s57._loaded:
            self.s57.refresh()

    def _toggle(self, enabled):
        self.changed(replace(self.current(), enabled=enabled))
        self.refresh()

    def shutdown(self):
        self.manager.shutdown()
        self.s57.shutdown()

    def _charts_changed(self):
        self.changed(replace(self.current(), enc_cells=self.s57.store.enabled_cells()))
        self.refresh()

    def _managed(self, filename):
        settings = self.current()
        if self.manager._job_kind != "rename" or (settings.map_file, settings.water_file) != (filename, filename):
            self.changed(replace(settings, map_file=filename, water_file=filename))
        self.refresh(include_charts=False)


class _AreaMaintenance(QThread):
    progress = Signal(int, int, str)
    area_progress = Signal(object)
    completed = Signal(str, str)

    def __init__(self, selections, expected, directory, parent, *, additions=()):
        super().__init__(parent)
        self.selections, self.directory = selections, directory
        self.expected, self.additions = expected, additions
        self.cancel = Event()

    def run(self):
        try:
            filename = manage_areas(self.directory, self.selections, self.expected, additions=self.additions,
                                    cancel=self.cancel, progress=self.progress.emit, area_progress=self.area_progress.emit)
            self.completed.emit(filename, "")
        except Exception as error:
            self.completed.emit("", str(error))


class _AreaTransfer(_AreaMaintenance):
    preview = Signal(object)

    def __init__(self, operation, filename, selected, expected, directory, parent):
        super().__init__(selected, expected, directory, parent)
        self.operation, self.filename = operation, filename
        self.result_text = ""

    def run(self):
        try:
            kwargs = dict(cancel=self.cancel, progress=self.progress.emit, area_progress=self.area_progress.emit)
            if self.operation == "export":
                filename = export_areas(self.directory, self.selections, self.filename, **kwargs)
                self.result_text = f"Exported {len(self.selections)} area(s) to {Path(filename).name}"
            else:
                filename, added, skipped = import_areas(self.directory, self.filename, preview=self.preview.emit, **kwargs)
                self.result_text = f"Imported {added} area(s); kept {skipped} already listed area(s) unchanged"
            self.completed.emit(filename, "")
        except Exception as error:
            self.completed.emit("", str(error))


class OfflineAreaManager(QWidget):
    areas_updated = Signal(str)

    def __init__(self, directory, parent=None, *, select_area=None):
        super().__init__(parent)
        self.directory = directory.resolve()
        self.expected = saved_areas(self.directory)
        self.pending = []
        self.records = []
        self._counts = {}
        self._row_results = {}
        self._attempt_added = set()
        self._attempt_downloads = []
        self._checked = set()
        self._transfer_pending = set()
        self._job_kind = "update"
        self.job = None
        self._closing = False
        self._result_text = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        heading = QHBoxLayout()
        title = QLabel("Offline maps")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        heading.addWidget(title)
        heading.addStretch(1)
        self.add_area = QPushButton("Add Area...")
        self.add_area.setIcon(self.style().standardIcon(QStyle.SP_FileDialogNewFolder))
        ensure_text_button_fits(self.add_area)
        if select_area is not None:
            self.add_area.clicked.connect(select_area)
        self.add_area.setVisible(select_area is not None)
        heading.addWidget(self.add_area)
        root.addLayout(heading)
        transfers = QHBoxLayout()
        self.select_all = QCheckBox("Select All")
        self.select_all.toggled.connect(self._select_all)
        transfers.addWidget(self.select_all)
        transfers.addStretch(1)
        self.export = QPushButton("Export Selected...")
        self.export.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        ensure_text_button_fits(self.export)
        self.export.clicked.connect(self._export_selected)
        transfers.addWidget(self.export)
        self.import_button = QPushButton("Import Areas...")
        self.import_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        ensure_text_button_fits(self.import_button)
        self.import_button.clicked.connect(self._import_file)
        transfers.addWidget(self.import_button)
        root.addLayout(transfers)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Area name", "Detail", "Tiles", "Size", "Status", "Progress", "Action", ""])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setWordWrap(False)
        self.table.setItemDelegate(MapCellDelegate(self.table, progress_column=5, action_column=6, delete_column=7))
        self.table.itemDelegate().action.connect(lambda row, delete: self._delete_clicked(row) if delete else self._area_action(row))
        self.table.verticalHeader().setDefaultSectionSize(max(38, self.table.fontMetrics().height() + 22))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self._set_column_widths()
        root.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        reserve_label_lines(self.summary, 2)
        root.addWidget(self.summary)
        self.status = QLabel()
        self.status.setWordWrap(True)
        reserve_label_lines(self.status, 3)
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.download = QPushButton("Download Queued Areas")
        self.download.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        ensure_text_button_fits(self.download)
        self.download.clicked.connect(self._download_queued)
        buttons.addWidget(self.download)
        buttons.addStretch(1)
        self.delete_selected = QPushButton("Delete Selected")
        self.delete_selected.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.delete_selected.setToolTip("Delete the checked saved areas; shared data needed by other areas is kept")
        ensure_text_button_fits(self.delete_selected)
        self.delete_selected.clicked.connect(self._delete_selected)
        buttons.addWidget(self.delete_selected)
        self.cancel = QPushButton("Cancel Download")
        ensure_text_button_fits(self.cancel)
        self.cancel.clicked.connect(self._cancel)
        buttons.addWidget(self.cancel)
        root.addLayout(buttons)
        self.table.itemChanged.connect(self._name_changed)
        self._populate()
        QTimer.singleShot(0, self._ensure_sizes)

    def _ensure_sizes(self):
        if not self._closing and self.job is None and any("content_bytes" not in area for area in self.expected):
            self._start_operation(self.expected, kind="stats")

    def _set_column_widths(self):
        metrics = self.table.fontMetrics()
        header_metrics = self.table.horizontalHeader().fontMetrics()
        samples = ("Offline area name", "Regional detail", "200,000 / 200,000", "1023.9 MB",
                   "Downloading", "100%", "Download", "")
        padding = (30, 40, 24, 24, 24, 24, 32, 24)
        for column, (sample, extra) in enumerate(zip(samples, padding)):
            width = max(metrics.horizontalAdvance(sample), header_metrics.horizontalAdvance(self.table.horizontalHeaderItem(column).text())) + extra
            if column == 0:
                width *= 2
            if column == 5:
                width = max(125, width)
            if column == 7:
                width = max(36, width)
            self.table.setColumnWidth(column, width)

    def _populate(self):
        blocker = QSignalBlocker(self.table)
        self._checked &= {area_identity(area) for area in self.expected}
        self.table.setRowCount(0)
        self.records = self.expected + self.pending
        self.table.setRowCount(len(self.records))
        for row, area in enumerate(self.records):
            is_saved = row < len(self.expected)
            name = QTableWidgetItem(area["name"])
            name.setData(Qt.UserRole, row)
            if is_saved:
                name.setFlags(name.flags() | Qt.ItemIsUserCheckable)
                name.setCheckState(Qt.Checked if area_identity(area) in self._checked else Qt.Unchecked)
            west, south, east, north = area["bounds"]
            shape = f"Polygon: {len(area['polygon'])} corners" if "polygon" in area else "Rectangle"
            name.setToolTip(f"{area['name']}\n{shape}\nLatitude: {south:.4f} to {north:.4f}\nLongitude: {west:.4f} to {east:.4f}")
            self.table.setItem(row, 0, name)
            detail = QComboBox(self.table)
            for label, value in (("Harbour detail", 15), ("Regional detail", 14), ("Overview", 13)):
                detail.addItem(label, value)
            detail.setCurrentIndex(detail.findData(area["max_zoom"]))
            detail.setProperty("area_bounds", normalize_area(area))
            detail.currentIndexChanged.connect(self._detail_changed)
            self.table.setCellWidget(row, 1, detail)
            for column in (2, 3, 4):
                item = QTableWidgetItem()
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, column, item)
            count = area.get("tile_count") if is_saved else None
            count = self._tile_count(area) if count is None else count
            exact = is_saved and "content_bytes" in area
            self.table.item(row, 2).setText(f"{count:,}")
            self.table.item(row, 3).setText(_file_size(area["content_bytes"]) if exact else "--")
            self.table.item(row, 3).setToolTip("Actual saved map and local water-source content, not network transfer volume. Size is unknown before download. Shared tiles can count in multiple areas. Package size below includes database overhead.")
            self.table.item(row, 4).setText(self._row_results.get(area_identity(area), "Downloaded" if is_saved else "Queued"))
            for column, text in ((5, ""), (6, "Renew" if is_saved else "Download"), (7, "")):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                self.table.setItem(row, column, item)
            self.table.item(row, 5).setData(Qt.UserRole, 100 if is_saved else 0)
            self.table.item(row, 7).setToolTip("Delete saved area" if is_saved else "Remove queued area")
        self._update_controls()

    def _tile_count(self, area):
        key = (area_identity(area), area["max_zoom"])
        if key not in self._counts:
            self._counts[key] = area_tile_count(area["bounds"], area["max_zoom"], area.get("polygon"))
            if len(self._counts) > 256:
                self._counts.pop(next(iter(self._counts)))
        return self._counts[key]

    def _find_row(self, bounds):
        key = area_identity(bounds) if isinstance(bounds, dict) else tuple(bounds)
        for row in range(self.table.rowCount()):
            area = self.records[self.table.item(row, 0).data(Qt.UserRole)]
            if area_identity(area) == key:
                return row
        return None

    def _sync_pending(self):
        pending = []
        for row in range(self.table.rowCount()):
            index = self.table.item(row, 0).data(Qt.UserRole)
            if index >= len(self.expected):
                pending.append(dict(self.records[index], name=self.table.item(row, 0).text().strip()[:64] or "Offline area",
                                    max_zoom=self.table.cellWidget(row, 1).currentData()))
        self.pending = pending

    def refresh(self):
        if self.job is None:
            latest = saved_areas(self.directory)
            if latest != self.expected:
                self._sync_pending()
                self.expected = latest
                self._populate()
        self._update_controls()

    def queue_areas(self, selections):
        if self.job is not None:
            raise ValueError("Wait for the current map update before adding areas")
        self._sync_pending()
        selections = [dict(item, **normalize_area(item)) for item in selections]
        identities = {area_identity(area) for area in self.expected + self.pending}
        for item in selections:
            key = area_identity(item)
            if key in identities:
                raise ValueError("This area is already listed; use its Detail or Renew setting")
            identities.add(key)
        additions = [dict(item, bounds=list(item["bounds"]), name=item.get("name") or f"Offline area {len(self.expected) + len(self.pending) + index + 1}")
                     for index, item in enumerate(selections)]
        selection_tile_count(self.pending + additions)
        self.pending += additions
        self._row_results.clear()
        self._result_text = ""
        self._populate()
        self.table.scrollToItem(self.table.item(self.table.rowCount() - 1, 0))

    def _update_controls(self):
        idle = self.job is None
        self.add_area.setEnabled(idle)
        self.import_button.setEnabled(idle)
        self.export.setEnabled(idle and bool(self._checked))
        self.delete_selected.setEnabled(idle and bool(self._checked))
        self.select_all.setEnabled(idle and bool(self.expected))
        blocker = QSignalBlocker(self.select_all)
        self.select_all.setChecked(bool(self.expected) and all(area_identity(area) in self._checked for area in self.expected))
        self.download.setEnabled(idle and bool(self.pending))
        self.cancel.setVisible(not idle)
        self.cancel.setText("Cancel Export" if self._job_kind == "export" else "Cancel Import" if self._job_kind == "import" else "Cancel Delete" if self._job_kind == "delete" else "Cancel Update" if self._job_kind in {"rename", "stats"} else "Cancel Download")
        self.cancel.setEnabled(not idle and not self.job.cancel.is_set())
        text = f"{len(self.expected)} downloaded / {len(self.pending)} queued areas"
        path = self.directory / AREAS_FILE
        if path.exists():
            text += f" | Package: {_file_size(path.stat().st_size)}"
        if self.pending:
            try:
                count = selection_tile_count(self.pending)
                text += f"\nQueued: {count:,} unique tiles"
                if idle:
                    self.download.setEnabled(True)
            except ValueError as error:
                text += f"\n{error}"
                self.download.setEnabled(False)
        self.summary.setText(text)
        if idle:
            self.status.setText(self._result_text or ("Ready" if self.expected else "No downloaded areas"))
        self._set_editable(idle)

    @Slot(QTableWidgetItem)
    def _name_changed(self, item):
        if item.column() != 0 or self.job is not None:
            return
        index = item.data(Qt.UserRole)
        if index < len(self.expected):
            key = area_identity(self.expected[index])
            self._checked.add(key) if item.checkState() == Qt.Checked else self._checked.discard(key)
            self.export.setEnabled(bool(self._checked))
            self.delete_selected.setEnabled(bool(self._checked))
            blocker = QSignalBlocker(self.select_all)
            self.select_all.setChecked(bool(self.expected) and len(self._checked) == len(self.expected))
        name = item.text().strip()[:64] or "Offline area"
        if index >= len(self.expected):
            self._sync_pending()
            return
        if name != self.expected[index]["name"]:
            desired = [dict(area) for area in self.expected]
            desired[index]["name"] = name
            self._start_operation(desired, kind="rename")

    @Slot()
    def _detail_changed(self):
        if self.job is not None:
            return
        control = self.sender()
        row = self._find_row(control.property("area_bounds"))
        if row is None:
            return
        index = self.table.item(row, 0).data(Qt.UserRole)
        if index >= len(self.expected):
            previous = self.pending
            self._sync_pending()
            try:
                selection_tile_count(self.pending)
            except ValueError as error:
                self.pending = previous
                self._result_text = str(error)
            self._populate()
            return
        area = self.expected[index]
        zoom = control.currentData()
        if zoom == area["max_zoom"]:
            return
        updated = dict(area, max_zoom=zoom)
        if not self._confirm_download([updated]):
            blocker = QSignalBlocker(control)
            control.setCurrentIndex(control.findData(area["max_zoom"]))
            return
        desired = [dict(item) for item in self.expected]
        desired[index] = updated
        self._start_operation(desired)

    def _confirm_download(self, areas):
        try:
            count = selection_tile_count(areas)
        except ValueError as error:
            self._result_text = str(error)
            self.status.setText(self._result_text)
            return False
        known_size = sum(area.get("content_bytes", 0) for area in areas)
        if known_size <= 100 * 1024**2 and count < 500:
            return True
        previous = f"Previously saved content: {_file_size(known_size)}.\n" if known_size else ""
        warning = "Very large selection: downloading and preparing it may take a long time.\n" if count > LARGE_AREA_TILES else ""
        return ask_question(self, "Large offline map download",
                            f"Download {len(areas)} area(s), {count:,} unique tiles?\n"
                            + warning + previous + "The new size is only known during download.\n"
                            "The areas will be downloaded one at a time. Existing maps remain available.") == QMessageBox.Yes

    def _area_action(self, row):
        if self.job is not None:
            return
        self._sync_pending()
        index = self.table.item(row, 0).data(Qt.UserRole)
        area = self.records[index] if index < len(self.expected) else self.pending[index - len(self.expected)]
        if not self._confirm_download([area]):
            return
        if index < len(self.expected):
            desired = [dict(item) for item in self.expected]
            desired[index] = dict(area, refresh=True)
            self._start_operation(desired)
        else:
            self._start_operation(self.expected, [area])

    @Slot()
    def _download_queued(self):
        if self.job is not None:
            return
        self._sync_pending()
        if self.pending and self._confirm_download(self.pending):
            self._start_operation(self.expected, self.pending)

    def _delete_clicked(self, row):
        if self.job is not None:
            return
        self._sync_pending()
        index = self.table.item(row, 0).data(Qt.UserRole)
        if index >= len(self.expected):
            self.pending.pop(index - len(self.expected))
            self._populate()
            return
        area = self.expected[index]
        size = area.get("content_bytes")
        size_text = _file_size(size) if size is not None else "size unknown"
        if ask_question(self, "Delete offline area",
                        f'Delete "{area["name"]}" ({size_text})?\nShared tiles needed by other areas will be kept.') != QMessageBox.Yes:
            return
        self._start_operation([item for i, item in enumerate(self.expected) if i != index], kind="delete")

    def _start_operation(self, desired, additions=(), *, kind="update"):
        if self.job is not None:
            return
        self._sync_pending()
        originals = {area_identity(area): area for area in self.expected}
        self._attempt_downloads = [area for area in desired if area.get("refresh") or area["max_zoom"] != originals[area_identity(area)]["max_zoom"]] + list(additions)
        self._attempt_added = {area_identity(area) for area in additions}
        self._row_results.clear()
        self._result_text = ""
        self._job_kind = kind
        self.job = _AreaMaintenance(desired, self.expected, self.directory, self, additions=list(additions))
        self.job.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.job.area_progress.connect(self._area_progress, Qt.ConnectionType.QueuedConnection)
        self.job.completed.connect(self._completed, Qt.ConnectionType.QueuedConnection)
        self.job.finished.connect(self._finished)
        self._update_controls()
        self.status.setText("Deleting unused offline tiles..." if kind == "delete" else "Updating offline maps...")
        self.job.start()

    @Slot(bool)
    def _select_all(self, checked):
        if self.job is not None:
            return
        self._sync_pending()
        self._checked = {area_identity(area) for area in self.expected} if checked else set()
        self._populate()

    @Slot()
    def _delete_selected(self):
        if self.job is not None or self._closing:
            return
        selected = [area for area in self.expected if area_identity(area) in self._checked]
        if not selected:
            return
        names = "\n".join(f'- {area["name"]}' for area in selected[:10])
        if len(selected) > 10:
            names += f"\n... and {len(selected) - 10} more areas"
        if ask_question(self, "Delete selected offline areas",
                        f"Delete {len(selected)} selected saved area(s)?\n\n{names}\n\n"
                        "Shared data needed by other areas will be kept. Queued downloads are not removed.") != QMessageBox.Yes:
            return
        if not self._closing:
            keys = {area_identity(area) for area in selected}
            self._start_operation([area for area in self.expected if area_identity(area) not in keys], kind="delete")

    @Slot()
    def _export_selected(self):
        if self.job is not None:
            return
        selected = [area for area in self.expected if area_identity(area) in self._checked]
        if not selected:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Export selected offline areas", "Maritime-Offline-Maps.mbtiles", "Offline map packages (*.mbtiles)")
        if filename:
            if not filename.lower().endswith(".mbtiles"):
                filename += ".mbtiles"
            self._start_transfer("export", filename, selected)

    @Slot()
    def _import_file(self):
        if self.job is not None:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Import offline areas", "", "Offline map packages (*.mbtiles)")
        if not filename:
            return
        try:
            size = Path(filename).stat().st_size
        except OSError as error:
            self._result_text = str(error)
            self.status.setText(self._result_text)
            return
        if size > 100 * 1024**2:
            if ask_question(self, "Large offline map import", f"Import {_file_size(size)} of offline maps?\nExisting areas and shared tiles will be retained.") != QMessageBox.Yes:
                return
        self._start_transfer("import", filename, [])

    def _start_transfer(self, operation, filename, selected):
        if self.job is not None:
            return
        self._sync_pending()
        self._job_kind = operation
        self._transfer_pending.clear()
        self._result_text = ""
        self._row_results.clear()
        self.job = _AreaTransfer(operation, filename, selected, self.expected, self.directory, self)
        self.job.preview.connect(self._import_preview, Qt.ConnectionType.QueuedConnection)
        self.job.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.job.area_progress.connect(self._area_progress, Qt.ConnectionType.QueuedConnection)
        self.job.completed.connect(self._completed, Qt.ConnectionType.QueuedConnection)
        self.job.finished.connect(self._finished)
        self._update_controls()
        self.status.setText("Exporting offline areas..." if operation == "export" else "Checking offline package...")
        self.job.start()

    @Slot(object)
    def _import_preview(self, areas):
        if self._closing:
            return
        listed = {area_identity(area) for area in self.records}
        for area in areas:
            if area_identity(area) not in listed:
                self.pending.append(dict(area))
                self._transfer_pending.add(area_identity(area))
        self._populate()

    @Slot()
    def _cancel(self):
        if self.job is not None:
            self.job.cancel.set()
            self.cancel.setEnabled(False)
            self.status.setText("Cancelling; previous saved maps are retained...")

    @Slot(int, int, str)
    def _progress(self, count, total, text):
        if not self._closing:
            self.status.setText(text)

    @Slot(object)
    def _area_progress(self, updates):
        if self._closing:
            return
        blocker = QSignalBlocker(self.table)
        for update in updates:
            row = self._find_row(update)
            if row is None:
                continue
            self.table.item(row, 2).setText(f"{update['done']:,} / {update['total']:,}")
            self.table.item(row, 3).setText(_file_size(update["content_bytes"]) if update["done"] else "--")
            self.table.item(row, 4).setText(update["status"])
            self.table.item(row, 5).setData(Qt.UserRole, round(100 * update["done"] / max(1, update["total"])))

    def _set_editable(self, enabled):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsEnabled if enabled
                          else item.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsEnabled)
            self.table.cellWidget(row, 1).setEnabled(enabled)
            for column in (6, 7):
                self.table.item(row, column).setFlags(Qt.ItemIsEnabled if enabled else Qt.NoItemFlags)

    @Slot(str, str)
    def _completed(self, filename, error):
        if self._closing:
            return
        self._result_text = error or getattr(self.job, "result_text", "") or "Offline maps updated"
        if not error:
            if self._job_kind != "export":
                self.expected = saved_areas(self.directory)
                saved = {area_identity(area) for area in self.expected}
                self.pending = [area for area in self.pending if area_identity(area) not in saved]
                self._checked &= saved
            self._populate()
            if self._job_kind in {"update", "import", "rename", "delete"}:
                self.areas_updated.emit(filename)
        else:
            state = "Cancelled" if self.job.cancel.is_set() else "Failed"
            self._row_results = {area_identity(area): state for area in self._attempt_downloads} if self._job_kind == "update" else {}
            self.pending = [area for area in self.pending if area_identity(area) not in self._transfer_pending]
            self._populate()
        self.status.setText(self._result_text)

    def _finished(self):
        self.job.deleteLater()
        self.job = None
        self._update_controls()

    def shutdown(self):
        self._closing = True
        if self.job is not None:
            self.job.cancel.set()
            self.job.wait()
