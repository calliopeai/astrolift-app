"""Models navigation reflects actual owner gates and legacy app visibility."""

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken, Member, Organization
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import live_app_owners
from astrolift_registry.visibility import visible_registry_apps
from core.permissions import (
    ModuleEntitlement,
    Permission,
    PermissionDenied,
    check_permission,
    check_permission_any_scope,
)
from core.tenancy import get_current_tenant


def models_entitlement(info) -> ModuleEntitlement:
    """Advisory only: every read/write still checks its concrete target."""
    dark = ModuleEntitlement("models", False, False, False, False, True)
    tenant = get_current_tenant()
    request_user = getattr(getattr(info.context, "request", None), "user", None)
    if (
        tenant is None
        or request_user is None
        or not getattr(request_user, "is_authenticated", False)
        or request_user.pk != tenant.actor_user_id
        or not Organization.objects.filter(pk=tenant.organization_id, deleted_at__isnull=True).exists()
    ):
        return dark
    user = get_user_model().objects.filter(pk=request_user.pk, is_active=True).first()
    if user is None:
        return dark
    if (
        not user.is_superuser
        and not Member.objects.filter(
            user_id=user.pk,
            scope_kind="ORG",
            scope_id=tenant.organization_id,
            is_active=True,
            deleted_at__isnull=True,
        ).exists()
    ):
        return dark
    token = get_current_api_token()
    marker = None
    if token is not None:
        fresh = with_active_org_member(
            ApiToken.objects.filter(
                pk=token.pk,
                user_id=user.pk,
                organization_id=tenant.organization_id,
                is_revoked=False,
                deleted_at__isnull=True,
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())),
            user="user",
            organization="organization",
        ).first()
        if fresh is None:
            return dark
        marker = set_current_api_token(fresh)
    try:

        def owner_allowed(permission):
            try:
                check_permission(permission, scope=cluster_catalog_org_scope(permission)({}))
            except PermissionDenied:
                return False
            return True

        can_view = owner_allowed(Permission.ORG_READ)
        if not can_view:
            try:
                check_permission_any_scope(Permission.APP_READ)
                can_view = visible_registry_apps(
                    live_app_owners(RegisteredApp.objects.all()),
                    Permission.APP_READ,
                ).exists()
            except PermissionDenied:
                pass
        owner_write = owner_allowed(Permission.CLUSTER_UPDATE)
        return ModuleEntitlement("models", can_view, owner_write, owner_write, owner_write, True)
    finally:
        if marker is not None:
            reset_current_api_token(marker)
