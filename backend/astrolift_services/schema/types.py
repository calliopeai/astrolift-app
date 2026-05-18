"""GraphQL types for app secrets, managed services, secret bundles."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftSecretEditor")
class SecretEditorType:
    """Compact actor reference for the `lastEditedBy` surface (#424).

    Resolved from `Tracking.updated_by`; null when the record predates
    the per-field-attribution work or was set by a system actor.
    Mirrors the shape of `AstroliftUser` for the fields the UI needs
    without forcing a circular schema import.
    """

    id: str
    """Django auth user pk rendered as string (mirrors AstroliftUser)."""

    username: str
    display_name: str


@strawberry.type(name="AstroliftAppSecret")
class AppSecretType:
    """A secret reference visible to the workload at runtime.

    The ``value`` is never exposed via GraphQL except through the
    explicit ``revealAppSecret`` mutation (`secret.read`-gated) for
    literals; bundle / managed-service rows always stay opaque on
    read since the values live in the secrets backend.
    """

    id: str
    """Stable id derived from ``(source, key, environment)`` so the
    UI can key React lists; may collide if two sources surface the
    same key — UI presents both rows + tags by source."""

    key: str
    environment_name: str
    source: str
    """literal | bundle | managed_service"""

    bundle_slug: str
    """Set when source=bundle; empty otherwise."""

    managed_service_kind: str
    """Set when source=managed_service; empty otherwise."""

    is_masked: bool = True
    last_edited_at: dt.datetime | None = None
    last_edited_by: SecretEditorType | None = None


@strawberry.type(name="AstroliftSecretBundle")
class SecretBundleType:
    id: GUID
    slug: str
    name: str
    backend_ref: str
    organization_slug: str
    team_slug: str | None
    created_at: dt.datetime

    key_count: int = 0
    """Cached count of keys the bundle projects (#441).  Backed by
    the same ``SecretBundle.last_known_keys`` snapshot the attachment
    resolver consumes -- 0 when the cache has never populated."""

    last_known_keys_at: dt.datetime | None = None
    """Timestamp of the last successful key enumeration (#441).
    Null means the cache has never populated -- typically a brand-new
    bundle whose first ``setBundleSecret`` / scheduled refresh hasn't
    fired yet.  Operator UI surfaces this as a 'last updated'
    indicator next to ``keyCount``."""


@strawberry.type(name="AstroliftAppSecretBundleAttachment")
class AppSecretBundleAttachmentType:
    id: GUID
    bundle_slug: str
    bundle_name: str
    environment_name: str
    prefix: str
    registered_app_slug: str
    team_slug: str | None = None
    """Owning team of the underlying bundle; null for org-wide bundles."""

    key_count: int = 0
    """Number of keys the bundle is expected to project at runtime.

    Bundle values themselves live in the platform secrets backend
    (Vault / SecretsManager / GSM / KeyVault) — the platform stores
    only a reference + the manifest-declared envelope. When the
    envelope is unknown the count is 0 and the UI shows '?'.
    """

    merge_order: int = 0
    """Lowest-first merge order across attachments on the same
    (app, environment). App-local literals always win on collision
    regardless of merge order — operators read this column to debug
    precedence when two bundles project the same key."""

    attached_at: dt.datetime | None = None


@strawberry.type(name="AstroliftManagedService")
class ManagedServiceType:
    id: GUID
    name: str
    kind: str
    variant: str
    status: str
    status_error: str
    config: JSON
    registered_app_slug: str
    environment_name: str
    created_at: dt.datetime
    updated_at: dt.datetime
    last_action_at: dt.datetime | None = None
    last_action_kind: str = ""


@strawberry.type(name="AstroliftManagedServiceConnectionKey")
class ManagedServiceConnectionKeyType:
    """One entry of the connection envelope projected into the
    workload at runtime.

    The platform synthesizes a fixed key set per kind (see
    ``astrolift_manifest.env_injection._ENVELOPES``); the actual values
    live in the platform secrets backend and are not reachable from this
    API surface.  ``value`` is therefore a placeholder string ('secret-
    ref:<ref>' / 'env-ref:<key>') rather than plaintext — the audit
    trail still captures the disclosure of the key set so the operator
    can demonstrate provenance later.
    """

    key: str
    value: str
    """Always opaque — never plaintext.  The shape is
    ``secret-ref:<connection_secret_ref>`` when the platform has minted
    a secrets-backend pointer for this service, or
    ``placeholder:<note>`` when the workflow loop hasn't populated the
    binding yet."""

    is_secret: bool = True


@strawberry.type(name="AstroliftManagedServiceConnection")
class ManagedServiceConnectionType:
    managed_service_id: GUID
    kind: str
    name: str
    environment_name: str
    connection_secret_ref: str
    """Pointer into the platform secrets backend (e.g.
    ``vault:/acme/app/prod/postgres-primary``); empty until the workflow
    finalizes the binding."""

    keys: list[ManagedServiceConnectionKeyType]
    revealed_at: dt.datetime


@strawberry.type(name="AstroliftManagedServiceObject")
class ManagedServiceObjectType:
    """A single entry in the recent-objects listing for an
    ``object_store`` managed service.  Populated from the
    ``config['recent_objects']`` cache the workflow refreshes on a
    schedule — a live S3/GCS/Azure list call needs the driver SDK to
    expose ``list_recent_objects``, which is filed as a backend gap
    follow-up."""

    key: str
    size_bytes: int = 0
    last_modified: dt.datetime | None = None


@strawberry.type(name="AstroliftManagedServiceObjects")
class ManagedServiceObjectsType:
    managed_service_id: GUID
    kind: str
    name: str
    objects: list[ManagedServiceObjectType]
    truncated: bool = False
    cache_age_seconds: int | None = None
    """How stale the ``recent_objects`` snapshot is; ``None`` when the
    workflow has never populated it (operator sees an info hint in that
    case)."""


@strawberry.type(name="AstroliftManagedServiceQueueDepth")
class ManagedServiceQueueDepthType:
    """Snapshot of the message depth on a `queue` / `topic` kind
    managed service.

    Sourced from the `config['depth_snapshot']` cache the workflow
    refreshes on a schedule — a live cloud round-trip needs the driver
    SDK to expose `queue_depth`, filed as a backend gap follow-up.
    """

    managed_service_id: GUID
    kind: str
    name: str
    depth: int = 0
    in_flight: int = 0
    sampled_at: dt.datetime | None = None
    """When the cached snapshot was last refreshed; ``None`` until the
    workflow loop has populated it."""


@strawberry.type(name="AstroliftManagedServiceTestEmailResult")
class ManagedServiceTestEmailResultType:
    managed_service_id: GUID
    recipient: str
    subject: str
    sent_at: dt.datetime
    transport: str
    """Configured transport kind at the time of send (``aws_ses`` /
    ``sendgrid`` / ``smtp`` / ``postmark``).  Useful for the operator
    to confirm which provider received the call."""


# ---- Email observability surface (#629, #631, #632, #633, #634) ------


@strawberry.type(name="AstroliftEmailSendQuota")
class EmailSendQuotaType:
    """SES-style send-rate + 24h volume ceiling.

    Renders as the headroom tile on the email-detail page.
    ``sent_last_24h`` divided by ``max_24_hour_send`` is the daily
    progress bar; warn at 80% and red at 95%."""

    max_send_rate: float
    max_24_hour_send: float
    sent_last_24h: float


@strawberry.type(name="AstroliftEmailAccountStatus")
class EmailAccountStatusType:
    """Sandbox / production mode + reputation snapshot."""

    sending_enabled: bool
    production_access: bool
    """``False`` = sandbox (can only send to verified recipients).
    ``True`` = production access granted."""

    reputation_score: float | None = None
    """0.0-1.0; ``None`` until the account has sent enough to be
    meaningful (~1000 attempts)."""

    bounce_rate_pct: float | None = None
    complaint_rate_pct: float | None = None


@strawberry.type(name="AstroliftEmailDkimToken")
class EmailDkimTokenType:
    token: str
    cname_host: str
    cname_target: str


@strawberry.type(name="AstroliftEmailIdentityVerification")
class EmailIdentityVerificationType:
    """Identity-side verification details.

    ``status`` is ``Pending`` / ``Success`` / ``Failed``. UI shows a
    green badge on Success and a "Verify now" affordance otherwise."""

    identity: str
    is_domain: bool
    status: str
    verification_token: str = ""
    dkim_tokens: list[EmailDkimTokenType] = strawberry.field(default_factory=list)


@strawberry.type(name="AstroliftEmailDnsAuthCheck")
class EmailDnsAuthCheckType:
    """One row of the DKIM/SPF/DMARC panel."""

    protocol: str
    """``DKIM`` / ``SPF`` / ``DMARC``."""

    outcome: str
    """``GREEN`` / ``YELLOW`` / ``RED`` / ``UNKNOWN``."""

    records: list[str] = strawberry.field(default_factory=list)
    message: str = ""


@strawberry.type(name="AstroliftEmailDnsAuthStatus")
class EmailDnsAuthStatusType:
    identity: str
    checked_at: dt.datetime
    overall: str
    """Worst-of for the panel header."""

    dkim: EmailDnsAuthCheckType
    spf: EmailDnsAuthCheckType
    dmarc: EmailDnsAuthCheckType


@strawberry.type(name="AstroliftEmailSuppressionEntry")
class EmailSuppressionEntryType:
    address: str
    reason: str
    """``BOUNCE`` / ``COMPLAINT`` / ``MANUAL`` — uppercase for ergonomic
    enum match on the frontend's Strawberry codegen output."""

    suppressed_at: dt.datetime
    detail: str = ""


@strawberry.type(name="AstroliftEmailServiceDetail")
class EmailServiceDetailType:
    """Composite read-surface for the email-detail page.

    One round-trip carries the quota, account status, identity
    verification, DNS auth status, and a slice of the suppression
    list. Each field is independently nullable so the resolver can
    partially-populate when one backend call fails — the UI shades
    just the broken tile, the rest stays usable.
    """

    managed_service_id: GUID
    plugin_slug: str
    """``aws`` / ``gcp`` / ``azure`` — the cloud the resolver routed to.
    UI inspects this to decide which "unsupported" copy to show when
    a tile is null."""

    region: str
    identity: str

    quota: EmailSendQuotaType | None = None
    account_status: EmailAccountStatusType | None = None
    identity_verification: EmailIdentityVerificationType | None = None
    dns_auth_status: EmailDnsAuthStatusType | None = None
    suppression_entries: list[EmailSuppressionEntryType] = strawberry.field(
        default_factory=list
    )

    unsupported_notes: list[str] = strawberry.field(default_factory=list)
    """Free-form messages from drivers that raised UnsupportedOperationError;
    the UI surfaces these next to the empty tiles so operators see WHY
    the surface is empty (e.g. "GCP has no first-party transactional
    email observability — use the third-party vendor's console")."""


@strawberry.type(name="AstroliftSecretChangeApproval")
class SecretChangeApprovalType:
    """One approver's vote on a secret-change proposal (#488)."""

    id: GUID
    approver_user_id: str
    approver_display_name: str
    decision: str
    """``approved`` or ``rejected``."""

    decided_at: dt.datetime
    reason: str = ""


@strawberry.type(name="AstroliftSecretChangeProposal")
class SecretChangeProposalType:
    """A pending / decided secret-change proposal (#488).

    Returned by the propose / approve / reject / withdraw mutations
    and the queue queries.  The diff blob is pre-rendered at propose
    time so the proposal-detail page can render without re-reading
    + re-diffing.  Plaintext values are NEVER baked into the diff —
    the operator reveals them via the explicit ``revealAppSecret``
    mutation which carries its own audit trail (#424).
    """

    id: GUID
    registered_app_slug: str
    environment_name: str
    """Empty string for app-wide literal writes that don't bind to a
    specific environment row."""

    op: str
    """One of ``set | delete | attach_bundle | detach_bundle``."""

    status: str
    """One of ``pending | approved | rejected | applied | expired | withdrawn``."""

    proposer_user_id: str
    """Stringified Django user pk; empty when the proposer row was
    deleted after the proposal was created."""

    proposer_display_name: str

    payload: JSON
    """The proposed change.  Shape varies by op; the FE reads
    ``payload_diff`` for the render-friendly summary."""

    payload_diff: JSON
    """Pre-rendered before/after for the proposal-detail page."""

    required_approver_count: int
    approvals_count: int
    """Distinct approvers who voted approved (rejections are not
    counted here; one rejection moves the proposal to ``rejected``)."""

    expires_at: dt.datetime
    decided_at: dt.datetime | None = None
    applied_at: dt.datetime | None = None
    apply_error: str = ""

    created_at: dt.datetime
    approvals: list[SecretChangeApprovalType]


def secret_change_approval_to_type(approval) -> SecretChangeApprovalType:
    approver = approval.approver
    if approver is None:
        display = ""
        user_id = ""
    else:
        display = (
            approver.get_full_name()
            if hasattr(approver, "get_full_name") and approver.get_full_name()
            else (approver.username or approver.email or "")
        )
        user_id = str(approver.pk)
    return SecretChangeApprovalType(
        id=GUID(str(approval.guid)),
        approver_user_id=user_id,
        approver_display_name=display,
        decision=approval.decision,
        decided_at=approval.decided_at,
        reason=approval.reason or "",
    )


def secret_change_proposal_to_type(proposal) -> SecretChangeProposalType:
    proposer = proposal.proposer
    if proposer is None:
        proposer_display = ""
        proposer_id = ""
    else:
        proposer_display = (
            proposer.get_full_name()
            if hasattr(proposer, "get_full_name") and proposer.get_full_name()
            else (proposer.username or proposer.email or "")
        )
        proposer_id = str(proposer.pk)

    approvals = list(
        proposal.approvals.filter(deleted_at__isnull=True).select_related("approver").order_by("decided_at")
    )
    approved_count = sum(1 for a in approvals if a.decision == "approved")

    return SecretChangeProposalType(
        id=GUID(str(proposal.guid)),
        registered_app_slug=proposal.registered_app.slug,
        environment_name=proposal.environment_name or "",
        op=proposal.op,
        status=proposal.status,
        proposer_user_id=proposer_id,
        proposer_display_name=proposer_display,
        payload=proposal.payload or {},
        payload_diff=proposal.payload_diff or {},
        required_approver_count=int(proposal.required_approver_count or 0),
        approvals_count=approved_count,
        expires_at=proposal.expires_at,
        decided_at=proposal.decided_at,
        applied_at=proposal.applied_at,
        apply_error=proposal.apply_error or "",
        created_at=proposal.created_at,
        approvals=[secret_change_approval_to_type(a) for a in approvals],
    )


@strawberry.type(name="AstroliftRevealedSecret")
class RevealedSecretType:
    """Plaintext payload returned by `revealAppSecret` (#424).

    Returned only for `literal` source secrets — bundle and managed-
    service values live in the platform secrets backend and need a
    separate workflow to fetch (out of scope for this mutation)."""

    secret_id: str
    key: str
    environment_name: str
    value: str
    revealed_at: dt.datetime


def secret_editor_from_user(user) -> SecretEditorType | None:
    """Strawberry-friendly shaped from a Django auth user; returns
    None when no user is on the record (system writes, legacy)."""
    if user is None:
        return None
    username = getattr(user, "username", "") or ""
    try:
        full_name = user.get_full_name()
    except Exception:  # noqa: BLE001 — user model swap-safety
        full_name = ""
    display = full_name or username or getattr(user, "email", "") or ""
    return SecretEditorType(
        id=str(user.pk),
        username=username,
        display_name=display,
    )


def secret_bundle_to_type(b) -> SecretBundleType:
    return SecretBundleType(
        id=GUID(str(b.guid)),
        slug=b.slug,
        name=b.name,
        backend_ref=b.backend_ref or "",
        organization_slug=b.organization.slug,
        team_slug=b.team.slug if b.team_id else None,
        created_at=b.created_at,
        key_count=len(b.last_known_keys or []),
        last_known_keys_at=b.last_key_enum_at,
    )


def attachment_to_type(
    ref,
    *,
    key_count: int = 0,
    merge_order: int = 0,
) -> AppSecretBundleAttachmentType:
    return AppSecretBundleAttachmentType(
        id=GUID(str(ref.guid)),
        bundle_slug=ref.secret_bundle.slug,
        bundle_name=ref.secret_bundle.name,
        environment_name=ref.app_environment.name,
        prefix=ref.prefix or "",
        registered_app_slug=ref.registered_app.slug,
        team_slug=(ref.secret_bundle.team.slug if ref.secret_bundle.team_id else None),
        key_count=key_count,
        merge_order=merge_order,
        attached_at=ref.created_at,
    )


def managed_service_to_type(svc) -> ManagedServiceType:
    return ManagedServiceType(
        id=GUID(str(svc.guid)),
        name=svc.name,
        kind=svc.kind,
        variant=svc.variant or "",
        status=svc.status,
        status_error=svc.status_error or "",
        config=svc.config or {},
        registered_app_slug=svc.registered_app.slug,
        environment_name=svc.app_environment.name,
        created_at=svc.created_at,
        updated_at=svc.updated_at,
        last_action_at=svc.last_action_at,
        last_action_kind=svc.last_action_kind or "",
    )
