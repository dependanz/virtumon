# Virtumon

**Your desktop, expanded.**

Virtumon is growing into a cross-platform desktop toolkit with three parts:
virtual displays, its own remote-desktop host and client, and a window organizer
with cross-app tabs and custom layouts. The current implementation is the
Windows display CLI and its experimental management backend; the remote
desktop and organizer are planned work.

**Release status:** PyPI version **0.1.0** provides the banner, display listing,
and diagnostics. The **0.2.0a1** Windows candidate is developed in a stack of
draft branches, most recently `feature/windows-m3-heartbeat-outage`. Native
single-display, two-display, repetition, one-hour duration, and two bounded
recovery checks have passed on the recorded Windows 11 laptop. The candidate
remains unreleased, with specific compatibility and recovery gates open.
See the [M0 baseline](docs/m0-baseline-2026-11-24.md) for exact commits,
evidence boundaries and remaining work, and the
[product roadmap](docs/product-roadmap.md) for the expanded scope.

## Install the published release

Requires Python 3.10 or newer. Display discovery requires Windows 10 version
1607 or later, or Windows 11.

```console
python -m pip install virtumon
virtumon
```

Every command can also be run as `python -m virtumon`.

```console
virtumon --help          # Command-line help
virtumon --version       # Installed version
virtumon list            # Active displays in the current Windows desktop
virtumon list --json     # Display geometry as a JSON array
virtumon doctor          # Platform and display diagnostics
```

Ordinary `list` reports each active display's device name, desktop position,
physical pixel dimensions, and primary-display status. It includes physical
displays and displays supplied by existing virtual-display drivers.

## Windows management candidate

Install the candidate wheel from this checkout and follow
[Windows backend setup](docs/windows-backend.md) before using these commands.
The Python package does not install a Windows display driver. SudoVDA's current
upstream distribution has an explicit certificate-trust step; review that setup
guide before installing it.

The preview requires exactly one enabled SudoVDA adapter and rejects multiple
adapters. It records that adapter's device path with each managed monitor.
A Windows 11 hardware test revealed that adding a second monitor can clone the
first monitor onto the physical screen. The main preview therefore supports
only one managed monitor at a time. A separate
[`feature/windows-multi-monitor-reconcile` experimental branch](docs/windows-backend.md#experimental-two-display-reconciliation-separate-branch)
has an opt-in guarded all-owned-target update. Two concurrent outputs have
now been observed active through the SudoVDA watchdog on Windows 11, but
removing either output can re-clone the surviving monitor onto the physical
screen. The [selective-removal recovery path](docs/windows-acceptance-2026-11-08.md)
now has a passing native Windows 11 test: two independently attached virtual
screens stayed active after five seconds, removing `chat` preserved `work`
with its original UUID and geometry, and the remaining output was removable.
It captures the pre-removal layout and can reattach a verified owned clone
while preserving non-owned CCD signal modes. The
[legacy Windows roadmap](docs/roadmap.md) also records a user-reported 5/5
Chrome Remote Desktop interaction check. That is historical third-party
compatibility evidence; Virtumon's own remote host and client are not yet
implemented. Later branches passed the
[one-hour fixed-layout soak](docs/windows-m2-duration-2026-11-11.md).
Sleep/resume, reboot and broader compatibility remain open.
Exact-name removal always uses the recorded UUID.

```console
virtumon create workspace --resolution 1920x1080 --refresh 60
virtumon list --managed
virtumon list --managed --json
virtumon remove workspace
```

`create` defaults to 1920 x 1080 at 60 Hz, positioned to the right of the current
desktop. Use `--right-of`, `--left-of`, `--above`, or `--below` with an anchor to
choose a relative position; see `virtumon create --help` for accepted anchors.
`create --json` returns the created monitor object.

Managed monitors have a Virtumon name and UUID. `list --managed` reports those
records and their current status; ordinary `list` continues to show the Windows
desktop. `remove NAME...` removes the requested Virtumon monitors, and
`remove --all` is limited to Virtumon's managed records.

### Why a background agent runs

The first `create` starts a detached Virtumon agent. It keeps the SudoVDA driver
alive after the command exits. SudoVDA's watchdog defaults to three seconds;
its global one-second tick can disconnect monitors sooner than a full three
seconds after the last heartbeat. The agent must remain running while virtual
monitors are in use. Enabled watchdog values must be at least two seconds;
an upstream value of zero, which disables the watchdog, is also accepted.
Virtumon does not change the driver's registry settings. See the
[upstream watchdog configuration](https://github.com/SudoMaker/SudoVDA/blob/a4b09fa2aa731a964d0cb5d139cb1e6240e4da12/README.md#config).

```console
virtumon agent status
virtumon remove workspace
virtumon agent stop
```

`agent stop` refuses while managed records remain. This candidate does not
install a Windows service or configure automatic startup at login. Use a single
controller for SudoVDA during testing; close active Apollo streams and quit its
controller before using Virtumon.

Per-user state is stored in `%LOCALAPPDATA%\Virtumon`. Records become stale after
an agent restart and are not automatically recreated. Inspect `list --managed`,
remove the exact stale names, then recreate them. If the original driver device
path has changed, removal retains the record with an error; see the
[recovery details](docs/windows-backend.md#recover-stale-records).

## Support and validation

| Capability | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Banner, help, version | Available | Available | Available |
| Platform diagnostics | Available | Available | Available |
| Native display discovery | Available | Planned | Planned |
| Named virtual monitor creation/removal | 0.2.0a1 candidate; SudoVDA required | Planned | Planned |

The candidate targets 64-bit Windows 10/11 with a compatible SudoVDA driver.
A [native Windows 11 acceptance run](docs/windows-acceptance-2026-11-07.md)
passed creation of a 1280 x 720, 60 Hz monitor, background lifetime beyond the
driver watchdog interval, and selective removal while preserving the physical
desktop. Later native reports also credit
[two concurrent virtual monitors](docs/windows-acceptance-2026-11-08.md),
[25 complete cycles](docs/windows-m2-repetition-2026-11-08.md),
[one hour of continuous operation](docs/windows-m2-duration-2026-11-11.md),
[keeper termination](docs/windows-m3-recovery-2026-11-11.md), and
[heartbeat interruption](docs/windows-m3-heartbeat-2026-11-11.md).
These passes apply to their recorded Windows setup and tested scenarios;
they do not establish Virtumon's planned capture or remote-control pipeline.

From the repository in PowerShell, the acceptance script is read-only by default:

```powershell
.\tools\verify_windows.ps1 -Python .\.venv\Scripts\python.exe
```

Use `-Exercise` explicitly to create one uniquely named temporary monitor,
verify it survives the default watchdog interval, and remove that same monitor
in a `finally` block. The script never uses `remove --all`.

```powershell
.\tools\verify_windows.ps1 -Python .\.venv\Scripts\python.exe -Exercise
```

The package has no third-party Python runtime dependencies. Color is enabled
only for a compatible interactive terminal; set `NO_COLOR=1` for plain output.
JSON output is separate from the banner and diagnostic text.

## Roadmap and Windows reliability testing

The [first native M2 reliability smoke](docs/windows-m2-smoke-2026-11-08.md)
passed three cycles with an audited receipt. The follow-up
[25-cycle repetition run](docs/windows-m2-repetition-2026-11-08.md) completed
25/25 cycles and the uploaded JSON receipt passed the event and UUID audit.

See the [product milestones](docs/product-roadmap.md),
[legacy Windows release gates and native reliability runner](docs/roadmap.md),
and [proposed display-subsystem API](docs/architecture.md). The reliability
runner is **read-only by default** and requires explicit `--exercise` before it
creates any displays. Hardware tests must run locally, not in hosted CI:

```powershell
$env:VIRTUMON_EXPERIMENTAL_MULTI = '1'
& 'C:\Users\Danze\anaconda3\python.exe' .\tools\reliability_windows.py --exercise --cycles 3 --soak-seconds 30
```

It refuses an existing keeper or managed display, uses generated names, checks
both Windows and Virtumon display records, and saves a local diagnostic receipt.
The one-hour fixed-layout soak and bounded single-display keeper-crash and
heartbeat-outage recovery have passed. Native execution of the intermittent
post-mode-test route, external-layout batch cleanup, reboot, sleep/resume and
two-display crash/reconnect remain open. The experimental heartbeat fault
hook must be removed or excluded from a release build.

## Development

```console
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python -m pip install --force-reinstall dist/virtumon-0.2.0a1-py3-none-any.whl
python -m unittest discover -s tests -v
```

Tests exercise the installed package. A passing CI run cannot validate a live
Windows display driver; retain the Windows acceptance output before deciding to
publish this candidate. See [RELEASING.md](RELEASING.md) for release mechanics.

## License

MIT. Copyright 2026 Danzel Serrano. The separately installed display driver and
its distribution retain their upstream licenses.

