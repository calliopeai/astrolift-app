# NVKE workload credential ownership

`datastore.getWorkloadKubeconfig` is an explicit `nvke.admin` operation on the
configured management-Kubernetes connection. Its native input remains
`cluster_name` (the backing controller/CAPI name) and `management_namespace`.
It never falls back from another interface and never executes downloaded credentials.

The worker must establish ownership before reading the Secret. Either:

- An `NVKECluster` in the site's organization namespace maps to that backing name
  using the pinned management engine's `ClusterRemoteName` algorithm and has a
  nonempty controller UUID in `status.id`; or
- Astrolift has a durable `createCluster` receipt for the same organization, site,
  logical controller connection, namespace and contract revision, recording the
  UUID actually returned for that requested name. The runtime records this reference
  atomically with the encrypted accepted response, before waiting for readiness.
  The reference contains only the UUID and a site-bound HMAC of the name;
  original request and response payloads use encrypted storage. Caller JSON cannot supply
  this ownership override. Missing generated IDs are never adopted by name.

Native namespace discovery follows all pages of the organization-scoped list,
checks continuation cycles and stable resource versions, and refuses ambiguous
matches. Its 2,000-page budget fails explicitly. A cluster without its observed
controller UUID remains `ACCESS_NOT_READY`; unknown ownership fails as
`OWNERSHIP_UNVERIFIED` without requesting a Secret.

The live CAPI Cluster must then exist in the requested management namespace and
must not be deleting. The admin Secret must have all of:

- The exact expected name and namespace, a valid UID/resource version and no
  deletion timestamp.
- A matching immutable native cluster UUID label and `admin` kubeconfig mode.
  Current `nvke.nvidia.com` and legacy `nke.nvidia.com` labels are accepted;
  contradictory copies are refused.
- The matching `cluster.x-k8s.io/cluster-name` label and `Opaque` type.
- Exactly one controlling CAPI Cluster owner reference with the live Cluster UID,
  expected name and supported CAPI API group/version (`v1beta1` or `v1beta2`).

The CAPI identity is read again after the Secret; the organization-scoped native
resource is also read again when it supplied ownership. Replacement, deletion or
an altered native UUID refuses the download. Every HTTP request rechecks the
original caller's membership, token permissions, site authority, cancellation and
deadline through the operation fence. No partial proof or native Secret document
is published in operation metadata or workflow history.

Credential rotation on the same logical controller interface retains its durable
cluster ownership references. Replacing that interface creates a new identity;
old references do not authorize the replacement. Soft-deleting a creation receipt
also removes it as an ownership source. Keep creation receipts while managing
directly created controller clusters, or use an organization-scoped native resource
with a verified controller identity. Existing imported controller clusters without
either source require an explicit ownership-enrollment workflow; a name match is
insufficient.

Legacy Secrets without strong ownership labels/references must be republished by
the pinned native tenant-access controller before use through Astrolift. These
checks depend on trusted site configuration, unique organization namespace
assignment and Kubernetes/native controller authority; they do not repair the
upstream public API's lack of tenant caller authentication. Dedicated organization
deployments/proxies remain required for that public interface.

The implementation follows pinned NVKE source at
`92c39b8ad563ebe5ef705f781b3038b901aba10f`:
`management-engine/controllers/nvkecluster_controller.go`,
`controller/internal/capi/ownership.go`, and
`controller/internal/controllers/tenantaccess/{controller,secret}.go`.
Real local tests use the unmodified CAPI v1.13.6 Cluster CRD with recorded hash/license,
real Kubernetes UIDs, PostgreSQL receipts and controller HTTP fixtures. No CAPI
controllers or physical workloads are provisioned by these tests.
