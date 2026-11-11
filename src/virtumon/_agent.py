"""Internal detached keeper. Use ``virtumon agent`` for user-facing controls."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from socketserver import TCPServer
import threading
import time
import uuid

from . import __version__
from .backends.sudovda import SudoVDADriver
from .managed import Health, MonitorManager, OperationError
from .state import StateStore, file_lock, read_json, state_directory, write_json


class Keeper:
    """Keep the driver's watchdog alive using a dedicated synchronous handle."""
    def __init__(self, driver_factory, health: Health):
        self.driver_factory, self.health = driver_factory, health
        self.driver = None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="virtumon-heartbeat", daemon=True)
        # Test-only, one-shot, self-expiring heartbeat suppression. The keeper
        # process and its authenticated IPC remain alive during the pause.
        self._fault_lock = threading.Lock()
        self._fault_requested = 0.0
        self._fault_until = 0.0
        self._fault_used = False
        self._fault_started = threading.Event()

    def _connect(self):
        driver = self.driver_factory()
        try:
            driver.open()
            timeout, _remaining = driver.watchdog()
            if timeout == 1:
                raise OperationError("SudoVDA's one-second watchdog is too short for safe monitor management. "
                                     "Use the upstream default of three seconds (at least two seconds), then reload the driver.")
            self.health.set_timeout(timeout)
            driver.ping()
            self.health.succeed()
            return driver
        except Exception:
            try:
                driver.close()
            except Exception:
                pass
            raise

    def start(self) -> None:
        self.driver = self._connect()  # Fail startup before advertising readiness.
        self.thread.start()

    @contextmanager
    def control_driver(self):
        snapshot = self.health.snapshot()
        keeper_driver = self.driver
        if keeper_driver is None or snapshot["error"]:
            raise OperationError("The driver keeper is recovering; retry after 'virtumon agent status' reports a healthy heartbeat.")
        path = keeper_driver.device_path
        with self.driver_factory() as driver:
            current = self.health.snapshot()
            if (driver.device_path != path or self.driver is not keeper_driver or
                    current["error"] or current["generation"] != snapshot["generation"]):
                raise OperationError("The active SudoVDA device changed while opening a control handle; retry after the keeper recovers.")
            yield driver

    def pause_heartbeats_once(self, seconds: float = 5.0) -> dict:
        """Test-only one-shot pause, acked by the keeper thread itself.

        The pause begins when the heartbeat thread observes it, not when
        authenticated IPC receives the request. It self-expires; an IPC
        failure or test runner crash cannot prolong the suppression.
        """
        if seconds != 5.0:
            raise OperationError("M3-B permits only the fixed five-second pause.")
        with self._fault_lock:
            if self._fault_used or not self.thread.is_alive():
                raise OperationError("M3-B pause already used or keeper thread stopped.")
            self._fault_used = True
            self._fault_requested = seconds
        if not self._fault_started.wait(timeout=1.5):
            raise OperationError("Keeper heartbeat did not acknowledge the bounded fault.")
        return {"paused": True, "duration_seconds": seconds}

    def _run(self) -> None:
        try:
            while not self.stop_event.wait(0.25):
                now = time.monotonic()
                with self._fault_lock:
                    if self._fault_requested:
                        self._fault_until = now + self._fault_requested
                        self._fault_requested = 0.0
                        self._fault_started.set()
                    paused = now < self._fault_until
                if paused:
                    continue
                try:
                    if self.driver is None:
                        self.driver = self._connect()
                    else:
                        self.driver.ping()
                        self.health.succeed()
                except Exception as exc:
                    self.health.fail(str(exc))
                    if self.driver is not None:
                        try:
                            self.driver.close()
                        except Exception:
                            pass
                        self.driver = None
        finally:
            if self.driver is not None:
                try:
                    self.driver.close()
                except Exception:
                    pass

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)


class AgentService:
    def __init__(self, manager: MonitorManager, health: Health, keeper: Keeper | None = None):
        self.manager, self.health, self.keeper = manager, health, keeper
        self.server = None
        self.stopping = False

    def dispatch(self, request: dict):
        operation = request.get("operation")
        if operation == "status":
            snapshot = self.health.snapshot()
            return {"running": True, "version": __version__, "pid": os.getpid(),
                    "managed_count": len(self.manager.store.load()),
                    "heartbeat": "ok" if snapshot["error"] is None else "error",
                    "heartbeat_age_seconds": round(time.monotonic() - snapshot["last_success"], 3),
                    "watchdog_timeout_seconds": snapshot["timeout"], "error": snapshot["error"],
                    "stopping": self.stopping}
        if operation == "list":
            return self.manager.list_managed()
        with self.manager.lock:
            if self.stopping:
                raise OperationError("The Virtumon agent is stopping.")
            if operation == "m3_pause_heartbeat":
                # No public CLI command exposes this fault. It is disabled
                # unless the keeper was started with the explicit test opt-in.
                if os.environ.get("VIRTUMON_M3_B_ALLOW") != "YES":
                    raise OperationError("M3-B heartbeat fault injection is disabled.")
                if set(request) != {"operation", "name", "uuid"}:
                    raise OperationError("M3-B request must contain only the expected name and UUID.")
                if self.keeper is None:
                    raise OperationError("M3-B keeper fault controller is unavailable.")
                records = self.manager.store.load()
                if (len(records) != 1 or
                        records[0].get("name") != request.get("name") or
                        records[0].get("id") != request.get("uuid")):
                    raise OperationError("M3-B requires exactly the named, UUID-matched monitor.")
                health = self.health.snapshot()
                if health["timeout"] != 3 or health["error"] is not None or not self.manager._current(records[0]):
                    raise OperationError("M3-B requires healthy current ownership and a three-second watchdog.")
                return self.keeper.pause_heartbeats_once()
            if operation == "create":
                return self.manager.create(request.get("request"))
            if operation == "remove":
                return self.manager.remove(request.get("names"), request.get("all_monitors", False))
            if operation == "stop":
                if self.manager.store.load():
                    raise OperationError("The agent keeps your virtumons alive. Remove its managed monitors before stopping it.")
                self.stopping = True
                return {"stopped": True, "already_stopped": False}
        raise ValueError("Unknown agent operation.")


def create_server(service: AgentService, token: str) -> ThreadingHTTPServer:
    class LoopbackServer(ThreadingHTTPServer):
        def server_bind(self):
            # HTTPServer normally reverse-resolves its numeric listen address.
            # Local authenticated IPC needs no hostname or network DNS lookup.
            TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    class Handler(BaseHTTPRequestHandler):
        server_version = "Virtumon"

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, _format, *_arguments):
            pass  # Never log authentication headers or user request bodies.

        def reply(self, code: int, payload: dict):
            data = json.dumps(payload, allow_nan=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != "/v1":
                self.reply(404, {"ok": False, "error": "Unknown endpoint."})
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + token).encode("ascii")):
                self.reply(403, {"ok": False, "error": "Local agent authentication failed."})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json" or self.headers.get("Transfer-Encoding"):
                self.reply(400, {"ok": False, "error": "Expected a bounded JSON request."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError("Invalid request size.")
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict):
                    raise ValueError("Expected a JSON object.")
                result = service.dispatch(request)
            except Exception as exc:
                self.reply(400, {"ok": False, "error": str(exc)})
                return
            try:
                self.reply(200, {"ok": True, "result": result})
            finally:
                # Send the stop acknowledgment before the detached process can
                # exit and tear down its daemon request-handler threads.
                if request.get("operation") == "stop":
                    threading.Thread(target=self.server.shutdown, daemon=True).start()

    server = LoopbackServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    service.server = server
    return server


def serve(directory: Path, driver_factory=SudoVDADriver, layout=None) -> None:
    if layout is None:
        from . import windows_layout as layout
    instance_id = str(uuid.uuid4())
    with file_lock(directory / "agent.lock", timeout=0):
        health = Health()
        keeper = Keeper(driver_factory, health)
        manager = MonitorManager(StateStore(directory), keeper.control_driver, layout, health, instance_id)
        server = None
        try:
            keeper.start()
            token = secrets.token_hex(32)
            server = create_server(AgentService(manager, health, keeper), token)
            write_json(directory / "agent.json", {
                "api_version": 1, "version": __version__, "host": "127.0.0.1",
                "port": server.server_address[1], "token": token,
                "pid": os.getpid(), "instance_id": instance_id,
            })
            server.serve_forever(poll_interval=0.1)
        finally:
            if server is not None:
                server.server_close()
            if keeper.thread.is_alive():
                keeper.close()
            endpoint = read_json(directory / "agent.json", {})
            if isinstance(endpoint, dict) and endpoint.get("instance_id") == instance_id:
                (directory / "agent.json").unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    directory = args.state_dir or state_directory()
    try:
        serve(directory)
    except Exception as exc:
        write_json(directory / "startup-error.json", {"error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
