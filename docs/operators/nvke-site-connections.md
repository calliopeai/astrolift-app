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
role migration. Custom roles can grant them separately. Ordinary cluster bearer
scopes do not carry NVKE authority. Explicit automation scopes avoid the broad
platform `admin` wildcard:

| Token scope | Exact permission ceiling |
|---|---|
| `read:nvke` | `nvke.read` |
| `manage:nvke` | `nvke.manage` |
| `admin:nvke` | `nvke.admin` |

Select read separately for operations that require both read and mutation
authority. The token picker provides NVKE reader/operator presets and shows the
intersection with the owner's current roles. These scopes hold only in the
token's issuing organization. They grant no app, generic cluster, secret-store,
token-minting or future NVKE permission. Existing broad `admin` tokens retain
their behavior; no existing token or default device session gains NVKE scopes.

`astro auth login --scope nvke-read` requests only `read:nvke`.
`astro auth login --scope nvke` requests all three NVKE scopes, including workload
credential access and native maintenance where the account's roles permit it.
The browser names those scopes and explains the sensitive administrator access.
Both profiles require the server capability `identity.nvke_scopes`; the CLI
refuses older servers before starting sign-in. Refresh retains the approved
profile, while preapproved enrollment cannot gain it by claiming a client label.
Membership, role removal, token expiry/revocation and the native worker's
original-actor checks still apply. For unattended use, mint an expiring API token
with the minimum NVKE scopes through the existing token-management screen.

## Interfaces

Credential observations use an explicit site metrics authority with its own full
scrape URL, encrypted bearer/TLS credentials, timeout and version. Configure,
verify, disable and remove it through the GraphQL metrics-authority mutations.
Verification records `METRICS_READ`, which proves readable pinned metrics rather
than successful collection or cluster health. The controller serves these gauges
on its separate metrics listener; Astrolift does not infer its address or reuse
REST or management kubeconfig credentials. Browser and CLI controls remain open
under #2666, a child of #2661.

The reader pins all 18 credential gauges and 16 kubeadm credential names from the
upstream collector and agent catalog. It selects only an exact cluster name's
series and global subsystem flags. Admission requires a successful `getCluster`
receipt under the current site/controller authority and its exact version. The
selector comes from that encrypted receipt, never a caller-supplied cluster name.
Live controller reads before and after the scrape must still prove the same UUID
and name, a provisioned cluster and no deletion. It preserves exact reported numeric values as
decimal strings. Prometheus upstream gauges use float64, so this does not recover
precision already lost by the exporter. Stored certificate NotAfter, fresh
successful inventory, latest inspection availability, and monitoring enablement
remain independent facts. Missing series and a failed collection are not healthy
credential evidence. Renewal schedule timestamps never become expiry dates.
The exporter labels clusters by name rather than UUID. The controller checks
bind that selector, but cannot prove which cluster incarnation produced a retained
gauge after name reuse. These are exporter-reported observations; inspection
availability and reported observation age remain relevant.

`requestNvkeCredentialObservation` reserves the actor-owned `metrics.credentials`
outbox operation. It requires both `admin:nvke` and `read:nvke` access, checks the
original caller and all source/authority versions throughout execution, and keeps
the selected samples encrypted outside Temporal history. The request UUID recovers
the original receipt before changed-authority admission checks. Cancellation or an
observation deadline stops this read without changing the native cluster.

READ-authorized `astroliftNvkeCredentialObservation`,
`astroliftNvkeCredentialMetricsPage` and `astroliftNvkeCredentialCertificatesPage`
expose version-bound historical views with signed, caller-bound pagination and
server-side search. They retain the scrape time and distinguish a retained expiry
from latest inspection availability. Expiry comparison is explicitly **at scrape
time**. Missing observations remain unknown. Historical reads do not contact the
native listener and survive later authority rotation; raw result access still
requires administrator permission. Local HTTP/TLS, PostgreSQL, GraphQL and real
Temporal tests qualify this flow; live collector/node inspection remains to be
qualified on an NVKE installation.

Reads have response/sample budgets, socket and elapsed scrape deadlines, explicit
TLS/bearer configuration, no redirects, no ambient proxy credentials and no
automatic retries. Conflicting/duplicate or timestamped samples, undeclared
labels/credentials/states and nonfinite values fail explicitly. Private endpoint
paths, filters and HTTP headers are excluded from HTTP logs and OpenTelemetry/
Sentry client spans for
both the new reader and existing REST/datastore transport. Unrelated HTTP
logging and tracing remain active. Real HTTP/TLS and instrumented-client tests
qualify this boundary; they do not qualify native node inspection or alerts.

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
Reviewed execution, owner history and rotation reset are available through the
APIs below, the paired CLI commands, and the browser controls on successful
controller `getCluster` operation pages described in this guide.

## Reviewed native workflow requests

`requestNvkeNativeWorkflow` accepts `RequestNvkeNativeWorkflowInput`. Review the
current `siteId`, `expectedSiteVersion`, `expectedActorId`, and
`expectedAuthorityVersion`, choose a new UUID `requestId`, and provide `workflow`
from the native catalog and `parametersText` containing its exact JSON input.
`sourceOperationId` and `expectedSourceVersion` must identify a successful
controller `getCluster` observation for the same cluster UUID, collected using
the current site/controller authority. Current `nvke.admin` and `nvke.read` are
required. The complete input text is masked in the mutation audit and encrypted
in the outbox. Astro's own Temporal history carries operation IDs only; the
native Temporal service necessarily receives the native workflow input.

Use the same request UUID and reviewed inputs to recover a lost admission reply,
or query `astroliftNvkeRequest`. Changing any reviewed input under that UUID is
refused. One unresolved native maintenance request per cluster blocks another.
The native ID is `astro-nvke-<operation GUID>`; retain it for operator recovery.
Dispatch uses RejectDuplicate reuse and Fail conflict policies, with a stable
Temporal start request ID. Sending state is durable before dispatch. A lost
start acknowledgement is recovered only from matching native history: type,
queue, exact input, original run, and request proof must agree. Missing history
does not permit another start. An automatic retry never replaces a succeeded
rotation or starts a new rotation under its old owner.

`timeoutSeconds` (30–86400; default 7200) limits Astro observation. It does not set
a native workflow run/execution timeout, cancel a native owner, or constrain the
six-hour etcd rewrite. An observation timeout after dispatch becomes uncertain;
`resumeNvkeOperation` with the reviewed operation version extends observation of
the original identity. A reset/continued/retried native run is not silently
adopted. A cancellation requested before sending prevents dispatch; requesting
cancellation after sending continues observation and does not cancel or terminate
the native workflow. Retain the native owner's lock and follow its recovery
procedure. Authority changes require investigation rather than endpoint fallback.

Lost-node retirement additionally requires `acknowledgeLostNodeTeardown: true`,
the exact `nodeID`, and the retained `rotationWorkflowID`. The reviewed cluster
observation must report that owner's failed rotation, retained mutation lock,
and one of `add_secondary`, `promote_primary`, `rewrite`, or `remove_old_keys`.
Before dispatch Astro verifies a matching closed, unsuccessful native rotation
with the exact cluster input and configured queue. The native retirement workflow
then performs its live node/quorum/ownership checks and can permanently remove
that control-plane node. Backups, survivor rewrite checks, and a reviewed owner
reset remain operator recovery requirements; Astro does not clear the lock.

`native_workflow_completed` records successful native workflow completion only.
Collect fresh controller observations and site qualification evidence to prove
certificate outcomes, cleared rotation ownership, all-node convergence, and
preexisting Secret rewrite/readability. The local Temporal transport tests use
fixture workflows; they do not qualify native hardware or encryption changes.

## Browser native Temporal connection controls

The site's Native Temporal panel reads connection metadata without caching it.
Administrators can configure, verify namespace access, enable, disable, and
remove the registration. Configuration reviews the explicit target, namespace,
queue, TLS/credential mode, site version and authority version before submission.
Disabling or removing the registration requires confirmation; it does not cancel
native workflows or release their locks. Read-only users can inspect metadata
and refresh it.

Credential replacement fields are write-only. They are excluded from review
text and persistent browser storage, cleared after submission/closing, and
discarded with the draft when caller/site/authority scope changes. Preservation
sends no replacement bundle; clearing is explicit. Current actor and versions
are sent with each mutation. A late response from an old caller visit cannot
update the current panel, including switching away and back to the same caller.

An unconfirmed mutation blocks further writes until metadata is refreshed.
Refresh and review the resulting versions before another change. Namespace
verification is labelled as namespace access evidence and does not claim worker
availability, certificate renewal or encryption readiness. Browser native
workflow submission and history-reset controls remain separate work in #2665.

## Native owner history inspection

`astroliftNvkeNativeHistory(input: ReadNvkeNativeHistoryInput!)` returns a typed
result envelope for a reviewed native owner. Supply `siteId`,
`expectedSiteVersion`, `expectedActorId`, `expectedAuthorityVersion`,
`sourceOperationId`, `expectedSourceVersion`, and `workflow` (`RenewK8sCertsV2`
or `RotateEtcdEncryptionKey`). The source must be a successful current-authority
controller `getCluster` observation. Its renewal cycle/rotation owner supplies
the native workflow ID; callers cannot substitute an arbitrary owner. Both
`nvke.admin` and `nvke.read` are required before credentials are opened.

Inspection checks the native workflow type, queue, original input's exact cluster
UUID and current run. Every I/O boundary rechecks current caller/token, site,
native/controller authority and source observation. The response identifies the
native run/status and inspection time separately from `recordedState`,
`recordedPhase`, `retainedMutationLock` and `sourceObservedAt` from the saved
controller read. A recorded failure does not override a now-completed native run.

`items` contain decimal-string event IDs, event kinds, timestamps, known pinned
activity names and scheduling/workflow-task-completion IDs. Inputs, results,
failure messages, signal data, credentials, worker identities and unknown
activity names are never returned. Event IDs stay exact beyond JavaScript's
integer range. Inspect scheduled activities and their workflow-task-completion
references when reviewing a recovery boundary; no first-task shortcut is chosen.

Use `limit` 1–200 (default 50) and return `nextCursor` as `after` with the same
review and page size. Cursors expire after 30 minutes and bind caller,
site/authority/source, owner and run. A changed run invalidates the cursor rather
than mixing histories. Only decoded, retained history is supported for this
recovery view. Refresh the controller observation and restart inspection after
authority or source changes.

This query performs no reset, cancellation, node mutation or lock release.
Temporal reset can terminate the current run even when given an older base run;
local qualification also produced another run when the same reset request was
replayed after its first reset run completed. Reset admission/recovery must not
depend on repeated reset RPCs as a generic lost-reply strategy. Phase-specific
boundaries, operator confirmation of terminal conflicting node work and durable
reset-history proof are required before dispatching the reset described below.

## Reviewed native rotation reset API

`requestNvkeNativeReset` admits a durable `NvkeOperation` for the documented
`RotateEtcdEncryptionKey` recovery procedure. It requires `nvke.admin` and current
`nvke.read` authority, the original actor ID, site/version, explicit native
Temporal authority/version, and a successful exact `getCluster` source operation
ID/version. The source must still report the retained rotation owner, phase and
mutation lock. Execution reads the controller again before history review and
immediately before dispatch. A completed native owner is refused even when the
controller observation still reports failure. Renewal reset is refused: upstream
requires a fresh cycle or incident-specific recovery, not reopening a failed cycle.

Review `workflowId`, exact `baseRunId`, decimal-string `workflowTaskEventId` and
`activityEventId`, and the pinned `activityType`. The activity must have been
scheduled by that exact workflow task. A successfully completed Begin activity
must precede it and prove this cluster and owner. The first workflow task is
never an accepted boundary. The last successfully recorded native phase before
the boundary must match the review, preserving earlier phase work. Choose the
recorded `phase` and one `mode`:

| Mode | Required boundary |
|---|---|
| `retry_activity` | An unsuccessful phase-appropriate activity. Key rollout inputs must match this cluster and phase. Post-rewrite checks must preserve the acknowledged rewrite start. Successful earlier mutations are refused. An unsuccessful Record phase transition can retry only the next phase for the retained owner. A rollout result containing a native business error counts as unsuccessful. |
| `resume_rewrite` | A rewrite status poll retaining the exact acknowledged node and operation ID, within the original six-hour allowance. |
| `restart_rewrite` | The rewrite Start activity, after confirming earlier conflicting rewrite work is terminal. This can intentionally replay an earlier Start result. |
| `refresh_survivors` | Prepare after Begin, following a completed `retirementWorkflowId` whose native input proves this cluster and retained rotation owner. Available before finalization in the distribution/rewrite/removal phases. |

A retained `prepare` owner is accepted only when its controller state remains
running and its native run was terminated or timed out. A recorded prepare
failure uses a fresh workflow ID after lock release. Finalization recovery accepts
only Finalize or Complete boundaries, preserving completed key distribution.

Supply nonempty `rationale` and `quiescenceEvidence` references, each at most
4,000 UTF-8 bytes, and explicitly set both `acknowledgeBoundaryReviewed` and
`acknowledgeConflictingWorkStopped`. These are operator attestations: Astro does
not independently inspect agent processes or prove hardware quiescence. They
must cover late activities and node work that can outlive a closed Temporal run.
When resuming a matching acknowledged rewrite, confirm that *conflicting* work is
stopped; the matching rewrite may still be running. Retirement workflow completion
proves its recorded native execution, not an independent hardware observation.

Coordinate external Temporal operators during review and dispatch. Astro checks
that the exact reviewed run is still current immediately before reset, but the
native reset RPC has no atomic compare-and-swap against another client's reset.
A base run ID alone does not protect a newer current run from termination.

Astro encrypts the review and evidence at rest and redacts both text fields in
mutation audit variables. They are not passed as reset reasons. Native reset
history receives only an opaque Astro operation ID and keyed review digest.
Each request ID is bound to one immutable review; changing any reviewed field
requires a new request ID. An unresolved maintenance operation blocks another
for the cluster even if the operation was soft-deleted.

Dispatch persists a one-way sending state before one reset RPC, with SDK retry
disabled. A crash before that RPC can therefore leave an unconfirmed intent that
requires operator investigation. Astro never resends it. Lost replies are
recovered only from a matching native reset history event containing the exact
base/new run IDs, reviewed workflow-task boundary and opaque intent proof.
Common operation resume performs observation only once sending was recorded.
The observation deadline never cancels the native workflow or releases its lock.

A successful operation means that the proved reset workflow completed; it does
not independently certify encryption health. Local qualification uses real
PostgreSQL, controller HTTP reads and Temporal with fixture workflows. Full native
controller, agent and topology qualification, plus deployed browser qualification,
remain release requirements tracked in #2665.


The paired CLI exposes `astro nvke temporal workflow history WORKFLOW` with the
same site/authority/source versions and explicit `--after`/`--limit` pagination.
`temporal workflow reset --file private-review.json --request-file recovery.json`
submits the typed rotation reset review, with explicit site/authority/source
versions. `temporal workflow reset recover --request-file recovery.json` performs
receipt lookup only. Review input uses protected private files or stdin. The CLI
flushes a separate reset recovery identity containing metadata and digests before
admission, preserves the exact original UTF-8 review on pending retries, and
checks an existing receipt before opening the review file. Its recovery file
cannot substitute for a native workflow-start or HTTP/resource recovery record.
Compiled CLI qualification also exercises paginated renewal/rotation history,
lost native reset replies, recovery after review-file removal and old-cursor
refusal after a native run changes.


### Browser native admission foundation

The shared browser recovery journal recognizes native workflow-start and reset
records with explicit authority and source versions. It shares the same site key
and Web Lock as HTTP/resource admission, so an unresolved request cannot be
replaced by switching editors. Only metadata and a digest persist; native JSON,
reset rationale/evidence and credentials remain out of storage. Native JSON text
is sent verbatim. Typed reset field ordering does not change its digest, and
native event IDs remain decimal strings through the GraphQL boundary.

Recovery checks the original receipt before private input or a new admission.
Lookup-only recovery requires no private review. Actor visits and the stored
identity are checked immediately before submission, and a late response cannot
archive a request from an old visit. A first structured admission rejection can
be archived for the declared pre-insertion failure codes. `INVALID_INPUT` for an
already unconfirmed native request retains the journal because native input
validation can precede receipt lookup. Native `PRECONDITION`, `RESOURCE_BUSY`
and `UNSUPPORTED` refusals archive only a newly issued request's first structured
reply. They cannot discard an older request whose outcome remains unknown.

The history response guard binds site/authority/source versions, cluster, owner
and native run before publishing a page. It rejects inexact IDs, out-of-order
events, invalid scheduling references and mismatched pages. Native status and
controller-recorded state remain separate. The application hook uses these guards
for summary and cursor reads, with every page bound to the inspected run.

The real browser journal qualification runs without a live NVKE site:

```sh
cd frontend
npx vitest run lib/nvke graphql/nvke components/screens/nvke
npx playwright test --config playwright.journal.config.ts
```

The Playwright fixture serves the application protocol modules in an owned
loopback origin and exercises actual Web Locks/localStorage across two tabs.
Its admission/receipt callbacks are fixtures; it does not qualify native node
work, deployed UI or backend workflow execution.


### Browser maintenance review components

The Storybook `NvkeWorkflowPanel` provides all three pinned native workflow
choices and their JSON/schema editor, owner-history selection and cursor-list
presentation, and reviewed rotation reset forms. Successful controller
`getCluster` operation pages connect it through `useNvkeWorkflows`. Execution and
history inspection require current read/admin permissions, enabled explicit
native authority, and a source observation recorded at the current site version.
Historical maintenance metadata remains readable after site authority changes,
but cannot authorize native execution or history inspection; obtain a new
controller observation first. The contract's `inputSchemaText` preserves exact
native integer bounds when rendering the schema in a JavaScript browser.

Receipt-only recovery requires read permission and the original actor's journal;
it remains available after admin permission or native authority is removed.
Storage/Web Lock failures disable new admission while permitted owner inspection
remains available. Retirement additionally requires inspection of the closed,
unsuccessful current rotation run, its retained lock and a recoverable source
phase before its workflow choice becomes available.

Workflow input text remains verbatim. The selected cluster must match the source
observation; retirement additionally names the exact retained rotation owner and
node and requires permanent-removal acknowledgement. Renewal confirmation shows
force/schedule metadata. Unsafe-size integer input is not rounded into its
confirmation; the original JSON remains authoritative and backend-validated.

A reset proposal starts from an inspected scheduled activity, retaining exact
string run/activity/workflow-task IDs and the recorded phase. Begin is not a
selectable shortcut. A mode must be chosen explicitly, with guidance about the
original rewrite deadline, old-operation termination and survivor retirement.
Both boundary review and stopped conflicting node-work attestations are required.
Changing mode clears those attestations. Rationale/evidence stay out of
confirmation text and clear on closing/submission or scope/authority/source/run
changes. Completed, running, stale, mismatched or unauthorized history cannot
propose a reset. Backend phase/history proof remains the dispatch authority;
activity selection is not a client-side safety proof.

Native receipt pages now map the four declared maintenance operation keys to
`nvke.admin`, in addition to the read access required to enter the page. Arbitrary
Temporal names do not inherit this mapping. Capability changes invalidate pending
private result reads, including administrator revocation within the same actor
visit. Native completion copy does not certify site health; cancellation and
reconciliation explain the native owner/lock and no-replay semantics.

Local verification includes form interactions, all eight locale catalogs,
Storybook render/build, the production Next build and browser checks of metadata,
exact IDs, private-field clearing, accessibility and narrow layouts. These checks
use callback fixtures and do not qualify live native controller or node changes.
Connected hook tests exercise exact schema/input text, stale site observations,
lost replies, authority removal, read-only receipt recovery, storage refusal,
changed runs and actor visits. These isolated checks do not prove backend
integration; the separate connected qualification below exercises that boundary.

### Connected local browser qualification

`test_nvke_browser_integration.py` starts owned loopback Storybook and ASGI
servers against real PostgreSQL and Temporal. It runs the shipped maintenance
hook and panel in Chromium. A successful controller observation executes through
the real Astro workflow worker; controller responses and the native rotation
worker are fixtures. The browser inspects the failed retained rotation, selects
its rewrite activity, reviews the exact run/event boundary and submits a reset.

The test drops the API reply after durable admission and separately drops the
native reset RPC reply after Temporal accepts it. Receipt-only browser recovery
must recover the original request without another admission. Assertions require
one durable operation, one native reset RPC, completed resumed native work,
encrypted private review storage, redacted audit variables, and no private
review or cluster input in Astro workflow history. The token is injected through
child stdin and browser memory, never story args, URLs or persistent storage.
The harness refuses non-loopback origins and is only present in Storybook.

Build the catalog, then run with the backend's configured disposable PostgreSQL,
Redis and Temporal test environment and installed Playwright Chromium:

```sh
cd frontend
npm run build-storybook -- --output-dir /tmp/nvke-connected-storybook
cd ../backend
ASTROLIFT_NVKE_STORYBOOK=/tmp/nvke-connected-storybook python -m pytest \
  astrolift_clusters/tests/test_nvke_browser_integration.py \
  -o asyncio_mode=auto -q
```

The test skips unless a static build is explicitly selected; a selected build
with missing Node/Chromium or unavailable services fails. This qualifies the
connected local reset/recovery flow. It does not qualify deployed login, live
native controller/agent/hardware changes, or every browser workflow procedure.
