# Windows two-monitor hardware acceptance: 2026-11-08

**Result: PASS — two virtual monitors created and independently removed on the tested Windows 11 machine.**

This is a hardware observation from the user-supplied PowerShell transcript,
not a synthetic test or CI substitute. The experiment follows a previous
failure in which removing a second virtual monitor caused the first to become
cloned onto the physical screen.

## Configuration

| Property | Observed |
| --- | --- |
| Code commit | [`8e1ffcabc0f84207d26a08461939a476727f62b2`](https://github.com/dependanz/virtumon/commit/8e1ffcabc0f84207d26a08461939a476727f62b2) |
| Branch | `feature/windows-multi-monitor-reconcile` |
| Version | `0.2.0a1` (local experimental checkout, not a PyPI release) |
| Platform | Windows 11, AMD64 |
| Python | 3.13.5 |
| Driver | SudoVDA, protocol 0.2.1 |
| Driver watchdog | 3 seconds |
| Environment | `VIRTUMON_EXPERIMENTAL_MULTI=1` before the keeper started |
| Initial state | One physical 2560 × 1600 primary display; agent stopped; zero managed records |

The user pulled commit `8e1ffca`, stopped the old agent, reinstalled the
package, and confirmed driver readiness using `virtumon doctor`.

## Create both monitors

The two `create --json` calls succeeded:

| Monitor | Stable Virtumon UUID | Resolution | Hz | Position | Primary | Windows name |
| --- | --- | --- | --- | --- | --- | --- |
| Physical | Not managed by Virtumon | 2560 × 1600 | Not reported in the final display list | (0, 0) | Yes | `DISPLAY1` |
| `work` | `2f4e7eee-0924-46e8-92d0-539484bc4f4d` | 1920 × 1080 | 60 | (2560, 0) | No | `DISPLAY28` |
| `chat` | `49239512-ab55-49e7-a5d3-d19f36e1a02b` | 1080 × 1920 | 60 | (4480, 0) | No | `DISPLAY29` |

After a five-second delay, `list --managed --json` showed **both** named
outputs `active` with those same UUIDs and geometries. A separate
`list --json` showed all three desktop displays in the intended extended
layout and kept the laptop screen primary. This confirms both displays
remained available after the initiating CLI calls exited and beyond the
driver's stated watchdog period.

## Remove `chat` while preserving `work`

`virtumon remove chat --json` returned:

```json
{"removed": ["chat"], "errors": []}
```

After **another five seconds**:

- `list --managed --json` showed only `work`, with the **same UUID**,
  1920 × 1080 at (2560, 0), 60 Hz, status `active`, nonprimary.
- `list --json` showed the original physical screen plus `DISPLAY28`
  at the original `work` geometry, with no `chat` display.
- `agent status --json` reported `running=true`,
  `managed_count=1`, `heartbeat=ok`, watchdog 3 seconds, and no error.

This directly validates selective removal **without losing the remaining
managed desktop**, the regression observed in the earlier experiment.

The terminal output does not identify whether the surviving display was
reattached by the new repair code or remained independent without needing
repair. It establishes the externally visible postcondition, not the precise
internal branch taken.

## Final cleanup

`virtumon remove work --json` returned
`{"removed": ["work"], "errors": []}`. `virtumon agent stop` then printed
`Stopped the Virtumon agent.`

The transcript does **not** include another `list --json` after final
cleanup; therefore, final desktop enumeration is not independently reported
at that moment. No errors were reported by either cleanup command.

## Remaining validation

This pass establishes a two-monitor create/list/heartbeat/selective-remove
lifecycle on one Windows machine. It does **not** establish all of the
following:

- Chrome Remote Desktop viewing and interactive mouse/keyboard input to
  both monitors *during this successful two-monitor run*.
- Extended runtime stability, wake/sleep, reconnects, restart recovery,
  or concurrent use with other display controllers.
- Different GPUs, scaling/DPI, arbitrary placement or resolution
  combinations, and third or subsequent virtual monitors.
- Which internal CCD repair branch actually executed during removal.

Retain the draft experimental PR until remote-interaction and broader safety
coverage are reviewed; the published PyPI `0.1.0` release is unchanged.
