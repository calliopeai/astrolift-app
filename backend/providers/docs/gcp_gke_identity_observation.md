# Internal GKE identity configuration observer

`gcp.gke_identity_observation.GKEIdentityObserver` is an internal read-only port,
not an enabled workload-identity feature or a public model connection API. It
consumes an originally recorded `NativeIdentityContext`, the declared GKE
location/name/native `Cluster.id`, and exact Kubernetes namespace and
ServiceAccount names plus their original UIDs. Empty UIDs return unavailable
before ADC or native reads. Names and owner labels never authorize UID adoption.

The caller must provide a current-admission checkpoint that explicitly returns
`None` on admission. Boolean or other returned values refuse. It runs before ambient
credential discovery/client construction and before/after every native or
Kubernetes response, including failed transport. The same admitted ADC identity
reads the ACTIVE Resource Manager project ID/number mapping and the exact native
cluster. No ambient project, endpoint override or cached kubeconfig substitutes
for the declared context. GKE location may be a zone or region; it is not inferred
from the Vertex region.

Standard admission reads the complete, unpaged `ListNodePools` response, requires
1–64 uniquely named RUNNING pools, and requires `GKE_METADATA` on every pool.
Cluster-wide Workload Identity Federation enablement does not establish those
pool settings. Mixed/unknown/empty inventories are unavailable; this initial
port has no selected-pool scheduling contract. Autopilot uses its native enabled
flag plus the exact workload-pool check, independently of Standard pool fields.
These are configuration observations, not node capacity or pod scheduling proof.
See [Google's GKE workload identity guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/workload-identity).

The production Kubernetes adapter makes only exact namespace and ServiceAccount
GETs through a fresh native public/RFC1918 IPv4 or `*.gke.goog` endpoint and its verified
CA. It ignores proxies, refuses redirects/encoded/oversized responses, and uses
bounded TLS HTTP reads. RFC1918 addresses require the same verified native
cluster ID/endpoint/CA path; loopback, link-local, multicast, reserved and
unspecified destinations refuse. Namespace and ServiceAccount UIDs, resource versions, live
state and organization/app/cluster GUID labels must match. An absent GSA
annotation may still be observed as unlinked configuration; a foreign annotation
refuses. No create, annotation, apply, delete, identity or IAM operation occurs.

The port rereads project, cluster, Standard pools, and exact Kubernetes objects
before returning. Resource versions are bounded opaque nonempty visible strings,
compared unchanged without numeric parsing or ordering. Any replacement or observed configuration/resource-version
change refuses. Sequential observations are not an atomic native snapshot, CAS
or a promise that resources cannot change immediately afterward. A subsequent
mutation needs its own current checkpoint and original identity admission.

Private Google token transport permits bounded standard OAuth, Google metadata,
STS and IAM Credentials routes, without ambient proxies or redirects. This first
adapter accepts normal service-account, authorized-user and Compute Engine ADC;
other credential types and executable external-account configuration refuse.
No operator token, native body, CA, or Kubernetes payload is logged. SDK clients
use fixed public Container/Resource Manager gRPC hosts, no retry, a 10-second RPC
deadline and a 2 MiB response bound. HTTP reads use a 10-second socket timeout plus
a checked read deadline (a final blocked read may consume one additional socket
timeout); there are at most 64 subjects and 64 Standard pools.

`configuration_observed` records only these observations. `workload_ready` and
`impersonation_verified` are always false. No GSA impersonation, Endpoint invoke,
Kubernetes rollout, host-network behavior or token-cache revocation is certified.
Caller-owned originally recorded UIDs and accepted authority still need the
reviewed durable journal/orchestration boundary. No journal, migration, workflow
registration, public schema, KSA preparation or app render integration is added.

One KSA annotation selects one GSA. The Endpoint-only IAM port cannot represent
Cloud SQL/GCS/PubSub or other mixed app unions. Before any eventual KSA/IAM effect,
that later caller must inventory all coherent bindings and refuse unsupported
mixed unions while preserving old workloads/identity. No legacy GSA/project-IAM
adoption is implied. A disappeared lease/heartbeat is not cancellation of a sent
IAM request; durable callers must preserve and block unresolved send uncertainty.
Full #2278 acceptance remains open.
