"""Temporary apps (#2433): set, extend, shorten or clear an app's expiry, and
the organization's policy for them. The rules live in
``astrolift_registry.expiry``."""

from __future__ import annotations

import datetime as dt

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.scopes import identity_organization_scope
from astrolift_registry.schema.types import (
    RegisteredAppType,
    TemporaryAppPolicyType,
    app_to_type,
    policy_to_type,
)
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.input(name="SetAppExpiryInput")
class SetAppExpiryInput:
    """Exactly one of ``expiresIn`` (from now), ``expiresAt``, ``extendBy``
    (from the current expiry) or ``clear`` (make the app permanent)."""

    app_slug: str
    expires_in: str | None = strawberry.field(default=None, description="30m, 4h, 7d, 6w or 3mo from now.")
    expires_at: dt.datetime | None = None
    extend_by: str | None = strawberry.field(
        default=None, description="Move the current expiry later by this long (30m, 4h, 7d, 6w, 3mo)."
    )
    clear: bool = strawberry.field(
        default=False,
        description=(
            "Make the app permanent. Needs app.update, the organization's rules for "
            "app.expiry.clear (a fresh verification by default), and is refused where the "
            "organization requires temporary apps. Agents are refused unless the organization allows them."
        ),
    )
    delete_data_on_expiry: bool | None = None


@strawberry.input(name="TemporaryAppPolicyInput")
class TemporaryAppPolicyInput:
    max_ttl: str = strawberry.field(default="90d", description="The longest an app may live (15m to 366d).")
    temporary_only_for_members: bool = strawberry.field(
        default=False, description="Members without org.update may create temporary apps only."
    )
    ttl_required_surfaces: list[str] = strawberry.field(
        default_factory=list,
        description="Surfaces whose new apps must expire: chat_studio, mcp, api_token.",
    )
    data_grace_days: int = strawberry.field(
        default=7,
        description="Days an expired app's managed services are kept before they are deleted (0 to 90).",
    )


def _app_target(*args, **kwargs):
    data = kwargs.get("input")
    return ("RegisteredApp", data.app_slug) if data is not None else None


def _change_extras(result) -> dict | None:
    data = getattr(result, "data", None)
    if data is None:
        return None
    return {"expires_at": data.expires_at.isoformat() if data.expires_at else None}


@strawberry.type
class ExpiryMutations:
    @strawberry.field(
        description=(
            "Set, extend, shorten or clear a temporary app's expiry (#2433). The timer moves at once "
            "and the 24 h and 1 h warnings are sent again for the new time. Audited as "
            "app.expiry.set, app.expiry.extend or app.expiry.clear."
        )
    )
    @mutation_audit(action="app.expiry.update", target=_app_target, extras=_change_extras)
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def set_app_expiry(self, info: Info, input: SetAppExpiryInput) -> MutationResultType[RegisteredAppType]:
        from astrolift_identity.action_safety import gate
        from astrolift_registry import expiry
        from astrolift_registry.models import RegisteredApp

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.select_related("organization", "team", "project")
            .filter(organization_id=org_id, slug=(input.app_slug or "").strip(), deleted_at__isnull=True)
            .first()
            if org_id is not None
            else None
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")
        given = [bool(input.expires_in), input.expires_at is not None, bool(input.extend_by), input.clear]
        if sum(given) != 1:
            return gql_failure(
                ErrorCode.VALIDATION.value, "give exactly one of expiresIn, expiresAt, extendBy or clear"
            )
        try:
            if input.clear:
                refusal = expiry.permanent_refusal(app.organization)
                if refusal is not None:
                    return gql_failure(ErrorCode.PERMISSION_DENIED.value, refusal, field="clear")
                deny = gate(info, "app.expiry.clear", app=app, resolver_name="set_app_expiry")
                if deny is not None:
                    return deny
                expires_at = None
            elif input.extend_by:
                if app.expires_at is None:
                    return gql_failure(ErrorCode.PRECONDITION.value, "the app is permanent", field="extendBy")
                try:
                    later = app.expires_at + expiry.parse_duration(input.extend_by)
                except expiry.ExpiryError as exc:
                    raise expiry.ExpiryError(str(exc), field="extendBy") from exc
                expires_at = expiry.resolve(app.organization, expires_in=None, expires_at=later)
            else:
                expires_at = expiry.resolve(
                    app.organization, expires_in=input.expires_in, expires_at=input.expires_at
                )
            expiry.set_expiry(
                app,
                expires_at,
                actor_user_id=tenant.actor_user_id,
                delete_data=input.delete_data_on_expiry,
            )
        except expiry.ExpiryError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field=exc.field)
        return gql_success(app_to_type(app, info=info))

    @strawberry.field(
        description=(
            "The organization's policy for temporary apps (#2433): the longest an app may live, "
            "whether members may create temporary apps only, which surfaces require an expiry and "
            "how long an expired app's data is kept."
        )
    )
    @mutation_audit(action="org.temporary_app_policy.update")
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def set_temporary_app_policy(
        self, info: Info, input: TemporaryAppPolicyInput
    ) -> MutationResultType[TemporaryAppPolicyType]:
        from astrolift_identity.models import Organization
        from astrolift_registry import expiry

        tenant = get_current_tenant()
        org = Organization.objects.filter(pk=tenant.organization_id if tenant else None).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")
        try:
            org.temporary_app_policy = expiry.policy_json(
                max_ttl=input.max_ttl,
                temporary_only_for_members=input.temporary_only_for_members,
                ttl_required_surfaces=list(input.ttl_required_surfaces),
                data_grace_days=input.data_grace_days,
            )
        except expiry.ExpiryError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field=exc.field)
        org.save(update_fields=["temporary_app_policy", "updated_at", "version"])
        return gql_success(policy_to_type(org))
