"""Bounded, read-only evidence for Windows display-discovery failures.

The original CCD/GDI observations are serialized without making more API calls.
Optional enrichment never changes a display and never reads agent credentials,
environment variables, ownership files, registry data, or driver handles.
"""

from __future__ import annotations

import copy
import time


MAX_PATHS, MAX_MODES, MAX_SOURCES, MAX_MONITOR_PATHS = 64, 128, 64, 8
MAX_TEXT, MAX_ERRORS = 512, 16
ENRICHMENT_BUDGET_SECONDS = 1.0


def _text(value, limit=MAX_TEXT):
    return str(value)[:limit]


def _luid(value):
    return [int(value.LowPart), int(value.HighPart)]


def _source_key(path):
    source = path.sourceInfo
    return (*_luid(source.adapterId), int(source.id))


def _target_key(path):
    target = path.targetInfo
    return (*_luid(target.adapterId), int(target.id))


def _rate(value):
    return [int(value.Numerator), int(value.Denominator)]


def _source_details(source):
    paths = source.monitor_paths
    flags = int(source.flags)
    return {
        "device": _text(source.device), "flags": hex(flags), "flags_raw": flags,
        "attached": bool(flags & 1), "primary": bool(flags & 4), "mirroring": bool(flags & 8),
        "monitor_path_count": len(paths), "monitor_paths_truncated": len(paths) > MAX_MONITOR_PATHS,
        "monitor_paths": [_text(path) for path in paths[:MAX_MONITOR_PATHS]],
    }


def _snapshot_details(snapshot, source_names, problem_device, priority_targets=()):
    if snapshot is None:
        return None
    problem = problem_device.casefold() if problem_device else None
    paths = list(enumerate(snapshot.paths))
    # Preserve original indexes, even when the problematic path lies beyond the
    # output cap or a QDC_ALL_PATHS query has many possible source/target pairs.
    paths.sort(key=lambda pair: not (
        _target_key(pair[1]) in priority_targets or
        (problem is not None and source_names.get(_source_key(pair[1]), "").casefold() == problem)
    ))
    chosen = paths[:MAX_PATHS]
    records, mode_indexes, seen_modes = [], [], set()
    for index, path in chosen:
        source, target = path.sourceInfo, path.targetInfo
        source_raw, target_raw = int(source.modeInfoIdx), int(target.modeInfoIdx)
        records.append({
            "index": index, "flags": hex(int(path.flags)),
            "source": {
                "luid": _luid(source.adapterId), "id": int(source.id),
                "device": _text(source_names[_source_key(path)]) if _source_key(path) in source_names else None,
                "mode_raw": hex(source_raw), "mode_legacy": source_raw,
                "mode_virtual": source_raw >> 16, "clone_group": source_raw & 0xFFFF,
                "mode_legacy_in_bounds": source_raw < len(snapshot.modes),
                "mode_virtual_in_bounds": source_raw >> 16 != 0xFFFF and source_raw >> 16 < len(snapshot.modes),
                "status": int(source.statusFlags),
            },
            "target": {
                "luid": _luid(target.adapterId), "id": int(target.id),
                "mode_raw": hex(target_raw), "mode_legacy": target_raw,
                "mode_virtual": target_raw >> 16, "desktop_index": target_raw & 0xFFFF,
                "mode_legacy_in_bounds": target_raw < len(snapshot.modes),
                "mode_virtual_in_bounds": target_raw >> 16 != 0xFFFF and target_raw >> 16 < len(snapshot.modes),
                "available": bool(target.targetAvailable), "refresh": _rate(target.refreshRate),
                "rotation": int(target.rotation), "scaling": int(target.scaling),
                "output_technology": int(target.outputTechnology), "status": int(target.statusFlags),
            },
        })
        # Keep both interpretations: this report must not assume the decoder
        # being investigated is correct. Invalid/out-of-range indexes are omitted.
        for candidate in (source_raw >> 16, target_raw >> 16, source_raw, target_raw, target_raw & 0xFFFF):
            if (len(mode_indexes) < MAX_MODES and 0 <= candidate < len(snapshot.modes) and
                    candidate not in seen_modes):
                mode_indexes.append(candidate)
                seen_modes.add(candidate)
    for index in range(len(snapshot.modes)):
        if len(mode_indexes) >= MAX_MODES:
            break
        if index not in seen_modes:
            mode_indexes.append(index)
            seen_modes.add(index)
    modes = []
    for index in mode_indexes[:MAX_MODES]:
        mode = snapshot.modes[index]
        entry = {"index": index, "type": int(mode.infoType), "id": int(mode.id), "luid": _luid(mode.adapterId)}
        if mode.infoType == 1:
            value = mode.sourceMode
            entry.update(width=int(value.width), height=int(value.height), pixel_format=int(value.pixelFormat),
                         position=[int(value.position.x), int(value.position.y)])
        elif mode.infoType == 2:
            value = mode.targetMode
            entry.update(active_size=[int(value.activeSize.cx), int(value.activeSize.cy)],
                         total_size=[int(value.totalSize.cx), int(value.totalSize.cy)],
                         refresh=_rate(value.vSyncFreq), horizontal_sync=_rate(value.hSyncFreq),
                         scan_line_ordering=int(value.scanLineOrdering))
        elif mode.infoType == 3:
            value = mode.desktopImageInfo
            entry.update(source_size=[int(value.PathSourceSize.x), int(value.PathSourceSize.y)],
                         region=[int(getattr(value.DesktopImageRegion, field))
                                 for field in ("left", "top", "right", "bottom")],
                         clip=[int(getattr(value.DesktopImageClip, field))
                               for field in ("left", "top", "right", "bottom")])
        modes.append(entry)
    return {
        "virtual_mode_aware": bool(snapshot.virtual_mode_aware),
        "path_count": len(snapshot.paths), "mode_count": len(snapshot.modes),
        "paths_truncated": len(snapshot.paths) > len(records), "modes_truncated": len(snapshot.modes) > len(modes),
        "paths": records, "modes": modes,
    }


def capture_metadata(snapshot, device_sources, source_names, *, reason, problem_device=None):
    """Return JSON data from exactly the supplied observations; call no APIs."""
    problem = problem_device.casefold() if problem_device else None
    candidates = [source for source in device_sources
                  if problem is not None and source.device.casefold() == problem]
    match_status = ("not-requested" if problem is None else "missing" if not candidates else
                    "ambiguous" if len(candidates) != 1 else
                    "attached" if candidates[0].flags & 1 else "detached")
    sources = sorted(device_sources, key=lambda source: (
        source.device.casefold() != problem, not bool(source.flags & 4), not bool(source.flags & 1)))
    names = sorted(source_names.items(), key=lambda item: item[1].casefold() != problem)
    return {
        "schema": "virtumon.windows-metadata.v1", "reason": _text(reason, 2048),
        "observation_note": "CCD and GDI were read separately during the same failed discovery attempt; this is not an atomic Windows snapshot.",
        "index_notes": {
            "mode_legacy": "Full 32-bit modeInfoIdx interpretation.",
            "mode_virtual": "High 16-bit mode index interpretation; 65535 means unspecified.",
            "clone_group": "Raw low 16 bits; meaningful as a clone group only when the virtual source mode index is unspecified.",
            "desktop_index": "Raw low 16 bits of the target modeInfoIdx; 65535 means unspecified in the virtual layout.",
        },
        "problem": {"device": _text(problem_device) if problem_device is not None else None,
                    "match_status": match_status, "match_count": len(candidates),
                    "matches_truncated": len(candidates) > MAX_MONITOR_PATHS,
                    "matches": [_source_details(source) for source in candidates[:MAX_MONITOR_PATHS]]},
        "ccd": _snapshot_details(snapshot, source_names, problem_device),
        "gdi": {"source_count": len(device_sources), "sources_truncated": len(device_sources) > MAX_SOURCES,
                "sources": [_source_details(source) for source in sources[:MAX_SOURCES]]},
        "source_names": [{"luid": [int(key[0]), int(key[1])], "id": int(key[2]), "device": _text(value)}
                         for key, value in names[:MAX_SOURCES]],
        "source_names_truncated": len(names) > MAX_SOURCES,
        "limits": {"paths": MAX_PATHS, "modes": MAX_MODES, "sources": MAX_SOURCES,
                   "monitor_paths_per_source": MAX_MONITOR_PATHS, "text_characters": MAX_TEXT},
        "diagnostic_errors": [],
    }


def _mode_details(mode):
    if mode is None:
        return None
    return {"size": int(mode.dmSize), "fields": hex(int(mode.dmFields)),
            "width": int(mode.dmPelsWidth), "height": int(mode.dmPelsHeight),
            "position": [int(mode.dmPosition.x), int(mode.dmPosition.y)],
            "refresh": int(mode.dmDisplayFrequency), "bits_per_pixel": int(mode.dmBitsPerPel)}


def enrich_metadata(report, api):
    """Add best-effort reads once, retaining the exact original observations.

    The one-second budget is checked between native calls; synchronous User32
    calls cannot be preempted here. There are no retry loops or mode writes.
    """
    result = copy.deepcopy(report)
    errors = result.setdefault("diagnostic_errors", [])
    supplemental = result["supplemental"] = {
        "time_budget_seconds": ENRICHMENT_BUDGET_SECONDS, "budget_exhausted": False,
        "current_modes": [], "target_paths": [], "skipped_operations": [],
    }
    start = time.monotonic()

    def read(label, callback):
        if time.monotonic() - start >= ENRICHMENT_BUDGET_SECONDS:
            supplemental["budget_exhausted"] = True
            if len(supplemental["skipped_operations"]) < MAX_ERRORS:
                supplemental["skipped_operations"].append(label)
            return None
        try:
            return callback()
        except Exception as exc:
            if len(errors) < MAX_ERRORS:
                errors.append({"operation": label, "error": _text(f"{type(exc).__name__}: {exc}")})
            return None

    problem = result.get("problem", {}).get("device")
    devices = [problem] if problem else []
    for source in result.get("gdi", {}).get("sources", []):
        if source["attached"] and source["device"].casefold() not in {name.casefold() for name in devices}:
            devices.append(source["device"])
    for device in devices[:8]:
        if supplemental["budget_exhausted"]:
            break
        mode = read("current_mode:" + device, lambda name=device: _mode_details(api.current_mode(name)))
        supplemental["current_modes"].append({"device": device, "mode": mode})

    def desktop_displays():
        from .displays import list_displays
        displays = list_displays()
        return {"count": len(displays), "truncated": len(displays) > MAX_SOURCES,
                "displays": [{field: getattr(display, field) for field in
                              ("device", "x", "y", "width", "height", "primary")}
                             for display in displays[:MAX_SOURCES]]}

    supplemental["desktop"] = read("EnumDisplayMonitors", desktop_displays)
    # Importing this module itself never loads User32. Construct only a packet
    # containing the recorded adapter/target identity for the read-only name API.
    from . import windows_layout as W
    paths = (result.get("ccd") or {}).get("paths", [])
    paths = sorted(paths, key=lambda path: path["source"].get("device") != problem)
    targets = set()
    for path in paths:
        if len(supplemental["target_paths"]) >= 8 or supplemental["budget_exhausted"]:
            break
        target = path["target"]
        key = (*target["luid"], target["id"])
        if key in targets:
            continue
        targets.add(key)
        packet = W._PATH_INFO()
        packet.targetInfo.adapterId = W._LUID(*target["luid"])
        packet.targetInfo.id = target["id"]
        interface = read("target_path:" + str(key), lambda current=packet: api.target_path(current))
        if interface is not None:
            interface = _text(interface)
        matches = [source["device"] for source in result.get("gdi", {}).get("sources", [])
                   if interface and interface.casefold() in [value.casefold() for value in source["monitor_paths"]]]
        supplemental["target_paths"].append({
            "luid": target["luid"], "id": target["id"], "source_device": path["source"].get("device"),
            "monitor_path": interface, "matching_captured_gdi_sources": matches,
        })

    def all_paths():
        observed = api.snapshot(active_only=False)
        names = {(*item["luid"], item["id"]): item["device"] for item in result.get("source_names", [])}
        priority = {(*path["target"]["luid"], path["target"]["id"]) for path in paths
                    if problem and (path["source"].get("device") or "").casefold() == problem.casefold()}
        return _snapshot_details(observed, names, problem, priority_targets=priority)

    supplemental["all_paths"] = read("QueryDisplayConfig:all-paths", all_paths)

    def legacy_paths():
        observed = api.snapshot(virtual_mode_aware=False)
        names = {(*item["luid"], item["id"]): item["device"] for item in result.get("source_names", [])}
        return _snapshot_details(observed, names, problem)

    supplemental["legacy_active"] = read("QueryDisplayConfig:legacy-active", legacy_paths)
    supplemental["elapsed_seconds"] = round(time.monotonic() - start, 6)
    return result
