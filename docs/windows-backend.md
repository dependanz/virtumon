# Windows virtual monitor backend

This document describes the **unreleased Virtumon 0.2.0a1 candidate**. The
published 0.1.0 package only provides display discovery. The implementation and
its first hardware acceptance run must be reviewed before publishing a release.

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

`remove NAME...` accepts one or more managed names. `remove --all` applies to
Virtumon's managed records. It does not mean "remove every display on the
computer". Use explicit names during initial testing.

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
a partial creation failure. It never issues `remove --all` or `agent stop`.

The script returns nonzero on a command failure, invalid JSON, failed assertion,
or cleanup failure. Keep the full output for review. If the shell or computer
is forcibly terminated, `finally` cannot run; inspect `list --managed` and remove
the printed test name explicitly after reconnecting.

Passing this exercise confirms a basic create/list/remove cycle on that
computer. Also check Windows Display Settings and the intended remote-desktop
client in the same desktop session before treating the candidate as validated.
Capture behavior, mixed-DPI arrangements, reconnects, sleep/resume, and different
GPU/driver combinations need separate hardware coverage.

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
