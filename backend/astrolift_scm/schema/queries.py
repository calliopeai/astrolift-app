"""Read-only SCM queries."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

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


@strawberry.type
class ScmQuery:
    @strawberry.field
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_source_connections(self, info: Info) -> list[SourceConnectionType]:
        """Org-level connections + the current viewer's own personal
        connections. Other users' personal tokens never surface — a
        token belongs to the user that minted it, period."""
        from django.db.models import Q

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        viewer_pk = viewer.pk if viewer is not None and viewer.is_authenticated else None

        scope = Q(user__isnull=True)
        if viewer_pk is not None:
            scope = scope | Q(user_id=viewer_pk)

        qs = (
            SourceConnection.objects.filter(organization_id=org_id, deleted_at__isnull=True)
            .filter(scope)
            .select_related("user", "parent_oauth_app")
            .order_by("user_id", "-is_active", "kind", "account_login")[:200]
        )
        return [source_connection_to_type(c) for c in qs]

    @strawberry.field
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_ssh_deploy_keys(self, info: Info, app_slug: str | None = None) -> list[SshDeployKeyType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        qs = SshDeployKey.objects.filter(organization_id=org_id).select_related("registered_app")
        if app_slug == "":
            # Caller asked for org-scoped only.
            qs = qs.filter(registered_app__isnull=True)
        elif app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        qs = qs.order_by("registered_app__slug", "name")
        return [ssh_key_to_type(k) for k in qs[:200]]

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
