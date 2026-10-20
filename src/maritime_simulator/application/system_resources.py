# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import dataclass, replace
import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys


MIB = 1024.0 * 1024.0
_last_memory_status = None


def cached_memory_status() -> SystemMemoryStatus:
    return _last_memory_status or SystemMemoryStatus(None, None, "awaiting-worker")


@dataclass(frozen=True, slots=True)
class SystemMemoryStatus:
    total_mb: float | None
    available_mb: float | None
    source: str
    swap_used_mb: float | None = None
    paging_out_mb: float | None = None
    process_rss_mb: float | None = None
    children_rss_mb: float | None = None

    @property
    def tree_rss_mb(self) -> float | None:
        if self.process_rss_mb is None:
            return None
        return self.process_rss_mb + (self.children_rss_mb or 0.0)


def sample_memory_status() -> SystemMemoryStatus:
    """Worker-only sample. RSS totals include descendants but can double-count shared pages."""
    status = system_memory_status()
    main, children = process_tree_rss()
    global _last_memory_status
    _last_memory_status = replace(status, process_rss_mb=main, children_rss_mb=children)
    return _last_memory_status


def _tree_rss(rows, root: int) -> tuple[float | None, float | None]:
    processes = {pid: (parent, rss) for pid, parent, rss in rows}
    if root not in processes:
        return None, None
    selected = {root}
    pending = set(processes) - selected
    while True:
        found = {pid for pid in pending if processes[pid][0] in selected}
        if not found:
            break
        selected.update(found)
        pending.difference_update(found)
    return processes[root][1], sum(processes[pid][1] for pid in selected if pid != root)


def process_tree_rss() -> tuple[float | None, float | None]:
    if sys.platform.startswith("win"):
        return _windows_process_tree_rss()
    try:
        result = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], check=True,
                                capture_output=True, text=True, timeout=1.0)
        rows = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) == 3:
                rows.append((int(fields[0]), int(fields[1]), float(fields[2]) / 1024))
        return _tree_rss(rows, os.getpid())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None


def _windows_process_tree_rss() -> tuple[float | None, float | None]:
    from ctypes import wintypes as w

    class Entry(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
                    ("heap", ctypes.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                    ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
                    ("name", w.WCHAR * 260)]

    class Counters(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("faults", w.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak_rss", "rss", "peak_pool", "pool",
                                                "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]

    try:
        kernel, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi")
        kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
        kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(Counters), w.DWORD]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value or not snapshot:
            return None, None
        rows = []
        try:
            entry = Entry()
            entry.size = ctypes.sizeof(entry)
            ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                rows.append((int(entry.pid), int(entry.parent), 0.0))
                ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        # Open only this application's process tree, not every process in the OS.
        selected = {os.getpid()}
        while True:
            added = {pid for pid, parent, _ in rows if parent in selected} - selected
            if not added:
                break
            selected.update(added)
        measured = []
        for pid, parent, _ in rows:
            if pid not in selected:
                continue
            handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
            if not handle:
                continue
            try:
                counters = Counters()
                counters.size = ctypes.sizeof(counters)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.size):
                    measured.append((pid, parent, counters.rss / MIB))
            finally:
                kernel.CloseHandle(handle)
        rss = {pid: value for pid, _parent, value in measured}
        if os.getpid() not in rss:
            return None, None
        return rss[os.getpid()], sum(value for pid, value in rss.items() if pid != os.getpid())
    except (AttributeError, OSError, TypeError, ValueError):
        return None, None


def system_memory_status(platform: str | None = None) -> SystemMemoryStatus:
    """Return host memory capacity without adding a platform-specific dependency."""
    current = platform or sys.platform
    if current.startswith("linux"):
        status = _linux_memory_status(Path("/proc/meminfo"))
        if status is not None:
            return status
    elif current.startswith("win"):
        status = _windows_memory_status()
        if status is not None:
            return status
    elif current == "darwin":
        status = _macos_memory_status()
        if status is not None:
            return status
    return _posix_memory_status()


def _linux_memory_status(path: Path) -> SystemMemoryStatus | None:
    try:
        values = _parse_linux_meminfo(path.read_text(encoding="ascii", errors="replace"))
    except OSError:
        return None
    total_kib = values.get("MemTotal")
    available_kib = values.get("MemAvailable")
    if available_kib is None:
        available_kib = sum(
            values.get(name, 0.0)
            for name in ("MemFree", "Buffers", "Cached", "SReclaimable")
        ) - values.get("Shmem", 0.0)
    if total_kib is None:
        return None
    return SystemMemoryStatus(
        total_mb=max(0.0, total_kib / 1024.0),
        available_mb=max(0.0, available_kib / 1024.0),
        source="linux-proc",
        swap_used_mb=max(0.0, values.get("SwapTotal", 0) - values.get("SwapFree", 0)) / 1024,
        paging_out_mb=_linux_paging_out(),
    )


def _parse_linux_meminfo(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in text.splitlines():
        name, separator, remainder = line.partition(":")
        if not separator:
            continue
        match = re.search(r"[-+]?\d+(?:\.\d+)?", remainder)
        if match:
            values[name.strip()] = float(match.group(0))
    return values


def _linux_paging_out() -> float | None:
    try:
        values = dict(line.split() for line in Path("/proc/vmstat").read_text().splitlines())
        return int(values["pswpout"]) * os.sysconf("SC_PAGE_SIZE") / MIB
    except (OSError, KeyError, ValueError):
        return None


def _windows_memory_status() -> SystemMemoryStatus | None:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = (
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        )

    try:
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return SystemMemoryStatus(
            total_mb=status.ullTotalPhys / MIB,
            available_mb=status.ullAvailPhys / MIB,
            source="windows-global-memory",
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _macos_memory_status() -> SystemMemoryStatus | None:
    total = _posix_total_memory_mb()
    try:
        result = subprocess.run(
            ["/usr/bin/vm_stat"],
            check=True,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        available = _parse_macos_vm_stat(result.stdout)
        page_size = re.search(r"page size of\s+(\d+)\s+bytes", result.stdout)
        swapped = re.search(r"Swapouts:\s*(\d+)", result.stdout)
        paging = int(swapped[1]) * int(page_size[1]) / MIB if swapped and page_size else None
    except (OSError, subprocess.SubprocessError):
        available = None
        paging = None
    if total is None and available is None:
        return None
    swap_used = None
    try:
        output = subprocess.run(["/usr/sbin/sysctl", "-n", "vm.swapusage"], check=True,
                                capture_output=True, text=True, timeout=1.0).stdout
        match = re.search(r"used\s*=\s*([\d.]+)M", output)
        swap_used = float(match[1]) if match else None
    except (OSError, subprocess.SubprocessError):
        pass
    return SystemMemoryStatus(total_mb=total, available_mb=available, source="macos-vm-stat",
                              swap_used_mb=swap_used, paging_out_mb=paging)


def _parse_macos_vm_stat(text: str) -> float | None:
    page_match = re.search(r"page size of\s+(\d+)\s+bytes", text)
    if page_match is None:
        return None
    page_size = int(page_match.group(1))
    pages: dict[str, int] = {}
    for line in text.splitlines()[1:]:
        name, separator, raw = line.partition(":")
        if not separator:
            continue
        match = re.search(r"\d+", raw.replace(".", ""))
        if match:
            pages[name.strip()] = int(match.group(0))
    available_pages = sum(
        pages.get(name, 0)
        for name in ("Pages free", "Pages inactive", "Pages speculative")
    )
    return available_pages * page_size / MIB


def _posix_total_memory_mb() -> float | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / MIB
    except (OSError, TypeError, ValueError):
        return None


def _posix_memory_status() -> SystemMemoryStatus:
    total = _posix_total_memory_mb()
    available = None
    try:
        if "SC_AVPHYS_PAGES" in os.sysconf_names:
            available = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / MIB
    except (OSError, TypeError, ValueError):
        pass
    return SystemMemoryStatus(total_mb=total, available_mb=available, source="posix-sysconf")
