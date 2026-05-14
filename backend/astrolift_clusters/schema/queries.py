from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_clusters.models import (
    ManagedDomain,
    ProviderPlugin,
    TenantCluster,
)
from astrolift_clusters.schema.types import (
    ManagedDomainType,
    ProviderPluginType,
    TenantClusterType,
    cluster_to_type,
    domain_to_type,
    plugin_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class ClustersQuery:
    @strawberry.field
    @require_permission(Permission.CLUSTER_REGISTER)
    @tenant_scoped()
    def astrolift_clusters(self, info: Info) -> list[TenantClusterType]:
        qs = TenantCluster.objects.select_related("organization", "provider_plugin").order_by("slug")[:200]
        return [cluster_to_type(c) for c in qs]

    @strawberry.field
    @require_permission(Permission.APP_CREATE)
    @tenant_scoped()
    def astrolift_cluster_count(self, info: Info) -> int:
        """Count of active clusters bound to the caller's org.

        Used by the /apps/new wizard to gate Step 1: registering an
        app with zero connected clusters is meaningless (the deploy
        has nowhere to land). Scoped to ``APP_CREATE`` rather than
        ``CLUSTER_REGISTER`` because the natural caller is the app
        author, not the cluster operator — they need a shippable
        preflight signal even when they can't register clusters
        themselves. Soft-deleted and inactive clusters are excluded;
        only rows that can actually accept a deploy count.
        """
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return 0
        return TenantCluster.objects.filter(
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
            is_active=True,
        ).count()

    @strawberry.field
    @require_permission(Permission.PROVIDER_PLUGIN_READ)
    @tenant_scoped()
    def astrolift_managed_domains(self, info: Info) -> list[ManagedDomainType]:
        qs = ManagedDomain.objects.select_related("organization").order_by("zone")[:200]
        return [domain_to_type(d) for d in qs]

    @strawberry.field
    @require_permission(Permission.PROVIDER_PLUGIN_READ)
    @tenant_scoped()
    def astrolift_provider_plugins(self, info: Info) -> list[ProviderPluginType]:
        qs = ProviderPlugin.objects.order_by("slug")[:100]
        return [plugin_to_type(p) for p in qs]
