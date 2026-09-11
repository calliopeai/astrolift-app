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

An overseer can discover agents, dispatch a task, retain its returned ID, poll
or list its runs, inspect results, and cancel when required. It uses these
ordinary scoped operations; hosting a chat UI does not grant additional
permissions or create a second scheduler. The current MCP launch tool supports
Task-family agents. Service-family agents retain their existing deployment
controls.
