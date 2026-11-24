# M0 native Windows snapshot — November 24, 2026

Source: the user's complete PowerShell output supplied during the M0 review.
These observations were read on the Windows host, not executed by the review's
Linux environment. This record contains the reported results without the
repeated shell prompts.

## Repository

```text
Computer: MSI
Repository: C:\dev\virtumon
git status --short --branch:
## feature/windows-m3-heartbeat-outage...origin/feature/windows-m3-heartbeat-outage

HEAD: 1be811a9c40f8622cd08abb48234173519cf8052
Last commit: 1be811a 2026-11-11T14:07:26-05:00 Align stale-identity safety rejection across recovery checks
```

No tracked changes or untracked files were listed by the supplied status
command. The current live remote tip was independently observed at `86d9eca`
in this review; `360d562` and `86d9eca` only update documentation. The local
status line compares against cached tracking information, not a fresh fetch.

## Interpreter and installed package

```text
python.exe: C:\Users\Danze\anaconda3\python.exe
virtumon.exe: C:\Users\Danze\anaconda3\Scripts\virtumon.exe
Python: C:\Users\Danze\anaconda3\python.exe
Module: C:\Users\Danze\anaconda3\Lib\site-packages\virtumon\__init__.py

virtumon 0.2.0a1
Virtumon 0.2.0a1
Platform: Windows 11 (AMD64)
Python: 3.13.5
Display discovery: OK (1 active display(s))
Virtual monitor creation: SudoVDA driver available
Driver protocol: 0.2.1; watchdog: 3 seconds
Agent: stopped or unresponsive; 0 saved monitor(s)
```

## Desktop and managed records

Ordinary display enumeration:

```json
[
  {
    "device": "\\\\.\\DISPLAY1",
    "x": 0,
    "y": 0,
    "width": 2560,
    "height": 1600,
    "primary": true
  }
]
```

Managed-display enumeration:

```json
[]
```

Agent status:

```json
{
  "running": false,
  "managed_count": 0,
  "version": null,
  "error": "The agent is stopped or unresponsive; saved monitor mappings are unverified."
}
```

## Interpretation and limits

- The requested current-native snapshot is complete: clean checkout, selected
  interpreter, installed module/version, driver readiness, desktop geometry,
  empty managed journal and agent observation are recorded.
- Source comparison establishes runtime-code equivalence between checkout
  `1be811a` and review base `86d9eca`. Package version and import path do not
  establish that the installed package bytes match either commit.
- `running=false` is the CLI's stopped-or-unresponsive fallback. This output
  does not independently prove that no keeper process exists. With zero saved
  records, it does not identify an orphaned owned display requiring cleanup.
- The snapshot performs no display creation/removal, keeper start/stop,
  heartbeat fault injection, sleep or reboot. It adds no new lifecycle or
  recovery acceptance and does not prove readiness for a disruptive test.
- Exact hardware/driver-package identity, iPad model/software, network setup
  and remote-desktop latency measurements remain open in the M0 reference
  record. No unsupported values are inferred from the hostname `MSI`.
