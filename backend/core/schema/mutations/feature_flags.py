"""``setFeatureFlag`` — the platform-admin runtime feature flipper.

Flips a public Constance feature flag from the in-app admin "feature
flipper" screen (/administration/features) so operators no longer need
the Django admin. Only the curated ``_PUBLIC_FEATURE_FLAGS`` allow-list
in ``core/schema/types/server_info.py`` is settable — the same list
``astroliftServerInfo.featureFlags`` reflects out — so this can never
write an arbitrary Constance key.

A flag is a per-install Constance value: flipping it changes the install
for every tenant, so only the platform operator may set one, as with the
other fleet-wide operator mutations (e.g. ``resyncAllAstroliftCiWorkflows``
in ``astrolift_scm``). ``Permission.ADMIN_ELEVATE`` alone is not that gate:
the stock org owner and admin roles hold it (#1978). Not ``@tenant_scoped``,
because a flag is not per-org data.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, check_platform_operator, require_permission
from core.schema.types.server_info import _PUBLIC_FEATURE_FLAGS, FeatureFlagInfo

# public dotted key -> (constance_key, description). Built once from the
# single source of truth in server_info so the *settable* set can never
# drift from the *readable* set surfaced by astroliftServerInfo.
_PUBLIC_KEY_TO_CONSTANCE: dict[str, tuple[str, str]] = {
    public_key: (constance_key, description)
    for constance_key, public_key, description in _PUBLIC_FEATURE_FLAGS
}


def _feature_flag_target(*args, **kwargs):
    """``@mutation_audit`` target hook: stamp the affected flag onto the
    audit row as ``(FeatureFlag, <public_key>)`` so the change is
    queryable per flag. Returns ``None`` (untargeted) if the key arg
    isn't present (defensive — never blocks the audit write)."""
    key = kwargs.get("key")
    if key is None and len(args) >= 3:
        key = args[2]
    if not key:
        return None
    return "FeatureFlag", key


def _set_model_connection_flag(info, config, key, enabled):
    """Fresh install operator admission; other feature setters retain their contract."""
    from dataclasses import replace
    from types import SimpleNamespace

    from django.contrib.auth import get_user_model
    from django.http import HttpRequest

    from astrolift_identity import abac
    from astrolift_identity.api_tokens import get_current_api_token, with_active_org_member
    from astrolift_identity.models import ApiToken
    from core.current_credential import current_dispatch_credential
    from core.current_session import fresh_authenticated_session
    from core.permissions import PermissionDenied, check_permission
    from core.tenancy import get_current_tenant

    permission = Permission.ADMIN_ELEVATE
    request = getattr(info.context, "request", None)
    actor_id = getattr(getattr(info.context, "user", None), "pk", None)
    tenant = get_current_tenant()
    actor = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    if actor is None or (tenant is not None and tenant.actor_user_id != actor.pk):
        raise PermissionDenied(permission, None, "Current operator authority is unavailable.")
    with current_dispatch_credential(permission):
        token = get_current_api_token()
        if token is not None:
            if token.team_id is not None:
                raise PermissionDenied(permission, None, "Current operator credential is unavailable.")
            if not with_active_org_member(
                ApiToken.objects.filter(pk=token.pk, user_id=actor.pk),
                user="user",
                organization="organization",
            ).exists():
                raise PermissionDenied(permission, None, "Current operator credential is unavailable.")
            attrs = abac.attributes_for(actor.pk)
        elif isinstance(request, HttpRequest) or hasattr(request, "session"):
            bag = fresh_authenticated_session(request, actor_user_id=actor.pk, permission=permission)
            attrs = abac.attributes_from_request(
                SimpleNamespace(META=getattr(request, "META", {}), session=bag), actor.pk
            )
        else:
            attrs = abac.attributes_for(actor.pk)
        with abac.request_attributes(replace(attrs, cache={})):
            check_platform_operator(actor, gate=permission)
            check_permission(permission)
            setattr(config, key, bool(enabled))


@strawberry.type
class FeatureFlagMutations:
    @strawberry.field(
        description=(
            "Toggle a public runtime feature flag (the admin 'feature "
            "flipper'). Platform-admin only. ``key`` is the public dotted "
            "key from ``astroliftServerInfo.featureFlags`` (e.g. "
            "``zentinelle.enabled``); the backing Constance value is set "
            "and the updated flag is returned. Unknown / non-public keys "
            "are rejected with a VALIDATION error. Install-time features "
            "(``astroliftServerInfo.buildTimeFeatures``) are NOT settable "
            "here — they require a redeploy."
        )
    )
    @mutation_audit(action="admin.feature_flag.set", target=_feature_flag_target)
    @require_permission(Permission.ADMIN_ELEVATE)
    def set_feature_flag(self, info: Info, key: str, enabled: bool) -> MutationResultType[FeatureFlagInfo]:
        check_platform_operator(info.context.user, gate=Permission.ADMIN_ELEVATE)
        mapping = _PUBLIC_KEY_TO_CONSTANCE.get(key)
        if mapping is None:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown or non-public feature flag: {key!r}",
                field="key",
            )
        constance_key, description = mapping

        # Lazy import matches _resolve_feature_flags(): both resolve
        # sys.modules["constance"].config, so a test that swaps in a fake
        # constance module sees the write reflected on the next read.
        from constance import config as constance_config

        if key == "models.bedrock_connections_enabled":
            _set_model_connection_flag(info, constance_config, constance_key, enabled)
        else:
            setattr(constance_config, constance_key, bool(enabled))

        return gql_success(FeatureFlagInfo(key=key, enabled=bool(enabled), description=description))
