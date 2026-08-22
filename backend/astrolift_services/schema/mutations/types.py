"""Strawberry input and payload types for the mutation package."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID


@strawberry.input
class SetAppSecretInput:
    """Sets an app-level [env] literal in the staged manifest buffer.

    The frontend's secrets editor surfaces a 'set' UX; on save the
    mutation writes to ``manifest_raw_staged`` (same buffer #277
    set up). The user follows up with ``pushManifestToRepo`` to
    open a PR with the changes."""

    app_slug: str
    key: str
    value: str
    # Optimistic-concurrency gate (#497). When set, the mutation
    # compares against ``RegisteredApp.version`` and refuses to apply
    # the secret write if the app row has moved on since the caller
    # fetched it (a concurrent settings/secret edit, etc.). Null =
    # skip the check (back-compat).
    if_match_version: int | None = None

    # ---- sidecar metadata (#677 / #678) -----------------------------
    expires_at: dt.datetime | None = None
    """Operator-declared rotation deadline (#677).  When supplied,
    the resolver upserts an ``AppSecretMetadata`` row for the (app,
    env, key) triple.  Omitted/null preserves whatever the previous
    metadata row held (no clobber)."""

    set_via: str | None = None
    """Provenance tag (#678).  Defaults to ``web`` for set/rotate
    writes that don't supply it; a CLI / env-paste / managed-service
    integration may pass an explicit value so the FE can render
    'Set via CLI on May 12' in the secret-row's last-edited tooltip."""

    scope: str = "all"
    """Audience scope (#752).  One of ``all`` / ``production`` /
    ``preview`` / ``preview:<branch>``.  Persisted on the sidecar
    metadata row; resolution-time filtering drops the secret for envs
    that don't match."""


@strawberry.input
class SetAppSecretMetadataInput:
    """Standalone metadata edit (#677 / #678).

    Lets the FE edit expiry / provenance without re-writing the
    underlying secret value — operators sometimes annotate metadata
    after the value was set by an integration that didn't tag it.
    """

    app_slug: str
    key: str
    environment_name: str | None = None
    """Empty / None means 'applies to every env that surfaces this
    key' — used by the FE's wildcard editor.  A concrete env-name
    creates a per-env override row."""

    expires_at: dt.datetime | None = None
    """Null clears the deadline.  A concrete datetime sets it."""

    set_via: str | None = None
    """One of ``web | cli | env_paste | bundle | managed_service``.
    None preserves the existing value when the row already exists;
    on first creation defaults to ``web``."""

    scope: str = "all"
    """Audience scope (#752).  Defaults to ``all``."""


@strawberry.input
class RotateAppSecretInput:
    """Rotate an app-level [env] literal (#726).

    Wire-identical to ``SetAppSecretInput`` and resolves through the
    same staging + proposal pipeline. The only difference is the audit
    action — rotations emit ``app.secret.rotate`` so SRE can answer
    'has anyone rotated this key in the last 90 days' as a distinct
    question from 'has anyone touched it'."""

    app_slug: str
    key: str
    value: str
    if_match_version: int | None = None

    # ---- sidecar metadata (#677 / #678 / #752) ----------------------
    # Same shape as ``SetAppSecretInput``.  A rotation is naturally a
    # moment to refresh expiry; the FE pre-fills the existing value
    # so an operator's "rotate" click without changing the expiry
    # carries the previous value back through.
    expires_at: dt.datetime | None = None
    set_via: str | None = None
    scope: str = "all"


@strawberry.input
class DeleteAppSecretInput:
    app_slug: str
    key: str


@strawberry.input
class BulkImportAppSecretsInput:
    app_slug: str
    dotenv_text: str
    """Multi-line ``KEY=value`` text (.env shape)."""


@strawberry.input
class AttachSecretBundleInput:
    app_slug: str
    environment_name: str
    bundle_slug: str
    prefix: str | None = None


@strawberry.input
class DetachSecretBundleInput:
    attachment_id: GUID


@strawberry.input
class RevealAppSecretInput:
    """Persistent reveal-on-demand for one secret row (#424).

    `app_slug` scopes the lookup to one app (the caller's current page);
    `secret_id` is the stable id minted by `_list_app_secrets` —
    ``{source}:{env}:{key}`` — so the UI can ask for "this row I'm
    looking at" without re-deriving the source/env tuple.

    Reveal is intentionally an explicit per-row mutation rather than
    a query: every successful call audit-logs the actor + IP so the
    org's audit trail captures the disclosure.
    """

    app_slug: str
    secret_id: str


@strawberry.input
class ProposeSecretChangeInput:
    """Explicit proposal creation (#488).

    Same surface as ``setAppSecret`` / ``deleteAppSecret`` /
    ``attachSecretBundle`` / ``detachSecretBundle`` rolled into one
    discriminated input.  Callers can use this even when the app does
    NOT have ``requires_secret_approval=True`` — opt-in workflow for
    teams that want the review trail without flipping the gate.

    When the app DOES have the gate on, the legacy mutations proxy
    through this same path automatically; the discriminated payload
    here is the canonical shape.
    """

    app_slug: str
    op: str
    """``set | delete | attach_bundle | detach_bundle``."""

    environment_name: str | None = None
    """Required for attach/detach; optional for literal set/delete
    (which are app-wide writes against ``manifest_raw_staged``).  The
    env name is still captured on the row for display purposes when
    provided."""

    key: str | None = None
    """Required for set / delete."""

    value: str | None = None
    """Required for set."""

    bundle_slug: str | None = None
    """Required for attach_bundle."""

    prefix: str | None = None
    """Optional for attach_bundle."""

    attachment_id: GUID | None = None
    """Required for detach_bundle."""


@strawberry.input
class ApproveSecretChangeInput:
    proposal_id: GUID
    reason: str | None = None


@strawberry.input
class RejectSecretChangeInput:
    proposal_id: GUID
    reason: str
    """Required (non-empty) — every rejection leaves an explanation
    on the audit trail."""


@strawberry.input
class WithdrawSecretChangeInput:
    proposal_id: GUID


@strawberry.input
class RotateSecretBundleInput:
    """Fire the ``RotateSecretBundleWorkflow`` for one bundle. The
    workflow re-fetches values from the SecretsBackend, applies them
    across every cluster the bundle is referenced on, then bounces
    every Deployment that envFroms the bundle so pods pick up the
    new values immediately (#365)."""

    id: GUID


@strawberry.input
class ProvisionManagedServiceInput:
    app_slug: str
    environment_name: str
    kind: str
    """Catalog kind: postgres | redis | object_store | queue | ..."""

    name: str | None = None
    variant: str | None = None
    config: strawberry.scalars.JSON | None = None
    isolation: str | None = None
    """``shared`` | ``dedicated``, or null to take the org's compliance
    floor and then the variant default. Not part of ``config``: driver
    config schemas are per-variant and several close themselves to extra
    keys, so a portable request cannot travel inside one."""


@strawberry.input
class ProvisionProjectManagedServiceInput:
    project_id: GUID
    cluster_id: GUID
    environment_name: str = "production"
    kind: str = ""
    name: str | None = None
    variant: str | None = None
    config: strawberry.scalars.JSON | None = None
    isolation: str | None = None
    agent_environment_spec_slugs: list[str] = strawberry.field(default_factory=list)
    app_environment_ids: list[GUID] = strawberry.field(default_factory=list)


@strawberry.input
class AttachProjectManagedServiceInput:
    managed_service_id: GUID
    agent_environment_spec_slug: str | None = None
    app_environment_id: GUID | None = None


@strawberry.input
class DetachProjectManagedServiceInput:
    attachment_id: GUID


@strawberry.input
class CreateProjectSecretBundleInput:
    project_id: GUID
    cluster_id: GUID
    name: str
    slug: str
    backend_ref: str | None = None


@strawberry.input
class UpdateProjectSecretBundleInput:
    id: GUID
    name: str
    backend_ref: str


@strawberry.input
class ProjectSecretBundleKeyInput:
    bundle_id: GUID
    key: str
    value: str | None = None


@strawberry.input
class UpdateManagedServiceInput:
    id: GUID
    config: strawberry.scalars.JSON | None = None
    name: str | None = None


@strawberry.input
class ReprovisionManagedServiceInput:
    """Trigger a full reprovision cycle for a managed service (#745).

    Use when a config change requires tearing down the backing cloud
    resource before re-creating it — i.e., the changed key is NOT in
    ``AstroliftManagedService.editableFields``. The service status
    transitions to PENDING; the lifecycle workflow loop picks it up.
    """

    managed_service_id: GUID


@strawberry.input
class DeprovisionManagedServiceInput:
    """Two-axis safety surface for the managed-service deprovision (#320).

    ``deleteData`` False (default): the driver takes the safest deletion
    path — RDS final-snapshot, S3 retains contents, queues drain. The
    artifact survives for later restore.

    ``deleteData`` True: irreversibly delete persistent state.

    ``forceDestroy`` False (default): respect cloud-side deletion-
    protection flags; refuse with an error message when a guard trips.

    ``forceDestroy`` True: bypass guards (suspend versioning, ignore
    deletion-protection, --atomic cleanup). Terraform-style semantic.

    The UI surfaces both as separate explicit checkboxes so destructive
    paths can't be triggered accidentally.
    """

    id: GUID
    delete_data: bool = False
    force_destroy: bool = False


@strawberry.input
class RevealManagedServiceConnectionInput:
    """Audit-logged reveal of the connection envelope key set for one
    bound managed service (#401).

    Mirrors `RevealAppSecretInput` (#424): the call is explicit and
    every successful reveal lands in the audit log via the
    @mutation_audit decorator + a sibling emit_audit row carrying the
    client IP.

    The returned envelope NEVER contains plaintext values — the actual
    credentials live in the platform secrets backend and aren't reachable
    from this API surface. The reveal exposes the envelope key set
    (variable names) + the `connection_secret_ref` pointer so an
    operator can confirm which env vars the workload sees and where to
    find the values in the secrets backend.
    """

    managed_service_id: GUID


@strawberry.input
class AddEmailSuppressionEntryInput:
    """Manually add an address to the email backend's account-level
    suppression list (#631).

    Used by operators to suppress a known-bad recipient before the
    backend auto-suppresses (e.g. an abusive recipient submitting fake
    complaints, or a recipient that's been informed via support that
    they'll stop receiving mail).
    """

    managed_service_id: GUID
    address: str
    """Recipient address to suppress."""

    reason: str = "MANUAL"
    """``BOUNCE`` / ``COMPLAINT`` / ``MANUAL`` — defaults to MANUAL for
    operator-initiated entries."""

    note: str = ""
    """Free-form audit note explaining why the operator suppressed this
    address (e.g. support ticket id). Persisted in the audit row, not
    on the suppression entry itself (the cloud doesn't carry it)."""


@strawberry.input
class RemoveEmailSuppressionEntryInput:
    """Manually remove an address from the suppression list (#631).

    Used when the original suppression was incorrect (false-positive
    bounce, complaint generated by a malicious mailbox the recipient
    has since shut down). Idempotent — removing an absent address
    returns ``ok=True`` with ``data.removed=False``.
    """

    managed_service_id: GUID
    address: str


@strawberry.type
class _EmailSuppressionAddPayload:
    address: str
    reason: str


@strawberry.type
class _EmailSuppressionRemovePayload:
    address: str
    removed: bool
    """``True`` if the address was on the list and has been removed;
    ``False`` if the address wasn't on the list (idempotent)."""


@strawberry.input
class CreateEmailTemplateInput:
    """Create a new transactional-email template (#635).

    ``name`` is the unique identifier the workload references on the
    backend's ``SendTemplatedEmail`` call. ``subject`` / ``htmlBody`` /
    ``textBody`` carry the template contents (backend-side substitution
    syntax, e.g. SES's ``{{handlebar}}``)."""

    managed_service_id: GUID
    name: str
    subject: str
    html_body: str
    text_body: str = ""
    """Optional plaintext fallback; backends accept an empty body when
    the workload always sends multipart-alternative HTML."""


@strawberry.input
class UpdateEmailTemplateInput:
    """Update an existing template in-place (#635).

    Replaces every field on the named template; backends don't support
    partial-update portably (SES, SendGrid, Postmark each require the
    full body on update)."""

    managed_service_id: GUID
    name: str
    subject: str
    html_body: str
    text_body: str = ""


@strawberry.input
class DeleteEmailTemplateInput:
    """Delete a template by name (#635)."""

    managed_service_id: GUID
    name: str


@strawberry.type
class _EmailTemplateDeletedPayload:
    name: str
    deleted: bool
    """``True`` when the template was deleted; ``False`` when it wasn't
    found (idempotent)."""


@strawberry.input
class SendManagedServiceTestEmailInput:
    """Operator-fired 'send test email' against a bound `email` kind
    managed service (#401).

    `subject` / `body` are optional — sensible defaults are used so the
    common case is a one-field interaction (recipient).  The send rides
    the existing `astrolift_operations.email_infra.send()` plumbing —
    the configured transport (SES / SendGrid / Postmark / SMTP) receives
    the payload; suppression list checks fire normally; bypasses
    UNSUBSCRIBE since the operator triggered it deliberately."""

    managed_service_id: GUID
    recipient: str
    subject: str | None = None
    body: str | None = None


@strawberry.type
class _ManagedServiceDeletedPayload:
    id: GUID
    deleted: bool


@strawberry.type
class _AppSecretWritePayload:
    app_slug: str
    key: str
    raw_manifest_staged: str
    pending_proposal_id: GUID | None = None
    """Set when ``requires_secret_approval=True`` on the app — the
    underlying write didn't apply; the caller polls the proposal id
    for approval state.  When None the write applied immediately."""


@strawberry.type
class _BulkImportPayload:
    app_slug: str
    keys_set: list[str]
    raw_manifest_staged: str


@strawberry.type
class _AppSecretMetadataPayload:
    """Return shape for ``setAppSecretMetadata`` (#677 / #678).

    Carries the resolved row contents so the FE can update the
    expires-at chip + provenance tooltip without a refetch round-trip.
    """

    app_slug: str
    key: str
    environment_name: str
    expires_at: dt.datetime | None
    set_via: str
    set_at: dt.datetime | None
    scope: str = "all"


@strawberry.type
class _AttachmentRemovedPayload:
    attachment_id: GUID
    deleted: bool
    pending_proposal_id: GUID | None = None
    """Set when the app requires secret approval — the detach didn't
    apply; the caller polls the proposal id for approval state."""


@strawberry.input
class AdoptManagedResourceInput:
    """Explicitly authorized takeover of an existing cloud resource (#1365).

    Adoption is the migration path off a pre-identity-tag resource. #1443 and
    #1446 made every Azure driver refuse a resource whose ownership tags do
    not name the calling managed service, teardown included, so anything
    provisioned before its driver stamped that tag can currently only be
    removed out of band. This is how such a resource is brought back under
    management, and it is also how a resource an operator built by hand is
    handed to the platform.

    It is never implicit. There is no config flag that reaches it, and
    provision, update and deprovision all still refuse on their own paths.
    """

    id: GUID
    """The managed service that will own the resource afterwards."""

    resource_id: str
    """The cloud resource being adopted — on Azure the full ARM resource id.
    Named explicitly rather than derived from the row's ``backendRef`` so the
    operator states which resource they inspected, and a stale or wrong
    ``backendRef`` cannot silently redirect the takeover."""

    reason: str
    """Why. Required: an adoption with no stated reason records a click
    rather than a decision, and this row is read during incidents."""

    acknowledged_prior_owner: str = ""
    """Required, and equal to the displaced identity, when the resource
    already belongs to a different Astrolift managed service or binding. A
    boolean confirmation could be satisfied without ever looking at the
    resource; naming the owner means the caller read what they are taking."""


@strawberry.type
class _ManagedResourceAdoptionPayload:
    """The audit record, returned so the caller sees what they approved."""

    id: GUID
    managed_service_id: GUID
    cloud: str
    resource_id: str
    surface: str
    classification: str
    """One of ``unmanaged`` / ``unstamped`` / ``foreign_owner`` /
    ``already_owned`` — what the resource looked like before the write."""

    prior_managed_by: str
    prior_managed_service_id: str
    prior_binding_id: str
    prior_markers: strawberry.scalars.JSON
    """Every ``astrolift*`` tag or metadata key found before the envelope was
    merged. The cloud no longer holds this once adoption writes."""

    stamped_markers: strawberry.scalars.JSON
    acknowledged_prior_owner: str
    reason: str
    actor_display: str
    status: str
    adopted_at: dt.datetime
