"""ApiToken minting, verification, scope enforcement, last-used touch.

Companion of :mod:`astrolift_lifecycle.deploy_tokens` — same lifecycle
shape (issue + verify + last-used stamp) but for the *user-level*
``alft_at_`` bearer instead of the *app-level* ``alft_dt_`` bearer.

The mutation in :mod:`astrolift_identity.schema.mutations` mints rows;
the auth integration in :mod:`astrolift_identity.auth_drf` calls
``verify_token`` + ``touch_token`` on every authed request.

Scopes are access classes (``read:apps``, ``write:apps``,
``agent-env-spec:write``, ``project:write``, ``secret:write``, ``workflow:write``,
``workflow:trigger``, ``read:clusters``, ``admin``). They narrow the
*user's* permission set down to the subset the token may exercise —
they never widen it. ``admin`` implies all other scopes (operators
intentionally treat it as the "full power" wildcard so they don't
have to enumerate every scope at mint time).
"""

from __future__ import annotations

import contextvars
import dataclasses
import datetime as dt
import hashlib
import secrets
from collections.abc import Iterable
from uuid import UUID

from django.utils import timezone

PLAINTEXT_PREFIX = "alft_at_"

# Canonical scope catalog. Mirrored in the UI's create-token sheet.
# Keep names lower-case + colon-separated to match the deploy-token
# convention (``app.deploy`` → here ``read:apps`` etc). New scopes
# get added here first so the mutation's validator picks them up.
SCOPE_READ_APPS = "read:apps"
SCOPE_WRITE_APPS = "write:apps"
SCOPE_READ_CLUSTERS = "read:clusters"
SCOPE_AGENT_ENV_SPEC_WRITE = "agent-env-spec:write"
SCOPE_PROJECT_WRITE = "project:write"
SCOPE_SECRET_READ = "secret:read"
SCOPE_SECRET_WRITE = "secret:write"
SCOPE_MCP_READ = "mcp:read"
SCOPE_MCP_DISPATCH = "mcp:dispatch"
SCOPE_MCP_WRITE = "mcp:write"
SCOPE_WORKFLOW_WRITE = "workflow:write"
SCOPE_WORKFLOW_TRIGGER = "workflow:trigger"
SCOPE_APP_ONBOARD = "app:onboard"
SCOPE_TEAM_WRITE = "team:write"
SCOPE_ADMIN = "admin"

ALLOWED_SCOPES: frozenset[str] = frozenset(
    {
        SCOPE_READ_APPS,
        SCOPE_WRITE_APPS,
        SCOPE_READ_CLUSTERS,
        SCOPE_AGENT_ENV_SPEC_WRITE,
        SCOPE_PROJECT_WRITE,
        SCOPE_SECRET_READ,
        SCOPE_SECRET_WRITE,
        SCOPE_MCP_READ,
        SCOPE_MCP_DISPATCH,
        SCOPE_MCP_WRITE,
        SCOPE_WORKFLOW_WRITE,
        SCOPE_WORKFLOW_TRIGGER,
        SCOPE_APP_ONBOARD,
        SCOPE_TEAM_WRITE,
        SCOPE_ADMIN,
    }
)

DEFAULT_SCOPES: tuple[str, ...] = (
    SCOPE_READ_APPS,
    SCOPE_READ_CLUSTERS,
    SCOPE_MCP_READ,
)

# Browser-approved CLI sessions need to exercise the CLI's documented
# operator commands — ALL of them (#1676): #1533's ``app:onboard`` covered
# register/ci-setup but left ``astro app deploy``, ``astro team create`` and
# ``astro project create`` ceiling-blocked for every token the CLI can mint,
# admins included. The ceiling is sized to the CLI surface; RBAC remains the
# authority on who may actually do each of these. Keep this separate from
# DEFAULT_SCOPES: generic API tokens and mobile/browser enrollment stay
# read-only unless the operator explicitly selects a stronger scope. Still
# excluded on purpose: ``admin`` (org/billing/cluster surfaces) and any path
# to ``api_token.create`` — a leaked CLI token must not mint its successors.
CLI_DEVICE_SCOPES: tuple[str, ...] = (
    *DEFAULT_SCOPES,
    SCOPE_WRITE_APPS,
    SCOPE_AGENT_ENV_SPEC_WRITE,
    SCOPE_PROJECT_WRITE,
    SCOPE_TEAM_WRITE,
    SCOPE_SECRET_WRITE,
    SCOPE_MCP_DISPATCH,
    SCOPE_MCP_WRITE,
    SCOPE_WORKFLOW_WRITE,
    SCOPE_WORKFLOW_TRIGGER,
    SCOPE_APP_ONBOARD,
)

_current_api_token: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "astrolift_api_token",
    default=None,
)

_READ_APP_PERMISSIONS = frozenset(
    {
        "org.read",
        "team.read",
        "project.read",
        "app.read",
        "app.read_logs",
        "app.read_metrics",
        "agent.read",
        "agent_env_spec.read",
        "agent_task.watch",
        "skill.read",
        "workflow.read",
        "secret.list",
    }
)
_WRITE_APP_PREFIXES = ("app.", "agent.", "agent_env_spec.", "skill.", "workflow.")
_WRITE_APP_PERMISSIONS = frozenset({"secret.write"})


def set_current_api_token(token) -> contextvars.Token:
    return _current_api_token.set(token)


def reset_current_api_token(token: contextvars.Token) -> None:
    _current_api_token.reset(token)


def get_current_api_token():
    return _current_api_token.get()


def token_matches_organization(token, organization_guid: str) -> bool:
    if not organization_guid:
        return True
    try:
        expected = UUID(organization_guid)
    except (ValueError, TypeError, AttributeError):
        return False
    return expected == token.organization.guid


def token_scope_allows_permission(token, permission: str) -> bool:
    """Apply the bearer scope ceiling to one RBAC permission slug."""
    if token is None or has_scope(token, SCOPE_ADMIN):
        return True
    scopes = set(token.scopes or [])
    if SCOPE_READ_APPS in scopes and permission in _READ_APP_PERMISSIONS:
        return True
    if SCOPE_WRITE_APPS in scopes and (
        permission.startswith(_WRITE_APP_PREFIXES) or permission in _WRITE_APP_PERMISSIONS
    ):
        return True
    if SCOPE_READ_CLUSTERS in scopes and permission == "provider_plugin.read":
        return True
    if SCOPE_AGENT_ENV_SPEC_WRITE in scopes and permission in {
        "agent_env_spec.create",
        "agent_env_spec.update",
        "agent_env_spec.delete",
    }:
        return True
    if SCOPE_PROJECT_WRITE in scopes and permission in {
        "project.create",
        "project.update",
        "project.delete",
    }:
        return True
    if SCOPE_TEAM_WRITE in scopes and permission in {
        "team.create",
        "team.update",
        "team.delete",
    }:
        return True
    if SCOPE_SECRET_READ in scopes and permission == "secret.read":
        return True
    if SCOPE_SECRET_WRITE in scopes and permission == "secret.write":
        return True
    if SCOPE_MCP_READ in scopes and permission == "agent.read":
        return True
    if SCOPE_MCP_DISPATCH in scopes and permission in {"agent.dispatch", "agent_task.send_input"}:
        return True
    if SCOPE_MCP_WRITE in scopes and permission in {"agent.create", "agent.update"}:
        return True
    if SCOPE_WORKFLOW_WRITE in scopes and permission in {
        "workflow.create",
        "workflow.update",
        "workflow.delete",
    }:
        return True
    if SCOPE_WORKFLOW_TRIGGER in scopes and permission == "workflow.trigger":
        return True
    if SCOPE_APP_ONBOARD in scopes and permission in {"app.create", "app.update"}:
        return True
    return False


@dataclasses.dataclass(slots=True, frozen=True)
class IssuedApiToken:
    plaintext: str
    token_hash: str
    last4: str


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def mint_token() -> IssuedApiToken:
    """Generate a fresh ``alft_at_…`` secret. Caller persists the
    hash + last4 onto the ``ApiToken`` row and discloses the
    plaintext to the operator exactly once."""
    plaintext = PLAINTEXT_PREFIX + secrets.token_urlsafe(32)
    return IssuedApiToken(
        plaintext=plaintext,
        token_hash=_hash(plaintext),
        last4=plaintext[-4:],
    )


def normalize_scopes(scopes: Iterable[str] | None) -> list[str]:
    """Apply the default scope set when caller passed none.

    Deduplicates while preserving order so the badge order in the UI
    matches the order the operator picked. Unknown scopes are NOT
    silently dropped — callers (the mutation) should call
    :func:`validate_scopes` first to surface them as a VALIDATION
    error.
    """
    if scopes is None:
        return list(DEFAULT_SCOPES)
    raw = [s.strip() for s in scopes if s and s.strip()]
    if not raw:
        return list(DEFAULT_SCOPES)
    seen: set[str] = set()
    out: list[str] = []
    for s in raw:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def validate_scopes(scopes: Iterable[str]) -> list[str]:
    """Return the list of unknown scope strings from ``scopes``.

    Empty list means everything is valid. Mutation surfaces the
    unknowns as a single VALIDATION envelope error.
    """
    return sorted({s for s in scopes if s not in ALLOWED_SCOPES})


def has_scope(token, required: str) -> bool:
    """Does ``token`` carry ``required`` (or the ``admin`` wildcard)?

    ``token`` is an ``ApiToken`` row. Pure read; no DB roundtrip.
    """
    scopes = token.scopes or []
    if SCOPE_ADMIN in scopes:
        return True
    return required in scopes


def enforce_scopes(token, required: Iterable[str]) -> str | None:
    """Return the first missing required scope, or None on success.

    Caller maps a return value into a 403 / PERMISSION_DENIED.
    """
    for r in required:
        if not has_scope(token, r):
            return r
    return None


def with_active_org_member(queryset, *, user: str, organization: str):
    """Keep only rows whose ``user`` still belongs to ``organization``.

    A credential is only as good as the membership it was issued under
    (#1910). Its owner must be an active account with a live, active ORG
    ``Member`` row in that organization, and the organization must not be
    soft-deleted. SCIM deprovisioning drops only the membership: the
    account stays active while the person belongs to another org, and
    their RoleBindings survive, so without this a removed person's bearer
    keeps acting in the org they left. ``user`` and ``organization`` name
    the queryset's foreign keys, so the bearer lookup and the device-flow
    refresh apply the same rule.
    """
    from django.db.models import Exists, OuterRef

    from astrolift_identity.models import Member

    membership = Member.objects.filter(
        user_id=OuterRef(f"{user}_id"),
        scope_kind=Member.ScopeKind.ORG,
        scope_id=OuterRef(f"{organization}_id"),
        is_active=True,
        deleted_at__isnull=True,
    )
    return queryset.filter(
        Exists(membership),
        **{f"{user}__is_active": True, f"{organization}__deleted_at__isnull": True},
    )


def is_active_org_member(user_id: int, organization_id: int) -> bool:
    """Does ``user_id`` belong to ``organization_id``, by the rule
    :func:`with_active_org_member` applies to credentials? The audit writers
    use it to decide whether a mutation may be filed under the tenant org
    (#1955)."""
    from django.db.models import Exists, OuterRef

    from astrolift_identity.models import Member, Organization

    membership = Member.objects.filter(
        user_id=user_id,
        user__is_active=True,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=OuterRef("pk"),
        is_active=True,
        deleted_at__isnull=True,
    )
    return (
        Organization.objects.filter(pk=organization_id, deleted_at__isnull=True)
        .filter(Exists(membership))
        .exists()
    )


def verify_token(plaintext: str):
    """Hash-lookup a presented plaintext bearer.

    Returns the matching ``ApiToken`` row when:

    * the hash matches a live row (``deleted_at IS NULL``)
    * the row is not revoked
    * the row has not expired
    * its owner is still an active member of its organization
      (:func:`with_active_org_member`)

    Returns ``None`` in every other case — the caller should respond
    with a generic ``401`` regardless of *why* so we don't leak
    "exists-but-revoked" vs "no such token".

    Every bearer entry point (the HTTP middleware and the exec
    WebSocket relay) authenticates through here, so the membership rule
    holds on every surface without a per-view check.
    """
    if not plaintext or not plaintext.startswith(PLAINTEXT_PREFIX):
        return None

    from astrolift_identity.models import ApiToken

    digest = _hash(plaintext)
    now = timezone.now()
    row = with_active_org_member(
        ApiToken.objects.filter(
            token_hash=digest,
            deleted_at__isnull=True,
            is_revoked=False,
        ),
        user="user",
        organization="organization",
    ).first()
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at <= now:
        return None
    return row


def touch_token(token, *, ip: str | None = None, user_agent: str | None = None) -> None:
    """Stamp ``last_used_at`` / ``last_used_ip`` / ``last_used_agent``.

    Called on every request that authed against this token. Uses
    ``update_fields`` so the audit ``Tracking`` columns
    (``updated_at`` / ``version``) advance but no other row data
    accidentally re-saves. ``ip`` is whatever
    :func:`client_ip_from_request` resolved; an invalid string is
    coerced to ``None`` because ``GenericIPAddressField`` would
    otherwise raise.
    """
    now = timezone.now()
    token.last_used_at = now
    token.last_used_ip = ip if _is_ip(ip) else None
    token.last_used_agent = (user_agent or "")[:512]
    token.save(
        update_fields=[
            "last_used_at",
            "last_used_ip",
            "last_used_agent",
            "updated_at",
            "version",
        ]
    )


def client_ip_from_request(request) -> str | None:
    """Extract the caller's IP, honoring ``X-Forwarded-For``.

    Behind ALB / Cloudfront we receive a comma-separated chain; the
    left-most entry is the original client. Falls back to
    ``REMOTE_ADDR`` (single proxy / direct connect) when no XFF is
    present. Returns ``None`` when we can't resolve a usable IP.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "") if hasattr(request, "META") else ""
    if xff:
        first = xff.split(",")[0].strip()
        if _is_ip(first):
            return first
    remote = request.META.get("REMOTE_ADDR", "") if hasattr(request, "META") else ""
    return remote if _is_ip(remote) else None


def user_agent_from_request(request) -> str:
    if not hasattr(request, "META"):
        return ""
    return (request.META.get("HTTP_USER_AGENT", "") or "")[:512]


def _is_ip(value: str | None) -> bool:
    """Cheap parse: GenericIPAddressField needs a well-formed v4/v6."""
    if not value:
        return False
    import ipaddress

    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def deadline_from_days(days: int | None) -> dt.datetime | None:
    """Translate ``expires_in_days`` to an absolute UTC instant.

    Centralized here so the mutation and any future re-issue flow
    agree on how the input shape maps to a deadline column.
    """
    if not days or int(days) <= 0:
        return None
    return timezone.now() + dt.timedelta(days=int(days))
