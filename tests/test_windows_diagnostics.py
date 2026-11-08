"""Failure evidence stays bounded and never invokes display mutation APIs."""

import copy
import ctypes as C
import json
import sys
import unittest
from unittest.mock import patch

from virtumon import windows_diagnostics as D
from virtumon import windows_layout as W
from virtumon.displays import Display


def native_snapshot(target_id=7, source_id=3, mode_index=1):
    path = W._PATH_INFO()
    path.flags = W.ACTIVE
    path.sourceInfo.adapterId = W._LUID(200, -3)
    path.sourceInfo.id = source_id
    path.sourceInfo.modeInfoIdx = mode_index << 16 | 0xFFFF
    path.targetInfo.adapterId = W._LUID(200, -3)
    path.targetInfo.id = target_id
    path.targetInfo.modeInfoIdx = 0xFFFF
    path.targetInfo.targetAvailable = 1
    path.targetInfo.refreshRate = W._RATIONAL(60, 1)
    modes = [W._MODE_INFO() for _ in range(mode_index + 1)]
    modes[0].infoType, modes[0].id, modes[0].adapterId = 2, target_id, W._LUID(200, -3)
    modes[0].targetMode.vSyncFreq = W._RATIONAL(0xFFFFFFFE, 0xFFFFFFFE)
    modes[mode_index].infoType, modes[mode_index].id, modes[mode_index].adapterId = 1, source_id, W._LUID(200, -3)
    modes[mode_index].sourceMode.width, modes[mode_index].sourceMode.height = 1280, 720
    modes[mode_index].sourceMode.position = W._POINT(0, 0)
    return W._Snapshot([path], modes, virtual_mode_aware=True)


def sources(flags=0):
    return [W._DeviceSource(r"\\.\DISPLAY1", 1 | 4, ("physical-interface",)),
            W._DeviceSource(r"\\.\DISPLAY28", flags, ("virtual-interface",))]


NAMES = {(200, -3, 3): r"\\.\DISPLAY28"}


class FakeAPI:
    def __init__(self, throwing=False):
        self.throwing = throwing
        self.calls = []

    def current_mode(self, device):
        self.calls.append(("current_mode", device))
        if self.throwing:
            raise OSError("test current-mode error")
        result = W._DEVMODE()
        result.dmSize = C.sizeof(result)
        result.dmPelsWidth, result.dmPelsHeight, result.dmDisplayFrequency = 1280, 720, 60
        return result

    def target_path(self, path):
        self.calls.append(("target_path", W._path_key(path)))
        if self.throwing:
            raise OSError("test target-path error")
        return "VIRTUAL-INTERFACE"

    def snapshot(self, active_only=True, *, virtual_mode_aware=True):
        self.calls.append(("snapshot", active_only, virtual_mode_aware))
        if self.throwing:
            raise OSError("test snapshot error")
        result = native_snapshot()
        result.virtual_mode_aware = virtual_mode_aware
        return result

    def change_mode(self, *_args):
        raise AssertionError("Diagnostics must never change a mode")

    def modes(self, *_args):
        raise AssertionError("Diagnostics must not enumerate selectable modes")


class MetadataCaptureTests(unittest.TestCase):
    def capture(self, devices=None, current=None, names=None, problem=r"\\.\DISPLAY28"):
        return D.capture_metadata(current or native_snapshot(), sources() if devices is None else devices,
                                  NAMES if names is None else names,
                                  reason="No attached source", problem_device=problem)

    def test_distinguishes_missing_duplicate_detached_and_attached_source(self):
        for devices, status, count in (
            (sources()[:1], "missing", 0),
            (sources() + [W._DeviceSource(r"\\.\display28", 1, ())], "ambiguous", 2),
            (sources(), "detached", 1),
            (sources(1), "attached", 1),
        ):
            with self.subTest(status=status):
                problem = self.capture(devices)["problem"]
                self.assertEqual((problem["match_status"], problem["match_count"]), (status, count))
        report = self.capture()
        self.assertEqual(report["problem"]["matches"][0]["flags"], "0x0")
        self.assertFalse(report["problem"]["matches"][0]["primary"])
        self.assertTrue(next(source for source in report["gdi"]["sources"] if source["device"].endswith("1"))["primary"])

    def test_preserves_exact_raw_indexes_query_format_and_unavailable_refresh(self):
        report = self.capture()
        source = report["ccd"]["paths"][0]["source"]
        self.assertEqual((source["mode_raw"], source["mode_legacy"], source["mode_virtual"], source["clone_group"]),
                         ("0x1ffff", 131071, 1, 65535))
        self.assertEqual(source["luid"], [200, -3])
        self.assertFalse(source["mode_legacy_in_bounds"])
        self.assertTrue(source["mode_virtual_in_bounds"])
        self.assertTrue(report["ccd"]["virtual_mode_aware"])
        target_mode = next(mode for mode in report["ccd"]["modes"] if mode["type"] == 2)
        self.assertEqual(target_mode["refresh"], [0xFFFFFFFE, 0xFFFFFFFE])
        json.dumps(report, allow_nan=False)

    def test_records_virtual_desktop_image_region_and_clip_separately(self):
        current = native_snapshot()
        desktop_mode = W._MODE_INFO()
        desktop_mode.infoType = 3
        desktop_mode.desktopImageInfo.PathSourceSize = W._POINT(1280, 720)
        desktop_mode.desktopImageInfo.DesktopImageRegion = W._RECT(10, 20, 1290, 740)
        desktop_mode.desktopImageInfo.DesktopImageClip = W._RECT(30, 40, 1200, 700)
        current.modes.append(desktop_mode)
        report = self.capture(current=current)
        recorded = next(mode for mode in report["ccd"]["modes"] if mode["type"] == 3)
        self.assertEqual(recorded["source_size"], [1280, 720])
        self.assertEqual(recorded["region"], [10, 20, 1290, 740])
        self.assertEqual(recorded["clip"], [30, 40, 1200, 700])

    def test_bounds_output_and_prioritizes_problem_paths_referenced_modes_and_sources(self):
        current = native_snapshot(mode_index=D.MAX_MODES + 8)
        relevant = current.paths[0]
        current.paths = [W._PATH_INFO() for _ in range(D.MAX_PATHS + 6)] + [relevant]
        many_sources = [W._DeviceSource("UNRELATED-" + str(index), 0, ())
                        for index in range(D.MAX_SOURCES + 6)] + sources()
        many_sources[-1].monitor_paths = tuple("x" * (D.MAX_TEXT + 3) for _ in range(D.MAX_MONITOR_PATHS + 3))
        report = self.capture(many_sources, current)
        self.assertEqual(len(report["ccd"]["paths"]), D.MAX_PATHS)
        self.assertEqual(report["ccd"]["paths"][0]["index"], len(current.paths) - 1)
        self.assertEqual(report["ccd"]["paths"][0]["source"]["device"], r"\\.\DISPLAY28")
        self.assertIn(D.MAX_MODES + 8, [mode["index"] for mode in report["ccd"]["modes"]])
        self.assertLessEqual(len(report["ccd"]["modes"]), D.MAX_MODES)
        self.assertTrue(report["ccd"]["paths_truncated"])
        self.assertTrue(report["ccd"]["modes_truncated"])
        self.assertEqual(len(report["gdi"]["sources"]), D.MAX_SOURCES)
        self.assertEqual(report["gdi"]["sources"][0]["device"], r"\\.\DISPLAY28")
        matched = report["problem"]["matches"][0]
        self.assertEqual(len(matched["monitor_paths"]), D.MAX_MONITOR_PATHS)
        self.assertTrue(matched["monitor_paths_truncated"])
        self.assertTrue(all(len(path) == D.MAX_TEXT for path in matched["monitor_paths"]))

    def test_enrichment_is_read_only_and_retains_original_observations(self):
        report = self.capture()
        original = copy.deepcopy(report)
        api = FakeAPI()
        desktop = [Display(r"\\.\DISPLAY1", 0, 0, 2560, 1600, True)]
        with patch("virtumon.displays.list_displays", return_value=desktop):
            result = D.enrich_metadata(report, api)
        self.assertEqual(report, original)
        self.assertEqual(result["ccd"], original["ccd"])
        self.assertEqual(result["gdi"], original["gdi"])
        self.assertEqual(result["problem"], original["problem"])
        self.assertEqual(result["diagnostic_errors"], [])
        self.assertEqual(api.calls[0], ("current_mode", r"\\.\DISPLAY28"))
        self.assertEqual([call for call in api.calls if call[0] == "snapshot"],
                         [("snapshot", False, True), ("snapshot", True, False)])
        self.assertEqual(result["supplemental"]["target_paths"][0]["matching_captured_gdi_sources"], [r"\\.\DISPLAY28"])
        self.assertEqual(result["supplemental"]["desktop"]["displays"][0]["width"], 2560)
        self.assertFalse(result["supplemental"]["legacy_active"]["virtual_mode_aware"])
        json.dumps(result, allow_nan=False)

    def test_supplemental_failures_keep_exact_pair_and_report_individual_errors(self):
        report = self.capture()
        api = FakeAPI(throwing=True)
        with patch("virtumon.displays.list_displays", side_effect=OSError("test desktop error")):
            result = D.enrich_metadata(report, api)
        self.assertEqual(result["ccd"], report["ccd"])
        self.assertEqual(result["gdi"], report["gdi"])
        self.assertEqual(result["reason"], report["reason"])
        self.assertTrue(result["diagnostic_errors"])
        self.assertIn("EnumDisplayMonitors", {entry["operation"] for entry in result["diagnostic_errors"]})
        self.assertIsNone(result["supplemental"]["all_paths"])
        json.dumps(result, allow_nan=False)

    def test_unavailable_legacy_query_is_reported_without_losing_other_evidence(self):
        report = self.capture()
        api = FakeAPI()
        with patch.object(api, "snapshot", side_effect=lambda active_only=True: native_snapshot()), \
                patch("virtumon.displays.list_displays", return_value=[]):
            result = D.enrich_metadata(report, api)
        self.assertEqual(result["ccd"], report["ccd"])
        self.assertIsNotNone(result["supplemental"]["all_paths"])
        self.assertIsNone(result["supplemental"]["legacy_active"])
        self.assertEqual([error["operation"] for error in result["diagnostic_errors"]],
                         ["QueryDisplayConfig:legacy-active"])

    def test_budget_prevents_more_native_reads_after_slow_first_call(self):
        report = self.capture()
        api, clock = FakeAPI(), [0.0]
        original = api.current_mode

        def slow(device):
            value = original(device)
            clock[0] += 2.0
            return value

        with patch.object(D.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(api, "current_mode", side_effect=slow), \
                patch("virtumon.displays.list_displays") as desktop:
            result = D.enrich_metadata(report, api)
        self.assertEqual(api.calls, [("current_mode", r"\\.\DISPLAY28")])
        desktop.assert_not_called()
        self.assertTrue(result["supplemental"]["budget_exhausted"])
        self.assertIn("QueryDisplayConfig:all-paths", result["supplemental"]["skipped_operations"])
        self.assertEqual(result["ccd"], report["ccd"])


@unittest.skipUnless(sys.platform == "win32", "Read-only native Windows diagnostics probe")
class NativeWindowsDiagnosticsTests(unittest.TestCase):
    def test_capture_and_enrich_real_user32_metadata_without_changing_displays(self):
        api = W._Win32()
        try:
            current = api.snapshot()
            observed_sources = api.device_sources()
            names = {W._source_key(path): api.source_name(path) for path in current.paths}
        except W.WindowsLayoutError as exc:
            if exc.winerror in (5, 50):
                self.skipTest(f"Runner has no accessible console display session/WDDM: {exc}")
            raise
        report = D.capture_metadata(current, observed_sources, names,
                                    reason="Read-only CI diagnostic probe", problem_device=next(iter(names.values()), None))
        with patch.object(api, "change_mode", side_effect=AssertionError("No display changes allowed")) as mutation:
            result = D.enrich_metadata(report, api)
        mutation.assert_not_called()
        self.assertEqual(result["diagnostic_errors"], [], json.dumps(result))
        self.assertEqual(result["ccd"]["path_count"], len(current.paths))
        self.assertEqual(result["gdi"]["source_count"], len(observed_sources))
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
