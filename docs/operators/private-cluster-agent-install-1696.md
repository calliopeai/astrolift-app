# Reviewed server-owned cluster agent installation (#1696)

The control-plane Temporal worker installs the keep-alive agent through the registered
cluster provider. The operator machine needs only access to the control plane. It does
not need kubeconfig or network access to a private Kubernetes endpoint. The worker must
have that network route, DNS and certificate trust. This is an installation path, not
an assertion that every control-plane deployment already has private-cluster routing.

Preconditions: `cluster.manage` at the cluster's organization; a shared platform
cluster also requires platform operator authority. Keep the same session or
bearer credential for the whole operation, since replays must present the
original identity. The Temporal worker, not the operator machine, needs the
route to the Kubernetes endpoint; for EKS the registered cluster credential
must be allowed `eks:DescribeCluster` and hold the Kubernetes access listed
under Worker identity. Surfaces: `astro operator cluster install-agent` and
`astro operator cluster agent-install-status`, the managed agent panel at
`/clusters/<cluster-slug>/settings`, or GraphQL via `astro api graphql`.
Placeholders: `<cluster-id>` and `<cluster-slug>` are `id` and `slug` from
`astroliftClustersPage`; `<request-file>` is a private local path you choose
and keep; `<version>` and `<source>` are the review's `version` and `source`;
`<request-id>` is a nonzero UUID you generate once; `<interval-seconds>` is
5 to 60; `<install-id>` is the installation `id` printed by `install-agent`.

```sh
astro operator cluster install-agent --cluster-id <cluster-id> --request-file <request-file>
astro operator cluster install-agent --cluster-id <cluster-id> --request-file <request-file> --interval-seconds <interval-seconds>
astro operator cluster agent-install-status --install-id <install-id> --cluster-id <cluster-id>
```

```graphql
query { astroliftClusterAgentInstallReview(clusterId: "<cluster-id>") { clusterId version source } }
mutation { installClusterAgent(input: {clusterId: "<cluster-id>", expectedVersion: <version>, expectedSource: "<source>", requestId: "<request-id>", intervalSeconds: <interval-seconds>}) { ok errors { code message field } data { id clusterId requestId status } } }
query { astroliftClusterAgentInstall(installId: "<install-id>") { id clusterId status secretConfirmed deploymentConfirmed heartbeatConfirmed retryable errorCode errorMessage } }
```

## API and recovery

`astroliftClusterAgentInstallReview(clusterId)` returns `clusterId`, `version` and
`source`. Persist these with a canonical nonzero UUID `requestId`, the selected
`intervalSeconds`, control-plane URL, organization and actor before calling
`installClusterAgent(input: {clusterId, expectedVersion, expectedSource, requestId,
intervalSeconds})`. The optional interval retains the current interval when omitted;
an explicit interval is clamped to the existing 5–60 second bounds. The new API's
required `expectedSource` is additive to existing APIs; there is no legacy credential
rotation or operator-side Secret fallback.

A lost HTTP reply is recovered by repeating exactly that original mutation tuple.
Do not fetch a fresh review, change the interval, replace the UUID or switch bearer
identity for a replay. An identical replay returns the same operation even after its
own successful activation advances the cluster version. A different or deleted
operation with the same actor/org/request UUID refuses the request. Current admission
permissions still apply. `astroliftClusterAgentInstall(installId)` is a read-only status
lookup restricted to the original actor and organization, with current permissions
and the original credential ceiling checked again. The cluster Settings page's managed
agent panel drives `installClusterAgent` and polls `astroliftClusterAgentInstall`.

`ok: true` means the operation was accepted or replayed. Its six states are `QUEUED`,
`INSTALLING`, `AWAITING_HEARTBEAT`, `SUCCEEDED`, `REFUSED` and `UNCERTAIN`.
`secretConfirmed` and `deploymentConfirmed` describe durable observed object receipts.
Only `SUCCEEDED` with `heartbeatConfirmed` establishes that the installed candidate
sent an authenticated heartbeat. This is not rollout readiness, pod identity
attestation, or an application/data availability guarantee. An uncertain operation
keeps its exclusive cluster lease; replay recovers its exact Temporal execution or
starts a new generation only after a known closed execution. Older generations cannot
perform further provider effects. Temporal receives only operation ID and generation.

The original actor, bearer identity, original scope ceiling, organization/team and
request attributes are retained privately. Every installation provider stage rechecks current
membership, role/ABAC permission, token expiry/revocation/current scope and shared
cluster operator authority under the canonical cluster/provider locks. An expired,
revoked or otherwise withdrawn original credential is never replaced by a broader
replay credential. A staged heartbeat with withdrawn authority/source terminalizes
only operation metadata. A fresh authorized admission with a fresh review may likewise
refuse that invalid old lease and create a new operation. Valid pending operations,
including provider-uncertain operations with valid authority/source, remain exclusive.
Neither metadata refusal path changes provider objects or the current active key.

## Credential and object ownership

The operation generates a candidate key once and stores only its hash and encrypted
ciphertext in the database. The raw key is decrypted only in worker memory for one
immutable, uniquely named Secret `astrolift-agent-<operation GUID>`. Raw keys are absent
from API results, workflow arguments/history and application logs. Kubernetes transport
debug logging is disabled for this path; provider exception text is replaced with fixed
metadata-only diagnostics. Storage-encryption configuration remains an operational
prerequisite. Old Secrets are retained and never overwritten or deleted by this flow.

The same operation owns a unique candidate Deployment with a unique pod selector. The
previous Deployment and heartbeat credential continue working while the candidate is
pending or any installation stage fails. The candidate's first valid JSON heartbeat
must pass current source/authority, recorded Namespace UID, exact Secret UID/key and
exact Deployment UID/template checks before activation. Activation records the new
active Secret and Deployment names/UIDs and replaces the heartbeat hash under the same
cluster lock. A waiting old heartbeat cannot overwrite activation or receive jobs for
the new identity. Heartbeat telemetry alone changes only timestamp and payload; it
cannot mutate lifecycle, capabilities, cloud identity or authentication configuration.

Only after activation may the old Deployment be deleted, and only if its exact original
UID was durably recorded. The delete includes UID and resource-version preconditions.
A foreign/replaced old Deployment is retained. Unconfirmed deletion leaves `SUCCEEDED`
and `heartbeatConfirmed` truthful but sets `RETIREMENT_UNCONFIRMED`; expose this warning
even on successful status. Exact mutation replay may retry that bounded cleanup. Status
reads do not clean up. Cleanup failure never rolls back the new credential or replaces
the old object's ownership. Old Secrets are not garbage-collected here.

An existing `astrolift-system` Namespace must carry the existing canonical
`astrolift.io/managed-by` value `platform` or `astrolift-control-plane`; an optional
`astrolift.io/cluster-guid` must match. The namespace UID is then recorded and checked
again. Shared agent ServiceAccount/RBAC objects require canonical platform ownership;
existing identities are durably observed before conditional updates. Candidate Secret
and Deployment names cannot adopt foreign objects. Recorded objects that disappear or
are recreated with new UIDs refuse automatic replacement. Reconciliation uses the
recorded active Deployment name, UID and Secret, rather than reverting to a fixed name.
Moving an active recorded identity to a different physical cluster with missing objects
currently fails closed and requires operator review; this is not a retarget/rollback
or cross-provider cleanup mechanism.

## Worker identity and compatibility

For EKS, the registered credential is used for both `DescribeCluster` and the signed
Kubernetes authentication token. Scoped assume-role registrations retain their external
ID and role session name; ambient registrations remain compatible. IAM must permit
registered-role cluster discovery and signing, and that identity must have Kubernetes
access. The Kubernetes identity needs namespace read/create; Secret read/create;
agent ServiceAccount/ClusterRole/ClusterRoleBinding read/create/patch; candidate
Deployment read/create; and previous Deployment read/delete for confirmed retirement.
Secret update/delete is unnecessary. Existing Namespace creation/ownership and exact
cluster access policies remain admission requirements, not implicit authority grants.

Existing `issueClusterAgentKey` and `deployClusterAgent` retain their legacy inputs and
provider behavior when no server-owned installation is pending. Pending installations
refuse those writes before changing keys or dispatching cloud effects. Since #2550,
`deployClusterAgent` on a cluster with no recorded server-owned Secret first reads the
manually installed `astrolift-agent` Secret in `astrolift-system` and refuses with
`PRECONDITION` unless its key hash and heartbeat URL match the cluster's active
credential; a cluster with a recorded server-owned Secret is reconciled instead. Explicit legacy
key rotation clears the new server-owned active bindings and returns to the legacy
fixed Secret/Deployment path; it does not modify operation-owned Secrets. Periodic
agent reconciliation skips pending installations and uses exact recorded objects after
activation. There is no hidden enforcement cutover for unrelated legacy callers.

## Source tracking and boundaries

The public `source` HMAC contains only nonsecret cluster/provider identity and persisted
version/time markers, heartbeat origin and agent image. It contains no credentials or
credential-value hashes. A private operation fingerprint also binds every cluster field
currently consumed by dispatch: provider/region/endpoint/CA/auth method/auth config,
provider config, verified cloud account, activity/deletion, interval and active agent
bindings. Provider slug/enabled/deletion and persisted markers are bound as well. The
public review therefore refuses provider-source changes between review and reservation,
and tracked A→B→A writes invalidate the original review.

Relevant full/partial `TenantCluster` and `ProviderPlugin` source saves lock the current
row and use its persisted version before the existing tracked save; partial saves also
persist version/time. This fixes stale-instance repeated counters without changing the
generic tracking mixin or unrelated models. Audit of supported current cluster/plugin
writers includes registration/update-or-create, provider catalog bootstrap, management
and capability/cloud-identity saves, legacy key/deploy writes, and reconciliation. They
use normal tracked saves for semantic changes. The sole current production queryset
update of TenantCluster is the locked, current-key conditional heartbeat
timestamp/payload update; neither field drives dispatch authority. Historical migrations
and test setup bypasses are outside live writer admission. ProviderPluginConfig is not
read by the cluster-driver configuration path. Direct external database tampering that
bypasses these writers is outside the tracked-marker guarantee.

Conditional Kubernetes writes use UID/resource-version checks and never force field
ownership. This follows Kubernetes [API concurrency controls](https://kubernetes.io/docs/reference/using-api/api-concepts/)
and [server-side apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/).
There is still an external get→activation race: Kubernetes has no transaction spanning
the object reads, the control-plane database and a heartbeat. Namespace validation and
later object creation are likewise not a cross-object atomic operation. External actors
can change resources after the last read. No blanket provider CAS, exactly-once cloud
execution, cross-system atomicity or physical pod attestation is claimed. Exact object
receipts, durable checkpoints and retries bound known effects; ambiguous effects remain
uncertain until reconciled.

## Validation scope

`backend/astrolift_clusters/tests/test_server_agent_install_1696.py` runs against an
owned disposable PostgreSQL database and a native HTTP Kubernetes boundary using the
installed private provider wheel and real Kubernetes dynamic client. It exercises the
actual bearer GraphQL/heartbeat endpoints, UID/resource-version conflicts, partial and
lost writes, candidate preservation/retirement, source ABA and lock waits, original
credential withdrawal, namespace ownership and real Temporal worker recovery/history.
`backend/providers/tests/aws/test_agent_install_identity_1696.py` checks the actual
SigV4 token derives from the registered assume-role session including external ID.
Synthetic identities only; no live AWS/Kubernetes installation or production database
was used. Frontend SDL/types are generated by their owning contract/codegen tools.

## Verify

```sh
astro operator cluster agent-install-status --install-id <install-id> --cluster-id <cluster-id>
./run.sh test astrolift_clusters/tests/test_server_agent_install_1696.py providers/tests/aws/test_agent_install_identity_1696.py
```

The status read shows `SUCCEEDED` with `heartbeat confirmed: true` and no
`RETIREMENT_UNCONFIRMED` note, and both test modules pass.
