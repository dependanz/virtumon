"""Synthetic three-display CCD reconciliation; does not touch real hardware."""

import unittest
from unittest.mock import patch

from virtumon import windows_layout as W
from virtumon.windows_multi import reconcile_new
from test_windows_clone import CloneAPI, PHYSICAL, OWNED, copied_snapshot
from test_windows_layout import Identity, screen


CHAT = Identity(202, -2, 23)


class TwoDisplayAPI(CloneAPI):
    def __init__(self):
        super().__init__()
        # Pre-add geometry, independent attached GDI monitor.
        self.screens[1].update(x=2560, y=0, width=1920, height=1080,
                               refresh=60, target_width=1920, target_height=1080)
        self.sources[0].flags = 1
        self.sources[0].monitor_paths = ("monitor-owned",)
        self.sources[1].monitor_paths = ("monitor-physical",)
        self.baseline = [
            W.NativeDisplay("PHYSICAL", 0, 0, 2560, 1600, 120, True, 101, 1, 11),
            W.NativeDisplay("VIRTUAL", 2560, 0, 1920, 1080, 60, False, 202, -2, 22),
        ]
        self.pre_add = copied_snapshot(self.snapshot()), self.device_sources()
        # Windows hotplug displaced the existing virtual output into the
        # physical screen's clone group, while independently attaching chat.
        self.screens[1].update(x=0, y=0, width=2560, height=1600,
                               target_width=2880, target_height=1620)
        self.screens.append(
            screen(202, -2, 23, 1, "CHAT", x=2560, y=0,
                   width=1080, height=1920, refresh=60)
        )
        self.sources[0].flags = 0
        self.sources[0].monitor_paths = ()
        self.sources[1].monitor_paths = ("monitor-physical", "monitor-owned")
        self.sources.append(W._DeviceSource("CHAT", 1, ("monitor-new",)))
        self.interfaces[W._key(CHAT)] = "monitor-new"
        self.physical_before = dict(self.screens[0])


class MultiMonitorTests(unittest.TestCase):
    def setUp(self):
        self.api = TwoDisplayAPI()
        self.clock = 0.0
        self.patch_api = patch.object(W, "_get_api", return_value=self.api)
        self.patch_api.start()
        self.addCleanup(self.patch_api.stop)
        clock = patch.object(W.time, "monotonic", side_effect=lambda: self.clock)
        sleep = patch.object(W.time, "sleep", side_effect=self.advance)
        clock.start()
        sleep.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleep.stop)

    def advance(self, dt):
        self.clock += dt

    def reconcile(self, **kw):
        args = dict(api=self.api,
                    observation=(self.api.snapshot(), self.api.device_sources(), {}, None),
                    new_key=W._key(CHAT), baseline=self.api.baseline,
                    owned_before=[self.api.baseline[1]], original=self.api.pre_add,
                    width=1080, height=1920, refresh=60, x=4480, y=0,
                    deadline=1, guard=lambda: True)
        args.update(kw)
        return reconcile_new(**args)

    def test_recloned_existing_monitor_restored_and_new_one_placed(self):
        old = self.api.snapshot()
        result = self.reconcile()
        self.assertEqual(len(result), 3)
        self.assertEqual([flag for _, flag in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls, "Never issue GDI change-mode on a cloned source")
        self.assertEqual(self.api.screens[0], self.api.physical_before)
        by_key = {W._key(r.display): r.display for r in result}
        self.assertEqual((by_key[W._key(OWNED)].width,
                          by_key[W._key(OWNED)].height,
                          by_key[W._key(OWNED)].x,
                          by_key[W._key(OWNED)].y,
                          by_key[W._key(OWNED)].primary), (1920, 1080, 2560, 0, False))
        self.assertEqual((by_key[W._key(CHAT)].width,
                          by_key[W._key(CHAT)].height,
                          by_key[W._key(CHAT)].x,
                          by_key[W._key(CHAT)].y,
                          by_key[W._key(CHAT)].primary), (1080, 1920, 4480, 0, False))
        for candidate, validate in self.api.ccd_calls:
            self.assertEqual(bytes(candidate.paths[0]), bytes(old.paths[0]))
            self.assertEqual(W._path_key(candidate.paths[0]), W._key(PHYSICAL))
            self.assertEqual(candidate.paths[1].targetInfo.modeInfoIdx, 0xFFFFFFFF)
            self.assertEqual(candidate.paths[2].targetInfo.modeInfoIdx, 0xFFFFFFFF)
            for index in (old.paths[0].sourceInfo.modeInfoIdx >> 16,
                          old.paths[0].targetInfo.modeInfoIdx >> 16,
                          old.paths[0].targetInfo.modeInfoIdx & 0xFFFF):
                self.assertEqual(bytes(candidate.modes[index]), bytes(old.modes[index]))

    def test_pre_add_baseline_and_exact_identity_required(self):
        for change in ("no_baseline", "wrong_new_id", "missing_owned",
                       "wrong_pre_snapshot", "nonowned_signal_changed",
                       "duplicate_interface", "wrong_child", "other_host"):
            with self.subTest(change=change):
                self.setUp()
                opts = {}
                if change == "no_baseline":
                    opts["original"] = None
                elif change == "wrong_new_id":
                    opts["new_key"] = (202, 2, 23)
                elif change == "missing_owned":
                    opts["owned_before"] = []
                elif change == "wrong_pre_snapshot":
                    opts["original"] = (self.api.snapshot(), self.api.device_sources())
                elif change == "nonowned_signal_changed":
                    self.api.screens[0]["target_pixel_rate"] = 999999
                elif change == "duplicate_interface":
                    self.api.interfaces[W._key(CHAT)] = "monitor-owned"
                elif change == "wrong_child":
                    self.api.sources[1].monitor_paths = ("monitor-physical", "other-child")
                elif change == "other_host":
                    self.api.sources[1].monitor_paths = ("monitor-physical",)
                    self.api.sources[2].monitor_paths += ("monitor-owned",)
                self.assertIsNone(self.reconcile(**opts))
                self.assertFalse(self.api.ccd_calls)

    def test_validate_failure_keeps_other_display_unchanged(self):
        self.api.ccd_validate_result = 1610
        with self.assertRaises(W.WindowsLayoutError):
            self.reconcile()
        self.assertEqual([v for _, v in self.api.ccd_calls], [True])
        self.assertEqual(self.api.screens[0], self.api.physical_before)

    def test_topology_change_after_validation_prevents_apply(self):
        self.api.on_validate_ccd = lambda api: api.screens[0].update(target_pixel_rate=88888888)
        with self.assertRaisesRegex(W.WindowsLayoutError, "Desktop changed"):
            self.reconcile()
        self.assertEqual([v for _, v in self.api.ccd_calls], [True])

    def test_lost_ownership_after_validation_never_applies(self):
        alive = [True]
        self.api.on_validate_ccd = lambda api: alive.__setitem__(0, False)
        with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
            self.reconcile(guard=lambda: alive[0])
        self.assertEqual([v for _, v in self.api.ccd_calls], [True])

    def test_apply_failure_never_replays_old_desktop_snapshot(self):
        self.api.ccd_apply_result = 31
        with self.assertRaises(W.WindowsLayoutError):
            self.reconcile()
        self.assertEqual([v for _, v in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls)

    def test_bad_new_signal_readback_is_not_success(self):
        self.api.on_apply_ccd = lambda api: api.screens[2].update(target_refresh=59.94)
        with self.assertRaises(W.WindowsLayoutError):
            self.reconcile()
        self.assertEqual([v for _, v in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls)

    def test_recloning_an_owned_output_after_apply_is_not_success(self):
        def reclone(api):
            api.sources[0].flags = 0
            api.sources[0].monitor_paths = ()
            api.sources[1].monitor_paths += ("monitor-owned",)
        self.api.on_apply_ccd = reclone
        with self.assertRaises(W.WindowsLayoutError):
            self.reconcile()
        self.assertEqual([v for _, v in self.api.ccd_calls], [True, False])


if __name__ == "__main__":
    unittest.main()
