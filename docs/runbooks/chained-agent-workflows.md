# Chained agent workflows

Astrolift agent workflows are ordered pipelines. `WorkflowDefinition.states`,
`transitions`, and `model_label` belong to the older generic Django object
state machine; they do not select an LLM and do not control an agent pipeline.
An agent's model is selected by its environment/runtime package.

The canonical pipeline is a `WorkflowDefinition` with ordered
`WorkflowStage` rows. The TOML example in
[`../examples/chained-agent-workflow.toml`](../examples/chained-agent-workflow.toml)
maps one `[[stage]]` table to one row:

For an organization-owned agent repository, commit definitions under
`workflows/*.toml` (nested paths under `workflows/` are also accepted). Agent
repo registration, manual resync, and source push reconciliation parse and
upsert those definitions after the repo's agents are reconciled. The
`backend/workflows/catalogue/*.toml` directory is reserved for platform-owned
built-in templates and is not the tenant configuration path.

| TOML | Persisted field | Runtime meaning |
|---|---|---|
| `agent` | `agent_ref` and, when available, `agent_definition` | Organization-local agent workload. |
| `environment_spec_slug` | `environment_spec_slug` | Environment recipe frozen onto the `AgentTask`. |
| `skills` | `skill_refs` | Ordered org/global skill overlays added to a task-specific immutable Brief. |
| `prompt` | `prompt` | Agent instruction overlay, or the approval question on a human gate. |
| `output_key` | `output_key` | Key used in the run's `named_outputs` map. Defaults to `stage_<order>`. |
| `workflow` | `workflow_ref` | Visible child definition invoked by a `kind = "workflow"` stage. |

A configured `Workflow.stage_bindings` may override a definition without
editing it. Bindings are keyed by stage order:

```json
{
  "0": {
    "agent_workload_id": "<workload-guid>",
    "skill_refs": ["emr-evidence", "customer-runbook"],
    "params": {
      "environment_spec_slug": "emr-triage-prod",
      "prompt": "Investigate this tenant using the production runbook.",
      "output_key": "tenant_evidence"
    }
  }
}
```

Resolution precedence is binding override, then stage default. The executor
freezes the resolved packet before dispatch. A local `agent` slug can therefore
be imported before its workload exists and resolves when a matching workload is
later registered in the run's organization.

Each agent receives its immediate predecessor at the top level for backward
compatibility and an explicit `_astrolift_workflow` object:

```json
{
  "_astrolift_workflow": {
    "input": {"issue": "EMR-123"},
    "previous": {"classification": "product_bug"},
    "outputs": {
      "evidence": {"reproduced": true},
      "classification": {"classification": "product_bug"}
    },
    "stage": {"order": 2, "output_key": "report"}
  }
}
```

The workflow result retains the ordered `outputs` list and also exposes
`named_outputs` plus `final_output`. Agent-stage output is the callback's real
structured result; terminal status and execution IDs remain metadata on the
ordered output record.

## Nested workflows

Use a workflow stage to package a reusable series of steps inside another
pipeline:

```toml
[[stage]]
kind = "workflow"
workflow = "emr-triage-patch"
output_key = "patch_review"
on_failure = "retry"
timeout = 1800
```

The parent passes the same composed stage packet shown above as the child's
trigger input. The child's `final_output` becomes the value of the parent's
`output_key`; its credentials, environment specs, skills, and stage bindings
are resolved inside the child and are never inherited from the parent.

Nested invocations are first-class Temporal child workflows and first-class
`WorkflowRun` rows. The run stores its parent run, invoking stage execution,
and nesting depth, so the Observe graph links directly to the child run.
Cancelling or terminating a parent propagates to its active children.

Repository reconciliation is atomic and order-independent: a parent manifest
may sort before its child manifest in the same repository. Reconciliation and
interactive edits reject missing/invisible children, cross-project references,
cycles, and nesting deeper than eight levels. A project definition may invoke
another definition in the same project or a reusable org/global definition
with no project; it cannot reach into a different project packet.

## Execution status reconciliation

Temporal remains authoritative for execution state. The worker registers
`astro-workflow_run_reconcile` every 60 seconds to repair the database mirrors
and their configured workflow instances. It is included in the default active
allowlist; installations overriding `ASTROLIFT_ACTIVE_SCHEDULES` must include
`workflow_run_reconcile` to enable it. Each tick checks up to 40 active,
open-stage, or pending-cleanup runs and audits up to 10 terminal mirrors. Independent rotating
cursors with fixed cycle bounds prevent old history or continuous new arrivals
from starving active runs.

Every lookup pins both Temporal workflow ID and execution run ID. The response
must match both IDs and the definition workflow type. The database write also
rechecks organization, identity, and version under a row lock. A missing run ID,
unavailable Temporal service, or concurrent database change leaves the
record unchanged for a later sweep. `CONTINUED_AS_NEW` is not inferred to mean
completion. Terminal observations require Temporal's actual close timestamp.

`NOT_FOUND` for the exact execution marks its Temporal history expired and logs
once at INFO. A running mirror settles as `expired` (History expired), an
unknown outcome, rather than success or failure; its end timestamp is the time
expiry was observed, not an inferred execution close time. Already terminal
mirrors keep their recorded status, end timestamp, result and failure. The nullable
`WorkflowRun.temporal_history_expired_at` marker (migration
`astrolift_operations.0027`) excludes these mirrors from subsequent Temporal
lookups and audit sweeps. Open stages settle, and owned-task cleanup continues
locally on later ticks until complete without querying the missing execution.
Apply the additive migration before rolling workers; the previous release can
run against the expanded schema.

An authoritative observation repairs an incorrectly terminal configured record
as well as an unfinished one. Abnormal closure settles pending/running stage
records while preserving completed stage output. After a confirmed terminal
observation it also retries cleanup for one explicitly owned AgentTask. A
normal workflow finalizer attempts up to five tasks; a rotating task cursor
prevents a persistently failing deletion from starving the rest.

The tick returns `evaluated`, `repaired`, `unchanged`, `skipped`, and `errors` in
Temporal and logs the same counters. Individual RPCs have a three-second timeout,
concurrency is capped at ten, and the sweep has a 40-second deadline inside a
50-second activity with one attempt. A later scheduled tick retries unresolved
records. Database writes use PostgreSQL lock/statement timeouts. Disabling the
Temporal runtime also disables reconciliation.

## Agent task cleanup

Before external creation, each new task saves its backend, namespace, cluster
GUID and endpoint, or Docker daemon identity, plus its deterministic resource
name in `dispatch_target` (migration `astrolift_agents.0024`). Spawn and Stop
share a PostgreSQL session advisory lock across worker processes. An in-flight
spawn returns pending cleanup; a later sweep uses the saved resource name even
if the worker died before saving the spawn response. Delayed activities cannot
open a new stage or task on a closed workflow.

Cleanup requires the exact workflow run and organization, an explicit
stage-to-AgentRun-to-AgentTask link, and exclusive ownership of that AgentRun.
It never selects tasks through workload names or pod prefixes. A missing or
changed saved target is reported as an error instead of using the current
default cluster or Docker daemon. Legacy tasks without a saved target require
placement recovery before automatic cleanup.

Kubernetes deletion verifies the task label, uses the Job UID as a precondition,
and requests foreground deletion. Completion requires absence of both the Job
and its dependent pods. Docker deletion verifies the task label, removes the
container by immutable ID, and confirms absence on the original daemon. An
unavailable provider remains an error. Completed tasks retain their resources
for the existing log-retention policy; failed or cancelled tasks can still have
resource cleanup retried without changing their original outcome.

The run's existing `failure.task_cleanup` JSON contains `status` (`pending`,
`failed`, or `completed`), `remaining`, and up to 20 task-specific errors. This
does not overwrite the workflow's terminal status, original failure message, or
close time. A workflow can be cancelled while resource deletion is pending;
operators must check cleanup status before treating Stop as fully settled.
The task receives its own completed cleanup receipt only after confirmed
deletion. No GraphQL contract changes are required for these existing JSON
fields.

## Exact execution observation and control

`workflowExecution(executionId: ID!)` accepts the numeric `WorkflowRun` ID
returned by dispatch or that record's GUID. It supports configured workflows
and direct definition runs without scanning a recent-run list. A configured
`WorkflowInstance` primary key is a different identifier. The query requires
`workflow.read` in the owning definition's project (or organization for a
template); deleted, foreign-organization, and non-definition runs are absent.

The response includes `guid`, `recordId`, `organizationGuid`, `definitionSlug`,
both Temporal IDs, `status`, `isTerminal`, times, the original `failure`,
`taskCleanup`, and `observationError`. Temporal observations must match both
IDs and workflow type; closure also requires an actual close timestamp. Reads
return the authoritative observation without rewriting the database. Missing
identity or unavailable history retains recorded state with an explicit error.
Clients must not interpret that unverified state as fresh proof of completion.

`workflowExecutionStages(executionId: ID!, limit: Int! = 100, after: String)`
uses the same organization/project read permission and exact record lookup.
It returns the execution GUID, record ID, organization GUID, both Temporal IDs,
and a `stages { items, nextCursor, totalCount }` page. `totalCount` is `null`;
pages default to 100 rows and cap at 200. Follow `nextCursor`
until null. The cursor belongs to this organization and Temporal incarnation;
invalid or mismatched cursors fail instead of restarting the list.

Stage attempts are ordered by creation time and GUID, newest first. New attempts
stay ahead of an existing cursor; a refresh starts a new inspection. Each item
includes approval state/note, attempt number, errors, and linked agent/child runs.
Soft-deleted execution rows are excluded, but deleted stage definitions retain
their historical metadata. Reading these recorded rows does not contact Temporal
and is not evidence of live execution status. Clients must match the execution
identity on every page and keep closure/cleanup observation separate.

`taskCleanup` reports `not_requested`, `pending`, `failed`, `completed`, or
`not_required`, plus `remaining`, `errors`, and `retryable`. Execution closure
and resource deletion are separate facts. A terminal execution with pending or
failed cleanup must remain visible to operators until cleanup is resolved.

`controlWorkflowExecution(executionId:, workflowId:, runId:, action:, reason:)`
requires `workflow.trigger` in the same scope. Callers must supply both original
Temporal IDs; `action` is `cancel`, `terminate`, or `cleanup`. Termination requires
a nonblank reason. Controls first describe the exact execution, then recheck the
database identity and version under a lock. A closed execution acknowledges a
repeated Stop without selecting a newer incarnation of the same workflow ID.

Cleanup requires authoritative closure and attempts one explicitly owned task
through the reconciliation path described above. Provider failures are durable
and remain retryable. The mutation's `ok` / `requested` acknowledge acceptance;
its `execution` payload reports current closure and cleanup independently. A
response with pending cleanup must not be displayed as fully stopped. Exact
Temporal reads and controls bound connection establishment to five seconds and
the individual RPC to three seconds; unavailable services never imply success.
