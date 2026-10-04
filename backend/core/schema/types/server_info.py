"""Public ``astroliftServerInfo`` handshake (#479).

Multi-install clients (mobile, CLI, SDK) call this BEFORE login so they
can decide which UI / commands to surface for the specific install
they're talking to. Different installs may be at different versions
with different feature support, so a static client pinned to a minimum
version would be a bad multi-install UX.

Permission model — unauthenticated by design:

* The query MUST be callable without a session cookie or any tenant
  context. The mobile install-registration flow probes the URL before
  the user logs in to learn whether device-flow is supported.
* The response carries NO operator-sensitive data: capabilities and
  feature flags here are an intentional, curated public surface.
* No tenant_scoped / require_permission decorators on the resolver —
  see the docstring on :class:`AstroliftServerInfoQuery` for the
  audit-friendly justification. The AST guardrail
  (``core/tests/test_tenancy_guardrail.py``) only scans
  ``astrolift_*/schema/*.py``, so a resolver under ``core/schema/``
  doesn't need an EXEMPT entry — but adding one would be incorrect
  anyway since this resolver lives in core, not in a domain app.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime

import strawberry
from django.conf import settings
from django.utils import timezone
from strawberry.types import Info

from core.capabilities_registry import shipped_capabilities

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Feature-flag public allow-list.
# ---------------------------------------------------------------------

# Constance keys safe to publish to unauthenticated callers. Each entry
# is (constance_key, public_key_in_response, description). Anything
# touching secrets, retention bounds an attacker could leverage, or
# tenant-counts stays off this list.
_PUBLIC_FEATURE_FLAGS: tuple[tuple[str, str, str], ...] = (
    (
        "BEDROCK_MODEL_CONNECTIONS_ENABLED",
        "models.bedrock_connections_enabled",
        "Existing Bedrock connections are enabled; current hosting admission and app invocation are checked separately.",
    ),
    (
        "TEMPORAL_ENABLED",
        "workflows.temporal_enabled",
        "Durable-workflow runtime is online; clients can expect workflow ids on long-running mutations.",
    ),
    (
        "DEPLOY_PIPELINE_ENABLED",
        "deploys.pipeline_enabled",
        "App deploy pipeline (start/promote/rollback/teardown) is enabled.",
    ),
    (
        "EMAIL_NOTIFICATIONS",
        "notifications.email_enabled",
        "Outbound email notifications are enabled on this install.",
    ),
    (
        "ZENTINELLE_ENABLED",
        "zentinelle.enabled",
        "Zentinelle governance surfaces (agent Activity/Reasoning/Token-Usage/Compliance tabs) are enabled on this install.",
    ),
    (
        "ZENTINELLE_GATEWAY_ENABLED",
        "zentinelle.gateway_enabled",
        "The Zentinelle gateway is deployed into clusters registered with an organization's Zentinelle connection.",
    ),
    (
        "ALLOW_SELF_APPROVE_DEPLOYS",
        "approvals.self_approve_allowed",
        "Single-engineer / dev orgs may approve their own deploy requests.",
    ),
    (
        "DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED",
        "deploy_tokens.legacy_prefix_accepted",
        "Pre-#449 ``alfdt_`` deploy-token prefix is still accepted alongside the canonical ``alft_dt_`` shape.",
    ),
    (
        "ADMIN_COST_ENABLED",
        "admin.cost_enabled",
        "Platform-admin Cost console (/administration/cost) is enabled.",
    ),
    (
        "ADMIN_QUOTAS_ENABLED",
        "admin.quotas_enabled",
        "Platform-admin Quotas console (/administration/quotas) is enabled.",
    ),
    (
        "ADMIN_PERMISSIONS_ENABLED",
        "admin.permissions_enabled",
        "Platform-admin Permissions console (/administration/permissions) is enabled.",
    ),
    (
        "CHAT_STUDIO_INTEGRATION_ALLOWED",
        "modules.chat_studio_integration_allowed",
        "Organizations may turn on the Chat Studio integration module. Off forces it off for every organization.",
    ),
    (
        "AGENT_LIVE_ATTACH_ALLOWED",
        "modules.agent_live_attach_allowed",
        "Organizations may turn on live agent attach. Off forces it off for every organization.",
    ),
    (
        "CHAT_STUDIO_AGENT_RUNS_ALLOWED",
        "modules.chat_studio_agent_runs_allowed",
        "Organizations may turn on launching registered Astrolift agents from Chat Studio. "
        "Off forces it off for every organization.",
    ),
)


@strawberry.type(description="Runtime feature toggle reported by ``astroliftServerInfo``.")
class FeatureFlagInfo:
    key: str
    enabled: bool
    description: str | None = None


# ---------------------------------------------------------------------
# Build-time (install-time) feature inventory.
# ---------------------------------------------------------------------

# Human-readable descriptions for the ``config.features.Feature`` enum.
# These features gate app / schema LOADING at boot (they change
# INSTALLED_APPS + which GraphQL modules assemble), so unlike the
# Constance runtime flags above they are NOT live-toggleable — flipping
# them requires a redeploy. Surfaced read-only so an operator sees the
# full feature set and which env var controls each. Keyed by the enum
# value (which doubles as the public dotted key).
_BUILD_TIME_FEATURE_DESCRIPTIONS: dict[str, str] = {
    "workflows": "Workflow-definition app + its GraphQL surface (visual-flow / TOML manifests).",
    "temporal": "Durable-workflow runtime integration (Temporal client + activities).",
    "opensearch": "OpenSearch-backed log history / search service.",
    "file_uploads": "File-upload endpoints + presigned-URL flow.",
    "deploy_pipeline": "App deploy pipeline apps/activities are loaded at boot.",
    "agents": "Agents + dispatch apps and their GraphQL surface.",
}


@strawberry.type(
    description=(
        "Install-time (build-time) platform feature reported by "
        "``astroliftServerInfo``. These gate app / schema loading at boot "
        "and are NOT runtime-toggleable — changing one requires a "
        "redeploy. ``envVar`` is the environment variable that controls it."
    )
)
class BuildTimeFeatureInfo:
    key: str
    enabled: bool
    env_var: str
    description: str | None = None


@strawberry.type(
    description=(
        "Install identity + capabilities handshake (#479). "
        "Returned by ``astroliftServerInfo``. Callable by unauthenticated "
        "clients so multi-install mobile / CLI / SDK callers can pick the "
        "right UI and gate commands before login."
    )
)
class AstroliftServerInfo:
    version: str
    api_version: str
    install_id: str
    install_slug: str
    install_label: str | None = None
    region: str | None = None
    server_time: datetime = strawberry.field(
        description="Current server-side wall clock (UTC). Used by clients to detect clock drift.",
    )
    capabilities: list[str] = strawberry.field(default_factory=list)
    feature_flags: list[FeatureFlagInfo] = strawberry.field(default_factory=list)
    build_time_features: list[BuildTimeFeatureInfo] = strawberry.field(
        default_factory=list,
        description=(
            "Install-time feature inventory (read-only). Requires a "
            "redeploy to change; surfaced for operator awareness."
        ),
    )
    auth_methods: list[str] = strawberry.field(default_factory=list)


# ---------------------------------------------------------------------
# Resolver helpers — pure, easy to unit-test, no info-context inputs.
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _InstallIdentity:
    install_id: str
    install_slug: str
    install_label: str | None
    region: str | None


def _resolve_install_identity() -> _InstallIdentity:
    """Resolve the install identity from env, falling back to a stable
    derived value when nothing is configured.

    Operators set ``ASTROLIFT_INSTALL_ID`` / ``_SLUG`` / ``_LABEL`` /
    ``_REGION`` in production. Dev installs typically set neither; for
    them we derive a hash from the django secret key + hostname so
    restarts return the same id (mobile clients use it to detect "same
    URL is now a different install").
    """
    explicit_id = os.environ.get("ASTROLIFT_INSTALL_ID", "").strip()
    if explicit_id:
        install_id = explicit_id
    else:
        seed = f"{settings.SECRET_KEY}|{socket.gethostname()}".encode()
        install_id = "install-" + hashlib.sha256(seed).hexdigest()[:24]

    slug = os.environ.get("ASTROLIFT_INSTALL_SLUG", "").strip() or "local"
    raw_label = os.environ.get("ASTROLIFT_INSTALL_LABEL", "").strip()
    label = raw_label or None
    raw_region = os.environ.get("ASTROLIFT_INSTALL_REGION", "").strip()
    region = raw_region or None
    return _InstallIdentity(install_id=install_id, install_slug=slug, install_label=label, region=region)


def _resolve_api_version() -> str:
    """SHA256 fingerprint of the live GraphQL schema string, prefixed
    with the running platform version so clients can spot both major
    upgrades and minor schema additions.

    Importing the schema lazily keeps this module safe to import from
    ``config.schema`` (no circular import — we are imported from there).
    """
    try:
        from config.schema import schema as live_schema

        sdl = live_schema.as_str()
        digest = hashlib.sha256(sdl.encode("utf-8")).hexdigest()[:16]
    except Exception:  # pragma: no cover — schema import never raises in practice
        logger.warning("astroliftServerInfo: could not fingerprint schema", exc_info=True)
        digest = "unfingerprinted"
    return f"{settings.VERSION}+{digest}"


def _resolve_feature_flags() -> list[FeatureFlagInfo]:
    """Read the public-safe Constance keys and reflect them out.

    Constance lookups can raise if the cache backend isn't wired (test
    bootstrap edge cases); each key is read defensively so one missing
    backend doesn't take down the whole handshake.
    """
    try:
        from constance import config as constance_config
    except Exception:  # pragma: no cover — constance is always installed
        logger.warning("astroliftServerInfo: constance not importable", exc_info=True)
        return []

    out: list[FeatureFlagInfo] = []
    for constance_key, public_key, description in _PUBLIC_FEATURE_FLAGS:
        try:
            value = bool(getattr(constance_config, constance_key))
        except Exception:
            # A missing key on a freshly-migrated install is acceptable
            # — surface it as disabled so the client sees the absence
            # cleanly rather than getting a 500 from the handshake.
            value = False
        out.append(FeatureFlagInfo(key=public_key, enabled=value, description=description))
    return out


def _resolve_build_time_features() -> list[BuildTimeFeatureInfo]:
    """Reflect the ``config.features.Feature`` enum out read-only.

    These are env-var gated and evaluated at boot (they change
    INSTALLED_APPS + schema assembly), so they are reported for operator
    awareness only — the ``setFeatureFlag`` mutation cannot touch them.
    Read defensively so a config edge case never takes down the
    handshake.
    """
    try:
        from config.features import FEATURE_DEFAULTS, Feature, is_enabled
    except Exception:  # pragma: no cover — config.features always imports
        logger.warning("astroliftServerInfo: config.features not importable", exc_info=True)
        return []

    out: list[BuildTimeFeatureInfo] = []
    for feature in Feature:
        env_var, _default = FEATURE_DEFAULTS[feature]
        try:
            enabled = bool(is_enabled(feature))
        except Exception:
            enabled = False
        out.append(
            BuildTimeFeatureInfo(
                key=feature.value,
                enabled=enabled,
                env_var=env_var,
                description=_BUILD_TIME_FEATURE_DESCRIPTIONS.get(feature.value),
            )
        )
    return out


def _resolve_auth_methods() -> list[str]:
    """Enumerate the auth methods the client may present to this install.

    Order matters: clients pick the first method they support. Session
    cookie is always available because that's the local-dev /
    superuser-login path. Auth0 surfaces only when configured (OIDC /
    Cognito both surface through the AUTH0_* settings).
    """
    methods = ["session_cookie"]
    if getattr(settings, "AUTH0_DOMAIN", None):
        methods.append("auth0")
    return methods


def allow_anonymous(fn):
    """Marker decorator: this resolver is intentionally callable without
    authentication.

    No behavioural effect — the decorator exists so a reader of the
    resolver sees a loud, grep-able marker that the missing
    ``@tenant_scoped`` / ``@require_permission`` stack is deliberate
    rather than a missed guard. New entries must be reviewed.
    """
    fn.__allow_anonymous__ = True  # type: ignore[attr-defined]
    return fn


# ---------------------------------------------------------------------
# Root query class. Wired into the assembled schema in config/schema.py.
# ---------------------------------------------------------------------


@strawberry.type
class AstroliftServerInfoQuery:
    """Root query exposing the install handshake.

    The single field ``astrolift_server_info`` is unauthenticated by
    design — see module docstring. No data here is tenant-scoped; the
    capability list and feature-flag allow-list are curated public
    surfaces (see ``core/capabilities_registry.py`` and
    ``_PUBLIC_FEATURE_FLAGS`` above).
    """

    @strawberry.field(
        description=(
            "Multi-install handshake. Returns version, capabilities, "
            "feature flags, install identity, and server time so a "
            "mobile / CLI / SDK client can decide which UI to render "
            "before logging in."
        )
    )
    @allow_anonymous
    def astrolift_server_info(self, info: Info) -> AstroliftServerInfo:
        identity = _resolve_install_identity()
        return AstroliftServerInfo(
            version=settings.VERSION,
            api_version=_resolve_api_version(),
            install_id=identity.install_id,
            install_slug=identity.install_slug,
            install_label=identity.install_label,
            region=identity.region,
            server_time=timezone.now(),
            capabilities=shipped_capabilities(),
            feature_flags=_resolve_feature_flags(),
            build_time_features=_resolve_build_time_features(),
            auth_methods=_resolve_auth_methods(),
        )


__all__ = [
    "AstroliftServerInfo",
    "AstroliftServerInfoQuery",
    "BuildTimeFeatureInfo",
    "FeatureFlagInfo",
    "allow_anonymous",
]
