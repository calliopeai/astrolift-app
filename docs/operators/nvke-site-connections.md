# NVKE sites and connections

An NVKE site belongs to one Astrolift organization. Its organization namespace
is immutable. Removing a site soft-deletes its local record and retains its
history; it does not destroy upstream infrastructure. Outstanding operations,
including uncertain outcomes, must be resolved before removal.

## Permissions

| Permission | Authority |
|---|---|
| `nvke.read` | Read the organization's site/connection metadata |
| `nvke.manage` | NVKE workload lifecycle authority, separate from site credentials |
| `nvke.admin` | Create/configure/disable/remove sites and configure credentials |

These permissions are organization-scoped. A team or project grant does not
authorize an organization-owned site. The active account must remain a member
of the organization, or be the platform operator. Another organization's site
ID and an unknown site ID produce the same result.

Organization owners and admins receive the explicit permissions through the
role migration. Custom roles can grant them separately. The existing `admin`
bearer scope covers them; ordinary cluster bearer scopes do not carry NVKE
authority.

## Interfaces

Configure each required interface separately:

| Surface | Endpoint base | Purpose |
|---|---|---|
| `CONTROLLER` | Includes `/api`, for example `https://nvke.example/api` | Existing controller REST operations |
| `TENANT` | Base hosting `/v1/clusters` | Declarative workload-cluster public API |
| `OPERATOR` | Base hosting `/v1/machine-images` and related resources | Public artifact and class APIs |
| `DATASTORE` | Management Kubernetes API server base | Explicit management resource and access operations |

The pinned tenant public API is a PoC with a deployment-selected namespace.
Its deployment/proxy must be scoped to the organization namespace recorded on
the site. Do not treat it as an authenticated multi-tenant service by itself.
Astrolift's direct datastore adapter binds the namespace from the trusted site
mapping. A failed public API call never silently switches to datastore credentials.

Endpoint URLs cannot contain credentials, query strings, fragments or path
traversal. HTTPS is the default. Plain HTTP requires the explicit `allowHttp`
setting for an evaluation endpoint. The connection timeout is bounded to
1–300 seconds. Saving a connection clears its prior verification state;
configuration alone does not prove reachability or remote readiness.

## GraphQL configuration

Create a site:

```graphql
mutation {
  createNvkeSite(input: {
    name: "GPU site"
    slug: "gpu-site"
    organizationNamespace: "org-owned"
  }) {
    ok
    errors { code message }
    data { id version }
  }
}
```

Configure a connection with the returned site ID/version:

```graphql
mutation ConfigureConnection($input: ConfigureNvkeConnectionInput!) {
  configureNvkeConnection(input: $input) {
    ok
    errors { code message }
    data {
      id
      version
      connections { id version surface endpoint hasCredentials verifiedAt }
    }
  }
}
```

The input includes `siteId`, `expectedSiteVersion`, `surface`,
`expectedConnectionVersion` (`0` for a new connection), `endpoint`, and optional
`credentials`, `allowHttp` and `timeoutSeconds`. The credential object accepts
`token`, `caPem`, `clientCertificatePem` and `clientKeyPem`. Supply the client
certificate and private key together.

- Omit `credentials`, or pass `null`, to preserve the existing credential bundle.
- Pass a new credential object to replace the bundle, including on rotation.
- Pass an empty credential object to clear it.

Credentials are encrypted with the platform's secrets-at-rest backend. API
responses contain only `hasCredentials`, never the credential bundle or its
ciphertext. Audit redaction follows schema positions, including nested variables
and rejected endpoint URLs containing embedded credentials.

Every connection configuration change advances both its own version and the
site version. Refresh after a `VERSION_MISMATCH` response instead of resubmitting
an old configuration. Site enable/disable and removal also require the current
site version.

Read sites with cursor pagination:

```graphql
query Sites($after: String) {
  astroliftNvkeSites(after: $after, limit: 50) {
    items { id version name slug isActive connections { surface hasCredentials } }
    nextCursor
    totalCount
  }
}
```

Use `astroliftNvkeSite(siteId: ...)` for one site,
`setNvkeSiteEnabled(input: ...)` to disable/re-enable it, and
`removeNvkeSite(input: ...)` to remove the local registration. Site configuration
does not start remote cluster provisioning.

## Durable operations

`astroliftNvkeContract` exposes the pinned revision, complete controller OpenAPI,
six management-resource schemas, and 88 allowed actions with their exact permission.
It accepts no arbitrary URL, Kubernetes resource kind, namespace or HTTP method.
Operator APIs and cluster-scoped datastore resources require `nvke.admin`, as do
workload kubeconfig retrieval, controller config profiles and SPIRE updates.

Submit `requestNvkeOperation(input: RequestNvkeOperationInput!)` with `siteId`,
`expectedSiteVersion`, a caller-generated UUID `requestId`, `operationKey`, and
JSON `parameters`. HTTP parameters use `path`, `query`, `body`, and conditional
`resource_version`. Datastore parameters use `name`, `document`, `resource_version`,
`uid`, and the action's declared options. The native schemas retain every field;
invalid/unknown fields are rejected before admission. `timeoutSeconds` defaults
to 7200 and must be 30–86400.

The same request ID and identical input return the original operation, including
after a response is lost. A different action, input, timeout or site version with
that ID returns `CONFLICT`. Removed request identities cannot be reused. Inputs
and observations/results are encrypted at rest; mutation audit logs mask the
whole parameters field. Temporal history contains database IDs, generation numbers
and safe status strings, never native scripts, kubeconfigs or connection keys.

Use `astroliftNvkeOperations(siteId: ..., after: ..., limit: ...)` or
`astroliftNvkeOperation(operationId: ...)` for metadata. Read the encrypted result
through `astroliftNvkeOperationResult`, which also requires the action's permission.
Result data may contain credentials or cloud-init scripts; callers must handle it
as sensitive. Metadata and results are scoped to the active organization.

The worker verifies active membership, original API token validity/scope, the
original site version and namespace, contract revision, and exact connection
configuration before every request. Changing or disabling a connection does not
silently grant a queued request new authority. TLS material is temporary and
private; no request falls back to another interface.

`SUCCEEDED` means an observed result: reads completed, artifacts match accepted
content, deleted resource identities disappeared, or lifecycle conditions and
running node/control-plane counts satisfy readiness. Dry runs are labelled
`dry_run_complete`. Accepted requests remain `AWAITING_READY`; transport acceptance
alone is insufficient. Cancellation before sending produces `CANCELLED`.
Cancellation after sending continues observation of the original effect; it does
not undo provisioning or manufacture a successful upstream cancellation.

A lost mutation response, changed authority after sending, or uncertain execution
produces `UNCERTAIN`. Automatic recovery never replays such a write. An admin can
request `resumeNvkeOperation` with `operationId`, `expectedVersion` and a bounded
`timeoutSeconds`; it requires unchanged original authority and a known resource
identity and resumes observation using the saved sending state. A controller
create whose response and generated ID were both lost requires operator
investigation; name matching alone cannot prove ownership.

The minute `NvkeOutboxTickWorkflow` recovers a bounded batch of pending dispatches.
For manual recovery run `python manage.py nvke_outbox --limit 10`. Recovery verifies
the original Temporal workflow type and inputs; a stopped execution receives a
new generation retaining encrypted progress. Missing/expired history is not proof
that the old execution stopped. Uncertain rows are excluded from this sweep.

### Current qualification limits

These foundations do not yet constitute full application support; UI, CLI,
bootstrap, workload access, telemetry and live controller qualification are
tracked under #2656. In particular, the pinned controller does not expose a direct
OIDC-enabled read-back field. That mutation cannot currently prove completion
and will remain pending, then uncertain at its deadline. The pinned management
engine declares pool readiness counters but does not populate all of them; the
adapter requires positive evidence instead of treating reconciliation acceptance
as readiness. These need further integration with workload/controller observations.

## Browser native editors

`requestNvkeOperationText` accepts `RequestNvkeOperationTextInput` with the same
site/version/action/UUID/timeout tuple, `expectedActorId`, and `parametersText`.
`expectedActorId` is the current `astroliftMyProfile.userId` reviewed by the editor;
an identity change before admission is refused. The text must contain one bounded
JSON object. Parsing happens on the server, retaining native int64 values that a
JavaScript JSON round trip could change. Both admission paths share one encrypted
outbox and idempotency identity. Audit logs mask the complete text field.

`astroliftNvkeOperationResultText` returns the authorized result as JSON text, so
browser downloads preserve native numbers and fields. Results still require the
original action's permission. Fetch them explicitly without browser cache/storage.
`astroliftNvkeRequest(siteId: ..., requestId: ...)` recovers metadata for the current
actor's exact UUID, including after a lost admission reply. Another actor's UUID or
another organization's site does not disclose a receipt. A missing receipt alone
does not prove an in-flight admission had no effect; retry the original identity.

## Imported workload access enrollment

For a controller-managed cluster created outside Astro, first execute `getCluster`
with the exact native `clusterID` through the site's controller connection. After
the operation succeeds, an administrator with both `nvke.admin` and `nvke.read`
can call `enrollNvkeWorkloadAccess` with `siteId`, `expectedSiteVersion`,
`expectedActorId`, `sourceOperationId`, and `expectedSourceVersion`. Enrollment
checks the encrypted successful response ID against the requested UUID and uses
its native name, without accepting a caller-supplied name or cluster ID. The read
must belong to the same organization/site and current controller authority.
Listing a cluster does not enroll it. An uncertain create is not enrollment proof;
this procedure does not reconcile or replay that create.

The durable binding stores the native UUID and a keyed name digest, without
credentials or a plaintext cluster name. Retrying the same enrollment returns
the existing live binding. Soft-deleting the source observation does not remove
the binding. Renewing credentials on the same logical controller connection
retains it; deleting/replacing that connection stops it from authorizing access.
Bindings also require the pinned upstream revision.

`astroliftNvkeWorkloadAccessBindings(siteId, after, limit)` pages metadata within
the active organization. `revokeNvkeWorkloadAccess` requires `siteId`,
`expectedSiteVersion`, `expectedActorId`, `bindingId`, and `expectedVersion`, and
soft-deletes the imported grant. Re-enrollment requires a new successful read
completed after revocation; retrying an old enrollment cannot restore the grant.
Revocation stops subsequent downloads that rely
on this grant. An independent native CRD or Astro create receipt can still prove
ownership; revoking an imported grant is not a blanket deny for the cluster.

Enrollment performs no native writes and does not download a credential. Every
`datastore.getWorkloadKubeconfig` still verifies the live CAPI cluster UID and
Secret owner reference, exact native cluster-ID label, admin mode, namespace,
and cluster-name label. Unlabelled legacy Secrets remain refused. The paired CLI
exposes `astro nvke access list`, `enroll`, and `revoke` with explicit reviewed
site/source/binding versions. The site's Imported workload access panel pages
binding metadata and supports reviewed revocation. To enroll, open a successful
controller `getCluster` operation and select Enroll workload access. The browser
prepares the exact UUID from the saved resource observation without downloading
the native result. Its confirmation names the site/version, native cluster UUID,
and source operation/version. A changed source or caller invalidates the review;
an unconfirmed write requires refreshing the grant list before reviewing a retry.
No grant command downloads credentials. Certificate renewal automation remains
pending.

## Historical credential maintenance observations

Open a successful controller `getCluster` operation to inspect its Credential
maintenance panel, or query `astroliftNvkeMaintenanceObservation` with `siteId`,
`operationId`, `expectedActorId`, and `expectedVersion`. Current organization
membership and `nvke.read` are checked before decrypting the saved response.
The requested UUID and returned native cluster UUID must match. Reads perform
no native I/O and do not download the raw result. A changed connection does not
erase historical evidence; the page identifies the saved observation time.

The projection reports the next scheduled renewal check, mutation-lock owner,
renewal cycle/workflow identity, accepted/succeeded/failed state, successful
`renewed` versus `unchanged` outcome, forced flag, timestamps, failed node and
known phase. It also reports etcd key-rotation workflow, phase, state, progress,
bounded failure reason and retained-lock evidence. Free-text messages, key
material, unknown fields and unsupported status values are excluded. Missing
observations remain unknown. Printable native workflow IDs, including Unicode,
are preserved within the native identifier size budget.

`renewalScheduleOverdueAtObservation` compares the next scheduled check with the
saved observation time, and is false while a recorded cycle is accepted. It is
unknown when scheduling or cycle evidence is incomplete. It is not an X.509
expiry date. A successful no-op cycle means `unchanged`, not that all credentials
were renewed. Provisioning success does not override failed maintenance or a
retained mutation lock. A failed cycle/rotation with its matching lock is shown
as retained ownership; absence of trustworthy lock evidence remains unknown.

Refreshing this panel rereads the saved observation. Run a new controller
`getCluster` operation to collect current status. This panel neither starts a
renewal nor resets workflows or clears locks. The pinned upstream operator paths
use native Temporal `RenewK8sCertsV2`, `RotateEtcdEncryptionKey`, and
`RetireLostEtcdRotationNode`; explicit native Temporal authority and reviewed
controls are tracked in #2665. Node certificate expiry/inspection metrics and
workload credential expiry remain separate qualification work under #2661.

## Native Temporal connection authority

Administrators can configure a separate native Temporal authority with
`configureNvkeTemporalAuthority`. Review `siteId`, `expectedActorId`,
`expectedSiteVersion`, and `expectedVersion` (zero for a new authority), then
provide an explicit `address` (`host:port`), `namespace`, and `taskQueue`.
`useTls` defaults to true; `serverName` optionally overrides the TLS server name.
`timeoutSeconds` bounds connection and namespace inspection, not a native
workflow's execution duration. This connection never inherits Astro's Temporal
endpoint, namespace, queue, or the site's HTTP credentials.

The optional write-only `credentials` object accepts `apiKey`, `caPem`,
`clientCertificatePem`, and `clientKeyPem`; client certificate and key must be
paired. Credentials are encrypted at rest. Omitting credentials preserves the
existing ciphertext without decrypting it; an empty object clears it. Plaintext
connections require explicit credential clearing and no TLS server name.
Configuration advances both authority and site versions and clears previous
verification. Audit records mask credentials and rejected connection addresses.

`astroliftNvkeTemporalAuthority(siteId)` returns scoped metadata and
`hasCredentials`, never credential values. `setNvkeTemporalAuthorityEnabled` and
`removeNvkeTemporalAuthority` require the same reviewed actor/site/authority
versions; removal soft-deletes the authority. Replacing it creates a new identity.
`verifyNvkeTemporalAuthority` checks the configured namespace using the native
connection and rechecks current permissions and unchanged authority around I/O.
`NAMESPACE_VERIFIED` proves namespace access only; it does not prove a worker is
polling the configured queue or that a workflow type is registered there.

The contract query's `nativeWorkflows` catalog separately exposes the three
pinned operator workflow input schemas, with upstream declaration and
registration checks. These are additional to the 88 HTTP/datastore actions.
This foundation does not start, reset, or retire native workflows. Reviewed
execution, recovery, and browser/CLI controls remain in #2665.
