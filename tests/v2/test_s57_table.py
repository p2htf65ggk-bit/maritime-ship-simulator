# SPDX-License-Identifier: MPL-2.0
import sqlite3

from PySide6.QtCore import QPersistentModelIndex, Qt, qInstallMessageHandler
from PySide6.QtTest import QAbstractItemModelTester, QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTableView, QTableWidget
import pytest

from maritime_simulator.ui import s57_panel
from maritime_simulator.ui.s57_table import ChartFilterProxy, NOAATableModel, S57TableModel


def record(index=0, **values):
    return dict(dict(code=f"US{index:06d}", name=f"Area {index}", bounds=[0, 0, 1, 1],
                     enabled=True, edition="1", update="0", date="20261010", size=1024,
                     content_bytes=2048, feature_count=10, scale=50000, source="NOAA ENC"), **values)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(tmp_path, monkeypatch, app):
    widget = s57_panel.S57Panel(tmp_path, lambda: True)
    rows = [record(index) for index in range(200)]
    monkeypatch.setattr(widget.store, "records", lambda code=None: list(rows) if code is None else [row for row in rows if row["code"] == code])
    widget.refresh()
    yield widget, rows
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_source_and_proxy_obey_qt_model_contract(app):
    messages = []
    previous = qInstallMessageHandler(lambda _type, _context, text: messages.append(text))
    try:
        models = [S57TableModel({}, lambda _row: ("Check", False, ""), lambda *_args: True),
                  NOAATableModel([record(index) for index in range(10)], {})]
        testers = []
        for model in models:
            proxy = ChartFilterProxy()
            proxy.setSourceModel(model)
            for tested in (model, proxy):
                testers.append(QAbstractItemModelTester(tested, QAbstractItemModelTester.FailureReportingMode.Warning))
            model.set_records([record(index) for index in range(10)])
            proxy.set_search("area 3")
            assert proxy.rowCount() == 1
            model.update_record(record(3, name="Harbour"))
            assert proxy.rowCount() == 0
            proxy.set_search("")
            model.set_records([record(index) for index in range(11)])
            model.set_records([])
            assert proxy.rowCount() == 0
        assert not any("FAIL!" in message for message in messages), messages
    finally:
        qInstallMessageHandler(previous)


def test_large_catalogues_use_metadata_without_per_cell_widgets(app, tmp_path):
    calls = []
    model = S57TableModel({}, lambda row: (calls.append(row["code"]) or "Check", False, ""), lambda *_args: True)
    rows = [record(index) for index in range(10000)]
    model.set_records(rows)
    assert model.rowCount() == 10000 and model.columnCount() == 12
    assert not calls, "Action formatting must be lazy, not performed for every catalogue row"
    for row in range(1000):
        assert model.index(row, 7).data() == "Check"
    assert len(model._actions) == 512
    assert model.index(999, 7).data() == "Check" and len(calls) == 1000
    panel = s57_panel.S57Panel(tmp_path, lambda: True, defer_load=True)
    panel.cell_model.set_records(rows)
    chooser = s57_panel._NOAAChooser(rows, {}, None)
    try:
        assert isinstance(panel.table, QTableView) and not isinstance(panel.table, QTableWidget)
        assert isinstance(chooser.table, QTableView) and not isinstance(chooser.table, QTableWidget)
        assert not panel.table.indexWidget(panel.proxy.index(0, 0))
        chooser.select_all.click()
        assert len(chooser.selected) == 10000 and chooser.accept_button.isEnabled()
        chooser.search.setText("area 9999")
        chooser.select_all.click()
        assert len(chooser.selected) == 9999 and 9999 not in chooser.selected
    finally:
        panel.shutdown()
        panel.deleteLater()
        chooser.deleteLater()
        app.processEvents()


def test_noaa_single_and_bulk_checks_emit_one_bounded_update(app):
    model = NOAATableModel([record(index) for index in range(10000)], {})
    updates, selections = [], []
    model.dataChanged.connect(lambda first, last, roles: updates.append((first.row(), last.row(), roles)))
    model.selection_changed.connect(lambda: selections.append(True))
    assert model.setData(model.index(9876, 0), Qt.Checked, Qt.CheckStateRole)
    assert updates == [(9876, 9876, [Qt.CheckStateRole])] and selections == [True]
    updates.clear()
    model.select_codes({row["code"] for row in model.records}, True)
    assert len(updates) == 1 and len(selections) == 2 and len(model.selected_codes) == 10000
    model.select_codes({row["code"] for row in model.records}, True)
    assert len(updates) == 1 and len(selections) == 2
    model.select_codes({"not a cell"}, True)
    assert len(model.selected_codes) == 10000


def test_bulk_metadata_updates_preserve_indices_and_emit_one_change(app):
    model = S57TableModel({}, lambda _row: ("Check", False, ""), lambda *_args: True)
    rows = [record(index, enabled=False) for index in range(10000)]
    model.set_records(rows)
    index = QPersistentModelIndex(model.index(5000, 0))
    changes, resets = [], []
    model.dataChanged.connect(lambda first, last, _roles: changes.append((first.row(), last.row())))
    model.modelReset.connect(lambda: resets.append(True))
    model.set_records([dict(row, enabled=True) for row in rows])
    assert changes == [(0, 9999)] and not resets
    assert index.isValid() and index.data(Qt.CheckStateRole) == Qt.Checked


def test_filtered_sorted_actions_and_use_follow_chart_identity(panel, monkeypatch):
    widget, rows = panel
    writes, actions = [], []
    monkeypatch.setattr(widget.store, "set_enabled", lambda *args: writes.append(args))
    monkeypatch.setattr(widget, "_delete", lambda codes: actions.append(("delete", codes)))
    monkeypatch.setattr(widget, "_renew", lambda records, **_kwargs: actions.append(("renew", [row["code"] for row in records])))
    widget.search.setText("area 19")
    widget.proxy.sort(1, Qt.DescendingOrder)
    row = widget.proxy.record(0)
    assert row["code"] != rows[0]["code"]
    widget._cell_action(0, True)
    widget._cell_action(0, False)
    assert actions == [("delete", [row["code"]]), ("renew", [row["code"]])]
    assert widget.proxy.setData(widget.proxy.index(0, 0), Qt.Unchecked, Qt.CheckStateRole)
    assert writes == [(row["code"], False)]
    assert not widget.cell_model.records[widget.cell_model.rows[row["code"]]]["enabled"]


def test_hidden_selection_survives_filter_and_sort_and_bulk_actions(panel, monkeypatch):
    widget, rows = panel
    widget.table.selectRow(3)
    widget.search.setText("area 19")
    widget._select_visible(True)
    assert len(widget._selected_codes) == 12 and rows[3]["code"] in widget._selected_codes
    widget.proxy.sort(1, Qt.DescendingOrder)
    widget._select_visible(False)
    assert widget._selected_codes == {rows[3]["code"]}
    calls = []
    monkeypatch.setattr(s57_panel, "ask_question", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(widget, "_start", lambda operation, codes: calls.append((operation, codes)))
    widget._delete()
    assert calls == [("delete", [rows[3]["code"]])]
    widget.search.clear()
    selected = widget.table.selectionModel().selectedRows()
    assert [widget.proxy.record(index.row())["code"] for index in selected] == [rows[3]["code"]]


def test_progress_only_updates_matching_row_without_reset_or_scroll_jump(panel, app):
    widget, rows = panel
    widget.resize(1000, 500)
    widget.show()
    app.processEvents()
    widget.table.selectRow(150)
    widget.table.scrollTo(widget.proxy.index(150, 0))
    app.processEvents()
    scroll = widget.table.verticalScrollBar().value()
    index = QPersistentModelIndex(widget.proxy.index(150, 1))
    updates, resets = [], []
    widget.cell_model.dataChanged.connect(lambda first, last, _roles: updates.append((first.row(), first.column(), last.column())))
    widget.cell_model.modelReset.connect(lambda: resets.append(True))
    for step in range(500):
        widget._cell_progress(rows[150]["code"], "Downloading", step % 100)
    app.processEvents()
    assert len(updates) == 500 and set(updates) == {(150, 5, 6)} and not resets
    assert index.isValid() and index.data() == rows[150]["code"]
    assert widget._selected_codes == {rows[150]["code"]}
    assert widget.table.verticalScrollBar().value() == scroll
    widget.refresh()
    app.processEvents()
    assert index.isValid() and widget.table.verticalScrollBar().value() == scroll


def test_refresh_preserves_selection_scroll_and_removes_deleted_ids(panel, app):
    widget, rows = panel
    widget.resize(1000, 500)
    widget.show()
    app.processEvents()
    widget.table.selectRow(150)
    widget.table.scrollTo(widget.proxy.index(150, 0))
    app.processEvents()
    scroll = widget.table.verticalScrollBar().value()
    selected = rows[150]["code"]
    rows.insert(0, record(10001))
    widget.refresh()
    app.processEvents()
    assert widget._selected_codes == {selected}
    assert [widget.proxy.record(index.row())["code"] for index in widget.table.selectionModel().selectedRows()] == [selected]
    assert widget.table.verticalScrollBar().value() == scroll
    rows[:] = [row for row in rows if row["code"] != selected]
    widget.refresh()
    assert not widget._selected_codes and not widget.delete.isEnabled()


def test_use_write_failure_keeps_checkbox_unchanged(panel, monkeypatch):
    widget, rows = panel
    def failed(*_args):
        raise sqlite3.OperationalError("read only")
    monkeypatch.setattr(widget.store, "set_enabled", failed)
    assert not widget.proxy.setData(widget.proxy.index(0, 0), Qt.Unchecked, Qt.CheckStateRole)
    assert widget.proxy.index(0, 0).data(Qt.CheckStateRole) == Qt.Checked
    assert "read only" in widget.status.text() and rows[0]["enabled"]


def test_checkboxes_and_actions_accept_mouse_clicks(panel, app, monkeypatch):
    widget, rows = panel
    widget.resize(1400, 550)
    widget.show()
    app.processEvents()
    actions = []
    monkeypatch.setattr(widget.store, "set_enabled", lambda *_args: None)
    monkeypatch.setattr(widget, "_delete", actions.append)
    index = widget.proxy.index(0, 0)
    option = QStyleOptionViewItem()
    option.initFrom(widget.table)
    widget.table.itemDelegate().initStyleOption(option, index)
    option.rect = widget.table.visualRect(index)
    checkbox = widget.table.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, option, widget.table)
    QTest.mouseClick(widget.table.viewport(), Qt.LeftButton, pos=checkbox.center())
    assert widget.proxy.index(0, 0).data(Qt.CheckStateRole) == Qt.Unchecked
    delete = widget.table.visualRect(widget.proxy.index(0, 8))
    QTest.mouseClick(widget.table.viewport(), Qt.LeftButton, pos=delete.center())
    assert actions == [[rows[0]["code"]]]
