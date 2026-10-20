# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from threading import Event, RLock
import time

from PySide6.QtCore import QThread, Signal

from maritime_simulator.application.memory_policy import AdaptiveMemoryPolicy
from maritime_simulator.application.system_resources import sample_memory_status


class MemoryMonitor(QThread):
    sampled = Signal(object, object, object)

    def __init__(self, water_mask, s57_store, parent=None, *, sampler=sample_memory_status, interval_s=5):
        super().__init__(parent)
        self.water_mask, self.s57_store = water_mask, s57_store
        self.sampler, self.interval_s = sampler, interval_s
        self.policy = AdaptiveMemoryPolicy()
        self.status = None
        self._server = None
        self._lock = RLock()
        self._stop = Event()
        self.last_error = ""

    def set_tile_server(self, server):
        with self._lock:
            self._server = server
        if server is not None:
            allocation = self.policy.budget.allocations()
            # New servers inherit the current limits before serving their first viewport.
            server.set_memory_limits(allocation["raster"], allocation["enc"])

    def run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                status = self.sampler()
                if self._stop.is_set():
                    break
                budget = self.policy.update(status)
                limits = budget.allocations()
                usage = self.water_mask.apply_memory_budget(limits["water"], limits["coast"])
                usage.update(self.s57_store.apply_memory_budget(limits["coverage"]))
                with self._lock:
                    server = self._server
                if server is not None:
                    usage.update(server.apply_memory_budget(limits["raster"], limits["enc"]))
                self.status = status
                self.last_error = ""
                if not self._stop.is_set():
                    self.sampled.emit(status, budget, usage)
            except Exception as error:
                # Telemetry failure must neither kill monitoring nor disrupt simulation/output.
                self.last_error = f"{type(error).__name__}: {error}"[:200]
            self._stop.wait(max(0.05, self.interval_s - (time.monotonic() - started)))

    def stop(self):
        self._stop.set()
        self.wait()
