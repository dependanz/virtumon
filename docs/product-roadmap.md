# Virtumon product milestones

Planning baseline: November 24, 2026. Dates follow the M0 review and native
feasibility results; none are committed here.

## Product scope

1. OS-recognized virtual displays alongside physical displays.
2. Virtumon's own remote-desktop host and client, initially proving the
   Windows-host/iPad-client/private-LAN use case.
3. Cross-app native window tab groups and arbitrary layouts, including a
   two-row, three-column layout and Chrome/Ableton windows sharing one region.

The remote product is Virtumon's implementation. OS APIs, codec libraries and
transport libraries are implementation dependencies; Sunshine or Moonlight are
not the host/client product or an integration requirement.

## Milestones and exits

| Product milestone | Deliverable | Exit evidence |
| --- | --- | --- |
| M0: baseline and feasibility | Credit existing work; establish shared contracts, platform approaches and reference measurements | Verified source/acceptance account, current native checkout snapshot, concrete platform proof results or explicit support exclusions, reference devices/network, and measured initial client feasibility |
| M1: common foundation | Stable display API around the existing manager; capability discovery; identity/coordinate/lifecycle contracts; diagnostics and profile schema | CLI and new API use one implementation, schema compatibility is preserved, ownership invariants hold, actual capabilities are reported, and affected native behavior is verified |
| M2: remote desktop | Own host/client: capture, encoding/decoding, authenticated connection, display selection and input | Physical and virtual displays controlled on the reference LAN; measured responsiveness, reconnect, permission failures and identity changes handled correctly |
| M3: window organization | Native window discovery, custom regions, cross-app groups and restoration | Two-by-three layout and Chrome/Ableton tab-group scenarios, focus/modal/minimum-size cases, display loss and restoration tested on supported backends |

M2 and M3 can advance in parallel against the M1 contracts. Their small
feasibility experiments belong in M0; they do not require completion of every
future display-backend or release-qualification task.

Integration and release-candidate work will combine these tracks with an
explicit platform matrix, reproducible installation and recovery checks.
Passing one milestone does not authorize publication or mark other platforms
as supported.

## Current status

**M0 is in progress.** The repository and historical Windows evidence review
is complete, fresh driver-free tests pass, and contract/platform drafts are
available. The user's native snapshot confirms a clean `MSI` checkout at
`1be811a`, SudoVDA readiness and a physical-only desktop with no managed
records. Full reference hardware/network details, platform proof runs and the
iPad client experiment are still outstanding. See the
[M0 record](m0-baseline-2026-11-24.md),
[shared contracts](product-contracts.md), and
[platform/measurement plan](platform-feasibility.md).

The older [Windows roadmap](roadmap.md) uses M1-M8 and M3-A-M3-E for its own
release gates. Those names remain historical identifiers; they must not be
read as completion of these new product milestones.
