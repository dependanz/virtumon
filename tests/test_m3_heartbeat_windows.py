"""Simulation of M3-B: never pauses the real driver or process."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import m3_heartbeat_windows as m3


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        assert 0 < seconds <= 1.0
        self.t += seconds


class FakeCli:
    def __init__(self, clock):
        self.clock = clock
        self.pid = 42424
        self.physical = dict(device=r"\\.\DISPLAY1", x=0, y=0, width=2560,
                             height=1600, primary=True)
        self.monitor = None
        self.heartbeat_fault = False
        self.stale_resurrects = False
        self.never_recovers = False
        self.never_disappears = False
        self.change_primary = False
        self.fault_declined = False
        self.calls = []
        self.removed = []
        self.injected_count = 0

    def status(self):
        error = self.heartbeat_fault and (
            self.never_recovers or 2 <= self.clock.t < 5)
        return {
            "running": self.monitor is not None or getattr(self, "keeper_running", False),
            "managed_count": int(self.monitor is not None), "pid": self.pid,
            "heartbeat": "error" if error else "ok",
            "error": "Heartbeat overdue" if error else None,
        }

    def managed(self):
        if self.monitor is None:
            return []
        row = self.monitor.copy()
        if self.heartbeat_fault and self.clock.t >= 2:
            row["status"] = ("active" if self.stale_resurrects and self.clock.t >= 5
                             else "stale")
            if row["status"] == "stale":
                row.update(device=None, x=None, y=None, primary=False)
        return [row]

    def displays(self):
        physical = self.physical.copy()
        if self.change_primary and self.heartbeat_fault and self.clock.t >= 1:
            physical["width"] = 2000
        screens = [physical]
        if (self.monitor is not None and
                (not self.heartbeat_fault or self.never_disappears or self.clock.t < 3)):
            screens.append({
                field: self.monitor[field] for field in
                ("device", "x", "y", "width", "height", "primary")
            })
        return screens

    def call(self, *args, shape=None):
        self.calls.append(args)
        if args[0] == "create":
            name = args[1]
            if name.startswith("m3blocked-"):
                raise m3.m2.AcceptanceFailure("create failed: stale ownership")
            if self.monitor is not None:
                raise AssertionError("Unexpected second monitor create")
            self.monitor = dict(
                name=name, id=str(uuid.uuid4()), status="active",
                backend="sudovda", width=1920, height=1080, refresh_hz=60.0,
                device=r"\\.\DISPLAY28", x=2560, y=0, primary=False,
                creation_route="clone-prepare",
            )
            self.keeper_running = True
            result = self.monitor.copy()
        elif args == ("agent", "status", "--json"):
            result = self.status()
        elif args == ("list", "--managed", "--json"):
            result = self.managed()
        elif args == ("list", "--json"):
            result = self.displays()
        elif args[0] == "remove":
            assert len(args) == 3 and args[-1] == "--json", args
            assert self.monitor and args[1] == self.monitor["name"]
            self.removed.append((args[1], self.monitor["id"]))
            self.monitor = None
            result = {"removed": [args[1]], "errors": []}
        elif args == ("agent", "stop"):
            assert self.monitor is None
            self.keeper_running = False
            result = "Stopped"
        else:
            raise AssertionError(f"Unexpected fake CLI {args}")
        if shape is not None:
            assert type(result) is shape, args
        return result


class FakeControl:
    def __init__(self, cli):
        self.cli = cli
        self.calls = []

    def call(self, action, *, timeout=None, **kwargs):
        self.calls.append(action)
        if action == "m3_pause_heartbeat":
            if self.cli.fault_declined:
                raise m3.m2.AcceptanceFailure("Fault injection is disabled.")
            assert self.cli.monitor
            assert kwargs == {
                "name": self.cli.monitor["name"],
                "uuid": self.cli.monitor["id"]}, kwargs
            self.cli.heartbeat_fault = True
            self.cli.injected_count += 1
            return {"paused": True, "duration_seconds": 5.0}
        if action == "status":
            return self.cli.status()
        if action == "list":
            return self.cli.managed()
        raise AssertionError(action)


class HeartbeatOutageTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cli = FakeCli(self.clock)
        self.control = FakeControl(self.cli)
        self.receipt = {"events": []}
        self.baseline = [self.cli.physical.copy()]

    def run_exercise(self):
        return m3.exercise(
            self.cli, self.control, self.baseline, clock=self.clock,
            sleep=self.clock.sleep, timeout_seconds=20, receipt=self.receipt)

    def phases(self):
        return [x["phase"] for x in self.receipt["events"]]

    def test_five_second_pause_recovers_but_original_uuid_remains_stale(self):
        self.run_exercise()
        self.assertEqual(self.cli.injected_count, 1)
        self.assertEqual(len(self.cli.removed), 1)
        self.assertIsNone(self.cli.monitor)
        self.assertFalse(self.cli.keeper_running)
        self.assertIn("heartbeat-overdue-observed", self.phases())
        self.assertIn("stale-ownership-observed", self.phases())
        self.assertIn("virtual-output-disappeared", self.phases())
        self.assertIn("heartbeat-recovered-identity-stale", self.phases())
        self.assertIn("unsafe-creation-refused", self.phases())
        self.assertIn("cleanup-baseline-restored", self.phases())
        self.assertIn("recovery-passed", self.phases())
        self.assertNotIn("exercise-failure", self.phases())
        self.assertGreaterEqual(self.clock.t, 5.0)
        self.assertLess(self.clock.t, 6.0)

    def test_stale_reactivation_fails_and_does_not_mark_success(self):
        self.cli.stale_resurrects = True
        with self.assertRaisesRegex(m3.m2.AcceptanceFailure,
                                    "stale target was incorrectly reactivated"):
            self.run_exercise()
        self.assertNotIn("recovery-passed", self.phases())
        self.assertIn("exercise-failure", self.phases())
        self.assertEqual(len(self.cli.removed), 1)

    def test_no_heartbeat_recovery_is_not_a_pass(self):
        self.cli.never_recovers = True
        with self.assertRaisesRegex(m3.m2.AcceptanceFailure, "did not observe"):
            self.run_exercise()
        self.assertIn("exercise-failure", self.phases())
        self.assertNotIn("recovery-passed", self.phases())

    def test_no_watchdog_disappearance_is_not_a_pass(self):
        self.cli.never_disappears = True
        with self.assertRaisesRegex(m3.m2.AcceptanceFailure, "did not observe"):
            self.run_exercise()
        self.assertIn("exercise-failure", self.phases())
        self.assertNotIn("recovery-passed", self.phases())

    def test_physical_change_fails_closed(self):
        self.cli.change_primary = True
        with self.assertRaisesRegex(m3.m2.AcceptanceFailure, "physical primary changed"):
            self.run_exercise()
        self.assertNotIn("recovery-passed", self.phases())
        self.assertIn("cleanup-incomplete", self.phases())

    def test_refused_injection_does_not_terminate_keeper_or_leave_monitor(self):
        self.cli.fault_declined = True
        with self.assertRaisesRegex(m3.m2.AcceptanceFailure, "disabled"):
            self.run_exercise()
        self.assertEqual(self.cli.injected_count, 0)
        self.assertEqual(len(self.cli.removed), 1)
        self.assertIsNone(self.cli.monitor)
        self.assertIn("cleanup-baseline-restored", self.phases())

    def test_default_invocation_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            location = Path(folder)
            with patch.object(m3.m2, "InstalledCli", return_value=self.cli), \
                 patch.object(m3.m2, "preflight", return_value=(
                     self.baseline, [], {"running": False}, "virtumon 0.2.0a1")), \
                 patch.object(m3.m2, "receipt_path",
                              return_value=location / "receipt.json"):
                self.assertEqual(m3.main([]), 0)
            self.assertEqual(self.cli.calls, [])
            self.assertEqual(self.control.calls, [])
            receipts = list(location.glob("m3-heartbeat-*.json"))
            self.assertEqual(len(receipts), 1)
            self.assertEqual(json.loads(receipts[0].read_text())["result"], "pass")


if __name__ == "__main__":
    unittest.main()
