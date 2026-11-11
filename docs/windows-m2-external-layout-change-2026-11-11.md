# Windows M2 external display-layout change during soak — 2026-11-11

**Result: expected test-invariant failure; native displays remained active; automatic cleanup incomplete.**

This report records a Windows Display Settings rearrangement performed by
the user while the one-hour M2 two-monitor soak was running. The test is no
longer valid as an uninterrupted, unchanged-layout duration acceptance run.

## Before the external change

- The previously instrumented test build was installed from commit
  `8989904`, with later documentation-only commits on
  `fix/windows-mode-test-reconciliation`.
- One physical primary `DISPLAY1` at (0,0), 2560×1600, and two
  named virtual monitors were independently active:
  `m2work-c24de29ae83c` (1920×1080 @60 at (2560,0)) and
  `m2chat-c24de29ae83c` (1080×1920 @60 at (4480,0)).
- The agent was running with two managed monitors and a healthy heartbeat.

## User-initiated change and observations

The user moved virtual displays in Windows Display Settings during the
soak. Chrome Remote Desktop showed confusing duplicate numeric Identify
overlays. Read-only `virtumon list` and `list --managed` independently
reported **three active Windows output records with distinct device names**,
not a confirmed native display clone. The virtual output coordinates had
changed to a vertical/offset arrangement while the physical primary remained
at (0,0), 2560×1600. The agent remained running and reported
`heartbeat=ok`, `managed_count=2` and no error.

The test then exited:

```text
FAIL: m2work-c24de29ae83c changed x.
Cleanup problems: selective removal refused before changing the driver:
Managed monitor 'm2work-c24de29ae83c' does not match its saved layout.
```

The script attempted to remove `chat` and `work` *separately*.
`MonitorManager.remove` correctly refused each selective operation
before invoking the driver, because it could not guarantee preserving a
survivor whose geometry had changed. The script consequently reported
`Baseline restoration: Unexpected Windows display count`.

The console reported runner receipt
`reliability-a0f12302cf0b4a6697f6c9fc8cc0bd62.json`.
This receipt has not been uploaded or audited here. Directly after the
failed test, both named monitors remained active with the agent healthy.
**A later user-supplied recovery verification confirms that they are no
longer managed and no longer present in Windows' active display list.**
The exact manual removal command/output was not supplied, so the successful
outcome does not establish that a particular batch-removal operation ran.

## Independently verified final recovery

After the user addressed the incomplete cleanup, the CLI reported:

```text
virtumon agent stop
Stopped the Virtumon agent.

virtumon agent status --json
{"running": false, "managed_count": 0, ...}

virtumon list --managed --json
[]

virtumon list --json
[{"device": "\\\\.\\DISPLAY1", "x": 0, "y": 0,
  "width": 2560, "height": 1600, "primary": true}]
```

**Recovery result: PASS** — no managed virtual outputs, only the physical
primary display in Windows, and the keeper stopped. This confirms a safe
eventual cleanup outcome, **not** that the old automatic cleanup succeeded
or that the new harness batch-cleanup logic has been validated on hardware.
The one-hour test remains invalidated by the external Windows Settings
arrangement change.

## Interpretation

- This is **not** a proven driver crash, watchdog lapse, native CCD clone,
  or failure to maintain virtual monitors over time.
- The one-hour soak's fixed-layout invariant was intentionally violated,
  so its FAIL result is correct and **cannot count as a duration PASS**.
- The *test harness cleanup strategy* had an avoidable gap: it tried
  selective removal twice rather than using the existing exact-name
  two-monitor removal operation after identifying both monitors as
  belonging to that test.
- The duplicate CRD Identify overlays remain a separate observation
  requiring capture/display-specific investigation if reproducible.

## Safety-oriented fix

On separate draft branch `fix/windows-reliability-batch-cleanup`:

1. Retain the original failure as FAIL; do not reinterpret an external
   geometry change as an accepted layout.
2. During failure cleanup only, if exactly the two script-generated
   monitor names remain and **both currently reported UUIDs match the
   UUIDs returned at creation**, call one `remove NAME1 NAME2 --json`
   request. Do not use `remove --all`.
3. Verify both returned names, restore the physical-only baseline and
   stop the keeper only after the live display/managed checks pass.
4. If any name or UUID is unexpected or a create never returned an
   authoritative UUID, refuse batch cleanup, preserve diagnostics,
   and require manual inspection instead.
5. Add driver-free synthetic tests for rearrangement, UUID mismatch
   and partial creation without a confirmed second UUID.

The changed harness is **not yet native-tested**. Native Windows display
control/driver reconciliation code and ownership safety guards remain
unchanged. Keep the fix as a draft until CI and controlled hardware
validation pass. Do not merge/publish as part of this observation.
