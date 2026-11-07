"""Read-only discovery of displays attached to the current Windows desktop."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass


class DisplayDiscoveryError(RuntimeError):
    """The operating system could not enumerate displays."""


class UnsupportedPlatformError(DisplayDiscoveryError):
    """Display discovery has not been implemented for this platform."""


@dataclass(frozen=True)
class Display:
    """One active display; geometry is in physical desktop pixels.

    Device names describe the current session and are not persistent IDs.
    Negative coordinates are normal for displays above or left of the primary.
    """

    device: str
    x: int
    y: int
    width: int
    height: int
    primary: bool


def _windows_error(operation: str) -> DisplayDiscoveryError:
    code = ctypes.get_last_error()
    detail = str(ctypes.WinError(code)) if code else "no Windows error code provided"
    return DisplayDiscoveryError(f"{operation} failed: {detail}")


def list_displays() -> list[Display]:
    """Enumerate active Windows displays without changing display configuration.

    A temporary thread DPI context ensures physical pixel coordinates on mixed
    DPI desktops. The prior thread context is restored before returning.
    """
    if sys.platform != "win32":
        raise UnsupportedPlatformError(
            "Display discovery is currently available on Windows only; "
            "macOS and Linux support is planned."
        )

    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        set_context = user32.SetThreadDpiAwarenessContext
    except AttributeError as exc:
        raise UnsupportedPlatformError(
            "Physical-pixel display discovery requires Windows 10 version 1607 or later."
        ) from exc
    set_context.argtypes = [ctypes.c_void_p]
    set_context.restype = ctypes.c_void_p
    get_info = user32.GetMonitorInfoW
    get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
    get_info.restype = wintypes.BOOL
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, ctypes.c_void_p, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), ctypes.c_ssize_t,
    )
    enumerate_monitors = user32.EnumDisplayMonitors
    enumerate_monitors.argtypes = [
        wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback_type, ctypes.c_ssize_t,
    ]
    enumerate_monitors.restype = wintypes.BOOL
    displays: list[Display] = []
    callback_errors: list[Exception] = []

    @callback_type
    def visit(handle, _hdc, _rect, _data):
        # Exceptions cannot propagate across a ctypes callback boundary.
        try:
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            ctypes.set_last_error(0)
            if not get_info(handle, ctypes.byref(info)):
                raise _windows_error("GetMonitorInfoW")
            rect = info.rcMonitor
            displays.append(Display(
                device=info.szDevice, x=rect.left, y=rect.top,
                width=rect.right - rect.left, height=rect.bottom - rect.top,
                primary=bool(info.dwFlags & 1),
            ))
            return True
        except Exception as exc:
            callback_errors.append(exc)
            return False

    ctypes.set_last_error(0)
    previous = set_context(ctypes.c_void_p(-3))  # PER_MONITOR_AWARE
    if not previous:
        raise _windows_error("SetThreadDpiAwarenessContext")
    try:
        ctypes.set_last_error(0)
        success = enumerate_monitors(None, None, visit, 0)
        if callback_errors:
            raise DisplayDiscoveryError(str(callback_errors[0])) from callback_errors[0]
        if not success:
            raise _windows_error("EnumDisplayMonitors")
    finally:
        ctypes.set_last_error(0)
        if not set_context(previous):
            raise _windows_error("Restoring thread DPI awareness")
    return sorted(displays, key=lambda item: (not item.primary, item.x, item.y, item.device))
