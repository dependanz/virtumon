# Windows hardware acceptance: 2026-11-07

**Result: PASS for one virtual monitor's create/list/heartbeat/remove cycle.**

Evidence is the user-supplied PowerShell transcript from the target Windows
computer, reviewed on November 7, 2026 (America/New_York). This records a native
hardware run, separate from simulated topology tests and read-only CI probes.

## Tested configuration

| Item | Observed value |
| --- | --- |
| Source commit | [`fc6e197a099975d4d5277c472281b1d3ea66244d`](https://github.com/dependanz/virtumon/commit/fc6e197a099975d4d5277c472281b1d3ea66244d) |
| Package version | `0.2.0a1`, installed from the updated checkout |
| OS and architecture | Windows 11, AMD64 |
| Python | 3.13.5 |
| SudoVDA protocol | 0.2.1 |
| Driver watchdog | 3 seconds |
| Initial desktop | One primary display, 2560 x 1600 at (0, 0) |
| Initial managed monitors | None |
| Exercise | `tools/verify_windows.ps1 -Python <explicit-python-path> -Exercise` |

The existing agent was stopped before the updated package was installed. The
reported wheel SHA-256 was
`27701052a515a9692ed64685e635bf6b8ad01be9b88cd72c64334a4eb2067f0b`.

## Observed result

Creation returned an active SudoVDA monitor at the requested 1280 x 720 and
60 Hz. After the create command exited, the script waited five seconds and
queried the managed and Windows display lists again:

| Display | Desktop size | Position | Primary |
| --- | --- | --- | --- |
| Existing physical display | 2560 x 1600 | (0, 0) | Yes |
| New virtual display | 1280 x 720 | (2560, 0) | No |

The same generated name and UUID remained active after that wait. Managed
geometry agreed with the independently enumerated Windows desktop. Removing
the generated name returned that name in `removed` and an empty `errors` list.
Final checks confirmed that the temporary managed record and additional active
display were absent, while the original physical display's size, position,
and primary status were preserved.

The transcript ended with:

```text
Create/list/heartbeat checks passed.
Cleanup verified; pre-existing displays and managed identities are preserved.

PASS: Windows monitor exercise completed.
```

The successful run used the commit containing the virtual-clone fix. The
console transcript does not expose which internal configuration path was used
for this particular creation.

## Remaining coverage

This establishes one additional monitor on this Windows configuration, with
background lifetime exceeding the reported three-second watchdog interval.
It does not establish prolonged runtime, restart persistence, or compatibility
with every Windows/driver configuration. There were no pre-existing managed
monitors, so preserving another Virtumon during removal remains untested here.

The next focused checks are:

1. Create a 1920 x 1080, 60 Hz `work` monitor to the right of the primary screen.
2. Create a 1080 x 1920, 60 Hz `chat` monitor to the right of `work`.
3. Verify both in Windows Display Settings and through the intended Chrome
   Remote Desktop session, including moving and interacting with a window on
   each virtual desktop.
4. Remove only `chat`; confirm that `work` retains its UUID, active status,
   mode, position, and nonprimary status, and that the physical screen is
   unchanged.

Capture behavior, mixed-DPI layouts, reconnects, sleep/resume, and other
GPU/driver combinations require their own observations. macOS and Linux
monitor creation remain planned.
