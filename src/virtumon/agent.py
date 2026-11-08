"""Authenticated localhost client for the detached Windows monitor keeper."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import subprocess
import sys
import time

from . import __version__
from .managed import offline_rows, validate_request
from .state import StateError, StateStore, ensure_directory, file_lock, read_json, state_directory


class AgentError(RuntimeError):
    """The agent could not complete an operation."""


class AgentUnavailableError(AgentError):
    """The local agent endpoint is unavailable or timed out."""


class AgentClient:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or state_directory()

    def _endpoint(self) -> dict:
        endpoint = read_json(self.directory / "agent.json")
        if endpoint is None:
            raise AgentUnavailableError("The Virtumon agent is not running.")
        if (not isinstance(endpoint, dict) or endpoint.get("api_version") != 1 or
                endpoint.get("host") != "127.0.0.1" or type(endpoint.get("port")) is not int or
                not 1 <= endpoint["port"] <= 65535 or not isinstance(endpoint.get("token"), str) or
                len(endpoint["token"]) != 64 or any(char not in "0123456789abcdef" for char in endpoint["token"])):
            raise StateError("Malformed Virtumon agent endpoint; no request was sent.")
        return endpoint

    def call(self, operation: str, *, timeout: float = 40, **arguments):
        endpoint = self._endpoint()
        connection = http.client.HTTPConnection("127.0.0.1", endpoint["port"], timeout=timeout)
        try:
            connection.request("POST", "/v1", json.dumps({"operation": operation, **arguments}),
                               {"Authorization": "Bearer " + endpoint["token"], "Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(262145)
            if len(raw) > 262144:
                raise AgentError("The local agent returned an oversized response.")
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise AgentError("The local agent returned an invalid response.") from exc
            if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
                raise AgentError("The local agent returned an invalid response.")
            if response.status != 200 or not payload["ok"]:
                raise AgentError(str(payload.get("error", "The local agent rejected the request.")))
            return payload.get("result")
        except (OSError, http.client.HTTPException) as exc:
            raise AgentUnavailableError(
                "The Virtumon agent did not respond. An operation may still be running; "
                "check 'virtumon agent status' and 'virtumon list --managed' before retrying."
            ) from exc
        finally:
            connection.close()

    def status(self) -> dict:
        try:
            return self.call("status", timeout=2)
        except AgentUnavailableError:
            records = StateStore(self.directory).load()
            return {"running": False, "managed_count": len(records), "version": None,
                    "error": "The agent is stopped or unresponsive; saved monitor mappings are unverified."}

    def list_managed(self) -> list[dict]:
        try:
            return self.call("list", timeout=10)
        except AgentUnavailableError:
            # Listing never launches a keeper or treats saved geometry as live.
            return offline_rows(StateStore(self.directory))

    def ensure_running(self, *, allow_version_mismatch: bool = False) -> dict:
        if sys.platform != "win32":
            raise AgentError("Virtual monitor creation is available on Windows only; macOS and Linux support is planned.")
        ensure_directory(self.directory)
        with file_lock(self.directory / "startup.lock"):
            try:
                status = self.call("status", timeout=2)
            except AgentUnavailableError:
                status = None
            if status:
                if not allow_version_mismatch and status.get("version") != __version__:
                    raise AgentError("A different Virtumon version is running. Remove its managed monitors and run "
                                     "'virtumon agent stop' before starting this version.")
                return status
            # A live but unresponsive process must not be replaced by a second
            # writer. Never infer process ownership from an arbitrary saved PID.
            try:
                with file_lock(self.directory / "agent.lock", timeout=0):
                    pass
            except StateError as exc:
                raise AgentError("The Virtumon agent is already running but is unresponsive. "
                                 "Inspect agent.log in the Virtumon state directory; a second agent was not started.") from exc
            StateStore(self.directory).load()
            (self.directory / "startup-error.json").unlink(missing_ok=True)
            with (self.directory / "agent.log").open("ab") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "virtumon._agent", "--state-dir", str(self.directory)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    return self.call("status", timeout=0.5)
                except AgentUnavailableError:
                    pass
                if process.poll() is not None:
                    detail = read_json(self.directory / "startup-error.json", {})
                    raise AgentError(detail.get("error", "The agent could not start; inspect agent.log in the state directory."))
                time.sleep(0.1)
            raise AgentError("The agent is still starting or blocked. Check 'virtumon agent status' before retrying.")

    def create(self, request: dict) -> dict:
        request = validate_request(request)
        self.ensure_running()
        return self.call("create", request=request)

    def remove(self, names: list[str], all_monitors: bool = False) -> dict:
        # Validate the complete selection before even starting a driver keeper.
        records = StateStore(self.directory).load()
        known = {record["name"].casefold() for record in records}
        if all_monitors and names or not all_monitors and not names:
            raise AgentError("Specify monitor names or --all.")
        missing = [name for name in names if name.casefold() not in known]
        if missing:
            raise AgentError("Unknown virtumon name(s): " + ", ".join(missing))
        if not records:
            return {"removed": [], "errors": []}
        self.ensure_running(allow_version_mismatch=True)
        return self.call("remove", names=names, all_monitors=all_monitors)

    def stop(self) -> dict:
        try:
            result = self.call("stop", timeout=5)
            with file_lock(self.directory / "agent.lock", timeout=5):
                return result
        except AgentUnavailableError:
            # Only say already stopped if the OS lock confirms no keeper owns it.
            with file_lock(self.directory / "agent.lock", timeout=0):
                return {"stopped": True, "already_stopped": True}
