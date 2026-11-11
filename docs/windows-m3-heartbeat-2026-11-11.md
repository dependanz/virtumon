# M3-B: keeper alive, heartbeat interrupted — experimental acceptance

**Status: synthetic CI and native acceptance pending.** M3-A has already
passed a verified keeper-process termination test; M3-B instead holds the
keeper process alive while intentionally suppressing heartbeat pings.

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
