"""Regression: SudoVDA reclones an existing output after GDI CDS_TEST.

The native 2026-11-09 diagnostic had phase='after-mode-test'. Both monitors
may be initially GDI-attached; the mode test can cause the older owned
monitor to lose its GDI source. Never run another GDI apply after a guarded
full-layout reconciliation. Synthetic only; no real display driver I/O.
"""

import unittest
from unittest.mock import patch

from virtumon import windows_layout as W
from test_windows_multi import TwoDisplayAPI, CHAT, OWNED


class LateModeTestCloneTests(unittest.TestCase):
    def setUp(self):
        self.api = TwoDisplayAPI()
        self.api.screens[1].update(x=2560, y=0, width=1920, height=1080,
                                   refresh=60, target_width=1920, target_height=1080)
        self.api.screens[2].update(x=2560, y=0, width=1080, height=1920)
        self.api.sources[0].flags = 1
        self.api.sources[0].monitor_paths = ("monitor-owned",)
        self.api.sources[1].flags = 1 | 4
        self.api.sources[1].monitor_paths = ("monitor-physical",)
        self.api.sources[2].flags = 1
        self.api.sources[2].monitor_paths = ("monitor-new",)
        self.physical = dict(self.api.screens[0])
        self.clock = 0.0
        api_patch = patch.object(W, "_get_api", return_value=self.api)
        clock_patch = patch.object(W.time, "monotonic", side_effect=lambda: self.clock)
        sleep_patch = patch.object(W.time, "sleep", side_effect=self.advance)
        for p in (api_patch, clock_patch, sleep_patch):
            p.start()
            self.addCleanup(p.stop)

    def advance(self, seconds):
        self.clock += seconds

    def displace_work(self, _api):
        work = self.api.screens[1]
        work.update(x=0, y=0, width=2560, height=1600,
                    target_width=2880, target_height=1620, refresh=60)
        self.api.sources[0].flags = 0
        self.api.sources[0].monitor_paths = ()
        self.api.sources[1].monitor_paths = ("monitor-physical", "monitor-owned")

    def configure(self, **overrides):
        options = dict(
            identity=CHAT, width=1080, height=1920, refresh_hz=60,
            x=4480, y=0, timeout=1, guard=lambda: True,
            creation_baseline=self.api.baseline,
            owned_baseline=[self.api.baseline[1]],
            captured_baseline=self.api.pre_add,
        )
        options.update(overrides)
        return W.configure_display(**options)

    def test_late_cloning_reconciles_without_any_second_gdi_apply(self):
        self.api.on_test = self.displace_work
        result = self.configure()
        self.assertEqual((result.x, result.y, result.width, result.height,
                          result.refresh_hz, result.primary),
                         (4480, 0, 1080, 1920, 60, False))
        self.assertEqual([(name, flags) for name, _, flags in self.api.calls],
                         [("CHAT", W.CDS_TEST)])
        self.assertEqual([flag for _, flag in self.api.ccd_calls], [True, False])
        self.assertEqual(self.api.screens[0], self.physical)
        active = W.list_native_displays()
        self.assertEqual(len(active), 3)
        work = next(item for item in active if W._key(item) == W._key(OWNED))
        self.assertEqual((work.x, work.y, work.width, work.height, work.primary),
                         (2560, 0, 1920, 1080, False))

    def test_changed_physical_signal_fails_closed_without_ccd_apply(self):
        def displacement(api):
            self.displace_work(api)
            api.screens[0]["target_pixel_rate"] = 99999999
        self.api.on_test = displacement
        with self.assertRaisesRegex(W.WindowsLayoutError,
                                    "consistent Windows display metadata") as ctx:
            self.configure()
        self.assertEqual(ctx.exception.diagnostics["phase"], "after-mode-test")
        self.assertEqual(self.api.ccd_calls, [])
        self.assertEqual([f for _, _, f in self.api.calls], [W.CDS_TEST])

    def test_ambiguous_interface_fails_closed(self):
        def displacement(api):
            self.displace_work(api)
            api.sources[2].monitor_paths = ("monitor-new", "unrelated-interface")
        self.api.on_test = displacement
        with self.assertRaisesRegex(W.WindowsLayoutError,
                                    "consistent Windows display metadata"):
            self.configure()
        self.assertEqual(self.api.ccd_calls, [])
        self.assertEqual([f for _, _, f in self.api.calls], [W.CDS_TEST])

    def test_ownership_loss_blocks_native_reconciliation(self):
        healthy = [True]
        def displacement(api):
            self.displace_work(api)
            healthy[0] = False
        self.api.on_test = displacement
        with self.assertRaisesRegex(W.WindowsLayoutError, "ownership is unverified"):
            self.configure(guard=lambda: healthy[0])
        self.assertEqual(self.api.ccd_calls, [])
        self.assertEqual([f for _, _, f in self.api.calls], [W.CDS_TEST])

    def test_no_owned_baseline_does_not_try_to_reconcile(self):
        self.api.on_test = self.displace_work
        with self.assertRaisesRegex(W.WindowsLayoutError,
                                    "consistent Windows display metadata"):
            self.configure(creation_baseline=None, owned_baseline=None,
                           captured_baseline=None)
        self.assertEqual(self.api.ccd_calls, [])
        self.assertEqual([f for _, _, f in self.api.calls], [W.CDS_TEST])


if __name__ == "__main__":
    unittest.main()
