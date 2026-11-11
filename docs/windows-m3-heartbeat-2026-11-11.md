# M3-B: keeper alive, heartbeat interrupted — experimental acceptance

**Status: synthetic CI PASS; native test CONSOLE-CONFIRMED PASS, structured-receipt audit pending.** M3-A has already passed a receipt-audited keeper-process termination test. M3-B holds the keeper alive while intentionally suppressing heartbeat pings.

## Safety constraints

- The production SudoVDA backend and driver are unchanged.
- A one-shot, five-second pause is hidden inside the authenticated local
  agent and **disabled unless `VIRTUMON_M3_B_ALLOW=YES` is in the
  keeper's environment before startup**. The local native runner also
  requires `--exercise --allow-heartbeat-pause`; default is read-only.
- The handler accepts only the exact name/UUID of the *sole* healthy
  managed monitor. It refuses any other managed count, a stale record,
  a watchdog timeout other than three seconds, and extra parameters.
  It cannot extend the pause or arm the same keeper twice.
- The pause begins in the keeper heartbeat thread, not the IPC thread,
  and self-expires after five seconds without a timer or caller action.
  The SudoVDA watchdog is global to that adapter. **Do not run with
  Apollo or another SudoVDA controller active.**
- A physical screen fallback is mandatory; never run over remote-only
  access. This may disconnect remote sessions and remove the test
  virtual monitor from Windows during the intended watchdog expiry.
- Exactly one new 1920×1080 virtual monitor is created on the sole
  physical-primary baseline. No existing monitors or keeper are allowed.
- After fault injection, verify the **same keeper PID remains running**,
  an overdue/error heartbeat is observed, ownership becomes `stale`,
  the virtual display disappears from native Windows enumeration, and
  the heartbeat subsequently returns to `ok` without reactivating
  the stale monitor.
- Attempt one additional uniquely named monitor creation only to verify
  that production rejects **stale ownership before a driver add**.
  The test fails if this creates a monitor or changes Windows topology.
- Cleanup removes only the saved test name/UUID, verifies physical-only
  baseline and stops the keeper. On uncertainty or a failed cleanup,
  preserve the receipt and inspect before repeating. No driver reset,
  certificate change, global clear, manual registry change or reboot.

## Native procedure (only after CI passes)

In the native Windows 11 checkout, install the M3-B branch and run:

```powershell
$python = 'C:\Users\Danze\anaconda3\python.exe'
& $python .\tools\m3_heartbeat_windows.py
```

This is **read-only**. Confirm an empty managed journal, one physical
primary and stopped keeper. Only with a physical-screen fallback:

```powershell
$env:VIRTUMON_EXPERIMENTAL_MULTI = '1'
$env:VIRTUMON_M3_B_ALLOW = 'YES'
& $python .\tools\m3_heartbeat_windows.py --exercise --allow-heartbeat-pause --observation-seconds 30
$testExit = $LASTEXITCODE
& $python -m virtumon list --managed --json
& $python -m virtumon list --json
& $python -m virtumon agent status --json
```

**Expected native receipt**:
`preflight-passed`, `exercise-start`, `created-monitor`,
`heartbeat-pause-acknowledged`, `heartbeat-overdue-observed`,
`stale-ownership-observed`, `virtual-output-disappeared`,
`heartbeat-recovered-identity-stale`,
`unsafe-creation-refused`, `cleanup-removed`,
`cleanup-baseline-restored`, `recovery-passed`, with `result=pass`.

The order among overdue, stale, and disappearance may vary across OS
polls. Individual heartbeat measurements are not persisted; the
events capture first observed transition timestamps. The exact
kernel watchdog tick is not inferred.

## Acceptance limits

The M3-B scenario covers a scheduled five-second interruption of the
heartbeat thread in a still-running keeper. It does not establish
behavior under a deadlocked process, arbitrary I/O stalls, GPU
driver replacement, host reboot or sleep/resume. M3-C/D/E and
the intermittent `multi-after-mode-test` native route are separate.
The fault hook should be removed or strictly excluded from
any release-ready production build.

## Native Windows console observations — 2026-11-11

The user checked out `feature/windows-m3-heartbeat-outage` at
`1be811a9c40f8622cd08abb48234173519cf8052`, built and installed
`virtumon 0.2.0a1` using Python 3.13.5 on Windows 11. Reported built
wheel SHA-256:
`2aa91b89f5a8ff4c9b44de25b371dc82f1677294db4a54c691cfb1e70cf77790`.

- Read-only preflight printed **PASS**. Receipt filename:
  `m3-heartbeat-0a7f3b1aadc4460fad5f9b4a5120c38a.json`.
- Independent preflight CLI checks reported `[]` managed monitors,
  only physical primary `DISPLAY1` at (0,0), 2560×1600, and the
  keeper stopped.
- The user explicitly set `VIRTUMON_EXPERIMENTAL_MULTI=1` and
  `VIRTUMON_M3_B_ALLOW=YES`, then invoked
  `m3_heartbeat_windows.py --exercise --allow-heartbeat-pause --observation-seconds 30`.
- The runner printed its M3-B scenario line and a receipt filename:
  `m3-heartbeat-00fa24eef2b640a898b477a38251071b.json`.
  The exit code was captured in `$m3bExit`. The conditional failure
  warning did **not** fire, so the native run is console-confirmed PASS.
- Independent post-run CLI checks reported `list --managed = []`,
  `list --json` with only physical primary `DISPLAY1` (0,0),
  2560×1600, and agent `running=false`, `managed_count=0`.

The native runner's success indicates its assertions for same-PID
heartbeat recovery, overdue heartbeat observation, persistent stale
ownership, virtual-output disappearance, unsafe additional creation
refusal and cleanup completed. **Neither structured receipt has yet
been uploaded or independently audited**. Therefore exact event
sequence and timestamps, original UUID, PID, measured observation
intervals and per-event fields remain unverified from receipts.

**Do not mark M3-B receipt-audited or release-ready until both JSON
receipts are supplied and reviewed.** This test does not validate M3-C
reboot, M3-D sleep/resume or M3-E two-monitor crash recovery. The
fault-injection hook must be removed or excluded from release builds.
