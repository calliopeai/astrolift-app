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
disclose them.

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
    cache = _query_cache(info)
    if cache is None:
        return _can_reveal(info, app=app, known_permissions=known_permissions, cache=None)
    return cache(
        f"secret.reveal:app:{app.pk}",
        lambda: _can_reveal(info, app=app, known_permissions=known_permissions, cache=cache),
    )


def redacted_manifest_text(
    info: Any,
    *,
    app: Any,
    raw_text: str,
    known_permissions: Iterable[str] | None = None,
) -> str:
    """``raw_text`` unchanged when the caller can reveal secrets, ``[env]``-masked otherwise.

    The one-line version of the gate every raw-manifest-text response
    must apply (#1920); see :func:`can_reveal_app_secrets`.
    """
    if not raw_text or can_reveal_app_secrets(info, app=app, known_permissions=known_permissions):
        return raw_text
    from astrolift_manifest.env_edit import redact_env_values

    return redact_env_values(raw_text)


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
    app: Any,
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
        effective = (
            resolve_effective_permissions(tenant, extra_scope=("APP", app.pk))
            if tenant is not None
            else set()
        )

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
