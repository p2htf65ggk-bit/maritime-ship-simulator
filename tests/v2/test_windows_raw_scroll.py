# SPDX-License-Identifier: MPL-2.0
"""Validate the Windows ABI and observer lifecycle without requiring user32."""

import ctypes
from types import SimpleNamespace

import pytest

from ui import windows_scroll
from ui.windows_scroll import _RawInputDevice, _RawInputHeader, _RawMouse, _RawMouseWheel


class ApiCall:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class RawApi:
    def __init__(self, registrations=()):
        self.registrations = list(registrations)
        self.changes = []
        self.payload = b""
        self.query_error = False
        self.read_error = False
        self.register_error = False
        self.GetRegisteredRawInputDevices = ApiCall(self.registered)
        self.RegisterRawInputDevices = ApiCall(self.register)
        self.GetRawInputData = ApiCall(self.data)

    def registered(self, buffer, count_pointer, size):
        assert size == ctypes.sizeof(_RawInputDevice)
        count = ctypes.cast(count_pointer, ctypes.POINTER(ctypes.c_uint32)).contents
        count.value = len(self.registrations)
        if self.query_error:
            count.value = 0
            return 0xffffffff
        if buffer is None:
            return 0xffffffff if self.registrations else 0
        for index, values in enumerate(self.registrations):
            buffer[index] = _RawInputDevice(*values)
        return len(self.registrations)

    def register(self, pointer, count, size):
        assert count == 1 and size == ctypes.sizeof(_RawInputDevice)
        device = ctypes.cast(pointer, ctypes.POINTER(_RawInputDevice)).contents
        values = (device.page, device.usage, device.flags, device.target)
        self.changes.append(values)
        if self.register_error:
            return 0
        self.registrations = [] if device.flags == 1 else [values]
        return 1

    def data(self, handle, command, buffer, size_pointer, header_size):
        assert handle == 9 and command == 0x10000003
        assert header_size == ctypes.sizeof(_RawInputHeader)
        size = ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_uint32)).contents
        if buffer is None:
            size.value = len(self.payload)
            return 0xffffffff if self.query_error else 0
        if self.read_error:
            return 0xffffffff
        assert size.value >= len(self.payload)
        ctypes.memmove(buffer, self.payload, len(self.payload))
        return len(self.payload)


def wheel_payload(amount=8, flags=0x0400, *, kind=0, device=99, size=None):
    total = ctypes.sizeof(_RawInputHeader) + ctypes.sizeof(_RawMouse)
    header = _RawInputHeader(kind, total if size is None else size, device, 0)
    mouse = _RawMouse()
    mouse.buttons = ((amount & 0xffff) << 16) | flags
    return bytes(header) + bytes(mouse)


def test_raw_mouse_abi_matches_windows_on_32_and_64_bit_hosts():
    assert ctypes.sizeof(_RawMouse) == 24
    assert _RawMouse.buttons.offset == 4
    assert _RawMouse.x.offset == 12
    assert ctypes.sizeof(_RawInputHeader) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
    assert ctypes.sizeof(_RawInputDevice) == (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 12)


def test_raw_registration_never_disables_legacy_mouse_or_takes_background_capture():
    api = RawApi()
    raw = _RawMouseWheel(api)
    assert api.changes == [(1, 2, 0, None)]
    raw.close()
    raw.close()
    assert api.changes == [(1, 2, 0, None), (1, 2, 1, None)]
    assert raw.wheel(9) is None


def test_existing_mouse_registration_is_neither_overwritten_nor_removed():
    api = RawApi([(1, 2, 0x100, 1001)])
    raw = _RawMouseWheel(api)
    raw.close()
    assert not api.changes
    assert api.registrations == [(1, 2, 0x100, 1001)]


def test_registration_preserves_non_mouse_and_later_changed_mouse_registration():
    api = RawApi([(1, 6, 0, None)])
    raw = _RawMouseWheel(api)
    assert api.changes == [(1, 2, 0, None)]
    api.registrations = [(1, 2, 0x100, 101)]
    raw.close()
    assert api.changes == [(1, 2, 0, None)]


@pytest.mark.parametrize("amount,flags,axis", [(8, 0x0400, "vertical"),
    (-8, 0x0400, "vertical"), (120, 0x0400, "vertical"),
    (-120, 0x0800, "horizontal")])
def test_raw_physical_wheel_sign_and_axis(amount, flags, axis):
    api = RawApi()
    raw = _RawMouseWheel(api)
    api.payload = wheel_payload(amount, flags)
    assert raw.wheel(9) == (abs(amount), axis)
    raw.close()


@pytest.mark.parametrize("payload", [b"", b"x" * 16, b"x" * 2048,
    wheel_payload(kind=1), wheel_payload(device=0), wheel_payload(size=1),
    wheel_payload(size=1000), wheel_payload(flags=0), wheel_payload(amount=0),
    wheel_payload()[:-1]])
def test_raw_invalid_non_mouse_and_movement_payloads_are_ignored(payload):
    api = RawApi()
    raw = _RawMouseWheel(api)
    api.payload = payload
    assert raw.wheel(9) is None
    raw.close()


def test_raw_api_errors_are_recoverable():
    api = RawApi()
    api.query_error = True
    with pytest.raises(OSError):
        _RawMouseWheel(api)
    api.query_error = False
    api.register_error = True
    with pytest.raises(OSError):
        _RawMouseWheel(api)
    api.register_error = False
    raw = _RawMouseWheel(api)
    api.payload = wheel_payload()
    for attribute in ("query_error", "read_error"):
        setattr(api, attribute, True)
        assert raw.wheel(9) is None
        setattr(api, attribute, False)
    assert raw.wheel(9) == (8, "vertical")
    raw.close()


def test_all_map_views_share_one_filter_and_raw_registration(monkeypatch):
    callbacks = []
    installed = []
    app = SimpleNamespace(installNativeEventFilter=installed.append,
        removeNativeEventFilter=installed.remove,
        aboutToQuit=SimpleNamespace(connect=callbacks.append))
    views = [SimpleNamespace(destroyed=SimpleNamespace(connect=lambda callback: None)) for _ in range(3)]
    api = RawApi()
    api.GetCurrentInputMessageSource = ApiCall(lambda pointer: 0)
    monkeypatch.setattr(windows_scroll, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(windows_scroll.QCoreApplication, "instance", lambda: app)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: api, raising=False)
    first, second = [windows_scroll.WindowsScrollSource.for_view(view) for view in views[:2]]
    assert len(installed) == 1 and len(callbacks) == 1
    assert first._observer is second._observer
    first.close()
    first.close()
    assert len(installed) == 1
    second.close()
    assert not installed
    third = windows_scroll.WindowsScrollSource.for_view(views[2])
    assert len(installed) == 1 and len(callbacks) == 1
    assert api.changes == [(1, 2, 0, None)]
    third.close()
    callbacks[0]()
    assert not installed
    assert api.changes == [(1, 2, 0, None), (1, 2, 1, None)]
