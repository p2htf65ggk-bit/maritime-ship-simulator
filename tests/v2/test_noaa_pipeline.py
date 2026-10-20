# SPDX-License-Identifier: MPL-2.0
import io
from pathlib import Path
from threading import Event, Lock
import time

from PySide6.QtCore import QPersistentModelIndex, Qt
from PySide6.QtWidgets import QApplication
import pytest

from maritime_simulator.visual import noaa, s57
from maritime_simulator.ui import s57_panel


def record(index=0, **values):
    code = f"US{index:06d}"
    return {**dict(code=code, name=code, url=f"https://www.charts.noaa.gov/ENCs/{code}.zip",
                edition="2", update="3", date="20261009", size=100,
                scale=50000, bounds=[0, 0, 1, 1], feature_count=10), **values}


@pytest.mark.parametrize("installed, expected", [
    ({}, True), ({"edition": "3", "date": "20240101"}, True),
    ({"update": "4"}, True), ({"update": "2"}, False),
    ({"edition": "1", "update": "99"}, False), ({"date": "20261008"}, False),
    ({"date": ""}, False), ({"update": ""}, False),
    ({"edition": "unknown"}, False), ({"bounds": []}, False),
    ({"feature_count": 0}, False), ({"update": "03"}, True),
    ({"bounds": None}, False), ({"date": "20269999"}, False),
    ({"date": "2026-10-09"}, True), ({"date": "2026-10-08"}, False),
])
def test_revision_comparison_is_numeric_conservative_and_never_downgrades(installed, expected):
    catalog = record()
    assert noaa.cell_is_current(catalog, {**catalog, **installed}) is expected
    assert not noaa.cell_is_current(catalog, None)


def test_real_noaa_catalogue_iso_dates_match_compact_s57_dsid_dates():
    catalog = record(date="2025-08-18")
    assert noaa.cell_is_current(catalog, record(date="20250818"))
    assert not noaa.cell_is_current(catalog, record(date="20250817"))


class Client:
    def __init__(self):
        self.closed = False
    def close(self):
        self.closed = True


def test_downloads_overlap_each_other_and_import_while_staging_stays_bounded(tmp_path):
    rows = [record(index) for index in range(30)]
    started = Event()
    importing = Event()
    overlap = Event()
    lock = Lock()
    active = peak = staged = 0
    clients = []
    imports = []
    percentages = []
    class Store:
        def records(self):
            return []
        def import_files(self, files, **_kwargs):
            importing.set()
            assert started.wait(3)
            time.sleep(0.015)
            imports.append(files[0].stem)
            importing.clear()
            return [files[0].stem]
    def client_factory():
        client = Client()
        clients.append(client)
        return client
    def fetch(_url, target, **kwargs):
        nonlocal active, peak, staged
        with lock:
            active += 1
            peak = max(peak, active)
            if active == noaa.DOWNLOAD_WORKERS:
                started.set()
        assert started.wait(3), "All download workers must run concurrently"
        time.sleep(0.02)
        if importing.is_set():
            overlap.set()
        target.write_bytes(b"archive")
        with lock:
            staged = max(staged, len(list(tmp_path.glob("*.zip"))))
            active -= 1
        kwargs["progress"](1, 1, "Download")
    result = noaa.sync_cells(Store(), rows, tmp_path, cancel=Event(), download_fn=fetch,
                             client_factory=client_factory, cell_progress=lambda *_args: None,
                             progress=lambda done, total, _text: percentages.append(done / total))
    assert result.imported == [row["code"] for row in rows] and not result.errors
    assert peak == noaa.DOWNLOAD_WORKERS == 5 and overlap.is_set()
    assert 1 <= staged <= noaa.PREFETCH_CELLS == 10
    assert len(imports) == len(rows) and not list(tmp_path.glob("*.zip"))
    assert len(clients) == noaa.DOWNLOAD_WORKERS and all(client.closed for client in clients)
    assert percentages == sorted(percentages) and percentages[-1] == 1


def test_select_all_downloads_only_missing_or_outdated_cells_and_force_renew_is_available(tmp_path):
    rows = [record(index) for index in range(5)]
    local_rows = [{**rows[0], "source": "Local import"}, {**rows[1], "update": "2"},
                  {**rows[2], "edition": "3"}, {**rows[3], "date": ""}]
    fetched = []
    class Store:
        def records(self):
            return local_rows
        def import_files(self, files, **_kwargs):
            return [files[0].stem]
    def fetch(_url, target, **_kwargs):
        fetched.append(target.stem)
        target.write_bytes(b"archive")
    kwargs = dict(cancel=Event(), progress=lambda *_args: None, cell_progress=lambda *_args: None,
                  download_fn=fetch, client_factory=Client)
    result = noaa.sync_cells(Store(), rows, tmp_path, **kwargs)
    assert result.current == [rows[0]["code"], rows[2]["code"]]
    assert set(fetched) == {rows[index]["code"] for index in (1, 3, 4)}
    assert result.imported == [rows[index]["code"] for index in (1, 3, 4)]
    fetched.clear()
    forced = noaa.sync_cells(Store(), [{**rows[0], "bounds": []}], tmp_path, force=True, **kwargs)
    assert fetched == forced.imported == [rows[0]["code"]]


def test_up_to_date_catalogue_creates_no_download_workers_or_imports(tmp_path):
    rows = [record(index) for index in range(6808)]
    class Store:
        def records(self):
            return rows
        def import_files(self, *_args, **_kwargs):
            pytest.fail("Current cells must not be imported again")
    def unexpected(*_args, **_kwargs):
        pytest.fail("Current cells must not create clients or downloads")
    result = noaa.sync_cells(Store(), rows, tmp_path, cancel=Event(), progress=lambda *_args: None,
                             cell_progress=unexpected, download_fn=unexpected, client_factory=unexpected)
    assert len(result.current) == 6808 and not result.imported and not result.errors


def test_explicit_reload_does_not_downgrade_newer_local_revision(tmp_path):
    class Store:
        def records(self):
            return [record(update="4")]
    result = noaa.sync_cells(Store(), [record()], tmp_path, cancel=Event(), force=True,
                             progress=lambda *_args: None, cell_progress=lambda *_args: None,
                             client_factory=lambda: pytest.fail("Reload must not downgrade"))
    assert result.current == [record()["code"]] and not result.imported


def test_noaa_chooser_shows_current_status_and_estimates_only_required_downloads():
    app = QApplication.instance() or QApplication([])
    rows = [record(index) for index in range(3)]
    chooser = s57_panel._NOAAChooser(rows, {rows[0]["code"]: rows[0]}, None)
    try:
        chooser.select_all.click()
        assert chooser.selected == {0, 1, 2}
        assert chooser.download_records == rows[1:]
        assert "2 to download" in chooser.count.text() and "1 up to date" in chooser.count.text()
        assert chooser.table.model().index(0, 6).data() == "Up to date"
        assert chooser.table.model().index(1, 6).data() == "Missing"
    finally:
        chooser.close()
        chooser.deleteLater()
        app.processEvents()


class Response(io.BytesIO):
    def __init__(self, body=b"archive", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.reason = "Test"
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}
    def getheader(self, name, default=None):
        return self.headers.get(name, default)


def test_https_connections_are_reused_and_closed(tmp_path, monkeypatch):
    connections = []
    class Connection:
        def __init__(self, host, **kwargs):
            self.host = host
            self.requests = []
            self.closed = False
            connections.append(self)
        def request(self, method, path, headers):
            self.requests.append(path)
        def getresponse(self):
            return Response()
        def close(self):
            self.closed = True
    monkeypatch.setattr(noaa.request, "getproxies", lambda: {})
    monkeypatch.setattr(noaa.http.client, "HTTPSConnection", Connection)
    client = noaa.NOAADownloadClient()
    for index in range(3):
        client.download(record(index)["url"], tmp_path / f"{index}.zip")
    assert len(connections) == 1 and len(connections[0].requests) == 3
    client.close()
    assert connections[0].closed


def test_server_closed_connection_is_retried_once(tmp_path, monkeypatch):
    connections = []
    class Connection:
        def __init__(self, *_args, **_kwargs):
            connections.append(self)
            self.closed = False
        def request(self, *_args, **_kwargs):
            if len(connections) == 1:
                raise noaa.http.client.RemoteDisconnected("Keep-alive closed")
        def getresponse(self):
            return Response()
        def close(self):
            self.closed = True
    monkeypatch.setattr(noaa.request, "getproxies", lambda: {})
    monkeypatch.setattr(noaa.http.client, "HTTPSConnection", Connection)
    client = noaa.NOAADownloadClient()
    try:
        target = tmp_path / "cell.zip"
        client.download(record()["url"], target)
        assert target.read_bytes() == b"archive" and len(connections) == 2
        assert connections[0].closed
    finally:
        client.close()
    assert all(connection.closed for connection in connections)


def test_configured_https_proxy_keeps_existing_urllib_download_handling(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(noaa.request, "getproxies", lambda: {"https": "http://proxy.example:8080"})
    monkeypatch.setattr(noaa, "download", lambda *args, **kwargs: calls.append((args, kwargs)))
    client = noaa.NOAADownloadClient()
    cancel = Event()
    target = tmp_path / "cell.zip"
    client.download(record()["url"], target, cancel=cancel)
    assert len(calls) == 1 and calls[0][0] == (record()["url"], target)
    assert calls[0][1]["cancel"] is cancel and not client.connections


@pytest.mark.parametrize("reply, message", [
    (lambda: Response(b"short", headers={"Content-Length": "50"}), "Incomplete"),
    (lambda: Response(headers={"Content-Length": str(s57.MAX_DOWNLOAD + 1)}), "size limit"),
    (lambda: Response(status=302, headers={"Location": "https://evil.test/secret"}), "official NOAA"),
    (lambda: Response(status=403), "403"),
])
def test_invalid_downloads_preserve_security_checks_and_remove_partial_files(tmp_path, monkeypatch, reply, message):
    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return reply()
        def close(self):
            pass
    monkeypatch.setattr(noaa.request, "getproxies", lambda: {})
    monkeypatch.setattr(noaa.http.client, "HTTPSConnection", Connection)
    target = tmp_path / "partial.zip"
    with pytest.raises(Exception, match=message):
        noaa.NOAADownloadClient().download(record()["url"], target)
    assert not target.exists()


def test_low_disk_space_failure_is_not_misreported_as_user_cancellation(tmp_path, monkeypatch):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(noaa.shutil, "disk_usage", lambda *_args: usage(1000, 1000, 0))
    class Store:
        def records(self):
            return []
    cancel = Event()
    result = noaa.sync_cells(Store(), [record()], tmp_path, cancel=cancel,
                             progress=lambda *_args: None, cell_progress=lambda *_args: None)
    assert not result.cancelled and not cancel.is_set()
    assert "disk space" in result.errors["batch"]


@pytest.mark.parametrize("free, size", [(1024**3, 1024), (noaa.DISK_RESERVE + 2 * 1024**2, 0)])
def test_noaa_disk_budget_uses_catalogue_size_and_reduces_prefetch_when_needed(tmp_path, monkeypatch, free, size):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    rows = [record(index, size=size) for index in range(10)]
    monkeypatch.setattr(noaa.shutil, "disk_usage", lambda *_args: usage(2 * 1024**3, 0, free))
    class Store:
        def records(self):
            return []
        def import_files(self, files, **_kwargs):
            return [files[0].stem]
    def fetch(_url, target, **kwargs):
        target.write_bytes(b"x" * 1024)
        kwargs["progress"](1024, 1024, "Downloading")
    if size == 0:
        # Unknown ZIPs must retain a conservative reservation; one can fit at a time.
        free = noaa.DISK_RESERVE + noaa.MAX_DOWNLOAD + 1024
    result = noaa.sync_cells(Store(), rows, tmp_path, cancel=Event(), download_fn=fetch,
                            client_factory=Client, progress=lambda *_: None, cell_progress=lambda *_: None)
    assert not result.errors and result.imported == [row["code"] for row in rows]
    assert not list(tmp_path.glob("*.zip"))


def test_noaa_underreported_size_stops_cell_before_disk_reserve_is_consumed(tmp_path, monkeypatch):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    free = noaa.DISK_RESERVE + 2 * 1024**2
    monkeypatch.setattr(noaa.shutil, "disk_usage", lambda *_args: usage(2 * 1024**3, 0, free))
    class Store:
        def records(self):
            return []
        def import_files(self, *_args, **_kwargs):
            pytest.fail("A partial download must not be imported")
    def fetch(_url, target, **kwargs):
        nonlocal free
        target.write_bytes(b"partial")
        free = noaa.DISK_RESERVE - 1
        kwargs["progress"](1024, 0, "Downloading")
    row = record(size=1)
    result = noaa.sync_cells(Store(), [row], tmp_path, cancel=Event(), download_fn=fetch,
                            client_factory=Client, progress=lambda *_: None, cell_progress=lambda *_: None)
    assert "disk space" in result.errors[row["code"]] and not result.cancelled
    assert not list(tmp_path.glob("*.zip"))


def test_completed_real_import_remains_installed_after_cancellation_and_is_skipped_next_time(tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parents[1] / "fixtures/s57/US4NJ1DG.zip"
    store = s57.S57Store(tmp_path / "s57")
    row = {**record(), "code": "US4NJ1DG", "url": "https://www.charts.noaa.gov/ENCs/US4NJ1DG.zip"}
    other = record(1)
    def fetch(_url, target, **kwargs):
        if target.stem != "US4NJ1DG":
            deadline = time.monotonic() + 5
            while not kwargs["cancel"].is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            s57.check_cancel(kwargs["cancel"])
        target.write_bytes(fixture.read_bytes())
    monkeypatch.setattr(s57_panel, "download", fetch)
    job = s57_panel._ENCJob(store, "download", [row, other])
    job.cell_progress.connect(lambda _code, state, _percentage: job.cancel.set() if state == "Ready" else None)
    results = []
    job.result.connect(lambda *args: results.append(args))
    job.run()
    assert results[0][0] == ["US4NJ1DG"] and "Cancelled" in results[0][1], results
    installed = store.records("US4NJ1DG")[0]
    assert installed["feature_count"] == 466
    row.update({key: installed[key] for key in ("edition", "update", "date")})
    next_job = s57_panel._ENCJob(store, "download", [row])
    monkeypatch.setattr(s57_panel, "download", lambda *_args, **_kwargs: pytest.fail("No second download"))
    next_job.run()
    assert next_job.current_codes == ["US4NJ1DG"] and store.records()[0] == installed


def test_chart_table_scrolls_during_work_without_allowing_mutations(tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    rows = [record(index, enabled=True, source="NOAA ENC", content_bytes=1000) for index in range(200)]
    monkeypatch.setattr(s57_panel, "load_catalog", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(panel.store, "records", lambda code=None: rows if code is None else [rows[int(code[2:])]])
    try:
        panel.refresh()
        panel.resize(950, 450)
        panel.show()
        app.processEvents()
        panel.job = object()
        panel._controls()
        assert panel.table.isEnabled() and panel.table.verticalScrollBar().isEnabled()
        assert not panel.table.model().index(0, 0).flags() & Qt.ItemIsUserCheckable
        assert not panel.table.model().index(0, 7).flags() & Qt.ItemIsEnabled
        assert not panel.table.model().index(0, 8).flags() & Qt.ItemIsEnabled
        assert not panel.renew.isEnabled() and not panel.delete.isEnabled()
        panel.table.setFocus()
        QTest.keyClick(panel.table, Qt.Key_PageDown)
        app.processEvents()
        scroll = panel.table.verticalScrollBar().value()
        assert scroll > 0
        panel._cell_progress(rows[0]["code"], "Downloading", 35)
        app.processEvents()
        assert panel.table.verticalScrollBar().value() == scroll
        panel.job = None
        panel._controls()
        assert panel.table.model().index(0, 0).flags() & Qt.ItemIsUserCheckable
        assert panel.table.model().index(0, 7).flags() & Qt.ItemIsEnabled
        assert panel.table.model().index(0, 8).flags() & Qt.ItemIsEnabled
    finally:
        panel.job = None
        panel.shutdown()
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_current_bulk_job_updates_ui_once_without_invalidating_charts(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    rows = [record(index, enabled=True, source="NOAA ENC", content_bytes=1000) for index in range(200)]
    monkeypatch.setattr(panel.store, "records", lambda code=None: rows if code is None else [rows[int(code[2:])]])
    monkeypatch.setattr(s57_panel, "download", lambda *_args, **_kwargs: pytest.fail("No downloads for current cells"))
    changes = []
    panel.charts_changed.connect(lambda: changes.append(True))
    try:
        panel.refresh()
        panel._start("download", rows)
        deadline = time.monotonic() + 5
        while panel.job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert panel.job is None and not panel._pending and not changes
        assert "200 already up to date" in panel.status.text()
        assert all(panel.table.model().index(row, 5).data() == "Up to date" for row in range(200))
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_row_renew_uses_revision_check_instead_of_forcing_current_download(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    row = record(enabled=True, source="NOAA ENC", content_bytes=1000)
    monkeypatch.setattr(panel.store, "records", lambda *_args: [row])
    calls = []
    monkeypatch.setattr(panel, "_renew", lambda rows, **kwargs: calls.append((rows, kwargs)))
    try:
        panel.refresh()
        panel._cell_action(0, False)
        assert calls == [([row], {})]
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("installed, label, highlighted", [
    ({}, "Reload", False), ({"update": "2"}, "Renew", True),
    ({"bounds": [], "feature_count": 0}, "Download", True),
    ({"update": ""}, "Renew", True),
])
def test_noaa_action_labels_and_highlights_follow_verified_revisions(tmp_path, monkeypatch, installed, label, highlighted):
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    row = record(enabled=True, source="NOAA ENC", content_bytes=1000, **installed)
    monkeypatch.setattr(panel.store, "records", lambda *_args: [row])
    try:
        panel.refresh()
        item = QPersistentModelIndex(panel.table.model().index(0, 7))
        panel._apply_catalog([record(date="2026-10-09")])
        assert item.isValid() and panel.table.model().index(0, 7) == item, "Checking revisions must not rebuild the table"
        assert item.data() == label
        assert item.data(s57_panel.ACTION_HIGHLIGHT_ROLE) is highlighted
        if label == "Download":
            assert panel.table.model().index(0, 5).data() == "Not downloaded"
            assert panel.table.model().index(0, 6).data(Qt.UserRole) == 0
        assert panel.job is None and panel.import_button.isEnabled()
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_background_catalogue_check_is_nonblocking_once_per_panel_and_preserves_scroll(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    rows = [record(index, enabled=True, source="NOAA ENC", content_bytes=1000) for index in range(200)]
    entered, release = Event(), Event()
    calls = []
    def catalog(*_args, **kwargs):
        calls.append(kwargs["refresh"])
        entered.set()
        assert release.wait(3)
        return rows
    monkeypatch.setattr(s57_panel, "load_catalog", catalog)
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    monkeypatch.setattr(panel.store, "records", lambda *_args: list(rows))
    try:
        panel.refresh()
        panel.resize(950, 450)
        panel.show()
        app.processEvents()
        assert entered.wait(2) and panel._catalog_job is not None
        assert panel.job is None and panel.table.isEnabled() and panel.import_button.isEnabled()
        panel.table.scrollTo(panel.table.model().index(100, 1))
        scroll = panel.table.verticalScrollBar().value()
        panel._check_catalog(force=True)
        release.set()
        deadline = time.monotonic() + 3
        while panel._catalog_job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert panel._catalog_job is None and calls == [True]
        assert panel.table.verticalScrollBar().value() == scroll
        assert all(panel.table.model().index(row, 7).data() == "Reload" for row in range(200))
        panel.hide()
        panel.show()
        app.processEvents()
        assert calls == [True]
        panel.refresh_catalog.click()
        deadline = time.monotonic() + 3
        while panel._catalog_job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert calls == [True, True]
    finally:
        release.set()
        panel.shutdown()
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_failed_check_does_not_claim_current_cells_and_can_be_retried(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    row = record(enabled=True, source="NOAA ENC")
    monkeypatch.setattr(panel.store, "records", lambda *_args: [row])
    def fail(*_args, **_kwargs):
        raise OSError("No internet")
    monkeypatch.setattr(s57_panel, "load_catalog", fail)
    try:
        panel.refresh()
        panel._check_catalog(force=True)
        deadline = time.monotonic() + 3
        while panel._catalog_job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert panel._catalog_job is None and panel._catalog is None
        assert panel.table.model().index(0, 7).data() == "Check"
        assert not panel.table.model().index(0, 7).data(s57_panel.ACTION_HIGHLIGHT_ROLE)
        assert "check failed" in panel.status.text() and panel.refresh_catalog.isEnabled()
        monkeypatch.setattr(s57_panel, "load_catalog", lambda *_args, **_kwargs: [row])
        panel._check_catalog(force=True)
        deadline = time.monotonic() + 3
        while panel._catalog_job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert panel.table.model().index(0, 7).data() == "Reload"
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_offline_and_hidden_panels_do_not_start_catalogue_requests(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(s57_panel, "load_catalog", lambda *_args, **_kwargs: pytest.fail("Unexpected network request"))
    panel = s57_panel.S57Panel(tmp_path, lambda: True)
    monkeypatch.setattr(panel.store, "records", lambda *_args: [record(enabled=True, source="NOAA ENC")])
    try:
        panel.refresh()
        panel.show()
        app.processEvents()
        panel._check_catalog(force=True)
        assert panel._catalog_job is None and panel._catalog is None
        panel.hide()
        panel.offline = lambda: False
        panel.refresh()
        app.processEvents()
        assert panel._catalog_job is None
    finally:
        panel.shutdown()
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_only_explicit_reload_forces_a_download_and_update_completion_clears_highlight(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    row = record(enabled=True, source="NOAA ENC", update="2")
    monkeypatch.setattr(panel.store, "records", lambda *_args: [row])
    calls = []
    monkeypatch.setattr(panel, "_renew", lambda rows, **kwargs: calls.append((rows, kwargs)))
    try:
        panel.refresh()
        panel._apply_catalog([record()])
        panel._cell_action(0, False)
        assert calls[-1] == ([row], {})
        row["update"] = "3"
        panel._cell_progress(row["code"], "Ready", 100)
        panel.refresh()
        assert panel.table.model().index(0, 7).data() == "Reload"
        assert not panel.table.model().index(0, 7).data(s57_panel.ACTION_HIGHLIGHT_ROLE)
        panel._cell_action(0, False)
        assert calls[-1] == ([row], {"force": True})
        panel._apply_catalog([])
        assert panel.table.model().index(0, 7).data() == "Unavailable"
        assert not panel.table.model().index(0, 7).data(s57_panel.ACTION_HIGHLIGHT_ROLE)
        assert not panel.table.model().index(0, 7).flags() & Qt.ItemIsEnabled
        assert "not in the active" in panel.table.model().index(0, 7).data(Qt.ToolTipRole)
        panel._cell_action(0, False)
        assert len(calls) == 2, "Unavailable charts must not start a renewal"
        panel._apply_catalog([], complete=False)
        assert panel.table.model().index(0, 7).data() == "Check"
        assert panel.table.model().index(0, 7).flags() & Qt.ItemIsEnabled
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_shutdown_cancels_background_check_without_stale_ui_updates(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    entered = Event()
    def catalog(*_args, **kwargs):
        entered.set()
        assert kwargs["cancel"].wait(3)
        return [record()]
    monkeypatch.setattr(s57_panel, "load_catalog", catalog)
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    try:
        panel._check_catalog(force=True)
        assert entered.wait(2)
        job = panel._catalog_job
        panel.shutdown()
        assert not job.isRunning()
        app.processEvents()
        assert panel._catalog is None
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_withdrawn_missing_cells_are_unavailable_and_local_cells_offer_import(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    offline = [False]
    panel = s57_panel.S57Panel(tmp_path, lambda: offline[0])
    rows = [record(bounds=[], feature_count=0, enabled=False, source="NOAA ENC"),
            record(1, code="GB4TESTA", enabled=True, source="Local import")]
    monkeypatch.setattr(panel.store, "records", lambda *_args: rows)
    try:
        panel.refresh()
        panel._apply_catalog([])
        assert panel.table.model().index(0, 7).data() == "Unavailable"
        assert not panel.table.model().index(0, 7).flags() & Qt.ItemIsEnabled
        assert not panel.table.model().index(0, 7).data(s57_panel.ACTION_HIGHLIGHT_ROLE)
        assert panel.table.model().index(1, 7).data() == "Import..."
        offline[0] = True
        panel._controls()
        assert panel.table.model().index(1, 7).flags() & Qt.ItemIsEnabled
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("megabytes, operation, answer, prompts", [
    (50, "download", s57_panel.QMessageBox.Yes, 0),
    (50, "renew", s57_panel.QMessageBox.Yes, 0),
    (100, "renew", s57_panel.QMessageBox.Yes, 0),
    (101, "download", s57_panel.QMessageBox.Yes, 1),
    (101, "renew", s57_panel.QMessageBox.Yes, 1),
    (101, "renew", s57_panel.QMessageBox.No, 1),
    (5000, "renew", s57_panel.QMessageBox.No, 1),
])
def test_download_confirmation_only_counts_required_large_downloads(tmp_path, monkeypatch, megabytes, operation, answer, prompts):
    app = QApplication.instance() or QApplication([])
    rows = [record(size=1000 * 1024**2, enabled=True, source="NOAA ENC"), record(1, size=megabytes * 1024**2)]
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    monkeypatch.setattr(panel.store, "records", lambda *_args: [rows[0]])
    monkeypatch.setattr(s57_panel, "load_catalog", lambda *_args, **_kwargs: rows)
    questions, imported = [], []
    def ask(_parent, title, text, **kwargs):
        questions.append((title, text, kwargs))
        return answer
    def sync(_store, _records, *_args, **_kwargs):
        imported.append(True)
        return noaa.NOAABatchResult(imported=[rows[1]["code"]], current=[rows[0]["code"]])
    monkeypatch.setattr(s57_panel, "ask_question", ask)
    monkeypatch.setattr(s57_panel, "sync_cells", sync)
    try:
        panel._renew(rows) if operation == "renew" else panel._start(operation, rows)
        deadline = time.monotonic() + 4
        while panel.job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert panel.job is None
        assert len(questions) == prompts
        assert bool(imported) is (not prompts or answer == s57_panel.QMessageBox.Yes)
        if prompts:
            assert f"1 chart ({megabytes} MB)" in questions[0][1]
            assert "keep using the simulator" in questions[0][1]
            assert questions[0][2] == {"confirm_label": "Download", "cancel_label": "Cancel"}
        if answer == s57_panel.QMessageBox.No:
            assert "cancelled" in panel.status.text()
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_small_explicit_reload_starts_without_a_confirmation(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    row = record(size=50 * 1024**2, enabled=True, source="NOAA ENC")
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    monkeypatch.setattr(panel.store, "records", lambda *_args: [row])
    monkeypatch.setattr(s57_panel, "load_catalog", lambda *_args, **_kwargs: [row])
    monkeypatch.setattr(s57_panel, "ask_question", lambda *_args, **_kwargs: pytest.fail("No small-download popup"))
    calls = []
    def sync(_store, _records, *_args, **kwargs):
        calls.append(kwargs["force"])
        return noaa.NOAABatchResult(imported=[row["code"]])
    monkeypatch.setattr(s57_panel, "sync_cells", sync)
    try:
        panel.refresh()
        panel._apply_catalog([row])
        panel._cell_action(0, False)
        deadline = time.monotonic() + 4
        while panel.job is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert panel.job is None and calls == [True]
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()


def test_no_button_really_cancels_chart_deletion(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    panel = s57_panel.S57Panel(tmp_path, lambda: False)
    monkeypatch.setattr(s57_panel, "ask_question", lambda *_args, **_kwargs: s57_panel.QMessageBox.No)
    monkeypatch.setattr(panel, "_start", lambda *_args: pytest.fail("No must not delete charts"))
    try:
        panel._delete([record()["code"]])
    finally:
        panel.shutdown()
        panel.deleteLater()
        app.processEvents()
