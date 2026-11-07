# Changelog

## 0.2.0a1 - Unreleased Windows preview

- Named `create` and `remove` commands backed by SudoVDA's per-monitor GUID interface.
- Resolution, refresh rate, and placement relative to primary or managed displays.
- `list --managed` and JSON output with verified live geometry or explicit stale state.
- Detached per-user keeper with an authenticated localhost endpoint and driver heartbeat.
- Durable ownership journal, exact-GUID cleanup, and verification through Windows display APIs.
- `agent status`, `agent stop`, and driver readiness in `doctor`.
- Driver installation notes and an opt-in native Windows acceptance script.

This preview requires a separately installed SudoVDA driver. Native creation and
remote-desktop behavior still require verification on a Windows machine with that
driver. macOS and Linux creation are not implemented. No driver is bundled or
installed automatically; the keeper communicates only over localhost.

## 0.1.0 - 2026-11-07

- First installable Virtumon release.
- Terminal hero banner with automatic color and compact terminal support.
- Console entry point and `python -m virtumon` support.
- Read-only Windows display listing, including clean JSON output.
- Platform and display-discovery diagnostics.
- No runtime dependencies or network access.

Virtual monitor creation is planned and is not part of this release.

