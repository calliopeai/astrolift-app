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
`WorkflowRun` mirror or caller-supplied workflow/run strings. The original
blocking execution-admission implementation is not suitable inside these
parent locks; production integration requires the separately reviewed bound
read/checkpoint successor.

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

Runtime acceptance must separately protect a typed Standard node-pool ceiling,
GKE incarnation/mode and placement fingerprints before the first controller
write. It must use that same accepted ceiling afterward; a later fresh pool
read is not original apply admission. Autoscaling nodes inside the admitted
pools remains normal. Controller observation here does not prove Pod readiness,
token exchange, workload impersonation, endpoint inference, or successful
production workflow orchestration.

The Kubernetes [update and concurrency contract](https://kubernetes.io/docs/reference/using-api/api-concepts/#updates-to-existing-resources)
defines resourceVersion handling; the controller shapes are documented in the
[Deployment](https://kubernetes.io/docs/reference/kubernetes-api/apps/deployment-v1/),
[StatefulSet](https://kubernetes.io/docs/reference/kubernetes-api/apps/stateful-set-v1/)
and [DaemonSet](https://kubernetes.io/docs/reference/kubernetes-api/apps/daemon-set-v1/)
API references. DaemonSet scheduling counts are observed status, not supplied
replica configuration.
