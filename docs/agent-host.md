# Agent Host Protocol control plane

Astrolift hosts AHP 1.0.0 at `/app/ahp` (WebSocket). The protocol source is
pinned to VS Code commit `08d4889f9ec4a1685d257b9b95de036c8e1ce1e5`.
This endpoint attaches to existing tasks and boxes. Registered-agent dispatch
continues through the ordinary control-plane API.

## Authentication and permissions

Use the same session cookie or bearer API token as exec. A bearer client sends
`X-Astrolift-Organization` with the selected organization UUID. The endpoint
rechecks authentication, organization membership, token ceilings, and group
permissions on every write and poll. Revocation closes an existing connection.

The organization must enable `agent_live_attach`; the install administrator can
force it off with `AGENT_LIVE_ATTACH_ALLOWED`. Disabled task attachment answers
`ahp_available: false`, a reason, and `fallback: agent_task.watch`.

| Operation | Permission |
|---|---|
| List/read a task session, subscribe, replay | `agent.read` at its org/team/project scope |
| Steering, question answers, tool confirmation | `agent_task.send_input` |
| Cancel an active task | `agent.dispatch` |
| Box terminal read/input/resize | `agent_box.attach` |

A protocol connection grants no new role. Viewer writes receive an authoritative
rejection and an audit entry. Clients cannot alter tool input through approval.
Configured Zentinelle policy runs before an approval is shown and again when a
controller approves. Denied calls remain denied; an unavailable or incomplete
Zentinelle connection leaves the call blocked. A task without Zentinelle uses
its existing human-approval path.

## State and replay

Send `initialize` first, with `channel: "ahp-root://"`, a bounded `clientId`, and
`protocolVersions: ["1.0.0"]`. Task resources are `ahp-session:/<task-uuid>`;
the default chat is that URI followed by `/chat`. The root lists registered
providers and authorized box terminals; `listSessions` returns authorized task
summaries with pagination. Root lifecycle notifications refresh visible summaries.
Unsupported creation, launch, file, and provider operations return method errors.

Task callbacks retain the durable ordered event log. An organization authority
serializes projection across workers and owns every `serverSeq`. Snapshots and
projection writes share that lock. The journal supports reconnect without an
in-memory worker, and authorization is reapplied to every requested channel.
An actor/client/sequence reserves one exact controller action; a conflicting
retry is refused. The root metadata supplies `astrolift.nextClientSeq` so a
returning client can send its next new intent without colliding with old input.

The runner callback advertises `structured_event_protocol_version: 1`. Observed
turn and tool lifecycle data is validated and stored atomically with text,
approval, and question events. Tool input previews are bounded to 8 KiB, result
previews to 2,000 characters, and durations to seven days. Old text callbacks
continue to work. A task can expose only events and gates its harness actually
reports. Reducer fixtures are checked against the pinned upstream implementation.

## Box prerequisites and detach

Each actor/client has a private durable PTY output channel over the box's existing
tmux session. A live lease prevents two connections using the same identity
from duplicating PTY output. Independent clients can attach concurrently. Input
and resize wait for external delivery before their journal receipt becomes
accepted; an uncertain delivery is recorded and never automatically resent.

Before advertising availability, Astrolift checks the live running pod, a
RuntimeClass using the `runsc` handler, the agent label and full expected network
fence rules, an active task gateway identity, and a ready gateway Deployment.
A missing or unreadable control refuses attachment and names the prerequisite.
The cluster must actually install gVisor and a NetworkPolicy-capable CNI; see
[Agent runtime configuration](agent-runtime-class.md). This API check is not a
substitute for the operator's runtime and network acceptance probe.

Unsubscribe, disconnect, and Ctrl-C close the attachment only. They do not stop
the task, delete its Job, stop the box, or dispose of its tmux session. Explicit
cancellation remains a separate authorized controller operation.

## Verification

The PostgreSQL tests cover concurrent projection, replay, RBAC and token
revocation, two independent WebSocket clients, policy denial and held approvals,
terminal leases, delivery receipts, missing live controls, and structured event
validation. Run the upstream conformance check with a scratch pinned checkout:

```sh
AHP_SOURCE=/path/to/pinned/upstream node scripts/ahp/check-chat-projection.mjs
```
