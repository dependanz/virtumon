"""Guarded CCD clone separation; synthetic topology, no native display writes.

The fixture models the reported Windows shape without retaining hardware IDs:
the owned virtual target has its own virtual-aware CCD source, but GDI exposes
both target interfaces through the existing primary source. Its advertised
signal initially differs from the requested desktop mode.
"""

import ctypes as C
import unittest
from unittest.mock import patch

from virtumon import windows_layout as W
from test_windows_layout import FakeAPI, Identity, mode, screen, snapshot


PHYSICAL = Identity(101, 1, 11)
OWNED = Identity(202, -2, 22)


def copied_snapshot(value):
    return W._Snapshot([W._copy(path) for path in value.paths],
                       [W._copy(item) for item in value.modes],
                       virtual_mode_aware=value.virtual_mode_aware)


class CloneAPI(FakeAPI):
    """Model the complete supplied-topology call, including other displays."""

    def __init__(self):
        super().__init__()
        self.screens = [
            screen(101, 1, 11, 0, "PHYSICAL", width=2560, height=1600, refresh=120),
            screen(202, -2, 22, 0, "VIRTUAL", width=2560, height=1600, refresh=59.94),
        ]
        self.screens[0].update(target_width=3840, target_height=2400, scaling=4)
        self.screens[1].update(target_width=3840, target_height=2160, scaling=1)
        self.sources = [
            W._DeviceSource("VIRTUAL", 0, ()),
            W._DeviceSource("PHYSICAL", 1 | 4, ("monitor-physical", "monitor-owned")),
        ]
        self.interfaces = {W._key(PHYSICAL): "monitor-physical", W._key(OWNED): "monitor-owned"}
        self.baseline = [W.NativeDisplay("PHYSICAL", 0, 0, 2560, 1600, 120.0, True, 101, 1, 11)]
        self.ccd_calls = []
        self.ccd_validate_result = self.ccd_apply_result = 0
        self.on_validate_ccd = self.on_apply_ccd = None
        self.extra_paths = []
        self.reverse_paths = False
        self.renumber_modes = False
        self.applied = False
        self.reads_after_apply = 0
        self.reclone_on_read = None
        self.initial_owned = dict(self.screens[1])

    def snapshot(self, active_only=True, *, virtual_mode_aware=True):
        if not virtual_mode_aware:
            raise AssertionError("Clone separation must not use remapped legacy CCD identities")
        if self.applied:
            self.reads_after_apply += 1
            if self.reads_after_apply == self.reclone_on_read:
                self.reclone_on_read = None
                self.screens[1] = dict(self.initial_owned)
                self.sources[0].flags, self.sources[0].monitor_paths = 0, ()
                self.sources[1].monitor_paths = ("monitor-physical", "monitor-owned")
        visible = [item for item in self.screens if item["active"] or not active_only]
        current = snapshot(visible)
        for item, path in zip(visible, current.paths):
            if not item["active"]:
                continue
            target = W._mode(current, path, source=False).targetMode
            target.activeSize = W._REGION(item.get("target_width", item["width"]),
                                         item.get("target_height", item["height"]))
            target.totalSize = W._REGION(target.activeSize.cx + 160, target.activeSize.cy + 40)
            target.pixelRate = item.get("target_pixel_rate", target.totalSize.cx * target.totalSize.cy * 60)
            target.hSyncFreq = W._RATIONAL(target.pixelRate, target.totalSize.cx)
            target.vSyncFreq = W._RATIONAL(round(item.get("target_refresh", item["refresh"]) * 1000), 1000)
            target.scanLineOrdering = item.get("scan_line", 1)
            path.targetInfo.refreshRate = target.vSyncFreq
            path.targetInfo.scanLineOrdering = target.scanLineOrdering
            path.targetInfo.scaling = item.get("scaling", 1)
            desktop = W._MODE_INFO()
            desktop.infoType, desktop.id = 3, path.targetInfo.id
            desktop.adapterId = path.targetInfo.adapterId
            desktop.desktopImageInfo.PathSourceSize = W._POINT(item["width"], item["height"])
            desktop.desktopImageInfo.DesktopImageRegion = W._RECT(0, 0, item["width"], item["height"])
            desktop.desktopImageInfo.DesktopImageClip = W._RECT(0, 0, item["width"], item["height"])
            path.targetInfo.modeInfoIdx = (path.targetInfo.modeInfoIdx & 0xFFFF0000) | len(current.modes)
            current.modes.append(desktop)
        current.paths.extend(W._copy(path) for path in self.extra_paths)
        if self.reverse_paths:
            current.paths.reverse()
        if self.renumber_modes:
            remap = {index: len(current.modes) - index - 1 for index in range(len(current.modes))}
            current.modes.reverse()
            for path in current.paths:
                source_index = path.sourceInfo.modeInfoIdx >> 16
                signal_index = path.targetInfo.modeInfoIdx >> 16
                desktop_index = path.targetInfo.modeInfoIdx & 0xFFFF
                if source_index != 0xFFFF:
                    path.sourceInfo.modeInfoIdx = (remap[source_index] << 16) | (path.sourceInfo.modeInfoIdx & 0xFFFF)
                path.targetInfo.modeInfoIdx = ((remap[signal_index] if signal_index != 0xFFFF else 0xFFFF) << 16 |
                                               (remap[desktop_index] if desktop_index != 0xFFFF else 0xFFFF))
        return current

    def target_path(self, path):
        return self.interfaces[W._path_key(path)]

    def device_sources(self):
        return [W._DeviceSource(source.device, source.flags, tuple(source.monitor_paths)) for source in self.sources]

    def current_mode(self, device):
        matches = [source for source in self.sources if source.device.casefold() == device.casefold()]
        if len(matches) != 1 or not matches[0].flags & 1:
            return None
        item = next(item for item in self.screens if item["device"].casefold() == device.casefold())
        return mode(item["width"], item["height"], item["refresh"], item["x"], item["y"])

    def set_config(self, proposed, *, validate):
        self.ccd_calls.append((copied_snapshot(proposed), validate))
        if validate:
            if self.on_validate_ccd:
                self.on_validate_ccd(self)
            return self.ccd_validate_result
        if self.ccd_apply_result:
            return self.ccd_apply_result
        active_keys = {W._path_key(path) for path in proposed.paths if path.flags & W.ACTIVE}
        for item in self.screens:
            key = item["low"], item["high"], item["target"]
            item["active"] = key in active_keys
            if not item["active"]:
                continue
            path = next(path for path in proposed.paths if W._path_key(path) == key)
            source = W._mode(proposed, path, source=True).sourceMode
            target_mode = W._mode(proposed, path, source=False)
            item.update(x=source.position.x, y=source.position.y, width=source.width, height=source.height,
                        scaling=path.targetInfo.scaling)
            if target_mode is None:
                item.update(target_width=source.width, target_height=source.height,
                            refresh=path.targetInfo.refreshRate.Numerator / path.targetInfo.refreshRate.Denominator)
            else:
                target = target_mode.targetMode
                item.update(target_width=target.activeSize.cx, target_height=target.activeSize.cy,
                            refresh=target.vSyncFreq.Numerator / target.vSyncFreq.Denominator)
        # Distinct supplied source positions create distinct GDI desktop views.
        views = {}
        for source in self.sources:
            source.flags, source.monitor_paths = 0, ()
        for item in self.screens:
            if not item["active"]:
                continue
            source = next(source for source in self.sources if source.device == item["device"])
            owner = views.setdefault((item["x"], item["y"]), source)
            owner.flags = 1 | (4 if (item["x"], item["y"]) == (0, 0) else 0)
            owner.monitor_paths += (self.interfaces[(item["low"], item["high"], item["target"])],)
        if self.on_apply_ccd:
            self.on_apply_ccd(self)
        self.applied = True
        return 0


class CloneSeparationTests(unittest.TestCase):
    def setUp(self):
        self.api = CloneAPI()
        self.clock = 0.0
        self.api_patch = patch.object(W, "_get_api", return_value=self.api)
        self.api_mock = self.api_patch.start()
        self.addCleanup(self.api_patch.stop)
        clock_patch = patch.object(W.time, "monotonic", side_effect=lambda: self.clock)
        sleep_patch = patch.object(W.time, "sleep", side_effect=self.advance)
        clock_patch.start()
        sleep_patch.start()
        self.addCleanup(clock_patch.stop)
        self.addCleanup(sleep_patch.stop)

    def advance(self, delay):
        self.clock += delay

    def configure(self, **overrides):
        arguments = dict(identity=OWNED, width=1280, height=720, refresh_hz=60,
                         x=2560, y=0, timeout=1, guard=lambda: True,
                         creation_baseline=self.api.baseline)
        arguments.update(overrides)
        return W.configure_display(**arguments)

    def reset_api(self):
        self.api = CloneAPI()
        self.api_mock.return_value = self.api
        self.clock = 0.0

    def test_confirmed_cross_adapter_clone_becomes_only_requested_extended_monitor(self):
        original = self.api.snapshot()
        physical = dict(self.api.screens[0])
        result = self.configure()
        self.assertEqual((result.device, result.x, result.y, result.width, result.height, result.refresh_hz, result.primary),
                         ("VIRTUAL", 2560, 0, 1280, 720, 60, False))
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls, "Clone separation must not use ChangeDisplaySettingsEx on the shared GDI view")
        self.assertEqual(self.api.screens[0], physical)
        for proposed, _validate in self.api.ccd_calls:
            self.assertTrue(proposed.virtual_mode_aware)
            self.assertEqual([W._path_key(path) for path in proposed.paths],
                             [W._path_key(path) for path in original.paths])
            self.assertEqual(bytes(proposed.paths[0]), bytes(original.paths[0]))
            for index in (original.paths[0].sourceInfo.modeInfoIdx >> 16,
                          original.paths[0].targetInfo.modeInfoIdx >> 16,
                          original.paths[0].targetInfo.modeInfoIdx & 0xFFFF):
                self.assertEqual(bytes(proposed.modes[index]), bytes(original.modes[index]))
            owned = proposed.paths[1]
            self.assertEqual(W._source_key(owned), (202, -2, 0))
            self.assertEqual(owned.targetInfo.modeInfoIdx, 0xFFFFFFFF)
            self.assertEqual(owned.targetInfo.refreshRate.Numerator / owned.targetInfo.refreshRate.Denominator, 60)
        final = W._records(self.api, self.api.snapshot())
        self.assertEqual(len(final), 2)
        self.assertTrue(next(record for record in final if W._key(record.display) == W._key(PHYSICAL)).display.primary)

    def test_clone_separation_requires_both_precreation_baseline_and_live_guard(self):
        for overrides in ({"creation_baseline": None}, {"guard": None}, {"guard": lambda: False}):
            with self.subTest(overrides=tuple(overrides)):
                self.reset_api()
                with self.assertRaises(W.WindowsLayoutError):
                    self.configure(**overrides)
                self.assertFalse(self.api.ccd_calls)
                self.assertFalse(self.api.calls)

    def test_target_already_in_creation_baseline_is_not_eligible_for_separation(self):
        self.api.baseline.append(W.NativeDisplay("VIRTUAL", 0, 0, 2560, 1600, 59.94, False, 202, -2, 22))
        with self.assertRaises(W.WindowsLayoutError):
            self.configure()
        self.assertFalse(self.api.ccd_calls)

    def test_existing_desktop_must_still_match_creation_baseline(self):
        self.api.screens[0]["refresh"] = 119
        with self.assertRaises(W.WindowsLayoutError):
            self.configure()
        self.assertFalse(self.api.ccd_calls)

    def test_ambiguous_or_unproven_clone_associations_never_reach_set_display_config(self):
        for case in ("wrong-luid", "duplicate-target", "shared-source", "own-monitor-child",
                     "duplicate-gdi-source", "unknown-peer-child", "unequal-source-rectangle"):
            with self.subTest(case=case):
                self.reset_api()
                overrides = {}
                if case == "wrong-luid":
                    overrides["identity"] = Identity(202, 2, 22)
                elif case == "duplicate-target":
                    self.api.extra_paths = [self.api.snapshot().paths[1]]
                elif case == "shared-source":
                    shared = W._copy(self.api.snapshot().paths[1])
                    shared.targetInfo.id = 23
                    self.api.extra_paths = [shared]
                elif case == "own-monitor-child":
                    self.api.sources[0].monitor_paths = ("unexpected-owned-view-child",)
                elif case == "duplicate-gdi-source":
                    self.api.sources.append(W._DeviceSource("virtual", 0, ()))
                elif case == "unknown-peer-child":
                    self.api.sources[1].monitor_paths += ("unrelated-monitor-interface",)
                else:
                    self.api.screens[1]["x"] = 100
                with self.assertRaises(W.WindowsLayoutError):
                    self.configure(**overrides)
                self.assertFalse(self.api.ccd_calls)
                self.assertFalse(self.api.calls)

    def test_validation_failure_never_applies_or_retries_with_relaxed_modes(self):
        self.api.ccd_validate_result = 1610
        with self.assertRaises(W.WindowsLayoutError):
            self.configure()
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True])
        self.assertFalse(self.api.calls)

    def test_physical_signal_change_after_validation_aborts_before_apply(self):
        self.api.on_validate_ccd = lambda api: api.screens[0].update(target_pixel_rate=987654321)
        with self.assertRaises(W.WindowsLayoutError):
            self.configure()
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True])

    def test_path_priority_change_after_validation_aborts_before_apply(self):
        self.api.on_validate_ccd = lambda api: setattr(api, "reverse_paths", True)
        with self.assertRaises(W.WindowsLayoutError):
            self.configure()
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True])

    def test_mode_array_renumbering_preserves_semantics_before_apply_and_readback(self):
        self.api.on_validate_ccd = lambda api: setattr(api, "renumber_modes", True)
        result = self.configure()
        self.assertEqual((result.width, result.height, result.x), (1280, 720, 2560))
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True, False])

    def test_target_interface_change_after_validation_aborts_before_apply(self):
        self.api.on_validate_ccd = lambda api: api.interfaces.__setitem__(W._key(OWNED), "replacement-monitor-interface")
        with self.assertRaises(W.WindowsLayoutError):
            self.configure()
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True])

    def test_ownership_loss_during_validation_prevents_apply(self):
        healthy = [True]
        self.api.on_validate_ccd = lambda _api: healthy.__setitem__(0, False)
        with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
            self.configure(guard=lambda: healthy[0])
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True])

    def test_failed_apply_does_not_replay_the_previous_whole_topology(self):
        self.api.ccd_apply_result = 31
        with self.assertRaises(W.WindowsLayoutError):
            self.configure()
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls)

    def test_bad_source_or_target_readback_is_not_success_and_has_no_global_rollback(self):
        for change in ({"width": 1279}, {"target_refresh": 59.94},
                       {"target_width": 3840, "target_height": 2160}, {"scan_line": 2}):
            with self.subTest(change=change):
                self.reset_api()
                self.api.on_apply_ccd = lambda api, changes=change: api.screens[1].update(changes)
                with self.assertRaises(W.WindowsLayoutError):
                    self.configure()
                self.assertEqual([validate for _, validate in self.api.ccd_calls], [True, False])
                self.assertFalse(self.api.calls)

    def test_postapply_gdi_association_changes_cannot_report_success(self):
        for case in ("wrong-owned-child", "owned-still-on-host", "other-child-changed", "primary-switched"):
            with self.subTest(case=case):
                self.reset_api()

                def corrupt(api):
                    if case == "wrong-owned-child":
                        api.sources[0].monitor_paths = ("wrong-monitor",)
                    elif case == "owned-still-on-host":
                        api.sources[1].monitor_paths += ("monitor-owned",)
                    elif case == "other-child-changed":
                        api.sources[1].monitor_paths = ("replacement-physical-monitor",)
                    else:
                        api.sources[0].flags = 1 | 4
                        api.sources[1].flags = 1

                self.api.on_apply_ccd = corrupt
                with self.assertRaises(W.WindowsLayoutError):
                    self.configure()
                self.assertEqual([validate for _, validate in self.api.ccd_calls], [True, False])
                self.assertFalse(self.api.calls)

    def test_reclone_after_verified_separation_is_not_automatically_applied_again(self):
        # First two reads verify the extension; the following prepare snapshot
        # observes Windows or another controller reverting it to a clone.
        self.api.reclone_on_read = 3
        with self.assertRaises(W.WindowsLayoutError):
            self.configure()
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls)

    def test_ownership_loss_after_apply_prevents_readback_success_or_topology_rollback(self):
        healthy = [True]
        self.api.on_apply_ccd = lambda _api: healthy.__setitem__(0, False)
        with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
            self.configure(guard=lambda: healthy[0])
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls)

    def test_unrelated_physical_change_after_apply_is_reported_without_broad_rollback(self):
        self.api.on_apply_ccd = lambda api: api.screens[0].update(target_pixel_rate=987654321)
        with self.assertRaises(W.WindowsLayoutError):
            self.configure()
        self.assertEqual([validate for _, validate in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls)


class SetDisplayConfigABITests(unittest.TestCase):
    def test_explicit_virtual_aware_validate_and_apply_flags_and_fixed_width_signature(self):
        class Function:
            def __init__(self, callback=lambda *args: 0):
                self.callback = callback

            def __call__(self, *args):
                return self.callback(*args)

        class DLL:
            pass

        dll, seen = DLL(), []
        original = CloneAPI().snapshot()

        def set_config(path_count, paths, mode_count, modes, flags):
            path_values = C.cast(paths, C.POINTER(W._PATH_INFO))
            mode_values = C.cast(modes, C.POINTER(W._MODE_INFO))
            seen.append((path_count, mode_count, flags))
            self.assertEqual(bytes(path_values[0]), bytes(original.paths[0]))
            self.assertEqual(bytes(mode_values[0]), bytes(original.modes[0]))
            return 0

        for name in ("GetDisplayConfigBufferSizes", "QueryDisplayConfig", "DisplayConfigGetDeviceInfo",
                     "EnumDisplayDevicesW", "EnumDisplaySettingsExW", "ChangeDisplaySettingsExW"):
            setattr(dll, name, Function())
        dll.SetDisplayConfig = Function(set_config)
        api = W._Win32(dll)
        self.assertEqual(api.set_config(original, validate=True), 0)
        self.assertEqual(api.set_config(original, validate=False), 0)
        self.assertEqual(seen, [(len(original.paths), len(original.modes), 0x8060),
                                (len(original.paths), len(original.modes), 0x80A0)])
        self.assertIs(dll.SetDisplayConfig.restype, C.c_int32)
        self.assertEqual(dll.SetDisplayConfig.argtypes,
                         [W.U32, C.POINTER(W._PATH_INFO), W.U32, C.POINTER(W._MODE_INFO), W.U32])


if __name__ == "__main__":
    unittest.main()
