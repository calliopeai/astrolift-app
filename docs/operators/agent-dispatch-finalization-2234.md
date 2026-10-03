# Final registered-agent dispatch failure

`DispatchAgentTaskWorkflow` now settles final dispatch-activity failure, exhausted
activity retries and schedule-to-close timeout through the existing locked
`AgentTask.transition_to` primitive. The same transaction freezes one completion
callback event. A retryable dispatch activity failure is not final: it resumes the
same task/job and creates no completion event. Agent/container `max_retries` and
`AgentRun.retry_count` retain their existing meanings; the Temporal activity
attempt is recorded separately.

Before registered dispatch effects, the worker reads its actual Temporal execution
and original start input. It records the namespace, canonical task workflow ID,
original run ID, actor kind/user ID/token ID and dispatch activity/attempt in the
internal `dispatch_execution` field. It copies no actor display, trigger payload,
result, credential or exception body into that field. This is execution provenance,
not a new permission grant or physical-container attestation. The worker needs
DescribeWorkflowExecution and GetWorkflowExecutionHistory access in its own
namespace. Existing GraphQL documents and public mutation inputs are unchanged.

Finalization records durable intent under the task control/row locks before
cleanup. It uses only the saved dispatch placement and external/planned job ID,
with the existing task GUID and Kubernetes ownership/UID checks. It never chooses
a new default cluster. Only confirmed stop permits a new terminal transition.
Unknown placement, an unavailable/changed cluster, failed deletion or unconfirmed
stop remains nonterminal and keeps the finalization activity retrying without a
schedule-to-close ceiling. Restarting the worker resumes the original operation;
it does not create a replacement task or job. A completion/cancellation already
committed wins without changing its result, failure or callback event. Durable
operator Stop intent wins over a pending platform timeout.

New failure packets contain only `AGENT_DISPATCH_EXHAUSTED` or
`AGENT_DISPATCH_TIMEOUT` and a static message. The new finalizer activity/history
carries task identity, classification and terminal status, not exception bodies.
Kubernetes stop/attachment-cleanup diagnostics omit exception traceback/message
content while retaining deletion error types and unconfirmed-stop behavior. The
existing encrypted callback outbox, HMAC transport, policy/secret checks, 24-hour
retry cadence and manual replay are unchanged.

## Temporal compatibility and limits

The dispatch activity's name and single task-ID argument are unchanged. The new
finalization command is behind `agent-dispatch-terminal-finalization-2234` at the
final failure branch. Completed older histories replay their original commands
and result; replay does not repair tasks already stranded by an older completed
wrapper. Those require verified operator recovery using original placement, not
a workflow reset or invented run/ownership binding. Older unfinished histories
can enter the new finalization branch when final failure occurs. Their binding is
read from the actual current worker/start history; a new database field alone is
not evidence of any older physical effect. Missing physical placement remains
pending rather than being fabricated.

A different workflow run, actor tuple or finalization classification cannot adopt
an existing binding. Temporal termination/reset, direct database tampering, loss
of retained workflow history and infrastructure that never recovers are explicit
boundaries: this change does not promise cleanup after an execution is forcibly
terminated or automatically migrate ownership to a replacement run. A Temporal
Describe followed by a database/provider operation is not a cross-system CAS.
The existing provider stop contract determines resource deletion proof; this
change adds no broader resource/data-retirement guarantee. It also does not change
existing successful dispatch result/history contracts.

## Native verification

The focused tests use real disposable PostgreSQL, the official native Temporal
test server, the actual exported workflow/activities and controlled real private
interface HTTPS. Provider boundaries use the real Kubernetes Job spawner or a
faithful ownership/stop/confirmation boundary; no cloud mutation or Docker is
required. They cover final activity exhaustion, final timeout, operator cancel,
queued timeout before activity execution, retry-versus-agent-attempt separation,
exactly one encrypted event, sensitive failures, stale run/intent, unknown/changed
placement, completion races, unfinished-history upgrade, completed-history replay
and uncertain cleanup across worker replacement. A final event sent to a
controlled 503 receiver recovers with the same body/event and a new delivery ID.
These tests do not attest customer VPC reachability or a production callback POST.
