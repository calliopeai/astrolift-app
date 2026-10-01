"""Verified organization-owned model placement, independent of app/project owners.

These helpers only resolve coherent live rows. Callers must still check their
specific permission before transport, credential access or infrastructure work.
"""

from __future__ import annotations

import re
from uuid import UUID

from django.db.models import Q

from astrolift_clusters.models import TenantCluster
from astrolift_clusters.scopes import cluster_catalog_org_scope
from core.permissions import Permission
from core.tenancy import get_current_tenant

MODEL_ALIAS = re.compile(r"[a-z][a-z0-9_]{0,31}\Z", re.ASCII)


def available_model_clusters(qs, organization_id: int | None):
    """Use the existing managed/active org-or-install-shared placement contract."""
    if not organization_id:
        return qs.none()
    return qs.filter(
        Q(organization_id=organization_id) | Q(organization_id__isnull=True),
        deleted_at__isnull=True,
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED,
        provider_plugin__deleted_at__isnull=True,
        provider_plugin__is_enabled=True,
    )


def live_cluster_models(qs, organization_id: int | None):
    if not organization_id:
        return qs.none()
    return qs.filter(
        organization_id=organization_id,
        organization__deleted_at__isnull=True,
        deleted_at__isnull=True,
        kind="model_endpoint",
        variant="vllm",
        registered_app__isnull=True,
        app_environment__isnull=True,
        project__isnull=True,
        tenant_cluster__deleted_at__isnull=True,
        tenant_cluster__provider_plugin__deleted_at__isnull=True,
    ).filter(
        Q(tenant_cluster__organization_id=organization_id) | Q(tenant_cluster__organization_id__isnull=True),
    )


def live_cluster_model_by_guid(guid, *, organization_id: int | None = None):
    from astrolift_services.models import ManagedService

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    # An explicit organization must agree with the active request context.
    if organization_id is not None and organization_id != org_id:
        return None
    try:
        parsed = UUID(str(guid))
    except (ValueError, TypeError, AttributeError):
        return None
    return (
        live_cluster_models(ManagedService.objects.filter(guid=parsed), org_id)
        .select_related("organization", "tenant_cluster", "tenant_cluster__provider_plugin")
        .first()
    )


def cluster_model_org_scope(permission: Permission):
    """Explicit ORG permission scope and the cluster credential team ceiling."""
    return cluster_catalog_org_scope(permission)


def model_binding_prefix(alias: str) -> str:
    """New subscriptions always use an explicit named, collision-safe binding."""
    if not isinstance(alias, str) or not MODEL_ALIAS.fullmatch(alias):
        raise ValueError("Model subscription alias must match [a-z][a-z0-9_]{0,31}.")
    return f"MODEL_{alias.upper()}_"
