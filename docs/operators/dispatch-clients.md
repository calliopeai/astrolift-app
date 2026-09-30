# Dispatch clients

The CLI, console and agent clients use Astrolift's existing Dispatch lifecycle.
The controller schedules work, receives runtime status and records outcomes.
A chat-based overseer can use the same API as another authenticated client.

## Discover and run work

The CLI supports the operator path:

```sh
astro agent workloads ls --json
astro agent ls --status running --json
astro agent dispatch triage --input '{"repository":"example/service"}'
astro agent logs <task-id>
astro agent cancel <task-id>
```

GraphQL exposes `agentFleet`, `agentTasks`, `agentTask`, `agentRuntimes` and
`agentTaskTransitionsSince`, alongside the launch/cancel and steering mutations.

Agent clients connect to the existing MCP endpoint, `/api/mcp/v1/`, with an
Astrolift API token and the normal MCP initialization/session handshake.
`mcp:read` plus `agent.read` reveals discovery tools; dispatch also needs
`mcp:dispatch` plus `agent.dispatch`. Tool discovery reflects both token scopes
and the user's current RBAC grants.

The same `mcp:dispatch` token scope permits box pod discovery and attachment
when the account holds `agent_box.attach` in the target organization. Normal
browser-approved `astro auth login` credentials include this scope; the IDE's
Connect Astrolift command uses that CLI flow. Token scopes remain a ceiling,
not an account grant. Read-only tokens and generic IDE/mobile/browser enrollment
cannot attach. Refresh uses the persisted session kind's current scope set;
no role change or administrator token is needed to repair the CLI scope mapping.

| Tool | Use |
|---|---|
| `astrolift_list_agents` | Discover registered agents and their source/package state. |
| `astrolift_list_runtimes` | Discover the configured runtime names and container images. |
| `astrolift_list_tasks` | List runs by `status`, `agent_slug`, and/or `project_slug`. |
| `astrolift_get_task` | Read one run's lifecycle, placement, result and failure. |
| `astrolift_run_agent` | Launch a registered task agent with `trigger_payload`. |
| `astrolift_cancel_task` | Stop a run through the existing cancellation path. |

For example, call `astrolift_list_tasks` with:

```json
{"status":"running","limit":50}
```

The response contains `tasks` and `next_cursor`. Supply that cursor with the
same filters for the next page; null means the end. Pages sort newest first
with a stable UUID tiebreaker and are capped at 200 rows. Restart at page one
to refresh live state; pagination walks the result set and is not an event
subscription. Task lists omit potentially large results; fetch an individual
task to read its result or failure.

Each task exposes its public ID, status, lifecycle timestamps, pod/namespace
and dispatcher placement when assigned. Dispatcher metadata is a recorded
snapshot with `last_heartbeat_at`, not a synchronous health probe. Credentials
and dispatcher endpoints are not included. Every page is scoped to the active
organization and any team restriction on the token, including shared-agent
read access. A cursor does not grant access to its originating tenant.

Discovery reflects permissions held on an app, project, team or organization;
every object operation checks its target. A selected context grants no extra
authority. Workloads inherit their app; tasks use their recorded project first,
then recorded team, then their live registered-agent app. Invalid recorded
ownership never falls through to the definition. Tasks without usable ownership
require organization authority, retaining access to historical runs for org
operators. These are the same ownership rules used by GraphQL and VNC.

Token permissions and team restrictions remain additional limits over role
bindings. A secondary-team app share needs `viewer` access for reads and
`deployer` or `owner` access for controls, plus the matching permission on that
team. Read-only tokens and shares cannot dispatch or cancel tasks. Package
resources follow the same agent boundary, and an ambiguous authorized agent slug
cannot select a different workload.

An overseer can discover agents, dispatch a task, retain its returned ID, poll
or list its runs, inspect results, and cancel when required. It uses these
ordinary scoped operations; hosting a chat UI does not grant additional
permissions or create a second scheduler. The current MCP launch tool supports
Task-family agents. Service-family agents retain their existing deployment
controls.

## Startup diagnostics

`AstroliftAgentTask.startupDiagnostic` and `AstroliftAgentBox.startupDiagnostic`
are nullable observations of their own scoped pod. The shape is `{ phase,
reason, message, podName, observedAt }`; `observedAt` is an ISO timestamp.
No observation yet means null. Phases are `Pending`, `Starting` (pod Running
but not ready), `Running` (ready), `Succeeded`, `Failed`, or `Unknown`.

A Pending pod with `reason: Unschedulable` exposes the scheduler condition's
bounded message, such as `2 Insufficient cpu`. Container waiting states expose
only their reason, excluding verbose configuration-bearing messages. Pending
is recoverable and does not imply agent inference. K8s tasks remain provisioning
until a pod is ready; an existing external Job prevents redispatch while waiting.

Successful probes refresh `observedAt`; clients should display it as an
observation, not assume continued freshness. Probe errors and a vanished pod
preserve the last snapshot. Ready observations clear reason/message. Task timeout
retains the snapshot and includes its message in `failureMessage` before deleting
the Job. Box restart clears the old snapshot. Existing task/box read permission,
token and tenant scopes govern these fields; no additional cluster grant is needed.

Apply migration `astrolift_agents.0040` before deploying the web/worker image.
Older clients can ignore the additive fields. Older servers do not expose them;
clients may retry an older query only for an unknown-field schema error, never an
authorization or network failure. Capacity changes remain an operator decision.
