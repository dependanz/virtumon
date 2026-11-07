"""ABI and guarded-layout tests; these do not claim a real Windows hardware test."""

import ctypes as C
import json
import sys
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from virtumon import windows_layout as W


@dataclass(frozen=True)
class Identity:
    adapter_low: int
    adapter_high: int
    target_id: int


def mode(width, height, refresh=60, x=0, y=0):
    result = W._DEVMODE()
    result.dmSize = C.sizeof(result)
    result.dmFields = W.DM_POSITION | W.DM_PELSWIDTH | W.DM_PELSHEIGHT | W.DM_DISPLAYFREQUENCY
    result.dmPosition = W._POINT(x, y)
    result.dmPelsWidth, result.dmPelsHeight = width, height
    result.dmDisplayFrequency, result.dmBitsPerPel = round(refresh), 32
    return result


def screen(low, high, target, source, device, x=0, y=0, width=1920, height=1080, refresh=60, active=True):
    return dict(low=low, high=high, target=target, source=source, device=device,
                x=x, y=y, width=width, height=height, refresh=refresh, active=active)


def snapshot(screens):
    paths, modes = [], []
    for item in screens:
        path = W._PATH_INFO()
        luid = W._LUID(item["low"], item["high"])
        path.sourceInfo.adapterId = path.targetInfo.adapterId = luid
        path.sourceInfo.id, path.targetInfo.id = item["source"], item["target"]
        path.targetInfo.targetAvailable = 1
        path.targetInfo.rotation = path.targetInfo.scaling = 1
        path.sourceInfo.modeInfoIdx = path.targetInfo.modeInfoIdx = 0xFFFFFFFF
        if item["active"]:
            path.flags = W.ACTIVE | W.VIRTUAL_MODE
            path.sourceInfo.modeInfoIdx = len(modes) << 16 | 0xFFFF
            source = W._MODE_INFO()
            source.infoType, source.id, source.adapterId = 1, item["source"], luid
            source.sourceMode.width, source.sourceMode.height = item["width"], item["height"]
            source.sourceMode.pixelFormat = 4
            source.sourceMode.position = W._POINT(item["x"], item["y"])
            modes.append(source)
            path.targetInfo.modeInfoIdx = len(modes) << 16 | 0xFFFF
            target = W._MODE_INFO()
            target.infoType, target.id, target.adapterId = 2, item["target"], luid
            target.targetMode.vSyncFreq = W._RATIONAL(round(item["refresh"] * 1000), 1000)
            path.targetInfo.refreshRate = target.targetMode.vSyncFreq
            modes.append(target)
        paths.append(path)
    return W._Snapshot(paths, modes)


class FakeAPI:
    def __init__(self, inactive=False):
        self.screens = [screen(100, 1, 7, 0, "PHYSICAL"),
                        screen(200, -3, 7, 1, "VIRTUAL", x=1920, width=800, height=600, active=not inactive)]
        self.options = [mode(800, 600), mode(1280, 720), mode(1920, 1080)]
        self.sources = [W._DeviceSource("VIRTUAL", 0 if inactive else 1, ("monitor-interface-virtual",)),
                        W._DeviceSource("PHYSICAL", 1 | 4, ("monitor-interface-physical",))]
        self.calls = []
        self.test_result = self.apply_result = 0
        self.on_test = self.on_apply = None

    def snapshot(self, active_only=True):
        return snapshot([s for s in self.screens if s["active"] or not active_only])

    def source_name(self, path):
        key = W._source_key(path)
        return next(s["device"] for s in self.screens if (s["low"], s["high"], s["source"]) == key)

    def target_path(self, _path):
        return "MONITOR-INTERFACE-VIRTUAL"

    def device_sources(self):
        return self.sources

    def current_mode(self, device):
        s = next(s for s in self.screens if s["device"] == device and s["active"])
        return mode(s["width"], s["height"], s["refresh"], s["x"], s["y"])

    def modes(self, _device):
        return [W._copy(m) for m in self.options]

    def change_mode(self, device, change, flags):
        self.calls.append((device, W._copy(change), flags))
        if flags == W.CDS_TEST:
            if self.on_test:
                self.on_test(self)
            return self.test_result
        if self.apply_result:
            return self.apply_result
        s = next(s for s in self.screens if s["device"] == device)
        if change.dmFields & W.DM_POSITION:
            s["x"], s["y"] = change.dmPosition.x, change.dmPosition.y
        if change.dmFields & W.DM_PELSWIDTH:
            s["width"] = change.dmPelsWidth
        if change.dmFields & W.DM_PELSHEIGHT:
            s["height"] = change.dmPelsHeight
        if change.dmFields & W.DM_DISPLAYFREQUENCY:
            s["refresh"] = change.dmDisplayFrequency
        s["active"] = bool(s["width"] and s["height"])
        for source in self.sources:
            if source.device == device:
                source.flags = source.flags | 1 if s["active"] else source.flags & ~1
        if self.on_apply:
            callback, self.on_apply = self.on_apply, None
            callback(self)
        return 0


VIRTUAL = Identity(200, -3, 7)


class ABITests(unittest.TestCase):
    def test_windows_layouts_are_fixed_width_even_on_linux(self):
        expected = {W._LUID: 8, W._POINT: 8, W._SOURCE_MODE: 20, W._SIGNAL: 48,
                    W._DESKTOP_IMAGE: 40, W._MODE_INFO: 64, W._PATH_SOURCE: 20,
                    W._PATH_TARGET: 48, W._PATH_INFO: 72, W._INFO_HEADER: 20,
                    W._SOURCE_NAME: 84, W._TARGET_NAME: 420, W._DISPLAY_DEVICE: 840, W._DEVMODE: 220}
        for structure, size in expected.items():
            with self.subTest(structure=structure.__name__):
                self.assertEqual(C.sizeof(structure), size)
        self.assertEqual(W._MODE_INFO.mode.offset, 16)
        self.assertEqual(W._DEVMODE.dmPosition.offset, 76)
        self.assertEqual(W._DEVMODE.dmPelsWidth.offset, 172)
        self.assertEqual(W._DEVMODE.dmDisplayFrequency.offset, 184)
        self.assertEqual(W._POINT(-2000, -100).x, -2000)

    def test_prototypes_and_buffer_resize_retry(self):
        template = snapshot([screen(200, -3, 7, 1, "VIRTUAL")])

        class Function:
            def __init__(self, callback=lambda *args: 0):
                self.callback = callback

            def __call__(self, *args):
                return self.callback(*args)

        class DLL:
            pass

        dll = DLL()
        seen = []

        def sizes(flags, paths, modes):
            seen.append(flags)
            C.cast(paths, C.POINTER(W.U32))[0] = 1
            C.cast(modes, C.POINTER(W.U32))[0] = 2
            return 0

        def query(flags, count_paths, paths, count_modes, modes, topology):
            self.assertIsNone(topology)
            if len(seen) == 1:
                return 122
            paths[0] = template.paths[0]
            modes[0], modes[1] = template.modes
            return 0

        dll.GetDisplayConfigBufferSizes, dll.QueryDisplayConfig = Function(sizes), Function(query)
        for name in ("DisplayConfigGetDeviceInfo", "EnumDisplayDevicesW", "EnumDisplaySettingsExW", "ChangeDisplaySettingsExW"):
            setattr(dll, name, Function())
        api = W._Win32(dll)
        result = api.snapshot()
        self.assertEqual(seen, [0x12, 0x12])
        self.assertEqual(W._path_key(result.paths[0]), (200, -3, 7))
        self.assertIs(dll.ChangeDisplaySettingsExW.restype, C.c_int32)
        self.assertIs(dll.ChangeDisplaySettingsExW.argtypes[2], C.c_void_p)
        self.assertIs(dll.ChangeDisplaySettingsExW.argtypes[4], C.c_void_p)
        self.assertTrue(result.virtual_mode_aware)
        legacy = api.snapshot(virtual_mode_aware=False)
        self.assertFalse(legacy.virtual_mode_aware)
        self.assertEqual(seen[-1], W.QDC_ONLY_ACTIVE_PATHS)


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.patch = patch.object(W, "_get_api", return_value=self.api)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def configure(self, **overrides):
        args = dict(identity=VIRTUAL, width=1280, height=720, refresh_hz=60, x=1920, y=0, timeout=0)
        args.update(overrides)
        return W.configure_display(**args)

    def test_complete_luid_and_target_identity_and_virtual_mode_indexes(self):
        # Both adapters deliberately have target 7; the signed high LUID differs.
        displays = W.list_native_displays()
        self.assertEqual(len(displays), 2)
        target = W.wait_for_display(VIRTUAL, timeout=0)
        self.assertEqual((target.device, target.adapter_high, target.width), ("VIRTUAL", -3, 800))
        self.assertEqual(W.wait_for_display(Identity(100, 1, 7), timeout=0).device, "PHYSICAL")
        self.assertFalse(W.is_display_present(Identity(200, 3, 7)))

    def test_observed_windows_query_format_without_virtual_path_flag(self):
        # Actual native CI values: SOURCE id 0 is mode 1, TARGET 5527112 is
        # mode 0. The path's flags are only ACTIVE, even for the packed query.
        self.api.screens = [screen(23539, 0, 5527112, 0, "DISPLAY1", width=1024, height=768, refresh=64)]
        self.api.sources = [W._DeviceSource("DISPLAY1", 1 | 4, ("physical-interface",))]
        for aware in (True, False):
            with self.subTest(virtual_mode_aware=aware):
                current = self.api.snapshot()
                current.virtual_mode_aware = aware
                current.modes = [current.modes[1], current.modes[0]]
                path = current.paths[0]
                path.flags = W.ACTIVE
                path.sourceInfo.modeInfoIdx = 0x1FFFF if aware else 1
                path.targetInfo.modeInfoIdx = 0xFFFF if aware else 0
                current.modes[0].targetMode.vSyncFreq = W._RATIONAL(0xFFFFFFFE, 0xFFFFFFFE)
                path.targetInfo.refreshRate = W._RATIONAL(64, 1)
                actual = W._records(self.api, current)[0].display
                self.assertEqual((actual.width, actual.height, actual.x, actual.y, actual.primary),
                                 (1024, 768, 0, 0, True))
                self.assertEqual((actual.adapter_low, actual.adapter_high, actual.target_id, actual.refresh_hz),
                                 (23539, 0, 5527112, 64))
                # Query metadata must not weaken the mode identity checks.
                current.modes[1].id = 999
                with self.assertRaisesRegex(W.WindowsLayoutError, "valid CCD source geometry"):
                    W._records(self.api, current)

    def test_unavailable_signal_refresh_uses_path_or_reports_unknown(self):
        current = self.api.snapshot()
        target = current.modes[1].targetMode
        for sentinel in (0xFFFFFFFE, 0xFFFFFFFF):
            with self.subTest(sentinel=sentinel):
                target.vSyncFreq = W._RATIONAL(sentinel, sentinel)
                current.paths[0].targetInfo.refreshRate = W._RATIONAL(64, 1)
                self.assertEqual(W._records(self.api, current)[0].display.refresh_hz, 64)
                current.paths[0].targetInfo.refreshRate = W._RATIONAL(0, 0)
                self.assertEqual(W._records(self.api, current)[0].display.refresh_hz, 0)

    def test_inactive_presence_is_reported_without_fabricating_geometry(self):
        self.api.screens[1]["active"] = False
        self.api.sources[0].flags = 0
        self.assertEqual(len(W.list_native_displays()), 1)
        pending = W.wait_for_display(VIRTUAL, timeout=0)
        self.assertFalse(pending.active)
        self.assertEqual((pending.device, pending.width, pending.height), ("VIRTUAL", 0, 0))
        self.assertTrue(W.is_display_present(VIRTUAL))

    def test_unknown_identity_times_out_without_changes(self):
        with self.assertRaisesRegex(W.WindowsLayoutError, "Timed out"):
            W.wait_for_display(Identity(999, -3, 7), timeout=0)
        self.assertFalse(self.api.calls)

    def test_supported_resize_changes_only_named_target_and_verifies(self):
        physical = dict(self.api.screens[0])
        actual = self.configure()
        self.assertEqual((actual.width, actual.height, actual.x, actual.refresh_hz), (1280, 720, 1920, 60))
        self.assertEqual(self.api.screens[0], physical)
        self.assertEqual([(name, flags) for name, _, flags in self.api.calls], [("VIRTUAL", 2), ("VIRTUAL", 0)])

    def test_nonprimary_target_at_ccd_origin_moves_beside_existing_primary(self):
        # Reproduce the layout condition implied by the first hardware failure,
        # with independent GDI evidence that the new source is not primary.
        self.api.screens[0].update(width=2560, height=1600)
        self.api.screens[1].update(x=0, y=0)
        physical = dict(self.api.screens[0])
        self.assertFalse(W.wait_for_display(VIRTUAL, timeout=0).primary)
        actual = self.configure(x=2560)
        self.assertEqual((actual.x, actual.y, actual.width, actual.height, actual.primary),
                         (2560, 0, 1280, 720, False))
        self.assertEqual(self.api.screens[0], physical)
        self.assertEqual([(name, flags) for name, _, flags in self.api.calls], [("VIRTUAL", 2), ("VIRTUAL", 0)])

    def test_gdi_primary_cannot_be_moved_even_when_ccd_position_is_nonzero(self):
        self.api.sources[0].flags = 1 | 4
        self.api.sources[1].flags = 1
        with self.assertRaisesRegex(W.WindowsLayoutError, "primary.*target=VIRTUAL.*requested=\\(1920, 0\\)"):
            self.configure()
        self.assertFalse(self.api.calls)

    def test_uncertain_gdi_primary_metadata_prevents_any_native_change(self):
        for case in ("missing", "duplicate", "detached", "no-primary", "multiple-primary"):
            with self.subTest(case=case):
                self.api.sources = [W._DeviceSource("VIRTUAL", 1, ("monitor-interface-virtual",)),
                                    W._DeviceSource("PHYSICAL", 1 | 4, ("monitor-interface-physical",))]
                if case == "missing":
                    self.api.sources.pop(0)
                elif case == "duplicate":
                    self.api.sources.append(W._DeviceSource("virtual", 1, ("another-interface",)))
                elif case == "detached":
                    self.api.sources[0].flags = 0
                elif case == "no-primary":
                    self.api.sources[1].flags = 1
                else:
                    self.api.sources[0].flags = 1 | 4
                with self.assertRaisesRegex(W.WindowsLayoutError, "consistent Windows display metadata"):
                    self.configure()
                self.assertFalse(self.api.calls)

    def test_timeout_captures_the_failed_observations_once_before_any_write(self):
        self.api.sources[0].flags = 0
        failed_snapshot = self.api.snapshot()
        report = {"reason": "exact failed pair"}
        with patch.object(self.api, "snapshot", return_value=failed_snapshot), \
                patch("virtumon.windows_diagnostics.capture_metadata", return_value=report) as capture, \
                patch("virtumon.windows_diagnostics.enrich_metadata", side_effect=lambda value, api: value) as enrich:
            with self.assertRaisesRegex(W.WindowsLayoutError, "matches=1; flags=0x0") as caught:
                self.configure()
        self.assertIs(capture.call_args.args[0], failed_snapshot)
        self.assertIs(capture.call_args.args[1], self.api.sources)
        self.assertEqual(capture.call_args.kwargs["problem_device"], "VIRTUAL")
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(enrich.call_count, 1)
        self.assertEqual(caught.exception.diagnostics, {"reason": "exact failed pair", "phase": "arrival"})
        self.assertFalse(self.api.calls)

    def test_failed_diagnostic_capture_keeps_the_original_layout_error(self):
        self.api.sources[0].flags = 0
        with patch("virtumon.windows_diagnostics.capture_metadata", side_effect=RuntimeError("capture unavailable")):
            with self.assertRaisesRegex(W.WindowsLayoutError, "consistent Windows display metadata.*matches=1") as caught:
                self.configure()
        self.assertEqual(caught.exception.diagnostics["capture_error"], "capture unavailable")
        self.assertEqual(caught.exception.diagnostics["phase"], "arrival")
        self.assertFalse(self.api.calls)

    def test_failed_enrichment_does_not_discard_the_exact_failed_observations(self):
        self.api.sources[0].flags = 0
        report = {"problem": {"device": "VIRTUAL", "match_count": 1, "flags": [0]}}
        with patch("virtumon.windows_diagnostics.capture_metadata", return_value=report), \
                patch("virtumon.windows_diagnostics.enrich_metadata", side_effect=RuntimeError("supplement unavailable")):
            with self.assertRaisesRegex(W.WindowsLayoutError, "matches=1; flags=0x0") as caught:
                self.configure()
        self.assertEqual(caught.exception.diagnostics["problem"], report["problem"])
        self.assertEqual(caught.exception.diagnostics["enrichment_error"], "supplement unavailable")
        self.assertFalse(self.api.calls)

    def test_post_apply_metadata_diagnostic_survives_target_rollback_error_wrapper(self):
        initial = W._records(self.api, self.api.snapshot())
        failure = W.WindowsLayoutError("post-apply metadata mismatch")
        failure.diagnostics = {"phase": "after-mode-apply", "problem_device": "VIRTUAL"}

        def records(api, deadline, guard=None, *, phase="discovery"):
            if phase == "after-mode-apply":
                raise failure
            return initial

        with patch.object(W, "_active_records", side_effect=records):
            with self.assertRaisesRegex(W.WindowsLayoutError, "post-apply metadata mismatch") as caught:
                self.configure()
        self.assertEqual(caught.exception.diagnostics, failure.diagnostics)
        self.assertEqual([device for device, _, _ in self.api.calls], ["VIRTUAL", "VIRTUAL", "VIRTUAL"])

    def test_empty_ccd_cannot_hide_an_attached_gdi_desktop(self):
        with self.assertRaisesRegex(W._PendingTopology, "no active paths.*attached display"):
            W._records(self.api, snapshot([]))
        self.api.sources = []
        self.assertEqual(W._records(self.api, snapshot([])), [])

    def test_gdi_metadata_settles_with_fresh_ccd_query_before_any_mode_call(self):
        self.api.sources.pop(0)
        clock, reads = [0.0], []
        original_snapshot = self.api.snapshot

        def observe(active_only=True):
            reads.append(clock[0])
            return original_snapshot(active_only)

        def settle(delay):
            self.assertFalse(self.api.calls)
            clock[0] += delay
            self.api.sources.insert(0, W._DeviceSource("virtual", 1, ("monitor-interface-virtual",)))

        with patch.object(self.api, "snapshot", side_effect=observe), \
                patch.object(W.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(W.time, "sleep", side_effect=settle):
            actual = self.configure(timeout=1)
        self.assertFalse(actual.primary)
        self.assertAlmostEqual(clock[0], 0.1)
        self.assertEqual(reads[:2], [0.0, 0.1])
        self.assertEqual([flags for _, _, flags in self.api.calls], [2, 0])

    def test_ownership_loss_during_gdi_metadata_wait_blocks_all_mode_calls(self):
        self.api.sources.pop(0)
        clock, current = [0.0], [True]

        def invalidate(delay):
            clock[0] += delay
            current[0] = False

        with patch.object(W.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(W.time, "sleep", side_effect=invalidate):
            with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
                self.configure(timeout=1, guard=lambda: current[0])
        self.assertAlmostEqual(clock[0], 0.1)
        self.assertFalse(self.api.calls)

    def test_move_only_keeps_fractional_mode_and_supports_negative_coordinates(self):
        self.api.screens[1].update(width=1280, height=720, refresh=59.94)
        actual = self.configure(x=-1280, refresh_hz=59.94)
        self.assertEqual(actual.x, -1280)
        self.assertAlmostEqual(actual.refresh_hz, 59.94)
        self.assertTrue(all(change.dmFields == W.DM_POSITION for _, change, _ in self.api.calls))

    def test_fractional_mode_change_is_not_silently_rounded(self):
        with self.assertRaisesRegex(W.WindowsLayoutError, "fractional"):
            self.configure(refresh_hz=59.94)
        self.assertFalse(self.api.calls)

    def test_verified_inactive_gdi_association_can_be_attached(self):
        self.api.screens[1]["active"] = False
        self.api.sources[0].flags = 0
        actual = self.configure()
        self.assertTrue(actual.active)
        self.assertEqual(actual.device, "VIRTUAL")
        self.assertEqual([flags for _, _, flags in self.api.calls], [2, 0])

    def test_inactive_source_can_settle_before_configure_timeout(self):
        self.api.screens[1]["active"] = False
        self.api.sources = self.api.sources[1:]
        clock = [0.0]

        def settle(delay):
            self.assertFalse(self.api.calls, "No mode test or write is safe before association resolves")
            clock[0] += delay
            self.api.sources.insert(0, W._DeviceSource("VIRTUAL", 0, ("monitor-interface-virtual",)))

        with patch.object(W.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(W.time, "sleep", side_effect=settle):
            actual = self.configure(timeout=1)
        self.assertTrue(actual.active)
        self.assertEqual((actual.device, actual.width, actual.height), ("VIRTUAL", 1280, 720))
        self.assertAlmostEqual(clock[0], 0.1)
        self.assertEqual([flags for _, _, flags in self.api.calls], [2, 0])

    def test_inactive_resolution_timeout_is_bounded_and_reports_latest_cause(self):
        self.api.screens[1]["active"] = False
        self.api.sources = self.api.sources[1:]
        clock = [0.0]

        def advance(delay):
            clock[0] += delay

        def interface(_path):
            return "" if clock[0] < 0.1 else "monitor-interface-virtual"

        with patch.object(W.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(W.time, "sleep", side_effect=advance), \
                patch.object(self.api, "target_path", side_effect=interface):
            with self.assertRaisesRegex(W.WindowsLayoutError, "Timed out.*Latest resolution error:.*unique, unused") as error:
                self.configure(timeout=0.25)
        self.assertIsInstance(error.exception.__cause__, W._UnsafeTarget)
        self.assertAlmostEqual(clock[0], 0.25)
        self.assertFalse(self.api.calls)

    def test_ambiguous_inactive_mapping_remains_pending_and_cannot_change_layout(self):
        self.api.screens[1]["active"] = False
        self.api.sources[0].flags = 0
        self.api.sources.append(W._DeviceSource("OTHER", 0, ("monitor-interface-virtual",)))
        pending = W.wait_for_display(VIRTUAL, timeout=0)
        self.assertFalse(pending.active)
        self.assertEqual(pending.device, "")
        with self.assertRaisesRegex(W.WindowsLayoutError, "unique, unused"):
            self.configure()
        self.assertFalse(self.api.calls)

    def test_arrived_target_with_delayed_name_metadata_remains_pending(self):
        self.api.screens[1]["active"] = False
        with patch.object(self.api, "target_path", side_effect=W.WindowsLayoutError("name metadata is not ready")):
            pending = W.wait_for_display(VIRTUAL, timeout=0)
            self.assertFalse(pending.active)
            self.assertEqual(pending.device, "")
            with self.assertRaisesRegex(W.WindowsLayoutError, "metadata is not ready"):
                self.configure()
        self.assertFalse(self.api.calls)

    def test_inactive_source_associated_with_two_monitors_is_rejected(self):
        self.api.screens[1]["active"] = False
        self.api.sources[0].flags = 0
        self.api.sources[0].monitor_paths += ("physical-monitor-interface",)
        with self.assertRaisesRegex(W.WindowsLayoutError, "unique, unused"):
            self.configure()
        self.assertFalse(self.api.calls)

    def test_inactive_candidate_cannot_reuse_an_active_physical_source(self):
        self.api.screens[1].update(low=100, high=1, target=8, source=0, device="PHYSICAL", active=False)
        self.api.sources = [W._DeviceSource("PHYSICAL", 1 | 4, ("monitor-interface-virtual",))]
        with self.assertRaisesRegex(W.WindowsLayoutError, "unique, unused"):
            self.configure(identity=Identity(100, 1, 8))
        self.assertFalse(self.api.calls)

    def test_clone_mode_is_rejected_before_any_write(self):
        self.api.screens[1].update(low=100, high=1, target=8, source=0, device="PHYSICAL", x=0)
        clones = W.list_native_displays()
        self.assertEqual(len(clones), 2)
        self.assertTrue(all(display.primary for display in clones))
        with self.assertRaisesRegex(W.WindowsLayoutError, "clone mode"):
            self.configure(identity=Identity(100, 1, 8))
        self.assertFalse(self.api.calls)

    def test_invalid_primary_overlap_and_gap_are_rejected(self):
        for x, message in ((0, "primary"), (1000, "overlaps"), (2000, "gap")):
            with self.subTest(x=x), self.assertRaisesRegex(W.WindowsLayoutError, message):
                self.configure(x=x)
        self.assertFalse(self.api.calls)

    def test_unsupported_mode_is_rejected_before_testing_or_applying(self):
        with self.assertRaisesRegex(W.WindowsLayoutError, "does not advertise"):
            self.configure(width=1366)
        self.assertFalse(self.api.calls)

    def test_incomplete_saved_mode_prevents_unrecoverable_change(self):
        incomplete = mode(800, 600)
        incomplete.dmFields = W.DM_PELSWIDTH | W.DM_PELSHEIGHT
        with patch.object(self.api, "current_mode", return_value=incomplete):
            with self.assertRaisesRegex(W.WindowsLayoutError, "complete current mode"):
                self.configure()
        self.assertFalse(self.api.calls)

    def test_unreported_refresh_never_satisfies_verification(self):
        unknown = W.NativeDisplay("VIRTUAL", 1920, 0, 800, 600, 0.0, False, 200, -3, 7)
        self.assertFalse(W._requested(unknown, 800, 600, 0.001, 1920, 0, False))

    def test_failed_mode_test_never_applies(self):
        self.api.test_result = -2
        with self.assertRaisesRegex(W.WindowsLayoutError, "unsupported mode"):
            self.configure()
        self.assertEqual([flags for _, _, flags in self.api.calls], [2])

    def test_concurrent_topology_change_aborts_before_apply(self):
        self.api.on_test = lambda api: api.screens[0].update(width=1919)
        with self.assertRaisesRegex(W.WindowsLayoutError, "desktop changed"):
            self.configure()
        self.assertEqual([flags for _, _, flags in self.api.calls], [2])

    def test_primary_source_change_during_mode_test_aborts_before_apply(self):
        def switch_primary(api):
            api.sources[0].flags = 1 | 4
            api.sources[1].flags = 1

        self.api.on_test = switch_primary
        with self.assertRaisesRegex(W.WindowsLayoutError, "desktop changed"):
            self.configure()
        self.assertEqual([flags for _, _, flags in self.api.calls], [W.CDS_TEST])

    def test_bad_readback_restores_only_the_target(self):
        original = [dict(s) for s in self.api.screens]
        self.api.on_apply = lambda api: api.screens[1].update(refresh=59)
        with self.assertRaisesRegex(W.WindowsLayoutError, "original layout was restored"):
            self.configure()
        self.assertEqual(self.api.screens, original)
        self.assertEqual([(name, flags) for name, _, flags in self.api.calls],
                         [("VIRTUAL", 2), ("VIRTUAL", 0), ("VIRTUAL", 0)])

    def test_unexpected_physical_change_is_detected_without_broad_rollback(self):
        self.api.on_apply = lambda api: api.screens[0].update(width=1919)
        with self.assertRaisesRegex(W.WindowsLayoutError, "changed another display.*no broad rollback"):
            self.configure()
        self.assertEqual([name for name, _, _ in self.api.calls], ["VIRTUAL", "VIRTUAL"])

    def test_bad_inactive_attachment_is_detached_without_removing_backend_monitor(self):
        self.api.screens[1]["active"] = False
        self.api.sources[0].flags = 0
        self.api.on_apply = lambda api: api.screens[1].update(refresh=59)
        with self.assertRaisesRegex(W.WindowsLayoutError, "original layout was restored"):
            self.configure()
        self.assertFalse(self.api.screens[1]["active"])
        self.assertTrue(W.is_display_present(VIRTUAL))
        self.assertEqual(self.api.calls[-1][1].dmPelsWidth, 0)

    def test_invalid_identity_timeout_and_refresh_fail_before_writes(self):
        for overrides in ({"identity": Identity(-1, 0, 1)}, {"timeout": -1}, {"refresh_hz": float("nan")}):
            with self.subTest(overrides=overrides), self.assertRaises(W.WindowsLayoutError):
                self.configure(**overrides)
        self.assertFalse(self.api.calls)

    def test_false_or_failed_ownership_guard_blocks_discovery_and_all_mode_calls(self):
        for guard in (lambda: False, lambda: (_ for _ in ()).throw(RuntimeError("watchdog unavailable"))):
            with self.subTest(guard=guard), patch.object(self.api, "snapshot") as discovery:
                with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
                    self.configure(guard=guard)
                discovery.assert_not_called()
        self.assertFalse(self.api.calls)

    def test_ownership_invalidation_during_arrival_wait_blocks_recycled_target(self):
        recycled = self.api.screens.pop()
        current, clock = [True], [0.0]

        def recycle(delay):
            clock[0] += delay
            current[0] = False
            self.api.screens.append(recycled)

        with patch.object(W.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(W.time, "sleep", side_effect=recycle):
            with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
                self.configure(timeout=1, guard=lambda: current[0])
        self.assertAlmostEqual(clock[0], 0.1)
        self.assertFalse(self.api.calls)

    def test_ownership_invalidation_during_inactive_resolution_blocks_ready_source(self):
        self.api.screens[1]["active"] = False
        self.api.sources = self.api.sources[1:]
        current, clock = [True], [0.0]

        def settle_in_new_generation(delay):
            clock[0] += delay
            current[0] = False
            self.api.sources.insert(0, W._DeviceSource("VIRTUAL", 0, ("monitor-interface-virtual",)))

        with patch.object(W.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(W.time, "sleep", side_effect=settle_in_new_generation):
            with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
                self.configure(timeout=1, guard=lambda: current[0])
        self.assertAlmostEqual(clock[0], 0.1)
        self.assertFalse(self.api.calls)

    def test_ownership_is_rechecked_before_native_mode_test(self):
        current = [True]
        original = self.api.modes

        def invalidate_while_enumerating(device):
            current[0] = False
            return original(device)

        with patch.object(self.api, "modes", side_effect=invalidate_while_enumerating):
            with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
                self.configure(guard=lambda: current[0])
        self.assertFalse(self.api.calls)

    def test_ownership_invalidation_during_mode_test_prevents_apply(self):
        current = [True]
        self.api.on_test = lambda api: current.__setitem__(0, False)
        with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
            self.configure(guard=lambda: current[0])
        self.assertEqual([flags for _, _, flags in self.api.calls], [W.CDS_TEST])

    def test_ownership_loss_during_failed_apply_cannot_reauthorize_rollback(self):
        current = [True]
        original = self.api.change_mode

        def failed_apply(device, change, flags):
            result = original(device, change, flags)
            if flags == 0:
                current[0] = False
                return -1
            return result

        def guard():
            if not current[0]:
                # A later True must not revive the stale identity in this call.
                current[0] = True
                return False
            return True

        with patch.object(self.api, "change_mode", side_effect=failed_apply):
            with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
                self.configure(guard=guard)
        self.assertEqual([flags for _, _, flags in self.api.calls], [W.CDS_TEST, 0])
        self.assertEqual(self.api.screens[1]["width"], 1280)

    def test_rollback_rechecks_ownership_after_its_native_snapshot(self):
        current, failed = [True], [False]
        original_change, original_snapshot = self.api.change_mode, self.api.snapshot

        def partial_failure(device, change, flags):
            result = original_change(device, change, flags)
            if flags == 0:
                failed[0] = True
                return -1
            return result

        def invalidate_during_rollback(active_only=True):
            result = original_snapshot(active_only)
            if failed[0]:
                current[0] = False
            return result

        with patch.object(self.api, "change_mode", side_effect=partial_failure), \
                patch.object(self.api, "snapshot", side_effect=invalidate_during_rollback):
            with self.assertRaisesRegex(W.WindowsLayoutError, "rollback skipped: current-session ownership is unverified"):
                self.configure(guard=lambda: current[0])
        self.assertEqual([flags for _, _, flags in self.api.calls], [W.CDS_TEST, 0])

    def test_readback_cannot_report_success_after_ownership_invalidation(self):
        current = [True]
        original = self.api.snapshot

        def invalidate_during_readback(active_only=True):
            result = original(active_only)
            if any(flags == 0 for _, _, flags in self.api.calls):
                current[0] = False
            return result

        with patch.object(self.api, "snapshot", side_effect=invalidate_during_readback):
            with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
                self.configure(guard=lambda: current[0])
        self.assertEqual([flags for _, _, flags in self.api.calls], [W.CDS_TEST, 0])


def _snapshot_diagnostics(snapshot):
    if snapshot is None:
        return None
    paths = []
    for path in snapshot.paths[:32]:
        source, target = path.sourceInfo, path.targetInfo
        paths.append({
            "flags": hex(path.flags),
            "source": {"luid": [source.adapterId.LowPart, source.adapterId.HighPart], "id": source.id,
                       "mode_raw": hex(source.modeInfoIdx), "mode_legacy": source.modeInfoIdx,
                       "mode_virtual": source.modeInfoIdx >> 16, "clone_group": source.modeInfoIdx & 0xFFFF,
                       "status": source.statusFlags},
            "target": {"luid": [target.adapterId.LowPart, target.adapterId.HighPart], "id": target.id,
                       "mode_raw": hex(target.modeInfoIdx), "mode_virtual": target.modeInfoIdx >> 16,
                       "desktop_index": target.modeInfoIdx & 0xFFFF, "available": target.targetAvailable,
                       "refresh": [target.refreshRate.Numerator, target.refreshRate.Denominator]},
        })
    modes = []
    for index, mode_info in enumerate(snapshot.modes[:64]):
        entry = {"index": index, "type": mode_info.infoType, "id": mode_info.id,
                 "luid": [mode_info.adapterId.LowPart, mode_info.adapterId.HighPart]}
        if mode_info.infoType == 1:
            source = mode_info.sourceMode
            entry.update(width=source.width, height=source.height, pixel_format=source.pixelFormat,
                         position=[source.position.x, source.position.y])
        elif mode_info.infoType == 2:
            signal = mode_info.targetMode
            entry.update(active_size=[signal.activeSize.cx, signal.activeSize.cy],
                         refresh=[signal.vSyncFreq.Numerator, signal.vSyncFreq.Denominator])
        elif mode_info.infoType == 3:
            desktop = mode_info.desktopImageInfo
            entry.update(source_size=[desktop.PathSourceSize.x, desktop.PathSourceSize.y],
                         region=[desktop.DesktopImageRegion.left, desktop.DesktopImageRegion.top,
                                 desktop.DesktopImageRegion.right, desktop.DesktopImageRegion.bottom])
        else:
            entry["raw"] = bytes(mode_info).hex()
        modes.append(entry)
    return {"virtual_mode_aware": snapshot.virtual_mode_aware, "path_count": len(snapshot.paths),
            "mode_count": len(snapshot.modes), "paths": paths, "modes": modes}


def _native_diagnostics(api, current):
    structures = (W._LUID, W._SOURCE_MODE, W._SIGNAL, W._MODE_INFO, W._PATH_SOURCE, W._PATH_TARGET, W._PATH_INFO)
    result = {"pointer_size": C.sizeof(C.c_void_p), "python": sys.version.split()[0],
              "abi": {s.__name__: {"size": C.sizeof(s), "alignment": C.alignment(s)} for s in structures},
              "offsets": {"mode_union": W._MODE_INFO.mode.offset, "source_position": W._SOURCE_MODE.position.offset,
                          "path_target": W._PATH_INFO.targetInfo.offset, "path_flags": W._PATH_INFO.flags.offset},
              "virtual_aware": _snapshot_diagnostics(current), "gdi": []}
    # Compare the unmodified legacy query and GDI independently of our decoder.
    # Both calls are read-only and only run after the native probe has failed.
    try:
        result["legacy"] = _snapshot_diagnostics(api.snapshot(virtual_mode_aware=False))
    except Exception as exc:
        result["legacy_error"] = str(exc)
    for path in current.paths[:8] if current is not None else []:
        try:
            device = api.source_name(path)
            native_mode = api.current_mode(device)
            result["gdi"].append({"device": device, "mode": None if native_mode is None else {
                "fields": hex(native_mode.dmFields), "width": native_mode.dmPelsWidth, "height": native_mode.dmPelsHeight,
                "position": [native_mode.dmPosition.x, native_mode.dmPosition.y], "refresh": native_mode.dmDisplayFrequency}})
        except Exception as exc:
            result["gdi"].append({"error": str(exc)})
    return result


class NativeDiagnosticTests(unittest.TestCase):
    def test_diagnostics_expose_both_index_interpretations_and_mode_identity(self):
        current = snapshot([screen(200, -3, 7, 1, "VIRTUAL", width=1280, height=720)])
        report = _snapshot_diagnostics(current)
        source = report["paths"][0]["source"]
        self.assertEqual((source["mode_raw"], source["mode_legacy"], source["mode_virtual"]), ("0xffff", 65535, 0))
        self.assertEqual(report["modes"][0], {"index": 0, "type": 1, "id": 1, "luid": [200, -3],
                                             "width": 1280, "height": 720, "pixel_format": 4, "position": [0, 0]})
        json.dumps(report)


@unittest.skipUnless(sys.platform == "win32", "Read-only native Windows API probe")
class NativeWindowsProbeTests(unittest.TestCase):
    def test_real_user32_discovery_without_virtual_driver(self):
        """Exercise actual User32 structures/prototypes; never change a mode."""
        api = W._Win32()
        current = None
        try:
            current = api.snapshot()
            records = W._records(api, current)
            sources = api.device_sources()
        except W.WindowsLayoutError as exc:
            if exc.winerror in (5, 50):
                self.skipTest(f"Runner has no accessible console display session/WDDM: {exc}")
            self.fail(f"{exc}\nCCD_DIAGNOSTICS={json.dumps(_native_diagnostics(api, current), sort_keys=True)}")
        self.assertEqual(len(records), sum(bool(p.flags & W.ACTIVE) for p in current.paths))
        for record in records:
            self.assertTrue(record.display.device)
            self.assertGreater(record.display.width, 0)
            self.assertGreater(record.display.height, 0)
            matched = [source for source in sources
                       if source.device.casefold() == record.display.device.casefold()]
            self.assertEqual(len(matched), 1)
            self.assertEqual(record.display.primary, bool(matched[0].flags & W.DISPLAY_DEVICE_PRIMARY_DEVICE))
            native_mode = api.current_mode(record.display.device)
            if native_mode is not None:
                self.assertEqual(native_mode.dmSize, C.sizeof(W._DEVMODE))
                self.assertGreater(native_mode.dmPelsWidth, 0)
                self.assertGreater(native_mode.dmPelsHeight, 0)
        for source in sources:
            self.assertTrue(source.device)


if __name__ == "__main__":
    unittest.main()
