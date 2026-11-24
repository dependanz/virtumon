# Platform feasibility and responsiveness — M0

Status: API documentation reviewed November 24, 2026. Native implementation
spikes remain open except the already-recorded Windows display work.
This document is a proof plan, not a platform-support or latency claim.

## Platform matrix

| Target | Documented path / existing evidence | Proof needed before support is claimed |
| --- | --- | --- |
| Windows host | Existing SudoVDA display lifecycle; Microsoft's IddCx model supports virtual/network displays [1]. Desktop Duplication exposes active-display frames [2]. `SendInput` and `SetWindowPos` provide input and window operations [3,4]. | Capture physical and SudoVDA displays; prove frame/cursor mapping, elevated-app limits, desktop changes, mixed DPI, modal windows and minimum sizes. |
| macOS host | ScreenCaptureKit streams selected screen content [5]. Accessibility APIs expose supported attributes/actions and trust checks [6]. | Capture and permitted window/input operations; permission denial/revocation. A supported, distributable public virtual-display creation path remains unverified. |
| Linux Wayland host | ScreenCast/RemoteDesktop portals supply permitted PipeWire capture and input. `AvailableSourceTypes` may advertise `VIRTUAL` [7,8]. | Probe a named compositor and portal backend. An optional protocol capability does not establish installed support or persistence. |
| Linux Wayland organizer | Core Wayland isolates other clients' surfaces and global positions [9]. | Prove compositor-specific enumeration, placement, activation and restoration. Capture/input portal access alone is insufficient. |
| Linux X11 host/organizer | RandR supplies output discovery and dynamic configuration [10]. | Specify X server/window manager and driver; separately prove capture, input, window actions and virtual-output creation. RandR configuration alone does not prove a new usable output can be created. |
| Windows/macOS/Linux clients | Candidate viewer/control roles with shared remote contracts. | Prove each selected client's decoder, presentation and input stack; host API feasibility is not client acceptance. |
| iPadOS client | Candidate native decode path: VideoToolbox [11]. This is the initial mobile viewer/control target. | Actual-device decoding/presentation, touch, keyboard/pointer, rotation, display selection and reconnect. No iPad host or cross-app organizer support is asserted. |

The largest unresolved display/window questions are macOS public virtual-display
creation and Wayland cross-app window control. Classify unavailable capabilities
explicitly. Keep a virtual output inside a new headless session distinct from
an extension of the user's existing desktop.

Windows input injection is limited by process integrity/UIPI [3]; successful
ordinary-app control does not prove elevated-app or secure-desktop control.
macOS accessibility functions can return unsupported, disabled or unresponsive
errors [6]. These are observable capability/permission outcomes.

The Wayland portal returns stream geometry in compositor coordinates, which
may differ from pixel coordinates [7]. Providers must supply transforms rather
than assume stream dimensions equal desktop dimensions. Query portal versions
and capabilities at runtime instead of assuming the newest documented fields
exist everywhere.

## Reference setup record

Proposed first lane: **Windows host → own iPad client over a private LAN**.
Use a wired host and wireless iPad when those links are available, and record
the actual configuration. A particular router purchase is not a prerequisite.

| Field | Current knowledge | Required reference record |
| --- | --- | --- |
| Host | Current user-supplied snapshot: computer `MSI`, Windows 11 AMD64; one 2560×1600 physical primary at (0,0) | Exact hardware model, OS build, CPU/GPU, GPU driver and selected desktop session |
| Display backend | Current snapshot: SudoVDA available, protocol 0.2.1 / three-second watchdog; no saved managed monitors | Driver package/version and adapter identity for the reference record |
| Software | Clean Windows checkout `1be811a`; same runtime source as reviewed `86d9eca`; installed conda module reports `0.2.0a1` / Python 3.13.5 | Fingerprint installed build; version and import path alone do not identify exact installed source |
| Client | iPad is the requested starting client | Exact model, iPadOS version, screen refresh, attached keyboard/pointer and build |
| Network | Private-LAN use case | Router/AP, link types/speeds, Wi-Fi band/channel, measured RTT/loss and competing traffic |
| Stream | No selected, measured pipeline yet | Codec/profile, encoder/decoder, resolution, cadence, bitrate and queue policy |

Initial test modes should include the existing landscape and portrait virtual
display use cases (1920×1080 and 1080×1920), plus physical-display capture.
Use 60 fps as an initial requested test point where supported, and record
actual capture/decode/presentation cadence. Neither 60 fps nor a chosen
resolution establishes responsiveness.

## Small proof tasks and acceptance

| ID | Task | Required result |
| --- | --- | --- |
| P0 | Read current Windows checkout/package/desktop state | Complete from the user's [MSI snapshot](evidence/m0-2026-11-24-native-snapshot.md); no mutation or installed-byte verification implied |
| P1 | Windows capture plus input | Own local capture harness identifies physical/virtual display; actual frame/cursor geometry; controlled test-app input with explicit authorization; release stuck keys on disconnect |
| P2 | iPad media/input spike | Own client receives a test stream, decodes/presents on the actual device and returns touch/keyboard/pointer events; record supported formats and failures |
| P3 | First complete LAN loop | Own Windows capture/encode → own iPad decode/display → own Windows input; record end-to-end response and stream switching; reconnect does not revive stale targets |
| P4 | Windows organizer spike | Chrome windows and Ableton occupy one anchored group; switching, modal dialogs and restore behavior observed; two-by-three layout respects minimum sizes and work area |
| P5 | macOS capability spike | ScreenCaptureKit and accessibility permission paths exercised; public virtual-display approach proven or explicitly left unsupported |
| P6 | Named Wayland/X11 backend spikes | Record compositor/server and report output discovery, virtual creation, capture, input and window-control outcomes separately; success in one does not establish the others |

These experiments establish a viable path, not complete M2/M3 features.
Retain small reproducible harnesses and structured receipts with source/build,
device, parameters, observed results and limitations. A feasibility blocker
needs an explicit product scope decision before M0 is closed. Do not fabricate
native success from mocked providers or a video-only test pattern.

## Responsiveness measurement protocol

This is an engineering measurement proposal. Thresholds and milestone dates
follow the first complete measured pipeline.

1. Freeze and record the reference configuration above. Run both basic
   interaction and sustained-motion/load workloads against physical and
   virtual displays. Keep test duration and sample counts explicit.
2. Assign input and frame trace IDs. On the host, measure input receipt to
   injection, capture readiness to encode completion, and packet handoff.
   On the client, measure arrival to decode completion and presentation
   submission. Record queue age, frame drops and discontinuities.
3. Use monotonic clocks for intervals within each device. Do not subtract
   unrelated host/client timestamps without a measured clock offset, drift
   and uncertainty. RTT is useful; RTT/2 is a symmetry assumption, not an
   observed one-way delay.
4. Measure input-to-photon independently: record an instrumented input/LED
   and the responding client screen in one high-speed-camera view. A host
   test app changes a visible patch after the input. State camera temporal
   resolution and input-indicator uncertainty. Software presentation
   submission alone does not establish when a pixel became visible.
5. Report local cursor feedback separately from remote application response.
   Local cursor drawing can improve perceived responsiveness but cannot
   establish low remote content latency.
6. Report p50/p95, sample count, run duration, frame cadence, dropped frames,
   freezes and reconnect time. Include the raw samples or a reproducible
   receipt. Measure single-display selection before increasing simultaneous
   stream count, then characterize the additional load.

No latency number has been measured or promised in this review. The existing
driver-heartbeat timestamps are unrelated to capture/encode/network/decode
or input-to-photon latency.

## Primary documentation

1. [Microsoft — Indirect display driver model](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/indirect-display-driver-model-overview)
2. [Microsoft — Desktop Duplication](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/desktop-duplication-api)
3. [Microsoft — SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
4. [Microsoft — SetWindowPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos)
5. [Apple — Capturing screen content in macOS](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)
6. [Apple — AXUIElement APIs](https://developer.apple.com/documentation/applicationservices/axuielement_h?changes=latest_ma_2)
7. [XDG — ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
8. [XDG — RemoteDesktop portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)
9. [Wayland — Protocol and model](https://wayland.freedesktop.org/docs/book/Protocol.html)
10. [X.Org — libXrandr](https://www.x.org/libraries/libxrandr/)
11. [Apple — VideoToolbox](https://developer.apple.com/documentation/videotoolbox)
