# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import sqlite3
import tempfile
from threading import Event

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QSignalBlocker, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QStyle, QTableView, QVBoxLayout, QWidget

from maritime_simulator.visual.s57 import S57Store, check_cancel, download, load_catalog, plan_import_cells, staged_import_cells
from maritime_simulator.visual.noaa import DOWNLOAD_WORKERS, cell_is_current, cell_needs_download, sync_cells
from .dialogs import OpaqueDialog, ask_question, ensure_text_button_fits, reserve_label_lines
from .offline_widgets import ACTION_HIGHLIGHT_ROLE, MapCellDelegate, map_progress_bar
from .s57_table import ChartFilterProxy, NOAATableModel, S57TableModel, chart_date as _date


def _row_selection(model, rows):
    selection = QItemSelection()
    first = last = None
    for row in sorted(set(rows)):
        if last is not None and row != last + 1:
            selection.select(model.index(first, 0), model.index(last, model.columnCount() - 1))
            first = None
        if first is None:
            first = row
        last = row
    if first is not None:
        selection.select(model.index(first, 0), model.index(last, model.columnCount() - 1))
    return selection


class _ENCJob(QThread):
    result = Signal(object, str)
    progress = Signal(int, int, str)
    cell_progress = Signal(str, str, int)
    large_download = Signal(int, object)

    def __init__(self, store, operation, data=(), parent=None, *, force=False, confirm_large=False):
        super().__init__(parent)
        self.store, self.operation, self.data = store, operation, data
        self.cancel = Event()
        self.cell_errors = {}
        self.current_codes = []
        self.catalog_records = None
        self.force = force
        self.confirm_large = confirm_large
        self.approval = Event()
        self.approved = False

    def approve(self, accepted):
        self.approved = bool(accepted)
        self.approval.set()

    def run(self):
        value = []
        active = None
        try:
            if self.operation.startswith("catalog"):
                value = load_catalog(self.store.directory, refresh=True,
                                     cancel=self.cancel, progress=self.progress.emit)
                self.catalog_records = value
            elif self.operation == "import":
                self.progress.emit(0, 0, "Scanning S57 files and folders...")
                cells = plan_import_cells(self.data, self.cancel)
                with tempfile.TemporaryDirectory(prefix="enc-batch-", dir=self.store.directory) as work:
                    with closing(staged_import_cells(cells, work, self.cancel)) as staged:
                        for index, source in enumerate(staged):
                            check_cancel(self.cancel)
                            active = source.stem.upper()
                            label = f"Importing {index + 1}/{len(cells)}: {active}"
                            self.progress.emit(index, len(cells), label)
                            self.cell_progress.emit(active, "Importing", 0)
                            imported = self.store.import_files([source], cancel=self.cancel,
                                                               progress=lambda *_args: self.progress.emit(index, len(cells), label))
                            value.extend(imported)
                            for code in imported:
                                self.cell_progress.emit(code, "Ready", 100)
                            active = None
                            self.progress.emit(index + 1, len(cells), label)
            elif self.operation == "delete":
                self.store.delete(self.data, cancel=self.cancel)
                value = self.data
            elif self.operation == "show":
                self.progress.emit(0, 0, "Enabling selected charts...")
                self.store.set_enabled_many(self.data, True, cancel=self.cancel)
                value = self.data
            else:
                self.store.directory.mkdir(parents=True, exist_ok=True)
                records = self.data
                catalog = None
                if self.operation == "renew":
                    self.catalog_records = load_catalog(self.store.directory, refresh=True,
                                                        cancel=self.cancel, progress=self.progress.emit)
                    catalog = {row["code"]: row for row in self.catalog_records}
                elif self.operation == "download":
                    self.catalog_records = list(records)
                skipped = []
                candidates = []
                for row in records:
                    check_cancel(self.cancel)
                    code = row["code"]
                    if catalog is not None:
                        if code not in catalog:
                            skipped.append(code)
                            self.cell_errors[code] = "Not in the active NOAA catalog. Existing chart kept; import updated original files/folder to renew it."
                            self.cell_progress.emit(code, "Skipped", 0)
                            continue
                        row = catalog[code]
                    candidates.append(row)
                if self.confirm_large:
                    installed = {row["code"]: row for row in self.store.records()}
                    needed = [row for row in candidates if cell_needs_download(row, installed.get(row["code"]), force=self.force)]
                    size = sum(row["size"] for row in needed)
                    if size > 100 * 1024**2:
                        self.large_download.emit(len(needed), size)
                        while not self.approval.wait(0.1):
                            check_cancel(self.cancel)
                        check_cancel(self.cancel)
                        if not self.approved:
                            self.result.emit([], "NOAA download cancelled; existing charts were kept.")
                            return
                with tempfile.TemporaryDirectory(prefix="noaa-download-", dir=self.store.directory) as work:
                    batch = sync_cells(self.store, candidates, work, cancel=self.cancel,
                                       progress=self.progress.emit, cell_progress=self.cell_progress.emit,
                                       force=self.force, download_fn=download)
                value = batch.imported
                self.current_codes = batch.current
                self.cell_errors.update(batch.errors)
                if batch.errors or skipped or batch.cancelled:
                    detail = "; ".join(f"{code}: {error}" for code, error in list(batch.errors.items())[:3])
                    skipped_note = (" Renew skipped local/withdrawn cells by importing updated original files/folder."
                                    if skipped else "")
                    cancelled_note = " Cancelled; existing and completed charts were kept." if batch.cancelled else ""
                    self.result.emit(value, f"{len(value)} completed cell(s) retained. {len(batch.errors)} failed; {len(skipped)} skipped; "
                                     f"{len(batch.current)} up to date. " + detail + skipped_note + cancelled_note)
                    return
            self.result.emit(value, "")
        except Exception as error:
            if active is not None:
                self.cell_progress.emit(active, "Cancelled" if self.cancel.is_set() else "Failed", 0)
            self.result.emit(value, f"{len(value)} completed cell(s) retained. {error}" if value else str(error))


class _NOAAChooser(OpaqueDialog):
    def __init__(self, records, installed, parent):
        super().__init__(parent)
        self.records = records
        self.installed = installed if isinstance(installed, dict) else {code: {} for code in installed}
        self.cell_model = NOAATableModel(records, self.installed, self)
        self.proxy = ChartFilterProxy(self)
        self.proxy.setSourceModel(self.cell_model)
        self.setWindowTitle("Download NOAA ENC cells")
        self.resize(950, 650)
        root = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search cell ID or area name")
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)
        self.select_all = QCheckBox("Select All Visible")
        self.select_all.setToolTip("Select or clear only the cells currently shown by the search. Hidden selections are retained.")
        self.select_all.clicked.connect(self._select_visible)
        root.addWidget(self.select_all)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((40, 120, 290, 120, 110, 100, 130)):
            self.table.setColumnWidth(column, width)
        self.cell_model.selection_changed.connect(self._checked)
        root.addWidget(self.table, 1)
        terms = QLabel('<a href="https://www.charts.noaa.gov/ENCs/ENC_Agreement.shtml">NOAA terms</a> | '
                       '<a href="https://www.charts.noaa.gov/ENCs/ENCs.shtml">NOAA ENC downloads</a>')
        terms.setOpenExternalLinks(True)
        root.addWidget(terms)
        self.count = QLabel()
        self.count.setWordWrap(True)
        reserve_label_lines(self.count, 2)
        root.addWidget(self.count)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.accept_button = buttons.button(QDialogButtonBox.Ok)
        self.accept_button.setText("Download Selected")
        ensure_text_button_fits(self.accept_button)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._checked()

    def _filter(self, text):
        self.proxy.set_search(text)
        self._update_select_all()

    @property
    def selected(self):
        return {self.cell_model.rows[code] for code in self.cell_model.selected_codes}

    def _update_select_all(self):
        visible = self.proxy.visible_codes()
        checked = len(self.cell_model.selected_codes & visible)
        with QSignalBlocker(self.select_all):
            self.select_all.setEnabled(bool(visible))
            self.select_all.setCheckState(Qt.Unchecked if not checked else Qt.Checked if checked == len(visible) else Qt.PartiallyChecked)

    def _select_visible(self, checked):
        self.cell_model.select_codes(self.proxy.visible_codes(), checked)
        self._update_select_all()

    def _checked(self, _item=None):
        selected = self.cell_model.selected_codes
        needed = selected - self.cell_model.current_codes
        self.download_records = [row for row in self.records if row["code"] in needed]
        size = sum(row["size"] for row in self.download_records)
        self.count.setText(f"{len(selected)} selected | {len(needed)} to download ({size/1024**2:.1f} MB) | "
                           f"{len(selected & self.cell_model.current_codes)} up to date | up to {DOWNLOAD_WORKERS} downloads + 1 import")
        self.accept_button.setEnabled(bool(selected))
        self._update_select_all()


class S57Panel(QWidget):
    charts_changed = Signal()

    def __init__(self, directory, offline, parent=None, *, show_chart=None, choose_layers=None, defer_load=False):
        super().__init__(parent)
        self.store = S57Store(Path(directory) / "s57")
        self.offline, self.show_chart = offline, show_chart
        self.job = None
        self._catalog_job = None
        self._catalog = None
        self._catalog_complete = False
        self._catalog_attempted = False
        self._closing = False
        self._items = []
        self._pending = {}
        self._row_states = {}
        self._rendered_states = {}
        self._rows = {}
        self._selected_codes = set()
        self._changing_selection = False
        self._loaded = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        title = QLabel("S57 / ENC")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        controls.addWidget(title)
        controls.addStretch(1)
        self.import_button = QPushButton("Import S57...")
        self.import_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        import_menu = QMenu(self.import_button)
        import_menu.addAction("Import Files...", self._import)
        import_menu.addAction("Import Folder...", self._import_folder)
        self.import_button.setMenu(import_menu)
        controls.addWidget(self.import_button)
        self.noaa_button = QPushButton("Download NOAA...")
        self.noaa_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        self.noaa_button.clicked.connect(lambda: self._start("catalog"))
        controls.addWidget(self.noaa_button)
        self.refresh_catalog = QPushButton("Refresh NOAA Catalog")
        self.refresh_catalog.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_catalog.clicked.connect(lambda: self._check_catalog(force=True))
        self.refresh_catalog.setToolTip("Check the latest NOAA catalogue in the background. Highlight missing cells and available updates without downloading charts.")
        for button in (self.import_button, self.noaa_button, self.refresh_catalog):
            ensure_text_button_fits(button)
        root.addLayout(controls)
        selection = QHBoxLayout()
        self.select_all = QCheckBox("Select All Visible")
        self.select_all.setToolTip("Select cells for Show on Map, Renew or Delete without changing their Use checkboxes.")
        self.select_all.clicked.connect(self._select_visible)
        selection.addWidget(self.select_all)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter imported cells by ID or area")
        self.search.textChanged.connect(self._filter)
        selection.addWidget(self.search, 1)
        selection.addWidget(self.refresh_catalog)
        root.addLayout(selection)
        self.cell_model = S57TableModel(self._row_states, self._action_details, self._enabled, self)
        self.cell_model.offline = bool(self.offline())
        self.proxy = ChartFilterProxy(self)
        self.proxy.setSourceModel(self.cell_model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setItemDelegate(MapCellDelegate(self.table))
        self.table.itemDelegate().action.connect(self._cell_action)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((50, 115, 220, 115, 95, 130, 125, 95, 36, 150, 110, 110)):
            sample = ("Use", "US4NJ1DG", "Area name", "1:999,999", "999.9 MB", "Not downloaded", "100%", "Unavailable", "", "999 / 999", "NOAA ENC", "2026-10-08")[column]
            self.table.setColumnWidth(column, max(width, self.table.fontMetrics().horizontalAdvance(sample) + 24,
                                                 self.table.horizontalHeader().fontMetrics().horizontalAdvance(self.cell_model.headers[column]) + 24))
        self.table.verticalHeader().setDefaultSectionSize(max(40, self.table.fontMetrics().height() + 18))
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        root.addWidget(self.table, 1)
        self.summary = QLabel()
        reserve_label_lines(self.summary, 2)
        root.addWidget(self.summary)
        self.bar = map_progress_bar()
        self.bar.setVisible(False)
        root.addWidget(self.bar)
        self.status = QLabel()
        self.status.setWordWrap(True)
        reserve_label_lines(self.status, 3)
        root.addWidget(self.status)
        footer = QHBoxLayout()
        self.show_button = QPushButton("Show on Map")
        self.show_button.clicked.connect(self._show)
        self.show_button.setVisible(show_chart is not None)
        footer.addWidget(self.show_button)
        footer.addStretch(1)
        self.renew = QPushButton("Renew Selected")
        self.renew.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.renew.setToolTip("Check NOAA cells and download only missing or outdated revisions. Current cells are kept without downloading. Local charts need their updated original files/folder.")
        self.renew.clicked.connect(self._renew)
        footer.addWidget(self.renew)
        self.delete = QPushButton("Delete Selected")
        self.delete.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.delete.clicked.connect(self._delete)
        footer.addWidget(self.delete)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel)
        footer.addWidget(self.cancel_button)
        for button in (self.show_button, self.renew, self.delete, self.cancel_button):
            ensure_text_button_fits(button)
        root.addLayout(footer)
        # The parent Settings dialog starts on Start-up; do not build a hidden catalogue.
        self.summary.setText("Open S57 / ENC to load installed charts")
        if not defer_load:
            self.refresh()

    def refresh(self):
        self._loaded = True
        if self.job is not None:
            self._controls()
            return
        installed = set()
        try:
            items = self.store.records()
            installed = {row["code"] for row in items}
            items += [row for code, row in self._pending.items() if code not in installed]
        except (OSError, ValueError, sqlite3.Error) as error:
            items = []
            self.status.setText(f"Cannot read the local S57 index: {error}")
        self.summary.setText(f"{len(installed)} installed / {len(self._pending)} pending cells")
        self._changing_selection = True
        scroll = self.table.verticalScrollBar().value()
        try:
            previous_search = self.cell_model.search_text
            reset = self.cell_model.set_records(items)
            self._items = self.cell_model.records
            self._rows = self.cell_model.rows
            self._selected_codes.intersection_update(self._rows)
            if not reset:
                for code, state in self._row_states.items():
                    if state != self._rendered_states.get(code):
                        self.cell_model.notify(self._rows.get(code, -1), 5, 6)
            if reset or previous_search != self.cell_model.search_text:
                self._restore_selection()
            if reset:
                self.table.doItemsLayout()
                self.table.verticalScrollBar().setValue(scroll)
        finally:
            self._changing_selection = False
        self._rendered_states = dict(self._row_states)
        self._controls()

    def _restore_selection(self):
        rows = []
        for code in self._selected_codes:
            index = self.proxy.mapFromSource(self.cell_model.index(self._rows[code], 0))
            if index.isValid():
                rows.append(index.row())
        selection = self.table.selectionModel()
        with QSignalBlocker(selection):
            selection.select(_row_selection(self.proxy, rows), QItemSelectionModel.ClearAndSelect)

    def _selection_changed(self, *_args):
        if self._changing_selection:
            return
        self._selected_codes.difference_update(self.proxy.visible_codes())
        self._selected_codes.update(self.proxy.record(index.row())["code"]
                                    for index in self.table.selectionModel().selectedRows())
        self._controls()

    def _selected_records(self):
        return [row for row in self._items if row["code"] in self._selected_codes]

    def _action_details(self, record):
        if not re.fullmatch(r"US[A-Z0-9]{6}", record["code"]):
            return "Import...", False, "Renew this local chart by importing updated original files/folder; it is not a NOAA download."
        available = self._catalog.get(record["code"]) if self._catalog is not None else None
        if self._catalog_complete and available is None:
            return "Unavailable", False, "This cell is not in the active NOAA catalogue and cannot be downloaded from NOAA. Existing data is kept; use Import S57 for updated original files/folder."
        installed = len(record.get("bounds") or []) == 4 and record.get("feature_count") != 0
        if not installed:
            return "Download", True, "Download and import this missing cell."
        if available is None:
            return "Check", False, "NOAA availability has not been verified. Check the latest catalogue for a newer revision; current cells are not downloaded."
        if cell_is_current(available, record):
            return "Reload", False, "No newer NOAA revision is available. Explicitly reload the available revision; newer local revisions are kept. Only downloads above 100 MB require confirmation."
        revision = f"{available['edition']} / {available['update']} ({_date(available['date'])})"
        return "Renew", True, f"A newer or unverified NOAA revision is available: {revision}. Download and import it."

    def _update_actions(self):
        self.cell_model.invalidate_cache()
        if self._items:
            self.cell_model.dataChanged.emit(self.cell_model.index(0, 7), self.cell_model.index(len(self._items)-1, 7), [])

    def _apply_catalog(self, records, *, complete=True):
        self._catalog = {row["code"]: row for row in records}
        self._catalog_complete = complete
        self._catalog_attempted = True
        self._update_actions()
        self._controls()

    def _check_catalog(self, *, force=False):
        if (self._closing or self.offline() or self.job is not None or self._catalog_job is not None
                or (not force and (self._catalog_attempted or not self.isVisible()))):
            return
        if not force and not any(re.fullmatch(r"US[A-Z0-9]{6}", row["code"]) for row in self._items):
            return
        self._catalog_attempted = True
        self._catalog_job = _ENCJob(self.store, "catalog-check", parent=self)
        self._catalog_job.result.connect(self._catalog_result)
        self._catalog_job.finished.connect(self._catalog_finished)
        self.status.setText("Checking NOAA revisions in the background...")
        self._controls()
        self._catalog_job.start()

    def _catalog_result(self, records, error):
        job = self._catalog_job
        if self._closing or job is None or job.cancel.is_set() or self.offline():
            return
        if error:
            self.status.setText(f"NOAA update check failed; charts were kept. {error}")
            self.status.setToolTip(self.status.text())
            return
        self._apply_catalog(records)
        updates = sum(self._action_details(row)[:2] == ("Renew", True) for row in self._items)
        missing = sum(self._action_details(row)[0] == "Download" for row in self._items)
        unavailable = sum(self._action_details(row)[0] == "Unavailable" for row in self._items)
        self.status.setText(f"NOAA check complete: {updates} update(s), {missing} missing, {unavailable} unavailable cell(s). Highlighted actions download changes; Reload is optional.")

    def _catalog_finished(self):
        self._catalog_job.deleteLater()
        self._catalog_job = None
        if not self._closing:
            self._controls()

    def _controls(self):
        idle = self.job is None
        if self.offline() and self._catalog_job is not None:
            self._catalog_job.cancel.set()
        selected = self._selected_codes
        visible = self.proxy.visible_codes()
        checked = len(selected & visible)
        with QSignalBlocker(self.select_all):
            self.select_all.setCheckState(Qt.Unchecked if not checked else Qt.Checked if checked == len(visible) else Qt.PartiallyChecked)
        self.select_all.setEnabled(idle and bool(visible))
        self.search.setEnabled(idle)
        self.import_button.setEnabled(idle)
        self.noaa_button.setEnabled(idle and not self.offline())
        self.refresh_catalog.setEnabled(idle and self._catalog_job is None and not self.offline())
        self.noaa_button.setToolTip("Disable Offline mode to download NOAA charts" if self.offline() else "Choose NOAA ENC cells to download")
        self.table.setEnabled(True)
        self.show_button.setEnabled(idle and bool(selected))
        self.delete.setEnabled(idle and bool(selected))
        local_only = bool(selected) and all(not re.fullmatch(r"US[A-Z0-9]{6}", code) for code in selected)
        self.renew.setEnabled(idle and bool(selected) and (local_only or not self.offline()))
        self.cancel_button.setEnabled(not idle)
        self.cell_model.set_context(not idle, self.offline())

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self.refresh()
        QTimer.singleShot(0, self._check_catalog)

    def _filter(self, text):
        self._changing_selection = True
        try:
            self.proxy.set_search(text)
            self._restore_selection()
        finally:
            self._changing_selection = False
        self._controls()

    def _select_visible(self, checked):
        visible = self.proxy.visible_codes()
        if checked:
            self._selected_codes.update(visible)
        else:
            self._selected_codes.difference_update(visible)
        self._restore_selection()
        self._controls()

    def _import(self):
        filenames, _ = QFileDialog.getOpenFileNames(self, "Import S57 cells or exchange sets", "", "S57 ENC (*.000 *.zip);;All files (*)")
        if filenames:
            self._start("import", filenames)

    def _import_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Import S57 folder including subfolders")
        if directory:
            self._start("import", [directory])

    def _show(self):
        rows = [row for row in self._selected_records() if len(row["bounds"]) == 4]
        if rows and self.show_chart:
            self._show_bounds = [min(r["bounds"][0] for r in rows), min(r["bounds"][1] for r in rows), max(r["bounds"][2] for r in rows), max(r["bounds"][3] for r in rows)]
            self._start("show", [row["code"] for row in rows])

    def _delete(self, codes=None):
        codes = codes if isinstance(codes, list) else [row["code"] for row in self._selected_records()]
        answer = ask_question(self, "Delete S57 charts", f"Delete {len(codes)} selected chart(s) from this computer?") if codes else False
        if answer is True or answer == QMessageBox.Yes:
            self._start("delete", codes)

    def _renew(self, rows=None, *, force=False):
        rows = rows if isinstance(rows, list) else self._selected_records()
        if not rows:
            return
        if all(not re.fullmatch(r"US[A-Z0-9]{6}", row["code"]) for row in rows):
            self.status.setText("Choose updated original local cells/exchange sets. Keep their numbered updates together.")
            files, _ = QFileDialog.getOpenFileNames(self, "Renew local S57 cells - choose updated original files", "",
                                                   "S57 ENC (*.000 *.zip);;All files (*)")
            if files:
                self._start("import", files)
            return
        if self.offline():
            self.status.setText("Disable Offline mode to renew NOAA cells. Local chart import still works offline.")
            return
        self._start("renew", rows, force=force) if force else self._start("renew", rows)

    def _cell_action(self, row, delete):
        if self.job is not None:
            return
        record = self.proxy.record(row)
        if record is None:
            return
        if delete:
            self._delete([record["code"]])
        elif self._action_details(record)[0] == "Unavailable":
            self.status.setText("This cell is unavailable from NOAA. Import updated original files/folder; the installed chart is kept.")
        elif self._action_details(record)[0] == "Reload":
            self._renew([record], force=True)
        else:
            self._renew([record])

    def _cell_progress(self, code, state, percentage):
        self._row_states[code] = (state, percentage)
        row = self._rows.get(code)
        if row is None:
            return
        if state in {"Ready", "Failed", "Skipped"}:
            try:
                records = self.store.records(code)
            except (OSError, sqlite3.Error):
                records = []
            if records:
                self.cell_model.update_record(records[0])
                self._pending.pop(code, None)
        self.cell_model.notify(row, 5, 6)

    def _enabled(self, code, enabled):
        if self.job is not None:
            return False
        try:
            self.store.set_enabled(code, enabled)
            self.cell_model.update_record(dict(self._items[self._rows[code]], enabled=enabled))
        except (OSError, sqlite3.Error) as error:
            self.status.setText(f"Cannot update the local S57 index: {error}")
            self.refresh()
            return False
        self.charts_changed.emit()
        return True

    def _start(self, operation, data=(), *, force=False):
        if self.job is not None or self._closing:
            return
        if operation.startswith("catalog") and self.offline():
            self.status.setText("Offline mode is active. Local S57 import remains available.")
            return
        self.store.directory.mkdir(parents=True, exist_ok=True)
        if self._catalog_job is not None:
            self._catalog_job.cancel.set()
        if operation in {"download", "renew"}:
            for record in data:
                code = record["code"]
                self._row_states[code] = ("Queued", 0)
                self.cell_model.errors.pop(code, None)
                self._pending[code] = dict(record, enabled=False, source="NOAA ENC", bounds=[], content_bytes=None)
            self.refresh()
        self._value, self._error = None, ""
        self.job = _ENCJob(self.store, operation, data, self, force=force, confirm_large=True)
        self.job.result.connect(self._result)
        self.job.progress.connect(self._progress)
        self.job.cell_progress.connect(self._cell_progress)
        self.job.large_download.connect(self._confirm_download)
        self.job.finished.connect(self._finished)
        self.status.setText("Loading NOAA catalog..." if operation.startswith("catalog") else "Enabling charts..." if operation == "show" else "Preparing charts...")
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self._controls()
        self.job.start()

    def _confirm_download(self, count, size):
        job = self.sender()
        if job is not self.job or self._closing or self.offline() or job.cancel.is_set():
            job.approve(False)
            return
        answer = ask_question(self, "Download chart collection",
                              f"Download {count:,} {'chart' if count == 1 else 'charts'} ({size / 1024**2:.0f} MB)?\n\n"
                              "The installed charts will need additional disk space. You can keep using the simulator "
                              "while they download and install.\n\n"
                              "If you cancel, all completed charts remain available.",
                              confirm_label="Download", cancel_label="Cancel")
        job.approve(answer is True or answer == QMessageBox.Yes)

    def _progress(self, done, total, text):
        if total:
            self.bar.setRange(0, 100)
            self.bar.setValue(min(100, round(done * 100 / total)))
        else:
            self.bar.setRange(0, 0)
        self.status.setText(text)

    def _result(self, value, error):
        self._value, self._error = value, error

    def _finished(self):
        operation = self.job.operation
        cell_errors = dict(self.job.cell_errors)
        current_codes = list(self.job.current_codes)
        catalog_records = self.job.catalog_records
        self.job.deleteLater()
        self.job = None
        for code in current_codes:
            self._pending.pop(code, None)
            self._row_states[code] = ("Up to date", 100)
        self.bar.setVisible(False)
        if self._closing:
            return
        if operation == "delete" and not self._error:
            for code in self._value:
                self._pending.pop(code, None)
                self._row_states.pop(code, None)
                self.cell_model.errors.pop(code, None)
        if self._error:
            for code, (state, percentage) in tuple(self._row_states.items()):
                if state in {"Queued", "Downloading", "Downloaded", "Importing"}:
                    row = self._rows.get(code)
                    installed = row is not None and len(self._items[row]["bounds"]) == 4
                    self._row_states[code] = ("Ready", 100) if installed else ("Not downloaded", percentage)
                    if installed:
                        self._pending.pop(code, None)
        self.refresh()
        if catalog_records is not None:
            # Downloads use a catalogue subset; retain previously checked unrelated cells.
            if operation == "download" and self._catalog is not None:
                catalog_records = list({**self._catalog, **{row['code']: row for row in catalog_records}}.values())
            self._apply_catalog(catalog_records, complete=operation != "download" or self._catalog_complete)
        for code, detail in cell_errors.items():
            row = self._rows.get(code)
            if row is not None:
                self.cell_model.errors[code] = detail
                self.cell_model.notify(row, 5, 5, [Qt.ToolTipRole])
        if self._error:
            self.status.setText(self._error)
            self.status.setToolTip(self._error)
            if self._value:
                self.charts_changed.emit()
            return
        if operation.startswith("catalog"):
            chooser = _NOAAChooser(self._value, {r["code"]: r for r in self._items}, self)
            if chooser.exec() == QDialog.Accepted and not self.offline():
                records = [self._value[index] for index in sorted(chooser.selected)]
                self._start("download", records)
            else:
                self.status.setText("NOAA selection cancelled")
            chooser.deleteLater()
        else:
            verb = "Deleted" if operation == "delete" else "Renewed" if operation == "renew" else "Enabled" if operation == "show" else "Imported"
            self.status.setText(f"{verb} {len(self._value)} chart(s)")
            if current_codes:
                self.status.setText(f"{verb} {len(self._value)} chart(s); {len(current_codes)} already up to date")
            if self._value:
                self.charts_changed.emit()
            if operation == "show":
                self.show_chart(self._show_bounds)

    def _cancel(self):
        if self.job is not None:
            self.job.cancel.set()
            self.status.setText("Cancelling; completed cells are kept, unfinished changes are rolled back...")

    def shutdown(self):
        self._closing = True
        if self.job is not None:
            self.job.cancel.set()
            self.job.wait()
        if self._catalog_job is not None:
            self._catalog_job.cancel.set()
            self._catalog_job.wait()
