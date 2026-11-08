"""Targeted Windows display layout changes using current-session CCD identity.

Identity is the complete adapter LUID and target ID, not a persistent ownership
token. The backend must establish ownership before calling this module.
Ordinary changes address a named GDI source. Creation can also separate a
verified virtual clone using a supplied CCD configuration that preserves every
other path. No topology presets or registry-persisting changes are used.
"""

from __future__ import annotations

import ctypes as C
import math
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from .displays import DisplayDiscoveryError

U16, U32, I16, I32, U64 = C.c_uint16, C.c_uint32, C.c_int16, C.c_int32, C.c_uint64
ACTIVE, VIRTUAL_MODE = 1, 8
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP, DISPLAY_DEVICE_PRIMARY_DEVICE = 1, 4
QDC_ALL_PATHS, QDC_ONLY_ACTIVE_PATHS, QDC_VIRTUAL_MODE_AWARE = 1, 2, 0x10
DM_POSITION, DM_PELSWIDTH, DM_PELSHEIGHT, DM_DISPLAYFREQUENCY = 0x20, 0x80000, 0x100000, 0x400000
CDS_TEST = 2
SDC_USE_SUPPLIED_DISPLAY_CONFIG, SDC_VALIDATE, SDC_APPLY = 0x20, 0x40, 0x80
SDC_VIRTUAL_MODE_AWARE = 0x8000
_lock = threading.RLock()


class WindowsLayoutError(DisplayDiscoveryError):
    """A display could not be safely identified, changed, or verified."""

    winerror: int | None = None
    diagnostics: dict | None = None


class _UnsafeTarget(WindowsLayoutError):
    pass


class _OwnershipUnverified(WindowsLayoutError):
    pass


class _PendingTopology(WindowsLayoutError):
    """CCD and GDI metadata do not yet describe one consistent desktop."""

    observation: tuple | None = None


def _check_guard(guard: Callable[[], bool] | None) -> None:
    if guard is None:
        return
    try:
        current = guard()
    except Exception as exc:
        raise _OwnershipUnverified(
            "Current-session ownership is unverified because its guard failed; "
            "no further native display changes are permitted."
        ) from exc
    if not current:
        raise _OwnershipUnverified(
            "Current-session ownership is unverified; no further native display changes are permitted."
        )


class DisplayIdentity(Protocol):
    adapter_low: int
    adapter_high: int
    target_id: int


@dataclass(frozen=True)
class NativeDisplay:
    device: str
    x: int
    y: int
    width: int
    height: int
    refresh_hz: float
    primary: bool
    adapter_low: int
    adapter_high: int
    target_id: int
    active: bool = True


# Fixed-width Windows types (including UTF-16 arrays) keep ABI tests meaningful
# on Linux. Handles/pointers are declared separately at their native pointer size.
class _LUID(C.Structure):
    _fields_ = [("LowPart", U32), ("HighPart", I32)]


class _POINT(C.Structure):
    _fields_ = [("x", I32), ("y", I32)]


class _RECT(C.Structure):
    _fields_ = [(name, I32) for name in ("left", "top", "right", "bottom")]


class _RATIONAL(C.Structure):
    _fields_ = [("Numerator", U32), ("Denominator", U32)]


class _REGION(C.Structure):
    _fields_ = [("cx", U32), ("cy", U32)]


class _SOURCE_MODE(C.Structure):
    _fields_ = [("width", U32), ("height", U32), ("pixelFormat", U32), ("position", _POINT)]


class _SIGNAL(C.Structure):
    _fields_ = [("pixelRate", U64), ("hSyncFreq", _RATIONAL), ("vSyncFreq", _RATIONAL),
                ("activeSize", _REGION), ("totalSize", _REGION),
                ("videoStandard", U32), ("scanLineOrdering", U32)]


class _DESKTOP_IMAGE(C.Structure):
    _fields_ = [("PathSourceSize", _POINT), ("DesktopImageRegion", _RECT), ("DesktopImageClip", _RECT)]


class _MODE_UNION(C.Union):
    _fields_ = [("sourceMode", _SOURCE_MODE), ("targetMode", _SIGNAL), ("desktopImageInfo", _DESKTOP_IMAGE)]


class _MODE_INFO(C.Structure):
    _anonymous_ = ("mode",)
    _fields_ = [("infoType", U32), ("id", U32), ("adapterId", _LUID), ("mode", _MODE_UNION)]


class _PATH_SOURCE(C.Structure):
    _fields_ = [("adapterId", _LUID), ("id", U32), ("modeInfoIdx", U32), ("statusFlags", U32)]


class _PATH_TARGET(C.Structure):
    _fields_ = [("adapterId", _LUID), ("id", U32), ("modeInfoIdx", U32),
                ("outputTechnology", U32), ("rotation", U32), ("scaling", U32),
                ("refreshRate", _RATIONAL), ("scanLineOrdering", U32),
                ("targetAvailable", I32), ("statusFlags", U32)]


class _PATH_INFO(C.Structure):
    _fields_ = [("sourceInfo", _PATH_SOURCE), ("targetInfo", _PATH_TARGET), ("flags", U32)]


class _INFO_HEADER(C.Structure):
    _fields_ = [("type", U32), ("size", U32), ("adapterId", _LUID), ("id", U32)]


class _SOURCE_NAME(C.Structure):
    _fields_ = [("header", _INFO_HEADER), ("viewGdiDeviceName", U16 * 32)]


class _TARGET_NAME(C.Structure):
    _fields_ = [("header", _INFO_HEADER), ("flags", U32), ("outputTechnology", U32),
                ("edidManufactureId", U16), ("edidProductCodeId", U16),
                ("connectorInstance", U32), ("monitorFriendlyDeviceName", U16 * 64),
                ("monitorDevicePath", U16 * 128)]


class _DISPLAY_DEVICE(C.Structure):
    _fields_ = [("cb", U32), ("DeviceName", U16 * 32), ("DeviceString", U16 * 128),
                ("StateFlags", U32), ("DeviceID", U16 * 128), ("DeviceKey", U16 * 128)]


class _DEVMODE(C.Structure):
    # The display arm of DEVMODEW's first union occupies exactly 16 bytes.
    _fields_ = [("dmDeviceName", U16 * 32), ("dmSpecVersion", U16),
                ("dmDriverVersion", U16), ("dmSize", U16), ("dmDriverExtra", U16),
                ("dmFields", U32), ("dmPosition", _POINT),
                ("dmDisplayOrientation", U32), ("dmDisplayFixedOutput", U32),
                ("printerFields", I16 * 5), ("dmFormName", U16 * 32), ("dmLogPixels", U16)] + [
        (name, U32) for name in ("dmBitsPerPel", "dmPelsWidth", "dmPelsHeight", "dmDisplayFlags",
                                "dmDisplayFrequency", "dmICMMethod", "dmICMIntent", "dmMediaType",
                                "dmDitherType", "dmReserved1", "dmReserved2", "dmPanningWidth", "dmPanningHeight")
    ]


@dataclass
class _Snapshot:
    paths: list[_PATH_INFO]
    modes: list[_MODE_INFO]
    virtual_mode_aware: bool = True


@dataclass
class _DeviceSource:
    device: str
    flags: int
    monitor_paths: tuple[str, ...]


@dataclass
class _Record:
    display: NativeDisplay
    path: _PATH_INFO


def _text(value) -> str:
    return bytes(value).decode("utf-16-le").split("\0", 1)[0]


def _copy(value):
    return type(value).from_buffer_copy(bytes(value))


def _key(identity: DisplayIdentity) -> tuple[int, int, int]:
    values = (identity.adapter_low, identity.adapter_high, identity.target_id)
    limits = ((0, 0xFFFFFFFF), (-0x80000000, 0x7FFFFFFF), (0, 0xFFFFFFFF))
    if any(isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high
           for v, (low, high) in zip(values, limits)):
        raise WindowsLayoutError("Invalid native display identity; expected a uint32/int32 LUID and uint32 target ID.")
    return values


def _path_key(path: _PATH_INFO) -> tuple[int, int, int]:
    target = path.targetInfo
    return target.adapterId.LowPart, target.adapterId.HighPart, target.id


def _source_key(path: _PATH_INFO) -> tuple[int, int, int]:
    source = path.sourceInfo
    return source.adapterId.LowPart, source.adapterId.HighPart, source.id


def _win_error(operation: str, code: int) -> WindowsLayoutError:
    detail = C.FormatError(code).strip() if hasattr(C, "FormatError") else f"Windows error {code}"
    if code == 5:
        detail += "; run Virtumon in the signed-in Windows desktop session"
    error = WindowsLayoutError(f"{operation} failed ({code}): {detail}")
    error.winerror = code
    return error


class _Win32:
    def __init__(self, dll=None):
        if dll is None:
            if sys.platform != "win32":
                raise WindowsLayoutError("Native display layout is available on Windows only.")
            try:
                dll = C.WinDLL("user32", use_last_error=True)
            except OSError as exc:
                raise WindowsLayoutError(f"Unable to load Windows display APIs: {exc}") from exc
        self.dll = dll
        signatures = {
            "GetDisplayConfigBufferSizes": (I32, [U32, C.POINTER(U32), C.POINTER(U32)]),
            "QueryDisplayConfig": (I32, [U32, C.POINTER(U32), C.POINTER(_PATH_INFO),
                                         C.POINTER(U32), C.POINTER(_MODE_INFO), C.POINTER(U32)]),
            "SetDisplayConfig": (I32, [U32, C.POINTER(_PATH_INFO), U32, C.POINTER(_MODE_INFO), U32]),
            "DisplayConfigGetDeviceInfo": (I32, [C.POINTER(_INFO_HEADER)]),
            "EnumDisplayDevicesW": (I32, [C.c_wchar_p, U32, C.POINTER(_DISPLAY_DEVICE), U32]),
            "EnumDisplaySettingsExW": (I32, [C.c_wchar_p, U32, C.POINTER(_DEVMODE), U32]),
            "ChangeDisplaySettingsExW": (I32, [C.c_wchar_p, C.POINTER(_DEVMODE), C.c_void_p, U32, C.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(dll, name)
            function.restype, function.argtypes = result, arguments

    def snapshot(self, active_only: bool = True, *, virtual_mode_aware: bool = True) -> _Snapshot:
        flags = QDC_ONLY_ACTIVE_PATHS if active_only else QDC_ALL_PATHS
        if virtual_mode_aware:
            flags |= QDC_VIRTUAL_MODE_AWARE
        for _ in range(8):
            count_paths, count_modes = U32(), U32()
            result = self.dll.GetDisplayConfigBufferSizes(flags, C.byref(count_paths), C.byref(count_modes))
            if result:
                raise _win_error("GetDisplayConfigBufferSizes", result)
            paths = (_PATH_INFO * max(1, count_paths.value))()
            modes = (_MODE_INFO * max(1, count_modes.value))()
            result = self.dll.QueryDisplayConfig(flags, C.byref(count_paths), paths,
                                                  C.byref(count_modes), modes, None)
            if result == 122:  # Hotplug changed the required buffer size.
                continue
            if result:
                raise _win_error("QueryDisplayConfig", result)
            return _Snapshot([_copy(p) for p in paths[:count_paths.value]],
                             [_copy(m) for m in modes[:count_modes.value]],
                             virtual_mode_aware=virtual_mode_aware)
        raise WindowsLayoutError("Display topology kept changing during discovery; retry once it settles.")

    def _name(self, path, target=False):
        packet = _TARGET_NAME() if target else _SOURCE_NAME()
        info = path.targetInfo if target else path.sourceInfo
        packet.header = _INFO_HEADER(2 if target else 1, C.sizeof(packet), info.adapterId, info.id)
        result = self.dll.DisplayConfigGetDeviceInfo(C.byref(packet.header))
        if result:
            raise _win_error("DisplayConfigGetDeviceInfo", result)
        return _text(packet.monitorDevicePath if target else packet.viewGdiDeviceName)

    def source_name(self, path):
        return self._name(path)

    def target_path(self, path):
        return self._name(path, target=True)

    def device_sources(self) -> list[_DeviceSource]:
        sources = []
        index = 0
        while True:
            device = _DISPLAY_DEVICE()
            device.cb = C.sizeof(device)
            if not self.dll.EnumDisplayDevicesW(None, index, C.byref(device), 0):
                break
            name, monitors = _text(device.DeviceName), []
            monitor_index = 0
            while True:
                monitor = _DISPLAY_DEVICE()
                monitor.cb = C.sizeof(monitor)
                if not self.dll.EnumDisplayDevicesW(name, monitor_index, C.byref(monitor), 1):
                    break
                monitors.append(_text(monitor.DeviceID))
                monitor_index += 1
            sources.append(_DeviceSource(name, device.StateFlags, tuple(monitors)))
            index += 1
        return sources

    def current_mode(self, device: str) -> _DEVMODE | None:
        mode = _DEVMODE()
        mode.dmSize = C.sizeof(mode)
        if self.dll.EnumDisplaySettingsExW(device, 0xFFFFFFFF, C.byref(mode), 0):
            return mode
        return None

    def modes(self, device: str) -> list[_DEVMODE]:
        modes = []
        for index in range(16384):
            mode = _DEVMODE()
            mode.dmSize = C.sizeof(mode)
            if not self.dll.EnumDisplaySettingsExW(device, index, C.byref(mode), 0):
                return modes
            modes.append(mode)
        raise WindowsLayoutError(f"The mode list for {device} did not terminate.")

    def change_mode(self, device: str, mode: _DEVMODE, flags: int) -> int:
        return self.dll.ChangeDisplaySettingsExW(device, C.byref(mode), None, flags, None)

    def set_config(self, snapshot: _Snapshot, *, validate: bool) -> int:
        if not snapshot.virtual_mode_aware or not snapshot.paths:
            raise WindowsLayoutError("A nonempty virtual-aware CCD configuration is required.")
        paths = (_PATH_INFO * len(snapshot.paths))(*snapshot.paths)
        modes = (_MODE_INFO * len(snapshot.modes))(*snapshot.modes)
        # Deliberately omit ALLOW_CHANGES, SAVE_TO_DATABASE, topology presets,
        # and forced mode enumeration. Unrelated supplied modes must stay fixed.
        flags = (SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_VIRTUAL_MODE_AWARE |
                 (SDC_VALIDATE if validate else SDC_APPLY))
        return self.dll.SetDisplayConfig(len(paths), paths, len(modes), modes, flags)


def _get_api():
    return _Win32()


def _mode(snapshot, path, source: bool):
    info = path.sourceInfo if source else path.targetInfo
    index = info.modeInfoIdx
    # QDC_VIRTUAL_MODE_AWARE determines the returned union layout. Windows can
    # return packed indexes even when PATH_SUPPORT_VIRTUAL_MODE is absent on an
    # active path (observed on the native Windows CI display).
    if snapshot.virtual_mode_aware:
        index >>= 16
        if index == 0xFFFF:
            return None
    if index >= len(snapshot.modes):
        return None
    mode = snapshot.modes[index]
    if (mode.infoType != (1 if source else 2) or mode.id != info.id or
            bytes(mode.adapterId) != bytes(info.adapterId)):
        return None
    return mode


def _refresh_rate(rate: _RATIONAL) -> float | None:
    numerator, denominator = rate.Numerator, rate.Denominator
    # Some display drivers report unavailable signal timing as unsigned -2/-2
    # (or -1). Dividing that placeholder would falsely report a valid 1 Hz.
    invalid = (0, 0xFFFFFFFE, 0xFFFFFFFF)
    if numerator in invalid or denominator in invalid:
        return None
    return numerator / denominator


def _records(api, snapshot: _Snapshot, *, device_sources=None, source_names=None) -> list[_Record]:
    records, names = [], dict(source_names or {})
    active_paths = [path for path in snapshot.paths if path.flags & ACTIVE]
    if device_sources is None:
        device_sources = api.device_sources()

    def pending(message, problem_device=None):
        error = _PendingTopology(message)
        # Retain the same observations that rejected the layout. Serialization
        # and supplemental reads happen only once, if the retries time out.
        error.observation = (snapshot, device_sources, dict(names), problem_device)
        return error

    if not active_paths:
        if any(source.flags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP and not source.flags & 8
               for source in device_sources):
            raise pending("CCD reports no active paths while GDI still reports an attached display.")
        return records
    sources = {}
    for source in device_sources:
        sources.setdefault(source.device.casefold(), []).append(source)
    for path in active_paths:
        source = _mode(snapshot, path, source=True)
        if source is None or not source.sourceMode.width or not source.sourceMode.height:
            raise WindowsLayoutError("An active display has no valid CCD source geometry; no changes were made.")
        source_key = _source_key(path)
        if source_key not in names:
            names[source_key] = api.source_name(path)
        if not names[source_key]:
            raise WindowsLayoutError("Windows returned an empty GDI display name.")
        matches = sources.get(names[source_key].casefold(), [])
        if len(matches) != 1 or not matches[0].flags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            flags = ", ".join(hex(match.flags) for match in matches) or "none"
            raise pending(
                f"Windows has not reported one attached GDI source for {names[source_key]} "
                f"(matches={len(matches)}; flags={flags}).",
                names[source_key],
            )
        # A hotplug CCD source position alone is not sufficient evidence of
        # primary status. Use Windows' explicit GDI flag for the matched source;
        # never guess from (0, 0) when deciding whether a move is safe.
        primary = bool(matches[0].flags & DISPLAY_DEVICE_PRIMARY_DEVICE)
        mode = source.sourceMode
        target = _mode(snapshot, path, source=False)
        refresh = _refresh_rate(target.targetMode.vSyncFreq) if target is not None else None
        if refresh is None:
            refresh = _refresh_rate(path.targetInfo.refreshRate)
        if refresh is None:
            refresh = 0.0
        display = NativeDisplay(names[source_key], mode.position.x, mode.position.y,
                                mode.width, mode.height, refresh, primary, *_path_key(path))
        records.append(_Record(display, path))
    primary_sources = {record.display.device.casefold() for record in records if record.display.primary}
    if len(primary_sources) != 1:
        raise pending(
            f"Windows reported {len(primary_sources)} primary GDI sources for the active desktop; expected one."
        )
    return records


def _active_target(records, key):
    matches = [record for record in records if _key(record.display) == key]
    if len(matches) > 1:
        raise _UnsafeTarget("The native target has multiple active paths; refusing an ambiguous layout change.")
    return matches[0] if matches else None


def _present_paths(snapshot, key):
    return [p for p in snapshot.paths if _path_key(p) == key and
            (p.targetInfo.targetAvailable or p.flags & ACTIVE)]


def _guard_source(target: _Record, records):
    for record in records:
        if _key(record.display) == _key(target.display):
            continue
        if (_source_key(record.path) == _source_key(target.path) or
                record.display.device.casefold() == target.display.device.casefold()):
            raise _UnsafeTarget("The virtual monitor shares a GDI source with another display (clone mode). "
                                "Extend this virtual monitor in Windows Display settings before retrying.")


def _inactive_target(api, snapshot, records, key) -> _Record:
    paths = _present_paths(snapshot, key)
    if not paths:
        raise _UnsafeTarget("The virtual monitor is no longer present.")
    interface = api.target_path(paths[0]).casefold()
    if not interface:
        raise _UnsafeTarget("Windows has not supplied a monitor interface path for the inactive target.")
    matches = [s for s in api.device_sources()
               if interface in [p.casefold() for p in s.monitor_paths]]
    if len(matches) != 1 or len(matches[0].monitor_paths) != 1 or matches[0].flags & (1 | 8):
        raise _UnsafeTarget("The inactive target has no unique, unused GDI monitor association. "
                            "Enable only this virtual monitor in Windows Display settings, then retry.")
    device = matches[0].device
    candidates = {}
    for path in paths:
        if api.source_name(path).casefold() == device.casefold():
            candidates[_source_key(path)] = path
    if len(candidates) != 1:
        raise _UnsafeTarget("The inactive target maps to multiple possible display sources; "
                            "enable only this virtual monitor in Windows Display settings, then retry.")
    path = next(iter(candidates.values()))
    target = _Record(NativeDisplay(device, 0, 0, 0, 0, 0.0, False, *key, active=False), path)
    _guard_source(target, records)
    return target


def _deadline(timeout):
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
        raise WindowsLayoutError("Timeout must be a finite, nonnegative number.")
    return time.monotonic() + timeout


def _pause(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(0.1, remaining))
    return True


def _metadata_report(api, observation, reason, phase):
    """Best-effort failure evidence; never replace the operation's exception."""
    try:
        from .windows_diagnostics import capture_metadata, enrich_metadata
        snapshot, sources, names, problem_device = observation
        report = capture_metadata(snapshot, sources, names, reason=reason,
                                  problem_device=problem_device)
        report["phase"] = phase
        try:
            return enrich_metadata(report, api)
        except Exception as exc:
            report["enrichment_error"] = str(exc)
            return report
    except Exception as exc:
        return {"phase": phase, "reason": reason, "capture_error": str(exc)}


def _active_records(api, deadline, guard=None, *, phase="discovery", pending_handler=None):
    while True:
        _check_guard(guard)
        try:
            # Re-read both APIs after a transient mismatch. Retrying GDI against
            # an old CCD snapshot could otherwise accept stale source metadata.
            records = _records(api, api.snapshot())
        except _PendingTopology as exc:
            _check_guard(guard)
            if pending_handler is not None:
                resolved = pending_handler(exc)
                if resolved is not None:
                    _check_guard(guard)
                    return resolved
            if not _pause(deadline):
                error = WindowsLayoutError(
                    f"Timed out waiting for consistent Windows display metadata. Latest error: {exc}"
                )
                if exc.observation is not None:
                    error.diagnostics = _metadata_report(api, exc.observation, str(exc), phase)
                raise error from exc
            continue
        _check_guard(guard)
        return records


def _wait(api, key, deadline, guard=None, *, pending_handler=None):
    while True:
        options = {"pending_handler": pending_handler} if pending_handler is not None else {}
        records = _active_records(api, deadline, guard, phase="arrival", **options)
        target = _active_target(records, key)
        if target is not None:
            return target.display
        all_paths = api.snapshot(active_only=False)
        if _present_paths(all_paths, key):
            try:
                return _inactive_target(api, all_paths, records, key).display
            except WindowsLayoutError:
                # Presence was proved above. Missing source metadata is an
                # inactive/pending result; configure_display reports its cause.
                return NativeDisplay("", 0, 0, 0, 0, 0.0, False, *key, active=False)
        if not _pause(deadline):
            raise WindowsLayoutError("Timed out waiting for the backend's exact adapter LUID and target ID.")


def list_native_displays() -> list[NativeDisplay]:
    """Return active targets with physical-pixel geometry and actual refresh.

    A refresh of 0 means Windows did not report a usable refresh rational.
    Cloned targets are listed individually; they cannot be configured here.
    """
    api = _get_api()
    # Leave headroom within the managed-list client's ten-second RPC timeout.
    return sorted((r.display for r in _active_records(api, _deadline(5), phase="list-active")),
                  key=lambda d: (not d.primary, d.x, d.y, d.device, d.target_id))


def wait_for_display(identity: DisplayIdentity, timeout: float = 10) -> NativeDisplay:
    """Wait for arrival, including inactive targets.

    Inactive results have active=False and zero geometry. Their device is empty
    if Windows has not exposed an unambiguous independent GDI source yet.
    """
    return _wait(_get_api(), _key(identity), _deadline(timeout))


def is_display_present(identity: DisplayIdentity) -> bool:
    """Check current connected/active paths, including an inactive new target."""
    return bool(_present_paths(_get_api().snapshot(active_only=False), _key(identity)))


def _near(a, b):
    return math.isclose(a, b, rel_tol=0, abs_tol=0.02)


def _same_records(first, second):
    left, right = ({_key(r.display): r for r in records} for records in (first, second))
    if left.keys() != right.keys() or len(left) != len(first) or len(right) != len(second):
        return False
    for key, a in left.items():
        b = right[key]
        da, db = a.display, b.display
        if (da.device, da.x, da.y, da.width, da.height, da.primary, _source_key(a.path),
                a.path.targetInfo.rotation, a.path.targetInfo.scaling) != (
                db.device, db.x, db.y, db.width, db.height, db.primary, _source_key(b.path),
                b.path.targetInfo.rotation, b.path.targetInfo.scaling) or not _near(da.refresh_hz, db.refresh_hz):
            return False
    return True


def _others(records, key):
    return [r for r in records if _key(r.display) != key]


def _geometry(target, records, width, height, x, y):
    for value, name in ((width, "width"), (height, "height"), (x, "x"), (y, "y")):
        if isinstance(value, bool) or not isinstance(value, int):
            raise WindowsLayoutError(f"Display {name} must be an integer.")
    if not (0 < width <= 0x7FFFFFFF and 0 < height <= 0x7FFFFFFF and
            -0x80000000 <= x <= x + width <= 0x7FFFFFFF and
            -0x80000000 <= y <= y + height <= 0x7FFFFFFF):
        raise WindowsLayoutError("Display geometry is outside the Windows coordinate range.")
    others = _others(records, _key(target.display))
    expected_primary = target.display.primary if target.display.active else not others
    if ((x, y) == (0, 0)) != expected_primary:
        raise WindowsLayoutError(
            "That position would change the primary display "
            f"(target={target.display.device}; active={target.display.active}; "
            f"Windows primary={target.display.primary}; "
            f"current=({target.display.x}, {target.display.y}); requested=({x}, {y})). "
            "Choose an extended position."
        )
    rectangles = {(r.display.x, r.display.y, r.display.x + r.display.width,
                   r.display.y + r.display.height) for r in others}
    rectangles = [(x, y, x + width, y + height), *rectangles]
    edges = [set() for _ in rectangles]
    for i, a in enumerate(rectangles):
        for j in range(i):
            b = rectangles[j]
            horizontal = min(a[2], b[2]) - max(a[0], b[0])
            vertical = min(a[3], b[3]) - max(a[1], b[1])
            if horizontal > 0 and vertical > 0:
                raise WindowsLayoutError("The requested display rectangle overlaps another display.")
            if ((a[2] == b[0] or b[2] == a[0]) and vertical > 0 or
                    (a[3] == b[1] or b[3] == a[1]) and horizontal > 0):
                edges[i].add(j)
                edges[j].add(i)
    reached, pending = set(), [0]
    while pending:
        node = pending.pop()
        if node not in reached:
            reached.add(node)
            pending.extend(edges[node] - reached)
    if len(reached) != len(rectangles):
        raise WindowsLayoutError("The requested layout leaves a gap. Displays must share an edge; "
                                 "Windows could otherwise reposition the physical displays.")
    return expected_primary


def _requested(display, width, height, refresh, x, y, primary):
    return (display.active and display.refresh_hz > 0 and
            (display.width, display.height, display.x, display.y, display.primary) ==
            (width, height, x, y, primary) and _near(display.refresh_hz, refresh))


def _select_mode(api, target, current, width, height, refresh, x, y):
    display = target.display
    if (current is not None and display.refresh_hz > 0 and
            (display.width, display.height) == (width, height) and _near(display.refresh_hz, refresh)):
        mode = _copy(current)
        mode.dmFields = DM_POSITION  # Moving need not reselect a nominal refresh mode.
    else:
        if not math.isclose(refresh, round(refresh), rel_tol=0, abs_tol=1e-6) or round(refresh) < 1:
            raise WindowsLayoutError("Changing to a fractional refresh rate is not supported by the targeted "
                                     "DEVMODE API. An already-active fractional mode can be repositioned.")
        depth = current.dmBitsPerPel if current is not None and current.dmBitsPerPel else 32
        options = [m for m in api.modes(display.device)
                   if (m.dmPelsWidth, m.dmPelsHeight, m.dmDisplayFrequency, m.dmBitsPerPel) ==
                   (width, height, round(refresh), depth)]
        if not options:
            raise WindowsLayoutError(f"{display.device} does not advertise {width}x{height} at {refresh:g} Hz. "
                                     "Add that mode to the virtual display backend before retrying.")
        if current is not None:
            options = [m for m in options if m.dmDisplayFlags == current.dmDisplayFlags] or options
        mode = _copy(options[0])
        mode.dmFields |= DM_POSITION | DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    mode.dmPosition = _POINT(x, y)
    return mode


def _change_error(result):
    names = {1: "restart required", -1: "driver rejected the change", -2: "unsupported mode",
             -3: "settings could not be updated", -4: "invalid flags", -5: "invalid parameters", -6: "DualView restriction"}
    return WindowsLayoutError(f"ChangeDisplaySettingsExW failed ({result}): {names.get(result, 'unknown result')}.")


def _rollback(api, before, target, previous, guard=None):
    key = _key(target.display)
    attempted = False
    try:
        _check_guard(guard)
        now = _records(api, api.snapshot())
        _check_guard(guard)
        if _same_records(before, now):
            return "The original layout is unchanged."
        if not _same_records(_others(before, key), _others(now, key)):
            return "Other displays changed; no broad rollback was attempted."
        actual = _active_target(now, key)
        if actual is None:
            return "The target is inactive; no other display was changed by rollback."
        _guard_source(actual, now)
        if actual.display.device.casefold() != target.display.device.casefold():
            return "The GDI source changed; rollback was not attempted."
        restore = _copy(previous) if previous is not None else _DEVMODE()
        if previous is None:
            restore.dmSize = C.sizeof(restore)
            restore.dmFields = DM_POSITION | DM_PELSWIDTH | DM_PELSHEIGHT
            restore.dmPosition = _POINT(actual.display.x, actual.display.y)
        _check_guard(guard)
        attempted = True
        result = api.change_mode(actual.display.device, restore, 0)
        _check_guard(guard)
        if result:
            return f"Restoring only the target failed ({result})."
        restored = _records(api, api.snapshot())
        _check_guard(guard)
        return ("The original layout was restored." if _same_records(before, restored)
                else "Restoring only the target was requested, but the result could not be verified.")
    except _OwnershipUnverified:
        if attempted:
            return "Current-session ownership became unverified during target-only rollback; no further native changes were attempted."
        return "Target-only rollback skipped: current-session ownership is unverified."
    except WindowsLayoutError as exc:
        return f"Target-only rollback could not be completed: {exc}"


def configure_display(identity: DisplayIdentity, width: int, height: int, refresh_hz: float,
                      x: int, y: int, timeout: float = 10, *,
                      guard: Callable[[], bool] | None = None,
                      creation_baseline: list[NativeDisplay] | None = None) -> NativeDisplay:
    """Set and verify one owned monitor while preserving all other active paths.

    An inactive target is attached only when its exact monitor interface is
    associated with one unused GDI source. Inactive source metadata may settle
    within the same timeout used for arrival and verification. Changes are not
    saved to the Windows persistence database. During creation only, a supplied
    pre-add baseline and live guard allow a verified virtual clone to become an
    independent source. That operation preserves all other CCD paths and modes;
    failure relies on the manager's exact-GUID cleanup, never a desktop replay.
    Ordinary GDI failure attempts a guarded rollback of this target. A guard is
    checked throughout discovery, application, and rollback. Once it fails,
    this operation performs no further native display changes.
    """
    key, deadline = _key(identity), _deadline(timeout)
    if (isinstance(refresh_hz, bool) or not isinstance(refresh_hz, (int, float)) or
            not math.isfinite(refresh_hz) or not 0 < refresh_hz <= 0xFFFFFFFF):
        raise WindowsLayoutError("Refresh rate must be a finite, positive number.")
    _check_guard(guard)
    with _lock:
        _check_guard(guard)
        api = _get_api()
        pending_options = {}
        if creation_baseline is not None and guard is not None:
            from .windows_clone import extend_new_clone
            clone_resolved = False

            def pending_handler(error):
                nonlocal clone_resolved
                if clone_resolved:
                    return None
                resolved = extend_new_clone(api, error.observation, key, creation_baseline,
                                            width, height, refresh_hz, x, y, deadline, guard)
                clone_resolved = resolved is not None
                return resolved

            pending_options["pending_handler"] = pending_handler
        _wait(api, key, deadline, guard, **pending_options)
        while True:
            # Arrival can precede GDI source association. Re-read both active
            # topology and inactive candidates instead of reusing stale paths.
            _check_guard(guard)
            before = _active_records(api, deadline, guard, phase="prepare", **pending_options)
            _check_guard(guard)
            target = _active_target(before, key)
            if target is not None:
                break
            try:
                target = _inactive_target(api, api.snapshot(active_only=False), before, key)
                break
            except _UnsafeTarget as exc:
                _check_guard(guard)
                if not _pause(deadline):
                    raise WindowsLayoutError(
                        f"Timed out waiting for a safe independent GDI source. Latest resolution error: {exc}"
                    ) from exc
        _guard_source(target, before)
        primary = _geometry(target, before, width, height, x, y)
        if _requested(target.display, width, height, refresh_hz, x, y, primary):
            _check_guard(guard)
            return target.display
        previous = api.current_mode(target.display.device) if target.display.active else None
        if target.display.active and previous is None:
            raise WindowsLayoutError("Cannot save the target's current mode for rollback; no changes were made.")
        if previous is not None:
            required = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
            if previous.dmFields & required != required:
                raise WindowsLayoutError("Windows did not provide a complete current mode for rollback; no changes were made.")
            previous.dmPosition = _POINT(target.display.x, target.display.y)
            previous.dmFields |= DM_POSITION
        mode = _select_mode(api, target, previous, width, height, refresh_hz, x, y)
        _check_guard(guard)
        result = api.change_mode(target.display.device, mode, CDS_TEST)
        _check_guard(guard)
        if result:
            raise _change_error(result)
        fresh = _active_records(api, deadline, guard, phase="after-mode-test")
        _check_guard(guard)
        if not _same_records(before, fresh):
            raise WindowsLayoutError("The desktop changed while preparing the mode; retry the operation.")
        if not target.display.active:
            resolved = _inactive_target(api, api.snapshot(active_only=False), fresh, key)
            if (_source_key(resolved.path), resolved.display.device) != (_source_key(target.path), target.display.device):
                raise WindowsLayoutError("The inactive target's source changed; retry the operation.")
        try:
            _check_guard(guard)
            result = api.change_mode(target.display.device, mode, 0)
            _check_guard(guard)
            if result:
                raise _change_error(result)
            while True:
                _check_guard(guard)
                after = _active_records(api, deadline, guard, phase="after-mode-apply")
                _check_guard(guard)
                if not _same_records(_others(before, key), _others(after, key)):
                    raise WindowsLayoutError("Windows changed another display while applying the requested layout.")
                actual = _active_target(after, key)
                if actual is not None:
                    _guard_source(actual, after)
                    if _requested(actual.display, width, height, refresh_hz, x, y, primary):
                        _check_guard(guard)
                        return actual.display
                if not _pause(deadline):
                    raise WindowsLayoutError("Windows did not reach the requested resolution, refresh, position, and primary status.")
        except _OwnershipUnverified:
            # A later True cannot re-authorize an identity from the lost
            # generation. The manager can clean up by authoritative GUID.
            raise
        except WindowsLayoutError as exc:
            error = WindowsLayoutError(f"{exc} {_rollback(api, before, target, previous, guard)}")
            error.diagnostics = exc.diagnostics
            raise error from exc
