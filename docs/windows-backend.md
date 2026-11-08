# Windows virtual monitor backend

This document describes the **unreleased Virtumon 0.2.0a1 candidate**. The
published 0.1.0 package only provides display discovery. A
[native Windows 11 create/list/heartbeat/remove cycle](windows-acceptance-2026-11-07.md)
has passed. Concurrent monitors and the intended remote-desktop workflow remain
the next hardware checks. A subsequent two-monitor experiment found that
adding `chat` made Windows re-clone the existing `work` monitor onto the
physical screen while `chat` appeared independently at `work`'s old
position. Removing `chat` did not restore `work`. This candidate therefore
fails closed on attempts to create a second monitor, pending atomic reconciliation
of all managed paths. If the first display has already become cloned,
`list --managed` reports an explicit error record rather than failing entirely;
`remove NAME` still uses the saved UUID and can clean up the displaced monitor.

## Experimental two-display reconciliation (separate branch)

The guarded multi-monitor implementation is isolated on
`feature/windows-multi-monitor-reconcile` and is **not enabled by default**.
The normal `feature/windows-virtual-monitors` branch continues to block a
second monitor. This experimental version has synthetic topology tests but has
**not** passed a real two-monitor Windows hardware run.

The test must start with **zero managed monitors and a stopped agent**. In
the normal desktop PowerShell session, set the variable *before the first*
`create` so that the detached keeper inherits it:

```powershell
$env:VIRTUMON_EXPERIMENTAL_MULTI = '1'
virtumon create work --resolution 1920x1080 --refresh 60 --right-of primary
virtumon create chat --resolution 1080x1920 --refresh 60 --right-of work
```

The second creation captures exact pre-add CCD/GDI metadata and the
previously managed target identity, then checks that all active targets are
either known baseline targets or the newly created driver target. Only when
Windows has the verified displaced-monitor arrangement does Virtumon
prepare **one supplied CCD transaction** that restores `work` and places
`chat` together. It preserves non-owned source/target/desktop-image modes,
validates the complete supplied configuration, rechecks the live topology
and heartbeat, then applies and verifies both owned signal modes and
independent GDI monitor-interface associations.

**This is a hardware experiment, not a repair guarantee.** A native Windows
11 run confirmed that two outputs can be created, remain independent for
five seconds, and be viewed via Windows display enumeration. However,
removing one output caused the other to become cloned onto the physical
desktop. The experimental branch now attempts a *separate guarded
post-removal repair*: with two verified managed records it captures
pre-removal source and signal modes, removes the exact requested GUID,
then checks that the remaining output still has its original mode, target
identity, source, and monitor interface. If Windows re-clones it onto a
known non-owned display, only that surviving owned target's CCD modes are
adjusted using a validated supplied configuration. A physical display
change blocks the repair. The removed UUID is journaled as removed even
if survivor reattachment fails; the error identifies the surviving
record and saves a local removal diagnostic.

A [native Windows 11 two-monitor lifecycle test](windows-acceptance-2026-11-08.md)
now passed for the revised removal repair: `work` and `chat` were independently
attached for five seconds, removing `chat` retained the same live `work`
identity and geometry after another five seconds, and removing `work` succeeded.
The captured output does not identify whether the survivor required the
reattachment branch or remained independent without intervention. Other GPU
and driver configurations still need verification. The driver may
alter the existing desktop as soon as it removes one monitor, before
Virtumon can reattach the survivor. If the repair fails, remove the
remaining named monitor by its UUID and verify that the physical
desktop is intact. Never delete ownership records manually. Use a
physical-screen fallback and do not change Windows Display Settings or
run another SudoVDA controller during the test.
If `work` is displaced, remove it by exact name, confirm the desktop
returns to the physical display, and stop the agent before changing
installations. Do not delete ownership state files manually.

The current experiment supports **one existing managed monitor plus one
new monitor**; a third creation remains blocked. The env variable is
examined by the keeper, not the interactive CLI process. Setting it after
the keeper starts will not enable the experiment.

## Prerequisites

- Python 3.10 or newer and the installed Virtumon candidate wheel.
- A 64-bit Windows 10/11 desktop session.
- Exactly one enabled, compatible **SudoMaker Virtual Display Adapter
  (SudoVDA)** installed and working in Device Manager. The preview rejects
  multiple enabled adapters. Installing the Python wheel does not install one.
- Permission to install a driver and trust its upstream certificate during
  setup. Everyday Virtumon commands run in the user's desktop session.

Ordinary `virtumon list` is independent of SudoVDA. It remains useful when the
driver is absent. Creating a monitor requires the driver and the background
Virtumon agent.

## Obtain the driver

The reviewed SudoVDA repository has no standalone GitHub release. The verified
binary distribution is bundled with
[Apollo v0.4.6](https://github.com/ClassicOldSong/Apollo/releases/tag/v0.4.6):

- [Apollo-0.4.6.exe](https://github.com/ClassicOldSong/Apollo/releases/download/v0.4.6/Apollo-0.4.6.exe)
- SHA-256 reported by that release:
  `42b2aefaacb3474511517a56b96ee9f0517f30ac38b5dd2fda9fd5b478f5021a`
- Bundled INF: version **1.10.9.289**, dated **2025-07-14**, for `NTamd64`.

These details identify the reviewed package, rather than promising that every
future Apollo installer contains a compatible driver. The version is pinned
because its driver protocol and installer behavior have been inspected. Its
complete SudoVDA bundle can be extracted with 7-Zip for a driver-only setup;
the extraction procedure below was verified without executing the installer.

The Apollo installer installs the streaming host and related components as well
as SudoVDA. Its
[packaging script](https://github.com/ClassicOldSong/Apollo/blob/0cd32abaaa141d262477d039ac447b38fe99c394/cmake/packaging/windows_nsis.cmake)
runs `drivers\sudovda\install.bat`, installs the Apollo service, and applies its
other selected setup actions. Treat this as installation of Apollo, not a
driver-only installer. If Apollo is already installed, first check the existing
driver with `virtumon doctor`; do not reinstall it merely to try Virtumon.

The source repository's `src_assets/windows/drivers/sudovda` directory is not a
complete binary installer: it omits the compiled DLL and installer helper. Do
not download that directory alone and expect its batch file to install a driver.

### Certificate trust is part of installation

The reviewed upstream
[install.bat](https://github.com/ClassicOldSong/Apollo/blob/0cd32abaaa141d262477d039ac447b38fe99c394/src_assets/windows/drivers/sudovda/install.bat)
imports `sudovda.cer` into the machine's **Trusted Root Certification
Authorities** and **Trusted Publishers** stores before installing the driver.
This changes the certificates that Windows trusts. Inspect the downloaded
package and certificate, and proceed only if you choose to trust that upstream
publisher. Virtumon does not perform this import for you.

The extracted certificate is self-signed, with subject and issuer
`CN=sudovda@su.mk`, valid from **2025-07-13** to **2030-07-13**. Its SHA-256
fingerprint (also the SHA-256 of the DER-encoded `sudovda.cer` file) is:

```text
6accdcd519f6179d967db4eaa20ecf25a732ba30e87f4cffebc768b2c13c9007
```

The release notes describe this renewed five-year code-signing certificate.
This is **not a claim of Microsoft WHQL certification or attestation signing**.
The installer also removes an existing SudoVDA device node before creating the
new one, so close active streams and other SudoVDA controllers before installing
or upgrading it. A driver update affects every monitor on that adapter.

### Install only the extracted driver

1. Download the pinned `Apollo-0.4.6.exe` above and check its hash against the
   release value:

```powershell
Get-FileHash .\Apollo-0.4.6.exe -Algorithm SHA256
```

2. With [7-Zip](https://www.7-zip.org/download.html) installed, extract just the
   driver directory. This command reads the installer archive; it does not run
   Apollo setup:

```powershell
& "$env:ProgramFiles\7-Zip\7z.exe" x .\Apollo-0.4.6.exe 'drivers/sudovda/*' '-o.\Apollo-0.4.6-driver'
if ($LASTEXITCODE -ne 0) { throw 'Driver extraction failed.' }
Get-ChildItem .\Apollo-0.4.6-driver\drivers\sudovda
```

The extracted directory must contain `SudoVDA.dll`, `SudoVDA.inf`,
`sudovda.cat`, `sudovda.cer`, `nefconc.exe`, `install.bat`, and `uninstall.bat`.

3. Inspect the upstream script and certificate before trusting them:

```powershell
Get-Content .\Apollo-0.4.6-driver\drivers\sudovda\install.bat
certutil -dump .\Apollo-0.4.6-driver\drivers\sudovda\sudovda.cer
Get-FileHash .\Apollo-0.4.6-driver\drivers\sudovda\sudovda.cer -Algorithm SHA256
```

4. If you accept the certificate trust and driver changes described above,
   open an elevated PowerShell in the same download directory and run the
   extracted upstream installation script:

```powershell
& .\Apollo-0.4.6-driver\drivers\sudovda\install.bat
```

Review the certificate and driver installation output. The batch file's final
"Done" line alone does not verify success. Check Device Manager for
**SudoMaker Virtual Display Adapter**, then run `virtumon doctor` from your
normal desktop session. Driver-only extraction does not install Apollo's host,
service, firewall rules, or gamepad component.

If you also want Apollo, its full upstream installer remains a separate option.
Close active Apollo streams and quit its controller before testing Virtumon.
Follow upstream troubleshooting if Windows rejects the package. Virtumon does
not require or recommend disabling Secure Boot or Windows signature enforcement.

## Try the candidate

Run the read-only checks first:

```powershell
virtumon --version
virtumon doctor
virtumon list --json
virtumon list --managed --json
```

Then create and remove a named monitor:

```powershell
virtumon create workspace --resolution 1920x1080 --refresh 60
virtumon list --managed --json
virtumon list --json
virtumon remove workspace
```

The default mode is 1920 x 1080 at 60 Hz, placed to the right of the current
desktop. Relative placement options are `--right-of`, `--left-of`, `--above`,
and `--below`; inspect `virtumon create --help` for accepted anchor names.
The candidate configures SDR monitors. HDR and capture performance are outside
this initial acceptance test.

Primary status comes from the matched GDI source's
[`DISPLAY_DEVICE_PRIMARY_DEVICE` flag](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-display_devicew).
CCD source coordinates alone do not authorize a primary-display change. If CCD
and GDI metadata disagree while a monitor arrives, discovery retries within a
bounded deadline. Missing or ambiguous primary information blocks layout writes.
The primary-position error includes the target device and current/requested
coordinates so an unsuccessful hardware run can be diagnosed without guessing.

`remove NAME...` accepts one or more managed names. `remove --all` applies to
Virtumon's managed records. It does not mean "remove every display on the
computer". Use explicit names during initial testing.

### When Windows starts the new monitor as a clone

An observed creation failure showed a stable virtual clone across display
adapters: the new SudoVDA target had its own CCD source identity, but its GDI
source was detached. The physical screen's attached GDI source listed both
monitor interfaces, and a legacy CCD query described the shared desktop. In
that situation, waiting longer for an independent GDI source could not extend
the monitor. Microsoft's [CCD example](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/ccd-example-code)
also describes virtual clones whose separate sources have the same position.

During `create`, the candidate has a narrow path for promoting this verified,
newly owned virtual target to an independent desktop at its requested mode and
position. It checks the exact adapter/target identity, the clone
association, the pre-creation baseline, and current ownership. Ambiguous source
associations remain errors. Generic display discovery stays strict, and
read-only commands do not change a cloned desktop.

The promotion builds a supplied CCD configuration containing every active path
in its existing order. Changes are confined to the owned target's configuration;
other paths and their source, target, and desktop-image modes are preserved.
It uses [`SetDisplayConfig`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setdisplayconfig)
with `SDC_VALIDATE`, then `SDC_APPLY`, together with
`SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_VIRTUAL_MODE_AWARE`. It does not request
`SDC_ALLOW_CHANGES`, a global extend topology, or persistence in Windows' display
database. Fresh observations check the other displays before and after applying
the configuration. Readback must confirm independent GDI attachment, desktop
geometry, primary status, and the requested actual signal size and refresh.

Ownership guards also cover validation, application, and subsequent discovery.
An ownership interruption stops further layout writes. If promotion succeeds
but a later creation step fails, cleanup removes the newly created monitor by
its journaled UUID. It does not replay the old cloned desktop as a broad
rollback. Unconfirmed removal retains the ownership record and its error for
later recovery.

`SetDisplayConfig` accepts the complete active configuration, so an external
display change between the last query and the apply call remains a race; the
API provides no conditional apply tied to that queried snapshot. Leave Windows
Display Settings and other display controllers alone during the acceptance
test. If another display changes, the operation reports the discrepancy and
attempts cleanup of its own monitor rather than guessing how to restore the
other display.

The candidate containing this fix passed a
[native single-monitor lifecycle test](windows-acceptance-2026-11-07.md).
The console output does not identify the internal configuration path used;
remote-desktop behavior and concurrent virtual monitors remain unverified.

### JSON output

Ordinary `list --json` keeps the existing array of active Windows displays:

```json
[
  {
    "device": "\\\\.\\DISPLAY1",
    "x": 0,
    "y": 0,
    "width": 1920,
    "height": 1080,
    "primary": true
  }
]
```

`list --managed --json` is an array of managed monitor records. `create --json`
returns one record on success:

```json
{
  "name": "workspace",
  "id": "12345678-1234-4234-9234-123456789abc",
  "backend": "sudovda",
  "status": "active",
  "device": "\\\\.\\DISPLAY2",
  "width": 1920,
  "height": 1080,
  "refresh_hz": 60,
  "x": 1920,
  "y": 0,
  "primary": false
}
```

Status can be `active`, `stale`, `missing`, or `error`. Device and coordinates
may be `null` when there is no matching active Windows display. An `error`
string may explain a failed operation. The UUID is the managed identity;
Windows names such as `\\.\DISPLAY2` describe the current desktop and can
change.

`remove --json` reports successes and failures separately:

```json
{"removed": ["workspace"], "errors": []}
```

An unsuccessful individual removal is included as `{"name": "...", "error":
"..."}` in `errors`, and partial failure returns exit status 1. Treat a nonzero
exit status as a failure even if stdout contains JSON.

## Agent lifetime and ownership

SudoVDA is controlled through Windows device IOCTLs. Creating a monitor starts
the detached Virtumon agent, which keeps the driver handle open and sends
heartbeats after the initiating CLI command exits. `virtumon agent status`
reports the keeper; `virtumon agent stop` refuses while managed records remain.
This candidate does not install a service or add a login startup task.

The driver's default watchdog is **three seconds**. Its expiry disconnects
**all monitors on that SudoVDA adapter**, regardless of which application made
them. The driver decrements a countdown on a global one-second tick, so a
three-second setting can expire just over two seconds after the last heartbeat.
Virtumon conservatively invalidates ownership continuity when a heartbeat gap
could have crossed that earlier expiry; a later successful heartbeat does not
restore confidence in the old monitor identities.

The preview accepts an enabled watchdog only when its timeout is **at least two
seconds**. A value of **zero**, meaning disabled upstream, is also accepted; a
one-second timeout is rejected. The watchdog is configured upstream under
`HKLM\SOFTWARE\SudoMaker\SudoVDA`; Virtumon does not change that registry setting
at runtime. See [upstream configuration](https://github.com/SudoMaker/SudoVDA/blob/a4b09fa2aa731a964d0cb5d139cb1e6240e4da12/README.md#config).

An agent crash, forced termination, or system shutdown can remove active virtual
displays. A saved Virtumon record alone does not keep a monitor alive. Each
record pins the original driver device path as well as the monitor UUID, so
creation, heartbeat, and selective removal cannot silently switch to a different
adapter.

Use one SudoVDA controller during initial testing. In particular, do not run
Apollo's controller alongside Virtumon and assume their lifetimes are isolated.
Virtumon's selective removal uses the UUID it recorded for its own monitor;
that does not prevent another controller or the driver's watchdog from changing
the shared adapter.

### Recover stale records

Virtumon stores per-user state in `%LOCALAPPDATA%\Virtumon`. After an agent
restart, existing records are stale: the new agent does not assume that saved
display identities still belong to live monitors. It does not automatically
recreate them. To recover, inspect the records, remove the exact names you want
to replace, and recreate those monitors with the desired mode and placement:

```console
virtumon list --managed
virtumon remove workspace
virtumon create workspace --resolution 1920x1080 --refresh 60
```

Recreate a name only after its removal succeeds. If the original driver device
path changed, for example after reinstalling the adapter, Virtumon retains the
record with an error instead of directing removal at a different adapter.
Inspect `virtumon doctor` and the recorded error before proceeding; deleting
state files is not a substitute for confirming which monitors still exist.

## Windows acceptance script

From the repository, point the script at the interpreter where the candidate
wheel is installed:

```powershell
.\tools\verify_windows.ps1 -Python .\.venv\Scripts\python.exe
```

With Conda or several Python installations, use the active interpreter's exact
path to keep the package and verification environment aligned:

```powershell
$virtumonPython = python -c "import sys; print(sys.executable)"
.\tools\verify_windows.ps1 -Python $virtumonPython
```

The default `-Python python` chooses the first matching application. It never
joins the paths of multiple Python installations into one executable name.

The default run calls version, doctor, and ordinary/managed JSON listing. It
does not create or remove a monitor, install a driver, import a certificate,
change registry settings, or stop an agent.

Add `-Exercise` for the explicit hardware test:

```powershell
.\tools\verify_windows.ps1 -Python .\.venv\Scripts\python.exe -Exercise
```

The exercise creates a unique `virtumon-check-...` monitor at 1280 x 720 and
60 Hz, waits beyond the default watchdog interval, and matches its managed
name, UUID, device, and geometry to the Windows display list. It records the
pre-existing displays and managed IDs so it can check that they remain present.
Its `finally` block attempts to remove only the generated name, including after
a partial creation failure. If creation already rolled back and the name is
absent, it proceeds directly to verification. Cleanup also checks that no extra
Windows display remains beyond the baseline; an unexpected display is reported,
not removed by guessing its ownership. It never issues `remove --all` or
`agent stop`.

The script returns nonzero on a command failure, invalid JSON, failed assertion,
or cleanup failure. Keep the full output for review. If the shell or computer
is forcibly terminated, `finally` cannot run; inspect `list --managed` and remove
the printed test name explicitly after reconnecting.

Passing this exercise confirms a basic create/list/remove cycle on that
computer. Also check Windows Display Settings and the intended remote-desktop
client in the same desktop session before treating the candidate as validated.
Capture behavior, mixed-DPI arrangements, reconnects, sleep/resume, and different
GPU/driver combinations need separate hardware coverage.

### Updating the installed preview

The background agent keeps its imported Python code until it exits. After
finishing with any managed monitors, stop the agent before installing an updated
checkout, even when the unreleased version number has not changed:

```powershell
virtumon agent stop
python -m pip install .
```

If stopping reports that managed monitors remain, inspect them and remove the
specific names you intend to close before updating. The next creation starts
the updated agent automatically.

### Diagnose a CCD/GDI metadata mismatch

If creation reports a timeout waiting for consistent Windows display metadata,
the error now distinguishes a missing GDI source, duplicate source matches,
and a matching source whose attached flag is clear. The timeout captures the
exact CCD snapshot and GDI enumeration that failed, followed by bounded,
best-effort read-only observations from the other Windows display APIs.

A detached GDI source does not by itself distinguish transient arrival from a
stable clone. The [creation-time clone path](#when-windows-starts-the-new-monitor-as-a-clone)
handles only the verified case described above; other missing or ambiguous
associations continue to fail without a speculative display change.

When this happens during creation, Virtumon saves a report before removing its
new monitor by UUID. The error prints its path:

```text
Diagnostic report: ...\Virtumon\diagnostics\create-<UUID>.json
```

The default directory is `%LOCALAPPDATA%\Virtumon\diagnostics`; a configured
`VIRTUMON_STATE_DIR` places the reports under that directory instead. The report
contains the requested position, baseline geometry, driver-returned identity,
operation phase, and display configuration metadata. The exact failed sample
and later supplemental reads are labeled separately because they are not an
atomic Windows snapshot. Supplemental reads have a bounded call count and a
time budget checked between calls; a synchronous Windows API call itself cannot
be interrupted by that budget.

Share the JSON report with the acceptance-script output. The report does not
establish cleanup success; that remains the acceptance script's final check.
If collecting or saving a report fails, the original creation error and normal
exact-GUID cleanup still take priority.

## Pinned backend references

| Component | Reviewed reference |
| --- | --- |
| SudoVDA source | [`a4b09fa2aa731a964d0cb5d139cb1e6240e4da12`](https://github.com/SudoMaker/SudoVDA/tree/a4b09fa2aa731a964d0cb5d139cb1e6240e4da12) |
| IOCTL protocol | [Header, version 0.2.1](https://github.com/SudoMaker/SudoVDA/blob/a4b09fa2aa731a964d0cb5d139cb1e6240e4da12/Common/Include/sudovda-ioctl.h) |
| Driver implementation | [Driver.cpp](https://github.com/SudoMaker/SudoVDA/blob/a4b09fa2aa731a964d0cb5d139cb1e6240e4da12/Virtual%20Display%20Driver%20%28HDR%29/SudoVDA/Driver.cpp) |
| Bundled driver metadata | [Apollo v0.4.6 INF](https://github.com/ClassicOldSong/Apollo/blob/0cd32abaaa141d262477d039ac447b38fe99c394/src_assets/windows/drivers/sudovda/SudoVDA.inf) |
| Certificate and driver setup | [Apollo v0.4.6 install.bat](https://github.com/ClassicOldSong/Apollo/blob/0cd32abaaa141d262477d039ac447b38fe99c394/src_assets/windows/drivers/sudovda/install.bat) |

The protocol accepts a monitor UUID and per-monitor mode at creation, returns
the adapter LUID and target ID, and removes a monitor by its UUID. These values
let Virtumon associate its managed record with the current Windows display.
This is the reason for choosing SudoVDA over a driver controlled only by a
global display count.

Virtumon is MIT-licensed. The SudoVDA README describes its changes as MIT,
CC0, or public domain, while retaining the Microsoft sample's licensing
requirements. Apollo is GPLv3. The candidate talks to an independently installed
driver; it does not bundle Apollo or redistribute its installer.
