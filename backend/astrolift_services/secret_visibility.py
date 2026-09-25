"""Shared "may this caller see a plaintext ``[env]`` value" predicate (#1920).

``revealAppSecret`` is the one sanctioned path to plaintext: it requires
``Permission.APP_READ`` *and* ``Permission.SECRET_READ`` scoped to the
app, plus a fresh session elevation (``@requires_elevation``). Every
other surface that serializes raw manifest text or echoes the ``[env]``
table back to a caller (the ``RegisteredApp`` GraphQL type, the
manifest-stage mutations, the secret-write mutations' echoed staged
manifest, a secret change proposal's value) must apply the identical
gate before showing a value, or a caller lacking ``secret.read`` can
read every staged secret through a field that was never meant to
disclose them. Container, job and task ``env`` values are the same kind
of secret and go through the same gate (#1948): in the manifest text, on
``AstroliftContainer.env``, in the rendered-manifest previews, and in a
manifest read straight from the source repo.

:func:`can_reveal_app_secrets` answers that question without a second
RBAC round-trip when the caller already resolved the app's effective
permission set (list resolvers do, via
:func:`astrolift_identity.permission_resolver.resolve_effective_permissions_for_apps`,
the bulk helper that backs ``viewerPermissions`` so a page of N apps
costs one query, not N); a single-app caller (a mutation returning one
updated app) gets a fresh single-app lookup instead. Either way the same
API-token scope ceiling and elevation check apply, so the answer can
never drift from what ``revealAppSecret`` itself would decide.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from graphql import OperationType

from core.permissions import Permission


def can_reveal_app_secrets(
    info: Any,
    *,
    app: Any,
    known_permissions: Iterable[str] | None = None,
) -> bool:
    """True when ``info``'s caller could call ``revealAppSecret`` on ``app``.

    ``known_permissions`` is the caller's already-resolved effective
    permission set for this app. Pass it when you have one (a list
    resolver iterating many apps) so the cost stays one bulk RBAC
    query for the whole page; omit it (the default) for a single app
    and this does its own lookup.
    """
    scope = ("APP", app.pk)
    cache = _query_cache(info)
    if cache is None:
        return _can_reveal(info, scope=scope, known_permissions=known_permissions, cache=None)
    return cache(
        f"secret.reveal:app:{app.pk}",
        lambda: _can_reveal(info, scope=scope, known_permissions=known_permissions, cache=cache),
    )


def can_reveal_org_secrets(info: Any) -> bool:
    """True when ``info``'s caller could call ``revealAppSecret`` on any
    app in its organization: ``app.read`` and ``secret.read`` bound
    org-wide, plus the same token ceiling and elevation (#1948).

    For a response with no app to scope the check to, such as a repo
    file fetched before any app is registered from it."""
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return False
    scope = ("ORG", tenant.organization_id)
    cache = _query_cache(info)
    if cache is None:
        return _can_reveal(info, scope=scope, known_permissions=None, cache=None)
    return cache(
        f"secret.reveal:org:{tenant.organization_id}",
        lambda: _can_reveal(info, scope=scope, known_permissions=None, cache=cache),
    )


def redacted_manifest_text(
    info: Any,
    *,
    app: Any,
    raw_text: str,
    known_permissions: Iterable[str] | None = None,
) -> str:
    """``raw_text`` unchanged when the caller can reveal secrets, env-masked otherwise.

    The one-line version of the gate every raw-manifest-text response
    must apply (#1920); see :func:`can_reveal_app_secrets`. The mask
    covers ``[env]`` and every container, job and task ``env`` (#1948).
    """
    if not raw_text or can_reveal_app_secrets(info, app=app, known_permissions=known_permissions):
        return raw_text
    from astrolift_manifest.env_edit import redact_env_values

    return redact_env_values(raw_text)


def redacted_render_input(info: Any, *, app: Any, manifest: Any) -> Any:
    """``manifest`` (a ``NormalizedManifest``) unchanged when the caller can
    reveal secrets, with every container env value masked otherwise (#1948).

    The rendered-manifest previews copy each container's env literals
    into the pod spec's ``env:`` entries, the values the manifest text
    masks, so they are rendered from this instead."""
    if can_reveal_app_secrets(info, app=app):
        return manifest
    from astrolift_manifest.env_edit import redact_container_env

    return redact_container_env(manifest)


def redacted_repo_file(info: Any, *, path: str, content: str) -> str:
    """A repo file's ``content``, env-masked when it is manifest-shaped and
    the caller cannot reveal secrets org-wide (#1948).

    Push to Repo writes the staged manifest, env literals included, into
    the repo, so its ``astrolift.toml`` carries exactly what the manifest
    fields mask. The file has no app to scope a check to (the wizard
    reads it before one exists), so revealing it takes the org-wide
    grants of :func:`can_reveal_org_secrets`. Manifest-shaped means a
    ``.toml`` path or text that parses as TOML; any other file comes back
    as fetched."""
    if not content or not _is_manifest_shaped(path, content) or can_reveal_org_secrets(info):
        return content
    from astrolift_manifest.env_edit import redact_env_values

    return redact_env_values(content)


def _is_manifest_shaped(path: str, content: str) -> bool:
    if path.lower().endswith(".toml"):
        return True
    import tomllib

    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return False
    return True


def _query_cache(info: Any) -> Callable[[str, Callable[[], bool]], bool] | None:
    """The per-request ``check_permission`` cache, for a query only.

    A query cannot change the caller's grants or elevation while it runs,
    so one answer per app, and one elevation check, can serve a whole
    page. A mutation document can (``elevateAdminSession`` or
    ``attestSession`` ahead of a field that echoes a manifest), so it
    always asks afresh."""
    operation = getattr(getattr(info, "operation", None), "operation", None)
    check = getattr(getattr(info, "context", None), "check_permission", None)
    if operation is not OperationType.QUERY or not callable(check):
        return None
    return check


def _can_reveal(
    info: Any,
    *,
    scope: tuple[str, int],
    known_permissions: Iterable[str] | None,
    cache: Callable[[str, Callable[[], bool]], bool] | None,
) -> bool:
    from astrolift_identity.api_tokens import get_current_api_token, token_scope_allows_permission
    from astrolift_identity.permission_resolver import resolve_effective_permissions
    from astrolift_identity.step_up import is_elevated_and_attested
    from core.tenancy import get_current_tenant

    if known_permissions is not None:
        effective = known_permissions
    else:
        tenant = get_current_tenant()
        effective = resolve_effective_permissions(tenant, extra_scope=scope) if tenant is not None else set()

    if Permission.APP_READ.value not in effective or Permission.SECRET_READ.value not in effective:
        return False

    # RBAC grants a role holds are not narrowed by the bearer token
    # that authenticated this particular call: a read-only deploy
    # token calling on behalf of an org_admin must not inherit that
    # admin's secret.read just because the role has it (mirrors the
    # ceiling ``core.permissions.check_permission`` applies).
    api_token = get_current_api_token()
    if api_token is not None and not (
        token_scope_allows_permission(api_token, Permission.APP_READ.value)
        and token_scope_allows_permission(api_token, Permission.SECRET_READ.value)
    ):
        return False

    if cache is None:
        return is_elevated_and_attested(info)
    return cache("secret.reveal:elevated", lambda: is_elevated_and_attested(info))
