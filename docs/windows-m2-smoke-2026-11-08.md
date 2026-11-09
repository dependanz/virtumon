# Windows M2 reliability smoke: 2026-11-08

**Result: console-confirmed PASS — 3/3 complete two-monitor cycles.**

This evidence was supplied by the user as PowerShell output from the Windows 11
development machine. The JSON receipt itself was **not uploaded or inspected**
at the time of this record. Distinguish this console-level acceptance from
an audited full receipt or a longer-duration reliability guarantee.

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
evidence beyond the initial single two-monitor acceptance. It does not by
itself prove the absence of rare long-running failures, nor does it provide
per-poll timestamps or exact monitor identities without inspecting the
generated JSON receipt.

## Next gate

**Receipt review pending:** inspect the three-cycle JSON receipt for
`result=pass`, cycle count, matching `created-work`, `created-chat`,
`soak-passed`, `selective-remove-passed`, `cycle-passed` events,
and `cleanup-baseline-restored`, with no failure/cleanup-error event.
Compare its recorded physical baseline with the post-run read-only listing.

After receipt review and confirmation of a clean machine, the next proposed
stage is 25 cycles with a 60-second soak per cycle. The full one-hour
duration test and M3 sleep/keeper/watchdog recovery remain unverified.

This result changes neither the published `0.1.0` package nor the draft
release status of PR #3 and PR #4.
