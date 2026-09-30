# Dispatch clients

The CLI, console and agent clients use Astrolift's existing Dispatch lifecycle.
The controller schedules work, receives runtime status and records outcomes.
A chat-based overseer can use the same API as another authenticated client.

## Discover and run work

### Saved task backlog

`agentTaskBacklog(orgId: ID!, taskId: ID!)` returns null until a compatible runner
has observed successful native planning/task results. Otherwise it returns
`{ harness, sessionId, revision, updatedAt, items { id text status activeForm details } }`.
Harness values are `claude-code-cli` and `codex-cli`; item status is `pending`,
`in_progress`, or `completed`. `updatedAt` is the server's acceptance timestamp,
not evidence that the pod is still running. An empty `items` list is an explicit
cleared backlog, including after the pod ends. Reads use precisely the task event
permission, token ceiling, owner-operation policy and organization/team visibility.

Runners negotiate `task_backlog_protocol_version: 1` in the authenticated callback
response. The independent `backlog` request field contains `revision`, `harness`,
`session_id`, and `items`; item optional fields use `active_form` and `details`.
It never becomes a task event. The callback's `backlog_revision` acknowledges the
persisted revision. A retry must preserve the identical revision and snapshot;
older/conflicting revisions and changed native session identity are refused.
The runner must preserve state across follow-up turns, and must not restart a
producer against an existing backlog without a corresponding checkpoint.

Snapshots contain at most 256 unique items and 128 KiB of compact UTF-8 JSON.
Session identities are 1–128 ASCII letters/digits/underscore/hyphen; item IDs are
1–128 characters, text and active form at most 8,192 characters, and details at
most 16,384. Text is nonempty; NUL and invalid UTF-8 are refused. Revisions are
positive signed 32-bit integers. Invalid snapshots reject the entire callback,
including accompanying events and terminal result. No state is silently truncated.

Deploy the additive `astrolift_agents.0040_agent_task_backlog` migration before
the backend. Roll back code while retaining the nullable column. Older runners
continue to work and report no backlog; newer runners must not publish this
field until support is advertised. Clients against an older schema should
report backlog unavailable rather than infer a plan from prose or task events.

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
