# M3-A Windows keeper termination and watchdog recovery — experimental acceptance

**Status: M3-A NATIVE RECEIPT-AUDITED PASS (2026-11-11).** M2's fixed-layout
3600-second soak also passed independently. M3-A demonstrates bounded recovery
from an intentionally terminated keeper on the tested Windows 11 laptop; it
does not establish M3-B/C/D/E.

## Contract to verify

The first bounded scenario deliberately terminates exactly the detached
Virtumon keeper, **not** the driver or Windows desktop session. There is
only **one temporary virtual monitor**, because adding a second creates
additional selective-removal risk without improving the first crash test.

1. Read-only preflight demands one physical primary, empty managed list,
   stopped keeper and SudoVDA readiness. Exercise requires
   `VIRTUMON_EXPERIMENTAL_MULTI=1` (inherited driver setup),
   `VIRTUMON_M3_ALLOW_TERMINATION=YES`, `--exercise`, and
   `--allow-keeper-termination`. Having a visible physical screen is a
   **human prerequisite** not machine-detectable.
2. Create a uniquely named 1920×1080 virtual monitor to the right of
   the physical primary; capture its authoritative UUID. Validate geometry,
   identity and healthy keeper heartbeat twice.
3. Verify the live agent PID agrees with its private endpoint and Windows
   process metadata. Require the selected Python interpreter, a
   `virtumon._agent --state-dir` command line and current state directory.
   If metadata are unavailable, **refuse termination**.
4. Send SIGTERM to exactly the verified keeper PID (Windows maps this to
   process termination); do not kill Python processes by name or PID read
   from an unverified state file.
5. Without automatically starting an agent, observe the saved UUID as
   **stale**, the agent no longer running, and the driver's virtual
   output disappearing from native Windows enumeration within a bounded
   interval (default 30 seconds). Preserve the unchanged physical primary
   throughout. This demonstrates watchdog-driven output disappearance
   *by observation*; the runner does not inspect the kernel watchdog timer.
6. After that observation, remove **only the exact test-generated UUID
   by its name**, allowing a new keeper to start for stale journal cleanup.
   Verify physical-only baseline and stop that keeper.
7. Keep write-once structured `m3-recovery-*.json` receipts, even on
   failures. No monitor is removed if create never returned a verifiable
   UUID. A failed output disappearance or unexpected identity is a FAIL,
   never a PASS with automatic desktop repairs.

## Native test commands

From the physical laptop display, after fetching and installing this
draft branch, first run the **read-only default**:

```powershell
$python = 'C:\Users\Danze\anaconda3\python.exe'
& $python .\tools\m3_recovery_windows.py
```

Only when that passes and a physical-local fallback is available,
the separate opt-in crash run is:

```powershell
$env:VIRTUMON_EXPERIMENTAL_MULTI = '1'
$env:VIRTUMON_M3_ALLOW_TERMINATION = 'YES'
& $python .\tools\m3_recovery_windows.py --exercise --allow-keeper-termination
$exitCode = $LASTEXITCODE
& $python -m virtumon list --managed --json
& $python -m virtumon list --json
& $python -m virtumon agent status --json
```

Do not invoke the crash run over a remote-only connection or while any
display other than the physical baseline is active. Windows Display
Settings, driver installation/uninstallation, system sleep/reboot, and
manual `taskkill` are deliberately outside this scenario.

## Planned M3 matrix

| Gate | Scope | Acceptance |
| --- | --- | --- |
| M3-A | Single keeper crash + watchdog + stale journal cleanup | Exact-PID crash; stale ownership; output disappears; physical unaffected; exact UUID cleanup |
| M3-B | Keeper heartbeat outage and generation invalidation | Known failures fail closed; no recycled target is promoted to active |
| M3-C | Reboot with saved ownership | No blind reattachment/recreation; safe stale observation and cleanup |
| M3-D | Sleep/resume | Driver/Windows topology recovery observed; no automatic primary mutation |
| M3-E | Two-monitor crash and remote reconnect | Only after one-monitor M3-A safety acceptance |

## Native receipt audit — 2026-11-11

The user checked out commit `a836b10` on
`feature/windows-m3-keeper-recovery` and installed `virtumon 0.2.0a1`
on their Windows 11 laptop. Reported pip wheel SHA-256:
`0c961d7bdcb28811256323f0e1b7af5644a2efbf8e158d4b4c9bdea8146aaa6d`.

**Read-only preflight receipt:** `m3-recovery-1ffce814d7084f3f80b4a27a05757e12.json`.
Schema `virtumon.m3-keeper-recovery/v1`; `exercise=false`, `result=pass`.
Preflight reported one baseline display, zero saved managed monitors, agent
stopped; only physical primary `DISPLAY1`, 2560×1600 at (0,0).
Recorded elapsed time: **4.282 seconds**.

**Disruptive M3-A receipt:** `m3-recovery-268331499afa4ad9bc554a428fc1a7fd.json`.
Schema `virtumon.m3-keeper-recovery/v1`; `exercise=true`,
`watchdog_wait_seconds=30`, `result=pass`. Ten correctly ordered events,
all verified against the uploaded JSON; no failure or cleanup-error events.

| Event | Time (UTC) | Verified details |
| --- | --- | --- |
| `preflight-passed` | 18:50:03.268651 | One baseline display, no saved monitor, agent stopped |
| `exercise-start` | 18:50:03.275837 | Unique name `m3crash-c4822702ec06` |
| `created-monitor` | 18:50:09.817173 | UUID `c4083c65-c67b-4b44-b772-ea185e27c31c`, `DISPLAY28`, `clone-prepare` |
| `keeper-identity-verified` | 18:50:13.368849 | Process PID `57000` |
| `keeper-termination-issued` | 18:50:18.885878 | Same PID `57000` |
| `stale-ownership-observed` | 18:50:25.800385 | Same UUID as creation |
| `watchdog-disappearance-observed` | 18:50:25.800442 | Windows enumerated only physical baseline |
| `cleanup-removed` | 18:50:35.741722 | Exact generated name removed |
| `cleanup-baseline-restored` | 18:50:40.894017 | Physical-only verification after cleanup |
| `recovery-passed` | 18:50:40.894049 | Same monitor UUID as creation |

The full disruptive run lasted **42.292 seconds** from
`18:49:58.602415` to `18:50:40.894070` UTC. The termination-issued
to disappearance-observed receipt timestamps differ by **6.915 seconds**.
This is the *observation interval including CLI/inspection overhead*, not
a direct measurement of driver watchdog expiration. The receipt's
`elapsed_window_seconds=30` is the configured *upper bound*, not a
measured elapsed time or a claim that disappearance took 30 seconds.
No kernel watchdog counters were read during this test.

Crucially, `created-monitor`, `stale-ownership-observed`, and
`recovery-passed` all refer to the exact same UUID. The two PID events
agree. The complete event order supports the planned safety assertions:
the old ownership was not silently presented as active, the physical
primary was preserved, and the stale record was removed by exact name.
Separate user CLI commands **after the run** corroborated
`list --managed = []`, only physical `DISPLAY1` (2560×1600, primary,
(0,0)) in `list --json`, and agent `running=false`,
`managed_count=0`.

**Acceptance conclusion:** M3-A single-monitor unexpected-keeper-exit,
stale ownership and bounded virtual-output disappearance/recovery
**PASS on the tested laptop**, supported by native structured receipts.
The disappearance is consistent with watchdog behavior; the precise
kernel timeout mechanism was not independently instrumented.
This does **not** exercise a simulated heartbeat failure while the keeper
stays alive (M3-B), reboot (M3-C), sleep/resume (M3-D), or two-monitor
crash and remote reconnect (M3-E). It also does not prove the
`multi-after-mode-test` reconciliation branch has run in native Windows.

Keep PR #7 draft while evaluating the remaining M3 gates and review.
The original synthetic tests alone do not establish the native result.
