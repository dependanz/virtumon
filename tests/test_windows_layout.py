"""ABI and guarded-layout tests; these do not claim a real Windows hardware test."""

import ctypes as C
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
        self.sources = [W._DeviceSource("VIRTUAL", 0 if inactive else 1, ("monitor-interface-virtual",))]
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
        self.api.sources = []
        clock = [0.0]

        def settle(delay):
            self.assertFalse(self.api.calls, "No mode test or write is safe before association resolves")
            clock[0] += delay
            self.api.sources = [W._DeviceSource("VIRTUAL", 0, ("monitor-interface-virtual",))]

        with patch.object(W.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(W.time, "sleep", side_effect=settle):
            actual = self.configure(timeout=1)
        self.assertTrue(actual.active)
        self.assertEqual((actual.device, actual.width, actual.height), ("VIRTUAL", 1280, 720))
        self.assertAlmostEqual(clock[0], 0.1)
        self.assertEqual([flags for _, _, flags in self.api.calls], [2, 0])

    def test_inactive_resolution_timeout_is_bounded_and_reports_latest_cause(self):
        self.api.screens[1]["active"] = False
        self.api.sources = []
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
        self.api.sources = [W._DeviceSource("PHYSICAL", 0, ("monitor-interface-virtual",))]
        with self.assertRaisesRegex(W.WindowsLayoutError, "clone mode"):
            self.configure(identity=Identity(100, 1, 8))
        self.assertFalse(self.api.calls)

    def test_clone_mode_is_rejected_before_any_write(self):
        self.api.screens[1].update(low=100, high=1, target=8, source=0, device="PHYSICAL", x=0)
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
        self.api.sources = []
        current, clock = [True], [0.0]

        def settle_in_new_generation(delay):
            clock[0] += delay
            current[0] = False
            self.api.sources = [W._DeviceSource("VIRTUAL", 0, ("monitor-interface-virtual",))]

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


@unittest.skipUnless(sys.platform == "win32", "Read-only native Windows API probe")
class NativeWindowsProbeTests(unittest.TestCase):
    def test_real_user32_discovery_without_virtual_driver(self):
        """Exercise actual User32 structures/prototypes; never change a mode."""
        api = W._Win32()
        try:
            current = api.snapshot()
            records = W._records(api, current)
            sources = api.device_sources()
        except W.WindowsLayoutError as exc:
            if exc.winerror in (5, 50):
                self.skipTest(f"Runner has no accessible console display session/WDDM: {exc}")
            raise
        self.assertEqual(len(records), sum(bool(p.flags & W.ACTIVE) for p in current.paths))
        for record in records:
            self.assertTrue(record.display.device)
            self.assertGreater(record.display.width, 0)
            self.assertGreater(record.display.height, 0)
            native_mode = api.current_mode(record.display.device)
            if native_mode is not None:
                self.assertEqual(native_mode.dmSize, C.sizeof(W._DEVMODE))
                self.assertGreater(native_mode.dmPelsWidth, 0)
                self.assertGreater(native_mode.dmPelsHeight, 0)
        for source in sources:
            self.assertTrue(source.device)


if __name__ == "__main__":
    unittest.main()
