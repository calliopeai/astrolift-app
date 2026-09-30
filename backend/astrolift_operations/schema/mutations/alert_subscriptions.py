"""AlertSubscriptionMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_operations.models import (
    UserAlertSubscription,
)
from astrolift_operations.schema.mutations.types import (
    ClearAlertSubscriptionInput,
    SetAlertSubscriptionInput,
)
from astrolift_operations.schema.types import (
    UserAlertSubscriptionType,
    user_alert_subscription_to_type,
)
from astrolift_operations.scopes import subscription_scope
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class AlertSubscriptionMutations:
    # ---- Alert subscriptions (#747) --------------------------------

    @strawberry.field
    @mutation_audit(action="alert_subscription.set")
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def set_alert_subscription(
        self,
        info: Info,
        input: SetAlertSubscriptionInput,
    ) -> MutationResultType[UserAlertSubscriptionType]:
        """Upsert the caller's per-app alert notification preference (#747).

        Creates a new subscription row or updates the existing active
        one for the same (user, app, alert_kind) tuple. Soft-deletes
        the previous row when upserting so the audit trail is intact.
        """
        from astrolift_registry.models import RegisteredApp

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "authentication required")

        valid_kinds = {k for k, _ in UserAlertSubscription.AlertKind.choices}
        if input.alert_kind not in valid_kinds:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"alert_kind must be one of {sorted(valid_kinds)}",
                field="alert_kind",
            )
        valid_channels = {c for c, _ in UserAlertSubscription.Channel.choices}
        if input.channel not in valid_channels:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"channel must be one of {sorted(valid_channels)}",
                field="channel",
            )

        # Scope the app lookup to the caller's org (#1183): slugs are
        # unique per-org, so an unscoped lookup let a caller bind a
        # subscription to (and confirm the existence of) a same-slug app
        # in another tenant. tenant is non-None here (guarded above).
        app = RegisteredApp.objects.filter(
            slug=input.app_slug,
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, f"app '{input.app_slug}' not found")

        existing = UserAlertSubscription.objects.filter(
            user_id=tenant.actor_user_id,
            registered_app=app,
            alert_kind=input.alert_kind,
            deleted_at__isnull=True,
        ).first()
        if existing is not None:
            existing.channel = input.channel
            existing.enabled = input.enabled
            existing.save(update_fields=["channel", "enabled", "updated_at", "version"])
            return gql_success(user_alert_subscription_to_type(existing))

        sub = UserAlertSubscription.objects.create(
            user_id=tenant.actor_user_id,
            registered_app=app,
            alert_kind=input.alert_kind,
            channel=input.channel,
            enabled=input.enabled,
        )
        return gql_success(user_alert_subscription_to_type(sub))

    @strawberry.field
    @mutation_audit(action="alert_subscription.clear")
    @require_permission(Permission.APP_READ, scope=subscription_scope(Permission.APP_READ))
    @tenant_scoped()
    def clear_alert_subscription(
        self,
        info: Info,
        input: ClearAlertSubscriptionInput,
    ) -> MutationResultType[UserAlertSubscriptionType]:
        """Soft-delete a per-app alert subscription (#747).

        Reverts the (user, app, alert_kind) slot to the dispatcher's
        noisy-fallback default. Uses the GUID returned by
        ``setAlertSubscription`` or ``myAlertSubscriptions``.
        """
        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "authentication required")

        sub = UserAlertSubscription.objects.filter(
            guid=input.id,
            registered_app__organization_id=tenant.organization_id,
            user_id=tenant.actor_user_id,
            deleted_at__isnull=True,
        ).first()
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "subscription not found")

        snapshot = user_alert_subscription_to_type(sub)
        sub.soft_delete()
        return gql_success(snapshot)
