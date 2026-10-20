# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date
import http.client
from pathlib import Path
import re
import shutil
from threading import Event, Lock, local
from urllib import request
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit

from .geodata import verified_ssl_context
from .s57 import MAX_DOWNLOAD, check_cancel, download, noaa_url


DOWNLOAD_WORKERS = 5
PREFETCH_CELLS = 10
DISK_RESERVE = 128 * 1024**2


def _download_reservation(row):
    try:
        size = int(row.get("size", 0))
    except (TypeError, ValueError, OverflowError):
        size = 0
    return min(MAX_DOWNLOAD, max(1024**2, size + size // 4)) if size > 0 else MAX_DOWNLOAD


def cell_is_current(record, installed):
    """Only skip a usable cell with known, equal or newer revision metadata."""
    bounds = installed.get("bounds") if installed else None
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4 or installed.get("feature_count") == 0:
        return False
    return _revision_is_current(record, installed)


def _revision_is_current(record, installed):
    revisions = []
    for row in (record, installed):
        values = [str(row.get(key, "")) for key in ("edition", "update")]
        if not all(re.fullmatch(r"[0-9]{1,10}", value) for value in values):
            return False
        revisions.append(tuple(int(value) for value in values))
    if revisions[1] != revisions[0]:
        return revisions[1] > revisions[0]
    dates = [str(row.get("date", "")) for row in (record, installed)]
    if not all(re.fullmatch(r"(?:[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2})", value) for value in dates):
        return False
    try:
        parsed = [date.fromisoformat(value) for value in dates]
    except ValueError:
        return False
    return parsed[1] >= parsed[0]


def cell_needs_download(record, installed, *, force=False):
    return not (cell_is_current(record, installed) and (not force or not _revision_is_current(installed, record)))


class NOAADownloadClient:
    """A worker-owned HTTPS connection; configured proxies retain urllib handling."""

    def __init__(self):
        self.context = verified_ssl_context()
        self.connections = {}
        self.proxy = bool(request.getproxies().get("https"))

    def close(self):
        for connection in self.connections.values():
            connection.close()
        self.connections.clear()

    def _discard(self, host):
        connection = self.connections.pop(host, None)
        if connection is not None:
            connection.close()

    def download(self, url, target, *, cancel=None, progress=None, limit=MAX_DOWNLOAD):
        url = noaa_url(url)
        check_cancel(cancel)
        if self.proxy:
            return download(url, target, cancel=cancel, progress=progress, limit=limit)
        original = url
        for attempt in range(2):
            url = original
            try:
                for _redirect in range(4):
                    check_cancel(cancel)
                    parsed = urlsplit(noaa_url(url))
                    host = parsed.hostname
                    if host not in self.connections:
                        self.connections[host] = http.client.HTTPSConnection(host, timeout=15, context=self.context)
                    connection = self.connections[host]
                    connection.request("GET", parsed.path, headers={
                        "User-Agent": "Maritime-Ship-Simulator/ENC", "Accept-Encoding": "identity",
                    })
                    response = connection.getresponse()
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.getheader("Location", "")
                        response.close()
                        self._discard(host)
                        if not location:
                            raise ValueError("NOAA redirect has no destination")
                        url = noaa_url(urljoin(url, location))
                        continue
                    try:
                        if response.status != 200:
                            raise HTTPError(url, response.status, response.reason, response.headers, None)
                        total = int(response.getheader("Content-Length", "0"))
                        if total < 0 or total > limit:
                            raise ValueError("NOAA download exceeds the safety size limit")
                        size = 0
                        with Path(target).open("wb") as writer:
                            while True:
                                check_cancel(cancel)
                                chunk = response.read(256 * 1024)
                                if not chunk:
                                    break
                                size += len(chunk)
                                if size > limit:
                                    raise ValueError("NOAA download exceeds the safety size limit")
                                writer.write(chunk)
                                if progress:
                                    progress(size, total, "Downloading NOAA")
                        if total and total != size:
                            raise ValueError("Incomplete NOAA download; please retry")
                        check_cancel(cancel)
                        return
                    finally:
                        response.close()
                raise ValueError("Too many NOAA redirects")
            except Exception as error:
                self._discard(urlsplit(url).hostname)
                Path(target).unlink(missing_ok=True)
                check_cancel(cancel)
                # A server-closed keep-alive connection can be retried once, not HTTP errors.
                if attempt or isinstance(error, HTTPError) or not isinstance(error, (OSError, http.client.HTTPException)):
                    raise


@dataclass
class NOAABatchResult:
    imported: list[str] = field(default_factory=list)
    current: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    cancelled: bool = False


def sync_cells(store, records, work, *, cancel, progress, cell_progress,
               force=False, download_fn=download, client_factory=NOAADownloadClient):
    """Bounded download-ahead with one transactional importer and per-cell failures."""
    installed = {row["code"]: row for row in store.records()}
    result = NOAABatchResult()
    selected = list({row["code"]: row for row in records}.values())
    needed = []
    for row in selected:
        check_cancel(cancel)
        current = installed.get(row["code"])
        # An explicit reload may replace the same revision, never a newer local one.
        if not cell_needs_download(row, current, force=force):
            result.current.append(row["code"])
        else:
            noaa_url(row["url"])
            needed.append(row)
    total = len(selected)
    if not needed:
        progress(total, total, f"All {len(result.current)} selected NOAA cells are up to date")
        return result

    lock = Lock()
    stop = Event()
    class DownloadCancellation:
        def is_set(self):
            return cancel.is_set() or stop.is_set()
    download_cancel = DownloadCancellation()
    workers = local()
    clients = []
    downloading = set()
    fractions = {}
    staged_bytes = {}
    completed = len(result.current)
    done_units = completed * 100
    importing = None

    def update(code, state, percentage):
        nonlocal completed, done_units, importing
        with lock:
            units = 100 if state in {"Ready", "Failed"} else percentage
            old = fractions.get(code, 0)
            done_units += units - old
            fractions[code] = units
            if state == "Downloading":
                downloading.add(code)
            else:
                downloading.discard(code)
            if state == "Importing":
                importing = code
            elif state in {"Ready", "Failed"}:
                if old < 100:
                    completed += 1
                if importing == code:
                    importing = None
            cell_progress(code, state, percentage)
            import_status = f"importing {importing}" if importing else "waiting for next import"
            progress(done_units, total * 100,
                     f"NOAA: {completed}/{total} finished | {len(downloading)} downloading | {import_status}")

    def fetch(row):
        code = row["code"]
        target = Path(work) / (code + ".zip")
        check_cancel(download_cancel)
        update(code, "Downloading", 0)
        if not hasattr(workers, "client"):
            workers.client = client_factory()
            with lock:
                clients.append(workers.client)
        last = -1
        last_disk_check = -1024**2

        def downloaded(done, size, _text):
            nonlocal last, last_disk_check
            with lock:
                staged_bytes[code] = done
            if done - last_disk_check >= 1024**2:
                last_disk_check = done
                if shutil.disk_usage(work).free < DISK_RESERVE:
                    raise ValueError("Not enough free disk space for NOAA downloads; free space and retry")
            percentage = min(49, round(done / size * 50)) if size else 0
            if percentage != last:
                last = percentage
                update(code, "Downloading", percentage)

        try:
            download_fn(row["url"], target, cancel=download_cancel, progress=downloaded, client=workers.client)
            check_cancel(download_cancel)
            update(code, "Downloaded", 50)
            return target
        except Exception:
            target.unlink(missing_ok=True)
            raise

    pending = {}
    next_index = 0
    imported = set()
    try:
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS, thread_name_prefix="noaa-enc") as executor:
            try:
                while next_index < len(needed) or pending:
                    check_cancel(cancel)
                    while next_index < len(needed) and len(pending) < PREFETCH_CELLS:
                        check_cancel(cancel)
                        row = needed[next_index]
                        with lock:
                            outstanding = sum(max(0, _download_reservation(item) - staged_bytes.get(item["code"], 0))
                                              for future, item in pending.items() if not future.done())
                        # Free space already excludes completed ZIPs. Import those before queuing more
                        # if capacity is tight; unknown sizes still reserve the full safety limit.
                        if shutil.disk_usage(work).free < outstanding + _download_reservation(row) + DISK_RESERVE:
                            if pending:
                                break
                            raise ValueError("Not enough free disk space for NOAA downloads; free space and retry")
                        pending[executor.submit(fetch, row)] = row
                        next_index += 1
                    ready, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in ready:
                        check_cancel(cancel)
                        row = pending[future]
                        code = row["code"]
                        target = Path(work) / (code + ".zip")
                        try:
                            target = future.result()
                            check_cancel(cancel)
                            update(code, "Importing", 50)
                            imported.update(store.import_files([target], cancel=cancel,
                                                               names={code: row["name"]}, origin="NOAA ENC"))
                            update(code, "Ready", 100)
                        except Exception as error:
                            check_cancel(cancel)
                            result.errors[code] = str(error)[:500]
                            update(code, "Failed", 0)
                        finally:
                            target.unlink(missing_ok=True)
                        del pending[future]
                        with lock:
                            staged_bytes.pop(code, None)
            finally:
                # Join producers before the caller removes staging files or destroys Qt objects.
                if pending:
                    stop.set()
                    for future in pending:
                        future.cancel()
    except Exception as error:
        result.cancelled = cancel.is_set()
        if not result.cancelled:
            result.errors["batch"] = str(error)[:500]
        for row in needed:
            code = row["code"]
            if code not in imported and code not in result.errors:
                cell_progress(code, "Cancelled" if result.cancelled else "Failed", 0)
    finally:
        for client in clients:
            client.close()
    result.imported = [row["code"] for row in selected if row["code"] in imported]
    return result
