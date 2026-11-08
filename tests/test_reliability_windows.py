"""Pure simulator for tools/reliability_windows.py: never calls a display driver."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "reliability_windows.py"
spec = importlib.util.spec_from_file_location("virtumon_windows_reliability", SCRIPT)
assert spec and spec.loader
reliability = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reliability)


class FakeCli:
    def __init__(self, *, failure: str | None = None):
        self.failure = failure
        self.calls = []
        self.physical = {
            "device": r"\\.\DISPLAY1", "x": 0, "y": 0,
            "width": 2560, "height": 1600, "primary": True,
        }
        self.monitors = {}
        self.agent_running = False
        self.remove_calls = []
        self.ids = iter([
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333",
            "44444444-4444-4444-8444-444444444444",
        ])

    def _rows(self):
        return list(self.monitors.values())

    def call(self, *arguments, shape=None):
        args = tuple(arguments)
        self.calls.append(args)
        result = None
        if args == ("--version",):
            result = "virtumon 0.2.0a1"
        elif args == ("doctor",):
            result = "Virtual monitor creation: SudoVDA driver available"
        elif args == ("list", "--json"):
            result = [self.physical.copy()]
            result.extend({
                key: row[key] for key in ("device", "x", "y", "width", "height", "primary")
            } for row in self._rows() if row.get("status") == "active")
        elif args == ("list", "--managed", "--json"):
            result = [row.copy() for row in self._rows()]
        elif args == ("agent", "status", "--json"):
            result = {
                "running": self.agent_running, "managed_count": len(self.monitors),
                "heartbeat": "ok" if self.agent_running else None,
                "error": None,
            }
        elif args == ("agent", "stop"):
            if self.monitors:
                raise reliability.AcceptanceFailure("Cannot stop keeper with active monitors.")
            self.agent_running = False
            result = "Stopped Virtumon keeper"
        elif args and args[0] == "create":
            name = args[1]
            if name in self.monitors:
                raise reliability.AcceptanceFailure("Duplicate managed monitor name")
            if len(self.monitors) >= 2:
                raise reliability.AcceptanceFailure("Only two displays are supported")
            self.agent_running = True
            if args[3] == "1920x1080":
                width, height, x = 1920, 1080, 2560
            else:
                width, height, x = 1080, 1920, 4480
            result = {
                "name": name, "id": next(self.ids), "backend": "sudovda",
                "width": width, "height": height, "refresh_hz": 60.0,
                "x": x, "y": 0, "device": r"\\.\DISPLAY" + str(28 + len(self.monitors)),
                "primary": False, "status": "active",
            }
            self.monitors[name] = result
            if self.failure == "second_create" and len(self.monitors) == 2:
                raise reliability.AcceptanceFailure("Simulated failed second create")
        elif args and args[0] == "remove":
            name = args[1]
            if name not in self.monitors:
                raise reliability.AcceptanceFailure(f"Unknown name {name}")
            self.remove_calls.append(name)
            del self.monitors[name]
            if self.failure == "survivor_detached" and self.monitors:
                self.monitors[next(iter(self.monitors))]["status"] = "error"
            result = {"removed": [name], "errors": []}
        else:
            raise AssertionError(f"Unexpected fake CLI command: {args}")
        if shape is not None:
            assert type(result) is shape, args
        return result


class Clock:
    def __init__(self):
        self.t = 0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        assert 0 < seconds <= 60
        self.t += seconds


def new_receipt():
    return {"events": []}


class NativeReliabilityHarnessTests(unittest.TestCase):

    def test_default_preflight_is_read_only_even_with_managed_monitors(self):
        fake = FakeCli()
        fake.monitors["user-work"] = {
            "name": "user-work", "id": uuid.uuid4().hex, "status": "active",
            "device": r"\\.\DISPLAY28", "x": 2560, "y": 0, "width": 1920,
            "height": 1080, "refresh_hz": 60, "primary": False,
        }
        _, managed, _, version = reliability.preflight(fake, exercise=False)
        self.assertEqual(len(managed), 1)
        self.assertIn("0.2", version)
        self.assertTrue(all(call[0] not in ("create", "remove") for call in fake.calls))
        self.assertNotIn(("agent", "stop"), fake.calls)

    def test_preflight_refuses_existing_records_before_any_mutation(self):
        fake = FakeCli()
        fake.monitors["existing"] = {
            "name": "existing", "id": str(uuid.uuid4()), "status": "active",
            "device": r"\\.\DISPLAY28", "x": 2560, "y": 0,
            "width": 1920, "height": 1080, "refresh_hz": 60, "primary": False,
        }
        with patch.object(reliability.sys, "platform", "win32"):
            with patch.dict(os.environ, {"VIRTUMON_EXPERIMENTAL_MULTI": "1"}):
                with self.assertRaisesRegex(reliability.AcceptanceFailure, "already exist"):
                    reliability.preflight(fake, exercise=True)
        self.assertEqual(fake.remove_calls, [])
        self.assertFalse(any(x[0] == "create" for x in fake.calls))

    def test_preflight_requires_optin_and_stopped_keeper(self):
        fake = FakeCli()
        with patch.object(reliability.sys, "platform", "win32"):
            with patch.dict(os.environ, {"VIRTUMON_EXPERIMENTAL_MULTI": "0"}):
                with self.assertRaisesRegex(reliability.AcceptanceFailure, "EXPERIMENTAL_MULTI"):
                    reliability.preflight(fake, exercise=True)
            fake.agent_running = True
            with patch.dict(os.environ, {"VIRTUMON_EXPERIMENTAL_MULTI": "1"}):
                with self.assertRaisesRegex(reliability.AcceptanceFailure, "Stop the existing"):
                    reliability.preflight(fake, exercise=True)
        self.assertEqual(fake.remove_calls, [])

    def test_two_full_cycles_preserve_physical_and_clean_up_exact_names(self):
        fake = FakeCli()
        clock = Clock()
        output = new_receipt()
        reliability.exercise(fake, [fake.physical.copy()], cycles=2,
                             soak_seconds=10, poll_seconds=5,
                             sleep=clock.sleep, clock=clock.monotonic,
                             receipt=output)
        self.assertEqual(fake.monitors, {})
        self.assertFalse(fake.agent_running)
        self.assertEqual(len(fake.remove_calls), 4)
        self.assertTrue(all(name.startswith(("m2chat-", "m2work-")) for name in fake.remove_calls))
        self.assertEqual(sum(x["phase"] == "cycle-passed" for x in output["events"]), 2)
        self.assertEqual(sum(x["phase"] == "selective-remove-passed" for x in output["events"]), 2)
        self.assertEqual(sum(x["phase"] == "cleanup-baseline-restored" for x in output["events"]), 1)
        self.assertEqual(fake.physical["primary"], True)
        self.assertFalse(any(args[:2] == ("remove", "--all") for args in fake.calls))

    def test_second_create_failure_cleans_only_generated_records(self):
        fake = FakeCli(failure="second_create")
        clock = Clock()
        output = new_receipt()
        with self.assertRaisesRegex(reliability.AcceptanceFailure, "Simulated failed second create"):
            reliability.exercise(fake, [fake.physical.copy()], cycles=1,
                                 soak_seconds=5, poll_seconds=5,
                                 sleep=clock.sleep, clock=clock.monotonic,
                                 receipt=output)
        self.assertEqual(fake.monitors, {})
        self.assertFalse(fake.agent_running)
        self.assertEqual(len(fake.remove_calls), 2)
        self.assertEqual(output["events"][-1]["phase"], "cleanup-baseline-restored")

    def test_survivor_detaches_after_selective_removal_is_not_pass(self):
        fake = FakeCli(failure="survivor_detached")
        clock = Clock()
        output = new_receipt()
        with self.assertRaisesRegex(reliability.AcceptanceFailure, "Unexpected Windows display count"):
            reliability.exercise(fake, [fake.physical.copy()], cycles=1,
                                 soak_seconds=5, poll_seconds=5,
                                 sleep=clock.sleep, clock=clock.monotonic,
                                 receipt=output)
        self.assertEqual(fake.monitors, {})
        self.assertFalse(fake.agent_running)
        self.assertEqual(sum(x["phase"] == "cycle-passed" for x in output["events"]), 0)
        self.assertEqual(len(fake.remove_calls), 2)

    def test_physical_geometry_change_aborts_before_any_pass(self):
        fake = FakeCli()
        clock = Clock()
        output = new_receipt()
        original = fake.physical.copy()

        def change_physical(seconds):
            clock.sleep(seconds)
            fake.physical["width"] = 1920

        with self.assertRaisesRegex(reliability.AcceptanceFailure, "physical display changed"):
            reliability.exercise(fake, [original], cycles=1, soak_seconds=5,
                                 poll_seconds=5, sleep=change_physical,
                                 clock=clock.monotonic, receipt=output)
        self.assertEqual(sum(x["phase"] == "cycle-passed" for x in output["events"]), 0)
        # If the physical layout changed unexpectedly, do not stop a keeper
        # merely because the managed list is empty; require manual inspection.
        self.assertTrue(fake.agent_running)
        self.assertTrue(any(e["phase"] == "cleanup-incomplete" for e in output["events"]))

    def test_receipt_is_write_once_and_json(self):
        with tempfile.TemporaryDirectory() as folder:
            location = Path(folder) / "receipt.json"
            data = {"schema": "virtumon.reliability-acceptance/v1", "events": []}
            reliability.save_receipt(location, data)
            self.assertEqual(json.loads(location.read_text(encoding="utf-8")), data)
            with self.assertRaises(FileExistsError):
                reliability.save_receipt(location, data)


if __name__ == "__main__":
    unittest.main()
