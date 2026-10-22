# SPDX-License-Identifier: MPL-2.0
"""Preserve Windows wheel/touchpad identity before Qt normalizes the event."""

from collections import deque
import ctypes
import os
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication


class _RawInputDevice(ctypes.Structure):
    _fields_ = [("page", ctypes.c_uint16), ("usage", ctypes.c_uint16),
                ("flags", ctypes.c_uint32), ("target", ctypes.c_void_p)]


class _RawInputHeader(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_uint32), ("size", ctypes.c_uint32),
                ("device", ctypes.c_void_p), ("wparam", ctypes.c_size_t)]


class _RawMouse(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint16), ("buttons", ctypes.c_uint32),
                ("raw_buttons", ctypes.c_uint32), ("x", ctypes.c_int32),
                ("y", ctypes.c_int32), ("extra", ctypes.c_uint32)]


class _RawMouseWheel:
    """Observe physical wheels without disabling legacy input or taking capture."""

    def __init__(self, user32):
        self._api = user32
        self._owned = False
        self._closed = False
        user32.GetRegisteredRawInputDevices.argtypes = [ctypes.POINTER(_RawInputDevice),
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
        user32.GetRegisteredRawInputDevices.restype = ctypes.c_uint32
        user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(_RawInputDevice),
            ctypes.c_uint32, ctypes.c_uint32]
        user32.RegisterRawInputDevices.restype = ctypes.c_int
        user32.GetRawInputData.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
        user32.GetRawInputData.restype = ctypes.c_uint32
        if not self._mouse_registration():
            # No NOLEGACY/INPUTSINK flags: ordinary mouse delivery is unchanged,
            # and raw wheel messages are received only while this app is active.
            device = _RawInputDevice(1, 2, 0, None)
            if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
                raise OSError("Raw mouse observation unavailable")
            self._owned = True

    def _mouse_registration(self):
        count = ctypes.c_uint32()
        size = ctypes.sizeof(_RawInputDevice)
        result = self._api.GetRegisteredRawInputDevices(None, ctypes.byref(count), size)
        # Windows may return INSUFFICIENT_BUFFER while reporting the required
        # count for a NULL buffer. The second call validates that list.
        if result == 0xffffffff and not count.value:
            raise OSError("Raw input registration query failed")
        if not count.value:
            return None
        if count.value > 128:
            raise OSError("Too many raw input registrations")
        devices = (_RawInputDevice * count.value)()
        result = self._api.GetRegisteredRawInputDevices(devices, ctypes.byref(count), size)
        if result == 0xffffffff or result > len(devices):
            raise OSError("Raw input registration read failed")
        return next((d for d in devices[:result] if d.page == 1 and d.usage == 2), None)

    def wheel(self, handle):
        if self._closed:
            return None
        size = ctypes.c_uint32()
        header_size = ctypes.sizeof(_RawInputHeader)
        get_data = self._api.GetRawInputData
        if get_data(handle, 0x10000003, None, ctypes.byref(size), header_size) == 0xffffffff:
            return None
        minimum = header_size + ctypes.sizeof(_RawMouse)
        if not minimum <= size.value <= 1024:
            return None
        data = ctypes.create_string_buffer(size.value)
        read = get_data(handle, 0x10000003, data, ctypes.byref(size), header_size)
        if read < minimum or read > len(data):
            return None
        header = _RawInputHeader.from_buffer(data)
        if header.kind != 0 or not header.device or not minimum <= header.size <= read:
            return None
        mouse = _RawMouse.from_buffer(data, header_size)
        flags = mouse.buttons & 0xffff
        amount = ctypes.c_int16(mouse.buttons >> 16).value
        if not amount or not flags & 0x0c00:
            return None
        return abs(amount), "horizontal" if flags & 0x0800 else "vertical"

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._owned:
            try:
                current = self._mouse_registration()
                if current is not None and current.flags == 0 and not current.target:
                    device = _RawInputDevice(1, 2, 1, None)  # RIDEV_REMOVE
                    self._api.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
            except (AttributeError, OSError, RuntimeError):
                pass


class WindowsScrollSource(QAbstractNativeEventFilter):
    def __init__(self, app, read_message, device_source, *, raw_wheel=None):
        super().__init__()
        self._app = app
        self._read_message = read_message
        self._device_source = device_source
        self._raw_wheel = raw_wheel
        self._pending = deque(maxlen=64)
        self._physical = deque(maxlen=64)
        self._leases = 0
        app.installNativeEventFilter(self)

    @classmethod
    def for_view(cls, view):
        if os.name != "nt":
            return None
        app = QCoreApplication.instance()
        existing = getattr(app, "_maritime_windows_scroll_source", None)
        if existing is not None and existing._app is not None:
            lease = _ScrollSourceLease(existing)
            view.destroyed.connect(lease.close)
            return lease
        from ctypes import wintypes

        class InputSource(ctypes.Structure):
            _fields_ = [("device_type", ctypes.c_uint32), ("origin", ctypes.c_uint32)]

        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_source = user32.GetCurrentInputMessageSource
            get_source.argtypes = [ctypes.POINTER(InputSource)]
            get_source.restype = wintypes.BOOL

            def read_message(address):
                address = int(address)
                if not address:
                    raise ValueError("Missing native message")
                msg = wintypes.MSG.from_address(address)
                if msg.message == 0x00ff:  # WM_INPUT
                    return msg.message, msg.time, msg.lParam
                delta = ctypes.c_int16((int(msg.wParam) >> 16) & 0xffff).value
                return msg.message, msg.time, delta

            def device_source():
                source = InputSource()
                return source.device_type if get_source(ctypes.byref(source)) else 0

            raw = getattr(app, "_maritime_raw_mouse_wheel", None)
            if raw is None:
                try:
                    raw = _RawMouseWheel(user32)
                    app._maritime_raw_mouse_wheel = raw
                    app.aboutToQuit.connect(raw.close)
                except (AttributeError, OSError, RuntimeError):
                    raw = None
            observer = cls(app, read_message, device_source, raw_wheel=raw.wheel if raw else None)
            app._maritime_windows_scroll_source = observer
            # The native filter is not a QObject: remove it before its Python
            # owner is released, including when a view is deleted directly.
            lease = _ScrollSourceLease(observer)
            view.destroyed.connect(lease.close)
            return lease
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
        self._physical.clear()

    def nativeEventFilter(self, event_type, message):
        if self._app is None or event_type != b"windows_generic_MSG":
            return False, 0
        try:
            message_id, stamp, delta = self._read_message(message)
            if message_id == 0x00ff and self._raw_wheel is not None:
                wheel = self._raw_wheel(delta)
                if wheel is not None:
                    self._physical.append((time.monotonic(), stamp, *wheel))
                return False, 0
            if message_id not in (0x020A, 0x020E):  # WM_MOUSEWHEEL / WM_MOUSEHWHEEL
                return False, 0
            device = self._device_source()
            if device in (0x02, 0x10) or (device == 0 and self._raw_wheel is not None):
                kind = "pan" if device == 0x10 or message_id == 0x020e else "mouse"
                self._pending.append((time.monotonic(), stamp, abs(delta),
                                      kind))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass
        # Observe only: Qt still dispatches the ordinary wheel event to its view.
        return False, 0

    def kind_for(self, event):
        now = time.monotonic()
        while self._pending and now - self._pending[0][0] > .25:
            self._pending.popleft()
        while self._physical and now - self._physical[0][0] > .25:
            self._physical.popleft()
        angle = event.angleDelta()
        delta = abs(angle.x()) + abs(angle.y())
        for index, (_seen, stamp, amount, kind) in enumerate(self._pending):
            if stamp == event.timestamp() and amount == delta:
                del self._pending[index]
                if kind == "mouse":
                    axis = "horizontal" if angle.x() else "vertical"
                    physical = next((i for i, (_seen, raw_stamp, raw_amount, raw_axis)
                        in enumerate(self._physical) if raw_stamp == stamp
                        and raw_amount == amount and raw_axis == axis), None)
                    if physical is not None:
                        del self._physical[physical]
                        return "zoom"
                    # Qt Windows drops pixelDelta and scroll phases. Touchpad
                    # wheel messages can even identify as a generic mouse.
                    # A real wheel has a matching device-backed WM_INPUT;
                    # touchpad compatibility messages do not. This also handles
                    # a touchpad's first scroll being an exact 120-unit step.
                    if self._raw_wheel is not None:
                        return "pan"
                    return "zoom" if amount else None
                return kind
        return None


class _ScrollSourceLease:
    """Share one native observer across embedded and detached map views."""

    def __init__(self, observer):
        self._observer = observer
        observer._leases += 1

    def kind_for(self, event):
        return self._observer.kind_for(event) if self._observer is not None else None

    def close(self, *_args):
        observer, self._observer = self._observer, None
        if observer is not None:
            observer._leases -= 1
            if not observer._leases:
                observer.close()
