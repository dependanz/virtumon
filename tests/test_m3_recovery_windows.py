"""Synthetic M3 keeper-crash tests: no real Windows process or driver is touched."""

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
import m3_recovery_windows as m3


class Clock:
    t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, value):
        self.t += value


class FakeCli:
    def __init__(self, clock):
        self.clock = clock
        self.pid = 46721
        self.calls = []
        self.monitor = None
        self.running = False
        self.killed = False
        self.watchdog_never_expires = False
        self.mutated_id = False
        self.primary = {
            "device": r"\\.\DISPLAY1", "x": 0, "y": 0,
            "width": 2560, "height": 1600, "primary": True}
        self.monitor_id = str(uuid.uuid4())
        self.terminate_calls = []

    def terminate(self, pid):
        assert pid == self.pid
        self.terminate_calls.append(pid)
        self.running = False
        self.killed = True

    def call(self, *args, shape=None):
        self.calls.append(args)
        if args[0] == "create":
            name = args[1]
            assert self.monitor is None
            self.monitor = {
                "name": name, "id": self.monitor_id, "backend": "sudovda",
                "width": 1920, "height": 1080, "refresh_hz": 60.0,
                "device": r"\\.\DISPLAY28", "x": 2560, "y": 0,
                "status": "active", "primary": False,
                "creation_route": "clone-prepare"}
            self.running = True
            result = self.monitor.copy()
        elif args == ("agent", "status", "--json"):
            result = {"running": self.running,
                      "managed_count": int(self.monitor is not None)}
            if self.running:
                result.update(pid=self.pid, heartbeat="ok", error=None)
        elif args == ("list", "--managed", "--json"):
            result = [] if self.monitor is None else [self.monitor.copy()]
            if self.killed and self.monitor is not None:
                result[0].update(status="stale", device=None,
                                 x=None, y=None, primary=False)
                if self.mutated_id:
                    result[0]["id"] = str(uuid.uuid4())
        elif args == ("list", "--json"):
            result = [self.primary.copy()]
            visible = (self.monitor is not None and
                       (not self.killed or self.watchdog_never_expires or
                        self.clock.t < 4))
            if visible:
                result.append({key: self.monitor[key] for key in
                               ("device", "x", "y", "width", "height", "primary")})
        elif args[0] == "remove":
            assert args[-1] == "--json"
            assert len(args) == 3
            assert self.monitor and self.monitor["name"] == args[1]
            self.monitor = None
            self.running = True  # Client has started a new keeper to clean journal.
            result = {"removed": [args[1]], "errors": []}
        elif args == ("agent", "stop"):
            assert self.monitor is None
            self.running = False
            result = "Stopped"
        else:
            raise AssertionError(f"Unexpected call: {args}")
        if shape is not None:
            assert type(result) is shape, args
        return result


class M3KeeperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.clock = Clock()
        self.cli = FakeCli(self.clock)
        (self.directory / "agent.json").write_text(
            json.dumps({"pid": self.cli.pid, "api_version": 1,
                        "host": "127.0.0.1"}), encoding="utf-8")
        self.receipt = {"events": []}

    def process(self, pid):
        return {
            "ProcessId": pid,
            "ExecutablePath": sys.executable,
            "CommandLine": (
                f'"{sys.executable}" -m virtumon._agent '
                f'--state-dir "{self.directory}"'),
        }

    def execute(self, info=None):
        return m3.exercise(
            self.cli, [self.cli.primary.copy()], state_dir=self.directory,
            process_info=info or self.process,
            terminate=self.cli.terminate, clock=self.clock,
            sleep=self.clock.sleep, timeout_seconds=10,
            receipt=self.receipt)

    def test_keeper_crash_stale_observation_watchdog_and_cleanup_pass(self):
        self.execute()
        phases = [event["phase"] for event in self.receipt["events"]]
        self.assertEqual(self.cli.terminate_calls, [self.cli.pid])
        self.assertFalse(self.cli.running)
        self.assertIsNone(self.cli.monitor)
        self.assertLess(self.clock.t, 10.1)
        for phase in ("created-monitor", "keeper-identity-verified",
                      "keeper-termination-issued", "stale-ownership-observed",
                      "watchdog-disappearance-observed", "cleanup-removed",
                      "cleanup-baseline-restored", "recovery-passed"):
            self.assertIn(phase, phases)
        self.assertNotIn("cleanup-incomplete", phases)
        self.assertNotIn(("remove", "--all", "--json"), self.cli.calls)

    def test_unexpected_keeper_executable_fails_closed_without_kill(self):
        def wrong_process(pid):
            result = self.process(pid)
            result["ExecutablePath"] = r"C:\OtherPython\python.exe"
            return result
        with self.assertRaisesRegex(m3.m2.AcceptanceFailure, "executable"):
            self.execute(wrong_process)
        self.assertEqual(self.cli.terminate_calls, [])
        self.assertFalse(self.cli.running)
        self.assertIsNone(self.cli.monitor)
        phases = [event["phase"] for event in self.receipt["events"]]
        self.assertIn("exercise-failure", phases)
        self.assertIn("cleanup-baseline-restored", phases)
        self.assertNotIn("recovery-passed", phases)

    def test_wrong_endpoint_pid_refuses_termination_and_cleans(self):
        (self.directory / "agent.json").write_text(
            json.dumps({"pid": self.cli.pid + 1, "api_version": 1,
                        "host": "127.0.0.1"}), encoding="utf-8")
        with self.assertRaisesRegex(m3.m2.AcceptanceFailure, "endpoint"):
            self.execute()
        self.assertEqual(self.cli.terminate_calls, [])
        self.assertIsNone(self.cli.monitor)

    def test_watchdog_no_disappearance_is_failure_but_exact_cleanup(self):
        self.cli.watchdog_never_expires = True
        with self.assertRaisesRegex(m3.m2.AcceptanceFailure, "Virtual output still present"):
            self.execute()
        self.assertEqual(self.cli.terminate_calls, [self.cli.pid])
        self.assertFalse(self.cli.running)
        self.assertIsNone(self.cli.monitor)
        self.assertTrue(any(e["phase"] == "exercise-failure"
                            for e in self.receipt["events"]))
        self.assertFalse(any(e["phase"] == "recovery-passed"
                             for e in self.receipt["events"]))

    def test_stale_uuid_mismatch_refuses_cleanup(self):
        self.cli.mutated_id = True
        with self.assertRaisesRegex(m3.m2.AcceptanceFailure, "stale UUID"):
            self.execute()
        self.assertEqual(self.cli.terminate_calls, [self.cli.pid])
        self.assertIsNotNone(self.cli.monitor)
        self.assertTrue(any(e["phase"] == "cleanup-incomplete"
                            for e in self.receipt["events"]))
        self.assertFalse(any(args[0] == "remove" for args in self.cli.calls))

    def test_read_only_preflight_does_not_terminate(self):
        with patch.object(m3.m2, "InstalledCli", return_value=self.cli), \
             patch.object(m3.m2, "preflight",
                          return_value=([self.cli.primary.copy()], [], {"running": False},
                                        "virtumon 0.2.0a1")), \
             patch.object(m3.m2, "receipt_path", return_value=self.directory / "receipt.json"):
            code = m3.main([])
        self.assertEqual(code, 0)
        self.assertEqual(self.cli.terminate_calls, [])
        self.assertEqual(self.cli.calls, [])
        saved = list(self.directory.glob("m3-recovery-*.json"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0].read_text())["result"], "pass")


if __name__ == "__main__":
    unittest.main()
