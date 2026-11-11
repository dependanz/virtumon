# M3-A Windows keeper termination and watchdog recovery — experimental acceptance

**Status: synthetic CI acceptance only; native M3-A run pending.** The
instrumented native M2 fixed-layout 3600-second soak passed, and the system
returned to its sole physical primary. M3 must test *failure recovery*, not
repeat healthy uptime.

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

The simulation uses fakes and monkeypatches; it does not prove the
Windows driver watchdog works under process termination. The native
result must be audited before closing M3-A. Keep related PRs drafts.
