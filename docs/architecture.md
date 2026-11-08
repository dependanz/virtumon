# Portable virtual-monitor architecture (design proposal)

## Mission

Virtumon is the monitor-creation counterpart to window libraries like GLFW:
discover outputs, request new OS-recognized virtual monitors, place and
configure them, then manage their ownership and lifetime with one common API.

This does NOT mean Virtumon creates application windows, implements a
streaming/capture protocol, or replaces the OS compositor/window manager.
An output attached to an existing user desktop differs from a separate
headless desktop session; handle those concepts with distinct capabilities.

All API examples below are **proposed**, not implemented in the current
0.2.0a1 preview.

## Candidate Python API

    from virtumon import VirtumonClient, MonitorSpec

    with VirtumonClient() as client:
        capabilities = client.capabilities()
        if not capabilities.create_virtual_monitor:
            raise RuntimeError("Virtual outputs unavailable in this session")

        primary = client.primary()
        work = client.create(MonitorSpec(
            name="work", width=1920, height=1080, refresh_hz=60,
            right_of=primary.id
        ))
        chat = client.create(MonitorSpec(
            name="chat", width=1080, height=1920, refresh_hz=60,
            right_of=work.id
        ))

        client.remove(chat.id)
        client.remove(work.id)

Closing a Python context manager closes the *connection*, not the monitors.
Monitors are destroyed only by an explicit owned-UUID removal. Otherwise a
simple exception or lexical scope exit could tear down a remote user's desktop.

## Semantic types

- MonitorSpec: requested name, geometry, refresh, placement constraints.
- MonitorHandle: durable Virtumon UUID, owning backend/session, verified
  live OS identity, status and actual mode.
- DisplayInfo: OS output listing, including physical or externally-owned
  virtual monitors; read-only unless ownership is established.
- Capabilities: create, multiple outputs, placement, custom modes, events,
  persistence, driver prerequisites, host/compositor limitations.
- Errors: UnsupportedPlatform, UnsupportedCapability, InvalidTopology,
  OwnershipLost, DriverUnavailable, TransientDisplayTopology, PartialRemoval.

Never use a Windows GDI device name (such as DISPLAY28) or a Linux output
label as the durable removal identity. Only the journaled Virtumon identity
authorizes destructive operations.

## Backend boundary

A backend-independent Protocol may expose:

    class MonitorBackend(Protocol):
        def capabilities(self) -> Capabilities: ...
        def enumerate(self) -> list[DisplayInfo]: ...
        def create(self, spec: MonitorSpec) -> MonitorHandle: ...
        def remove(self, owned_id: UUID) -> RemovalResult: ...
        def health(self) -> BackendHealth: ...

Core orchestrates desired topology, ownership, native readback verification,
journal/recovery and error handling; backends supply OS-specific operations.

- Windows: SudoVDA IOCTL UUIDs, CCD/GDI and SetDisplayConfig, per-user
  heartbeat keeper and driver watchdog.
- Linux: compositor-specific headless outputs (Sway/wlroots first) or a
  distinct DRM VKMS backend, with explicit desktop-session privileges.
- macOS: capability-gated, experimental implementation only if a safe,
  distributable mechanism exists; undocumented CoreGraphics APIs must
  not be marketed as broadly supported.

## Invariants

1. Physical output configuration and primary status are preserved across
   virtual monitor operations.
2. Revalidate native topology and all managed identities on each mutation.
3. Preserve non-owned outputs and source/target/signal modes; do not replay
   an old global snapshot as an unverified rollback.
4. Remove only an explicitly owned UUID; preserve records on partial failure.
5. No silent installation of drivers, certificates or elevated system policy.
6. A backend that supports enumeration but not creation reports the actual
   supported capability set.
7. Unexpected keeper/watchdog loss invalidates identity continuity;
   reconnect does not automatically authorize stale target IDs.

The Windows code should not be rewritten solely to resemble GLFW. Stabilize
this contract in Python first, then evaluate whether a native core/C ABI
would help additional language bindings.
