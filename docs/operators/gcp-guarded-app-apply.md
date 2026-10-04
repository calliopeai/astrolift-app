# Private guarded GKE app configuration

The private `gcp.gke_app_apply` port and
`astrolift_services.gcp_gke_app_apply_journal` adapter stage receipt-bound app
configuration. They are not activated by the ordinary deployment workflow and
do not report a deployed, ready, or inference-capable app. Existing AWS, Azure,
local, and legacy GCP render/apply paths are unchanged.

## Required caller boundaries

The caller must first establish the protected Deployment origin and actual
Temporal execution, current original GCP identity source, committed namespace
and ServiceAccount preparation, and completed IAM configuration. The compiled
plan binds that Deployment, operation UUID, workflow/run, prepared identity
fingerprint, and every resource's execution and placement hashes. A hash,
typed receipt, fixture callback, or reservation is not current authority.

Fresh original-caller and source callbacks are mandatory at reservation,
submission, observation, completion, and around native operations. Native
source reads belong outside database transactions; the final database callback
must check the current protected receipts and accepted source snapshot. The
caller must use trusted activity execution admission, not a delayed
`WorkflowRun` mirror or caller-supplied workflow/run strings. The actual activity
opens `deployment_execution_checkpoint(input)` and composes its DB-only checks;
the scoped callback re-admits the original execution on each invocation and SDK
metadata thread. It must remain open for the operation and refuse after closure
or cancellation. Bound receipt reads do not wait on EXPECTED row locks beneath
the source/apply parent locks.

Acquire the preparation, IAM, then app-apply advisory mutexes without waiting.
The app mutex is physical app/cluster/namespace ownership, independent of
current slugs. Short database transactions use the existing canonical parent
order, then IAM/preparation records, Deployment/source records, and apply
records. All row locks use `NOWAIT`. Exact PostgreSQL lock contention becomes
`JOURNAL_BUSY` only after rollback. Retry requires fresh admission; contention
does not authorize a send or clear uncertainty. No native call occurs inside
these transactions.

## Resource ownership and outcome

The plan includes each rendered Secret, ConfigMap, Service, PVC, Ingress,
NetworkPolicy, PodMonitor, Gateway and HTTPRoute, plus each supported
Deployment, StatefulSet or DaemonSet. Namespace and ServiceAccount writes are
excluded; their original prepared UIDs and GSA link are checked first. Unknown
resource kinds, unprepared subjects and conflicting owner metadata refuse.
CRD installation, native admission compatibility and application functionality
are not established by route support alone.

For each write, the adapter commits exact `UNSENT` then `SENT` metadata before
transport. Successive callbacks must return the same journal GUID and advancing
versions. Native acknowledged UID/resourceVersion/generation evidence is
committed before observation or another effect. Existing objects update only
under the recorded original UID and current opaque resourceVersion JSON Patch
tests. Foreign metadata is preserved. Existing objects at unowned paths are
never adopted, and server-side apply with forced conflict takeover is not used.

A lost create response without committed original UID evidence remains
unresolved. Names, operation markers and matching labels cannot recover that
incarnation or permit resend. Lost evidence-commit acknowledgment can recover
through the exact already committed UID, using fresh reads and admission.
Evidence retention after credential withdrawal permits no follow-on effects.
It still requires the original live owner and Deployment rows: owner or
Deployment deletion can prevent retention and leave the send unresolved.
Arbitrary owner-withdrawal retention is not provided by this version.

Validated Kubernetes `v1 Status` failures at the admitted native TLS endpoint
distinguish the bounded definitive rejection cases: 400/BadRequest,
403/Forbidden, 409/Conflict and 422/Invalid. Exact intent, code, fixed reason
and response hash remain append-only. Fresh admission and original UID checks
are required for retry; a create conflict never permits adoption. Transport
loss, 5xx and inconsistent or malformed failure responses remain unresolved.
Neither a stale completion receipt nor a new accepted generation can clear
`SENT`/`EVIDENCE` uncertainty. A new generation requires the previous completed
configuration or a definitive rejected attempt and preserves its original UID
ledger and protected history.

After every accepted resource is freshly observed, completion can record only
configuration observation. The database stores bounded identity metadata,
hashes and outcome history; it never stores Secret values, authorization,
compiled manifests or full PodSpecs. Any retained history prevents migration
0041 reversal. Keep the forward schema; do not purge or recast ownership rows
to make a downgrade succeed.

## Current staged limits

Deployment and StatefulSet require explicit static replicas from 1 to 256.
DaemonSet has no invented replica count: runtime admission must later observe
its exact original UID/generation and a positive desired scheduling count.
HPA, zero replicas, Jobs and CronJobs are refused in this apply version.
Only documented reviewed core/apps defaults are normalized, per controller
kind; unknown admission mutations retain acknowledged evidence and refuse
configuration completion.

Resource, top-level field, formerly-owned label and annotation removal are
refused before submission. Service/PVC native assignments are preserved and
pinned when observed; later allocation changes, including a PVC binding after
its initial response, require a separate reviewed transition. General cleanup,
robust backoff, partial-revision supersession, autoscaling, capacity changes,
physical-subject evolution, selected-environment handoff in shared namespaces
and whole-pipeline rename support remain open.

The private placement handoff now requires actual native acceptance before a
new durable reservation or controller write. `capture_placement` binds the
original GKE incarnation, Standard/Autopilot mode, sorted Standard node-pool
ceiling and final compiled controller execution/placement digest. The protected
accepted-plan JSON retains these bytes without adding a migration. Current
original-caller/source callbacks remain mandatory; this snapshot is not
authority. A historical plan without acceptance remains history only and cannot
be enriched in place or emit effects/runtime receipts. A genuinely new accepted
operation may capture a fresh ceiling through the guarded generation path.

`runtime_handoff` reads only the actual current completed operation and exact
committed UID/generation/configuration ledger, under the same source,
preparation, IAM and execution fences. `validate_runtime_handoff` binds every
subsequent callback to that current receipt; a caller-fabricated or stale typed
object does not acquire database authority. Native target construction then
rechecks the original controller UID/generation, current approved projection and
actual observed template/placement hashes. Reviewed API defaults can distinguish
the compiled digest from the recorded native effective template; neither hash
can silently replace the other.

Runtime targets retain the original admitted ceiling. Unrelated eligible new
pools do not expand it or alone prevent observation; a Pod on an unadmitted
pool fails runtime acceptance. Node autoscaling inside the admitted pools remains
normal. No new node selector is injected to change the reviewed template. A
removed/ineligible original pool, changed GKE incarnation or mode refuses.
Autopilot records its distinct mode and no invented Standard pool list.

Deployment and StatefulSet targets use their recorded original UID/generation
and accepted positive static replica count. A DaemonSet target requires its
current original UID/generation and matching observedGeneration, then an actual
integer desiredNumberScheduled from 1 to 256. Missing/zero desired count or
unobserved generation is pending with no fabricated target; malformed, negative
or oversized counts refuse. Runtime compares scheduled/updated/ready/available
counts to that observed target. A count or controller change during observation
cannot become success from an earlier sample.

This handoff can feed the existing read-only runtime observer, which checks
current owned Pods and Nodes. It does not activate the production executor,
prove token exchange, workload impersonation, Endpoint inference or successful
production workflow orchestration. Capacity/HPA/zero-replica support and the
other staged limits above remain open.

The Kubernetes [update and concurrency contract](https://kubernetes.io/docs/reference/using-api/api-concepts/#updates-to-existing-resources)
defines resourceVersion handling; the controller shapes are documented in the
[Deployment](https://kubernetes.io/docs/reference/kubernetes-api/apps/deployment-v1/),
[StatefulSet](https://kubernetes.io/docs/reference/kubernetes-api/apps/stateful-set-v1/)
and [DaemonSet](https://kubernetes.io/docs/reference/kubernetes-api/apps/daemon-set-v1/)
API references. DaemonSet scheduling counts are observed status, not supplied
replica configuration.

Runtime placement validates the complete bounded pool inventory but retains only the original admitted pool ceiling. An unrelated provisioning pool or pool without GKE_METADATA neither expands that ceiling nor invalidates eligible original pools. Initial capture includes only eligible RUNNING/GKE_METADATA pools and does not establish capacity or model compatibility. Standalone identity observation retains its stricter all-pools check.
