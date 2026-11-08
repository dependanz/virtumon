"""Named monitor operations with durable ownership and OS verification."""

from __future__ import annotations

from datetime import datetime, timezone
import re
import threading
import time
from types import SimpleNamespace
import uuid

from . import __version__
from .displays import DisplayDiscoveryError
from .state import StateStore, write_json


class OperationError(RuntimeError):
    """A requested display operation did not complete."""


def validate_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Names must start with a letter and contain at most 64 letters, digits, '-' or '_'.")
    if value.casefold() == "primary":
        raise ValueError("'primary' is reserved for the OS primary display.")
    return value


def validate_request(request: dict) -> dict:
    if not isinstance(request, dict):
        raise ValueError("Create request must be an object.")
    result = {key: request.get(key) for key in ("name", "width", "height", "refresh_hz", "side", "anchor")}
    result["name"] = validate_name(result["name"])
    for key in ("width", "height"):
        if type(result[key]) is not int or not 1 <= result[key] <= 16384:
            raise ValueError(f"{key} must be an integer from 1 to 16384 pixels.")
    if type(result["refresh_hz"]) is not int or not 1 <= result["refresh_hz"] <= 1000:
        raise ValueError("Refresh rate must be a whole number from 1 to 1000 Hz.")
    if result["side"] not in (None, "right", "left", "above", "below"):
        raise ValueError("Invalid placement direction.")
    if result["side"] is not None:
        if not isinstance(result["anchor"], str) or not result["anchor"]:
            raise ValueError("Placement requires a display anchor.")
        if result["anchor"].casefold() != "primary":
            validate_name(result["anchor"])
    elif result["anchor"] is not None:
        raise ValueError("An anchor requires a placement direction.")
    return result


def display_key(display) -> tuple[int, int, int]:
    return display.adapter_low, display.adapter_high, display.target_id


def geometry(display) -> tuple:
    return display.x, display.y, display.width, display.height, round(display.refresh_hz, 3), display.primary


def choose_position(request: dict, displays: list, managed: list[dict]) -> tuple[int, int]:
    primary = next((display for display in displays if display.primary), None)
    if request["side"] is None:
        edge = max(displays, key=lambda display: display.x + display.width, default=None)
        return (edge.x + edge.width, edge.y) if edge else (0, 0)
    if request["anchor"].casefold() == "primary":
        anchor = primary
    else:
        record = next((item for item in managed if item["name"].casefold() == request["anchor"].casefold()), None)
        anchor = None
        if record and record["status"] == "active":
            anchor = next((item for item in displays if item.device == record["device"]), None)
    if anchor is None:
        raise OperationError(f"Placement anchor '{request['anchor']}' is not an active display.")
    return {
        "right": (anchor.x + anchor.width, anchor.y),
        "left": (anchor.x - request["width"], anchor.y),
        "above": (anchor.x, anchor.y - request["height"]),
        "below": (anchor.x, anchor.y + anchor.height),
    }[request["side"]]


class Health:
    """A heartbeat outage invalidates mappings even if the driver later recovers."""
    def __init__(self):
        self.error: str | None = None
        self.generation = 0
        self.last_success = 0.0
        self.timeout = 3
        self._lock = threading.Lock()

    def fail(self, message: str) -> None:
        with self._lock:
            if self.error is None:
                self.generation += 1
            self.error = message

    def succeed(self) -> None:
        with self._lock:
            now = time.monotonic()
            # The driver's global one-second tick can decrement immediately
            # after a ping. Timeout=3 can expire just over two seconds later.
            if (self.last_success and self.timeout and self.error is None and
                    now - self.last_success >= max(0, self.timeout - 1)):
                self.generation += 1
            self.last_success = now
            self.error = None

    def set_timeout(self, seconds: int) -> None:
        with self._lock:
            self.timeout = seconds

    def snapshot(self) -> dict:
        with self._lock:
            if (self.last_success and self.timeout and self.error is None and
                    time.monotonic() - self.last_success >= max(0, self.timeout - 1)):
                self.error = "The driver heartbeat is overdue; saved display mappings are unverified."
                self.generation += 1
            return {"error": self.error, "generation": self.generation, "last_success": self.last_success, "timeout": self.timeout}


class MonitorManager:
    def __init__(self, store: StateStore, driver_factory, layout, health: Health, session_id: str | None = None):
        self.store, self.driver_factory, self.layout, self.health = store, driver_factory, layout, health
        self.session_id = session_id or str(uuid.uuid4())
        self.lock = threading.RLock()
        self.store.load()  # Fail before starting a writable service if state is malformed.

    def _current(self, record: dict) -> bool:
        health = self.health.snapshot()
        return (record.get("session_id") == self.session_id and
                record.get("generation") == health["generation"] and health["error"] is None)

    def _row(self, record: dict, displays: list) -> dict:
        result = {key: record[key] for key in ("name", "id", "backend", "width", "height", "refresh_hz")}
        result.update(status="stale", device=None, x=None, y=None, primary=False)
        identity = record.get("identity")
        if self._current(record) and identity is not None:
            key = identity["adapter_low"], identity["adapter_high"], identity["target_id"]
            display = next((item for item in displays if display_key(item) == key), None)
            if display:
                result.update(status="active", **{field: getattr(display, field) for field in
                              ("device", "x", "y", "width", "height", "refresh_hz", "primary")})
            else:
                result["status"] = "missing"
        if record.get("error"):
            result["status"] = "error"
            result["error"] = record["error"]
        return result

    def list_managed(self) -> list[dict]:
        with self.lock:
            records = self.store.load()
            if not records:
                return []
            try:
                displays = self.layout.list_native_displays()
            except DisplayDiscoveryError as exc:
                # A display hotplug can re-clone an already managed virtual
                # monitor onto the primary desktop. Keep the journal visible,
                # but do not promote any saved identity to an active display.
                rows = [self._row(record, []) for record in records]
                for row in rows:
                    prior = row.get("error")
                    row.update(
                        status="error", device=None, x=None, y=None, primary=False,
                        error=("Windows display topology is inconsistent: " + str(exc) +
                               (f"; previous error: {prior}" if prior else "")),
                    )
                return rows
            return [self._row(record, displays) for record in records]

    def create(self, request: dict) -> dict:
        request = validate_request(request)
        with self.lock:
            records = self.store.load()
            if any(record["name"].casefold() == request["name"].casefold() for record in records):
                raise OperationError(f"A virtumon named '{request['name']}' already exists. Remove it before reusing the name.")
            # Observed on Windows 11: the driver's second AddMonitor call
            # can re-clone the first virtual display onto the physical screen,
            # even though the new display briefly appears independently.
            # Until one guarded CCD transaction can reconcile ALL owned
            # displays, refuse to mutate a nonempty managed layout.
            if records:
                raise OperationError(
                    "Creating a second virtumon is temporarily unavailable in this preview: "
                    "Windows can displace an existing virtual monitor while adding another. "
                    "Remove the existing virtumon before creating a new one."
                )
            health = self.health.snapshot()
            if health["error"]:
                raise OperationError("Driver heartbeat is unavailable: " + health["error"])
            before = self.layout.list_native_displays()
            position = choose_position(request, before, [self._row(record, before) for record in records])
            x, y = position
            if any(x < item.x + item.width and x + request["width"] > item.x and
                   y < item.y + item.height and y + request["height"] > item.y for item in before):
                raise OperationError("The requested position overlaps an existing display. Choose an outer edge of the desktop.")
            record = {key: request[key] for key in ("name", "width", "height", "refresh_hz")}
            record.update(id=str(uuid.uuid4()), backend="sudovda", identity=None,
                          session_id=self.session_id, generation=health["generation"])
            records.append(record)
            self.store.save(records)  # Journal ownership BEFORE the first native mutation.
            identity = None
            attempted = False
            phase = "driver-add"
            try:
                with self.driver_factory() as driver:
                    record["driver_path"] = driver.device_path
                    self.store.save(records)
                    attempted = True
                    identity = driver.add(record["id"], record["width"], record["height"],
                                          record["refresh_hz"] * 1000, record["name"])
                record["identity"] = {key: getattr(identity, key) for key in ("adapter_low", "adapter_high", "target_id")}
                self.store.save(records)
                if not self._current(record):
                    raise OperationError("The driver heartbeat was interrupted before display configuration.")
                phase = "configure"
                display = self.layout.configure_display(identity, record["width"], record["height"],
                                                        record["refresh_hz"], *position,
                                                        guard=lambda: self._current(record),
                                                        creation_baseline=before)
                phase = "verify-created-display"
                after = self.layout.list_native_displays()
                by_key = {display_key(item): item for item in after}
                if any(display_key(item) not in by_key or geometry(item) != geometry(by_key[display_key(item)])
                       for item in before):
                    raise OperationError("Windows changed another display during creation; the new virtumon will be removed.")
                if not self._current(record):
                    raise OperationError("The driver heartbeat was interrupted during creation.")
                if display_key(display) != display_key(identity):
                    raise OperationError("Windows returned a different display identity during configuration.")
                display = by_key.get(display_key(identity))
                if display is None:
                    raise OperationError("The new display disappeared before creation could be verified.")
                if (display.width, display.height, display.x, display.y) != (record["width"], record["height"], *position):
                    raise OperationError("Windows did not apply the requested resolution and placement.")
                if abs(display.refresh_hz - record["refresh_hz"]) > 0.1:
                    raise OperationError("Windows did not apply the requested refresh rate.")
                record["last_observed"] = {field: getattr(display, field) for field in
                                           ("device", "x", "y", "width", "height", "refresh_hz", "primary")}
                self.store.save(records)
                return self._row(record, after)
            except Exception as exc:
                diagnostic_note = self._save_creation_diagnostic(exc, record, request, before, position, phase)
                cleanup_error = None
                if attempted:
                    try:
                        with self.driver_factory() as driver:
                            self._check_driver_path(record, driver)
                            removed = driver.remove(record["id"])
                        if removed and identity is not None and self._current(record):
                            self._wait_removed(identity)
                    except Exception as cleanup_exc:
                        cleanup_error = str(cleanup_exc)
                if cleanup_error:
                    record["error"] = f"Creation failed: {exc}; cleanup failed: {cleanup_error}"
                    self.store.save(records)
                    raise OperationError(record["error"] + f". Retry: virtumon remove {record['name']}" + diagnostic_note) from exc
                self.store.save([item for item in records if item["id"] != record["id"]])
                raise OperationError(f"Could not create '{record['name']}': {exc}. No new virtumon was retained."
                                     + diagnostic_note) from exc

    def _save_creation_diagnostic(self, error, record, request, before, position, phase) -> str:
        report = getattr(error, "diagnostics", None)
        if not isinstance(report, dict):
            return ""
        try:
            path = self.store.directory / "diagnostics" / f"create-{record['id']}.json"
            fields = ("device", "x", "y", "width", "height", "refresh_hz", "primary",
                      "adapter_low", "adapter_high", "target_id")
            write_json(path, {
                "schema": "virtumon.create-diagnostic/v1", "version": __version__,
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "error": str(error), "operation_phase": phase,
                "request": request, "requested_position": list(position),
                "monitor_id": record["id"], "driver_identity": record.get("identity"),
                "before_creation": [{field: getattr(display, field) for field in fields} for display in before],
                "native_metadata": report,
                "note": "Recorded before exact-GUID cleanup; this file does not establish cleanup success.",
            })
            return f" Diagnostic report: {path}"
        except Exception as diagnostic_exc:
            return f" Diagnostic report could not be saved: {diagnostic_exc}"

    @staticmethod
    def _check_driver_path(record: dict, driver) -> None:
        if record.get("driver_path") is not None and record["driver_path"] != driver.device_path:
            raise OperationError("This virtumon belongs to a different SudoVDA device. Its ownership record was retained; "
                                 "restore the original device before retrying removal.")

    def _wait_removed(self, identity, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while self.layout.is_display_present(identity):
            if time.monotonic() >= deadline:
                raise OperationError("Driver accepted removal, but Windows still reports the display.")
            time.sleep(0.1)

    def remove(self, names: list[str] | None = None, all_monitors: bool = False) -> dict:
        if type(all_monitors) is not bool or (names is not None and not isinstance(names, list)):
            raise ValueError("Invalid remove request.")
        if names:
            for name in names:
                validate_name(name)
        with self.lock:
            records = self.store.load()
            by_name = {record["name"].casefold(): record for record in records}
            if all_monitors and names:
                raise OperationError("Choose monitor names or --all, not both.")
            if not all_monitors and not names:
                raise OperationError("Specify a monitor name or --all.")
            if names:
                missing = [name for name in names if name.casefold() not in by_name]
                if missing:
                    raise OperationError("Unknown virtumon name(s): " + ", ".join(missing))
            selected = list(by_name) if all_monitors else list(dict.fromkeys(name.casefold() for name in names))
            result = {"removed": [], "errors": []}
            for name in selected:
                record = by_name[name]
                try:
                    with self.driver_factory() as driver:
                        self._check_driver_path(record, driver)
                        removed = driver.remove(record["id"])
                    # Old target IDs may have been reused. The GUID is authoritative;
                    # only verify target disappearance for a current, healthy mapping.
                    if removed and record.get("identity") and self._current(record):
                        self._wait_removed(SimpleNamespace(**record["identity"]))
                    records = [item for item in records if item["id"] != record["id"]]
                    self.store.save(records)
                    result["removed"].append(record["name"])
                except Exception as exc:
                    result["errors"].append({"name": record["name"], "error": str(exc)})
            return result


def offline_rows(store: StateStore) -> list[dict]:
    rows = []
    for record in store.load():
        row = {key: record[key] for key in ("name", "id", "backend", "width", "height", "refresh_hz")}
        row.update(status="stale", device=None, x=None, y=None, primary=False)
        row["error"] = "The Virtumon agent is not running; the saved display identity is unverified."
        rows.append(row)
    return rows
