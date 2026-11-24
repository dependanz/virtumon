# Shared product contracts — M0 draft

Status: proposed semantic contracts for M1 review, not implemented SDK classes
or a final wire schema. They connect virtual displays, Virtumon's own remote
host/client and native-window organization while preserving the existing
Windows implementation. [Baseline evidence](m0-baseline-2026-11-24.md).

## Entities and lifetimes

| Entity | Meaning and minimum information | Lifetime / authority |
| --- | --- | --- |
| Host | Application host identity, build identity, available backends | Distinct from a hostname or network address |
| DesktopSession | Interactive OS login/seat/compositor context | Native resources belong to this session |
| Display | Physical, owned virtual, external virtual or unknown output; observed geometry, mode, scale and rotation when available | Enumeration grants no authority to remove/configure an output |
| OwnedDisplay | Durable Virtumon UUID, backend/session, current native binding, ownership epoch, desired and observed state | Journaled UUID remains the owned-display identity key; removal also enforces backend ownership and safety checks |
| DisplayArrangement | Desired relative positions/modes of explicitly selected displays | Planned against an observed topology revision; preserve non-owned configuration |
| WorkspaceProfile | Versioned desired display arrangement, regions, groups, app selectors and restoration policy | Persistent intent; contains no live handles, local IPC tokens or remote credentials |
| Window | Backend/session, transient handle plus process/lifetime discriminator, observed frame and supported actions | Revalidate before mutation; process IDs and native handles can be reused |
| WindowGroup | Ordered live members, selected member, anchor display, region, visibility/focus and restore policy | Groups unrelated native windows without requiring cross-process embedding |
| RemoteSession | Authenticated peer, explicit grants, target desktop/display set, streams and input-control state | Transport loss is separate from display ownership and keeper lifetime |
| Stream | Display binding, stream epoch, codec/mode, crop/rotation and frame sequence | Reconfigure or invalidate after binding/format changes |

External displays may lack a durable identifier. Report an ephemeral,
session-scoped identity and its validity explicitly rather than manufacturing
an identity that appears persistent. A saved workspace resolves display/app
selectors to current objects, shows ambiguity, and never silently substitutes
an unrelated window or display.

## Identity boundaries

Keep these distinct:

- Durable owned-display UUID and its current OS adapter/target binding.
- OS desktop session and keeper instance epoch.
- Ownership generation and topology revision.
- Remote connection/session identity and stream epoch.

The existing journal field `session_id` is a **keeper epoch**, not a remote
session. `Health.generation` invalidates ownership. Neither may be repurposed.
A healthy reconnected keeper does not revive stale ownership, and a recovered
network connection does not authorize old native bindings.

## Coordinate and mode contract

Every rectangle or point specifies its coordinate space and referenced object.

| Space | Intended use |
| --- | --- |
| Native desktop physical pixels | Existing Windows `Display` geometry, including negative origins |
| Native logical/compositor coordinates | Native APIs that use scaled points; conversion requires backend metadata |
| Display-relative coordinates | Anchoring regions or input to one identified display |
| Encoded-frame pixels | Stream crop, rotation, scaling and resolution |
| Client viewport coordinates | iPad/browser/native view, including letterboxing and zoom |

Use explicit transforms through these spaces and tie them to the topology
revision and stream epoch. Reject input with an obsolete binding or missing
transform. An input event outside the active image is not silently mapped to
another display. Do not reuse screen pixel values as logical coordinates.

Store requested mode separately from observed mode. The old state schema uses
integer requested refresh rates; a new observation may have fractional refresh.
Unknown scale, rotation or refresh is unknown, not an assumed value of one or
60. Backends may refuse unsupported requests and must report actual readback.

## Provider boundaries

| Provider | Responsibility |
| --- | --- |
| Display provider | Enumerate, validate/create/remove owned displays, native readback and health |
| Window provider | Enumerate permitted windows; query actions; place, select, restore and observe |
| Capture provider | Select a permitted display/window and produce frames plus geometry/cursor metadata |
| Input provider | Apply authorized input in the specified desktop and coordinate space |
| Remote service | Pair/authenticate peers, negotiate grants/capabilities, maintain stream/control sessions |
| Workspace coordinator | Resolve profiles and groups, plan changes, report per-object outcomes and recovery |

A backend capability result includes availability, reason, required permission,
native/session conditions, maturity and limits. Availability distinguishes
`available`, `unsupported`, `permission_required` and `temporarily_unavailable`.
Maturity is separate, including `experimental`: a capability can be available
and experimental at the same time. Capability queries and observation do not
implicitly start services or mutate displays.

Initial capability families: enumerate/create/configure/remove displays;
maximum owned outputs; physical/virtual capture; supported encoders/decoders;
keyboard/pointer/touch input; window enumeration/placement/activation;
profile persistence and startup behavior. Probe actual runtime availability.

## Operations, errors and concurrency

Commands identify their target, authority, request ID and expected relevant
revision. A coordinator serializes conflicting mutations per desktop and
revalidates just before applying them. Request IDs support bounded duplicate
suppression; do not replay uncertain destructive operations automatically.

Results distinguish success, partial success, unsupported capability,
permission denial, stale identity, topology change, native failure and timeout.
Each result contains requested and observed outcomes plus a diagnostic ID.
A failed group operation does not imply every member was restored.

Events carry an ordered sequence within a host/desktop epoch. A detected gap
requires a fresh snapshot. Display removal invalidates dependent capture/input
bindings; profiles retain unresolved intent for review rather than moving
windows into an arbitrary fallback region.

## Remote-session and window-group behavior

Remote view, input, window control and display mutation are separate grants.
The current authenticated `127.0.0.1` agent is local IPC and stays local;
its token and diagnostic fault methods are not the LAN protocol. Network
transport and pairing choices remain to be proven, using established security
and transport implementations rather than custom cryptography.

Closing a client disconnects its session and releases injected keys/buttons.
It does not implicitly remove persistent displays or stop their keeper. Any
session-owned temporary display policy must be explicit and identity-safe.
Revoked or unavailable input permission suspends control visibly.

A tab group is an ordered set of native windows sharing a region, with one
selected member. Placement, foreground activation and hiding/minimizing are
separate backend capabilities. Minimum sizes, modal dialogs, app exits,
manual moves and permission failures produce explicit results. Record original
frames/state so ungrouping or organizer shutdown can attempt verified
restoration without terminating applications or overwriting later user changes.

For a two-by-three layout, represent six regions as normalized fractions of a
display's usable work area, then convert through its current coordinate map.
The same mechanism supports custom region sizes and anchored tab groups.

## M1 compatibility and acceptance

1. Wrap `MonitorManager` and existing Windows providers; keep CLI and SDK on
   the same lifecycle implementation.
2. Preserve CLI output and schema-1 ownership semantics. Schema 1 currently
   recognizes only `sudovda`; adding a backend requires a deliberate migration
   and recovery design, not an unversioned new dictionary value.
3. Preserve exact-UUID cleanup, journal retention on uncertain failure,
   physical-primary preservation and permanently stale mappings after loss.
4. Keep keeper/display lifetime independent of API context-manager lifetime
   and remote connection lifetime.
5. Add build/source identity to diagnostics so different `0.2.0a1` builds can
   be distinguished. Compatibility version and implementation identity differ.
6. Meaningful acceptance covers API/CLI parity, stale-reference rejection,
   capability refusal, schema migration/roundtrip, partial outcomes and
   coordinate transforms under negative origins, scaling and rotation.
7. Recheck affected native scenarios when implementation changes warrant it;
   retain historical passes with their original source and environment.

Language for future high-throughput media components, transport, codecs,
iPad UI framework and service-startup packaging are open implementation
choices. This draft does not require rewriting the Python display backend.
