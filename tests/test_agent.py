"""Exercise authenticated IPC and keeper failure handling without native mutation."""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from virtumon import __version__
from virtumon._agent import AgentService, Keeper, create_server
from virtumon.agent import AgentClient, AgentError
from virtumon.managed import Health, MonitorManager, OperationError
from virtumon.state import StateError, StateStore, file_lock, write_json


class EmptyLayout:
    @staticmethod
    def list_native_displays():
        return []


def record():
    return {"name": "work", "id": str(uuid.uuid4()), "backend": "sudovda",
            "width": 1920, "height": 1080, "refresh_hz": 60, "identity": None}


class AgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = StateStore(self.directory)
        self.health = Health()
        self.health.succeed()
        self.driver_calls = []

        def no_driver():
            self.driver_calls.append(True)
            raise AssertionError("No native driver should be opened by this test")

        self.manager = MonitorManager(self.store, no_driver, EmptyLayout, self.health)
        self.service = AgentService(self.manager, self.health)
        self.token = "a" * 64
        self.server = create_server(self.service, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        write_json(self.directory / "agent.json", {"api_version": 1, "host": "127.0.0.1",
                   "port": self.server.server_address[1], "token": self.token})
        self.client = AgentClient(self.directory)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, payload, token=None, content_type="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        try:
            headers = {"Content-Type": content_type}
            if token is not None:
                headers["Authorization"] = "Bearer " + token
            connection.request("POST", "/v1", json.dumps(payload), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_missing_or_wrong_token_cannot_reach_operations(self):
        for token in (None, "wrong"):
            code, response = self.request({"operation": "remove", "all_monitors": True}, token)
            self.assertEqual(code, 403)
            self.assertFalse(response["ok"])
        self.assertEqual(self.driver_calls, [])

    def test_invalid_request_is_rejected_without_driver_access(self):
        for payload, content_type in (([], "application/json"), ({"operation": "create"}, "text/plain"),
                                      ({"operation": "create", "request": {}}, "application/json")):
            code, response = self.request(payload, self.token, content_type)
            self.assertEqual(code, 400)
            self.assertFalse(response["ok"])
        self.assertEqual(self.driver_calls, [])

    def test_client_reads_status_and_list_without_native_access(self):
        status = self.client.status()
        self.assertTrue(status["running"])
        self.assertEqual(status["version"], __version__)
        self.assertEqual(status["heartbeat"], "ok")
        self.assertEqual(self.client.list_managed(), [])
        self.assertEqual(self.driver_calls, [])

    def test_stop_refuses_while_monitor_ownership_exists(self):
        self.store.save([record()])
        with self.assertRaisesRegex(AgentError, "Remove its managed monitors"):
            self.client.stop()
        self.assertFalse(self.service.stopping)
        self.assertTrue(self.thread.is_alive())
        self.assertEqual(self.driver_calls, [])

    def test_empty_agent_stops_without_driver_removal(self):
        result = self.client.stop()
        self.assertTrue(result["stopped"])
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.driver_calls, [])

    def test_client_will_not_connect_to_a_non_local_endpoint(self):
        write_json(self.directory / "agent.json", {"api_version": 1, "host": "example.com",
                   "port": 80, "token": self.token})
        with patch("virtumon.agent.http.client.HTTPConnection") as connection:
            with self.assertRaises(StateError):
                self.client.call("status")
            connection.assert_not_called()

    def test_missing_agent_lists_saved_records_as_stale_without_spawning(self):
        (self.directory / "agent.json").unlink()
        self.store.save([record()])
        with patch("virtumon.agent.subprocess.Popen") as spawn:
            rows = self.client.list_managed()
        spawn.assert_not_called()
        self.assertEqual(rows[0]["status"], "stale")
        self.assertIsNone(rows[0]["device"])
        self.assertIsNone(rows[0]["x"])

    def test_unresponsive_locked_agent_is_not_replaced(self):
        (self.directory / "agent.json").unlink()
        with file_lock(self.directory / "agent.lock", timeout=0):
            with patch("virtumon.agent.sys.platform", "win32"), patch("virtumon.agent.subprocess.Popen") as spawn:
                with self.assertRaisesRegex(AgentError, "second agent was not started"):
                    self.client.ensure_running()
                spawn.assert_not_called()

    def test_compatible_older_agent_allows_cleanup_but_not_creation(self):
        older = {"running": True, "version": "0.1.99", "managed_count": 1}
        with patch("virtumon.agent.sys.platform", "win32"), patch.object(self.client, "call", return_value=older):
            with self.assertRaisesRegex(AgentError, "different Virtumon version"):
                self.client.ensure_running()
            self.assertEqual(self.client.ensure_running(allow_version_mismatch=True), older)


class HeartbeatTests(unittest.TestCase):
    def test_one_second_watchdog_is_rejected_and_handle_closed(self):
        calls = []

        class Driver:
            def open(self):
                calls.append("open")

            def watchdog(self):
                return 1, 1

            def close(self):
                calls.append("close")

        keeper = Keeper(Driver, Health())
        with self.assertRaisesRegex(OperationError, "one-second watchdog"):
            keeper.start()
        self.assertEqual(calls, ["open", "close"])
        self.assertFalse(keeper.thread.is_alive())

    def test_control_handle_is_pinned_to_keeper_device(self):
        health = Health()
        health.succeed()

        class Driver:
            device_path = "another-device"

            def __enter__(self):
                return self

            def __exit__(self, *_arguments):
                pass

        keeper = Keeper(Driver, health)
        keeper.driver = type("KeeperHandle", (), {"device_path": "original-device"})()
        with self.assertRaisesRegex(OperationError, "active SudoVDA device changed"):
            with keeper.control_driver():
                self.fail("An operation received a handle for a different adapter")

    def test_delayed_success_invalidates_saved_target_mappings(self):
        health = Health()
        with patch("virtumon.managed.time.monotonic", return_value=10):
            health.succeed()
        with patch("virtumon.managed.time.monotonic", return_value=14):
            health.succeed()
            snapshot = health.snapshot()
        self.assertEqual(snapshot["generation"], 1)
        self.assertIsNone(snapshot["error"])

    def test_overdue_heartbeat_stays_stale_after_recovery(self):
        health = Health()
        with patch("virtumon.managed.time.monotonic", return_value=10):
            health.succeed()
        with patch("virtumon.managed.time.monotonic", return_value=14):
            snapshot = health.snapshot()
            self.assertEqual(snapshot["generation"], 1)
            self.assertIsNotNone(snapshot["error"])
            health.succeed()
            self.assertEqual(health.snapshot()["generation"], 1)

    def test_keeper_reopens_failed_handle_and_does_not_remove_monitors(self):
        recovered = threading.Event()
        calls = []
        health = Health()

        class Driver:
            def __init__(self):
                self.number = len(calls)
                self.pings = 0
                calls.append(self)
                self.closed = False

            def open(self):
                return self

            def watchdog(self):
                return 3, 3

            def ping(self):
                self.pings += 1
                if self.number == 0 and self.pings > 1:
                    raise RuntimeError("driver reloaded")
                if self.number > 0:
                    recovered.set()

            def close(self):
                self.closed = True

        keeper = Keeper(Driver, health)
        keeper.start()
        try:
            self.assertTrue(recovered.wait(timeout=3), "keeper did not reconnect")
        finally:
            keeper.close()
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(item.closed for item in calls))
        self.assertEqual(health.snapshot()["generation"], 1)
        self.assertIsNone(health.snapshot()["error"])


class AgentProcessTests(unittest.TestCase):
    def test_keeper_process_advertises_endpoint_and_releases_lock_on_stop(self):
        # This uses the real process, HTTP, state, lock and keeper lifecycle.
        # Only the native display driver is substituted; no displays are changed.
        code = textwrap.dedent("""
            from pathlib import Path
            import sys
            from virtumon._agent import serve
            class Driver:
                device_path = 'test-adapter'
                def open(self): return self
                def close(self): pass
                def ping(self): pass
                def watchdog(self): return 3, 3
                def __enter__(self): return self.open()
                def __exit__(self, *args): self.close()
            class Layout:
                @staticmethod
                def list_native_displays(): return []
            serve(Path(sys.argv[1]), Driver, Layout)
        """)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
            process = subprocess.Popen([sys.executable, "-c", code, str(directory)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=flags, close_fds=True, env=os.environ.copy())
            try:
                client = AgentClient(directory)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        _out, error = process.communicate()
                        self.fail("Agent exited before readiness: " + error.decode())
                    status = client.status()
                    if status["running"]:
                        break
                    time.sleep(0.05)
                else:
                    self.fail("Agent did not advertise readiness")
                self.assertEqual(status["heartbeat"], "ok")
                self.assertEqual(client.list_managed(), [])
                with self.assertRaises(StateError):
                    with file_lock(directory / "agent.lock", timeout=0):
                        pass
                self.assertTrue(client.stop()["stopped"])
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertFalse((directory / "agent.json").exists())
                with file_lock(directory / "agent.lock", timeout=0):
                    pass
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                process.communicate()


if __name__ == "__main__":
    unittest.main()
