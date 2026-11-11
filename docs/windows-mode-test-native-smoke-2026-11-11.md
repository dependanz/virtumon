# Native Windows late-mode-test fix smoke — user report 2026-11-11

**Result: console-confirmed PASS (one two-monitor lifecycle). Receipt audit pending.**

This record covers the first native hardware exercise of the candidate in
[draft PR #5](https://github.com/dependanz/virtumon/pull/5), after the
one-hour duration attempt had failed at second-monitor startup on the
previous build. The previous diagnostic placed that failure at
`native_metadata.phase=after-mode-test`.

## Installed test build

| Field | User-observed value |
| --- | --- |
| Branch | `fix/windows-mode-test-reconciliation` |
| Commit | [`c71f9e403a4a047e5c22dff7fc780a8b5c1c4717`](https://github.com/dependanz/virtumon/commit/c71f9e403a4a047e5c22dff7fc780a8b5c1c4717) |
| Built/installed version | `virtumon 0.2.0a1` from checkout |
| Built wheel SHA-256 | `931892bbc6ac4d27f14d5c6ba863376c2db8ab3ca41d992614066a6dc0347fa2` |
| Platform / Python | Windows 11 AMD64 / Python 3.13.5 |
| Driver | SudoVDA 0.2.1; watchdog 3 seconds |
| Test command | `reliability_windows.py --exercise --cycles 1 --soak-seconds 30 --poll-seconds 5` |
| Multi opt-in | `VIRTUMON_EXPERIMENTAL_MULTI=1` |
| Test-run receipt path | `reliability-25635fe585954f0aa6bcdcc5cedb751d.json` |

The user's preflight `doctor`, `list --managed --json`,
`list --json` and `agent status --json` showed a ready driver,
no managed monitors, one 2560×1600 physical primary at (0,0),
and a stopped agent.

## Reported exercise and independently checked cleanup

The exercise printed:

```text
Cycle 1/1: m2work-9bc0566b2fd9, m2chat-9bc0566b2fd9
Cycle 1/1: PASS
Receipt: ...\reliability-25635fe585954f0aa6bcdcc5cedb751d.json
```

The user captured `$LASTEXITCODE` immediately after the runner;
the subsequent nonzero-exit warning condition did not trigger.

After the run, three separate installed CLI commands reported:

- `list --managed --json` → `[]`
- `list --json` → exactly one physical `DISPLAY1`, 2560×1600
  at (0,0), primary `true`
- `agent status --json` → `running=false`, `managed_count=0`

The stopped agent's message about saved mappings being unverified is
expected and not evidence of any surviving managed record.

## Interpretation and limitations

This is a genuine native one-cycle PASS of the patched code with
independently observed clean final desktop and stopped keeper. The
runner is designed to verify actual Windows display enumeration,
owned UUID continuity, keeper health, selective removal and cleanup
during each successful cycle.

**The uploaded JSON receipt has not yet been inspected**, so do not
assert its event timestamps or a separate receipt-audited result.

**A passing cycle does not prove the new `after-mode-test` pending
handler was invoked.** The previous failure was intermittent. This
could have been a normal successful creation, or an ordinary
earlier-stage reconciliation. Neither of these alternatives contradicts
the successful native regression run.

Before closing this bug, inspect the receipt and gather targeted
non-invasive evidence that the new recovery route works on real
hardware when late recloning occurs. In particular, retain the guard
and avoid any automatic broad whole-desktop rollback. Run a bounded
additional regression before resuming the one-hour duration gate.

PR #3, PR #4 and PR #5 remain draft; no merge/tag/PyPI release
is authorized by this smoke pass.
