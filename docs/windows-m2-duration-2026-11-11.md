# M2 native one-hour reliability acceptance — 2026-11-11

**Status: CONSOLE-CONFIRMED PASS; structured-receipt audit pending.**

## Scope and provenance

The user ran the native Windows 11 reliability harness after switching to
`fix/windows-reliability-batch-cleanup` at commit `f2392e7`, installing
`virtumon 0.2.0a1` from the checkout, and enabling
`VIRTUMON_EXPERIMENTAL_MULTI=1`.

Installed wheel SHA-256 reported by pip:
`3d1159d10561c582e9ca389140db7efd8d57632c11b4fdbdbdb2a06123fe9a0a`.

Initial native preflight was independently checked by CLI:

- Windows 11 AMD64; Python 3.13.5.
- SudoVDA driver available; protocol 0.2.1; watchdog 3 s.
- `list --managed --json` returned `[]`.
- `list --json` returned physical primary `DISPLAY1`, (0,0),
  2560 × 1600, as the only active display.
- Agent reported `running=false`, `managed_count=0`.

## Native executions

| Gate | Command settings | Created names | Console result | Receipt |
| --- | --- | --- | --- | --- |
| M2 post-fix smoke | `--exercise --cycles 1 --soak-seconds 30 --poll-seconds 5` | `m2work-c89e81c36a43`, `m2chat-c89e81c36a43` | `Cycle 1/1: PASS` | `reliability-c1a471d8153849399b22ef1e9943f26d.json` |
| M2 duration | `--exercise --cycles 1 --soak-seconds 3600 --poll-seconds 5` | `m2work-19e87edc83b6`, `m2chat-19e87edc83b6` | `Cycle 1/1: PASS` | `reliability-e2346442b4464c46b93c27e4c1916ace.json` |

For both runs, the user captured `$LASTEXITCODE` into
`$smokeExit` or `$durationExit`; the respective conditional
error/warning branch did not execute.

After each run, independent CLI commands again reported:

- `list --managed --json` = `[]`;
- `list --json` = only physical primary `DISPLAY1`,
  2560 × 1600 at (0,0);
- `agent status --json`: `running=false`, `managed_count=0`.

The 3600-second configured soak **completed successfully according to
the harness and console**, unlike the earlier invalidated run in which
the user explicitly rearranged Windows displays. No external
rearrangement was reported during this successful run.

## What this validates and what it does not

The console result supports successful creation, sustained
fixed-layout supervision, selective-removal preservation, exact-name
cleanup, and restoration of the physical baseline, under the
runner's implemented acceptance assertions.

The two JSON receipts are **not yet provided or independently
audited**. This record does **not** assert exact observed elapsed
seconds, number of successful polls, creation-route values, individual
UUID continuity, or absence of receipt-level error events without
the structured data. All of those should be checked before labeling
the duration acceptance **receipt-audited**.

A normal PASS does not exercise the new cleanup fallback after a
forced external layout change, nor does it prove the intermittent
`multi-after-mode-test` reconciliation branch executed. It does
not cover M3 crash, watchdog, sleep, reboot, or remote reconnect
recovery behavior.

## Follow-up

1. Receive and audit both local receipt files, especially the
   one-hour receipt's event sequence, two creation routes, and
   timestamps.
2. Keep PR #6 in draft until its new guarded cleanup path is
   explicitly tested on the native machine, independently of
   M2 duration.
3. Begin a separate, bounded M3 failure/recovery test design,
   with physical-primary preservation and confirmed rollback
   as hard safety constraints.
