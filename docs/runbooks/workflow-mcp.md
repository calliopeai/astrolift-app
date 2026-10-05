# Native workflows through MCP

An agent can discover native Astrolift workflow definitions and configured
workflows through `/api/mcp/v1/`, using an `alft_at_` bearer with `mcp:read` and
the owner's live `workflow.read` grants. `tools/list` filters capabilities by
these grants. `FEATURE_WORKFLOWS=false` removes the workflow tools and refuses
their calls. The generated contract is `backend/contracts/mcp-tools.json`.

| Tool | Result |
| --- | --- |
| `astrolift_list_workflow_definitions` | Native definition summaries and stage topology; optional `project_id` filter |
| `astrolift_get_workflow_definition` | Exact `definition_id` (UUID), reviewed revision and input-schema contract; `definition: null` when no visible row exists |
| `astrolift_list_workflows` | Configured workflows, bindings, inputs and trigger settings |
| `astrolift_preview_workflow_manifest` | Canonical Workflow TOML validation and structured stage preview or parse error |
| `astrolift_export_workflow_manifest` | Canonical TOML for an exact permitted `definition_id`; `ok: false` when no visible row exists |

Both list tools accept `search`, `limit` (1–200, default 50), and `cursor`.
They return the native page shape: `items`, `next_cursor`, `total_count`,
`page` and `page_size`. Pass `next_cursor` unchanged to continue, preserving
the filters; stop when it is null. Native malformed cursors restart the page.
Search is limited to 1,024 characters, cursors to 4,096, and preview TOML to
262,144 characters, within the gateway's request-body limit.

For example, call `astrolift_list_workflow_definitions` with a project UUID,
then inspect a returned definition's `guid` using
`astrolift_get_workflow_definition`. Its revision and input contract are the
same reviewed values served to the visual builder. To prepare a customization,
export that exact ID, edit the TOML, and preview the result. See the native
[chained workflow example](../examples/chained-agent-workflow.toml) and
[nested workflow example](../examples/nested-agent-workflow.toml).

Collection queries narrow rows to the credential and live organization,
project and team grants. Selected team/project headers cannot expand access.
Exact reads and exports resolve the actual definition owner and never replace
an ID with a matching slug. Global templates require organization-level access;
a team-limited credential cannot reach them even when its owner has a broader
role. Permissions and token validity are checked again on every request.

The discovery and preview tools are read-only: preview/export do not create or
enable definitions, configure schedules, dispatch agents, start Temporal
executions, or approve human gates.

## Reviewed execution and recovery

| Tool | Required token scopes and permission | Result |
| --- | --- | --- |
| `astrolift_start_workflow_definition` | `mcp:dispatch`, `workflow:trigger`; `workflow.trigger` | Native `{ok, errors, data}` with reserved execution and request IDs |
| `astrolift_get_workflow_start` | `mcp:read`; `workflow.read` | Original actor-owned request under `start`, or null |
| `astrolift_list_workflow_runs` | `mcp:read`; `workflow.read` | Native page of permitted definition runs |
| `astrolift_get_workflow_execution` | `mcp:read`; `workflow.read` | Exact execution, engine observation and cleanup state under `execution`, or null |
| `astrolift_list_workflow_execution_stages` | `mcp:read`; `workflow.read` | Exact execution identity with its native `stages` page, or null |
| `astrolift_control_workflow_execution` | `mcp:dispatch`, `workflow:trigger`; `workflow.trigger` | Native `{ok, errors, requested, execution}` |

First inspect the exact definition with `astrolift_get_workflow_definition`.
Review its revision, input-schema digest and intended inputs with the user.
Persist a unique request ID before calling `astrolift_start_workflow_definition`
with `definition_id`, `expected_revision`, `expected_input_schema_digest`,
`request_id`, optional `inputs`, and explicit `confirmed: true`. Confirmation
does not replace the live permission, token, target, ABAC or native review checks.
Changed definitions or undeclared inputs are refused by the native start service.

Save `data.execution_id`, `data.temporal_workflow_id`, and
`data.temporal_run_id` when present. A submitted start means accepted by the
engine; inspect the execution to determine whether it completed. Native failures
set MCP `isError: true` and preserve the complete native result in both
`structuredContent` and the JSON text content. A failed start can still have
`data` containing the reserved request/execution identity.

After a timeout or uncertain dispatch, call `astrolift_get_workflow_start` with
the original request ID. It can reconcile the original recorded identity with
the engine, but never starts a new execution. Retrying the original start with
the same request ID and unchanged inputs joins that same native reservation;
changed inputs conflict. Do not generate a replacement request ID to recover a
lost response. A different actor cannot recover the request just by knowing its
ID. An engine outage preserves the recorded state and explicitly reports the
observation error.

Run discovery accepts the standard `search`, `limit` and `cursor`, plus
`statuses`, `definition_slugs`, `project_slugs` and `started_by_me`. Exact
execution tools accept the returned execution GUID or native record ID.
Stage history uses `limit` (1–200, default 100) and an execution-bound `cursor`;
its `total_count` is null, and a cursor from another execution is refused.
The stage fields use the native GraphQL resolvers, including human-gate state,
child runs, loop causes and fan-out/collection ancestry.

To control an execution, pass the exact `execution_id`, `workflow_id` and
`run_id` with `action: cancel`, `terminate`, or `cleanup`. Termination requires a
reason; other actions refuse a reason. Cleanup is for a verified terminal
execution. A successful `requested` response acknowledges delivery, so inspect
again for engine-confirmed closure and cleanup progress. A delayed control for
one incarnation cannot select a newer execution that reuses the workflow ID.

Native definition import/configuration/bindings and independent human-gate
decision adapters remain tracked in #2283. Execution authority does not grant
human approval authority.
