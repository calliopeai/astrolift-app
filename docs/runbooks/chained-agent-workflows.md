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
| `max_attempts` | `max_attempts` | Initial dispatch plus retries for an agent or nested-workflow stage; an integer from 1 to 20, default 3. |

`on_failure = "retry"` uses the stage's `max_attempts` count. A count of 1
allows the initial dispatch and no retry. Exhausting the count fails the run;
the stage cannot retry indefinitely. This is a dispatch attempt bound, separate
from a review loop's rounds. Other failure policies retain their own semantics:
`skip` proceeds after failure and `escalate` waits for an operator within the
stage timeout. The attempt count is included in the reviewed definition revision
and frozen plan, so editing a definition does not change an already reserved run.
Existing Temporal histories and earlier frozen plans retain their three-attempt
behavior.

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

## Bounded return edges and review rounds

A workflow can return to an earlier stage using `back_edge`. Both endpoints
must have explicit, unique `output_key` values; positional return targets are
refused. One outgoing return edge is allowed per stage:

```toml
[[stage]]
kind = "agent_dispatch"
agent = "writer"
output_key = "draft"
on_failure = "retry"
max_attempts = 4

[[stage]]
kind = "human_gate"
output_key = "review"
prompt = "Approve this draft?"
timeout = 86400
back_edge = { to = "draft", when = "gate_rejected", max_rounds = 5, on_exhausted = "escalate" }
```

`max_rounds` includes the initial pass and must be an integer from 1 to 20.
Rejection runs the producing segment again, then opens a new review gate.
Approval continues forward. Intermediate retries do not consume review rounds.
`stage_failed` applies after an agent or nested workflow exhausts its configured
attempts. `output_equals` compares a dotted object `path` to a JSON scalar
`value`; missing, nonfinite or incompatible data stops as unavailable.
The exhaustion policy is `fail` or `escalate`. Escalation waits for the existing
operator-clear signal within the stage's timeout and never opens another round.

Each edge has a lifetime budget that overlapping edges cannot reset. The
reviewed plan also refuses more than 1,000 stage visits or 500 execution units,
counting attempt budgets and potential fan-out branches. Dynamic fan-out reserves
its full 50-branch ceiling and refuses missing items or oversized collections.
Failure or missing branch results stop as incomplete instead of producing an
empty successful aggregation.

Agents receive feedback under `_astrolift_workflow.loop.feedback`, alongside
`edge`, `max_rounds`, `edge_round` and the global `round_number`. Named outputs
from the segment being revised are removed before its next pass. The normal
prior result remains the chained input; rejection feedback is passed separately.
`workflowStageExecutions` records `roundNumber`, typed `causedBy`, `attemptNumber`,
`fanoutStageId`, `fanoutParentExecutionGuid` and `fanoutIndex`. Global rounds
increase whenever any return edge fires; `causedBy.edgeRound` identifies one
edge's progress toward its `maxRounds` ceiling. Branches retain their actual
start times and share an explicit parent execution.

TOML and repository YAML retain the `back_edge`, `max_attempts` and `output_key`
fields. Plans are frozen with the reviewed revision before dispatch; later stage
edits cannot change that run's budgets. Temporal's
`workflow-bounded-back-edges-v1` patch preserves forward-only execution for older
histories. Historical rows default to round 1 without a cause and cannot prove
past loop or branch attribution. Existing organization copies of review templates
need an explicit reviewed edge before a new bounded run; the platform `rasd`
and `moderate` templates now declare three-round review edges.
`supervisor_worker` and `advisor` are unsupported executor patterns and are
refused on new runs.

In the Builder's Stages editor, **Execution bounds** separates maximum attempts
from the return edge's maximum rounds. Give the producer and review stage explicit
output keys, enable the return, then choose the earlier key and the trigger.
Output-field conditions accept a JSON string, number, boolean or `null`; invalid
JSON never submits a stage mutation. The Code view exports the same authored
limits and return targets. The run timeline uses recorded rounds and causes,
shows an edge's round ceiling, and exposes branch start times. Reordered stage
visits or repeated agent names do not establish a loop or branch identity.

New authoring choices exclude `supervisor_worker` and `advisor`; their legacy
records remain readable. Creating or cloning another definition or configured
workflow with either unsupported pattern is refused instead of labelling an
ordinary sequence as a supervisor or advisor implementation.

TOML has no `null` literal. A condition comparing an output field to JSON `null`
exports as `back_edge_json = '<JSON object>'`; importing that representation
restores the same typed condition. Supplying both `back_edge` and
`back_edge_json` is refused. The YAML and GraphQL forms use their native `null`.


## Imported bounded control loops

Flowise `loopAgentflow` node version `1.2` maps to an explicit checkpoint return
control when it is the only Loop, at the end of one connected sequential track.
Its integer `maxLoopCount` (default 5, accepted range 1–20) becomes `max_rounds`,
including the initial pass. The imported contract uses `when = "always"` and
`on_exhausted = "continue"`; native review loops still default to failure and
may explicitly escalate. Every pass publishes the source node ID, cap, optional
`fallbackMessage`, and source control content. At the cap, `content` contains the
nonempty fallback or the source default completion message, then the workflow
completes. Source target and label remain separate from the canonical return
output key and are preserved through TOML/YAML export, review and execution.

The mapping is based on the pinned upstream [Loop v1.2 component](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/components/nodes/agentflow/Loop/Loop.ts)
and [AgentFlow scheduler](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/server/src/utils/buildAgentflow.ts).
The importer refuses source state updates, state-bearing starts, unresolved
fallback variables, forward routes after the Loop, conditional/parallel tracks,
multiple Loops, unknown node versions and unresolved graph cycles. Ordinary
model/tool/configuration gaps remain visible for operator review; importing the
control does not install the original model or reproduce its agent configuration.

The visual editor keeps imported target, trigger and cap-completion policy
read-only, and allows the round cap to be changed without discarding the source
output contract. Changing that source mapping requires editing the native
manifest; mismatched source node and canonical return target are rejected.

## Serial collection bodies

A `collection` stage declares a forward body ending at an explicit output key.
Its `max_items` is required, from 1 to 50. Choose exactly one of `items`, an
ordered array of JSON records, or `items_path`, a dotted field in the preceding
output (the dispatch input for an initial stage). An empty array completes with
zero items. Missing data, non-record items and data exceeding the cap are
unavailable and dispatch no body stages; data is never truncated.

```toml
[workflow]
slug = "serial-review"
name = "Serial item review"
pattern = "chained"

[[stage]]
order = 0
kind = "collection"
output_key = "item_each"
iteration_json = '{"max_items":4,"items_path":"items","body_end":"review"}'

[[stage]]
order = 1
kind = "agent_dispatch"
agent = "worker"
output_key = "draft"
on_failure = "retry"
max_attempts = 2

[[stage]]
order = 2
kind = "human_gate"
output_key = "review"
timeout = 3600
```

Configure the agent binding using the definition's existing agent picker or
reviewed binding controls before starting. The collection engine forwards that
exact workload binding, environment recipe, skills and prompt to each item;
matching a slug in another app does not substitute its workload. A `workflow`
body stage similarly uses the exact bound nested definition. Every body must be
a contiguous forward range of agent, nested-workflow, checkpoint, human-gate or
record-format stages. Nested collections, parallel body stages and edges that
enter or leave a body range are refused. A bounded review return entirely inside
the body is supported; its local rounds and attempts apply independently to each
item. Returning to the outer collection repeats the collection under the outer
edge's lifetime cap. The reviewed plan multiplies item counts, attempts and
return visits before any dispatch and enforces the global execution budget.

Each item runs in a separate durable child workflow, in order. Its exact parent
execution, zero-based item index and child workflow identity are persisted.
Approving a gate with its execution GUID signals that item, after the usual
organization, actor and approval checks. The next item waits for the current
body to finish. The timeline displays recorded one-based item labels separately
from parallel branches and review rounds; it does not infer them from stage names.

The parent output includes `results` in input order, `item_execution_ids`,
`finished_count`, `complete` and the frozen `collection` binding. Complete means
every body finished under its authored failure policy: a skipped failed agent or
a cleared escalation retains that outcome and does not become a successful agent
result. A failed body stops later items and exposes `complete: false`. Abort or
cancellation closes the outstanding run and executions. Inputs and collected
outputs must be finite JSON, with string object keys, at most 32 nesting levels,
16,384 nodes and 256 KiB encoded size; an oversized aggregate fails explicitly.

The Stage editor exposes the maximum item count and forward body end, preserving
literal source records during cap edits. Dynamic inputs expose `items_path`.
TOML uses `iteration_json` to preserve JSON `null`; YAML and GraphQL retain the
same typed contract. Imported target bindings are read-only in the visual editor.

### Supported Langflow collection import

The supported mapping is based on Langflow commit
[`f9b283243d2fdd8502cb4ffd606c3058cff5017e`](https://github.com/langflow-ai/langflow/tree/f9b283243d2fdd8502cb4ffd606c3058cff5017e):
[CreateList](https://github.com/langflow-ai/langflow/blob/f9b283243d2fdd8502cb4ffd606c3058cff5017e/src/lfx/src/lfx/components/processing/create_list.py),
[Loop](https://github.com/langflow-ai/langflow/blob/f9b283243d2fdd8502cb4ffd606c3058cff5017e/src/lfx/src/lfx/components/flow_controls/loop.py),
[the isolated Loop body scheduler](https://github.com/langflow-ai/langflow/blob/f9b283243d2fdd8502cb4ffd606c3058cff5017e/src/lfx/src/lfx/base/flow_controls/loop_utils.py),
[Parser](https://github.com/langflow-ai/langflow/blob/f9b283243d2fdd8502cb4ffd606c3058cff5017e/src/lfx/src/lfx/components/processing/parser.py),
and [TypeConverter](https://github.com/langflow-ai/langflow/blob/f9b283243d2fdd8502cb4ffd606c3058cff5017e/src/lfx/src/lfx/components/processing/converter.py).

Import requires one exact `CreateList` collection edge into `Loop.data`, an
`item` edge into `Parser.input_data`, and `Parser.parsed_text` feedback into
`Loop.item`. An optional `Loop.done` edge may feed TypeConverter's explicit JSON
conversion without automatic parsing. Handle node IDs and input/output names
must agree, including Langflow's encoded handle representation. A default
`max_items` of 50 becomes explicit in the native contract; an optional source
`astrolift_max_items` field can lower it. Source component code, when present,
must match the pinned built-in implementation.

The importer preserves ordered text records, plain named Parser fields, missing
fields as empty strings and the ordered final record table. A record includes
the deterministic execution timestamp matching the pinned Message data shape.
TOML/YAML export and re-import preserve the literal records, cap, body binding,
pattern and separator. The native engine then actually executes each Parser body;
import does not merely flatten the source feedback cycle into a stage list.

Custom component code, state, routers, nested Loop graphs, Stringify mode,
unresolved variables and other source body component types remain unsupported
and are rejected explicitly. Broader Langflow agent/workflow source translation
is not certified by the native agent/workflow body support. The Langflow runtime
itself is not invoked by these import tests. Conditional-router message outputs
and branch-exclusion state remain outside this supported mapping.

The collection proof uses real PostgreSQL 15.15 and Temporal: source import and
authoring round-trip, ordered body completion, exact nested-workflow binding,
gate delivery through the actual GraphQL mutation, worker restart, malformed
input refusal before dispatch, and exact agent preparation with bounded
unavailable-target failures. Successful container-backed agent execution needs a
reachable cluster and is not established by an unavailable-target proof.
