# SPDX-License-Identifier: MPL-2.0
"""Preserve Windows wheel/touchpad identity before Qt normalizes the event."""

from collections import deque
import ctypes
import os
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication


class WindowsScrollSource(QAbstractNativeEventFilter):
    def __init__(self, app, read_message, device_source):
        super().__init__()
        self._app = app
        self._read_message = read_message
        self._device_source = device_source
        self._pending = deque(maxlen=64)
        app.installNativeEventFilter(self)

    @classmethod
    def for_view(cls, view):
        if os.name != "nt":
            return None
        from ctypes import wintypes

        class InputSource(ctypes.Structure):
            _fields_ = [("device_type", ctypes.c_uint32), ("origin", ctypes.c_uint32)]

        try:
            get_source = ctypes.WinDLL("user32", use_last_error=True).GetCurrentInputMessageSource
            get_source.argtypes = [ctypes.POINTER(InputSource)]
            get_source.restype = wintypes.BOOL

            def read_message(address):
                address = int(address)
                if not address:
                    raise ValueError("Missing native message")
                msg = wintypes.MSG.from_address(address)
                delta = ctypes.c_int16((int(msg.wParam) >> 16) & 0xffff).value
                return msg.message, msg.time, delta

            def device_source():
                source = InputSource()
                return source.device_type if get_source(ctypes.byref(source)) else 0

            observer = cls(QCoreApplication.instance(), read_message, device_source)
            # The native filter is not a QObject: remove it before its Python
            # owner is released, including when a view is deleted directly.
            view.destroyed.connect(observer.close)
            return observer
        except (AttributeError, OSError, RuntimeError):
            return None

    def close(self, *_args):
        if self._app is not None:
            try:
                self._app.removeNativeEventFilter(self)
            except RuntimeError:
                pass
            self._app = None
        self._pending.clear()

    def nativeEventFilter(self, event_type, message):
        if self._app is None or event_type != b"windows_generic_MSG":
            return False, 0
        try:
            message_id, stamp, delta = self._read_message(message)
            if message_id not in (0x020A, 0x020E):  # WM_MOUSEWHEEL / WM_MOUSEHWHEEL
                return False, 0
            device = self._device_source()
            if device in (0x02, 0x10):  # IMDT_MOUSE / IMDT_TOUCHPAD
                self._pending.append((time.monotonic(), stamp, abs(delta),
                                      "pan" if device == 0x10 else "zoom"))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass
        # Observe only: Qt still dispatches the ordinary wheel event to its view.
        return False, 0

    def kind_for(self, event):
        now = time.monotonic()
        while self._pending and now - self._pending[0][0] > .25:
            self._pending.popleft()
        angle = event.angleDelta()
        delta = abs(angle.x()) + abs(angle.y())
        for index, (_seen, stamp, amount, kind) in enumerate(self._pending):
            if stamp == event.timestamp() and amount == delta:
                del self._pending[index]
                return kind
        return None
