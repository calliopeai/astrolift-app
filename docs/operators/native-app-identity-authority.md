# Original app authority for native identity work

`astrolift_services.native_identity_authority` is a private integration boundary
for the GCP identity journal. The staged human deployment path captures a private
Deployment-bound original reference for the selected cluster’s Endpoint-only
consumer graph. It does not install or activate the native identity pipeline.

Bearer authentication and browser-session tracking remain separate. A request
authenticated by an API token does not create, refresh, reassign, or revive the
sidecar for any browser cookie it happens to carry. Ordinary browser requests
and device-session issuance retain their existing tracking behavior. In
particular, an approver's bearer cannot revive or replace the initiating user's
tracked browser authority; withdrawn original sessions remain unavailable.

The frozen reference dataclass lives in the Django-independent
`astrolift_workflows.native_identity_inputs` module. The service module preserves
its existing import as an alias. Its fields and signed JSON are unchanged; it can
be serialized and returned by a real sandboxed Temporal workflow without importing
ORM/session code. Deserialization supplies no authority: the receiving activity
must still run the original-credential and current-target checks below.

At an actual authenticated HTTP boundary,
`capture_app_identity_authority(request, environment_guid=..., permission=...)`
requires the exact live app environment and either `app.update` or `app.deploy`.
It rechecks the actor, organization, original credential and current target
RBAC/ABAC before returning an `AcceptedAppIdentityAuthority`. Persist that exact
reference as part of the server-owned accepted operation and target/union digest;
never accept a caller-authored reference or infer authority from an ambient
worker's cloud credentials.

The reference contains internal actor ID, organization/app/environment and
original owner/placement GUIDs, the original API-token or tracked-session GUID,
permission, accepted token scopes and keyed metadata digests. It contains no
session key, bearer, token hash, password hash, cloud credential or request body.
The installed Django User has no GUID, so its internal ID is bound to the
original credential GUID and target chain. A metadata signature binds every
reference field. Configured `SECRET_KEY_FALLBACKS` permit application signing-key
rotation; retiring the old key invalidates references signed with it.

Capture binds the actual HTTP middleware's authenticated token to the current
credential context and rechecks its original hash and team. A concurrent scope
expansion cannot widen the accepted request's scope ceiling. Missing or substituted
credential contexts, credential rotation and team rebinding refuse capture.

Before credential discovery and around every provider response, the durable
caller enters `current_app_identity_authority(reference)`. This independently
loads the exact original credential, current active actor/member, live original
app owner and placement, current scope ceiling, current RBAC and uncached ABAC.
An API-token hash/team change or lost scope refuses. Browser checks use the
actual persisted Django session, authentication hash and tracked sidecar;
logout, deletion, revocation, expiry or rebinding refuses. Missing or ambiguous
sidecars cannot be reconstructed or replaced by another active session.

Browser sign-in time and factors are read from that actual persisted session.
Workers have no live caller network request: client IP remains unknown, and an
IP-required policy refuses. The module does not fabricate an HTTP request or
reuse an old request attribute cache. A Django session carrier is used only by
Django's authentication backend, never passed to the permission resolver.

The context yields the freshly loaded environment and restores tenant/token/
ABAC contexts on exit. These sequential reads are not an atomic authority
snapshot and cannot cancel an already sent remote write. Journal fences, fresh
coherent app-wide grant unions, reviewed source/configuration revisions and
SENT/UNKNOWN recovery remain independent requirements. An accepted reference
does not grant permission, prove cloud identity or establish rollout/inference
readiness. Actual public dispatch capture, Temporal activity integration and
the full GCP connection lifecycle remain separate work.


## Staged deployment handoff

Authenticated human GraphQL start, redeploy and target promotion capture the
actual browser or API-token origin. The private `DeploymentIdentityOrigin`
receipt binds that signed reference to the exact Deployment GUID and organization
with a keyed digest. It is append-only at both model and PostgreSQL levels;
rollback of its migration refuses retained history. No public input accepts a
reference, credential, approval count or private receipt. `config_snapshot`
contains no authority reference.

Admission concerns the complete app graph on the **selected physical cluster**,
including direct and attached Endpoint sources, retained IAM/preparation
journals, and retained `GCPAppIdentitySource` bootstrap history for that exact
original app/cluster tuple. Every retained source state requires origin capture,
including uncertain sends and an empty desired Endpoint graph. Capture refuses
if the retained app source’s original organization or provider registration no
longer matches current ownership, even when a replacement provider also calls
itself GCP. A shared
`GCPClusterIdentitySource` pin alone does not imply an app has native effect
history or change unrelated app deployments. Protected preparation operations retain their journal parent through
`PROTECT`; an empty desired graph cannot erase that origin requirement.
A separate GCP environment does not change ordinary AWS/local target deployment
behavior. A missing target is never guessed as a supported native origin.
Unsupported mixed-service unions refuse before creating a Deployment. Deploy-token,
webhook, scheduled, rollback and legacy promotion origins refuse relevant native
work before dispatch rather than manufacture a worker actor.

Approval retains the initiating actor and credential; the approver cannot replace
that authority. Before recording a vote, the original credential and all current
non-approval admission are checked. Only a valid unsatisfied ALLOW
`approval_required` condition may defer while the existing pending quorum waits.
At dispatch, full policy uses actual distinct recorded human votes. A changed
policy requiring more than the recorded deployment quorum refuses; this path
does not redesign initial deployment decorators or silently change quorum.

The private `DeploymentAuthorityContext(deployment_guid)` is a lookup key, not
proof. `deployment_app_identity_authority(reference, context)` reloads the exact
protected receipt and current vote ledger on each entry. Execution requires at
least the stored quorum of distinct identified human votes and evaluates current
policy with `approval_request=False`; a pending receipt cannot execute through
pre-vote deferral. Counters and an unidentified credential-link vote cannot
substitute for distinct human approval evidence. The pre-vote helper keeps its
existing bounded deferral behavior. The consumer applies votes only
to that original Deployment/app/environment. Different aliases in the complete
app union retain ordinary `approvals=0`; callers must not re-sign sibling
references or reuse the selected environment's votes. Ordinary
`current_app_identity_authority(reference)` defaults remain zero. Producer and
source-bootstrap consumers must explicitly carry this context when the real
receipt-bound executor is wired; no ambient/thread/global vote count is supported.

A private append-only `DeploymentExecutionReceipt` first records an EXPECTED
intent inside the originating dispatch transaction, before `on_commit` starts
Temporal. It pins the protected origin, Deployment/organization, canonical
workflow ID/type, a domain-separated canonical pure-input hash and a server
operation UUID. The input body, credentials and deployment configuration are
not stored in this receipt. A pending approval does not create an enqueue intent
until current original-caller quorum admission permits dispatch.

The first activity rechecks original-caller execution admission and atomically
adds the BOUND receipt using trusted `activity.info()` workflow/run IDs. The
mutable `WorkflowRun` mirror can be written later; it is not authority. Subsequent
`admitted_deployment_execution(input)` consumers must match the same protected
input and actual run and reload the original caller and votes on every entry.
Only current `pending` or `deploying` DeployApp execution can use the receipt;
terminal states, pending approval and legacy `redeploying` cannot borrow it.
Same-run retries/replay retain the same operation and binding. A new run, reset
or continue-as-new for the same Deployment refuses; a new Deployment has a new
intent. Any unresolved owned native journals still require their independent
recovery fences. This run binding is not invocation, workload or readiness proof.

Both EXPECTED and BOUND history refuse UPDATE/DELETE, including soft deletion.
Migration 0047 reverses only with empty execution history; do not delete retained
history to downgrade. Failure-status writes also require the expected input and
actual workflow, and the original run when already bound. A tampered payload or
different run cannot mark the accepted Deployment failed. A withdrawn original
with an unchanged unbound intent may record fixed failure metadata; it cannot
bind a run or proceed to native effects.

The registered first activity checks the private receipt, pure workflow input and
current original authority before manifest, build or native effects. Valid native
requests then fail explicitly with `NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED`.
Its failure activity writes only fixed Deployment status metadata, without Secret
restoration or GitHub reflection. Patch markers preserve replay of older histories;
older native histories do not acquire these new guarantees. This is staged source,
not an available complete deployment capability or live cloud acceptance.

## Deeper deployment consumers

The private producer, source bootstrap and original-source reader accept an
explicit `deployment_context`. Each current checkpoint uses the same strict
`DeploymentAuthorityContext` lookup, exact protected origin and fresh vote ledger;
no approvals are copied into the signed authority or read from ambient state.
The accepted producer template includes the Deployment lookup key in its source
fingerprint. Every environment alias is evaluated against its persisted cluster
placement region. The separate Vertex region identifies a native model source
and supplies no application placement authority. Selected-environment votes and
pre-vote deferral cannot authorize sibling aliases.

Pending GSA source acceptance retains the exact Deployment context as separate
metadata beside the unchanged signed authority fields in its protected
`authority_reference`. Two starts by the same caller on the same environment can
have identical signed references; this additional acceptance fence prevents one
Deployment's approval from resuming the other's pending create. A legacy pending
source without that context cannot acquire it retrospectively. Only the original
pending acceptance may resume under restored current policy. An already
`OBSERVED` source can be reused by a separately admitted later Deployment without
changing its original acceptance, account identity or creation evidence.

The narrow post-send evidence path compares the original reservation and full
stored acceptance, including its context, without looking up current approval
or granting permission. Withdrawal after a reply can retain the original UID as
evidence; it cannot authorize observation, another send or a different context.
Registered native activities can open the private scoped
`deployment_execution_checkpoint(input)` context and pass its `current` callback
explicitly into source bootstrap, original-source reads and Endpoint plan
production/refresh. Capture happens only inside the actual activity; every
callback reloads the protected origin, votes, input/run binding and current
Deployment status. Trusted SDK cancellation refuses both the bare admission
helper and the scoped callback. The callback also refuses once its local scope
closes, even if a copied Activity context remains available. A cancellation
request or a closed scope is not a query of Temporal final state.

Credential metadata threads run a fresh copy of the captured SDK context for
that invocation, with no ambient or serialized execution authority. Their own
default Django connection is closed after the complete callback; no global
connection cleanup sweeps unrelated aliases. An existing default connection or
transaction on an outer metadata invocation refuses without closing it. Nested
checks inside the callback-owned source transaction re-admit without closing
that transaction; the activity owner's active transaction stays open. No permission, votes or admission result survives a native wait. Current
source transitions call the callback after acquiring their parent/source locks;
post-send `current=False` evidence/unknown transitions retain only original
acknowledgement evidence without current execution authority. Only first binding
locks EXPECTED; later checks read immutable EXPECTED/BOUND history without row
locks, avoiding waits while source/preparation/IAM parents are held. Missing or
inconsistent binding still refuses.

These callbacks are optional for existing standalone private consumers and do
not activate the staged deployment path. The actual native pipeline must wire
them at every source, preparation/IAM, render/apply and runtime boundary, close
its clients within the activity scope, and complete the remaining
apply/runtime/inference chain before public activation.
