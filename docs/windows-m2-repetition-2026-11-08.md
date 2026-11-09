# Native Windows M2 reliability repetition acceptance — 2026-11-08

**Result: audited PASS — all 25 cycles and final cleanup confirmed by console and uploaded structured JSON receipt.**

The user supplied the native Windows 11 PowerShell transcript and subsequently
uploaded the exact `reliability_windows.py` JSON receipt. Its event sequence,
UUID continuity, timing and cleanup were audited. This is a successful
repetition test on one machine, **not** a continuous one-hour run or an
OS/driver failure-recovery test.

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

The complete receipt was subsequently uploaded and audited. It records
`schema=virtumon.reliability-acceptance/v1`, run ID
`de8540864fad460ab34976112035082a`, `exercise=true`,
`result=pass`, installed version `virtumon 0.2.0a1`, and the configured
25 cycles, 60-second soak and five-second poll interval.

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

## Structured receipt audit

The uploaded receipt contains **127 events** in the expected order.

| Event phase | Count |
| --- | ---: |
| `preflight-passed` | 1 |
| `created-work` | 25 |
| `created-chat` | 25 |
| `soak-passed` | 25 |
| `selective-remove-passed` | 25 |
| `cycle-passed` | 25 |
| `cleanup-baseline-restored` | 1 |
| Failure or cleanup-error events | **0** |

Each numbered cycle includes exactly five ordered phases: work creation,
chat creation, soak pass, survivor-preserving selective removal, and cycle
pass. Every `selective-remove-passed` survivor name and UUID agrees with
that cycle's `created-work` event. There are **50 distinct monitor UUIDs**
across the 50 creations, and **50 distinct generated names**. No missing
cycle, duplicated UUID, mismatched survivor or failure event was found.

The first recorded event phase confirms one physical primary
`DISPLAY1`, 2560×1600 at (0,0), zero saved managed monitors, and a
stopped agent. The last event is `cleanup-baseline-restored`, followed by
`result=pass`. The receipt does not include an independent post-run CLI
snapshot; this cleanup status is the runner's verified outcome.

The execution started `2026-11-09T01:12:05.443330+00:00` and finished
`2026-11-09T01:44:04.716554+00:00`, a total of **1,919.273 seconds
(31 min 59.3 sec)** including setup, verification and cleanup. The
configured total two-output soak is **1,500 seconds (25 minutes)**,
distributed across the 25 cycles; event timestamps show roughly 60.1–61.1
seconds from each chat creation to its soak-pass event. The receipt
records phase completion, not individual timestamped heartbeat polls.
Heartbeat continuity is inferred from the passing runner checks, rather
than measured directly from independent telemetry.

The run does not constitute a continuous 25-minute two-monitor uptime
test: the monitors were removed and recreated between cycles.

## Audit and remaining release gates

**Receipt audit completed: PASS.** Before the next hardware exercise,
confirm the current host state independently using read-only
`list --managed --json`, `list --json`, and `agent status --json`.
The next proposed stage is **one complete lifecycle with a continuous
3,600-second (one-hour) two-monitor soak**, optionally checking Chrome
Remote Desktop interactions and reconnects during that interval.

The one-hour duration stage, M3 host sleep/reboot and keeper/watchdog
recovery, additional Windows GPUs, Linux and macOS remain unverified.

PR #3 and PR #4 remain drafts; no merge, tag, or PyPI release is authorized
by this single-machine repetition result alone.
