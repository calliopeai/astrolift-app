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


@strawberry.type
class ClustersQuery:
    @strawberry.field
    @require_permission(Permission.CLUSTER_REGISTER)
    @tenant_scoped()
    def astrolift_clusters(self, info: Info) -> list[TenantClusterType]:
        qs = TenantCluster.objects.select_related("organization", "provider_plugin").order_by("slug")[:200]
        return [cluster_to_type(c) for c in qs]

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
