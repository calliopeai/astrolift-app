"""Explicit default roles (#2299), with no provider construction or DNS writes."""

import hashlib
import hmac
import json

from django.db import connection, transaction

from astrolift_clusters.models import ManagedDomain
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import get_current_tenant

_ROLES = {
    "none": frozenset(),
    "tenant_apps": frozenset({"tenant_apps"}),
    "preview_envs": frozenset({"preview_envs"}),
    "both": frozenset({"tenant_apps", "preview_envs"}),
}
# Shared rows have no organization FK. Operators in different organizations
# take this same transaction-scoped protocol lock before the domain row locks.
_SHARED_DEFAULT_LOCK = 22990001


def defaults_fingerprint(domain, organization, rows=None):
    """Reviewed scope impact, not permission or provider-health proof."""

    def facts(items):
        return sorted((str(row.guid), row.version, row.default_for) for row in items)

    if rows is None:
        rows = ManagedDomain.objects.filter(organization_id=domain.organization_id, deleted_at__isnull=True)
    own = (
        ManagedDomain.objects.filter(organization=organization, deleted_at__isnull=True)
        if domain.organization_id is None
        else ()
    )
    override = organization.default_managed_domain
    payload = {
        "domain": "astrolift.managed-domain.defaults-review.v1",
        "organization": str(organization.guid),
        "organization_version": organization.version,
        "selected": str(domain.guid),
        "scope": "shared" if domain.organization_id is None else "own",
        "rows": facts(rows),
        "own_precedence": facts(own),
        "override": None
        if override is None
        else (
            str(override.guid),
            override.version,
            bool(override.deleted_at),
            override.verification_state,
            override.dns_driver,
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def default_refusal(domain, organization, roles=frozenset({"tenant_apps", "preview_envs"})):
    """Current metadata eligibility; this is not DNS/TLS health or executable authority."""
    from astrolift_clusters.domain_diagnostics import provision_cluster
    from astrolift_drivers.registry import DriverNotFound, plugins

    if domain.dns_driver == "cloudflare_read_only":
        return "READ_ONLY_DOMAIN"
    if domain.verification_state == ManagedDomain.VerificationState.PENDING:
        return "DOMAIN_PROOF_PENDING"
    if domain.provision_state != "mark_active" or not domain.provision_zone_id:
        return "DOMAIN_PROVISIONING_INCOMPLETE"
    if not isinstance(domain.dns_config, dict):
        return "DOMAIN_WRITER_UNAVAILABLE"
    cluster = provision_cluster(domain)
    if cluster is None:
        return "DOMAIN_WRITER_UNAVAILABLE"
    provider = cluster.provider_plugin
    if provider.deleted_at is not None or not provider.is_enabled:
        return "DOMAIN_WRITER_UNAVAILABLE"
    if domain.dns_driver not in (provider.slug, "route53" if provider.slug == "aws" else provider.slug):
        return "DOMAIN_WRITER_CHANGED"
    try:
        plugins.get(provider.slug, "dns")
    except DriverNotFound:
        return "DOMAIN_WRITER_UNAVAILABLE"
    # The resolver's explicit override outranks role flags for BOTH use cases.
    # This operation never clears or reinterprets that separate setting.
    override = organization.default_managed_domain
    if (
        override is not None
        and override.deleted_at is None
        and override.verification_state != ManagedDomain.VerificationState.PENDING
        and override.dns_driver != "cloudflare_read_only"
    ):
        return "ORGANIZATION_DEFAULT_OVERRIDE"
    if domain.organization_id is None:
        own = (
            ManagedDomain.objects.filter(organization=organization, deleted_at__isnull=True)
            .exclude(verification_state=ManagedDomain.VerificationState.PENDING)
            .exclude(dns_driver="cloudflare_read_only")
        )
        if any(_ROLES.get(row.default_for, frozenset()) & roles for row in own):
            return "ORGANIZATION_DEFAULT_TAKES_PRECEDENCE"
    return ""


def lock_default_scope(organization_id):
    """Take the locks ``update_domain`` holds while it changes a scope's default
    roles, in its order, and return the scope's live rows (#2572). Removing a
    zone checks whether it is an effective default under them, so a default
    change cannot land between that check and the delete. Call inside a
    transaction."""
    if organization_id is None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_SHARED_DEFAULT_LOCK])
    return list(
        ManagedDomain.objects.select_for_update()
        .filter(organization_id=organization_id, deleted_at__isnull=True)
        .order_by("pk")
    )


def _credential_pin(token):
    if token is None:
        return None
    return (
        token.pk,
        str(token.guid),
        token.user_id,
        token.organization_id,
        token.team_id,
        tuple(sorted(token.scopes or [])),
        token.expires_at,
        token.token_hash,
    )


def _same_credential(original, current):
    observed = _credential_pin(current)
    if original is None or observed is None:
        return original is observed
    return original[:-1] == observed[:-1] and hmac.compare_digest(original[-1], observed[-1])


def admitted_update(info, domain, original_credential):
    """Fresh credential, browser sidecar, membership and ABAC after lock waits."""
    from astrolift_clusters.domain_diagnostics import _allowed, _auth, _operator
    from astrolift_clusters.scopes import domain_org_scope
    from astrolift_identity.api_tokens import get_current_api_token

    permission = Permission.PROVIDER_PLUGIN_CONFIGURE
    allowed = False
    with _auth(info) as user:
        if not _same_credential(original_credential, get_current_api_token()):
            raise PermissionDenied(permission, None, "Original credential changed.")
        allowed = (domain.organization_id is not None or _operator(user)) and _allowed(
            permission, domain_org_scope(permission, "domain_id")({"domain_id": str(domain.guid)})
        )
    if not allowed:
        raise PermissionDenied(permission, None, "Current domain configuration permission is required.")


def update_domain(info, input):
    """Serialize explicit role choice within the existing own/shared scope."""
    role = input.default_for
    if role is not None and role not in _ROLES:
        raise ValueError("INVALID_DEFAULT_ROLE")
    from astrolift_identity.api_tokens import get_current_api_token

    original_credential = _credential_pin(getattr(info.context.request, "_api_token", None))
    if not _same_credential(original_credential, get_current_api_token()):
        raise PermissionDenied(
            Permission.PROVIDER_PLUGIN_CONFIGURE, None, "Original credential is unavailable."
        )
    tenant = get_current_tenant()
    with transaction.atomic():
        organization = (
            Organization.objects.select_for_update()
            .filter(pk=tenant.organization_id, deleted_at__isnull=True)
            .first()
        )
        if organization is None:
            raise PermissionDenied(
                Permission.PROVIDER_PLUGIN_CONFIGURE, None, "Current organization is unavailable."
            )
        visible = (
            ManagedDomain.objects.filter(guid=str(input.id), deleted_at__isnull=True)
            .filter(organization_id=organization.pk)
            .first()
        )
        if visible is None:
            visible = ManagedDomain.objects.filter(
                guid=str(input.id), organization_id__isnull=True, deleted_at__isnull=True
            ).first()
        if visible is None:
            return None
        if visible.organization_id is None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_SHARED_DEFAULT_LOCK])
        rows = list(
            ManagedDomain.objects.select_for_update()
            .filter(organization_id=visible.organization_id, deleted_at__isnull=True)
            .order_by("pk")
        )
        domain = next((row for row in rows if row.pk == visible.pk), None)
        if domain is None:
            return None
        if input.expected_version is not None and input.expected_version != domain.version:
            raise ValueError("MANAGED_DOMAIN_VERSION_CHANGED")
        admitted_update(info, domain, original_credential)
        if input.expected_defaults_fingerprint is not None and (
            input.expected_defaults_fingerprint != defaults_fingerprint(domain, organization, rows)
        ):
            raise ValueError("MANAGED_DOMAIN_DEFAULTS_CHANGED")
        if role not in (None, "none") and domain.dns_driver == "cloudflare_read_only":
            raise ValueError("READ_ONLY_DOMAIN")
        if input.dns_config is not None:
            domain.dns_config = input.dns_config
        if domain.dns_driver == "cloudflare_read_only":
            from astrolift_clusters.dns_provider_connections import admitted

            with admitted(info.context.request, write=True):
                pass
        selected = _ROLES[role] if role is not None else frozenset()
        if selected:
            reason = default_refusal(domain, organization, selected)
            if reason:
                raise ValueError(reason)
        # Compute all sibling updates before saving; malformed legacy flags
        # are not guessed or normalized as a side effect of this selection.
        siblings = []
        for row in rows:
            if row.pk == domain.pk or not selected:
                continue
            previous = _ROLES.get(row.default_for)
            if previous is None:
                raise ValueError("EXISTING_DEFAULT_ROLE_INVALID")
            remaining = previous - selected
            if remaining != previous:
                replacement = next(key for key, value in _ROLES.items() if value == remaining)
                siblings.append((row, replacement))
        for row, replacement in siblings:
            row.default_for = replacement
            row.save(update_fields={"default_for", "version", "updated_at"})
        if role is not None:
            domain.default_for = role
        if input.is_wildcard_managed is not None:
            domain.is_wildcard_managed = input.is_wildcard_managed
        if input.dns_config is not None:
            domain.dns_config = input.dns_config
        domain.save()
        return domain
