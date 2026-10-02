# Reviewed workflow and pipeline starts

Public `astroliftServerInfo.capabilities` advertises
`workflows.reviewed_definition_starts`, `workflows.definition_input_contracts`
and `workflows.definition_start_recovery` for exact definition review, schema
review and actor-scoped recovery. Pipeline clients can detect
`pipelines.reviewed_starts`, `pipelines.versioned_start_requests`,
`pipelines.start_request_recovery`, `pipelines.exact_execution_cancellation`
and `pipelines.bounded_run_details`. These stable keys describe available API
contracts. They grant no permissions, reveal no request or input values, and
do not promise an enabled Temporal engine. Check runtime feature flags and
handle the normal scoped permission and precondition errors after login.

Read `workflowDefinitionById(id: GUID!)` before starting a workflow definition.
The response identifies the exact global or organization definition; an organization
copy with the same slug never replaces this GUID. Its revision covers its execution
configuration, stages, and resolved nested definition graph. Read `inputContract`
for the schema digest, supported status, simple form fields, defaults, enums and
constraints. `acceptsInputs: false` explicitly means no inputs are accepted.

Inputs use JSON Schema draft 2020-12 with an object root, at most 64 KiB and a
maximum supported schema nesting depth of 32. Direct property and array item
defaults are applied recursively before validation; defaults inside composition
or references are annotations and are not automatically applied. Simple scalar
properties support a generated form. Objects, arrays and compositions require a
JSON editor or an API client validating the returned schema. Unsupported regex,
dynamic or external references fail closed with a clear contract error.
`writeOnly` and `x-astrolift-sensitive` fields accept opaque `secret://` references,
never literal secret values or defaults.
Sensitive annotations on `unevaluatedProperties` or `unevaluatedItems` are applied
conservatively to every property or item; prefer explicit sensitive properties
for contracts that mix ordinary values and secret references. References are
passed as references, not resolved or expanded by this input contract. Existing agent secret bindings remain
responsible for supplying actual secrets. Submitted inputs are encrypted at rest
with the same secret backend used by platform secrets and omitted from mutation
audit/debug variables. Do not persist input values in browser storage.

Call `startWorkflowDefinition(input: {definitionId, expectedRevision,
expectedInputSchemaDigest, requestId, inputs, confirmed: true})`. The caller creates
one stable request ID before dispatch and keeps the same exact body when retrying.
The actor- and organization-scoped durable key is never recycled. Conflicting or
deleted requests are refused. Current scoped permissions, active credentials,
enabled state, input contract and revision are checked inside the reservation and
again before dispatch. Existing browser elevation requirements remain enforced;
this contract does not implement the broader mobile consent or proof system.

A successful envelope means Temporal accepted the exact execution, not that the
workflow completed. A failed envelope may still contain its reserved execution,
workflow ID and `dispatchStatus: uncertain`; keep that identity. Recover with
`workflowDefinitionStartRequest(requestId)` in the same organization and as the
same actor. This performs a bounded read-only engine lookup and verifies workflow
type and the original stored inputs before recording the exact Temporal run ID.
It never submits a new execution. A closed execution is never restarted under the
same request. Same-body submission retries are supported for at least 24 hours;
after that, use read-only recovery rather than creating a replacement blindly.

For pipelines, read `astroliftPipeline(id)` and use `startPipelineRun(input:
{pipelineId, expectedVersion, requestId, ref, confirmed: true})`. The durable run
retains its organization, app, reviewed pipeline version and unique run number,
including deleted history. Recover with `pipelineStartRequest(pipelineId,
requestId)`. Signed provider webhook retries reconcile the same durable run;
uncertain submissions return 503 so the provider can retry. Pipeline engine IDs
use the immutable run GUID. The worker refuses a changed reviewed pipeline before
loading its source; the fetched source digest is recorded separately because a
branch reference can move.

Legacy `triggerPipelineRun` and `cancelPipelineRun` signatures remain additive,
but clients without reviewed identity and confirmation receive `PRECONDITION`.
Legacy `runWorkflowDefinition(workflowSlug, triggerPayload)` also refuses effects
without reviewed proof. Prefer `startWorkflowDefinition`; compatibility callers
may add nullable `definitionId`, `expectedRevision`, `expectedInputSchemaDigest`,
`requestId` and `confirmed: true`. The supplied GUID must own the exact supplied
slug. It never falls back to an organization copy sharing that slug. The legacy
result retains `workflowRunId` and `temporalWorkflowId` and adds nullable
`temporalRunId`, `requestId` and `dispatchStatus` for uncertain response recovery.
This affects Definition starts only; configured `runWorkflow` and real agent-task
dispatch remain separate APIs.
For cancellation first read `astroliftPipelineRun(id)` and send `runId`,
`expectedVersion`, `temporalWorkflowId`, `temporalRunId` and `confirmed: true`.
The exact engine execution must still be running; a later incarnation with the
same workflow ID is never signalled. `cancellationStatus: acknowledged` means the
signal was accepted. `observed` means that exact engine execution is closed.
`cleanupStatus` independently records `pending`, `complete` or `failed`. The
worker deletes only the recorded Kubernetes cluster, namespace and Job UID, checks
ownership labels, uses UID delete preconditions, and verifies Job, Pod and owned
Secret removal. A failed cleanup never appears as complete.

Nested run detail is bounded to 20 jobs, 50 steps per job and a 64,000-character
recorded log excerpt. `jobsTruncated`, `stepsTruncated`, `logTruncated` and
`logKind: recorded` describe that projection. Reach every remaining row through
`pipelineJobRunsPage(runId, limit, after)` and
`pipelineStepRunsPage(runId, jobRunId, limit, after)` using `nextCursor`. These
fields expose recorded state and excerpts, not a live or full log stream.

## Disposable client acceptance fixtures

Use an explicitly selected disposable staging organization:

```sh
python manage.py create_reviewed_start_fixtures --organization ORG_GUID
python manage.py create_reviewed_start_fixtures --organization ORG_GUID --execute
```

The default reports the concrete plan without changing data. The executed command
creates one checkpoint definition that completes and one human-gate definition
that fails after its one-second approval timeout. It prints only exact definition
IDs, revision/schema digests and expected outcomes. Re-read each definition with
the real client, start it using a new stable request ID, and record the returned
execution and Temporal run IDs plus final outcome. A caller needs workflow read
and trigger permission, and browser elevation if required. Delete these definitions
through the normal scoped soft-delete API after collecting receipts. Creating the
fixtures is not evidence that a remote staging client acceptance test has passed.
