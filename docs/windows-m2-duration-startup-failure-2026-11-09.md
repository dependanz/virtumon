# M2 sustained-duration attempt — second-display startup failure (2026-11-09)

**Result: FAIL before the one-hour soak began.**

The user ran the installed experimental Virtumon preview through
`tools/reliability_windows.py --exercise --cycles 1 --soak-seconds 3600
--poll-seconds 5`, after updating the reliability branch to
[`e0e9eee`](https://github.com/dependanz/virtumon/commit/e0e9eeefb546ae39e7da4cd702cb8e0d8446d8fb)
and confirming an empty managed list, one 2560×1600 physical primary,
and a stopped keeper. Read-only preflight passed.

## Native failure

The script printed:

```text
Cycle 1/1: m2work-eea096ebbbe1, m2chat-eea096ebbbe1
FAIL: virtumon create m2chat-eea096ebbbe1 --resolution 1080x1920
  --refresh 60 --right-of m2work-eea096ebbbe1 --json failed (1):
  Could not create 'm2chat-eea096ebbbe1':
  Timed out waiting for consistent Windows display metadata.
  Latest error: Windows has not reported one attached GDI source
  for \\.\DISPLAY28 (matches=1; flags=0x0).
  No new virtumon was retained.
```

The console reported these **local** diagnostic paths:

- Creation failure, captured before the new UUID's cleanup:
  `create-88e97d7f-ac38-4620-a952-cbc5f6fddc89.json`
- Overall reliability runner receipt:
  `reliability-f5aebfc2bf064877a3468809bab35db1.json`

Both are under the user's local Virtumon `diagnostics` directory.
Neither JSON file was attached at the time of this record; do not assume
their contents. No physical-desktop/keeper state readback was shown
after the failure, so **full cleanup and survivor state are unverified**.

The accompanying PowerShell `$LASTEXITCODE` condition raised the
intended test-failed exception. No one-hour soak took place.

## Preliminary technical interpretation

The new SudoVDA hotplug appears to have displaced the existing
`work` target, leaving the GDI view of `DISPLAY28` detached.
This symptom occurred in earlier, successfully reconciled
two-monitor tests; its presence alone is **not** sufficient to explain
why the guarded multi-output restore failed in this attempt.

`windows_multi._recognize()` returns `None` when a safety
precondition fails, without currently recording which test rejected
the observation. The caller retries discovery until its deadline,
so the timeout text identifies the inconsistent GDI view but does not
prove the precise rejection cause. The pre-cleanup CCD/GDI diagnostic
is needed to distinguish a changed topology from an overly strict
recognition condition.

Do not weaken physical-display invariants or retry native
`SetDisplayConfig` on this evidence alone.

## Next evidence and safety steps

1. Run read-only `virtumon list --managed --json`, `virtumon list --json`,
   and `virtumon agent status --json` from the same installed interpreter.
   Do not blindly remove all outputs, reset displays, or delete the journal.
2. Upload both local JSON files for a private comparison with the
   previous successful topology. Hardware identifiers from the native
   diagnostic should not be committed publicly without review.
3. Only after identifying a safe, reproducible deviation should
   code or diagnostic instrumentation change, with synthetic
   regression tests and an explicit hardware acceptance rerun.

The previously audited 25/25 repetition test **remains valid**; this
new failed attempt demonstrates that a fresh second-monitor hotplug
can still fail. PR #3/PR #4 remain drafts and no release is authorized.
