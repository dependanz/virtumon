# Native reconciliation-route instrumentation smoke — 2026-11-11

**Result: console-confirmed PASS — 3/3 complete two-monitor lifecycles.**

This test used the instrumented experimental build from commit
[`8989904`](https://github.com/dependanz/virtumon/commit/8989904ee4b40282d525f12c87da785ddce1f333)
on the same Windows 11 machine. It follows a receipt-audited one-cycle
native PASS for the preceding `c71f9e4` hotplug fix. This record
does **not** claim any specific reconciliation route was triggered.

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
for an event-by-event audit.

## Outstanding evidence

**Receipt audit pending.** The reported JSON receipt has not yet been
uploaded or examined. Specifically, the new `creation_route` fields
in the `created-work` and `created-chat` events are unknown.

The instrumented routes can distinguish `direct`,
`multi-arrival`, `multi-prepare`, and `multi-after-mode-test`.
Only observing the last route would directly show that the newly
patched late-reconciliation path was exercised on native hardware.
Other successful paths remain valuable lifecycle evidence, but do
not validate the intermittent branch itself.

No separate post-run `list`/agent-status snapshot was provided
for this three-cycle test; prior standalone cleanup checks on
the same patched machine were successful.

**Next:** Upload the local receipt, audit route values and lifecycle
events, then consider the duration gate. Do not declare the
one-hour test passed or release the experimental change until
native long-duration and recovery validation complete.
