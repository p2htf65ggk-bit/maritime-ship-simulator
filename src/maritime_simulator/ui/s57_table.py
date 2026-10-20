# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from collections import OrderedDict
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal

from maritime_simulator.visual.noaa import cell_is_current
from .offline_widgets import ACTION_HIGHLIGHT_ROLE


def chart_date(value):
    text = str(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 and text.isdigit() else text


def chart_size(value):
    if value is None:
        return "--"
    return f"{value / 1024**2:.1f} MB" if value >= 1024**2 else f"{value / 1024:.1f} KB"


class ChartTableModel(QAbstractTableModel):
    headers = ()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = []
        self.rows = {}
        self.search_text = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.headers):
            return self.headers[section]
        return None

    def set_records(self, records):
        if records == self.records:
            return False
        same_order = [row["code"] for row in records] == [row["code"] for row in self.records]
        changed = [index for index, (old, new) in enumerate(zip(self.records, records)) if old != new] if same_order else []
        if not same_order:
            self.beginResetModel()
        self.records = records
        self.rows = {row["code"]: index for index, row in enumerate(records)}
        self.search_text = [(row["code"] + " " + row["name"]).casefold() for row in records]
        self.invalidate_cache()
        if not same_order:
            self.endResetModel()
        elif changed:
            self.dataChanged.emit(self.index(changed[0], 0), self.index(changed[-1], self.columnCount()-1), [])
        return not same_order

    def update_record(self, record):
        row = self.rows.get(record["code"])
        if row is None:
            return
        self.records[row] = record
        self.search_text[row] = (record["code"] + " " + record["name"]).casefold()
        self.invalidate_cache(record["code"])
        self.notify(row)

    def invalidate_cache(self, code=None):
        pass

    def notify(self, row, first=0, last=None, roles=()):
        if 0 <= row < len(self.records):
            self.dataChanged.emit(self.index(row, first), self.index(row, self.columnCount()-1 if last is None else last), list(roles))


class ChartFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""

    def set_search(self, text):
        query = text.casefold().strip()
        if self.query != query:
            self.beginFilterChange()
            self.query = query
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):
        return not self.query or self.query in self.sourceModel().search_text[row]

    def visible_codes(self):
        source = self.sourceModel()
        return {record["code"] for row, record in enumerate(source.records)
                if not self.query or self.query in source.search_text[row]}

    def record(self, row):
        index = self.mapToSource(self.index(row, 0))
        return self.sourceModel().records[index.row()] if index.isValid() else None


class S57TableModel(ChartTableModel):
    headers = ("Use", "Cell", "Area", "Scale", "Size", "Status", "Progress", "Action", "", "Edition / Update", "Source", "Issued")

    def __init__(self, states, action_details, set_use, parent=None):
        super().__init__(parent)
        self.states = states
        self.action_details = action_details
        self.set_use = set_use
        self.busy = False
        self.offline = False
        self.errors = {}
        self._actions = OrderedDict()

    def invalidate_cache(self, code=None):
        if code is None:
            self._actions.clear()
        else:
            self._actions.pop(code, None)

    def action(self, record):
        code = record["code"]
        if code not in self._actions:
            self._actions[code] = self.action_details(record)
            if len(self._actions) > 512:
                self._actions.popitem(last=False)
        self._actions.move_to_end(code)
        return self._actions[code]

    def state(self, record):
        installed = len(record.get("bounds") or []) == 4 and record.get("feature_count") != 0
        return self.states.get(record["code"], ("Ready", 100) if installed else ("Not downloaded", 0))

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, column = self.records[index.row()], index.column()
        if role == Qt.CheckStateRole and column == 0 and len(row.get("bounds") or []) == 4:
            return Qt.Checked if row["enabled"] else Qt.Unchecked
        if role == Qt.UserRole and column == 6:
            return self.state(row)[1]
        if role == ACTION_HIGHLIGHT_ROLE and column == 7:
            return self.action(row)[1]
        if role == Qt.ToolTipRole:
            if column == 0:
                return "This cell has not been imported yet" if len(row.get("bounds") or []) != 4 else ""
            if column == 4:
                return "Stored geometry and attributes, excluding shared SQLite/index overhead. Reimport old cells to calculate their size."
            if column == 5 and row["code"] in self.errors:
                return self.errors[row["code"]]
            if column == 7:
                return self.action(row)[2]
            if column == 8:
                return "Delete this cell (confirmation required)"
        if role not in {Qt.DisplayRole, Qt.ToolTipRole}:
            return None
        if column in (0, 8):
            return ""
        if column == 1:
            return row["code"]
        if column == 2:
            return row["name"]
        if column == 3:
            return f"1:{row['scale']:,}"
        if column == 4:
            return chart_size(row.get("content_bytes"))
        if column == 5:
            return self.state(row)[0]
        if column == 6:
            return f"{self.state(row)[1]}%"
        if column == 7:
            return self.action(row)[0]
        if column == 9:
            return f"{row['edition']} / {row['update']}"
        if column == 10:
            return row["source"]
        if column == 11:
            return chart_date(row["date"])
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        row, column = self.records[index.row()], index.column()
        flags = Qt.ItemIsSelectable
        if column == 7:
            local = not re.fullmatch(r"US[A-Z0-9]{6}", row["code"])
            if not self.busy and self.action(row)[0] != "Unavailable" and (local or not self.offline):
                flags |= Qt.ItemIsEnabled
        elif column == 8:
            if not self.busy:
                flags |= Qt.ItemIsEnabled
        else:
            flags |= Qt.ItemIsEnabled
        if column == 0 and not self.busy and len(row.get("bounds") or []) == 4:
            flags |= Qt.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.EditRole):
        if (role == Qt.CheckStateRole and index.isValid() and index.column() == 0
                and self.flags(index) & Qt.ItemIsUserCheckable):
            return self.set_use(self.records[index.row()]["code"], value == Qt.Checked or value == Qt.Checked.value)
        return False

    def set_context(self, busy, offline):
        if (self.busy, self.offline) != (busy, offline):
            self.busy, self.offline = busy, offline
            if self.records:
                self.dataChanged.emit(self.index(0, 0), self.index(len(self.records)-1, 8), [])


class NOAATableModel(ChartTableModel):
    headers = ("", "Cell", "Area", "Scale", "Updated", "Download", "Status")
    selection_changed = Signal()

    def __init__(self, records, installed, parent=None):
        super().__init__(parent)
        self.selected_codes = set()
        self.current_codes = {row["code"] for row in records if cell_is_current(row, installed.get(row["code"]))}
        self.installed = installed
        self.set_records(records)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, column = self.records[index.row()], index.column()
        if role == Qt.CheckStateRole and column == 0:
            return Qt.Checked if row["code"] in self.selected_codes else Qt.Unchecked
        if role not in {Qt.DisplayRole, Qt.ToolTipRole}:
            return None
        if column == 0:
            return ""
        if column == 1:
            text = row["code"]
        elif column == 2:
            text = row["name"]
        elif column == 3:
            text = f"1:{row['scale']:,}"
        elif column == 4:
            text = chart_date(row["date"])
        elif column == 5:
            text = f"{row['size']/1024:.0f} KB"
        else:
            text = "Up to date" if row["code"] in self.current_codes else "Update needed" if row["code"] in self.installed else "Missing"
        return text if role == Qt.DisplayRole else text + "\nOnly missing or outdated cells are downloaded. Renew checks the latest catalogue and keeps current cells."

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | (Qt.ItemIsUserCheckable if index.column() == 0 else Qt.NoItemFlags)

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.CheckStateRole or not index.isValid() or index.column() != 0:
            return False
        self.select_codes({self.records[index.row()]["code"]}, value == Qt.Checked or value == Qt.Checked.value)
        return True

    def select_codes(self, codes, checked):
        codes = set(codes) & self.rows.keys()
        changed = codes - self.selected_codes if checked else codes & self.selected_codes
        if not changed:
            return
        if checked:
            self.selected_codes.update(codes)
        else:
            self.selected_codes.difference_update(codes)
        rows = [self.rows[code] for code in changed]
        self.dataChanged.emit(self.index(min(rows), 0), self.index(max(rows), 0), [Qt.CheckStateRole])
        self.selection_changed.emit()
