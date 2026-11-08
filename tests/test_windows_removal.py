"""Selective SudoVDA removal with an owned survivor; synthetic CCD only."""

import unittest
from unittest.mock import patch

from virtumon import windows_layout as W
from virtumon.windows_multi import reconcile_new
from test_windows_multi import TwoDisplayAPI, CHAT, OWNED


class SelectiveRemovalTopologyTests(unittest.TestCase):
    def setUp(self):
        self.api = TwoDisplayAPI()
        self.clock = 0.0
        self.patch_api = patch.object(W, "_get_api", return_value=self.api)
        self.patch_api.start()
        self.addCleanup(self.patch_api.stop)
        clock_patch = patch.object(W.time, "monotonic", side_effect=lambda: self.clock)
        sleep_patch = patch.object(W.time, "sleep", side_effect=self.advance)
        clock_patch.start()
        sleep_patch.start()
        self.addCleanup(clock_patch.stop)
        self.addCleanup(sleep_patch.stop)

        # Create the fully independent three-display topology first.
        reconcile_new(
            self.api,
            (self.api.snapshot(), self.api.device_sources(), {}, None),
            W._key(CHAT), self.api.baseline, [self.api.baseline[1]],
            self.api.pre_add, 1080, 1920, 60, 4480, 0, 1, lambda: True,
        )
        self.assertEqual(len(self.api.ccd_calls), 2)
        pre = self.api.snapshot()
        self.before = [record.display for record in W._records(self.api, pre)]
        self.captured = (pre, self.api.device_sources())
        self.survivor = next(item for item in self.before if W._key(item) == W._key(OWNED))
        self.physical = dict(self.api.screens[0])
        self._remove_chat_and_reclone_work()
        from virtumon.windows_removal import _physical_signature
        original_physical = _physical_signature(
            self.captured[0], {W._key(OWNED), W._key(CHAT)}
        )
        current_physical = _physical_signature(
            self.api.snapshot(), {W._key(OWNED)}
        )
        self.assertEqual(original_physical, current_physical,
                         "Pre/post removal physical CCD fixture must have an unchanged signal.")

    def advance(self, dt):
        self.clock += dt

    def _remove_chat_and_reclone_work(self):
        # The native driver has removed only CHAT, but Windows also re-cloned
        # the work output onto the primary screen. This is the observed bug.
        self.api.screens = [screen for screen in self.api.screens
                            if (screen["low"], screen["high"], screen["target"]) != W._key(CHAT)]
        work = self.api.screens[1]
        work.update(x=0, y=0, width=2560, height=1600,
                    target_width=2880, target_height=1620, refresh=60)
        self.api.sources[0].flags = 0
        self.api.sources[0].monitor_paths = ()
        self.api.sources[1].monitor_paths = ("monitor-physical", "monitor-owned")
        self.api.sources[2].flags = 0
        self.api.sources[2].monitor_paths = ()
        self.api.ccd_calls.clear()
        self.api.applied = False

    def restore(self, **overrides):
        kw = dict(
            survivor=self.survivor, before=self.before,
            captured=self.captured, removed_identity=CHAT,
            guard=lambda: True, timeout=1,
        )
        kw.update(overrides)
        return W.restore_after_removal(**kw)

    def test_removing_chat_reclones_work_and_guarded_restore_preserves_physical(self):
        result = self.restore()
        self.assertEqual((result.x, result.y, result.width, result.height, result.refresh_hz,
                          result.primary), (2560, 0, 1920, 1080, 60, False))
        self.assertEqual(self.api.screens[0], self.physical)
        self.assertEqual([v for _, v in self.api.ccd_calls], [True, False])
        self.assertEqual(len(W._records(self.api, self.api.snapshot())), 2)
        self.assertFalse(self.api.calls)
        from virtumon.windows_removal import _physical_signature
        physical_before = _physical_signature(
            self.captured[0], {W._key(OWNED), W._key(CHAT)}
        )
        for proposal, _validate in self.api.ccd_calls:
            self.assertEqual(W._path_key(proposal.paths[1]), W._key(OWNED))
            # The removal topology contains fewer mode entries than the
            # three-display capture. Compare exact non-owned mode *content*
            # by path identity, never unrelated array index numbers.
            self.assertEqual(
                _physical_signature(proposal, {W._key(OWNED)}),
                physical_before,
            )

    def test_survivor_keeps_independent_mode_without_unnecessary_setdisplayconfig(self):
        self.api.screens[1].update(x=2560, y=0, width=1920, height=1080,
                                   target_width=1920, target_height=1080)
        self.api.sources[0].flags = 1
        self.api.sources[0].monitor_paths = ("monitor-owned",)
        self.api.sources[1].monitor_paths = ("monitor-physical",)
        result = self.restore()
        self.assertEqual(W._key(result), W._key(OWNED))
        self.assertEqual(self.api.ccd_calls, [])
        self.assertEqual(self.api.screens[0], self.physical)

    def test_different_physical_signal_prevents_any_repair_write(self):
        self.api.screens[0]["target_pixel_rate"] = 123456789
        with self.assertRaisesRegex(W.WindowsLayoutError, "non-owned display"):
            self.restore()
        self.assertEqual(self.api.ccd_calls, [])

    def test_no_live_ownership_prevents_any_repair_write(self):
        with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
            self.restore(guard=lambda: False)
        self.assertEqual(self.api.ccd_calls, [])

    def test_wrong_removed_identity_prevents_any_repair_write(self):
        with self.assertRaisesRegex(W.WindowsLayoutError, "identities are ambiguous"):
            self.restore(removed_identity=OWNED)
        self.assertEqual(self.api.ccd_calls, [])

    def test_validation_failure_never_applies(self):
        self.api.ccd_validate_result = 1610
        with self.assertRaises(W.WindowsLayoutError):
            self.restore()
        self.assertEqual([v for _, v in self.api.ccd_calls], [True])
        self.assertEqual(self.api.screens[0], self.physical)

    def test_ownership_loss_during_validation_blocks_apply(self):
        healthy = [True]
        self.api.on_validate_ccd = lambda _api: healthy.__setitem__(0, False)
        with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
            self.restore(guard=lambda: healthy[0])
        self.assertEqual([v for _, v in self.api.ccd_calls], [True])

    def test_postapply_signal_mismatch_does_not_report_success(self):
        self.api.on_apply_ccd = lambda api: api.screens[1].update(target_refresh=59.94)
        with self.assertRaises(W.WindowsLayoutError):
            self.restore()
        self.assertEqual([v for _, v in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls)

    def test_apply_failure_does_not_replay_old_desktop(self):
        self.api.ccd_apply_result = 31
        with self.assertRaises(W.WindowsLayoutError):
            self.restore()
        self.assertEqual([v for _, v in self.api.ccd_calls], [True, False])
        self.assertFalse(self.api.calls)


if __name__ == "__main__":
    unittest.main()
