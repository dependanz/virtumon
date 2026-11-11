"""M3-B: one-shot native heartbeat outage while keeper process remains alive.

Read-only unless --exercise AND --allow-heartbeat-pause AND the exact
VIRTUMON_M3_B_ALLOW=YES environment opt-in are present. Only one new monitor
is permitted. A keeper-local authenticated, UUID-guarded fault pauses pings
for exactly five seconds, then resumes automatically. Do not use remote-only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
import sys
import time
import uuid

import reliability_windows as m2


def require(condition, message):
    m2.require(condition, message)


def physical_ok(displays, baseline, target):
    physical = [r for r in displays if r.get("device") == baseline[0]["device"]]
    require(len(physical) == 1 and
            m2.physical_signature(physical[0]) == m2.physical_signature(baseline[0]) and
            sum(r.get("primary") is True for r in displays) == 1,
            "M3-B physical primary changed during heartbeat fault.")
    require(len(displays) <= 2 and
            all(r.get("device") in (baseline[0]["device"], target) for r in displays),
            "M3-B unexpected Windows output during heartbeat fault.")


def observe_recovery(cli, control, *, baseline, name, monitor_id, device, pid,
                     clock, sleep, timeout_seconds, receipt):
    deadline = clock() + timeout_seconds
    error_seen = False
    stale_seen = False
    disappeared = False
    while True:
        status = control.call("status", timeout=2)
        rows = control.call("list", timeout=3)
        displays = cli.call("list", "--json", shape=list)
        require(status.get("running") is True and status.get("pid") == pid and
                status.get("managed_count") == 1,
                "M3-B keeper exited/restarted or changed its managed count.")
        require(type(rows) is list and len(rows) == 1 and
                rows[0].get("name") == name and rows[0].get("id") == monitor_id,
                "M3-B ownership name or UUID changed during fault.")
        physical_ok(displays, baseline, device)
        if status.get("heartbeat") == "error" and not error_seen:
            error_seen = True
            m2.event(receipt, "heartbeat-overdue-observed",
                     error=status.get("error"))
        if rows[0].get("status") == "stale" and not stale_seen:
            stale_seen = True
            m2.event(receipt, "stale-ownership-observed", name=name, uuid=monitor_id)
        if len(displays) == 1 and not disappeared:
            disappeared = True
            m2.event(receipt, "virtual-output-disappeared")
        if (status.get("heartbeat") == "ok" and status.get("error") is None and
                stale_seen and disappeared and error_seen):
            require(rows[0].get("status") == "stale",
                    "M3-B stale identity was reactivated after heartbeat recovery.")
            m2.event(receipt, "heartbeat-recovered-identity-stale", pid=pid,
                     name=name, uuid=monitor_id)
            return
        if status.get("heartbeat") == "ok" and stale_seen and rows[0].get("status") == "active":
            raise m2.AcceptanceFailure("M3-B stale target was incorrectly reactivated.")
        if clock() >= deadline:
            raise m2.AcceptanceFailure(
                "M3-B did not observe heartbeat outage, stale ownership, "
                "display disappearance and heartbeat recovery within deadline.")
        sleep(min(0.25, max(0.01, deadline - clock())))


def verify_stale_create_refusal(cli, control, *, name, monitor_id, baseline, device, receipt):
    """Request an impossible unsafe add; refusal must precede any new add."""
    probe = "m3blocked-" + uuid.uuid4().hex[:12]
    try:
        cli.call("create", probe, "--resolution", "800x600", "--refresh",
                 "60", "--right-of", "primary", "--json", shape=dict)
    except m2.AcceptanceFailure as exc:
        require("stale ownership" in str(exc).casefold(),
                "M3-B unexpected creation rejection; expected stale ownership.")
    else:
        raise m2.AcceptanceFailure(
            "M3-B UNSAFE: created a second monitor after stale identity.")
    rows = control.call("list", timeout=3)
    displays = cli.call("list", "--json", shape=list)
    require(len(rows) == 1 and rows[0].get("name") == name and
            rows[0].get("id") == monitor_id and rows[0].get("status") == "stale",
            "M3-B refusal did not preserve exactly one stale owned UUID.")
    physical_ok(displays, baseline, device)
    require(len(displays) == 1, "M3-B blocked creation changed Windows topology.")
    m2.event(receipt, "unsafe-creation-refused", probe=probe)


def exercise(cli, control, baseline, *, clock, sleep, timeout_seconds, receipt):
    require(len(baseline) == 1 and baseline[0].get("primary") is True,
            "M3-B requires one physical primary.")
    name = "m3heartbeat-" + uuid.uuid4().hex[:12]
    known_id = None
    failed = None
    m2.event(receipt, "exercise-start", name=name)
    try:
        created = cli.call("create", name, "--resolution", "1920x1080",
                           "--refresh", "60", "--right-of", "primary",
                           "--json", shape=dict)
        expected = m2._require_created(
            created, name=name, width=1920, height=1080,
            x=baseline[0]["x"] + baseline[0]["width"], y=baseline[0]["y"])
        known_id = expected["id"]
        observed = m2.verify_layout(cli, baseline, {name: expected}, expect_agent=True)
        status = observed["agent"]
        pid = status.get("pid")
        device = created.get("device")
        require(type(pid) is int and pid > 0 and
                type(device) is str and device != baseline[0]["device"],
                "M3-B expected a healthy keeper and independent target.")
        m2.event(receipt, "created-monitor", name=name, uuid=known_id,
                 device=device, pid=pid,
                 creation_route=created.get("creation_route", "unreported"))

        # Validate the live keeper is still the one created for this monitor.
        second = m2.verify_layout(cli, baseline, {name: expected}, expect_agent=True)
        require(second["agent"].get("pid") == pid,
                "M3-B keeper PID changed before the fault.")
        reply = control.call("m3_pause_heartbeat", name=name,
                             uuid=known_id, timeout=3)
        require(type(reply) is dict and reply.get("paused") is True and
                reply.get("duration_seconds") == 5.0,
                "M3-B keeper did not acknowledge the five-second pause.")
        m2.event(receipt, "heartbeat-pause-acknowledged",
                 pid=pid, duration_seconds=5.0)

        observe_recovery(cli, control, baseline=baseline, name=name,
                         monitor_id=known_id, device=device, pid=pid,
                         clock=clock, sleep=sleep, timeout_seconds=timeout_seconds,
                         receipt=receipt)
        verify_stale_create_refusal(cli, control, name=name,
                                    monitor_id=known_id, baseline=baseline,
                                    device=device, receipt=receipt)
    except BaseException as exc:
        failed = exc
        m2.event(receipt, "exercise-failure", error=str(exc))
    finally:
        if known_id is None:
            issues = ["Create did not return a verified monitor UUID; manual inspection required."]
            m2.event(receipt, "cleanup-skipped-unverified-uuid")
        else:
            issues = m2._cleanup(cli, baseline, [name], receipt, {name: known_id})
        if issues:
            m2.event(receipt, "cleanup-incomplete", errors=issues)
        if failed is not None:
            raise m2.AcceptanceFailure(str(failed) +
                (" | Cleanup: " + "; ".join(issues) if issues else "")) from failed
        if issues:
            raise m2.AcceptanceFailure(
                "M3-B cleanup incomplete: " + "; ".join(issues))
    m2.event(receipt, "recovery-passed", name=name, uuid=known_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", action="store_true",
                        help="create one monitor and inject one heartbeat pause")
    parser.add_argument("--allow-heartbeat-pause", action="store_true",
                        help="second explicit acknowledgement of intentional watchdog expiry")
    parser.add_argument("--observation-seconds", type=int, default=30,
                        help="bounded observation window in seconds (10..60)")
    args = parser.parse_args(argv)
    if not 10 <= args.observation_seconds <= 60:
        parser.error("--observation-seconds must be between 10 and 60")
    if args.allow_heartbeat_pause and not args.exercise:
        parser.error("--allow-heartbeat-pause requires --exercise")
    run_id = uuid.uuid4().hex
    receipt = {
        "schema": "virtumon.m3-heartbeat-outage/v1",
        "run_id": run_id, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "exercise": args.exercise, "observation_seconds": args.observation_seconds,
        "events": [], "result": "unknown",
    }
    output = m2.receipt_path(run_id).with_name("m3-heartbeat-" + run_id + ".json")
    code = 1
    try:
        cli = m2.InstalledCli(sys.executable)
        if args.exercise:
            require(args.allow_heartbeat_pause,
                    "Disruptive test requires --allow-heartbeat-pause.")
            require(os.environ.get("VIRTUMON_M3_B_ALLOW") == "YES",
                    "Set VIRTUMON_M3_B_ALLOW=YES before starting the keeper.")
        baseline, managed, status, version = m2.preflight(cli, exercise=args.exercise)
        receipt["version"] = version
        receipt["baseline_displays"] = baseline
        m2.event(receipt, "preflight-passed",
                 baseline_count=len(baseline), saved_managed_count=len(managed),
                 agent_running=bool(status.get("running")))
        if args.exercise:
            from virtumon.agent import AgentClient
            print("M3-B: one-monitor five-second heartbeat outage", flush=True)
            exercise(cli, AgentClient(), baseline, clock=time.monotonic,
                     sleep=time.sleep, timeout_seconds=args.observation_seconds,
                     receipt=receipt)
        else:
            print("PASS: read-only M3-B preflight; no monitors or heartbeat faults.")
        receipt["result"] = "pass"
        code = 0
    except BaseException as exc:
        receipt["result"] = "fail"
        receipt["error"] = str(exc)
        print("FAIL:", exc, file=sys.stderr)
    finally:
        receipt["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        try:
            m2.save_receipt(output, receipt)
            print("Receipt:", output, flush=True)
        except Exception as exc:
            print("M3-B could not write receipt:", exc, file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
