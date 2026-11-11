# Native reconciliation-route instrumentation smoke — 2026-11-11

**Result: receipt-audited PASS — 3/3 complete two-monitor lifecycles, including six successful guarded reconciliations.**

This test used the instrumented experimental build from commit
[`8989904`](https://github.com/dependanz/virtumon/commit/8989904ee4b40282d525f12c87da785ddce1f333)
on the same Windows 11 machine. It follows a receipt-audited one-cycle
native PASS for the preceding `c71f9e4` hotplug fix. The uploaded receipt now establishes the specific reconciliation routes
used in each of the three successful cycles. None was `multi-after-mode-test`.

## Environment and evidence

| Property | Reported observation |
| --- | --- |
| Branch | `fix/windows-mode-test-reconciliation` |
| Commit | `8989904ee4b40282d525f12c87da785ddce1f333` |
| Installed package | `virtumon 0.2.0a1`, built from the checkout |
| Wheel SHA-256 | `b7c9c5c4604fd7457105537e6695fb329cbdaafee984a24104cbe078480ab75e` |
| Baseline managed monitors | `[]` |
| Baseline desktop | One physical `DISPLAY1`, 2560 × 1600, at (0,0), primary |
| Baseline keeper | `running=false`, `managed_count=0` |
| Opt-in | `VIRTUMON_EXPERIMENTAL_MULTI=1` |
| Test command | `reliability_windows.py --exercise --cycles 3 --soak-seconds 30 --poll-seconds 5` |
| Result | `Cycle 1/3: PASS`; `Cycle 2/3: PASS`; `Cycle 3/3: PASS` |
| Receipt filename | `reliability-7ad03ecddc5d4e6ab04a02c26f18c378.json` |

The user captured the runner exit code in `$runExit`, then evaluated
`if ($runExit -ne 0)`; no warning was printed.

The runner's implemented PASS criteria include checked Windows/managed
geometry, keeper heartbeat during each soak, selective removal preserving
the prior monitor's UUID, removal of the final monitor and physical
baseline restoration. Therefore, console results strongly support a
successful end-to-end cycle, although the JSON receipt is required
for an event-by-event audit, now completed.

## Structured receipt audit

The user uploaded the complete native receipt for run
`7ad03ecddc5d4e6ab04a02c26f18c378`. Its schema is
`virtumon.reliability-acceptance/v1`, with `exercise=true`,
`result=pass`, 3 cycles, 30-second two-monitor soak, and five-second
poll interval. All **17 events** were present in expected order:
one `preflight-passed`, three each of `created-work`,
`created-chat`, `soak-passed`, `selective-remove-passed`,
and `cycle-passed`, then `cleanup-baseline-restored`. There
were **zero** failure/cleanup-error events, six distinct monitor UUIDs,
and three exact survivor UUID/name matches.

### Native creation routes

| Cycle | `work` route | `chat` route | Outcome |
| --- | --- | --- | --- |
| 1 | `clone-prepare` | `multi-prepare` | PASS |
| 2 | `clone-prepare` | `multi-prepare` | PASS |
| 3 | `clone-prepare` | `multi-prepare` | PASS |

Every successful `work` creation invoked guarded single-display clone
reconciliation at preparation; every successful `chat` creation invoked
guarded multi-display reconciliation at preparation. These are **six
observed native reconciliation operations**, not six unassisted direct
creations. The route is a successful-path diagnostic emitted from
the creation call, not inferred from final screen geometry.

**No `multi-after-mode-test` event was observed.** Consequently, this
receipt does **not** prove that the newly patched late-mode-test
reconciliation path was exercised on real hardware. The five synthetic
late-stage regression checks and earlier native lifecycle acceptance
remain useful but separate evidence.

The start timestamp was `2026-11-11T14:48:41.085782+00:00`;
the end timestamp was `2026-11-11T14:51:07.568522+00:00`.
Total run time: **146.483 seconds**, including all three distinct
30-second soaks, validation, selective removal and cleanup.
The baseline was a single physical `DISPLAY1`, 2560×1600 at (0,0),
primary, with zero managed records and a stopped agent. The terminal
`cleanup-baseline-restored` is the runner's successful check; a
separate post-run read-only CLI listing was **not** supplied with this
particular upload.

## Next gate

The next controlled milestone is a single **one-hour uninterrupted
two-monitor soak** on the instrumented build, contingent on a new
clean read-only preflight. Preserve its structured receipt, including
creation-route labels. If creation again fails, keep its private
`create-*.json` native metadata diagnostic for investigation and do
not immediately retry. The earlier failure and specific
`multi-after-mode-test` route remain unverified as resolved on native
hardware until they are actually observed or separately reproduced.

PR #3, #4, and #5 remain drafts. No merge, tag, or PyPI publication
is authorized by this smoke run alone.
