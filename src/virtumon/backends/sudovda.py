"""SudoVDA's per-monitor Windows IOCTL interface, implemented with ctypes.

ABI source: SudoMaker/SudoVDA commit
a4b09fa2aa731a964d0cb5d139cb1e6240e4da12, Common/Include/sudovda-ioctl.h.
The same header ships in Apollo v0.4.6. No native libraries are loaded until
open(), so this module can be imported and its wire formats tested on Linux.

An open handle does not keep displays alive: a caller must send regular ping()
requests while using monitors when the driver's watchdog is enabled. The
watchdog covers the entire driver, not individual clients. This module never
changes that setting or removes monitors on close().
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from dataclasses import dataclass


DRIVER_INTERFACE_GUID = "e5bcc234-1e0c-418a-a0d4-ef8b7501414d"
IOCTL_ADD_VIRTUAL_DISPLAY = 0x222000
IOCTL_REMOVE_VIRTUAL_DISPLAY = 0x222004
IOCTL_GET_WATCHDOG = 0x22200C
IOCTL_DRIVER_PING = 0x222220
IOCTL_GET_PROTOCOL_VERSION = 0x2223FC

_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_NO_MORE_ITEMS = 259
_ERROR_NOT_FOUND = 1168
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_U32 = ctypes.c_uint32
_I32 = ctypes.c_int32
_HANDLE = ctypes.c_void_p


class DriverError(RuntimeError):
    """A driver operation failed; winerror retains the native error if present."""

    def __init__(self, message: str, *, winerror: int | None = None) -> None:
        super().__init__(message)
        self.winerror = winerror


class DriverUnavailableError(DriverError):
    """Windows or an accessible SudoVDA device is unavailable."""


class DriverProtocolError(DriverError):
    """The installed driver's protocol cannot safely be used by this backend."""


@dataclass(frozen=True)
class NativeIdentity:
    """Current-session display target; always match BOTH LUID and target ID.

    The monitor UUID is its persistent identity. Windows can recycle these
    adapter/target values after removal, reboot, or a driver restart.
    """

    adapter_low: int
    adapter_high: int
    target_id: int


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", _U32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", _U32), ("HighPart", _I32)]


class _AddParams(ctypes.Structure):
    _fields_ = [
        ("Width", _U32),
        ("Height", _U32),
        ("RefreshRate", _U32),
        ("MonitorGuid", _GUID),
        ("DeviceName", ctypes.c_char * 14),
        ("SerialNumber", ctypes.c_char * 14),
    ]


class _RemoveParams(ctypes.Structure):
    _fields_ = [("MonitorGuid", _GUID)]


class _AddOutput(ctypes.Structure):
    _fields_ = [("AdapterLuid", _LUID), ("TargetId", _U32)]


class _WatchdogOutput(ctypes.Structure):
    _fields_ = [("Timeout", _U32), ("Countdown", _U32)]


class _ProtocolOutput(ctypes.Structure):
    # A C++ bool is one byte here; do not use Win32 BOOL (four bytes).
    _fields_ = [
        ("Major", ctypes.c_uint8),
        ("Minor", ctypes.c_uint8),
        ("Incremental", ctypes.c_uint8),
        ("TestBuild", ctypes.c_uint8),
    ]


class _DeviceInterfaceData(ctypes.Structure):
    _fields_ = [
        ("cbSize", _U32),
        ("InterfaceClassGuid", _GUID),
        ("Flags", _U32),
        ("Reserved", ctypes.c_size_t),
    ]


class _Win32API:
    """Lazy Windows bindings; fixed-width integers also keep Linux tests valid."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise DriverUnavailableError("SudoVDA virtual monitor control is available on Windows only.")
        try:
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
        except OSError as exc:
            raise DriverUnavailableError(f"Could not load the Windows driver APIs: {exc}") from exc

        def bind(dll, name, result, arguments):
            function = getattr(dll, name)
            function.restype = result
            function.argtypes = arguments
            setattr(self, name, function)

        bind(self.setupapi, "SetupDiGetClassDevsW", _HANDLE,
             [ctypes.POINTER(_GUID), ctypes.c_wchar_p, _HANDLE, _U32])
        bind(self.setupapi, "SetupDiEnumDeviceInterfaces", _I32,
             [_HANDLE, ctypes.c_void_p, ctypes.POINTER(_GUID), _U32,
              ctypes.POINTER(_DeviceInterfaceData)])
        bind(self.setupapi, "SetupDiGetDeviceInterfaceDetailW", _I32,
             [_HANDLE, ctypes.POINTER(_DeviceInterfaceData), ctypes.c_void_p,
              _U32, ctypes.POINTER(_U32), ctypes.c_void_p])
        bind(self.setupapi, "SetupDiDestroyDeviceInfoList", _I32, [_HANDLE])
        bind(self.kernel32, "CreateFileW", _HANDLE,
             [ctypes.c_wchar_p, _U32, _U32, ctypes.c_void_p, _U32, _U32, _HANDLE])
        bind(self.kernel32, "DeviceIoControl", _I32,
             [_HANDLE, _U32, ctypes.c_void_p, _U32, ctypes.c_void_p, _U32,
              ctypes.POINTER(_U32), ctypes.c_void_p])
        bind(self.kernel32, "CloseHandle", _I32, [_HANDLE])
        self.get_last_error = ctypes.get_last_error
        self.set_last_error = ctypes.set_last_error
        self.format_error = ctypes.FormatError


def _failure(api, operation: str, error_class=DriverError, *, code=None):
    code = api.get_last_error() if code is None else code
    detail = api.format_error(code).strip() if code else "no Windows error code provided"
    return error_class(f"{operation} failed (WinError {code}): {detail}", winerror=code)


def _as_guid(value: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _device_paths(api) -> list[str]:
    interface_guid = _as_guid(DRIVER_INTERFACE_GUID)
    api.set_last_error(0)
    info_set = api.SetupDiGetClassDevsW(
        ctypes.byref(interface_guid), None, None, 0x12,  # PRESENT | DEVICEINTERFACE
    )
    if info_set in (None, 0, _INVALID_HANDLE_VALUE):
        raise _failure(api, "Finding SudoVDA devices", DriverUnavailableError)
    paths: list[str] = []
    try:
        index = 0
        while True:
            interface = _DeviceInterfaceData()
            interface.cbSize = ctypes.sizeof(interface)
            api.set_last_error(0)
            if not api.SetupDiEnumDeviceInterfaces(
                info_set, None, ctypes.byref(interface_guid), index, ctypes.byref(interface),
            ):
                if api.get_last_error() == _ERROR_NO_MORE_ITEMS:
                    break
                raise _failure(api, "Enumerating SudoVDA devices", DriverUnavailableError)
            index += 1
            required = _U32()
            api.set_last_error(0)
            success = api.SetupDiGetDeviceInterfaceDetailW(
                info_set, ctypes.byref(interface), None, 0, ctypes.byref(required), None,
            )
            if not success and api.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
                raise _failure(api, "Reading the SudoVDA device path", DriverUnavailableError)
            if required.value < 6:
                raise DriverProtocolError("Windows returned an invalid SudoVDA interface path size.")
            detail = ctypes.create_string_buffer(required.value)
            # The W variant requires cbSize=8 on Win64 and 6 on Win32, while
            # DevicePath starts at byte 4 on BOTH. Python's wchar is 4 bytes
            # on Linux, so decode the UTF-16 buffer explicitly instead.
            _U32.from_buffer(detail).value = 8 if ctypes.sizeof(_HANDLE) == 8 else 6
            api.set_last_error(0)
            if not api.SetupDiGetDeviceInterfaceDetailW(
                info_set, ctypes.byref(interface), detail, len(detail), ctypes.byref(required), None,
            ):
                raise _failure(api, "Reading the SudoVDA device path", DriverUnavailableError)
            try:
                path = detail.raw[4:].decode("utf-16-le").split("\0", 1)[0]
            except UnicodeDecodeError as exc:
                raise DriverProtocolError("Windows returned an invalid SudoVDA interface path.") from exc
            if not path:
                raise DriverProtocolError("Windows returned an empty SudoVDA interface path.")
            paths.append(path)
    finally:
        api.SetupDiDestroyDeviceInfoList(info_set)
    return paths


class SudoVDADriver:
    """One open SudoVDA handle; callers own persistence, keepalive, and layout.

    Use a separate instance for a heartbeat thread so a synchronous operation
    on another handle does not serialize the heartbeat at the file handle.
    Do not close an instance while one of its calls is in progress.
    """

    def __init__(self, *, _api=None) -> None:
        self._api = _api
        self._handle: int | None = None
        self._device_path: str | None = None

    @property
    def device_path(self) -> str | None:
        """Opened interface path; this is NOT a driver-generation identifier."""
        return self._device_path

    def open(self) -> SudoVDADriver:
        if self._handle is not None:
            return self
        if self._api is None:
            self._api = _Win32API()
        api = self._api
        paths = _device_paths(api)
        if not paths:
            raise DriverUnavailableError(
                "SudoVDA is unavailable: no enabled driver device was found. "
                "Install the SudoVDA driver before creating virtual monitors."
            )
        if len(paths) != 1:
            raise DriverUnavailableError(
                "Multiple enabled SudoVDA device interfaces were found. "
                "Virtumon requires exactly one SudoVDA adapter so creation, "
                "heartbeat, and removal use the same driver."
            )
        error = 0
        for path in paths:
            api.set_last_error(0)
            handle = api.CreateFileW(
                path, 0xC0000000, 3, None, 3, 0x80, None,
                # READ | WRITE; SHARE_READ | SHARE_WRITE; OPEN_EXISTING;
                # FILE_ATTRIBUTE_NORMAL, with synchronous I/O (no OVERLAPPED).
            )
            if handle not in (None, 0, _INVALID_HANDLE_VALUE):
                self._handle = handle
                self._device_path = path
                break
            error = api.get_last_error()
        if self._handle is None:
            raise _failure(api, "Opening the SudoVDA driver", DriverUnavailableError, code=error)
        try:
            major, minor, incremental, _test_build = self.protocol()
            # This is the upstream compatibility rule. TestBuild is metadata,
            # not a Windows signing or security setting.
            if major != 0 or minor < 2:
                raise DriverProtocolError(
                    f"SudoVDA protocol {major}.{minor}.{incremental} is incompatible; "
                    "Virtumon requires protocol major 0, minor 2 or newer."
                )
        except BaseException:
            try:
                self.close()
            except DriverError:
                pass  # Preserve the original protocol/connection failure.
            raise
        return self

    def close(self) -> None:
        """Release this handle; do not remove displays or disable the watchdog."""
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        self._device_path = None
        self._api.set_last_error(0)
        if not self._api.CloseHandle(handle):
            raise _failure(self._api, "Closing the SudoVDA driver")

    def __enter__(self) -> SudoVDADriver:
        return self.open()

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def _ioctl(self, code: int, parameters=None, output=None) -> None:
        if self._handle is None:
            raise DriverUnavailableError("The SudoVDA driver is not open.")
        returned = _U32()
        expected = ctypes.sizeof(output) if output is not None else 0
        self._api.set_last_error(0)
        if not self._api.DeviceIoControl(
            self._handle, code,
            ctypes.byref(parameters) if parameters is not None else None,
            ctypes.sizeof(parameters) if parameters is not None else 0,
            ctypes.byref(output) if output is not None else None,
            expected, ctypes.byref(returned), None,
        ):
            raise _failure(self._api, f"SudoVDA IOCTL 0x{code:06x}")
        if returned.value != expected:
            raise DriverProtocolError(
                f"SudoVDA IOCTL 0x{code:06x} returned {returned.value} bytes; "
                f"expected {expected}."
            )

    def protocol(self) -> tuple[int, int, int, bool]:
        output = _ProtocolOutput()
        self._ioctl(IOCTL_GET_PROTOCOL_VERSION, output=output)
        return output.Major, output.Minor, output.Incremental, bool(output.TestBuild)

    def watchdog(self) -> tuple[int, int]:
        """Return (configured timeout, remaining countdown), both in seconds.

        This query does not reset the countdown. A zero timeout disables it.
        """
        output = _WatchdogOutput()
        self._ioctl(IOCTL_GET_WATCHDOG, output=output)
        return output.Timeout, output.Countdown

    def ping(self) -> None:
        self._ioctl(IOCTL_DRIVER_PING)

    def add(
        self, monitor_uuid: str, width: int, height: int,
        refresh_millihz: int, name: str,
    ) -> NativeIdentity:
        """Create one UUID-owned monitor, returning its current Windows target.

        Existing UUIDs return the existing target without changing its mode.
        A full driver can reject even an existing UUID, so use ping(), not
        repeated add(), for keepalive. Only the initial preferred mode is set;
        desktop placement and applying a Windows display mode are separate.
        """
        for label, value in (("width", width), ("height", height),
                             ("refresh_millihz", refresh_millihz)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 0xFFFFFFFF:
                raise ValueError(f"{label} must be a positive 32-bit integer.")
        if refresh_millihz < 1000:
            raise ValueError("refresh_millihz must be at least 1000 (1 Hz).")
        identity = uuid.UUID(monitor_uuid)
        if not identity.int:
            raise ValueError("A nonzero monitor UUID is required.")
        if not isinstance(name, str):
            raise ValueError("The monitor name must be a string.")
        short_name = "".join(char if " " <= char <= "~" else "_" for char in name).strip()
        parameters = _AddParams()
        parameters.Width = width
        parameters.Height = height
        parameters.RefreshRate = refresh_millihz
        parameters.MonitorGuid = _GUID.from_buffer_copy(identity.bytes_le)
        parameters.DeviceName = (short_name or "Virtumon")[:13].encode("ascii")
        parameters.SerialNumber = identity.hex[:13].encode("ascii")
        output = _AddOutput()
        self._ioctl(IOCTL_ADD_VIRTUAL_DISPLAY, parameters, output)
        return NativeIdentity(output.AdapterLuid.LowPart, output.AdapterLuid.HighPart, output.TargetId)

    def remove(self, monitor_uuid: str) -> bool:
        """Remove exactly this UUID; return False if the driver cannot find it."""
        parameters = _RemoveParams(_as_guid(monitor_uuid))
        try:
            self._ioctl(IOCTL_REMOVE_VIRTUAL_DISPLAY, parameters)
        except DriverError as exc:
            if exc.winerror == _ERROR_NOT_FOUND:
                return False
            raise
        return True
