"""Read-only SCM queries."""

from __future__ import annotations

import strawberry
from django.db.models import Q
from strawberry.types import Info

from astrolift_graphql import PageType, keyset_page, search_q
from astrolift_scm.models import SourceConnection, SshDeployKey
from astrolift_scm.providers import ProviderError, fetch_file, list_repos
from astrolift_scm.schema.types import (
    RemoteRepoListType,
    SourceConnectionType,
    SourceFileType,
    SshDeployKeyType,
    remote_repo_to_type,
    source_connection_to_type,
    ssh_key_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


def _flag_reauth_on_user_token(conn: SourceConnection, exc: ProviderError) -> None:
    """A recoverable ``AUTH_FAILED`` reached through a per-user OAuth
    token means that token expired or was revoked. Flip
    ``reauth_required`` so the account drawer + repo picker surface the
    amber "Re-authorize" affordance and the operator can mint a fresh
    token in place; the OAuth callback clears it on a successful
    reconnect. Org-level rows (PATs, App installs) aren't reconnectable
    via the per-user dance, so they're left untouched — a 403 on those
    is a permission fix, not a token refresh."""
    if (
        exc.code != "AUTH_FAILED"
        or not exc.recoverable
        or conn.user_id is None
        or not conn.kind.endswith("_oauth_user")
        or conn.reauth_required
    ):
        return
    conn.reauth_required = True
    conn.save(update_fields=["reauth_required", "updated_at", "version"])


def _viewer_user_id(info: Info) -> int | None:
    """The authenticated caller's pk, or None for anonymous.

    A personal SCM token belongs to the user that minted it, so this is
    the second half of the connection-visibility scope (the first being
    the tenant org).
    """
    request = getattr(info.context, "request", None)
    viewer = getattr(request, "user", None) if request else None
    if viewer is not None and getattr(viewer, "is_authenticated", False):
        return viewer.pk
    return None


def _source_connections_qs(*, viewer_pk: int | None, search: str | None = None):
    """Filtered, unordered connections visible to this viewer.

    Org-level rows (``user IS NULL``) plus the viewer's OWN personal
    connections; another operator's personal token never surfaces.

    Shared by the list field and its paginated sibling so the two can
    never disagree about which rows exist. Ordering is deliberately
    not applied here — ``keyset_page`` imposes it from the seek key.

    Fails closed when there is no tenant org: ``organization_id=None``
    matches no row on a non-null FK (#1183).
    """
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None

    scope = Q(user__isnull=True)
    if viewer_pk is not None:
        scope = scope | Q(user_id=viewer_pk)

    qs = (
        SourceConnection.objects.filter(organization_id=org_id, deleted_at__isnull=True)
        .filter(scope)
        .select_related("user", "parent_oauth_app")
    )
    if search:
        # Same four fields the settings table filtered client-side,
        # so moving the box server-side doesn't change what matches.
        qs = qs.filter(search_q(search, "kind", "api_base_url", "account_login", "display_name"))
    return qs


def _ssh_deploy_keys_qs(*, app_slug: str | None, search: str | None = None):
    """Filtered, unordered deploy keys for the caller's org.

    Shared by the list field and its paginated sibling. ``app_slug``
    is tri-state: None = every key in the org, ``""`` = org-scoped
    keys only, a slug = that app's keys. Ordering is deliberately not
    applied here — ``keyset_page`` imposes it from the seek key.

    Fails closed when there is no tenant org (#1183).
    """
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = SshDeployKey.objects.filter(organization_id=org_id).select_related("registered_app")
    if app_slug == "":
        # Caller asked for org-scoped only.
        qs = qs.filter(registered_app__isnull=True)
    elif app_slug:
        qs = qs.filter(registered_app__slug=app_slug)
    if search:
        # Mirrors the settings table's client-side filter.
        qs = qs.filter(search_q(search, "name", "fingerprint_sha256", "registered_app__slug"))
    return qs


@strawberry.type
class ScmQuery:
    @strawberry.field(
        deprecation_reason=(
            "Caps at 200 rows with no way to reach the 201st. Use astroliftSourceConnectionsPage."
        )
    )
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_source_connections(self, info: Info) -> list[SourceConnectionType]:
        """Org-level connections + the current viewer's own personal
        connections. Other users' personal tokens never surface — a
        token belongs to the user that minted it, period."""
        qs = _source_connections_qs(viewer_pk=_viewer_user_id(info)).order_by(
            "user_id", "-is_active", "kind", "account_login"
        )
        return [source_connection_to_type(c) for c in qs[:200]]

    @strawberry.field
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_source_connections_page(
        self,
        info: Info,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[SourceConnectionType]:
        """Cursor-paginated source connections (#1235).

        Replaces ``astroliftSourceConnections``, whose 200-row cap made
        the 201st connection unreachable rather than merely slow to
        reach. An org that onboards per-user OAuth tokens accumulates one
        row per operator, so the cap is reachable in practice.

        Seek key is ``(-created_at, -guid)``. The list field's display
        ordering (grouped by user, then active, kind, login) can't carry
        a cursor: ``user_id`` is nullable and ``is_active`` is a boolean,
        so neither is a usable sort key. Newest-first is the trade.
        """
        page = keyset_page(
            _source_connections_qs(viewer_pk=_viewer_user_id(info), search=search),
            cursor=after,
            limit=limit,
        )
        return page.map(source_connection_to_type)

    @strawberry.field(
        deprecation_reason=(
            "Caps at 200 rows with no way to reach the 201st. Use astroliftSshDeployKeysPage."
        )
    )
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_ssh_deploy_keys(self, info: Info, app_slug: str | None = None) -> list[SshDeployKeyType]:
        qs = _ssh_deploy_keys_qs(app_slug=app_slug).order_by("registered_app__slug", "name")
        return [ssh_key_to_type(k) for k in qs[:200]]

    @strawberry.field
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_ssh_deploy_keys_page(
        self,
        info: Info,
        app_slug: str | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[SshDeployKeyType]:
        """Cursor-paginated SSH deploy keys (#1235).

        Replaces ``astroliftSshDeployKeys``: an org that mints a per-app
        keypair at registration accumulates one row per app, so the old
        200-row cap silently hid keys once the fleet outgrew it.

        Seek key is ``(-created_at, -guid)`` rather than the list field's
        ``(registered_app__slug, name)`` — the app slug is NULL on
        org-scoped keys, and a NULL sort column truncates a keyset walk.
        """
        page = keyset_page(
            _ssh_deploy_keys_qs(app_slug=app_slug, search=search),
            cursor=after,
            limit=limit,
        )
        return page.map(ssh_key_to_type)

    @strawberry.field
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_available_repos(
        self,
        info: Info,
        connection_id: str,
        search: str | None = None,
        limit: int = 100,
    ) -> RemoteRepoListType:
        """Repos the operator can register against, surfaced through
        a stored SourceConnection's credentials. The visibility-scope
        policy on the connection is applied at this layer; recoverable
        errors (auth failure, network blip) come back as a structured
        envelope so the UI can render a 'reconnect' affordance inline."""
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return RemoteRepoListType(
                repos=[],
                error_code="NO_ORG",
                error_message=None,
                recoverable=False,
            )

        conn = SourceConnection.objects.filter(
            organization_id=org_id,
            guid=str(connection_id),
            is_active=True,
            deleted_at__isnull=True,
        ).first()
        if conn is None:
            return RemoteRepoListType(
                repos=[],
                error_code="NOT_FOUND",
                error_message="connection not found or inactive",
                recoverable=False,
            )

        try:
            rows = list_repos(conn, search=search, limit=limit)
        except ProviderError as exc:
            _flag_reauth_on_user_token(conn, exc)
            return RemoteRepoListType(
                repos=[],
                error_code=exc.code,
                error_message=exc.message,
                recoverable=exc.recoverable,
            )

        return RemoteRepoListType(
            repos=[remote_repo_to_type(r) for r in rows],
            error_code=None,
            error_message=None,
            recoverable=False,
        )

    @strawberry.field
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_source_file(
        self,
        info: Info,
        connection_id: str,
        repo_full_name: str,
        path: str,
        ref: str,
    ) -> SourceFileType:
        """Fetch a single file from a remote repo at ``ref`` through
        a stored connection. Powers the wizard's manifest auto-fetch
        (read ``astrolift.toml`` from the repo's default branch and
        pre-fill the textarea).

        ``content`` null + ``error_code`` null = "file genuinely
        doesn't exist on this ref" — the UI shows the manual paste
        affordance. ``error_code`` non-null = recoverable auth /
        transient API failure; render a reconnect affordance."""
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return SourceFileType(
                repo_full_name=repo_full_name,
                path=path,
                ref=ref,
                content=None,
                error_code="NO_ORG",
                error_message=None,
                recoverable=False,
            )

        conn = SourceConnection.objects.filter(
            organization_id=org_id,
            guid=str(connection_id),
            is_active=True,
            deleted_at__isnull=True,
        ).first()
        if conn is None:
            return SourceFileType(
                repo_full_name=repo_full_name,
                path=path,
                ref=ref,
                content=None,
                error_code="NOT_FOUND",
                error_message="connection not found or inactive",
                recoverable=False,
            )

        try:
            content = fetch_file(
                conn,
                repo_full_name=repo_full_name,
                path=path,
                ref=ref,
            )
        except ProviderError as exc:
            _flag_reauth_on_user_token(conn, exc)
            return SourceFileType(
                repo_full_name=repo_full_name,
                path=path,
                ref=ref,
                content=None,
                error_code=exc.code,
                error_message=exc.message,
                recoverable=exc.recoverable,
            )

        return SourceFileType(
            repo_full_name=repo_full_name,
            path=path,
            ref=ref,
            content=content,
            error_code=None,
            error_message=None,
            recoverable=False,
        )
