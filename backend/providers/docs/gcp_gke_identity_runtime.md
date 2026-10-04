# GKE workload identity runtime observation

This private, read-only provider port observes current workload configuration and
rollout. It adds no public connection API, workflow registration, deploy command,
IAM grant, Kubernetes write, TokenRequest, token exchange or inference request.
Production dispatch, complete app-binding union/authority checks, durable journals
and subsequent workload deployment still require their separate integration.

## Trusted caller contract

Construct `GKEIdentityRuntimeObserver` with the original `GKEObservationContext`
from [the source observer](gcp_gke_identity_observation.md), plus 1–16 frozen
`WorkloadTarget` values. Each target pins namespace, controller kind/name/UID,
positive generation, original KSA name, nonempty bounded selector, accepted
current-controller template SHA256, original placement SHA256, desired count and
an explicit eligible Standard node-pool ceiling. Template/placement fingerprints
must come from a reviewed current source; a caller-supplied hash grants no authority.
The namespace/KSA UIDs remain the original physical identities. A historical
logical environment alias does not grant access to other environments sharing a KSA.

Call `observe(checkpoint=current_admission)` in a context manager, or close the
observer afterward. The checkpoint must recheck the real current original
credential, org/app/cluster/provider placement, complete binding union and accepted
operation. There is no permissive default checkpoint and this port does not replace
those checks with configuration hashes. Checkpoints run before credential/client
discovery and before/after native responses, including held/error responses.
Withdrawal propagates; returned native failure reasons are fixed private codes.

The observer reuses admitted ADC and fixed public Resource Manager/Container
clients. It verifies current ACTIVE project ID/number mapping, original native
Cluster.id, location, RUNNING configuration and project workload pool. Standard
pools must be a complete bounded inventory with current GKE_METADATA configuration;
actual pool locations (or the documented cluster locations default) are reread.
Autopilot retains its observed native configuration without adding a Standard
metadata-server node selector.

## Observed rollout contract

| Workload | Required current observation |
| --- | --- |
| Deployment | Original controller generation/template/selector, matching replica counters, one current active owned ReplicaSet, no old active revision, owned current Ready Pods |
| StatefulSet | Original controller, matching replica counters, nonempty equal current/update revisions and matching Pod revision labels |
| DaemonSet | Current desired/current/updated/ready/available counts, no misscheduled/unavailable count, owned Ready Pods; only exact generated node-name affinity and bounded documented tolerations may supplement declared placement |
| ReplicaSet | Original controller, current generation and desired/ready/available/fully-labeled counts, directly owned Ready Pods |
| Job | Original selector/template, nonsuspended positive current active count and owned Ready Pods; a completed Job is separate execution evidence, not a running rollout |
| CronJob | Actual current owned active Job identities and matching original job template, active counts and Ready Pods; schedule/last-success time alone never establishes running or completed execution |

Completed execution is recorded only from an actual owned Job whose native
`Complete=True` condition and succeeded count satisfy its explicit/default
completion count, without `Failed=True`. Job/CronJob APIs need not report
`observedGeneration`; their result leaves that fact false rather than inventing
controller generation acknowledgement. A complete Job can be observed while
`workload_ready=false`; a future scheduled execution remains pending.

Each selected Pod must be Running, Ready, have ready running container statuses,
retain the original KSA name, template behavior and controller ownership chain,
and have `hostNetwork=false`. Missing declared values, injected command/args/env,
security context, sidecars or unknown execution fields refuse. Only explicit
bounded API defaults and the recognized automatic service-account projected
volume/mount are permitted. Those default token mounts do not verify token claims.
Declared labels/annotations and execution fields remain checked.

Each current Node must be Ready, have a GCE providerID matching the verified project
ID or number and zone, and match the eligible native pool location and caller's
Standard pool ceiling. Existing nodeSelector and required nodeAffinity retain
In/NotIn/Exists/DoesNotExist/Gt/Lt semantics (AND within a term, OR across terms).
Preferred affinity remains nonbinding and unchanged. Unknown predicates refuse;
no selector, user constraint or toleration is removed or added by this reader.

This is sequential configuration evidence: Kubernetes Node labels/providerID plus
current GKE pool metadata do **not** prove Compute MIG membership or cryptographic
node identity. KSA name/UID/configuration plus Ready Pods do **not** prove the Pod's
issued token issuer/UID claims, successful GSA impersonation or Endpoint invocation.
`impersonation_verified` and `inference_verified` remain false.

## Bounds, drift and recovery

All Kubernetes traffic uses the fresh observed endpoint and verified CA, private
admitted token, fixed GET paths and a server-built limit. No kubeconfig, ambient
proxy, user continuation/URL, redirect, retry, exec, write or credential output is
used. Namespace-wide Pod/ReplicaSet/Job inventories are capped at 256 items;
continuation/remaining counts or overflow refuse rather than report healthy empty.
There are at most 512 runtime reads and an aggregate checked 60-second deadline.
Native SDK/HTTP response limits remain 2 MiB with 10-second native RPC/socket/read
bounds; an already blocked socket may consume one additional socket timeout before
the aggregate checkpoint can refuse. Bodies, PodSpecs, tokens and response errors
are neither returned nor logged.

Final rereads compare original UID/generation, source/template/spec, owners,
placement and readiness facts. Routine opaque resourceVersion, managedFields,
heartbeat/transition timestamps and status messages do not invalidate unchanged
workload/node observations. Opaque RVs must still be present and valid on each
read; namespace/KSA within-observation resource-version comparisons and native source rereads
retain the source observer's stricter behavior. Any relevant drift, partial read,
unknown identity, withdrawal or pending rollout prevents ready success. These
sequential reads are not an atomic snapshot, Kubernetes CAS, cancellation of an
older send or a guarantee against changes after return. Retry requires the same
original identity plus a fresh actual caller admission; changed targets require a
new reviewed operation, never silent adoption.

Native SDK serializers and owned TLS protocol tests cover all six workload kinds,
Standard/Autopilot, source withdrawal, partial/error/oversized reads, wrong owners,
revisions, execution injection, placement predicates and final reread drift. This
fixture proof makes no claim of live GKE rollout, federated-token exchange, effective
IAM or model inference acceptance.

## Primary contracts

- [GKE workload identity concepts](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/workload-identity)
- [GKE workload identity setup and Standard/Autopilot behavior](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/workload-identity)
- [Cluster locations](https://docs.cloud.google.com/kubernetes-engine/docs/reference/rest/v1/projects.locations.clusters)
- [Node pool locations](https://docs.cloud.google.com/kubernetes-engine/docs/reference/rest/v1/projects.locations.clusters.nodePools)
- [Deployment API](https://kubernetes.io/docs/reference/kubernetes-api/apps/deployment-v1/), [StatefulSet API](https://kubernetes.io/docs/reference/kubernetes-api/apps/stateful-set-v1/), [ReplicaSet API](https://kubernetes.io/docs/reference/kubernetes-api/apps/replica-set-v1/)
- [DaemonSet placement defaults](https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/)
- [Job API](https://kubernetes.io/docs/reference/kubernetes-api/batch/job-v1/), [CronJob API](https://kubernetes.io/docs/reference/kubernetes-api/batch/cron-job-v1/)
