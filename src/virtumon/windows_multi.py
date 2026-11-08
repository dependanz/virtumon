"""Reconcile multiple *owned* virtual display targets after a Windows hotplug.

Windows may re-clone an existing SudoVDA output onto the laptop screen when a
second one arrives. During a create transaction only, this module can restore
all previously owned targets and place the new target in a single validated
SetDisplayConfig operation. No other path, mode or primary setting is proposed
for change. The caller must provide an observed pre-add CCD snapshot, a list of
the previously owned targets, and a live ownership guard.

EXPERIMENTAL: this path is opt-in. A successful unit test is not a Windows
hardware acceptance run.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from . import windows_layout as W
from . import windows_clone as C


@dataclass
class _Plan:
    snapshot: W._Snapshot
    sources: list[W._DeviceSource]
    owned: dict[tuple[int, int, int], W.NativeDisplay]
    new_key: tuple[int, int, int]
    expected: dict[tuple[int, int, int], tuple[int, int, float, int, int]]
    interfaces: dict[tuple[int, int, int], str]
    nonowned_signature: tuple
    original_signature: tuple


def _key(path):
    return W._path_key(path)


def _signal(snapshot, path):
    return W._mode(snapshot, path, False)


def _nonowned_signature(snapshot, exclude):
    """Compare mode values/priority, independent of the array's mode indexes."""
    result = []
    for path in snapshot.paths:
        if not path.flags & W.ACTIVE or _key(path) in exclude:
            continue
        target = path.targetInfo
        result.append((
            _key(path), W._source_key(path), path.flags,
            path.sourceInfo.statusFlags, target.outputTechnology, target.rotation,
            target.scaling, target.refreshRate.Numerator, target.refreshRate.Denominator,
            target.scanLineOrdering, target.targetAvailable, target.statusFlags,
            C._mode_signature(W._mode(snapshot, path, True)),
            C._mode_signature(W._mode(snapshot, path, False)),
            C._mode_signature(C._desktop_mode(snapshot, path)),
        ))
    return tuple(result)


def _layout_matches(before, after):
    first = {W._key(x): x for x in before}
    second = {W._key(x): x for x in after}
    if len(first) != len(before) or len(second) != len(after) or first.keys() != second.keys():
        return False
    return all(
        (a.device.casefold(), a.x, a.y, a.width, a.height, a.primary,
         a.active) == (b.device.casefold(), b.x, b.y, b.width, b.height, b.primary,
                       b.active) and W._near(a.refresh_hz, b.refresh_hz)
        for key in first for a, b in [(first[key], second[key])]
    )


def _recognize(api, observation, new_key, baseline, owned_before, original):
    if observation is None or original is None or not baseline or not owned_before:
        return None
    snapshot, sources, _names, _problem = observation
    prior_snapshot, prior_sources = original
    if not snapshot.virtual_mode_aware or not prior_snapshot.virtual_mode_aware:
        return None
    owned = {W._key(x): x for x in owned_before}
    baseline_keys = {W._key(x) for x in baseline}
    if (len(owned) != len(owned_before) or not set(owned) <= baseline_keys or
            new_key in baseline_keys or new_key in owned):
        return None
    expected_keys = baseline_keys | {new_key}
    active_paths = [p for p in snapshot.paths if p.flags & W.ACTIVE]
    if (len(active_paths) != len(expected_keys) or
            {_key(p) for p in active_paths} != expected_keys or
            len({_key(p) for p in active_paths}) != len(active_paths) or
            any(p.flags & W.VIRTUAL_MODE == 0 or
                not p.targetInfo.targetAvailable for p in active_paths)):
        return None
    before_paths = [p for p in prior_snapshot.paths if p.flags & W.ACTIVE]
    if (len(before_paths) != len(baseline) or
            {_key(p) for p in before_paths} != baseline_keys):
        return None
    try:
        prior_records = W._records(api, prior_snapshot, device_sources=prior_sources)
    except W.WindowsLayoutError:
        return None
    if not _layout_matches(baseline, [r.display for r in prior_records]):
        return None

    excluded = set(owned) | {new_key}
    original_nonowned = _nonowned_signature(prior_snapshot, excluded)
    if original_nonowned != _nonowned_signature(snapshot, excluded):
        return None

    paths = {_key(p): p for p in active_paths}
    # Distinct sources and distinct mode slots are prerequisites to modifying
    # two source modes without overwriting another path's mode.
    source_keys = [W._source_key(p) for p in active_paths]
    src_indices = [p.sourceInfo.modeInfoIdx >> 16 for p in active_paths]
    if len(set(source_keys)) != len(source_keys) or len(set(src_indices)) != len(src_indices):
        return None
    try:
        for path in active_paths:
            source, signal = W._mode(snapshot, path, True), _signal(snapshot, path)
            if (source is None or signal is None or not source.sourceMode.width or
                    not source.sourceMode.height or not signal.targetMode.activeSize.cx or
                    not signal.targetMode.activeSize.cy):
                return None
            C._desktop_mode(snapshot, path)
    except W.WindowsLayoutError:
        return None

    interfaces = {key: api.target_path(path).casefold() for key, path in paths.items()}
    if not all(interfaces.values()) or len(set(interfaces.values())) != len(interfaces):
        return None
    all_sources = {x.device.casefold(): x for x in sources}
    if len(all_sources) != len(sources):
        return None
    attached = [source for source in sources if source.flags & 1 and not source.flags & 8]
    if sum(bool(x.flags & 4) for x in attached) != 1:
        return None
    owners_by_child = {}
    for source in sources:
        for child in source.monitor_paths:
            owners_by_child.setdefault(child.casefold(), []).append(source)
    if (set(owners_by_child) != set(interfaces.values()) or
            any(len(owners_by_child[p]) != 1 for p in interfaces.values())):
        return None

    physical = [p for p in active_paths if _key(p) not in excluded]
    for path in physical:
        key = _key(path)
        prior = next((r for r in prior_records if W._key(r.display) == key), None)
        if prior is None:
            return None
        hosting = owners_by_child[interfaces[key]][0]
        if not hosting.flags & 1 or hosting.device.casefold() != prior.display.device.casefold():
            return None
        if prior.display.primary != bool(hosting.flags & 4):
            return None

    # An existing owned monitor may have been cloned onto another display.
    # The newly created output may be independently attached at the OLD
    # monitor's position, but its interface must still belong to one GDI view.
    for key, before in owned.items():
        path = paths[key]
        associated = owners_by_child[interfaces[key]][0]
        if not associated.flags & 1:
            return None
        own_name = api.source_name(path).casefold()
        if own_name and own_name == associated.device.casefold():
            # If still independent, its geometry should not have changed.
            s = W._mode(snapshot, path, True).sourceMode
            if (s.position.x, s.position.y, s.width, s.height) != (
                    before.x, before.y, before.width, before.height):
                return None
        else:
            # A clone is acceptable only onto an already known, nonowned
            # attached source. Otherwise this could be someone else's output.
            if associated.device.casefold() not in {
                r.display.device.casefold() for r in prior_records
                if W._key(r.display) not in owned
            }:
                return None
            if own_name and own_name in all_sources:
                own_view = all_sources[own_name]
                if own_view.flags & (1 | 4 | 8) or own_view.monitor_paths:
                    return None

    new_path = paths[new_key]
    new_host = owners_by_child[interfaces[new_key]][0]
    if not new_host.flags & 1 or new_host.flags & 4:
        return None
    # Do not accept a new monitor that has been assigned to someone else's
    # physical or existing managed GDI source.
    if interfaces[new_key] in [
        interfaces[x] for x in baseline_keys
    ]:
        return None

    return _Plan(snapshot, sources, owned, new_key, {}, interfaces,
                 original_nonowned, _nonowned_signature(snapshot, set()))


def _candidate(plan, new_geometry):
    desired = {key: (old.width, old.height, old.refresh_hz, old.x, old.y)
               for key, old in plan.owned.items()}
    desired[plan.new_key] = new_geometry
    snap = W._Snapshot([W._copy(p) for p in plan.snapshot.paths],
                       [W._copy(m) for m in plan.snapshot.modes])
    for path in snap.paths:
        key = _key(path)
        if key not in desired:
            continue
        width, height, refresh, x, y = desired[key]
        rate = Fraction(str(refresh)).limit_denominator(1000000)
        if not (0 < rate.numerator <= 0xFFFFFFFF and
                0 < rate.denominator <= 0xFFFFFFFF):
            raise W.WindowsLayoutError("The requested refresh cannot be encoded in CCD.")
        source = W._mode(snap, path, True)
        if source is None:
            raise W.WindowsLayoutError("The owned display's source mode disappeared.")
        source.sourceMode.width, source.sourceMode.height = width, height
        source.sourceMode.position = W._POINT(x, y)
        path.sourceInfo.modeInfoIdx = (path.sourceInfo.modeInfoIdx & 0xFFFF0000) | 0xFFFF
        path.targetInfo.modeInfoIdx = 0xFFFFFFFF
        path.targetInfo.refreshRate = W._RATIONAL(rate.numerator, rate.denominator)
        path.targetInfo.rotation = 1
        path.targetInfo.scaling = 1
        path.targetInfo.scanLineOrdering = 1
    return snap, desired


def _expected_desktop(baseline, owned, new_key, new_geom):
    records = [x for x in baseline if W._key(x) not in owned]
    records += list(owned.values())
    width, height, refresh, x, y = new_geom
    records.append(W.NativeDisplay("", x, y, width, height, refresh, False, *new_key))
    # Independently check that intended desktop rectangles don't overlap and
    # that the new output is connected to the current desktop.
    placeholder = W._Record(records[-1], None)
    W._geometry(placeholder, [W._Record(x, None) for x in records[:-1]],
                width, height, x, y)
    return records


def reconcile_new(api, observation, new_key, baseline, owned_before,
                  original, width, height, refresh, x, y, deadline, guard):
    """Return full active records after one guarded transaction, or None."""
    W._check_guard(guard)
    plan = _recognize(api, observation, new_key, baseline, owned_before, original)
    W._check_guard(guard)
    if plan is None:
        return None
    phase, attempted = "multi-prepare", False
    new_geometry = (width, height, refresh, x, y)
    owned_keys = set(plan.owned)
    try:
        _expected_desktop(baseline, plan.owned, new_key, new_geometry)
        proposed, expected = _candidate(plan, new_geometry)
        excluded = owned_keys | {new_key}
        if _nonowned_signature(proposed, excluded) != plan.nonowned_signature:
            raise W.WindowsLayoutError("The proposal would modify a non-owned display.")
        W._check_guard(guard)
        phase = "multi-validate"
        result = api.set_config(proposed, validate=True)
        W._check_guard(guard)
        if result:
            raise W._win_error("SetDisplayConfig multi-monitor validation", result)
        # Live topology must remain the same immediately before applying.
        snapshot = api.snapshot()
        sources = api.device_sources()
        fresh = _recognize(api, (snapshot, sources, {}, None), new_key,
                           baseline, owned_before, original)
        W._check_guard(guard)
        if (fresh is None or
                _nonowned_signature(fresh.snapshot, set()) != plan.original_signature):
            raise W.WindowsLayoutError("Desktop changed before multi-monitor apply.")
        # Compare all CCD paths and the GDI monitor-to-source ownership.
        if sorted((x.device.casefold(), x.flags, tuple(sorted(p.casefold() for p in x.monitor_paths)))
                  for x in sources if x.flags & 1 or x.monitor_paths) != sorted(
                  (x.device.casefold(), x.flags, tuple(sorted(p.casefold() for p in x.monitor_paths)))
                  for x in plan.sources if x.flags & 1 or x.monitor_paths):
            raise W.WindowsLayoutError("GDI attachment changed before multi-monitor apply.")
        phase = "multi-apply"
        W._check_guard(guard)
        attempted = True
        result = api.set_config(proposed, validate=False)
        W._check_guard(guard)
        if result:
            raise W._win_error("SetDisplayConfig multi-monitor apply", result)
        phase = "multi-readback"
        while True:
            W._check_guard(guard)
            rows = W._active_records(api, deadline, guard, phase=phase)
            W._check_guard(guard)
            by_key = {W._key(row.display): row for row in rows}
            if set(by_key) != set(expected) | {W._key(x) for x in baseline if W._key(x) not in owned_keys}:
                raise W.WindowsLayoutError("A display identity changed during multi-monitor apply.")
            originals = [r.display for r in rows if W._key(r.display) not in excluded]
            old_physical = [r for r in baseline if W._key(r) not in owned_keys]
            if not _layout_matches(old_physical, originals):
                raise W.WindowsLayoutError("The non-owned desktop changed during multi-monitor apply.")
            readback = api.snapshot()
            W._check_guard(guard)
            if _nonowned_signature(readback, excluded) != plan.nonowned_signature:
                raise W.WindowsLayoutError("A non-owned CCD signal/mode changed during multi-monitor apply.")
            source_views = api.device_sources()
            child_hosts = {}
            for source in source_views:
                for child in source.monitor_paths:
                    child_hosts.setdefault(child.casefold(), []).append(source)
            success = True
            for key, (pw, ph, hz, px, py) in expected.items():
                item = by_key.get(key)
                raw = next((p for p in readback.paths if p.flags & W.ACTIVE and _key(p) == key), None)
                if item is None or raw is None:
                    success = False
                    break
                s, t = W._mode(readback, raw, True), W._mode(readback, raw, False)
                hosts = child_hosts.get(plan.interfaces[key], [])
                if (not W._requested(item.display, pw, ph, hz, px, py, False) or
                        s is None or t is None or
                        (s.sourceMode.width, s.sourceMode.height,
                         s.sourceMode.position.x, s.sourceMode.position.y) !=
                        (pw, ph, px, py) or
                        (t.targetMode.activeSize.cx, t.targetMode.activeSize.cy) != (pw, ph) or
                        t.targetMode.scanLineOrdering != 1 or
                        not W._near(W._refresh_rate(t.targetMode.vSyncFreq) or 0, hz) or
                        len(hosts) != 1 or not hosts[0].flags & 1 or
                        hosts[0].flags & (4 | 8) or
                        api.source_name(raw).casefold() != hosts[0].device.casefold() or
                        tuple(child.casefold() for child in hosts[0].monitor_paths) !=
                        (plan.interfaces[key],)):
                    success = False
                    break
            if success:
                W._check_guard(guard)
                return rows
            if not W._pause(deadline):
                raise W.WindowsLayoutError(
                    "Windows did not independently attach all owned virtual displays "
                    "at their requested desktop and signal modes."
                )
    except W.WindowsLayoutError as exc:
        if exc.diagnostics is None:
            exc.diagnostics = W._metadata_report(api, observation, str(exc), phase)
        exc.diagnostics["multi_reconciliation"] = {
            "phase": phase, "apply_attempted": attempted,
            "owned_targets": len(plan.owned), "requested_target": list(new_key),
            "recovery": "Only the new monitor UUID is removed by failure cleanup.",
        }
        raise
