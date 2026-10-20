# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import json
import time

from PySide6.QtCore import QTimer


class InactiveWebPage:
    """Discard hidden documents under pressure, keeping only small navigation state."""

    def __init__(self, view, before_reload, on_discard):
        self.view = view
        self.before_reload = before_reload
        self.on_discard = on_discard
        self.hidden_since = None
        self.state = None
        self.discarded = False
        self._generation = 0
        self._restore_pending = False
        self._closed = False

    def hide(self, freeze):
        if self._closed or self.hidden_since is not None:
            return
        self.hidden_since = time.monotonic()
        if self.discarded:
            return
        self._generation += 1
        generation = self._generation
        self.state = None
        completed = False

        def finish(value=None):
            nonlocal completed
            if generation != self._generation or self.hidden_since is None:
                return
            if isinstance(value, str) and len(value) <= 8192:
                try:
                    state = json.loads(value)
                    if isinstance(state, dict):
                        self.state = state
                except ValueError:
                    pass
            if not completed:
                completed = True
                freeze()

        self.view.page().runJavaScript(
            "window.exportNavigationState ? JSON.stringify(window.exportNavigationState()) : 'null';", finish)
        QTimer.singleShot(500, finish)

    def show(self):
        if self._closed:
            return
        self.hidden_since = None
        self._generation += 1
        if self.discarded:
            self.discarded = False
            self._restore_pending = True
            self.before_reload()

    def discard_if_idle(self, pressure, *, now_s=None):
        now = time.monotonic() if now_s is None else now_s
        if (self._closed or not pressure or self.discarded or self.hidden_since is None
                or now - self.hidden_since < 60 or not self.state or self.state.get("busy")):
            return False
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage
            page = self.view.page()
            if page.isLoading():
                return False
            page.setVisible(False)
            page.setLifecycleState(QWebEnginePage.LifecycleState.Discarded)
            if page.lifecycleState() != QWebEnginePage.LifecycleState.Discarded:
                return False
            self.discarded = True
            self.on_discard()
            return True
        except (AttributeError, RuntimeError):
            return False

    def loaded(self):
        if self._closed or self.hidden_since is not None:
            return
        if self._restore_pending and self.state:
            payload = json.dumps(self.state, ensure_ascii=True, allow_nan=False)
            self.view.page().runJavaScript(
                f"if (window.restoreNavigationState) window.restoreNavigationState({payload});")
        self._restore_pending = False

    def reset(self):
        # Explicit navigation/provider changes invalidate snapshots of the old document.
        self._generation += 1
        self.hidden_since = None
        self.state = None
        self.discarded = False
        self._restore_pending = False

    def close(self):
        self._closed = True
        self._generation += 1
        self.hidden_since = None
