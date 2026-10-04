# Original app authority for native identity work

`astrolift_services.native_identity_authority` is a private integration boundary
for the GCP identity journal. Existing public mutation/workflow contracts do not
capture these references yet. The module enables no model connection or cloud
effect on its own.

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
