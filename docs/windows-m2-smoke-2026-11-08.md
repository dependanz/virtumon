# Windows M2 reliability smoke: 2026-11-08

**Result: PASS — 3/3 complete two-monitor cycles, confirmed by console and structured receipt.**

The user supplied PowerShell output from the Windows 11 development machine
and subsequently uploaded the exact exercise's JSON receipt for review.
This provides console and structured receipt confirmation, **not** an
independent kernel/driver trace or long-duration reliability guarantee.

## Build and preparation

| Field | Observed |
| --- | --- |
| Repository branch | `feature/windows-m2-reliability` |
| Commit | [`4e63eb41ed5001b389b626df3d89fc74c0dad6ef`](https://github.com/dependanz/virtumon/commit/4e63eb41ed5001b389b626df3d89fc74c0dad6ef) |
| Wheel version | `virtumon 0.2.0a1` installed from checkout |
| Wheel SHA-256 | `d7295a74499349698aed6424c4d72f40523b6fb8e6429f9b5744f04b697d85bf` |
| Interpreter | Python executable in user's Anaconda base environment |
| Preflight | `reliability_windows.py` returned PASS (read-only) |
| Exercise opt-in | `VIRTUMON_EXPERIMENTAL_MULTI=1`; `--exercise` |
| Cycles | 3 |
| Configured soak | 30 seconds per two-monitor lifecycle, 90 seconds total |
| Poll interval | 5 seconds |
| Command failure check | PowerShell `$LASTEXITCODE` did not trigger the nonzero-exit exception |

## Observed console

```text
Cycle 1/3: m2work-4a7d4d317a27, m2chat-4a7d4d317a27
Cycle 1/3: PASS
Cycle 2/3: m2work-f344dee9d9f6, m2chat-f344dee9d9f6
Cycle 2/3: PASS
Cycle 3/3: m2work-eea5ba120232, m2chat-eea5ba120232
Cycle 3/3: PASS
```

The script reported two local JSON receipt paths:

- Read-only preflight: `reliability-0e5af2c1e1d641bfab4a690d71539449.json`
- Three-cycle hardware exercise: `reliability-d5fc8bdea83f4a4297d110ce9ea9a3bd.json`

## Interpretation

Based on the pinned implementation of `tools/reliability_windows.py`,
a cycle prints PASS only after both uniquely named monitors were created,
the Windows output enumeration matched the managed geometry/UUID records,
the keeper heartbeat remained healthy during the configured two-monitor soak,
the `chat` output was removed, the surviving `work` output remained valid
after five more seconds, and `work` was removed, the original physical
baseline rechecked, and the agent stopped.

All three cycles reporting PASS is meaningful repeated native-hardware
evidence beyond the initial single two-monitor acceptance. The separately
uploaded JSON receipt confirms the following event counts and UUID continuity:

| Phase | Count |
| --- | ---: |
| `preflight-passed` | 1 |
| `created-work` | 3 |
| `created-chat` | 3 |
| `soak-passed` | 3 |
| `selective-remove-passed` | 3 |
| `cycle-passed` | 3 |
| `cleanup-baseline-restored` | 1 |
| Failure or cleanup-error events | 0 |

The receipt uses schema `virtumon.reliability-acceptance/v1`, run ID
`d5fc8bdea83f4a4297d110ce9ea9a3bd`, `exercise=true`, `result=pass`,
version `virtumon 0.2.0a1`, `cycles=3`, `soak_seconds=30` and
`poll_seconds=5`. Each of the three selective-remove events retains the
exact UUID created for its cycle's `work` monitor, and each `chat` UUID
differs from its `work` UUID. The recorded initial state has one physical
primary `DISPLAY1`, 2560 x 1600 at (0,0), zero saved managed monitors,
and a stopped keeper.

The recorded run began at `2026-11-09T00:13:34.668110+00:00` and ended at
`2026-11-09T00:15:56.915143+00:00` (about **142.25 seconds overall**,
including preflight, creation, checks, selective removal and cleanup). The
configured two-monitor soak totals **90 seconds** across the three cycles.
The receipt records successful terminal events, not individual timestamped
heartbeat polls; those checks are implemented by the pinned runner and
their success is evidenced indirectly by the PASS events.

This does not prove the absence of rare long-running failures or establish
additional compatibility beyond the tested machine.

## Next gate

**Receipt review completed: PASS.** All required phases and the final
`cleanup-baseline-restored` event are present, with no recorded failure.
An independently captured post-run `virtumon list --json` was not included
with this receipt; the runner itself checked the physical desktop.

The next proposed stage is **25 cycles with a 60-second two-monitor soak per
cycle**, contingent on a fresh local read-only preflight and the machine
starting with zero managed displays and the agent stopped. The full one-hour
duration test and M3 sleep/keeper/watchdog recovery remain unverified.

This result changes neither the published `0.1.0` package nor the draft
release status of PR #3 and PR #4.
