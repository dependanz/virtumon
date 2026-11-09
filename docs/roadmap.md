# Virtumon release roadmap

Proposed milestones, not commitments that every platform works today.
Hardware observations and user-reported acceptance must remain distinct
from simulated CI tests.

## Baseline: November 8, 2026

- PyPI 0.1.0: read-only Windows discovery.
- Unreleased Windows preview 0.2.0a1: SudoVDA monitor create/list/remove.
- One-monitor native acceptance: passed on Windows 11.
- Two-monitor native lifecycle and selective removal: passed on one Windows 11 laptop.
- Chrome Remote Desktop manual acceptance: user reported 5/5 passed, including
  window interaction on both virtual monitors and remote-client reconnect.
- Experimental multi-monitor implementation remains in draft PR #3.
- [M2 three-cycle Windows reliability smoke](windows-m2-smoke-2026-11-08.md):
  PASS (3/3, 30-second soak each), independently corroborated by the
  uploaded structured receipt's event sequence and cleanup-baseline result.
- [M2 25-cycle native repetition acceptance](windows-m2-repetition-2026-11-08.md):
  **audited PASS** (25/25, 60-second soak each), including 50 unique
  owned UUIDs, survivor continuity and restored physical baseline.
- [M2 one-hour duration attempt](windows-m2-duration-startup-failure-2026-11-09.md):
  **failed before soak** during creation of the second virtual monitor.
  Diagnostic and cleanup receipt review pending. This blocks claiming the
  sustained-duration gate and warrants intermittent hotplug investigation.
- Sustained duration, sleep/reboot recovery, alternate GPU stacks, third
  monitor, Linux and macOS: not yet accepted.

## Windows 0.2.0 release gates

| Gate | Scope | Definition of done |
| --- | --- | --- |
| M1 functionality | Native lifecycle and remote interaction | Accepted on target Windows 11 machine |
| M2 reliability | Repetition, duration, heartbeat, cleanup | Native receipts, no identity loss or primary change |
| M3 recovery | Keeper restart/crash, watchdog, sleep/reboot | Safe stale identity and documented recovery behavior |
| M4 compatibility | Mixed DPI, portrait, modes, another Windows GPU | Explicit validated support matrix |
| M5 portable API | Backend protocol, handle and capability types | CLI/SDK share one core; Windows still passes |
| M6 security/packaging | Driver trust, IPC, install/recovery | Review and reproducible fresh install |
| M7 release candidate | 0.2.0rc1, CI, hardware matrix | No critical unresolved regressions |
| M8 publication | Merge, tag, PyPI, installed-wheel test | 0.2.0 released and smoke tested |

## M2 native acceptance runner

The opt-in tools/reliability_windows.py script exercises the **installed**
package, never a mocked Windows driver. It performs read-only checks by
default. In exercise mode it refuses if the keeper is already running,
any managed monitor exists, or there is more than one active baseline display.

Run from PowerShell with the experimental branch installed:

    Set-Location C:\dev\virtumon
    $python = 'C:\Users\Danze\anaconda3\python.exe'
    & $python .\tools\reliability_windows.py

    $env:VIRTUMON_EXPERIMENTAL_MULTI = '1'
    & $python .\tools\reliability_windows.py --exercise --cycles 3 --soak-seconds 30
    if ($LASTEXITCODE -ne 0) { throw 'M2 smoke failed; inspect receipt and cleanup state.' }

Each cycle uses unique owned names, verifies both managed UUIDs against
Windows geometry, polls the keeper heartbeat during the soak, removes the
second monitor, verifies first-monitor preservation after five seconds, then
removes the first and stops the agent. It rechecks the physical desktop,
never uses remove --all, and saves a JSON receipt even on failure.

Suggested staged acceptance:

| Stage | Cycles | Two-output soak per cycle | Purpose |
| --- | ---: | ---: | --- |
| Smoke | 3 | 30 seconds | Basic repeatability, exact UUIDs and physical preservation |
| Repetition | 25 | 60 seconds | Detect intermittent clone/orphan state |
| Duration | 1 | 3600 seconds | Remote interaction and repeated client reconnect |
| Recovery | Separate manual protocol | N/A | Watchdog/agent/sleep events require separate precautions |

The smoke stage has a verified receipt. The 25-cycle repetition stage has
completed successfully and its uploaded JSON receipt has passed the event,
UUID and cleanup audit. Do not start the one-hour duration stage until
the previous stage has a verified native receipt and clean physical baseline.
If the process is forcibly terminated, inspect the state before cleanup;
remove ONLY the test-generated names. Do not delete ownership files.

The script intentionally does not install/uninstall the SudoVDA driver,
import certificates, change display registry settings, reset an unknown
desktop, or exercise host sleep/reboot. A physical-screen fallback is required.

## Future versions (targets)

- 0.3: first supported Linux compositor backend, likely Sway/wlroots,
  with a portable Python API and explicit per-compositor capabilities.
- 0.4: expand Linux desktop compatibility and evaluate experimental macOS.
- 1.0: stable and documented virtual-monitor lifecycle across the specific
  OS/compositor configurations that pass native acceptance.

General Linux or macOS support cannot be inferred from an importable Python
package. An unsupported backend must fail clearly, not create a fake monitor.

See architecture.md for the planned portable contract.
