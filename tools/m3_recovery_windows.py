"""M3-A: guarded single-monitor keeper-termination recovery acceptance.

DEFAULT MODE IS READ-ONLY. --exercise requires two independent opt-ins and
physical console access. The only forcibly terminated process is the exact
Virtumon keeper created for this run, verified by live status, private endpoint,
executable path and command line. It never kills a driver or changes a physical
monitor. One virtual monitor minimizes topology impact. Receipt records are
write-once. A new keeper may start *only* to remove the exact test UUID.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

import reliability_windows as m2


def guard(condition, message):
    m2.require(condition, message)


def normalized_path(value):
    return os.path.normpath(str(value)).replace("/", "\\").casefold()


def query_process_windows(pid: int):
    """Read process provenance via OS process metadata, not a saved PID alone."""
    command = ("$p = Get-CimInstance Win32_Process -Filter 'ProcessId = " +
               str(pid) + "' -ErrorAction Stop; " +
               "if ($null -eq $p) { throw 'Keeper PID not found' }; " +
               "$p | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
    try:
        output = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            text=True, capture_output=True, encoding="utf-8", errors="replace",
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise m2.AcceptanceFailure("Could not verify keeper OS process identity.") from exc
    guard(output.returncode == 0, "Keeper process metadata unavailable; no termination attempted.")
    try:
        row = json.loads(output.stdout)
    except (TypeError, ValueError) as exc:
        raise m2.AcceptanceFailure("Invalid keeper process metadata.") from exc
    guard(type(row) is dict, "Expected one keeper process record.")
    return row


def validate_keeper_pid(cli, *, state_dir: Path, process_info) -> int:
    """Refuse PID reuse, unrelated Python interpreters and stale endpoints."""
    status = cli.call("agent", "status", "--json", shape=dict)
    pid = status.get("pid")
    guard(status.get("running") is True and status.get("heartbeat") == "ok" and
          status.get("error") is None and type(pid) is int and pid > 0 and
          status.get("managed_count") == 1,
          "Keeper is not healthy with exactly one owned monitor; no termination.")
    try:
        endpoint = json.loads((state_dir / "agent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise m2.AcceptanceFailure("Private keeper endpoint unreadable; no termination.") from exc
    guard(type(endpoint) is dict and endpoint.get("pid") == pid and
          endpoint.get("host") == "127.0.0.1" and endpoint.get("api_version") == 1,
          "Live keeper PID disagrees with its private endpoint; no termination.")
    info = process_info(pid)
    guard(type(info) is dict and info.get("ProcessId") == pid,
          "OS-reported process PID differs; no termination.")
    exe = info.get("ExecutablePath")
    command = info.get("CommandLine")
    guard(type(exe) is str and normalized_path(exe) == normalized_path(sys.executable),
          "Keeper executable is not the selected Python interpreter; no termination.")
    guard(type(command) is str and "virtumon._agent" in command and
          "--state-dir" in command and normalized_path(state_dir) in
          normalized_path(command),
          "OS command line is not the intended Virtumon keeper; no termination.")
    return pid


def observe_watchdog(cli, *, baseline, name, monitor_id, target_device,
                     clock, sleep, timeout_seconds, receipt):
    """Read-only observations; never restart a keeper while waiting."""
    deadline = clock() + timeout_seconds
    saw_stale = False
    while True:
        state = cli.call("agent", "status", "--json", shape=dict)
        managed = cli.call("list", "--managed", "--json", shape=list)
        displays = cli.call("list", "--json", shape=list)
        guard(state.get("running") is False,
              "Keeper unexpectedly restarted during crash observation.")
        guard(len(managed) == 1 and managed[0].get("name") == name and
              managed[0].get("id") == monitor_id and
              managed[0].get("status") == "stale",
              "Saved crash ownership is not exactly the expected stale UUID.")
        if not saw_stale:
            m2.event(receipt, "stale-ownership-observed", name=name, uuid=monitor_id)
            saw_stale = True
        physical = [r for r in displays if r.get("device") == baseline[0]["device"]]
        guard(len(physical) == 1 and
              m2.physical_signature(physical[0]) == m2.physical_signature(baseline[0]) and
              sum(r.get("primary") is True for r in displays) == 1,
              "Physical primary changed during keeper termination.")
        guard(len(displays) <= 2 and
              all(r.get("device") in (baseline[0]["device"], target_device)
                  for r in displays),
              "Unknown desktop output appeared after keeper exit.")
        if len(displays) == 1:
            m2.event(receipt, "watchdog-disappearance-observed",
                     elapsed_window_seconds=timeout_seconds)
            return
        if clock() >= deadline:
            raise m2.AcceptanceFailure(
                "Virtual output still present after bounded watchdog observation.")
        sleep(min(0.5, max(0.01, deadline - clock())))


def exercise(cli, baseline, *, state_dir, process_info, terminate,
             clock, sleep, timeout_seconds, receipt):
    guard(len(baseline) == 1 and baseline[0].get("primary") is True,
          "M3-A requires exactly one physical primary.")
    name = "m3crash-" + uuid.uuid4().hex[:12]
    known_id = None
    failed = None
    m2.event(receipt, "exercise-start", name=name)
    try:
        created = cli.call(
            "create", name, "--resolution", "1920x1080", "--refresh", "60",
            "--right-of", "primary", "--json", shape=dict)
        expected = m2._require_created(
            created, name=name, width=1920, height=1080,
            x=baseline[0]["x"] + baseline[0]["width"], y=baseline[0]["y"])
        known_id = expected["id"]
        observed = m2.verify_layout(cli, baseline, {name: expected}, expect_agent=True)
        status = observed["agent"]
        target_device = created.get("device")
        guard(isinstance(target_device, str) and target_device != baseline[0]["device"],
              "Created virtual display has no distinct GDI device.")
        m2.event(receipt, "created-monitor", name=name, uuid=known_id,
                 device=target_device, creation_route=created.get("creation_route", "unreported"))
        pid = validate_keeper_pid(cli, state_dir=state_dir, process_info=process_info)
        guard(pid == status.get("pid"), "Keeper PID changed while verifying; no termination.")
        m2.event(receipt, "keeper-identity-verified", pid=pid)
        # Final race-reducing check against the current live state immediately
        # before the intentionally disruptive action.
        second = m2.verify_layout(cli, baseline, {name: expected}, expect_agent=True)
        guard(second["agent"].get("pid") == pid,
              "Keeper process changed before termination.")
        guard(validate_keeper_pid(cli, state_dir=state_dir,
                                  process_info=process_info) == pid,
              "Keeper process changed during final authorization.")
        terminate(pid)
        m2.event(receipt, "keeper-termination-issued", pid=pid)
        observe_watchdog(cli, baseline=baseline, name=name,
                         monitor_id=known_id, target_device=target_device,
                         clock=clock, sleep=sleep, timeout_seconds=timeout_seconds,
                         receipt=receipt)
    except BaseException as exc:
        failed = exc
        m2.event(receipt, "exercise-failure", error=str(exc))
    finally:
        # Never attempt removal if a creation failed before returning a UUID.
        if known_id is not None:
            issues = m2._cleanup(cli, baseline, [name], receipt, {name: known_id})
        else:
            issues = ["Create did not return an authoritative UUID; inspect state manually."]
            m2.event(receipt, "cleanup-skipped-unverified-uuid")
        if issues:
            m2.event(receipt, "cleanup-incomplete", errors=issues)
        if failed is not None:
            raise m2.AcceptanceFailure(str(failed) +
                                       (" | Cleanup: " + "; ".join(issues) if issues else "")) from failed
        if issues:
            raise m2.AcceptanceFailure("Recovery cleanup was incomplete: " +
                                       "; ".join(issues))
    m2.event(receipt, "recovery-passed", name=name, uuid=known_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", action="store_true",
                        help="create one virtual monitor, forcibly terminate its verified keeper")
    parser.add_argument("--allow-keeper-termination", action="store_true",
                        help="explicit second acknowledgement for the disruptive test")
    parser.add_argument("--watchdog-wait-seconds", type=int, default=30,
                        help="bounded watchdog disappearance observation (10..60)")
    args = parser.parse_args(argv)
    if not 10 <= args.watchdog_wait_seconds <= 60:
        parser.error("--watchdog-wait-seconds must be between 10 and 60")
    if args.allow_keeper_termination and not args.exercise:
        parser.error("--allow-keeper-termination requires --exercise")
    run_id = uuid.uuid4().hex
    receipt = {
        "schema": "virtumon.m3-keeper-recovery/v1", "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "exercise": args.exercise, "watchdog_wait_seconds": args.watchdog_wait_seconds,
        "events": [], "result": "unknown",
    }
    path = m2.receipt_path(run_id).with_name("m3-recovery-" + run_id + ".json")
    code = 1
    try:
        cli = m2.InstalledCli(sys.executable)
        if args.exercise:
            guard(args.allow_keeper_termination,
                  "Disruptive action requires --allow-keeper-termination.")
            guard(os.environ.get("VIRTUMON_M3_ALLOW_TERMINATION") == "YES",
                  "Set VIRTUMON_M3_ALLOW_TERMINATION=YES for this explicit experiment.")
        baseline, managed, agent, version = m2.preflight(cli, exercise=args.exercise)
        receipt["version"] = version
        receipt["baseline_displays"] = baseline
        m2.event(receipt, "preflight-passed",
                 baseline_count=len(baseline), saved_managed_count=len(managed),
                 agent_running=bool(agent.get("running")))
        if args.exercise:
            guard(sys.platform == "win32",
                  "Keeper termination is only implemented for native Windows.")
            from virtumon.state import state_directory
            print("M3-A: guarded one-monitor keeper termination", flush=True)
            exercise(cli, baseline, state_dir=state_directory(),
                     process_info=query_process_windows,
                     terminate=lambda pid: os.kill(pid, signal.SIGTERM),
                     clock=time.monotonic, sleep=time.sleep,
                     timeout_seconds=args.watchdog_wait_seconds, receipt=receipt)
        else:
            print("PASS: read-only M3 preflight; no keeper was started or terminated.")
        receipt["result"] = "pass"
        code = 0
    except BaseException as exc:
        receipt["result"] = "fail"
        receipt["error"] = str(exc)
        print("FAIL:", exc, file=sys.stderr)
    finally:
        receipt["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        try:
            m2.save_receipt(path, receipt)
            print("Receipt:", path, flush=True)
        except Exception as exc:
            print("Unable to save M3 receipt:", exc, file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
