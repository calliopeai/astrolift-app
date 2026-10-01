"""Cluster retirement must preserve the transport needed to remove shared models."""


def has_cluster_owned_models(cluster_id: int) -> bool:
    from astrolift_services.models import ManagedService

    return ManagedService.objects.filter(
        tenant_cluster_id=cluster_id,
        organization__isnull=False,
        kind="model_endpoint",
        variant="vllm",
    ).exists()


MODEL_CLEANUP_REQUIRED = "Remove all shared model deployments before retiring this cluster."


def recheck_cluster_authority(cluster, permission):
    """Refresh identity and grants after the final placement lock.

    Cluster mutations remain organization-only; install-shared rows additionally
    require the current platform operator. The locked region is the operation's
    actual ABAC target, never a request-provided or pre-lock region.
    """
    from dataclasses import replace

    from django.contrib.auth import get_user_model
    from django.db.models import Q
    from django.utils import timezone

    from astrolift_clusters.scopes import cluster_catalog_org_scope
    from astrolift_identity import abac
    from astrolift_identity.api_tokens import (
        get_current_api_token,
        reset_current_api_token,
        set_current_api_token,
        with_active_org_member,
    )
    from astrolift_identity.models import ApiToken
    from core.permissions import PermissionDenied, check_permission, check_platform_operator
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    actor_id = tenant.actor_user_id if tenant else None
    user = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    if user is None:
        raise PermissionDenied(permission, None, "Authentication required")
    token = get_current_api_token()
    marker = None
    try:
        if token is not None:
            active = with_active_org_member(
                ApiToken.objects.filter(pk=token.pk, is_revoked=False, deleted_at__isnull=True).filter(
                    Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
                ),
                user="user",
                organization="organization",
            ).first()
            if (
                active is None
                or active.user_id != actor_id
                or active.organization_id != tenant.organization_id
            ):
                raise PermissionDenied(permission, None, "Authentication required")
            marker = set_current_api_token(active)
        attrs = replace(
            abac.attributes_for(actor_id),
            cache={},
            environment=None,
            region=cluster.region or None,
            approvals=0,
            now=timezone.now(),
        )
        with abac.request_attributes(attrs):
            check_permission(permission, scope=cluster_catalog_org_scope(permission)({}))
            if cluster.organization_id is None:
                check_platform_operator(user, gate=permission)
    finally:
        if marker is not None:
            reset_current_api_token(marker)
