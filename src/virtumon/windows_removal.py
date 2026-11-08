"""Restore one surviving Virtumon after exact-GUID removal of another.

On the observed Windows 11/SudoVDA combination, removing one independent
virtual output can re-clone the remaining one onto the physical primary.
This narrow helper permits a guarded CCD repair of the *surviving owned
target*, never an arbitrary display. It requires a validated pre-removal
desktop snapshot and rejects topology or physical-signal drift.

No driver IOCTL is issued here. Native removal has already occurred.
"""

from __future__ import annotations

from . import windows_layout as W
from . import windows_clone as C


def _active_keys(snapshot):
    return [W._path_key(path) for path in snapshot.paths if path.flags & W.ACTIVE]


def _physical_signature(snapshot, owned_keys):
    return C._signature(snapshot, exclude=owned_keys)


def _check_physical_modes(snapshot, original, excluded_before, survivor_key):
    if (_physical_signature(snapshot, {survivor_key}) !=
            _physical_signature(original, excluded_before)):
        raise W.WindowsLayoutError(
            "A non-owned display's CCD path or signal changed during removal; "
            "no survivor layout adjustment was attempted."
        )


def restore_survivor(api, survivor, before, captured, removed_identity,
                     deadline, guard):
    """Verify survivor, or restore its proven clone without altering others.

    precondition: capture_creation_baseline() was run before native removal;
    the driver removed only removed_identity's UUID, and exactly one managed
    monitor remains. The guard pins the current session/heartbeat generation.
    """
    W._check_guard(guard)
    if guard is None or captured is None:
        raise W.WindowsLayoutError("Survivor repair requires a live ownership guard and pre-removal capture.")
    original, original_sources = captured
    survivor_key, removed_key = W._key(survivor), W._key(removed_identity)
    before_by_key = {W._key(item): item for item in before}
    if (survivor_key == removed_key or survivor_key not in before_by_key or
            removed_key not in before_by_key or len(before_by_key) != len(before)):
        raise W.WindowsLayoutError("The removed and surviving monitor identities are ambiguous.")
    baseline = [item for item in before if W._key(item) not in (survivor_key, removed_key)]
    if not baseline or sum(bool(item.primary) for item in baseline) != 1:
        raise W.WindowsLayoutError("There is no unambiguous non-owned primary baseline.")
    if (set(_active_keys(original)) != set(before_by_key) or
            len(_active_keys(original)) != len(before)):
        raise W.WindowsLayoutError("Pre-removal CCD paths disagree with the verified desktop.")

    own_path = [p for p in original.paths if p.flags & W.ACTIVE and W._path_key(p) == survivor_key]
    if len(own_path) != 1:
        raise W.WindowsLayoutError("No unique owned target existed before native removal.")
    own_interface = api.target_path(own_path[0]).casefold()
    if not own_interface:
        raise W.WindowsLayoutError("The owned monitor interface was not available before removal.")
    # The snapshot and GDI source table were acquired before remove; require
    # that the saved survivor was independently attached then.
    attached_before = [
        item for item in original_sources
        if own_interface in tuple(p.casefold() for p in item.monitor_paths)
    ]
    if (len(attached_before) != 1 or
            not attached_before[0].flags & W.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP or
            attached_before[0].flags & (W.DISPLAY_DEVICE_PRIMARY_DEVICE | 8) or
            attached_before[0].device.casefold() != survivor.device.casefold() or
            len(attached_before[0].monitor_paths) != 1):
        raise W.WindowsLayoutError("Surviving output was not independently attached before removal.")

    restored = False

    def repair_pending(pending):
        nonlocal restored
        W._check_guard(guard)
        if restored or pending.observation is None:
            return None
        current_snapshot = pending.observation[0]
        keys = _active_keys(current_snapshot)
        expected = {W._key(x) for x in baseline} | {survivor_key}
        if len(keys) != len(expected) or set(keys) != expected:
            # Do not mutate an unknown desktop, even if Windows has a stale
            # phantom path or another output was connected concurrently.
            return None
        _check_physical_modes(current_snapshot, original,
                              {survivor_key, removed_key}, survivor_key)
        # A clone is authorized only if the exact surviving monitor interface
        # moved onto a known non-owned host. extend_new_clone supplies a
        # complete validated config with changes confined to that target.
        result = C.extend_new_clone(
            api, pending.observation, survivor_key, baseline,
            survivor.width, survivor.height, survivor.refresh_hz,
            survivor.x, survivor.y, deadline, guard,
        )
        restored = result is not None
        return result

    try:
        rows = W._active_records(api, deadline, guard,
                                 phase="after-selective-remove",
                                 pending_handler=repair_pending)
        W._check_guard(guard)
        actual_by_key = {W._key(item.display): item for item in rows}
        expected_keys = {W._key(item) for item in baseline} | {survivor_key}
        if len(rows) != len(expected_keys) or set(actual_by_key) != expected_keys:
            raise W.WindowsLayoutError("A display disappeared or an unexpected target remained after removal.")
        other_rows = [row.display for row in rows if W._key(row.display) != survivor_key]
        from .windows_multi import _layout_matches
        if not _layout_matches(baseline, other_rows):
            raise W.WindowsLayoutError("A non-owned display changed during removal.")
        target = actual_by_key[survivor_key].display
        if (target.device.casefold() != survivor.device.casefold() or
                not W._requested(target, survivor.width, survivor.height,
                                 survivor.refresh_hz, survivor.x, survivor.y,
                                 survivor.primary) or target.primary):
            raise W.WindowsLayoutError("The remaining Virtumon lost its original independent desktop mode.")
        snapshot = api.snapshot()
        W._check_guard(guard)
        _check_physical_modes(snapshot, original,
                              {survivor_key, removed_key}, survivor_key)
        paths = [p for p in snapshot.paths
                 if p.flags & W.ACTIVE and W._path_key(p) == survivor_key]
        if len(paths) != 1 or api.target_path(paths[0]).casefold() != own_interface:
            raise W.WindowsLayoutError("The owned target's monitor interface changed during removal.")
        source_mode = W._mode(snapshot, paths[0], True)
        target_mode = W._mode(snapshot, paths[0], False)
        if (source_mode is None or target_mode is None or
                (source_mode.sourceMode.width, source_mode.sourceMode.height,
                 source_mode.sourceMode.position.x, source_mode.sourceMode.position.y) !=
                (survivor.width, survivor.height, survivor.x, survivor.y) or
                (target_mode.targetMode.activeSize.cx,
                 target_mode.targetMode.activeSize.cy) != (survivor.width, survivor.height) or
                target_mode.targetMode.scanLineOrdering != 1 or
                not W._near(W._refresh_rate(target_mode.targetMode.vSyncFreq) or 0,
                            survivor.refresh_hz)):
            raise W.WindowsLayoutError("Surviving monitor signal timing could not be verified.")
        associations = [
            item for item in api.device_sources()
            if own_interface in tuple(p.casefold() for p in item.monitor_paths)
        ]
        if (len(associations) != 1 or
                associations[0].device.casefold() != target.device.casefold() or
                associations[0].flags & (W.DISPLAY_DEVICE_PRIMARY_DEVICE | 8) or
                not associations[0].flags & W.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP or
                tuple(p.casefold() for p in associations[0].monitor_paths) != (own_interface,)):
            raise W.WindowsLayoutError("The remaining monitor was not independently attached.")
        W._check_guard(guard)
        return target
    except W.WindowsLayoutError as exc:
        if exc.diagnostics is None:
            observation = None
            try:
                snapshot = api.snapshot()
                observation = (snapshot, api.device_sources(), {}, None)
            except Exception:
                pass
            if observation is not None:
                exc.diagnostics = W._metadata_report(
                    api, observation, str(exc), "after-selective-remove"
                )
        if exc.diagnostics is not None:
            exc.diagnostics["selective_removal"] = {
                "survivor_target": list(survivor_key),
                "removed_target": list(removed_key),
                "repair_applied": restored,
                "recovery": "Remove the remaining named virtumon by its saved UUID.",
            }
        raise
