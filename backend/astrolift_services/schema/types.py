"""GraphQL types for app secrets, managed services, secret bundles."""

from __future__ import annotations

import datetime as dt
from typing import Any

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID
from astrolift_services.models import WorkloadIdentityGrant, grant_state_for
from astrolift_services.secret_visibility import can_reveal_app_secrets

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

    # ---- sidecar metadata (#677 / #678) -----------------------------
    # Both fields are nullable / empty by default — most app secrets do
    # not carry explicit expiry or non-default provenance.  When the
    # operator (or a CLI / env-paste / bundle writer) sets these via
    # ``setAppSecretMetadata`` the FE renders an expiry chip and a
    # "Set via <source>" tooltip in the secret-row's last-edited cell.

    expires_at: dt.datetime | None = None
    """Operator-declared rotation deadline (#677).  Null when no
    explicit deadline is set — the typical state for evergreen
    literals.  The FE colours the chip warning when this is within
    14 days and renders a 'rotate now' affordance."""

    set_via: str = "web"
    """Provenance of the most recent set/rotate write (#678).  One of
    ``web | cli | env_paste | bundle | managed_service``.  Defaults
    to ``web`` for back-fill and rows the platform never tagged."""

    scope: str = "all"
    """Audience scope for this secret (#752).  One of ``all`` /
    ``production`` / ``preview`` / ``preview:<branch>``.  Resolution-time
    filtering drops rows whose scope doesn't match the queried env."""

    deploys_as_shown: bool = True
    """False for a literal row whose staged value has no matching applied
    secret-change proposal, under an app that requires secret approval
    (#1923). Such a value is what the operator sees here, but not what the
    next deploy actually puts in front of a workload -- it reverts to the
    last-approved (``manifest_raw``) value instead. Always true for
    bundle / managed_service rows: secret approval only gates literal
    ``[env]`` writes."""


@strawberry.type(name="AstroliftSecretBundleConsumer")
class SecretBundleConsumerType:
    id: GUID
    consumer_kind: str
    consumer_slug: str
    environment_name: str


@strawberry.type(name="AstroliftSecretBundle")
class SecretBundleType:
    id: GUID
    slug: str
    name: str
    backend_ref: str
    organization_slug: str
    team_slug: str | None
    project_slug: str | None
    cluster_slug: str | None
    created_at: dt.datetime
    consumers: list[SecretBundleConsumerType] = strawberry.field(default_factory=list)

    key_count: int = 0
    key_names: list[str] = strawberry.field(default_factory=list)
    """Cached count of keys the bundle projects (#441).  Backed by
    the same ``SecretBundle.last_known_keys`` snapshot the attachment
    resolver consumes -- 0 when the cache has never populated."""

    last_known_keys_at: dt.datetime | None = None
    """Timestamp of the last successful key enumeration (#441).
    Null means the cache has never populated -- typically a brand-new
    bundle whose first ``setBundleSecret`` / scheduled refresh hasn't
    fired yet.  Operator UI surfaces this as a 'last updated'
    indicator next to ``keyCount``."""


@strawberry.type(name="AstroliftSecretBundleReveal")
class SecretBundleRevealType:
    key: str
    value: str
    provider: str
    revealed_at: dt.datetime


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


@strawberry.type(name="AstroliftWorkloadIdentityGrant")
class WorkloadIdentityGrantType:
    """One authorization a binding needs, and whether the cloud has it (#1367).

    A federated credential proves who the pod is; on Azure a separate role
    assignment says what it may touch, and that object can still be
    propagating or have been rejected after the service itself is active.
    """

    id: GUID
    managed_service_id: GUID
    environment_name: str
    provider_plugin_slug: str
    identity_role_name: str
    role_name: str
    role_definition_id: str
    scope: str
    assignment_name: str
    state: str
    """``pending`` | ``applied`` | ``failed``. ``pending`` is the settling
    window, not a fault: ARM assignments take seconds to replicate."""

    reason: str
    """Why it is not applied yet. Empty once it is."""

    last_attempted_at: dt.datetime | None
    applied_at: dt.datetime | None


@strawberry.type(name="AstroliftManagedService")
class ManagedServiceType:
    id: GUID
    name: str
    kind: str
    variant: str
    isolation: str
    """The requested mode (``shared`` | ``dedicated``), or ``""`` for
    unspecified. The mode actually provisioned at can be firmer than this:
    the org's compliance floor outranks the request."""

    status: str
    status_error: str
    config: JSON
    applied_config: JSON | None
    operation_kind: str
    operation_workflow_id: str
    operation_run_id: str
    operation_started_at: dt.datetime | None
    operation_completed_at: dt.datetime | None
    registered_app_slug: str
    project_slug: str
    owner_scope: str
    cluster_slug: str
    environment_name: str
    provider_portal_url: str
    created_at: dt.datetime
    updated_at: dt.datetime
    last_action_at: dt.datetime | None = None
    last_action_kind: str = ""
    editable_fields: list[str] = strawberry.field(default_factory=list)
    attachments: list[ManagedServiceAttachmentType] = strawberry.field(default_factory=list)
    volume_bindings: list[ManagedServiceVolumeBindingType] = strawberry.field(default_factory=list)
    """Config keys the driver accepts via ``update()`` without full
    reprovision. ``["*"]`` means all fields; ``[]`` means all changes
    require ``reprovisionManagedService``."""

    grant_state: str = "not_required"
    """``not_required`` | ``pending`` | ``applied`` | ``failed`` (#1367).

    ``status`` describes the resource; this describes the workload's
    authorization to reach it, which is a separate object on Azure and can
    lag or fail on its own. ``not_required`` means the binding declares no
    assignable grant — on AWS/GCP the grants live in the identity's own
    policy, and several Azure drivers resolve entirely to control-plane work
    whose material reaches the pod as a projected Secret."""

    binding_ready: bool = True
    """False while any required grant is unapplied.

    Derived rather than left to each client: a green badge next to a workload
    that cannot reach its data plane is the exact failure this field exists
    to prevent, and every surface would otherwise have to re-derive it."""

    workload_identity_grants: list[WorkloadIdentityGrantType] = strawberry.field(
        default_factory=list,
    )


@strawberry.type(name="AstroliftManagedServiceCostPreview")
class ManagedServiceCostPreviewType:
    managed_service_id: GUID
    available: bool
    reason: str
    message: str
    monthly_total: float | None
    currency: str
    line_items: JSON
    pricing_source_url: str
    pricing_fetched_at: str
    notes: list[str]
    approximate: bool


@strawberry.type(name="AstroliftManagedServiceCatalogEntry")
class ManagedServiceCatalogEntryType:
    id: str
    provider_plugin_slug: str
    kind: str
    variant: str
    display_name: str
    description: str
    status: str
    tier: str
    available: bool
    unavailable_reason: str
    is_default_for_kind: bool
    size_options: list[str]
    config_schema: JSON
    binding_envs: list[str]
    issue_url: str


@strawberry.type(name="AstroliftManagedServiceAttachment")
class ManagedServiceAttachmentType:
    id: GUID
    consumer_kind: str
    consumer_slug: str
    environment_name: str


@strawberry.type(name="AstroliftManagedServiceVolumeBinding")
class ManagedServiceVolumeBindingType:
    """Credential-free runtime mount metadata for a managed filesystem."""

    id: GUID
    name: str
    mount_path: str
    sub_path: str
    source_kind: str
    protocol: str
    claim_name: str
    claim_namespace: str
    storage_class_name: str
    csi_driver: str
    read_only: bool
    capacity: str
    access_modes: list[str]
    workload_names: list[str]
    container_names: list[str]
    credential_reference_count: int


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


@strawberry.type(name="AstroliftModelEndpointTest")
class ModelEndpointTestType:
    """Outcome of one bounded ``testModelEndpoint`` chat completion (#2064).

    ``status`` is one of ``succeeded`` / ``failed`` / ``timed_out``.
    ``failed`` and ``timed_out`` both carry an ``error`` and leave the
    reply/latency/token fields at their zero values -- the mutation
    always returns ``ok: true`` at the envelope level once the agent's
    round trip is accounted for; a chat-completion failure is data, not
    a mutation error.
    """

    status: str
    reply: str
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    error: str


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
    suppression_entries: list[EmailSuppressionEntryType] = strawberry.field(default_factory=list)

    unsupported_notes: list[str] = strawberry.field(default_factory=list)
    """Free-form messages from drivers that raised UnsupportedOperationError;
    the UI surfaces these next to the empty tiles so operators see WHY
    the surface is empty (e.g. "GCP has no first-party transactional
    email observability — use the third-party vendor's console")."""


# ---- Email template management (#635, #628) --------------------------


@strawberry.type(name="AstroliftEmailTemplate")
class EmailTemplateType:
    """One transactional-email template (#635).

    Mirrors :class:`_sdk.email.EmailTemplate`. ``createdAt`` is null
    when the backend doesn't surface a creation timestamp on the
    per-template fetch (SES only carries it on the list metadata);
    the UI falls back to the "—" placeholder."""

    name: str
    subject: str
    html_body: str
    text_body: str
    created_at: dt.datetime | None = None


@strawberry.type(name="AstroliftTemplateSendStatPoint")
class TemplateSendStatPointType:
    """One 15-minute interval of per-template send counters (#628).

    Sourced from the cloud's metrics pipeline (SES → CloudWatch on
    the ``ses2:TemplateName`` dimension). UI renders an empty list as
    a "no metrics yet" hint rather than a flat zero-line."""

    timestamp: dt.datetime
    sends: int
    deliveries: int
    bounces: int
    complaints: int


# ---- Per-message SES event log (#756, unblocks #624 / #625 / #626) ----


@strawberry.type(name="AstroliftEmailMessage")
class EmailMessageType:
    """One per-message SES event row sourced from ``EmailEvent`` (#624).

    Surfaces the message envelope (id, recipient, subject) and the
    event kind plus the raw SES notification metadata blob. The FE
    renders these as a paginated table on the email service's
    "Messages" tab; ``metadata`` lets the row expander show the
    underlying SES reason chain (bounce code, complaint feedback type)
    without a follow-up round-trip."""

    id: GUID
    message_id: str
    """SES ``messageId`` — the same id shows up across multiple rows
    when the message moves through several lifecycle kinds."""

    recipient: str
    subject: str
    event_kind: str
    """One of ``send`` / ``delivery`` / ``bounce`` / ``complaint`` /
    ``open`` / ``click``. Stored lowercase to match the SES wire format."""

    occurred_at: dt.datetime
    metadata: JSON
    """Raw SES notification body so the row expander can render
    bounce / complaint specifics without re-reading the SNS message.
    The exact shape varies by ``notificationType``; the FE narrows on
    ``metadata.bounce`` / ``metadata.complaint`` / etc."""


@strawberry.type(name="AstroliftEmailEngagementMetrics")
class EmailEngagementMetricsType:
    """Aggregate engagement counters for one email service over a
    rolling window (#626).

    The four percentage fields are derived from the raw counts:
    bounce/complaint are expressed as a percentage of ``total_sends``
    (matches the SES reputation surface), open/click as a percentage
    of ``total_deliveries`` (per industry convention — opens against
    sends understates engagement because bounces never had a chance to
    open). Zero-denominator cases return 0.0 rather than divide-by-
    zero so the FE doesn't need null-guards on every tile."""

    total_sends: int
    total_deliveries: int
    total_bounces: int
    total_complaints: int
    total_opens: int
    total_clicks: int

    bounce_rate_pct: float
    complaint_rate_pct: float
    open_rate_pct: float
    click_rate_pct: float

    window_days: int
    """Lookback window the resolver used (echoed from the query
    argument). Lets the FE label the tile "Last 30 days" without
    keeping the query's variable in component state."""


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
    ``payload_diff`` for the render-friendly summary.

    ``set`` / ``rotate`` proposals carry the literal plaintext value
    under ``payload["value"]``. It is masked to ``[REDACTED]`` unless
    the viewer holds ``secret.read`` and is step-up elevated (#1920),
    the same gate ``revealAppSecret`` enforces. Approvers only ever
    needed ``secret.approve`` to review a proposal via ``payload_diff``;
    this field must not become the side door around that."""

    payload_diff: JSON
    """Pre-rendered before/after for the proposal-detail page. Its
    ``value_masked`` hints are ``***`` under the same gate as
    ``payload``."""

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


_DIFF_SIDES = ("before", "after")


def _proposal_secret_material(payload: dict[str, Any], payload_diff: dict[str, Any]) -> bool:
    """Whether the proposal carries anything a viewer who cannot reveal
    secrets must not see: a set/rotate ``payload["value"]``, or a
    ``payload_diff`` value hint. delete / attach_bundle / detach_bundle
    usually carry neither, and skip the reveal check entirely."""
    if "value" in payload:
        return True
    return any(
        isinstance(payload_diff.get(side), dict) and payload_diff[side].get("value_masked")
        for side in _DIFF_SIDES
    )


def _redact_proposal(payload: dict[str, Any], payload_diff: dict[str, Any]) -> tuple[dict, dict]:
    """Mask a proposal for a viewer who cannot reveal secrets (#1920).

    ``payload["value"]`` is the literal plaintext. ``value_masked`` in
    the diff is a hint built at propose time from the first three and
    last characters of the before/after values, four plaintext
    characters of the secret, so it collapses to ``***``."""
    if "value" in payload:
        payload = {**payload, "value": "[REDACTED]"}
    redacted_diff = dict(payload_diff)
    for side in _DIFF_SIDES:
        entry = payload_diff.get(side)
        if isinstance(entry, dict) and entry.get("value_masked"):
            redacted_diff[side] = {**entry, "value_masked": "***"}
    return payload, redacted_diff


def secret_change_proposal_to_type(proposal, *, info: Info) -> SecretChangeProposalType:
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

    payload = proposal.payload or {}
    payload_diff = proposal.payload_diff or {}
    if _proposal_secret_material(payload, payload_diff) and not can_reveal_app_secrets(
        info, app=proposal.registered_app
    ):
        payload, payload_diff = _redact_proposal(payload, payload_diff)

    return SecretChangeProposalType(
        id=GUID(str(proposal.guid)),
        registered_app_slug=proposal.registered_app.slug,
        environment_name=proposal.environment_name or "",
        op=proposal.op,
        status=proposal.status,
        proposer_user_id=proposer_id,
        proposer_display_name=proposer_display,
        payload=payload,
        payload_diff=payload_diff,
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
    consumers: list[SecretBundleConsumerType] = []
    if b.project_id:
        consumers.extend(
            SecretBundleConsumerType(
                id=GUID(str(ref.guid)),
                consumer_kind="agent",
                consumer_slug=ref.environment_spec.slug,
                environment_name=ref.environment or "default",
            )
            for ref in b.agent_refs.select_related("environment_spec").filter(
                deleted_at__isnull=True,
            )
        )
        consumers.extend(
            SecretBundleConsumerType(
                id=GUID(str(ref.guid)),
                consumer_kind="app",
                consumer_slug=ref.registered_app.slug,
                environment_name=ref.app_environment.name,
            )
            for ref in b.app_refs.select_related(
                "registered_app",
                "app_environment",
            ).filter(deleted_at__isnull=True)
        )
    return SecretBundleType(
        id=GUID(str(b.guid)),
        slug=b.slug,
        name=b.name,
        backend_ref=b.backend_ref or "",
        organization_slug=b.organization.slug,
        team_slug=b.team.slug if b.team_id else None,
        project_slug=b.project.slug if b.project_id else None,
        cluster_slug=b.tenant_cluster.slug if b.tenant_cluster_id else None,
        created_at=b.created_at,
        consumers=consumers,
        key_count=len(b.last_known_keys or []),
        key_names=list(b.last_known_keys or []),
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


@strawberry.type(name="AstroliftSecretHistoryActor")
class SecretHistoryActorType:
    """Compact actor reference for one row of secret-history (#725).

    ``id`` is the Django auth user pk stringified, mirroring the rest of
    the actor surfaces in the schema. ``username`` is empty for
    ``system`` actors (no associated user row); the FE renders that as
    'system' in the timeline."""

    id: str
    username: str


@strawberry.type(name="AstroliftSecretHistoryEntry")
class SecretHistoryEntryType:
    """One row of per-key secret-history audit timeline (#725).

    Returned newest-first, capped at 50. Sourced from ``AuditEvent``
    rows whose ``action`` is one of ``app.secret.set``,
    ``app.secret.delete``, ``app.secret.rotate`` and whose ``target_id``
    encodes ``<app_slug>:<key>``. The plaintext value is never returned —
    the audit row never carries it in the first place.
    """

    timestamp: dt.datetime
    actor: SecretHistoryActorType
    action: str
    """``set`` | ``delete`` | ``rotate`` — the trailing segment of
    ``app.secret.<action>`` so the FE can switch on a stable string
    without re-parsing the dotted prefix."""

    success: bool
    error_code: str = ""
    """Empty on success; set to the resolver-emitted code on failure
    (e.g. ``PERMISSION_DENIED``, ``VALIDATION``, ``INTERNAL``)."""

    source_ip: str = ""
    """Best-effort client IP captured at mutation time; empty when the
    request didn't carry one (system actors, scheduled jobs)."""


#: ``editable_fields()`` answers depend only on the driver class, so one
#: resolution per (plugin, kind, variant) serves every row. Keyed on the class
#: rather than the tuple would be equivalent; the tuple is what callers have.
_EDITABLE_FIELDS_CACHE: dict[tuple[str, str, str], list[str]] = {}


def _editable_fields_uncached(driver_cls) -> list[str] | None:
    """Call ``editable_fields()`` without constructing a cloud client.

    ``editable_fields`` is a pure contract method: all 82 implementations
    return a literal and none reads ``self``. Calling it against an
    uninitialised instance, the same idiom ``managed_service_catalog`` uses for
    ``config_schema`` and ``binding_schema``, avoids building a driver (and its
    SDK clients) per row, which is what made this too expensive to resolve on
    list queries.

    Returns None when the call did not produce a usable answer, so the caller
    can decide the fallback rather than having one baked in here.
    """
    try:
        method = driver_cls.editable_fields
        raw = getattr(method, "__wrapped__", method)
        result = raw(object.__new__(driver_cls))
    except Exception:  # noqa: BLE001 - a rendering path must stay readable
        return None
    return list(result) if result is not None else None


def _editable_fields_for(svc) -> list[str]:
    """The config keys this service's driver can apply in place.

    Falls back to ``["*"]`` when the driver cannot be resolved. That is
    fail-open at this layer, deliberately: it only widens what the UI *offers*,
    and ``updateManagedService`` re-resolves before starting a workflow while
    the driver itself refuses with ``unsupported_update()`` (#1376). Narrowing
    to ``[]`` on a transient resolution failure would tell an operator their
    service can never be edited, which is worse and less recoverable than
    offering an edit that is then refused.
    """
    try:
        from astrolift_drivers.managed_resolution import resolve_managed_driver
        from astrolift_drivers.registry import DriverNotFound

        cluster = svc.effective_cluster
        if cluster is None:
            return ["*"]
        plugin = getattr(cluster, "provider_plugin", None)
        if plugin is None:
            return ["*"]
        variant = getattr(svc, "variant", "") or ""

        key = (plugin.slug, svc.kind, variant)
        cached = _EDITABLE_FIELDS_CACHE.get(key)
        if cached is not None:
            return list(cached)

        try:
            resolved = resolve_managed_driver(
                cluster_plugin_slug=plugin.slug,
                kind=svc.kind,
                variant=variant,
            )
        except DriverNotFound:
            return ["*"]

        result = _editable_fields_uncached(resolved.driver_cls)
        if result is None:
            # A driver whose editable_fields is not pure after all. Build it
            # properly rather than reporting the permissive default, which is
            # the bug this function exists to fix.
            try:
                from core.cluster_observability import managed_config_for

                driver = resolved.driver_cls(
                    config=managed_config_for(
                        resolved.plugin_slug,
                        cluster,
                        kind=svc.kind,
                        variant=variant,
                    ),
                )
                raw = driver.editable_fields()
                result = list(raw) if raw is not None else ["*"]
            except Exception:  # noqa: BLE001
                return ["*"]

        _EDITABLE_FIELDS_CACHE[key] = list(result)
        return list(result)
    except Exception:  # noqa: BLE001
        return ["*"]


def managed_service_to_type(svc, *, resolve_editable_fields: bool = True) -> ManagedServiceType:
    """Render a managed service for GraphQL.

    ``resolve_editable_fields`` now defaults on. It was opt-in because resolving
    meant constructing a driver per row, and no call site ever opted in, so
    every service reported ``["*"]`` regardless of what its driver said (#1414).
    The client handles the honest answers already: an empty list disables Edit
    and points at Re-provision, and a restricted list renders one input per key
    instead of a single input labelled ``*``. None of that could run.

    Resolution no longer constructs anything (see ``_editable_fields_uncached``)
    and is cached per (plugin, kind, variant), so a list query costs one
    resolution per distinct variant rather than one per row.
    """
    editable = _editable_fields_for(svc) if resolve_editable_fields else ["*"]
    grants = [row for row in svc.workload_identity_grants.all() if row.deleted_at is None]
    state = grant_state_for(grants)
    from astrolift_services.provider_links import provider_portal_url

    return ManagedServiceType(
        id=GUID(str(svc.guid)),
        name=svc.name,
        kind=svc.kind,
        variant=svc.variant or "",
        isolation=svc.isolation or "",
        status=svc.status,
        status_error=svc.status_error or "",
        config=svc.config or {},
        applied_config=svc.applied_config,
        operation_kind=svc.operation_kind or "",
        operation_workflow_id=svc.operation_workflow_id or "",
        operation_run_id=svc.operation_run_id or "",
        operation_started_at=svc.operation_started_at,
        operation_completed_at=svc.operation_completed_at,
        registered_app_slug=svc.registered_app.slug if svc.registered_app_id else "",
        project_slug=svc.project.slug if svc.project_id else "",
        owner_scope=svc.owner_scope,
        cluster_slug=(svc.effective_cluster.slug if svc.effective_cluster else ""),
        environment_name=svc.effective_environment_name,
        provider_portal_url=provider_portal_url(svc),
        created_at=svc.created_at,
        updated_at=svc.updated_at,
        last_action_at=svc.last_action_at,
        last_action_kind=svc.last_action_kind or "",
        editable_fields=editable,
        attachments=[managed_service_attachment_to_type(row) for row in svc.attachments.all()],
        volume_bindings=[
            managed_service_volume_binding_to_type(row)
            for row in svc.volume_bindings.all()
            if row.deleted_at is None
        ],
        grant_state=state,
        binding_ready=state
        not in {
            WorkloadIdentityGrant.State.PENDING.value,
            WorkloadIdentityGrant.State.FAILED.value,
        },
        workload_identity_grants=[workload_identity_grant_to_type(row) for row in grants],
    )


def workload_identity_grant_to_type(row) -> WorkloadIdentityGrantType:
    return WorkloadIdentityGrantType(
        id=GUID(str(row.guid)),
        managed_service_id=GUID(str(row.managed_service.guid)),
        environment_name=row.app_environment.name,
        provider_plugin_slug=row.provider_plugin_slug,
        identity_role_name=row.identity_role_name,
        role_name=row.role_name,
        role_definition_id=row.role_definition_id,
        scope=row.scope,
        assignment_name=row.assignment_name,
        state=row.state,
        reason=row.reason,
        last_attempted_at=row.last_attempted_at,
        applied_at=row.applied_at,
    )


def managed_service_catalog_entry_to_type(row) -> ManagedServiceCatalogEntryType:
    return ManagedServiceCatalogEntryType(
        id=row.id,
        provider_plugin_slug=row.provider_plugin_slug,
        kind=row.kind,
        variant=row.variant,
        display_name=row.display_name,
        description=row.description,
        status=row.status,
        tier=row.tier,
        available=row.available,
        unavailable_reason=row.unavailable_reason,
        is_default_for_kind=row.is_default_for_kind,
        size_options=list(row.size_options),
        config_schema=row.config_schema,
        binding_envs=list(row.binding_envs),
        issue_url=row.issue_url,
    )


def managed_service_attachment_to_type(row) -> ManagedServiceAttachmentType:
    if row.agent_environment_spec_id:
        return ManagedServiceAttachmentType(
            id=GUID(str(row.guid)),
            consumer_kind="agent",
            consumer_slug=row.agent_environment_spec.slug,
            environment_name="default",
        )
    return ManagedServiceAttachmentType(
        id=GUID(str(row.guid)),
        consumer_kind="app",
        consumer_slug=row.app_environment.registered_app.slug,
        environment_name=row.app_environment.name,
    )


def managed_service_volume_binding_to_type(row) -> ManagedServiceVolumeBindingType:
    return ManagedServiceVolumeBindingType(
        id=GUID(str(row.guid)),
        name=row.name,
        mount_path=row.mount_path,
        sub_path=row.sub_path or "",
        source_kind=row.source_kind,
        protocol=row.protocol,
        claim_name=row.claim_name or "",
        claim_namespace=row.claim_namespace or "",
        storage_class_name=row.storage_class_name or "",
        csi_driver=row.csi_driver or "",
        read_only=row.read_only,
        capacity=row.capacity,
        access_modes=list(row.access_modes or []),
        workload_names=list(row.workload_names or []),
        container_names=list(row.container_names or []),
        credential_reference_count=len(row.secret_refs or {}),
    )
