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

    def _run(self) -> None:
        try:
            while not self.stop_event.wait(0.25):
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
    def __init__(self, manager: MonitorManager, health: Health):
        self.manager, self.health = manager, health
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
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
            server = create_server(AgentService(manager, health), token)
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
