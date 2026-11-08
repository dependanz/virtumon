"""Separate a just-created, positively identified Windows virtual clone.

This is deliberately not a general topology editor. The caller must provide a
pre-driver-add baseline and a current ownership guard. SetDisplayConfig takes
the whole active configuration, so every unrelated path and mode is retained,
checked again before apply, and verified afterwards. A failed creation is
cleaned up by the manager's authoritative driver UUID, never by replaying an
old desktop configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from . import windows_layout as W


@dataclass
class _Clone:
    snapshot: W._Snapshot
    sources: list[W._DeviceSource]
    names: dict
    target: W._Record
    others: list[W._Record]
    interface: str


def _baseline_matches(baseline, records):
    old = {W._key(display): display for display in baseline}
    new = {W._key(record.display): record.display for record in records}
    if len(old) != len(baseline) or len(new) != len(records) or old.keys() != new.keys():
        return False
    for key, first in old.items():
        second = new[key]
        if (first.device.casefold(), first.x, first.y, first.width, first.height, first.primary,
                first.active) != (second.device.casefold(), second.x, second.y, second.width,
                                 second.height, second.primary, second.active):
            return False
        if not W._near(first.refresh_hz, second.refresh_hz):
            return False
    return True


def _desktop_mode(snapshot, path):
    index = path.targetInfo.modeInfoIdx & 0xFFFF
    if index == 0xFFFF:
        return None
    if index >= len(snapshot.modes):
        raise W.WindowsLayoutError("Invalid desktop-image index in the CCD configuration.")
    mode = snapshot.modes[index]
    if (mode.infoType != 3 or mode.id != path.targetInfo.id or
            bytes(mode.adapterId) != bytes(path.targetInfo.adapterId)):
        raise W.WindowsLayoutError("The desktop-image mode does not belong to its CCD target.")
    return mode


def _mode_signature(mode):
    if mode is None:
        return None
    value = {1: mode.sourceMode, 2: mode.targetMode, 3: mode.desktopImageInfo}[mode.infoType]
    return mode.infoType, mode.adapterId.LowPart, mode.adapterId.HighPart, mode.id, bytes(value)


def _signature(snapshot, exclude=None):
    """Compare mode contents and priority, independent of mode-array indexes."""
    result = []
    for path in snapshot.paths:
        if not path.flags & W.ACTIVE or W._path_key(path) == exclude:
            continue
        target = path.targetInfo
        result.append((
            W._source_key(path), W._path_key(path), path.flags, path.sourceInfo.statusFlags,
            target.outputTechnology, target.rotation, target.scaling,
            target.refreshRate.Numerator, target.refreshRate.Denominator,
            target.scanLineOrdering, target.targetAvailable, target.statusFlags,
            _mode_signature(W._mode(snapshot, path, True)),
            _mode_signature(W._mode(snapshot, path, False)),
            _mode_signature(_desktop_mode(snapshot, path)),
        ))
    return tuple(result)


def _gdi_signature(clone):
    # Ignore unrelated detached adapter slots, but preserve all actual desktop
    # sources and the detached source we intend to make independent.
    device = clone.target.display.device.casefold()
    return sorted((source.device.casefold(), source.flags,
                   tuple(sorted(path.casefold() for path in source.monitor_paths)))
                  for source in clone.sources
                  if source.flags & W.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP or
                  source.device.casefold() == device)


def _independent_gdi(api, clone, path):
    """The owned interface moved out of its clone host, and nowhere else did."""
    device = clone.target.display.device.casefold()
    sources = api.device_sources()
    matches = [source for source in sources if source.device.casefold() == device]
    if (len(matches) != 1 or not matches[0].flags & 1 or matches[0].flags & (4 | 8) or
            tuple(value.casefold() for value in matches[0].monitor_paths) != (clone.interface,) or
            api.target_path(path).casefold() != clone.interface):
        return False

    def other_sources(values, before):
        return sorted((source.device.casefold(), source.flags,
                       tuple(sorted(value.casefold() for value in source.monitor_paths
                                    if not before or value.casefold() != clone.interface)))
                      for source in values
                      if source.flags & 1 and source.device.casefold() != device)

    return other_sources(clone.sources, True) == other_sources(sources, False)


def _recognize(api, observation, key, baseline):
    if observation is None or not baseline or key in {W._key(display) for display in baseline}:
        return None
    snapshot, sources, names, _ = observation
    sources = [W._DeviceSource(source.device, source.flags, tuple(source.monitor_paths)) for source in sources]
    if (not snapshot.virtual_mode_aware or not snapshot.paths or
            any(not path.flags & W.ACTIVE or not path.flags & W.VIRTUAL_MODE or
                not path.targetInfo.targetAvailable for path in snapshot.paths)):
        return None
    matches = [path for path in snapshot.paths if W._path_key(path) == key]
    if len(matches) != 1:
        return None
    path = matches[0]
    source_mode = W._mode(snapshot, path, True)
    if source_mode is None or not source_mode.sourceMode.width or not source_mode.sourceMode.height:
        return None
    if any(other is not path and (W._source_key(other) == W._source_key(path) or
           other.sourceInfo.modeInfoIdx >> 16 == path.sourceInfo.modeInfoIdx >> 16)
           for other in snapshot.paths):
        return None
    names = dict(names)
    for current in snapshot.paths:
        source_key = W._source_key(current)
        if source_key not in names:
            names[source_key] = api.source_name(current)
        # Preserve known physical signal modes, not just desktop rectangles.
        signal = W._mode(snapshot, current, False)
        if (W._mode(snapshot, current, True) is None or signal is None or
                not signal.targetMode.activeSize.cx or not signal.targetMode.activeSize.cy):
            return None
        _desktop_mode(snapshot, current)
    device = names[W._source_key(path)]
    if not device:
        return None
    own_sources = [source for source in sources if source.device.casefold() == device.casefold()]
    if (len(own_sources) != 1 or own_sources[0].flags & (1 | 4 | 8) or
            own_sources[0].monitor_paths):
        return None
    interface = api.target_path(path).casefold()
    if not interface:
        return None
    hosts = [source for source in sources
             if interface in [value.casefold() for value in source.monitor_paths]]
    if (len(hosts) != 1 or not hosts[0].flags & 1 or hosts[0].flags & 8 or
            len(hosts[0].monitor_paths) < 2):
        return None
    other_snapshot = W._Snapshot([other for other in snapshot.paths if other is not path], snapshot.modes)
    try:
        others = W._records(api, other_snapshot, device_sources=sources, source_names=names)
    except W._PendingTopology:
        return None
    if not _baseline_matches(baseline, others):
        return None
    known_devices = {record.display.device.casefold() for record in others}
    attached = {source.device.casefold() for source in sources if source.flags & 1 and not source.flags & 8}
    if attached != known_devices:
        return None
    host = hosts[0]
    peers = [record for record in others if record.display.device.casefold() == host.device.casefold()]
    source = source_mode.sourceMode
    if not peers or any((record.display.x, record.display.y, record.display.width, record.display.height) !=
                        (source.position.x, source.position.y, source.width, source.height) for record in peers):
        return None
    interfaces = [api.target_path(record.path).casefold() for record in peers] + [interface]
    if (any(not value for value in interfaces) or len(set(interfaces)) != len(interfaces) or
            sorted(interfaces) != sorted(value.casefold() for value in host.monitor_paths)):
        return None
    if api.current_mode(device) is not None:
        return None
    # This record is only a placement plan: the owned target currently has no
    # independent GDI desktop, so it cannot be treated as the desktop primary.
    target = W._Record(W.NativeDisplay(device, 0, 0, 0, 0, 0.0, False, *key, active=False), path)
    W._guard_source(target, others)
    return _Clone(snapshot, sources, names, target, others, interface)


def _candidate(clone, width, height, refresh, x, y):
    original = clone.snapshot
    result = W._Snapshot([W._copy(path) for path in original.paths],
                         [W._copy(mode) for mode in original.modes])
    key = W._key(clone.target.display)
    path = next(path for path in result.paths if W._path_key(path) == key)
    mode = W._mode(result, path, True).sourceMode
    mode.width, mode.height, mode.position = width, height, W._POINT(x, y)
    path.sourceInfo.modeInfoIdx = (path.sourceInfo.modeInfoIdx & 0xFFFF0000) | 0xFFFF
    # The clone's old target timing and desktop projection describe its former
    # mirrored image. Windows must calculate these for the requested source.
    path.targetInfo.modeInfoIdx = 0xFFFFFFFF
    rate = Fraction(str(refresh)).limit_denominator(1000000)
    if not (0 < rate.numerator <= 0xFFFFFFFF and 0 < rate.denominator <= 0xFFFFFFFF):
        raise W.WindowsLayoutError("The requested refresh cannot be represented by a Windows rational.")
    path.targetInfo.refreshRate = W._RATIONAL(rate.numerator, rate.denominator)
    path.targetInfo.rotation = path.targetInfo.scaling = path.targetInfo.scanLineOrdering = 1
    return result


def extend_new_clone(api, observation, key, baseline, width, height, refresh, x, y, deadline, guard):
    """Resolve only a proven new virtual clone; return None for other mismatches."""
    W._check_guard(guard)
    if guard is None:
        return None
    clone = _recognize(api, observation, key, baseline)
    W._check_guard(guard)
    if clone is None:
        return None
    phase, attempted = "clone-prepare", False
    try:
        primary = W._geometry(clone.target, clone.others, width, height, x, y)
        candidate = _candidate(clone, width, height, refresh, x, y)
        original_signature = _signature(clone.snapshot)
        other_signature = _signature(clone.snapshot, exclude=key)
        if _signature(candidate, exclude=key) != other_signature:
            raise W.WindowsLayoutError("The clone candidate would modify another display.")
        W._check_guard(guard)
        phase = "clone-validate"
        result = api.set_config(candidate, validate=True)
        W._check_guard(guard)
        if result:
            raise W._win_error("SetDisplayConfig validation", result)
        # CCD offers no compare-and-swap. Minimize the remaining external-change
        # race by rechecking the full live topology immediately before apply.
        fresh_snapshot = api.snapshot()
        fresh_sources = api.device_sources()
        fresh = _recognize(api, (fresh_snapshot, fresh_sources, {}, None), key, baseline)
        W._check_guard(guard)
        if (fresh is None or _signature(fresh.snapshot) != original_signature or
                _gdi_signature(fresh) != _gdi_signature(clone) or fresh.interface != clone.interface or
                fresh.names != clone.names):
            raise W.WindowsLayoutError("The desktop changed while preparing the virtual clone; retry the operation.")
        phase = "clone-apply"
        W._check_guard(guard)
        attempted = True
        result = api.set_config(candidate, validate=False)
        W._check_guard(guard)
        if result:
            raise W._win_error("SetDisplayConfig apply", result)
        phase = "after-clone-apply"
        while True:
            after = W._active_records(api, deadline, guard, phase=phase)
            W._check_guard(guard)
            others = W._others(after, key)
            if not W._same_records(clone.others, others) or not _baseline_matches(baseline, others):
                raise W.WindowsLayoutError("Windows changed another display while separating the virtual clone.")
            readback = api.snapshot()
            W._check_guard(guard)
            if _signature(readback, exclude=key) != other_signature:
                raise W.WindowsLayoutError("Windows changed another display's CCD mode while separating the virtual clone.")
            actual = W._active_target(after, key)
            raw_paths = [path for path in readback.paths if path.flags & W.ACTIVE and W._path_key(path) == key]
            if actual is not None and len(raw_paths) == 1:
                W._guard_source(actual, after)
                raw = raw_paths[0]
                signal = W._mode(readback, raw, False)
                source = W._mode(readback, raw, True)
                # Matching desktop pixels alone could conceal scaling into the
                # clone's old 4K signal. Require the requested actual mode too.
                if (W._requested(actual.display, width, height, refresh, x, y, primary) and
                        W._source_key(raw) == W._source_key(clone.target.path) and
                        actual.display.device.casefold() == clone.target.display.device.casefold() and
                        raw.targetInfo.rotation == 1 and signal is not None and
                        source is not None and
                        (source.sourceMode.width, source.sourceMode.height,
                         source.sourceMode.position.x, source.sourceMode.position.y) == (width, height, x, y) and
                        (signal.targetMode.activeSize.cx, signal.targetMode.activeSize.cy) == (width, height) and
                        signal.targetMode.scanLineOrdering == 1 and
                        W._near(W._refresh_rate(signal.targetMode.vSyncFreq) or 0, refresh) and
                        _independent_gdi(api, clone, raw)):
                    W._check_guard(guard)
                    return after
            if not W._pause(deadline):
                raise W.WindowsLayoutError("Windows did not separate the virtual clone at the requested desktop and signal mode.")
    except W.WindowsLayoutError as exc:
        if exc.diagnostics is None:
            exc.diagnostics = W._metadata_report(api, observation, str(exc), phase)
        exc.diagnostics["clone_extension"] = {
            "phase": phase, "apply_attempted": attempted,
            "requested": {"width": width, "height": height, "refresh_hz": refresh, "position": [x, y]},
            "cleanup": "The manager removes only its driver UUID; no saved desktop snapshot is replayed.",
        }
        raise
