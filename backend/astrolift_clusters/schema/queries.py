from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_clusters.models import (
    ManagedDomain,
    ProviderPlugin,
    TenantCluster,
)
from astrolift_clusters.schema.types import (
    BootstrapPlanType,
    ManagedDomainType,
    ProviderPluginType,
    TenantClusterType,
    bootstrap_plan_to_type,
    cluster_to_type,
    domain_to_type,
    plugin_to_type,
)
from astrolift_graphql import GUID
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
        """Count of managed clusters bound to the caller's org.

        Used by the /apps/new wizard to gate Step 1: registering an
        app with zero managed clusters is meaningless (the deploy has
        nowhere to land). Scoped to ``APP_CREATE`` rather than
        ``CLUSTER_REGISTER`` because the natural caller is the app
        author, not the cluster operator — they need a shippable
        preflight signal even when they can't register clusters
        themselves. Soft-deleted and inactive clusters are excluded;
        ``lifecycle = "managed"`` is required (#316 — registered rows
        are metadata-only, not deploy targets until the operator has
        brought them into management).
        """
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return 0
        return TenantCluster.objects.filter(
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
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

    @strawberry.field
    @require_permission(Permission.CLUSTER_REGISTER)
    @tenant_scoped()
    def astrolift_app_count_for_cluster(self, info: Info, cluster_id: GUID) -> int:
        """Active apps bound to a specific cluster (#393).

        Counts ``RegisteredApp`` rows whose ``default_tenant_cluster``
        is the cluster OR whose ``AppEnvironment.tenant_cluster``
        targets it (either binding mechanism counts). Tenant-scoped;
        soft-deleted rows excluded.
        """
        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_registry.models import RegisteredApp

        cluster = TenantCluster.objects.filter(
            guid=str(cluster_id), deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return 0
        default_bound = RegisteredApp.objects.filter(
            default_tenant_cluster=cluster, deleted_at__isnull=True,
        ).values_list("pk", flat=True)
        env_bound = AppEnvironment.objects.filter(
            tenant_cluster=cluster, deleted_at__isnull=True,
        ).values_list("registered_app_id", flat=True)
        return len(set(default_bound) | set(env_bound))

    @strawberry.field
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def astrolift_cluster_bootstrap_plan(self, info: Info, cluster_id: GUID) -> BootstrapPlanType | None:
        """Driver-owned bootstrap recipe for ``cluster_id``.

        Static declaration — no cluster API calls; the recipe lives in
        the driver code. Each provider plugin returns an opinionated
        list of components (cert-manager, ingress, external-dns,
        Prometheus, ...) with provider-tuned helm values and
        operator-pickable sub-options. The UI renders the list as an
        interactive checklist and feeds the operator's selections to
        ``installClusterPrereqs``.
        """
        from core.cluster_management import (
            ClusterManagementError,
            bootstrap_components_dispatch,
        )

        cluster = (
            TenantCluster.objects.filter(guid=str(cluster_id), deleted_at__isnull=True)
            .select_related("provider_plugin")
            .first()
        )
        if cluster is None:
            return None
        try:
            components = bootstrap_components_dispatch(cluster=cluster)
        except ClusterManagementError:
            # Driver couldn't be built (plugin missing, config invalid).
            # Return an empty recipe rather than raising — the UI shows
            # the cluster anyway, just without an install checklist.
            components = []
        return bootstrap_plan_to_type(cluster, components)
