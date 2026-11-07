"""Private per-user state, atomic writes, and cross-process locks."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid


class StateError(RuntimeError):
    """Stored ownership or agent state cannot be read safely."""


def state_directory() -> Path:
    override = os.environ.get("VIRTUMON_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return base / "Virtumon"


def ensure_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)


def read_json(path: Path, default=None):
    try:
        if path.stat().st_size > 1_048_576:
            raise StateError(f"State file is unexpectedly large: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise StateError(f"Cannot read {path}: {exc}") from exc


def write_json(path: Path, value) -> None:
    ensure_directory(path.parent)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + 0.5
        while True:
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                # A Windows reader or scanner may briefly hold the previous
                # file without FILE_SHARE_DELETE. Retry only those native
                # sharing/access errors; preserve the old journal on failure.
                if getattr(exc, "winerror", None) not in (5, 32, 33) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    except (OSError, ValueError, TypeError) as exc:
        raise StateError(f"Cannot save {path}: {exc}") from exc
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


@contextmanager
def file_lock(path: Path, timeout: float = 10):
    """Hold a one-byte OS lock; process exit also releases it."""
    ensure_directory(path.parent)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise StateError("Another Virtumon process holds the state lock.") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class StateStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "monitors.json"

    def load(self) -> list[dict]:
        data = read_json(self.path, {"schema": 1, "monitors": []})
        if (not isinstance(data, dict) or type(data.get("schema")) is not int or
                data.get("schema") != 1 or not isinstance(data.get("monitors"), list)):
            raise StateError("Unsupported or malformed Virtumon ownership state; no displays were changed.")
        names, ids = set(), set()
        records = data["monitors"]
        for record in records:
            try:
                if not isinstance(record, dict):
                    raise ValueError("record is not an object")
                name = record["name"]
                monitor_uuid = uuid.UUID(record["id"])
                monitor_id = str(monitor_uuid)
                if monitor_uuid.version != 4 or record["id"] != monitor_id:
                    raise ValueError("monitor ID must be a canonical UUID4")
                if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) or
                        name.casefold() == "primary" or name.casefold() in names or monitor_id in ids):
                    raise ValueError("duplicate or invalid monitor name/ID")
                if record.get("backend") != "sudovda":
                    raise ValueError("unknown backend")
                driver_path = record.get("driver_path")
                if driver_path is not None and (not isinstance(driver_path, str) or not driver_path or
                                                len(driver_path) > 4096 or "\0" in driver_path):
                    raise ValueError("invalid driver path")
                for key in ("width", "height", "refresh_hz"):
                    upper_bound = 1000 if key == "refresh_hz" else 16384
                    if type(record[key]) is not int or not 1 <= record[key] <= upper_bound:
                        raise ValueError(f"invalid {key}")
                if "session_id" in record and str(uuid.UUID(record["session_id"])) != record["session_id"]:
                    raise ValueError("invalid session ID")
                if "generation" in record and (type(record["generation"]) is not int or record["generation"] < 0):
                    raise ValueError("invalid mapping generation")
                identity = record.get("identity")
                if identity is not None:
                    if not isinstance(identity, dict) or set(identity) != {"adapter_low", "adapter_high", "target_id"}:
                        raise ValueError("invalid display identity")
                    if any(type(value) is not int for value in identity.values()):
                        raise ValueError("non-integer display identity")
                    if not 0 <= identity["adapter_low"] <= 0xFFFFFFFF or not 0 <= identity["target_id"] <= 0xFFFFFFFF:
                        raise ValueError("display identity outside DWORD range")
                    if not -(1 << 31) <= identity["adapter_high"] < (1 << 31):
                        raise ValueError("adapter identity outside LONG range")
                names.add(name.casefold())
                ids.add(monitor_id)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise StateError(f"Malformed Virtumon ownership state: {exc}. No displays were changed.") from exc
        return records

    def save(self, records: list[dict]) -> None:
        write_json(self.path, {"schema": 1, "monitors": records})
