"""Module constants and helper functions for the mutation package."""

from __future__ import annotations

import datetime as dt
from datetime import timedelta

from django.utils import timezone
from strawberry.types import Info

from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    AppSecretMetadata,
    ManagedService,
    SecretChangeProposal,
)
from astrolift_services.secret_change_diff import build_diff
from core.tenancy import get_current_tenant


def _caller_org_id() -> int | None:
    """Current tenant's organization id, or None when there's no tenant
    context. Mutations over org-owned rows MUST treat None as
    deny-by-default (not-found), never as "all rows" (#1042 / #1183).

    ``@tenant_scoped()`` only asserts a tenant context exists; it does
    NOT filter any queryset. Every mutation that fetches by slug or guid
    has to add the org constraint itself or it reads/writes cross-org.
    """
    tenant = get_current_tenant()
    return tenant.organization_id if tenant is not None else None


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
    scope: str = "all",
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
            scope=scope,
            set_at=timezone.now(),
            created_by=actor,
            updated_by=actor,
        )
        return row
    # Apply optional updates atomically.  We don't clear
    # ``expires_at`` to None unless the caller explicitly passes a
    # value — `None` means "don't touch" per the input contract.
    updates: dict = {"set_at": timezone.now(), "scope": scope}
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
    soft-deleted, or not an email kind.

    Org-scoped (#1183): ManagedService has no direct org column, so we
    scope through the owning app. A cross-org / no-tenant caller can't
    resolve another tenant's email service by guessing its guid — org_id
    None → IS NULL → no match (RegisteredApp.organization is non-null) →
    deny-by-default. This single fix closes the cross-org path for every
    email-observability mutation that routes through here (suppression
    add/remove + template create/update/delete)."""
    svc = (
        ManagedService.objects.select_related(
            "app_environment",
            "app_environment__tenant_cluster",
            "app_environment__tenant_cluster__provider_plugin",
            "registered_app",
        )
        .filter(
            guid=str(managed_service_id),
            registered_app__organization_id=_caller_org_id(),
            deleted_at__isnull=True,
        )
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
