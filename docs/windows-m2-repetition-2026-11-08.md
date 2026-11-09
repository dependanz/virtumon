# Native Windows M2 reliability repetition acceptance — 2026-11-08

**Result: 25/25 cycles PASS, confirmed by user-supplied PowerShell console output.**

The `reliability_windows.py` runner's detailed JSON receipt has **not yet been
uploaded or independently audited**. This document records the exact scope
of the supplied transcript and does not claim the longer duration or failure
recovery gates are complete.

## Configuration and preflight

| Item | Observed |
| --- | --- |
| Repository | `dependanz/virtumon` |
| Branch | `feature/windows-m2-reliability` |
| Installed code baseline | [`bcc591e`](https://github.com/dependanz/virtumon/commit/bcc591e8bc7f649de2a19bf3fea632fdd1c2b6d6), a documentation-only update from the previously CI-tested harness |
| Operating system | Windows 11 (same machine as prior native acceptance) |
| Interpreter | `C:\Users\Danze\anaconda3\python.exe` |
| Exercise opt-in | `VIRTUMON_EXPERIMENTAL_MULTI=1` |
| CLI command | `reliability_windows.py --exercise --cycles 25 --soak-seconds 60 --poll-seconds 5` |
| Total configured two-output soak | **1,500 seconds (25 minutes)** |
| CLI baseline | Managed list `[]`; one 2560×1600 physical primary `DISPLAY1`; agent `running=false` and `managed_count=0` |

The user pulled `bcc591e` using `git pull --ff-only`; no source-code change
was included in that pull. The previously installed package and checked-out
runner were used in the test.

## Observed console outcome

The runner printed a unique `m2work-`/`m2chat-` pair for each of cycles 1
through 25 and printed **`Cycle N/25: PASS` for every N = 1,…,25**.
The final nonzero-`$LASTEXITCODE` PowerShell guard did not throw.

The reported local evidence path is:

```text
C:\Users\Danze\AppData\Local\Virtumon\diagnostics\reliability-de8540864fad460ab34976112035082a.json
```

This filename establishes the saved receipt location, **not** the uninspected
receipt's `result`, timestamps or internal event counts.

## Interpretation

The tested runner prints a cycle PASS after both monitors have been
created and verified against independent Windows display enumeration, the
keeper heartbeat has been checked throughout each configured two-output soak,
the `chat` output has been selectively removed, the `work` survivor has
been verified after an additional five seconds, and the final exact-name
removal plus physical baseline check and agent shutdown have succeeded.

On that basis, the console confirms:

- **25 successful complete two-monitor lifecycle cycles**
- **50 virtual-monitor creations and 50 exact-name removals**
- **25 survivor-preservation checks**
- **25 minutes of configured two-monitor soak**, split across 25 sessions
- **No reported cycle failure or PowerShell nonzero-exit exception**

The run does not constitute a continuous 25-minute two-monitor uptime
test: the monitors are removed and recreated between cycles.

## Audit and remaining release gates

**Receipt review pending:** verify `schema`, `exercise=true`,
`result=pass`, configured 25 cycles/60 seconds/5-second poll interval,
all 25 sets of create/soak/selective-removal/cycle-pass events, survivor
UUID continuity, lack of failure/cleanup-error events, and terminal
`cleanup-baseline-restored`. Request an independent read-only
`list --managed --json`, `list --json`, and `agent status --json`
before the next hardware test.

If the JSON audit and clean baseline both pass, proceed to a **one-hour
sustained two-monitor run**, with optional Chrome Remote Desktop interaction
and reconnect checks. M3 host sleep/reboot, keeper/watchdog fault recovery,
additional Windows GPUs, Linux and macOS remain unverified.

PR #3 and PR #4 remain drafts; no merge, tag, or PyPI release is authorized
by this console result alone.
