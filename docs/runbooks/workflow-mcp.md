# Workflow discovery through MCP

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

These tools are read-only: preview/export do not create or enable definitions,
configure schedules, dispatch agents, start Temporal executions, or approve
human gates. Native workflow authoring, reviewed starts with recovery, execution
controls and independent human approvals remain tracked in #2283.
