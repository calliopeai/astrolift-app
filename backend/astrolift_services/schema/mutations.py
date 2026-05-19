"""Mutations for app secrets, secret bundles, managed services."""

from __future__ import annotations

import datetime as dt
from datetime import timedelta

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import (
    delete_app_env_key,
    parse_dotenv,
    read_app_env,
    set_app_env_keys,
)
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    AppSecretBundleRef,
    AppSecretMetadata,
    ManagedService,
    SecretBundle,
    SecretChangeApproval,
    SecretChangeProposal,
)
from astrolift_services.schema.types import (
    AppSecretBundleAttachmentType,
    ManagedServiceConnectionKeyType,
    ManagedServiceConnectionType,
    ManagedServiceTestEmailResultType,
    ManagedServiceType,
    RevealedSecretType,
    SecretBundleType,
    SecretChangeProposalType,
    attachment_to_type,
    managed_service_to_type,
    secret_bundle_to_type,
    secret_change_proposal_to_type,
)
from astrolift_services.secret_change_apply import apply_proposal
from astrolift_services.secret_change_diff import build_diff
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.optimistic import check_version_match as _check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

# ---------------------------------------------------------------------
# Inputs


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

    # ---- sidecar metadata (#677 / #678) -----------------------------
    # Same shape as ``SetAppSecretInput``.  A rotation is naturally a
    # moment to refresh expiry; the FE pre-fills the existing value
    # so an operator's "rotate" click without changing the expiry
    # carries the previous value back through.
    expires_at: dt.datetime | None = None
    set_via: str | None = None


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


# Managed services CRUD (#281) ---------------------------------------


@strawberry.input
class ProvisionManagedServiceInput:
    app_slug: str
    environment_name: str
    kind: str
    """Catalog kind: postgres | redis | object_store | queue | ..."""

    name: str | None = None
    variant: str | None = None
    config: strawberry.scalars.JSON | None = None


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


@strawberry.type
class _AttachmentRemovedPayload:
    attachment_id: GUID
    deleted: bool
    pending_proposal_id: GUID | None = None
    """Set when the app requires secret approval — the detach didn't
    apply; the caller polls the proposal id for approval state."""


# ---------------------------------------------------------------------
# Helpers


_ENV_NAME_HINT = "must start with a letter or underscore and use only [A-Z0-9_] (POSIX env-var rules)"

# Default TTL for secret-change proposals (#488).  Pulled from
# Constance at create time; the constant here is the fallback used in
# tests and during early-boot when Constance isn't yet readable.
_DEFAULT_PROPOSAL_TTL_SECONDS = 7 * 24 * 3600


def _proposal_ttl_seconds() -> int:
    """Read SECRET_PROPOSAL_TTL_SECONDS from Constance with a safe
    fallback.  Imports lazily so the module load order doesn't pull
    Constance before settings are wired."""
    try:
        from constance import config as constance_config

        return int(getattr(constance_config, "SECRET_PROPOSAL_TTL_SECONDS", _DEFAULT_PROPOSAL_TTL_SECONDS))
    except Exception:  # noqa: BLE001 — DB not ready / Constance off
        return _DEFAULT_PROPOSAL_TTL_SECONDS


def _self_approve_secrets_allowed() -> bool:
    """Read ALLOW_SELF_APPROVE_SECRETS from Constance with a safe
    fallback to False (the safe default)."""
    try:
        from constance import config as constance_config

        return bool(getattr(constance_config, "ALLOW_SELF_APPROVE_SECRETS", False))
    except Exception:  # noqa: BLE001
        return False


def _is_eligible_secret_approver(app: RegisteredApp, *, user_id: int) -> bool:
    """Does ``user_id`` satisfy the app-level secret-approver eligibility?

    Mirrors ``_is_eligible_approver`` in astrolift_lifecycle.  When
    ``requires_secret_approval`` is off no per-app constraint applies;
    when on the user must be in ``secret_approver_users``.  An empty
    set with the flag on means "any holder of the
    ``secret.approve`` permission" — the permission gate covers that
    case.
    """
    if not app.requires_secret_approval:
        return True
    if not app.secret_approver_users.exists():
        # No explicit set → permission gate is the sole arbiter.
        return True
    return app.secret_approver_users.filter(pk=user_id).exists()


def _proposal_target_from_input(*args, **kwargs):
    """``@mutation_audit`` target hook — extract the proposal id from
    the input so the audit row carries the affected entity."""
    inp = kwargs.get("input")
    if inp is None and len(args) >= 3:
        inp = args[2]
    pid = getattr(inp, "proposal_id", None) if inp is not None else None
    if pid is None:
        return None
    return "SecretChangeProposal", str(pid)


def _app_secret_target_from_input(*args, **kwargs):
    """``@mutation_audit`` target hook for secret writes.

    Stores the audit row's ``target_id`` as ``<app_slug>:<key>`` so the
    per-key history query (#725) can filter precisely. ``target_kind``
    is ``AppSecret``. Returning ``None`` skips targeting — happens when
    the resolver is invoked through a path that doesn't carry a normal
    input (test scaffolding, etc.)."""
    inp = kwargs.get("input")
    if inp is None and len(args) >= 3:
        inp = args[2]
    if inp is None:
        return None
    app_slug = getattr(inp, "app_slug", None)
    key = getattr(inp, "key", None)
    if not app_slug or not key:
        return None
    return "AppSecret", f"{app_slug}:{key}"


def _maybe_create_proposal_for_write(
    *,
    app: RegisteredApp,
    op: str,
    payload: dict,
    environment_name: str = "",
    info: Info,
) -> SecretChangeProposal | None:
    """If the app requires secret approval, build + persist a proposal
    row and return it.  Otherwise return None so the caller proceeds
    with the direct write.  Caller is responsible for surfacing the
    proposal id back in its MutationResult envelope.
    """
    if not app.requires_secret_approval:
        return None
    actor = _actor_user(info)
    env = None
    if environment_name:
        env = AppEnvironment.objects.filter(
            registered_app=app,
            name=environment_name,
            deleted_at__isnull=True,
        ).first()
    diff = build_diff(
        app=app,
        op=op,
        payload=payload,
        environment_name=environment_name,
    )
    proposal = SecretChangeProposal.objects.create(
        registered_app=app,
        app_environment=env,
        environment_name=environment_name or "",
        proposer=actor,
        op=op,
        payload=payload,
        payload_diff=diff,
        required_approver_count=max(int(app.secret_minimum_approvals or 1), 1),
        expires_at=timezone.now() + timedelta(seconds=_proposal_ttl_seconds()),
        created_by=actor,
        updated_by=actor,
    )
    return proposal


_VALID_SECRET_SOURCES = {s.value for s in AppSecretMetadata.Source}


def _upsert_app_secret_metadata(
    *,
    app: RegisteredApp,
    key: str,
    environment_name: str = "",
    expires_at: dt.datetime | None = None,
    set_via: str | None = None,
    actor=None,
) -> AppSecretMetadata:
    """Upsert the operator-facing metadata sidecar for a secret literal.

    Idempotent on (registered_app, environment_name, key).  Each call
    refreshes ``set_at`` to ``timezone.now()`` so the FE can render a
    'set on <date>' tooltip independent of the underlying audit row.

    ``expires_at=None`` + ``set_via=None`` is a no-op on the timestamp /
    expiry but still touches ``set_at`` — operators sometimes want a
    'last-touched' refresh without changing the data, and the cost of
    one UPDATE per literal write is negligible against the platform's
    overall throughput.
    """
    if set_via is not None and set_via not in _VALID_SECRET_SOURCES:
        # Reject unknown sources up front so a typo doesn't silently
        # land an out-of-band value on the column.
        set_via = AppSecretMetadata.Source.WEB.value
    row = AppSecretMetadata.objects.filter(
        registered_app=app,
        environment_name=environment_name or "",
        key=key,
        deleted_at__isnull=True,
    ).first()
    if row is None:
        row = AppSecretMetadata.objects.create(
            registered_app=app,
            environment_name=environment_name or "",
            key=key,
            expires_at=expires_at,
            source=(set_via or AppSecretMetadata.Source.WEB.value),
            set_at=timezone.now(),
            created_by=actor,
            updated_by=actor,
        )
        return row
    # Apply optional updates atomically.  We don't clear
    # ``expires_at`` to None unless the caller explicitly passes a
    # value — `None` means "don't touch" per the input contract.
    updates: dict = {"set_at": timezone.now()}
    if expires_at is not None:
        updates["expires_at"] = expires_at
    if set_via is not None:
        updates["source"] = set_via
    for k, v in updates.items():
        setattr(row, k, v)
    if actor is not None:
        row.updated_by = actor
    row.save(
        update_fields=[*updates.keys(), "updated_by", "updated_at"],
    )
    return row


def _validate_env_key(key: str) -> str | None:
    if not key:
        return "key cannot be empty"
    if not (key[0].isalpha() or key[0] == "_"):
        return f"key {key!r} {_ENV_NAME_HINT}"
    if not all(c.isalnum() or c == "_" for c in key):
        return f"key {key!r} {_ENV_NAME_HINT}"
    return None


def _resolve_email_service(managed_service_id):
    """Fetch a ManagedService row with the full tenant-cluster path
    pre-joined, gated on ``kind == EMAIL``.

    Used by the email observability mutations so the resolver-entry
    code path can call into the cloud-specific driver without re-
    walking the FK chain. Returns None when the row is missing,
    soft-deleted, or not an email kind."""
    svc = (
        ManagedService.objects.select_related(
            "app_environment",
            "app_environment__tenant_cluster",
            "app_environment__tenant_cluster__provider_plugin",
            "registered_app",
        )
        .filter(guid=str(managed_service_id), deleted_at__isnull=True)
        .first()
    )
    if svc is None or svc.kind != ManagedService.Kind.EMAIL:
        return None
    return svc


def _actor_user(info):
    """Return the authenticated user from the resolver context or None.

    Anonymous / unauthenticated contexts (CLI bypass, system actor)
    return None so the caller can no-op the attribution write."""
    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) if request else None
    if user is None:
        user = getattr(info.context, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def _client_ip(info) -> str:
    """Best-effort caller-IP for the audit row. Honours
    X-Forwarded-For first (left-most hop) then REMOTE_ADDR."""
    request = getattr(info.context, "request", None)
    if request is None:
        return ""
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "") if hasattr(request, "META") else ""
    if fwd:
        return fwd.split(",")[0].strip()
    if hasattr(request, "META"):
        return request.META.get("REMOTE_ADDR", "") or ""
    return ""


def _stage_manifest(app, new_text: str, *, actor=None) -> str:
    """Validate the new TOML parses, then write to the staging
    buffer + clear it when it matches the source-of-truth.

    ``actor`` is the user attributing this write so the secrets row's
    ``lastEditedBy`` surface (#424) shows who staged the change. Pass
    None for system writes and the existing updated_by value stands."""
    try:
        parse_raw(new_text)
    except ManifestError as exc:
        raise ManifestError(str(exc)) from exc
    if new_text == (app.manifest_raw or ""):
        app.manifest_raw_staged = ""
    else:
        app.manifest_raw_staged = new_text
    update_fields = [
        "manifest_raw_staged",
        "updated_at",
        "version",
    ]
    if actor is not None:
        app.updated_by = actor
        update_fields.append("updated_by")
    app.save(update_fields=update_fields)
    return app.manifest_raw_staged


# ---------------------------------------------------------------------


@strawberry.type
class ServicesMutation:
    @strawberry.field
    @mutation_audit(action="app.secret.set", target=_app_secret_target_from_input)
    @requires_elevation(action_label="app.secret.set")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_app_secret(
        self,
        info: Info,
        input: SetAppSecretInput,
    ) -> MutationResultType[_AppSecretWritePayload]:
        validation_msg = _validate_env_key(input.key)
        if validation_msg:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                validation_msg,
                field="key",
            )
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        # #497 — optimistic-concurrency gate. Refuse to apply the
        # secret write when the caller's cached version is stale; the
        # FE refetches and re-prompts the operator instead of silently
        # overwriting a concurrent edit (e.g. another admin updated a
        # different setting on the same app while this form was open).
        mismatch = _check_version_match(app, if_match_version=input.if_match_version, kind="App")
        if mismatch is not None:
            return mismatch
        # #488: when the app requires secret approval the mutation
        # creates a proposal instead of applying.  Returning the
        # proposal id in the same envelope shape (with empty raw
        # manifest staged) keeps the caller code stable — they switch
        # on ``pendingProposalId`` to decide which flow they're in.
        proposal = _maybe_create_proposal_for_write(
            app=app,
            op=SecretChangeProposal.Op.SET.value,
            payload={"key": input.key, "value": input.value},
            info=info,
        )
        if proposal is not None:
            return gql_success(
                _AppSecretWritePayload(
                    app_slug=app.slug,
                    key=input.key,
                    raw_manifest_staged=app.manifest_raw_staged or "",
                    pending_proposal_id=GUID(str(proposal.guid)),
                )
            )
        source = app.manifest_raw_staged or app.manifest_raw or ""
        new_text = set_app_env_keys(source, {input.key: input.value})
        try:
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after edit: {exc}",
                field="rawManifest",
            )
        # #677 / #678 — refresh sidecar metadata on every direct write.
        # The metadata row is keyed at the wildcard env scope ('') for
        # set/rotate writes since the mutation itself isn't env-bound;
        # operators add per-env overrides via setAppSecretMetadata.
        _upsert_app_secret_metadata(
            app=app,
            key=input.key,
            environment_name="",
            expires_at=input.expires_at,
            set_via=input.set_via,
            actor=_actor_user(info),
        )
        return gql_success(
            _AppSecretWritePayload(
                app_slug=app.slug,
                key=input.key,
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.rotate", target=_app_secret_target_from_input)
    @requires_elevation(action_label="app.secret.rotate")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def rotate_app_secret(
        self,
        info: Info,
        input: RotateAppSecretInput,
    ) -> MutationResultType[_AppSecretWritePayload]:
        """Rotate one literal env value on the staged manifest (#726).

        Wire-identical to :meth:`set_app_secret`: same staging buffer,
        same validation, same optimistic-concurrency gate, same proposal
        flow when the app requires secret approval. Differs only in the
        audit action (``app.secret.rotate`` vs ``app.secret.set``) so
        SRE can distinguish credential lifecycle events from edits in
        the audit timeline.
        """
        validation_msg = _validate_env_key(input.key)
        if validation_msg:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                validation_msg,
                field="key",
            )
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        mismatch = _check_version_match(app, if_match_version=input.if_match_version, kind="App")
        if mismatch is not None:
            return mismatch
        proposal = _maybe_create_proposal_for_write(
            app=app,
            op=SecretChangeProposal.Op.SET.value,
            payload={"key": input.key, "value": input.value},
            info=info,
        )
        if proposal is not None:
            return gql_success(
                _AppSecretWritePayload(
                    app_slug=app.slug,
                    key=input.key,
                    raw_manifest_staged=app.manifest_raw_staged or "",
                    pending_proposal_id=GUID(str(proposal.guid)),
                )
            )
        source = app.manifest_raw_staged or app.manifest_raw or ""
        new_text = set_app_env_keys(source, {input.key: input.value})
        try:
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after rotate: {exc}",
                field="rawManifest",
            )
        # #677 / #678 — refresh sidecar metadata on rotate as well.
        _upsert_app_secret_metadata(
            app=app,
            key=input.key,
            environment_name="",
            expires_at=input.expires_at,
            set_via=input.set_via,
            actor=_actor_user(info),
        )
        return gql_success(
            _AppSecretWritePayload(
                app_slug=app.slug,
                key=input.key,
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.delete", target=_app_secret_target_from_input)
    @requires_elevation(action_label="app.secret.delete")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def delete_app_secret(
        self,
        info: Info,
        input: DeleteAppSecretInput,
    ) -> MutationResultType[_AppSecretWritePayload]:
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        source = app.manifest_raw_staged or app.manifest_raw or ""
        # Validate the key exists BEFORE creating a proposal — no point
        # gating a delete-of-nothing through review.
        _, removed = delete_app_env_key(source, input.key)
        if not removed:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"key {input.key!r} not present in [env]",
                field="key",
            )
        # #488: gate on approval policy after the existence check.
        proposal = _maybe_create_proposal_for_write(
            app=app,
            op=SecretChangeProposal.Op.DELETE.value,
            payload={"key": input.key},
            info=info,
        )
        if proposal is not None:
            return gql_success(
                _AppSecretWritePayload(
                    app_slug=app.slug,
                    key=input.key,
                    raw_manifest_staged=app.manifest_raw_staged or "",
                    pending_proposal_id=GUID(str(proposal.guid)),
                )
            )
        new_text, _ = delete_app_env_key(source, input.key)
        try:
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after delete: {exc}",
                field="rawManifest",
            )
        # #677 / #678 — soft-delete metadata rows for the removed key so
        # a re-add later doesn't silently re-use stale expiry / source.
        # Soft delete keeps the row available for the audit trail.
        actor = _actor_user(info)
        for row in AppSecretMetadata.objects.filter(
            registered_app=app,
            key=input.key,
            deleted_at__isnull=True,
        ):
            row.soft_delete(by=actor)
        return gql_success(
            _AppSecretWritePayload(
                app_slug=app.slug,
                key=input.key,
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.metadata.set", target=_app_secret_target_from_input)
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_app_secret_metadata(
        self,
        info: Info,
        input: SetAppSecretMetadataInput,
    ) -> MutationResultType[_AppSecretMetadataPayload]:
        """Set / clear operator-facing metadata for one secret literal
        (#677 / #678).

        The underlying value is never touched — this mutation only
        edits the sidecar ``AppSecretMetadata`` row.  Permission gate
        is ``app.update`` (same as set/rotate) since the metadata feeds
        the secret-row UI and an operator who can edit the app should
        be allowed to annotate its secrets.

        ``expires_at=null`` on an existing row preserves the current
        deadline.  To clear the deadline pass a value of ``None`` with
        ``set_via='clear'`` — reserved for future expansion when an
        explicit clear semantics is needed (current FE only sets +
        refreshes; it never clears)."""
        msg = _validate_env_key(input.key)
        if msg:
            return gql_failure(ErrorCode.VALIDATION.value, msg, field="key")
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        if input.set_via is not None and input.set_via not in _VALID_SECRET_SOURCES:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown set_via value {input.set_via!r}; allowed: {sorted(_VALID_SECRET_SOURCES)}",
                field="setVia",
            )
        row = _upsert_app_secret_metadata(
            app=app,
            key=input.key,
            environment_name=(input.environment_name or ""),
            expires_at=input.expires_at,
            set_via=input.set_via,
            actor=_actor_user(info),
        )
        return gql_success(
            _AppSecretMetadataPayload(
                app_slug=app.slug,
                key=row.key,
                environment_name=row.environment_name,
                expires_at=row.expires_at,
                set_via=row.source,
                set_at=row.set_at,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.bulk_import")
    @requires_elevation(action_label="app.secret.bulk_import")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def bulk_import_app_secrets(
        self,
        info: Info,
        input: BulkImportAppSecretsInput,
    ) -> MutationResultType[_BulkImportPayload]:
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        kvs = parse_dotenv(input.dotenv_text or "")
        if not kvs:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "no valid KEY=value lines parsed from input",
                field="dotenvText",
            )
        # Validate all keys before staging so a single bad name
        # doesn't write a half-applied result.
        for key in kvs:
            msg = _validate_env_key(key)
            if msg:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    msg,
                    field="dotenvText",
                )
        source = app.manifest_raw_staged or app.manifest_raw or ""
        new_text = set_app_env_keys(source, kvs)
        try:
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after bulk import: {exc}",
                field="rawManifest",
            )
        # #678 — tag every imported key as `env_paste` so the secret-row
        # tooltip can render "Set via .env paste on <date>".  Operators
        # then bulk-rotate via the dedicated UI; the source flips on
        # the next set/rotate.
        actor = _actor_user(info)
        for key in kvs:
            _upsert_app_secret_metadata(
                app=app,
                key=key,
                environment_name="",
                set_via=AppSecretMetadata.Source.ENV_PASTE.value,
                actor=actor,
            )
        return gql_success(
            _BulkImportPayload(
                app_slug=app.slug,
                keys_set=sorted(kvs.keys()),
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(
        action="app.secret.reveal",
        extras=lambda result: (
            {
                "secret_id": result.data.secret_id,
                "key": result.data.key,
                "environment_name": result.data.environment_name,
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @requires_elevation(action_label="app.secret.reveal")
    @require_permission(Permission.APP_READ, Permission.SECRET_READ)
    @tenant_scoped()
    def reveal_app_secret(
        self,
        info: Info,
        input: RevealAppSecretInput,
    ) -> MutationResultType[RevealedSecretType]:
        """Return plaintext for one secret row (#424).

        Requires both `app.read` (the caller can see the app) and
        `secret.read` (the caller can disclose the value). Plaintext
        is returned **only** for `literal`-source secrets — bundle and
        managed-service values live in the platform secrets backend
        and aren't reachable from the API surface.

        Every successful reveal lands in the audit log via the
        wrapping `@mutation_audit` decorator (actor + IP + tenant +
        target secret id). Failures audit-log too with the failure
        code so the trail captures attempted disclosures.
        """
        # Stable id format: ``{source}:{env}:{key}``. ``env`` is a
        # POSIX-shape env name (no colons); ``key`` is too for
        # literal secrets. Split on the first two colons so a key
        # that contains a colon (managed-service style ``svc.k``)
        # survives reassembly even though we won't reveal those.
        secret_id = input.secret_id or ""
        parts = secret_id.split(":", 2)
        if len(parts) != 3 or not all(parts):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "secretId must look like 'literal:<env>:<KEY>'",
                field="secretId",
            )
        source, env_name, key = parts
        if source != "literal":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"reveal is only supported for literal-source secrets; "
                    f"{source!r} values live in the platform secrets backend "
                    f"and aren't reachable from the API"
                ),
                field="secretId",
            )
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None or app.deleted_at is not None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")
        env_exists = AppEnvironment.objects.filter(
            registered_app=app,
            name=env_name,
            deleted_at__isnull=True,
        ).exists()
        if not env_exists:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {env_name!r} not found",
                field="secretId",
            )
        raw_text = app.manifest_raw_staged or app.manifest_raw or ""
        literals = read_app_env(raw_text)
        if key not in literals:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"no literal secret {key!r} in environment {env_name!r}",
                field="secretId",
            )
        # @mutation_audit (above) already records actor + tenant +
        # secret_id via its extras hook. Emit a sibling audit entry
        # carrying the client IP so the trail captures the disclosure
        # source — done as a separate emit_audit call so the IP isn't
        # surfaced in the GraphQL response payload (which would leak
        # the caller's IP back to a layered proxy).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="app.secret.reveal.disclosure",
                decision="ALLOW",
                target_kind="app_secret",
                target_id=secret_id,
                duration_ms=0,
                permissions=(
                    Permission.APP_READ.value,
                    Permission.SECRET_READ.value,
                ),
                extra={
                    "app_slug": input.app_slug,
                    "environment_name": env_name,
                    "key": key,
                    "client_ip": ip,
                },
            )
        )
        return gql_success(
            RevealedSecretType(
                secret_id=secret_id,
                key=key,
                environment_name=env_name,
                value=literals[key],
                revealed_at=timezone.now(),
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.bundle.attach")
    @requires_elevation(action_label="app.secret.bundle.attach")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def attach_secret_bundle(
        self,
        info: Info,
        input: AttachSecretBundleInput,
    ) -> MutationResultType[AppSecretBundleAttachmentType]:
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        env = AppEnvironment.objects.filter(
            registered_app=app,
            name=input.environment_name,
            deleted_at__isnull=True,
        ).first()
        if env is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {input.environment_name!r} not found",
                field="environmentName",
            )
        bundle = SecretBundle.objects.filter(slug=input.bundle_slug, deleted_at__isnull=True).first()
        if bundle is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"bundle {input.bundle_slug!r} not found",
                field="bundleSlug",
            )
        # Cross-org scoping: bundles belong to the calling tenant
        # via the @tenant_scoped() decorator, but bundle ↔ app
        # require same-org explicitly here so a typo doesn't
        # accidentally cross.
        if bundle.organization_id != app.organization_id:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "bundle and app belong to different organizations",
            )
        # #488: gate on secret-approval policy.
        if app.requires_secret_approval:
            proposal = _maybe_create_proposal_for_write(
                app=app,
                op=SecretChangeProposal.Op.ATTACH_BUNDLE.value,
                payload={
                    "bundle_slug": input.bundle_slug,
                    "prefix": input.prefix or "",
                },
                environment_name=env.name,
                info=info,
            )
            # Surface the proposal id by reusing the attachment payload
            # shape: id is the proposal guid so the FE can route on it.
            return gql_success(
                AppSecretBundleAttachmentType(
                    id=GUID(str(proposal.guid)),
                    bundle_slug=bundle.slug,
                    bundle_name=bundle.name,
                    environment_name=env.name,
                    prefix=input.prefix or "",
                    registered_app_slug=app.slug,
                    team_slug=(bundle.team.slug if bundle.team_id else None),
                    key_count=len(bundle.last_known_keys or []),
                    merge_order=0,
                    attached_at=None,
                )
            )
        actor = _actor_user(info)
        existing = AppSecretBundleRef.objects.filter(
            registered_app=app,
            app_environment=env,
            secret_bundle=bundle,
            deleted_at__isnull=True,
        ).first()
        if existing is not None:
            # Idempotent — same attachment with same prefix is a
            # no-op; differing prefix updates.
            new_prefix = input.prefix or ""
            if existing.prefix != new_prefix:
                existing.prefix = new_prefix
                update_fields = [
                    "prefix",
                    "updated_at",
                    "version",
                ]
                if actor is not None:
                    existing.updated_by = actor
                    update_fields.append("updated_by")
                existing.save(update_fields=update_fields)
            # #441: prefix change doesn't alter the key set itself, but
            # surface the cached count back to the operator so the UI
            # shows a real number rather than '?' on first render.
            return gql_success(
                attachment_to_type(
                    existing,
                    key_count=len(bundle.last_known_keys or []),
                )
            )
        ref = AppSecretBundleRef.objects.create(
            registered_app=app,
            app_environment=env,
            secret_bundle=bundle,
            prefix=input.prefix or "",
            created_by=actor,
            updated_by=actor,
        )
        # #441: eagerly enumerate so the UI can show the count on the
        # first read after attach.  Errors are swallowed inside the
        # helper -- attach must not fail because the secrets backend
        # was momentarily unreachable.
        from astrolift_services.bundle_keys import force_refresh_bundle_known_keys

        force_refresh_bundle_known_keys(bundle)
        return gql_success(
            attachment_to_type(
                ref,
                key_count=len(bundle.last_known_keys or []),
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.bundle.detach")
    @requires_elevation(action_label="app.secret.bundle.detach")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def detach_secret_bundle(
        self,
        info: Info,
        input: DetachSecretBundleInput,
    ) -> MutationResultType[_AttachmentRemovedPayload]:
        ref = (
            AppSecretBundleRef.objects.select_related(
                "registered_app",
                "app_environment",
            )
            .filter(guid=str(input.attachment_id))
            .first()
        )
        if ref is None or ref.deleted_at is not None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "attachment not found",
            )
        # #488: gate on secret-approval policy.
        app = ref.registered_app
        if app.requires_secret_approval:
            proposal = _maybe_create_proposal_for_write(
                app=app,
                op=SecretChangeProposal.Op.DETACH_BUNDLE.value,
                payload={"attachment_id": str(ref.guid)},
                environment_name=ref.app_environment.name,
                info=info,
            )
            return gql_success(
                _AttachmentRemovedPayload(
                    attachment_id=input.attachment_id,
                    deleted=False,
                    pending_proposal_id=GUID(str(proposal.guid)),
                )
            )
        ref.soft_delete()
        return gql_success(
            _AttachmentRemovedPayload(
                attachment_id=input.attachment_id,
                deleted=True,
            )
        )

    @strawberry.field
    @mutation_audit(action="secret_bundle.rotate")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def rotate_secret_bundle(
        self,
        info: Info,
        input: RotateSecretBundleInput,
    ) -> MutationResultType[SecretBundleType]:
        """Fire ``RotateSecretBundleWorkflow`` for one SecretBundle (#365).

        Re-applies the bundle's materialized k8s Secret on every
        cluster the bundle is referenced on (operator-fired path bounces
        consumers; the hourly scheduled path doesn't). Idempotent — the
        workflow id is bound to the bundle so re-firing joins the
        existing run rather than spawning a parallel one.
        """
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            RotateSecretBundleInput as RotateInput,
        )

        bundle = SecretBundle.objects.filter(
            guid=str(input.id),
            deleted_at__isnull=True,
        ).first()
        if bundle is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "secret bundle not found",
            )
        request = getattr(info.context, "request", None)
        user = getattr(request, "user", None) if request else None
        actor = (
            Actor(
                kind="user",
                user_id=user.pk,
                display=getattr(user, "username", "") or "",
            )
            if user is not None and getattr(user, "is_authenticated", False)
            else Actor(kind="system", display="rotate-secret-bundle")
        )
        start_workflow(
            "RotateSecretBundleWorkflow",
            args=[
                RotateInput(
                    secret_bundle_id=bundle.pk,
                    actor=actor,
                    bounce_workloads=True,
                ),
            ],
            workflow_id=f"RotateSecretBundleWorkflow-{bundle.guid}",
        )
        return gql_success(secret_bundle_to_type(bundle))

    # ---- Managed services CRUD (#281) ----------------------------

    @strawberry.field
    @mutation_audit(action="managed_service.provision")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def provision_managed_service(
        self,
        info: Info,
        input: ProvisionManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        """Provision a managed-service binding.

        DB-side write only — the actual workflow that drives the
        provider plugin's provision() lives in
        ``astrolift_workflows`` and reads from this row. The
        mutation creates the row in PENDING state; the workflow
        loop transitions it through PROVISIONING → ACTIVE."""
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        env = AppEnvironment.objects.filter(
            registered_app=app,
            name=input.environment_name,
            deleted_at__isnull=True,
        ).first()
        if env is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {input.environment_name!r} not found",
                field="environmentName",
            )
        valid_kinds = {k for k, _ in ManagedService.Kind.choices}
        if input.kind not in valid_kinds:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"kind must be one of {sorted(valid_kinds)}",
                field="kind",
            )
        name = (input.name or input.kind).strip()
        if ManagedService.objects.filter(
            registered_app=app,
            kind=input.kind,
            name=name,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"managed service ({input.kind}, {name!r}) already exists for this app",
                field="name",
            )
        svc = ManagedService.objects.create(
            registered_app=app,
            app_environment=env,
            kind=input.kind,
            name=name,
            variant=input.variant or "",
            config=dict(input.config or {}),
            status=ManagedService.Status.PENDING,
        )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.update")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_managed_service(
        self,
        info: Info,
        input: UpdateManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        svc = ManagedService.objects.select_related(
            "app_environment__tenant_cluster__provider_plugin",
            "registered_app",
        ).filter(guid=str(input.id), deleted_at__isnull=True).first()
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
            )
        if input.config is not None:
            from astrolift_services.schema.types import _editable_fields_for

            editable = _editable_fields_for(svc)
            if editable != ["*"]:
                incoming_keys = set(dict(input.config).keys())
                current_keys = set((svc.config or {}).keys())
                changed_keys = {
                    k for k in incoming_keys | current_keys
                    if dict(input.config).get(k) != (svc.config or {}).get(k)
                }
                blocked = changed_keys - set(editable)
                if blocked:
                    return gql_failure(
                        ErrorCode.VALIDATION.value,
                        f"fields {sorted(blocked)} cannot be changed in-place; "
                        "use reprovisionManagedService to apply them",
                        field="config",
                    )
        if input.name is not None:
            svc.name = input.name.strip()
        if input.config is not None:
            svc.config = dict(input.config)
        # Re-applying config kicks the workflow back to UPDATING;
        # the workflow loop will roll it forward to ACTIVE.
        if svc.status == ManagedService.Status.ACTIVE:
            svc.status = ManagedService.Status.UPDATING
        svc.save(
            update_fields=[
                "name",
                "config",
                "status",
                "updated_at",
                "version",
            ]
        )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.reprovision")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def reprovision_managed_service(
        self,
        info: Info,
        input: ReprovisionManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        """Trigger a full reprovision cycle for a managed service (#745).

        Transitions status to PENDING so the lifecycle workflow picks it
        up for a fresh provision pass. Use for config changes that are
        NOT in ``editable_fields`` (i.e., changes that require tearing
        down and re-creating the backing cloud resource).
        """
        svc = ManagedService.objects.select_related(
            "app_environment__tenant_cluster__provider_plugin",
            "registered_app",
        ).filter(guid=str(input.managed_service_id), deleted_at__isnull=True).first()
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )
        _blocked = {
            ManagedService.Status.DEPROVISIONING,
            ManagedService.Status.PROVISIONING,
        }
        if svc.status in _blocked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"managed service is {svc.status}; reprovision can only be "
                "triggered for services that are active, pending, updating, or failed",
                field="managedServiceId",
            )
        svc.status = ManagedService.Status.PENDING
        svc.save(update_fields=["status", "updated_at", "version"])
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.deprovision")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def deprovision_managed_service(
        self,
        info: Info,
        input: DeprovisionManagedServiceInput,
    ) -> MutationResultType[_ManagedServiceDeletedPayload]:
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            DeprovisionManagedServiceInput as DeprovisionInput,
        )

        svc = ManagedService.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
            )

        # Flip to DEPROVISIONING so the UI shows the in-flight state
        # immediately. The workflow re-asserts on entry; the platform
        # row is only soft-deleted by the workflow's finalize activity
        # AFTER the driver confirms the backend resource is gone.
        svc.status = ManagedService.Status.DEPROVISIONING
        svc.save(
            update_fields=[
                "status",
                "updated_at",
                "version",
            ]
        )

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        actor = Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(getattr(user, "email", "") or getattr(user, "username", "")),
        )
        start_workflow(
            "DeprovisionManagedServiceWorkflow",
            args=[
                DeprovisionInput(
                    managed_service_id=svc.pk,
                    actor=actor,
                    delete_data=bool(input.delete_data),
                    force_destroy=bool(input.force_destroy),
                ),
            ],
            workflow_id=f"DeprovisionManagedServiceWorkflow-{svc.guid}",
        )
        return gql_success(
            _ManagedServiceDeletedPayload(
                id=input.id,
                deleted=False,  # workflow finalizes the soft-delete
            )
        )

    # ---- Per-service quick actions (#401) -----------------------------
    #
    # The Settings landing surfaces a managed-services summary card with
    # a per-row dropdown of kind-specific actions:
    #   - postgres / redis / mysql  → revealManagedServiceConnection
    #   - object_store              → listManagedServiceObjects (query)
    #   - email                     → sendManagedServiceTestEmail
    #   - queue / topic             → managedServiceQueueDepth (query)
    #
    # Reveal mirrors #424's pattern: explicit mutation, decorated with
    # @mutation_audit + a sibling emit_audit row for the client IP.  No
    # plaintext leaves the platform — the envelope's `value` field is
    # always a `secret-ref:` or `placeholder:` shim.

    @strawberry.field
    @mutation_audit(
        action="managed_service.connection.reveal",
        extras=lambda result: (
            {
                "managed_service_id": str(result.data.managed_service_id),
                "kind": result.data.kind,
                "environment_name": result.data.environment_name,
                "key_count": len(result.data.keys),
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @requires_elevation(action_label="managed_service.connection.reveal")
    @require_permission(Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def reveal_managed_service_connection(
        self,
        info: Info,
        input: RevealManagedServiceConnectionInput,
    ) -> MutationResultType[ManagedServiceConnectionType]:
        """Disclose the connection envelope key set for one managed
        service (#401).

        Returns the stable env-var key set the workload sees at runtime
        for the service's kind, paired with the platform's
        `connection_secret_ref` pointer.  Plaintext values are NEVER
        returned — they live in the platform secrets backend (Vault /
        SecretsManager / GSM / KeyVault) and aren't reachable from this
        API surface.  Each `value` field is the opaque
        ``secret-ref:<ref>`` or ``placeholder:<note>`` shim that points
        the operator at where to fetch the value via the platform's
        secrets-backend client.

        Permission gate stacks `app.read` (caller can see the app) with
        `managed_service.update` (caller can disclose the pointer) so
        the surface matches the rest of the per-service action set.
        """
        # Lazy import to avoid circulars; the env_injection module is
        # the source of truth for the envelope key set per kind.
        from astrolift_manifest.env_injection import envelope_keys_for

        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(guid=str(input.managed_service_id), deleted_at__isnull=True)
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )

        envelope = envelope_keys_for(svc.kind)
        if not envelope:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"kind {svc.kind!r} has no connection envelope to reveal; "
                    "reveal is only meaningful for kinds the platform injects "
                    "env vars for (postgres, redis, mysql, queue, topic, "
                    "object_store, email, etc.)"
                ),
                field="managedServiceId",
            )

        # Shape each key as ``secret-ref:<ref>`` when the workflow has
        # populated `connection_secret_ref`, else ``placeholder:pending``
        # so the UI can render a clear "not yet provisioned" hint
        # without us inventing a fake value.
        ref = svc.connection_secret_ref or ""
        if ref:
            value_for = lambda k: f"secret-ref:{ref}#{k}"  # noqa: E731
        else:
            value_for = lambda k: "placeholder:pending"  # noqa: E731

        keys = [
            ManagedServiceConnectionKeyType(
                key=k,
                value=value_for(k),
                # The handful of non-secret envelope keys are stable
                # config (region, prefix) — surface them as is_secret=
                # False so the UI doesn't mask them.
                is_secret=not _is_envelope_key_public(k),
            )
            for k in envelope
        ]

        # Sibling audit row carrying the client IP so the trail captures
        # the disclosure source (#424 pattern).  Done as a separate
        # emit_audit call so the IP isn't surfaced in the GraphQL
        # response (which would leak the caller's IP to a layered
        # proxy).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.connection.reveal.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_READ.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "kind": svc.kind,
                    "name": svc.name,
                    "app_slug": svc.registered_app.slug,
                    "environment_name": svc.app_environment.name,
                    "key_count": len(keys),
                    "connection_secret_ref": ref,
                    "client_ip": ip,
                },
            )
        )

        # Stamp the cached "last operator action" surface so the
        # summary card can render "revealed N seconds ago" without
        # re-walking the audit log.
        now = timezone.now()
        svc.last_action_at = now
        svc.last_action_kind = "connection.reveal"
        svc.save(update_fields=["last_action_at", "last_action_kind", "updated_at", "version"])

        return gql_success(
            ManagedServiceConnectionType(
                managed_service_id=input.managed_service_id,
                kind=svc.kind,
                name=svc.name,
                environment_name=svc.app_environment.name,
                connection_secret_ref=ref,
                keys=keys,
                revealed_at=now,
            )
        )

    @strawberry.field
    @mutation_audit(
        action="managed_service.test_email.send",
        extras=lambda result: (
            {
                "managed_service_id": str(result.data.managed_service_id),
                "recipient": result.data.recipient,
                "transport": result.data.transport,
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @require_permission(Permission.APP_UPDATE, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def send_managed_service_test_email(
        self,
        info: Info,
        input: SendManagedServiceTestEmailInput,
    ) -> MutationResultType[ManagedServiceTestEmailResultType]:
        """Operator-fired test send through a bound `email` kind managed
        service (#401).

        Rides the existing `astrolift_operations.email_infra` plumbing —
        the configured transport (SES / SendGrid / Postmark / SMTP)
        receives the rendered payload.  Suppression list checks fire
        normally so a hard-bounced address won't be retried; bypasses
        UNSUBSCRIBE since the operator triggered it deliberately to
        verify deliverability.
        """
        # Lazy imports — keep mutation module light when email infra
        # isn't reached.
        from astrolift_operations.email_infra import (
            Email,
            EmailError,
            EmailKind,
            configured_transport,
            is_configured,
        )
        from astrolift_operations.email_infra import (
            send as email_send,
        )

        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(guid=str(input.managed_service_id), deleted_at__isnull=True)
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )
        if svc.kind != ManagedService.Kind.EMAIL:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"managed service is {svc.kind!r}, not 'email'; "
                    "test-email is only supported for email kinds (SES, "
                    "SendGrid, Postmark, SMTP variants)"
                ),
                field="managedServiceId",
            )

        recipient = (input.recipient or "").strip()
        if not recipient:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "recipient is required",
                field="recipient",
            )

        # Resolve a sane from_address from the binding config; falls
        # back to a synthesized address scoped to the app so the email's
        # provenance is obvious in the recipient's mailbox.
        config = svc.config or {}
        from_address = (
            config.get("email_from")
            or config.get("EMAIL_FROM")
            or f"noreply@{svc.registered_app.slug}.astrolift.local"
        )
        subject = (input.subject or "").strip() or (f"[Astrolift] Test email from {svc.name or svc.kind}")
        body = (input.body or "").strip() or (
            f"This is a deliverability test fired from the {svc.registered_app.slug} "
            f"settings page against managed service {svc.name or svc.kind} "
            f"({svc.app_environment.name}).  If you received this, the email "
            "binding is working."
        )

        if not is_configured():
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    "no email transport configured on this install; admin "
                    "must set EMAIL_BACKEND before test sends will land"
                ),
                field="managedServiceId",
            )

        try:
            email = Email(
                to_address=recipient,
                subject=subject,
                html_body=f"<p>{body}</p>",
                plain_body=body,
                from_address=from_address,
                kind=EmailKind.MANAGED_SERVICE_TEST,
            )
        except EmailError as exc:
            field = "recipient" if "to_address" in str(exc) else "managedServiceId"
            return gql_failure(
                ErrorCode.VALIDATION.value,
                str(exc),
                field=field,
            )

        try:
            email_send(email, suppression_lookup=lambda _addr: None)
        except EmailError as exc:
            return gql_failure(
                ErrorCode.INTERNAL.value,
                str(exc),
                field="managedServiceId",
            )

        transport = configured_transport().value

        # Cache the operator action for the summary card.
        now = timezone.now()
        svc.last_action_at = now
        svc.last_action_kind = "test_email.send"
        svc.save(update_fields=["last_action_at", "last_action_kind", "updated_at", "version"])

        # Sibling audit row carrying client IP + recipient so the trail
        # captures the disclosure source (operators triggering a test
        # send to an unfamiliar address should be obvious in audit).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.test_email.send.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "kind": svc.kind,
                    "name": svc.name,
                    "app_slug": svc.registered_app.slug,
                    "environment_name": svc.app_environment.name,
                    "recipient": recipient,
                    "from_address": from_address,
                    "transport": transport,
                    "client_ip": ip,
                },
            )
        )

        return gql_success(
            ManagedServiceTestEmailResultType(
                managed_service_id=input.managed_service_id,
                recipient=recipient,
                subject=subject,
                sent_at=now,
                transport=transport,
            )
        )

    # ---- Email suppression list (#631) -------------------------------

    @strawberry.field
    @mutation_audit(
        action="managed_service.email.suppression.add",
        extras=lambda result: (
            {"address": result.data.address, "reason": result.data.reason}
            if result.ok and result.data is not None
            else None
        ),
    )
    @require_permission(Permission.APP_UPDATE, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def add_email_suppression_entry(
        self,
        info: Info,
        input: AddEmailSuppressionEntryInput,
    ) -> MutationResultType[_EmailSuppressionAddPayload]:
        """Add an address to the SES account-level suppression list (#631).

        Sensitive op: the audit row carries actor + IP + reason +
        operator-supplied note so the trail captures the disclosure
        source. Suppression mutations are restricted to operators with
        ``managed_service.update`` because a manual entry can mask a
        legitimate deliverability problem (operator suppresses the
        complainer rather than fixing the content).
        """
        # Lazy imports — keep the mutation module light when email
        # observability isn't reached.
        from _sdk import UnsupportedOperationError
        from _sdk.email import SuppressionReason

        from astrolift_services.email_observability import driver_for_plugin_slug

        svc = _resolve_email_service(input.managed_service_id)
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found or not an email service",
                field="managedServiceId",
            )

        address = (input.address or "").strip()
        if not address:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "address is required",
                field="address",
            )

        # Validate reason maps onto the protocol enum.
        try:
            reason = SuppressionReason(input.reason or "MANUAL")
        except ValueError:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"reason must be one of {[r.value for r in SuppressionReason]}",
                field="reason",
            )

        plugin_slug = svc.app_environment.tenant_cluster.provider_plugin.slug
        region = svc.app_environment.tenant_cluster.region or ""
        try:
            driver = driver_for_plugin_slug(
                plugin_slug=plugin_slug,
                region=region,
            )
        except LookupError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )

        try:
            entry = driver.add_suppression_entry(
                address=address,
                reason=reason,
                note=input.note or "",
            )
        except UnsupportedOperationError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )
        except Exception as exc:  # noqa: BLE001
            return gql_failure(
                ErrorCode.INTERNAL.value,
                f"{type(exc).__name__}: {exc}",
                field="managedServiceId",
            )

        # Sibling audit row carries the IP + the operator note so the
        # trail captures why the address was suppressed.
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.email.suppression.add.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "address": address,
                    "reason": entry.reason.value,
                    "note": input.note or "",
                    "plugin_slug": plugin_slug,
                    "region": region,
                    "client_ip": ip,
                },
            )
        )

        return gql_success(
            _EmailSuppressionAddPayload(
                address=entry.address,
                reason=entry.reason.value,
            )
        )

    @strawberry.field
    @mutation_audit(
        action="managed_service.email.suppression.remove",
        extras=lambda result: (
            {"address": result.data.address, "removed": result.data.removed}
            if result.ok and result.data is not None
            else None
        ),
    )
    @require_permission(Permission.APP_UPDATE, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def remove_email_suppression_entry(
        self,
        info: Info,
        input: RemoveEmailSuppressionEntryInput,
    ) -> MutationResultType[_EmailSuppressionRemovePayload]:
        """Remove an address from the suppression list (#631).

        Idempotent: removing an address that wasn't on the list returns
        ``ok=True`` with ``data.removed=False``. Sensitive op: audit
        row carries the actor + IP so an operator un-suppressing a
        previously bounced address can be tracked back to the source.
        """
        from _sdk import UnsupportedOperationError

        from astrolift_services.email_observability import driver_for_plugin_slug

        svc = _resolve_email_service(input.managed_service_id)
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found or not an email service",
                field="managedServiceId",
            )
        address = (input.address or "").strip()
        if not address:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "address is required",
                field="address",
            )

        plugin_slug = svc.app_environment.tenant_cluster.provider_plugin.slug
        region = svc.app_environment.tenant_cluster.region or ""
        try:
            driver = driver_for_plugin_slug(
                plugin_slug=plugin_slug,
                region=region,
            )
        except LookupError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )

        try:
            removed = driver.remove_suppression_entry(address=address)
        except UnsupportedOperationError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )
        except Exception as exc:  # noqa: BLE001
            return gql_failure(
                ErrorCode.INTERNAL.value,
                f"{type(exc).__name__}: {exc}",
                field="managedServiceId",
            )

        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.email.suppression.remove.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "address": address,
                    "removed": removed,
                    "plugin_slug": plugin_slug,
                    "region": region,
                    "client_ip": ip,
                },
            )
        )

        return gql_success(
            _EmailSuppressionRemovePayload(
                address=address,
                removed=removed,
            )
        )

    # ---- Secret-change proposals (#488) ------------------------------

    @strawberry.field
    @mutation_audit(action="app.secret.proposal.create")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def propose_secret_change(
        self,
        info: Info,
        input: ProposeSecretChangeInput,
    ) -> MutationResultType[SecretChangeProposalType]:
        """Explicit proposal creation (#488).

        Works regardless of whether the app has
        ``requires_secret_approval`` on — teams can opt in to the
        review trail without flipping the gate.  When the gate IS on,
        the legacy mutations (``setAppSecret`` etc.) proxy to the same
        underlying logic; this is just the canonical surface.
        """
        valid_ops = {o.value for o in SecretChangeProposal.Op}
        if input.op not in valid_ops:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"op must be one of {sorted(valid_ops)}",
                field="op",
            )
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "app not found",
                field="appSlug",
            )

        # Per-op validation + payload assembly.  We refuse to mint a
        # proposal that we know would fail on apply (missing key,
        # missing bundle, …) so the queue stays clean.
        env_name = input.environment_name or ""
        if input.op == SecretChangeProposal.Op.SET.value:
            if not input.key:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "key is required for op=set",
                    field="key",
                )
            msg = _validate_env_key(input.key)
            if msg:
                return gql_failure(ErrorCode.VALIDATION.value, msg, field="key")
            if input.value is None:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "value is required for op=set",
                    field="value",
                )
            payload: dict = {"key": input.key, "value": input.value}
        elif input.op == SecretChangeProposal.Op.DELETE.value:
            if not input.key:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "key is required for op=delete",
                    field="key",
                )
            payload = {"key": input.key}
        elif input.op == SecretChangeProposal.Op.ATTACH_BUNDLE.value:
            if not input.bundle_slug:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "bundleSlug is required for op=attach_bundle",
                    field="bundleSlug",
                )
            if not env_name:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "environmentName is required for op=attach_bundle",
                    field="environmentName",
                )
            bundle = SecretBundle.objects.filter(
                slug=input.bundle_slug,
                deleted_at__isnull=True,
            ).first()
            if bundle is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"bundle {input.bundle_slug!r} not found",
                    field="bundleSlug",
                )
            if bundle.organization_id != app.organization_id:
                return gql_failure(
                    ErrorCode.PERMISSION_DENIED.value,
                    "bundle and app belong to different organizations",
                )
            payload = {
                "bundle_slug": input.bundle_slug,
                "prefix": input.prefix or "",
            }
        elif input.op == SecretChangeProposal.Op.DETACH_BUNDLE.value:
            if input.attachment_id is None:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "attachmentId is required for op=detach_bundle",
                    field="attachmentId",
                )
            ref = (
                AppSecretBundleRef.objects.select_related("app_environment")
                .filter(guid=str(input.attachment_id), deleted_at__isnull=True)
                .first()
            )
            if ref is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "attachment not found",
                    field="attachmentId",
                )
            payload = {"attachment_id": str(ref.guid)}
            env_name = env_name or ref.app_environment.name
        else:  # pragma: no cover — guarded above
            return gql_failure(ErrorCode.VALIDATION.value, "unknown op", field="op")

        actor = _actor_user(info)
        env_row = None
        if env_name:
            env_row = AppEnvironment.objects.filter(
                registered_app=app,
                name=env_name,
                deleted_at__isnull=True,
            ).first()
        diff = build_diff(
            app=app,
            op=input.op,
            payload=payload,
            environment_name=env_name,
        )
        proposal = SecretChangeProposal.objects.create(
            registered_app=app,
            app_environment=env_row,
            environment_name=env_name or "",
            proposer=actor,
            op=input.op,
            payload=payload,
            payload_diff=diff,
            required_approver_count=max(int(app.secret_minimum_approvals or 1), 1),
            expires_at=timezone.now() + timedelta(seconds=_proposal_ttl_seconds()),
            created_by=actor,
            updated_by=actor,
        )
        return gql_success(secret_change_proposal_to_type(proposal))

    @strawberry.field
    @mutation_audit(
        action="app.secret.proposal.approve",
        target=_proposal_target_from_input,
    )
    @require_permission(Permission.SECRET_APPROVE)
    @tenant_scoped()
    def approve_secret_change(
        self,
        info: Info,
        input: ApproveSecretChangeInput,
    ) -> MutationResultType[SecretChangeProposalType]:
        proposal = (
            SecretChangeProposal.objects.select_related("registered_app")
            .filter(guid=str(input.proposal_id), deleted_at__isnull=True)
            .first()
        )
        if proposal is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "proposal not found")
        if proposal.status != SecretChangeProposal.Status.PENDING.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"proposal is in status {proposal.status}, expected pending",
            )
        # TTL gate — refuse to approve an expired proposal even if
        # the sweeper hasn't transitioned it yet (race window).
        if proposal.expires_at <= timezone.now():
            with transaction.atomic():
                proposal.transition_to(SecretChangeProposal.Status.EXPIRED)
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "proposal has expired",
            )

        actor = _actor_user(info)
        if actor is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "authenticated user required to approve",
            )
        # Self-approval gate (#488 + mirror of ALLOW_SELF_APPROVE_DEPLOYS).
        if proposal.proposer_id == actor.pk and not _self_approve_secrets_allowed():
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cannot approve your own proposal — another approver required",
            )
        if not _is_eligible_secret_approver(proposal.registered_app, user_id=actor.pk):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "you are not in this app's secret-approver set",
            )

        with transaction.atomic():
            # Re-vote is disallowed by the unique constraint; treat a
            # duplicate as a no-op (idempotent approve).
            existing = SecretChangeApproval.objects.filter(
                proposal=proposal,
                approver=actor,
                deleted_at__isnull=True,
            ).first()
            if existing is None:
                SecretChangeApproval.objects.create(
                    proposal=proposal,
                    approver=actor,
                    decision=SecretChangeApproval.Decision.APPROVED.value,
                    reason=(input.reason or "").strip(),
                    created_by=actor,
                    updated_by=actor,
                )
            approved_count = SecretChangeApproval.objects.filter(
                proposal=proposal,
                decision=SecretChangeApproval.Decision.APPROVED.value,
                deleted_at__isnull=True,
            ).count()
            if approved_count >= int(proposal.required_approver_count or 1):
                proposal.transition_to(SecretChangeProposal.Status.APPROVED)
                result = apply_proposal(proposal, actor=actor)
                if result.ok:
                    proposal.transition_to(SecretChangeProposal.Status.APPLIED)
                else:
                    proposal.apply_error = result.error
                    proposal.save(
                        update_fields=[
                            "apply_error",
                            "updated_at",
                            "version",
                        ]
                    )

        proposal.refresh_from_db()
        return gql_success(secret_change_proposal_to_type(proposal))

    @strawberry.field
    @mutation_audit(
        action="app.secret.proposal.reject",
        target=_proposal_target_from_input,
    )
    @require_permission(Permission.SECRET_APPROVE)
    @tenant_scoped()
    def reject_secret_change(
        self,
        info: Info,
        input: RejectSecretChangeInput,
    ) -> MutationResultType[SecretChangeProposalType]:
        reason = (input.reason or "").strip()
        if not reason:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reason is required",
                field="reason",
            )
        proposal = (
            SecretChangeProposal.objects.select_related("registered_app")
            .filter(guid=str(input.proposal_id), deleted_at__isnull=True)
            .first()
        )
        if proposal is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "proposal not found")
        if proposal.status != SecretChangeProposal.Status.PENDING.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"proposal is in status {proposal.status}, expected pending",
            )
        actor = _actor_user(info)
        if actor is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "authenticated user required to reject",
            )
        # Proposer rejecting their own proposal would be equivalent to
        # withdrawal — redirect to that path for clarity in the audit
        # trail.
        if proposal.proposer_id == actor.pk:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "use withdrawSecretChange to retract your own proposal",
            )
        if not _is_eligible_secret_approver(proposal.registered_app, user_id=actor.pk):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "you are not in this app's secret-approver set",
            )

        with transaction.atomic():
            existing = SecretChangeApproval.objects.filter(
                proposal=proposal,
                approver=actor,
                deleted_at__isnull=True,
            ).first()
            if existing is None:
                SecretChangeApproval.objects.create(
                    proposal=proposal,
                    approver=actor,
                    decision=SecretChangeApproval.Decision.REJECTED.value,
                    reason=reason,
                    created_by=actor,
                    updated_by=actor,
                )
            # ANY rejection moves the proposal to rejected — one nay
            # kills the proposal, mirroring the deploy quorum policy.
            proposal.transition_to(SecretChangeProposal.Status.REJECTED)

        proposal.refresh_from_db()
        return gql_success(secret_change_proposal_to_type(proposal))

    @strawberry.field
    @mutation_audit(
        action="app.secret.proposal.withdraw",
        target=_proposal_target_from_input,
    )
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def withdraw_secret_change(
        self,
        info: Info,
        input: WithdrawSecretChangeInput,
    ) -> MutationResultType[SecretChangeProposalType]:
        """Proposer-only retraction (#488).  Distinct from rejection so
        the audit trail records the difference between 'proposer
        changed their mind' and 'approver said no'."""
        proposal = (
            SecretChangeProposal.objects.select_related("registered_app")
            .filter(guid=str(input.proposal_id), deleted_at__isnull=True)
            .first()
        )
        if proposal is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "proposal not found")
        if proposal.status != SecretChangeProposal.Status.PENDING.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"proposal is in status {proposal.status}, expected pending",
            )
        actor = _actor_user(info)
        if actor is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "authenticated user required to withdraw",
            )
        if proposal.proposer_id != actor.pk:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "only the proposer can withdraw a proposal",
            )
        with transaction.atomic():
            proposal.transition_to(SecretChangeProposal.Status.WITHDRAWN)
        proposal.refresh_from_db()
        return gql_success(secret_change_proposal_to_type(proposal))


# Envelope keys that are stable configuration (region, prefix, name)
# rather than secrets.  Used by the reveal mutation to mark non-secret
# rows so the UI doesn't mask them.
_PUBLIC_ENVELOPE_KEY_SUFFIXES: tuple[str, ...] = (
    "_REGION",
    "_PREFIX",
    "_NAME",
    "_HOST",
    "_PORT",
    "_DB",
    "_BUCKET",
    "_DOMAIN",
    "_FROM",
    "_PROVIDER",
    "_SSL_MODE",
    "_TLS",
    "_TOPIC",
    "_INDEX",
    "_INDEX_PREFIX",
    "_TABLE_NAME",
    "_VOLUME",
    "_PARTITION_KEY",
    "_SORT_KEY",
    "_DISTRIBUTION_ID",
    "_DOMAIN_NAME",
    "_INVALIDATION_ROLE",
    "_ENDPOINT",
    "_NAMESPACE",
    "_ORG",
    "_ARN_OR_ID",
    "_MASTER_SECRET_REF",
    "_BOOTSTRAP_SERVERS",
    "_SECURITY_PROTOCOL",
    "_SASL_MECHANISM",
    "_SASL_USERNAME",
    "_TOPIC_PREFIX",
)


def _is_envelope_key_public(key: str) -> bool:
    """An envelope key is 'public' (non-secret) if it carries
    configuration shape rather than a credential.  The reveal mutation
    marks public keys with ``is_secret=False`` so the UI doesn't mask
    them; secret-shaped keys (PASSWORD, API_KEY, URL with embedded
    creds, etc.) stay masked."""
    upper = key.upper()
    # URLs typically embed creds (postgres://user:pass@host); treat as
    # secret unless explicitly suffixed to a region/endpoint marker.
    if upper.endswith("_URL"):
        return False
    if upper.endswith(("_PASSWORD", "_API_KEY", "_USER")):
        return False
    for suffix in _PUBLIC_ENVELOPE_KEY_SUFFIXES:
        if upper.endswith(suffix):
            return True
    return False
