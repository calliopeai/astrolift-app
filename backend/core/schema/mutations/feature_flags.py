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
    def set_feature_flag(
        self, info: Info, key: str, enabled: bool
    ) -> MutationResultType[FeatureFlagInfo]:
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

        setattr(constance_config, constance_key, bool(enabled))

        return gql_success(
            FeatureFlagInfo(key=key, enabled=bool(enabled), description=description)
        )
