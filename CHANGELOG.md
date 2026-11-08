# Changelog

## Experimental multi-monitor branch (unreleased)

- Add opt-in `VIRTUMON_EXPERIMENTAL_MULTI=1` for a guarded two-monitor
  reconciliation. Capture original CCD/GDI metadata before adding the second
  monitor. If the first owned monitor is displaced, validate a single supplied
  display configuration that restores all owned outputs and preserves all
  non-owned modes, followed by independent GDI and real signal readback.
- The default one-at-a-time safety block remains in place.
- Hardware acceptance is required before releasing this path.

## 0.2.0a1 - Unreleased Windows preview

- Named `create` and `remove` commands backed by SudoVDA's per-monitor GUID interface.
- Resolution, refresh rate, and placement relative to primary or managed displays.
- `list --managed` and JSON output with verified live geometry or explicit stale state.
- Detached per-user keeper with an authenticated localhost endpoint and driver heartbeat.
- Durable ownership journal, exact-GUID cleanup, and verification through Windows display APIs.
- `agent status`, `agent stop`, and driver readiness in `doctor`.
- Driver installation notes and an opt-in native Windows acceptance script.
- Windows acceptance checks select one Python executable when several are installed;
  runtime regressions cover both Windows PowerShell and PowerShell when available.
- Determine primary display status from the matched Windows GDI source flag;
  retry inconsistent hotplug metadata within the existing operation deadline.
- Acceptance cleanup recognizes creation that already rolled back, retains the
  original failure, and checks for unexpected remaining Windows displays.
- Save the exact CCD/GDI observations from a metadata timeout before creation
  cleanup, including source-match counts, raw flags, identities, and operation
  phase. Diagnostic failures cannot replace the original error or skip cleanup.
- Add a creation-time path for a verified new SudoVDA target that Windows starts
  as a virtual clone across adapters. Validate and apply a nonpersistent supplied
  CCD configuration while preserving other active paths and their modes, then
  verify independent GDI attachment and the requested signal mode. Generic
  discovery remains strict.
- Guard clone promotion against ownership loss and observed topology changes;
  later creation failures retain exact-GUID cleanup rather than replaying the
  cloned desktop. Document the remaining race with external display changes.
- Avoid polling a stale native target during creation cleanup after ownership
  continuity is lost; removal still addresses the journaled monitor UUID.
- Mitigate a confirmed multi-monitor regression: adding a second SudoVDA
  monitor displaced the first into a clone of the physical screen. Temporarily
  block second-monitor creation before driver mutation until complete topology
  reconciliation is implemented. Managed listing now reports degraded ownership
  records rather than failing when Windows GDI discovery is inconsistent.
- Record the first successful [native Windows acceptance run](docs/windows-acceptance-2026-11-07.md):
  one 1280 x 720 monitor at 60 Hz, active beyond the driver watchdog interval,
  and selective removal with the original physical desktop preserved.

This preview requires a separately installed SudoVDA driver. The basic native
cycle passed on Windows 11; concurrent monitors and remote-desktop behavior
still require hardware verification. macOS and Linux creation are not
implemented. No driver is bundled or installed automatically; the keeper
communicates only over localhost.

## 0.1.0 - 2026-11-07

- First installable Virtumon release.
- Terminal hero banner with automatic color and compact terminal support.
- Console entry point and `python -m virtumon` support.
- Read-only Windows display listing, including clean JSON output.
- Platform and display-discovery diagnostics.
- No runtime dependencies or network access.

Virtual monitor creation is planned and is not part of this release.

