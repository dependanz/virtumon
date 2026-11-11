# M2 native one-hour reliability acceptance — 2026-11-11

**Status: RECEIPT-AUDITED PASS — 30-second smoke and 3,600-second two-monitor duration gate.**

## Test environment

The user ran the native Windows 11 reliability harness from branch
`fix/windows-reliability-batch-cleanup` at commit `f2392e7`;
`virtumon 0.2.0a1` was installed locally from that checkout,
with `VIRTUMON_EXPERIMENTAL_MULTI=1`.

Wheel SHA-256 reported by the native pip build:
`3d1159d10561c582e9ca389140db7efd8d57632c11b4fdbdbdb2a06123fe9a0a`.

Preflight: Windows 11 AMD64, Python 3.13.5, SudoVDA protocol 0.2.1,
3-second watchdog; no managed monitors; agent stopped; only physical
`DISPLAY1`, 2560×1600, primary, at (0,0).

## Native runs and independent receipt audit

Both `virtumon.reliability-acceptance/v1` structured receipts were
uploaded and independently checked against the native console output.
Each receipt includes exactly seven ordered events:
`preflight-passed`, `created-work`, `created-chat`,
`soak-passed`, `selective-remove-passed`, `cycle-passed`,
`cleanup-baseline-restored`. Both have `result=pass`; neither
includes an error, failure or incomplete-cleanup event.

| Check | 30-second smoke | One-hour sustained soak |
| --- | --- | --- |
| Command | `--exercise --cycles 1 --soak-seconds 30 --poll-seconds 5` | `--exercise --cycles 1 --soak-seconds 3600 --poll-seconds 5` |
| Receipt ID | `c1a471d8153849399b22ef1e9943f26d` | `e2346442b4464c46b93c27e4c1916ace` |
| `result` | `pass` | `pass` |
| Start (UTC) | 2026-11-11 17:10:22.244841 | 2026-11-11 17:11:28.153466 |
| End (UTC) | 2026-11-11 17:11:23.850312 | 2026-11-11 18:11:55.105982 |
| Total elapsed | **61.605 s** | **3,626.953 s** |
| `created-chat` → `soak-passed` | **31.642 s** | **3,601.208 s** |
| `work` route | `clone-prepare` | `clone-prepare` |
| `chat` route | `multi-prepare` | `multi-prepare` |
| Selective removal | `selective-remove-passed` | `selective-remove-passed` |
| Survivor UUID preservation | Verified exact match | Verified exact match |
| Cleanup | `cleanup-baseline-restored` | `cleanup-baseline-restored` |
| Error events | 0 | 0 |

**Smoke creation identities:**
- `work`: `m2work-c89e81c36a43`, UUID
  `88177bf1-8edf-49b0-b551-5c137bc60604`, `DISPLAY28`.
- `chat`: `m2chat-c89e81c36a43`, UUID
  `207bc8d2-6151-49d4-acc8-ae95c882ad8a`, `DISPLAY29`.
- The selective-removal survivor entry exactly matches the original
  `work` name and UUID.

**One-hour creation identities:**
- `work`: `m2work-19e87edc83b6`, UUID
  `be75f8fa-0a30-4b68-81c0-59e7afc6c9d8`, `DISPLAY28`.
- `chat`: `m2chat-19e87edc83b6`, UUID
  `60aaf6ec-3910-40eb-8664-3bcb96ff04af`, `DISPLAY29`.
- `soak-passed` was recorded at **18:11:42.241762 UTC**,
  followed by `selective-remove-passed` at **18:11:49.724152 UTC**,
  `cycle-passed` at **18:11:52.782361 UTC**, and
  `cleanup-baseline-restored` at **18:11:55.105927 UTC**.
- The selective-removal survivor name and UUID exactly match the
  `work` creation identity.

All four created UUIDs are distinct. In both receipts the baseline
physical monitor is `DISPLAY1`, 2560×1600 at (0,0), primary, and
`preflight-passed` confirms a single original display, zero saved
monitors, and a stopped agent. Independent post-run CLI output after
**each** test showed an empty managed list, only the physical primary
display and a stopped agent.

## Interpretation and coverage boundaries

**M2 fixed-layout one-hour duration acceptance: PASS**, backed by the
native harness result, chronological structured receipt, exact matched
survivor identity, and separate post-run cleanup verification. The
harness was configured to check live display geometry and keeper health
every five seconds during the 3,600-second soak; the receipt records
the successful `soak-passed` gate but **does not log individual poll
samples or heartbeat measurements**, so an exact number of completed
polls is not independently available from these receipts.

Both executions took the preparation-stage guarded reconciliation routes
(`clone-prepare`, `multi-prepare`). This demonstrates those paths
work in a sustained native run; it does **not** show the intermittent
`multi-after-mode-test` route executing.

A normal passing lifecycle does **not** exercise the new UUID-guarded
batch cleanup after user-driven Windows Display Settings rearrangement;
that new path is covered synthetically and still requires native
acceptance. The earlier interrupted duration attempt remains a
separate, correctly reported **external-layout-change FAIL**, not part
of this successful soak.

This acceptance does **not** establish the M3 crash, watchdog,
sleep/resume, reboot or unexpected process-termination recovery gates,
and is not authorization to merge draft PRs or publish to PyPI.

## Next work

- Keep PR #6 (guarded batch cleanup) draft until its new failure-path
  behavior is independently exercised on Windows under a controlled,
  physically recoverable procedure.
- Begin M3 with bounded, fail-closed keeper and watchdog interruption
  tests before more disruptive sleep/reboot scenarios.
- Preserve the native receipt filenames as audit references:
  `reliability-c1a471d8153849399b22ef1e9943f26d.json` and
  `reliability-e2346442b4464c46b93c27e4c1916ace.json`.
