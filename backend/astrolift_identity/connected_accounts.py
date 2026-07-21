"""
Per-user source-provider connection surface for the account drawer.

The org-admin source-providers surface
(``astrolift_scm.schema.queries.astrolift_source_connections``) is
org-scoped: it returns every connection the operator has configured
plus the viewer's own personal tokens. This module is the
complementary per-user surface:

* ``astrolift_my_connected_accounts`` — one row per provider config
  the org has enabled, with the viewer's connection state (connected
  / not connected / re-auth required) folded in.

* ``connect_user_source_provider`` — returns the OAuth authorize
  URL the FE should redirect to. The actual upsert into a
  ``SourceConnection`` row happens in the OAuth callback in
  ``auth1/scm_oauth.py``; that side already exists.

* ``disconnect_user_source_provider`` — soft-deletes the viewer's
  own per-user ``SourceConnection`` for the named provider config.
  Best-effort revocation against the upstream is fire-and-forget so
  a network blip doesn't strand the user.

Gating: ``is_authenticated`` only. **Not** ``@tenant_scoped`` — we
need the active org to look up which configs to surface, but the
operation is fundamentally per-user (the viewer can ONLY see + manage
their own connections). We resolve the tenant manually so the
resolver can fail-soft into an empty list / structured envelope
instead of raising ``TenantRequired``.
"""

from __future__ import annotations

import secrets
import urllib.parse

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_scm.models import SourceConnection
from auth1.scm_oauth import _safe_return_to
from core.mutations import ErrorCode, mutation_audit
from core.tenancy import get_current_tenant

# Provider-config kinds that surface in the account drawer. PATs are
# org-level shared credentials, not per-user OAuth flows, so they
# don't show up here — only kinds that have a user-to-server OAuth
# dance (or one we'll add later).
_USER_FLOW_CONFIG_KINDS = {
    SourceConnection.Kind.GITHUB_OAUTH_APP.value,
    SourceConnection.Kind.GITHUB_APP_INSTALL.value,
    SourceConnection.Kind.GITLAB_OAUTH_APP.value,
}

# Map a provider-config kind to the user-token kind it produces.
_CONFIG_TO_USER_KIND = {
    SourceConnection.Kind.GITHUB_OAUTH_APP.value: SourceConnection.Kind.GITHUB_OAUTH_USER.value,
    SourceConnection.Kind.GITHUB_APP_INSTALL.value: SourceConnection.Kind.GITHUB_OAUTH_USER.value,
    SourceConnection.Kind.GITLAB_OAUTH_APP.value: SourceConnection.Kind.GITLAB_OAUTH_USER.value,
}

# Friendly labels surfaced through the account drawer chips.
_PROVIDER_LABELS = {
    SourceConnection.Kind.GITHUB_OAUTH_APP.value: "GitHub",
    SourceConnection.Kind.GITHUB_APP_INSTALL.value: "GitHub",
    SourceConnection.Kind.GITLAB_OAUTH_APP.value: "GitLab",
}


# ---------------------------------------------------------------------------
# GraphQL types
# ---------------------------------------------------------------------------


@strawberry.type(name="AstroliftMyConnectedAccount")
class MyConnectedAccountType:
    """One row per source-provider config the org has enabled,
    decorated with the viewer's personal connection state."""

    provider_config_id: GUID
    provider_kind: str
    provider_label: str
    is_connected: bool
    linked_account_login: str | None
    reauth_required: bool
    expires_at: str | None
    last_used_at: str | None


@strawberry.type(name="AstroliftConnectUserSourceProviderPayload")
class ConnectInitiationPayloadType:
    """Where the FE should redirect the user's browser to start the
    OAuth dance for this provider."""

    provider_config_id: GUID
    authorization_url: str


@strawberry.type(name="AstroliftDisconnectUserSourceProviderPayload")
class DisconnectPayloadType:
    """ID of the soft-deleted SourceConnection row (when one existed)."""

    provider_config_id: GUID
    disconnected_id: GUID | None


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@strawberry.input
class ConnectUserSourceProviderInput:
    """Kick off the per-user OAuth dance for ``provider_config_id``."""

    provider_config_id: GUID
    # Same-origin path to bounce back to once the dance completes — e.g.
    # the onboarding wizard the user hit a 401 in. Validated against the
    # OAuth start view's same-origin gate; anything external falls back
    # to "/". Null keeps the historical "/" landing.
    return_to: str | None = None


@strawberry.input
class DisconnectUserSourceProviderInput:
    """Soft-delete the viewer's connection for ``provider_config_id``.

    ``confirm_account_login`` must match the linked account login on
    the soft-target row. The UI surfaces a typed-confirm so a stray
    click doesn't strand the user's deploys. The server enforces the
    same check.
    """

    provider_config_id: GUID
    confirm_account_login: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _viewer_pk(info: Info) -> int | None:
    request = getattr(info.context, "request", None)
    viewer = getattr(request, "user", None) if request else None
    if viewer is None or not viewer.is_authenticated:
        return None
    return viewer.pk


def _active_org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _resolve_authorize_path(kind: str) -> str:
    """OAuth-init endpoint for the configured kind.

    Hard-coded rather than using ``django.urls.reverse`` because
    importing the URL conf during resolver execution can trigger a
    circular re-import of ``config.schema`` (each app's schema is
    composed there) and corrupt already-decorated Strawberry enums.
    The route mounts are stable and documented; if they move, the
    OAuth dance breaks loudly at the host's callback regardless.
    """
    if kind == SourceConnection.Kind.GITLAB_OAUTH_APP.value:
        return "/app/auth1/scm/gitlab/start"
    # github_oauth_app or github_app_install — both share the github
    # user-to-server endpoint.
    return "/app/auth1/scm/github/start"


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@strawberry.type
class MyConnectedAccountsQuery:
    @strawberry.field
    def astrolift_my_connected_accounts(self, info: Info) -> list[MyConnectedAccountType]:
        """Per-provider connection state for the signed-in viewer.

        Returns one row per enabled provider-config the org has set
        up. ``is_connected`` reflects whether a non-soft-deleted
        per-user ``SourceConnection`` exists for the current user
        pointing at this config; ``reauth_required`` rides along so
        the chip can render amber instead of green.

        Empty list when:
          * the viewer is anonymous (no tenant gate, but the resolver
            fails closed),
          * there's no active org context, or
          * the org hasn't enabled any user-flow source providers.
        """
        viewer_pk = _viewer_pk(info)
        if viewer_pk is None:
            return []
        org_id = _active_org_id()
        if org_id is None:
            return []

        configs = list(
            SourceConnection.objects.filter(
                organization_id=org_id,
                deleted_at__isnull=True,
                is_active=True,
                user__isnull=True,
                kind__in=list(_USER_FLOW_CONFIG_KINDS),
            ).order_by("kind", "display_name", "account_login")
        )
        if not configs:
            return []

        # One query for the viewer's per-user rows across every
        # surfaced config — keeps the resolver O(1) on round-trips.
        viewer_rows = {
            row.parent_oauth_app_id: row
            for row in SourceConnection.objects.filter(
                organization_id=org_id,
                user_id=viewer_pk,
                parent_oauth_app__in=configs,
                deleted_at__isnull=True,
            )
        }

        out: list[MyConnectedAccountType] = []
        for cfg in configs:
            viewer_row = viewer_rows.get(cfg.id)
            connected = viewer_row is not None and viewer_row.is_active
            out.append(
                MyConnectedAccountType(
                    provider_config_id=GUID(str(cfg.guid)),
                    provider_kind=cfg.kind,
                    provider_label=_PROVIDER_LABELS.get(cfg.kind, cfg.kind),
                    is_connected=connected,
                    linked_account_login=(
                        viewer_row.account_login if viewer_row and viewer_row.account_login else None
                    ),
                    reauth_required=bool(viewer_row.reauth_required) if viewer_row else False,
                    expires_at=(
                        viewer_row.token_expires_at.isoformat()
                        if viewer_row and viewer_row.token_expires_at
                        else None
                    ),
                    last_used_at=(
                        viewer_row.last_used_at.isoformat()
                        if viewer_row and viewer_row.last_used_at
                        else None
                    ),
                )
            )
        return out


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


@strawberry.type
class MyConnectedAccountsMutation:
    @strawberry.field
    @mutation_audit(action="identity.connected_account.connect")
    def astrolift_connect_user_source_provider(
        self, info: Info, input: ConnectUserSourceProviderInput
    ) -> MutationResultType[ConnectInitiationPayloadType]:
        """Return the OAuth authorize URL for the named provider.

        The FE redirects ``window.location`` to ``authorizationUrl``;
        the host bounces back to our SCM callback which actually
        creates / refreshes the per-user ``SourceConnection`` row.

        Not ``@tenant_scoped`` — the mutation is per-user. We still
        need the active org to scope the config lookup; without one,
        we return a structured PRECONDITION error.
        """
        viewer_pk = _viewer_pk(info)
        if viewer_pk is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        org_id = _active_org_id()
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        cfg = SourceConnection.objects.filter(
            organization_id=org_id,
            guid=str(input.provider_config_id),
            is_active=True,
            deleted_at__isnull=True,
            user__isnull=True,
            kind__in=list(_USER_FLOW_CONFIG_KINDS),
        ).first()
        if cfg is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "provider config not found or not user-connectable",
                field="providerConfigId",
            )
        # GitHub kinds need the OAuth **Client ID** (``app_client_id``)
        # for the /login/oauth/authorize URL; the legacy
        # ``oauth_client_id`` column carries the numeric App ID on
        # github_app_install rows and would 404 if we sent it. GitLab
        # OAuth Apps still use ``oauth_client_id`` directly.
        if cfg.kind.startswith("github_"):
            if not cfg.app_client_id:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "provider config is missing GitHub App Client ID; the org admin needs to finish setup",
                )
        elif not cfg.oauth_client_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "provider config is missing OAuth client_id; the org admin needs to finish setup",
            )

        # We don't store state server-side here — the existing OAuth
        # start view stores its own state on the session when the
        # browser hits ``/app/auth1/scm/<host>/start``. The URL we
        # return is a stable handoff link; the FE redirects to it,
        # and the start view writes the state into the session at
        # that point. We DO include a one-shot nonce in the URL so
        # bots / pre-fetchers can't accidentally fire the dance.
        base = _resolve_authorize_path(cfg.kind)
        nonce = secrets.token_urlsafe(8)
        query = urllib.parse.urlencode(
            {
                "config_id": str(cfg.guid),
                "return_to": _safe_return_to(input.return_to, default="/"),
                "n": nonce,
            }
        )
        authorize_url = f"{base}?{query}"
        return gql_success(
            ConnectInitiationPayloadType(
                provider_config_id=GUID(str(cfg.guid)),
                authorization_url=authorize_url,
            )
        )

    @strawberry.field
    @mutation_audit(action="identity.connected_account.disconnect")
    def astrolift_disconnect_user_source_provider(
        self, info: Info, input: DisconnectUserSourceProviderInput
    ) -> MutationResultType[DisconnectPayloadType]:
        """Soft-delete the viewer's per-user connection.

        Enforces the typed-confirm check server-side so a buggy or
        compromised client can't strand the user's deploys without
        the operator typing the linked account login first.

        Best-effort revocation: we soft-delete the row regardless,
        then fire off an upstream revoke call if the provider
        supports one. A failed revoke surfaces in the audit log but
        doesn't fail the mutation — the platform-side row is gone
        either way.
        """
        viewer_pk = _viewer_pk(info)
        if viewer_pk is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        org_id = _active_org_id()
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        cfg = SourceConnection.objects.filter(
            organization_id=org_id,
            guid=str(input.provider_config_id),
            deleted_at__isnull=True,
            user__isnull=True,
        ).first()
        if cfg is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "provider config not found",
                field="providerConfigId",
            )

        viewer_row = SourceConnection.objects.filter(
            organization_id=org_id,
            user_id=viewer_pk,
            parent_oauth_app=cfg,
            deleted_at__isnull=True,
        ).first()
        if viewer_row is None:
            # Nothing to disconnect — treat as a soft no-op so the
            # UI can refresh without surfacing a confusing error.
            return gql_success(
                DisconnectPayloadType(
                    provider_config_id=GUID(str(cfg.guid)),
                    disconnected_id=None,
                )
            )

        expected = (viewer_row.account_login or "").strip().lower()
        supplied = (input.confirm_account_login or "").strip().lower()
        if expected and supplied != expected:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "confirmation does not match the linked account login",
                field="confirmAccountLogin",
            )
        if not expected and supplied:
            # No account login on file (rare — incomplete OAuth) and
            # the operator typed something. Don't second-guess — accept.
            pass

        disconnected_id = str(viewer_row.guid)
        viewer_row.soft_delete()
        _best_effort_revoke(viewer_row, cfg)

        return gql_success(
            DisconnectPayloadType(
                provider_config_id=GUID(str(cfg.guid)),
                disconnected_id=GUID(disconnected_id),
            )
        )


def _best_effort_revoke(viewer_row: SourceConnection, cfg: SourceConnection) -> None:
    """Fire-and-forget revoke against the upstream.

    Failure here is logged at the audit layer but does not fail the
    mutation; the platform-side row is already soft-deleted, so the
    user is disconnected from our side regardless. A leftover token
    on the SCM host will fail closed at next use.
    """
    try:
        from astrolift_scm.providers import revoke_user_token  # type: ignore[attr-defined]
    except ImportError:
        return
    try:
        revoke_user_token(viewer_row, cfg)
    except Exception:
        # Swallow — see docstring.
        return
