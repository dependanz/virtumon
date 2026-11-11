"""Opt-in native Windows reliability acceptance for Virtumon 0.2 preview.

This is a *local hardware exercise*, not a CI test. It uses only public CLI
commands; never remove --all, change display registry/certificates, restart
drivers, or remove pre-existing monitors.

By default it performs read-only preflight. --exercise requires:
- one physical primary display and zero saved Virtumon monitors
- the agent stopped before starting
- VIRTUMON_EXPERIMENTAL_MULTI=1 in this process (inherited by the keeper)
- driver readiness, and explicit bounded cycle/soak parameters

The per-cycle UUIDs, geometry, health and cleanup are verified before moving
to the next cycle. Receipts are written in the user's Virtumon diagnostics
directory, including on failure. Use the physical laptop screen as fallback.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


class AcceptanceFailure(RuntimeError):
    """A safety precondition or verification failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def event(receipt: dict, phase: str, **details) -> None:
    receipt["events"].append({"phase": phase, "at_utc": datetime.now(timezone.utc).isoformat(),
                               **details})


class InstalledCli:
    """Run the selected installed Virtumon, never import checkout internals."""

    def __init__(self, interpreter: str):
        self.interpreter = str(Path(interpreter).resolve())
        require(Path(self.interpreter).is_file(), "Python interpreter does not exist.")

    def call(self, *arguments: str, shape: type | None = None):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["NO_COLOR"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        args = [self.interpreter, "-m", "virtumon", *arguments]
        try:
            result = subprocess.run(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                timeout=60, cwd=tempfile.gettempdir(), env=env, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AcceptanceFailure("Virtumon CLI timed out: " + " ".join(arguments)) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise AcceptanceFailure(
                f"virtumon {' '.join(arguments)} failed ({result.returncode}): {detail}"
            )
        if shape is None:
            return result.stdout.strip()
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise AcceptanceFailure(
                "Invalid JSON from virtumon " + " ".join(arguments)
            ) from exc
        require(type(value) is shape, f"Incorrect JSON shape for {' '.join(arguments)}")
        return value


def physical_signature(row: dict) -> tuple:
    return tuple(row.get(k) for k in ("device", "x", "y", "width", "height", "primary"))


def preflight(cli, *, exercise: bool):
    version = cli.call("--version")
    doctor = cli.call("doctor")
    displays = cli.call("list", "--json", shape=list)
    managed = cli.call("list", "--managed", "--json", shape=list)
    agent = cli.call("agent", "status", "--json", shape=dict)
    require(displays, "Windows desktop enumeration returned no active monitors.")
    names = [row.get("device") for row in displays]
    require(len(names) == len(set(names)), "Duplicate Windows display device names.")
    require(sum(row.get("primary") is True for row in displays) == 1,
            "Exactly one primary desktop must be reported.")
    require("SudoVDA driver available" in doctor,
            "SudoVDA readiness was not confirmed by virtumon doctor.")
    if exercise:
        require(sys.platform == "win32", "--exercise is supported only on Windows.")
        require("0.2.0a" in version, "Install the experimental Virtumon 0.2 preview before exercising.")
        require(os.environ.get("VIRTUMON_EXPERIMENTAL_MULTI") == "1",
                "Set VIRTUMON_EXPERIMENTAL_MULTI=1 before starting the keeper.")
        require(not managed, "Managed monitors already exist; refusing to alter them.")
        require(not agent.get("running"), "Stop the existing agent before exercising.")
        require(len(displays) == 1 and displays[0].get("primary") is True,
                "This first M2 acceptance requires one physical primary display.")
        require(all(isinstance(displays[0].get(field), int)
                    for field in ("x", "y", "width", "height")),
                "Physical display geometry is incomplete.")
        require(displays[0]["width"] > 0 and displays[0]["height"] > 0,
                "Invalid physical display dimensions.")
    return displays, managed, agent, version


def verify_layout(cli, baseline: list[dict], expected: dict[str, dict],
                  *, expect_agent: bool):
    displays = cli.call("list", "--json", shape=list)
    managed = cli.call("list", "--managed", "--json", shape=list)
    agent = cli.call("agent", "status", "--json", shape=dict)

    require(len(displays) == len(baseline) + len(expected),
            "Unexpected Windows display count.")
    require(len(managed) == len(expected), "Unexpected managed monitor count.")
    require(len(set(x.get("device") for x in displays)) == len(displays),
            "Windows returned duplicate display device names.")
    original = baseline[0]
    match = [x for x in displays if x.get("device") == original["device"]]
    require(len(match) == 1 and physical_signature(match[0]) == physical_signature(original),
            "The physical display changed geometry, primary flag, or identity.")
    require(sum(x.get("primary") is True for x in displays) == 1,
            "The primary display changed.")

    for name, wanted in expected.items():
        rows = [r for r in managed if r.get("name") == name and r.get("id") == wanted["id"]]
        require(len(rows) == 1, f"{name} is missing or its managed UUID changed.")
        row = rows[0]
        require(row.get("status") == "active", f"{name} is not active: {row.get('error')}")
        require(row.get("primary") is False, f"{name} became a primary monitor.")
        for field in ("width", "height", "x", "y"):
            require(row.get(field) == wanted[field], f"{name} changed {field}.")
        require(isinstance(row.get("refresh_hz"), (int, float)) and
                abs(row["refresh_hz"] - wanted["refresh_hz"]) <= 0.1,
                f"{name} changed refresh rate.")
        os_rows = [r for r in displays if r.get("device") == row.get("device")]
        require(len(os_rows) == 1 and
                all(os_rows[0].get(k) == row.get(k)
                    for k in ("x", "y", "width", "height", "primary")),
                f"{name} is not an independent Windows desktop with the expected geometry.")
    if expect_agent:
        require(agent.get("running") is True, "The keeper stopped while monitors were active.")
        require(agent.get("heartbeat") == "ok" and agent.get("error") is None,
                "The keeper heartbeat is unhealthy.")
        require(agent.get("managed_count") == len(expected),
                "The keeper's managed count disagrees with the visible desktop.")
    return {"managed": managed, "displays": displays, "agent": agent}


def _new_names():
    token = uuid.uuid4().hex[:12]
    return "m2work-" + token, "m2chat-" + token


def _require_created(row, *, name: str, width: int, height: int,
                     x: int, y: int) -> dict:
    require(row.get("name") == name and row.get("status") == "active",
            f"Unexpected create result for {name}.")
    try:
        uuid.UUID(row["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AcceptanceFailure("Create returned an invalid UUID.") from exc
    require(all(row.get(k) == v for k, v in
                (("width", width), ("height", height), ("x", x), ("y", y),
                 ("primary", False))), "Created monitor has incorrect geometry.")
    require(isinstance(row.get("refresh_hz"), (int, float)) and
            abs(row["refresh_hz"] - 60) < 0.1,
            "Created monitor has incorrect refresh.")
    return {"id": row["id"], "width": width, "height": height,
            "x": x, "y": y, "refresh_hz": 60}


def _remove_exact(cli, name: str) -> None:
    result = cli.call("remove", name, "--json", shape=dict)
    require(result.get("removed") == [name] and result.get("errors") == [],
            f"Exact-name removal of {name} was not confirmed.")


def _cleanup(cli, baseline, attempted, receipt):
    """Remove ONLY script-generated names. Stop agent only after empty baseline."""
    issues = []
    for name in reversed(attempted):
        try:
            owned = cli.call("list", "--managed", "--json", shape=list)
            if any(row.get("name") == name for row in owned):
                _remove_exact(cli, name)
                event(receipt, "cleanup-removed", name=name)
        except Exception as exc:
            issues.append(f"{name}: {exc}")
            event(receipt, "cleanup-error", name=name, error=str(exc))
    try:
        verify_layout(cli, baseline, {}, expect_agent=False)
        agent = cli.call("agent", "status", "--json", shape=dict)
        if agent.get("running"):
            cli.call("agent", "stop")
        status = cli.call("agent", "status", "--json", shape=dict)
        require(not status.get("running"), "Keeper is still running after cleanup.")
        event(receipt, "cleanup-baseline-restored")
    except Exception as exc:
        issues.append(f"Baseline restoration: {exc}")
        event(receipt, "cleanup-error", error=str(exc))
    return issues


def exercise(cli, baseline, *, cycles: int, soak_seconds: int,
             poll_seconds: int, sleep, clock, receipt):
    require(len(baseline) == 1, "Reliability exercise requires one physical primary.")
    original = baseline[0]
    work_x = original["x"] + original["width"]
    work_y = original["y"]
    chat_x = work_x + 1920
    attempted = []
    failed = None

    try:
        for cycle in range(1, cycles + 1):
            work_name, chat_name = _new_names()
            print(f"Cycle {cycle}/{cycles}: {work_name}, {chat_name}", flush=True)
            expected = {}
            attempted.append(work_name)  # Before CLI: creation may partially succeed.
            work = cli.call("create", work_name, "--resolution", "1920x1080",
                            "--refresh", "60", "--right-of", "primary", "--json",
                            shape=dict)
            expected[work_name] = _require_created(
                work, name=work_name, width=1920, height=1080, x=work_x, y=work_y
            )
            verify_layout(cli, baseline, expected, expect_agent=True)
            event(receipt, "created-work", cycle=cycle, name=work_name,
                  uuid=work["id"], device=work.get("device"),
                  creation_route=work.get("creation_route", "unreported"))

            attempted.append(chat_name)
            chat = cli.call("create", chat_name, "--resolution", "1080x1920",
                            "--refresh", "60", "--right-of", work_name, "--json",
                            shape=dict)
            expected[chat_name] = _require_created(
                chat, name=chat_name, width=1080, height=1920, x=chat_x, y=work_y
            )
            verify_layout(cli, baseline, expected, expect_agent=True)
            event(receipt, "created-chat", cycle=cycle, name=chat_name,
                  uuid=chat["id"], device=chat.get("device"),
                  creation_route=chat.get("creation_route", "unreported"))

            start = clock()
            while True:
                remaining = soak_seconds - (clock() - start)
                if remaining <= 0:
                    break
                sleep(min(float(poll_seconds), remaining))
                verify_layout(cli, baseline, expected, expect_agent=True)
            event(receipt, "soak-passed", cycle=cycle, seconds=soak_seconds)

            _remove_exact(cli, chat_name)
            attempted.remove(chat_name)
            expected.pop(chat_name)
            sleep(5)
            verify_layout(cli, baseline, expected, expect_agent=True)
            event(receipt, "selective-remove-passed", cycle=cycle,
                  survivor=work_name, survivor_uuid=work["id"])

            _remove_exact(cli, work_name)
            attempted.remove(work_name)
            verify_layout(cli, baseline, {}, expect_agent=False)
            cli.call("agent", "stop")
            status = cli.call("agent", "status", "--json", shape=dict)
            require(not status.get("running"), "Keeper failed to stop after the cycle.")
            event(receipt, "cycle-passed", cycle=cycle)
            print(f"Cycle {cycle}/{cycles}: PASS", flush=True)
    except BaseException as exc:
        failed = exc
        event(receipt, "exercise-failure", error=str(exc))
    finally:
        cleanup_issues = _cleanup(cli, baseline, attempted, receipt)
        if cleanup_issues:
            event(receipt, "cleanup-incomplete", errors=cleanup_issues)
        if failed is not None:
            raise AcceptanceFailure(
                str(failed) +
                (" | Cleanup problems: " + "; ".join(cleanup_issues) if cleanup_issues else "")
            ) from failed
        if cleanup_issues:
            raise AcceptanceFailure("Final cleanup did not pass: " + "; ".join(cleanup_issues))


def receipt_path(run_id: str) -> Path:
    if os.environ.get("VIRTUMON_STATE_DIR"):
        root = Path(os.environ["VIRTUMON_STATE_DIR"]).expanduser()
    else:
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Virtumon"
    return root / "diagnostics" / ("reliability-" + run_id + ".json")


def save_receipt(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a previous acceptance receipt.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", action="store_true",
                        help="explicitly create/remove temporary virtual monitors")
    parser.add_argument("--cycles", type=int, default=3, help="full lifecycle cycles (1..100)")
    parser.add_argument("--soak-seconds", type=int, default=30,
                        help="both monitors active per cycle (5..3600 seconds)")
    parser.add_argument("--poll-seconds", type=int, default=5,
                        help="verification interval (1..60 seconds)")
    args = parser.parse_args(argv)
    if not 1 <= args.cycles <= 100:
        parser.error("--cycles must be between 1 and 100")
    if not 5 <= args.soak_seconds <= 3600:
        parser.error("--soak-seconds must be between 5 and 3600")
    if not 1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 1 and 60")
    run_id = uuid.uuid4().hex
    receipt = {
        "schema": "virtumon.reliability-acceptance/v1",
        "run_id": run_id, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "exercise": args.exercise,
        "configuration": {"cycles": args.cycles, "soak_seconds": args.soak_seconds,
                          "poll_seconds": args.poll_seconds},
        "events": [], "result": "unknown",
    }
    output = receipt_path(run_id)
    try:
        client = InstalledCli(sys.executable)
        baseline, managed, agent, version = preflight(client, exercise=args.exercise)
        receipt["version"] = version
        receipt["baseline_displays"] = baseline
        event(receipt, "preflight-passed", baseline_count=len(baseline),
              saved_managed_count=len(managed), agent_running=bool(agent.get("running")))
        if args.exercise:
            exercise(client, baseline, cycles=args.cycles,
                     soak_seconds=args.soak_seconds, poll_seconds=args.poll_seconds,
                     sleep=time.sleep, clock=time.monotonic, receipt=receipt)
        else:
            print("PASS: read-only preflight. Use --exercise for the hardware test.")
        receipt["result"] = "pass"
        return_code = 0
    except BaseException as exc:
        receipt["result"] = "fail"
        receipt["error"] = str(exc)
        print("FAIL:", exc, file=sys.stderr)
        return_code = 1
    finally:
        receipt["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        try:
            save_receipt(output, receipt)
            print("Receipt:", output, flush=True)
        except Exception as exc:
            print("Could not save reliability receipt:", exc, file=sys.stderr)
            return_code = 1
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
