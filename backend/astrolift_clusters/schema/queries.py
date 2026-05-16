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
    ClusterEventType,
    ClusterHealthType,
    ClusterLifecycleAuditEntryType,
    ClusterWorkflowRunType,
    ClusterWorkloadHealthType,
    ManagedDomainType,
    PodPhaseSummaryType,
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
    def astrolift_cluster_lifecycle_audit(
        self,
        info: Info,
        cluster_id: GUID,
        limit: int = 50,
    ) -> list[ClusterLifecycleAuditEntryType]:
        """Cluster-scoped slice of the mutation audit log (#68 slice 2).

        Filters ``MutationAuditLog`` by cluster-targeted operations
        whose ``variables`` JSON references this cluster's guid. The
        resolver surfaces a flat list of "what happened to this
        cluster, in what order, by whom" — the workhorse for the
        cluster-detail Status tab's lifecycle timeline card.
        """
        from core.schema.audit import MutationAuditLog

        cluster = TenantCluster.objects.filter(
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return []

        # Operations we care about for cluster lifecycle:
        # - registerTenantCluster / updateTenantCluster / unregister
        # - bringClusterIntoManagement / refreshClusterManagement
        # - decommissionCluster
        # - installClusterPrereqs
        # - configureProviderPlugin (when the plugin in question is bound)
        prefixes = (
            "cluster.",
            "managed_domain.",
            "provider_plugin.",
        )
        qs = MutationAuditLog.objects.select_related("user").order_by("-timestamp")
        cluster_guid = str(cluster.guid)
        cluster_slug = cluster.slug
        out: list[ClusterLifecycleAuditEntryType] = []
        for log in qs.iterator(chunk_size=200):
            if not any(log.operation.startswith(p) for p in prefixes):
                continue
            # JSON-references via either guid or slug match. Stringify
            # variables once and substring-match — cheap, no JSON-path
            # required on the DB side.
            variables_str = str(log.variables) if log.variables else ""
            if cluster_guid not in variables_str and cluster_slug not in variables_str:
                continue
            out.append(
                ClusterLifecycleAuditEntryType(
                    operation=log.operation,
                    variables=log.variables,
                    success=log.success,
                    errors=log.errors,
                    timestamp=log.timestamp,
                    actor=(log.user.username if log.user else None),
                ),
            )
            if len(out) >= limit:
                break
        return out

    @strawberry.field
    @require_permission(Permission.CLUSTER_REGISTER)
    @tenant_scoped()
    def astrolift_recent_cluster_workflows(
        self,
        info: Info,
        cluster_id: GUID,
        limit: int = 10,
    ) -> list[ClusterWorkflowRunType]:
        """Recent Temporal workflow runs targeting ``cluster_id`` (#394).

        Pulled live from Temporal's visibility API; doesn't duplicate
        state into a local table. Empty when Temporal is disabled or
        when the visibility query fails — the UI's empty-state copy
        is identical to "no runs yet" in either case.
        """
        from astrolift_workflows.client import list_workflows_for_cluster

        cluster = TenantCluster.objects.filter(
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return []
        rows = list_workflows_for_cluster(str(cluster.guid), limit=limit)
        return [
            ClusterWorkflowRunType(
                workflow_id=r.get("workflow_id", ""),
                workflow_type=r.get("workflow_type", ""),
                status=r.get("status", "UNKNOWN"),
                started_at=r.get("started_at", ""),
                closed_at=r.get("closed_at", ""),
                run_id=r.get("run_id", ""),
            )
            for r in rows
        ]

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
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return 0
        default_bound = RegisteredApp.objects.filter(
            default_tenant_cluster=cluster,
            deleted_at__isnull=True,
        ).values_list("pk", flat=True)
        env_bound = AppEnvironment.objects.filter(
            tenant_cluster=cluster,
            deleted_at__isnull=True,
        ).values_list("registered_app_id", flat=True)
        return len(set(default_bound) | set(env_bound))

    @strawberry.field
    @require_permission(Permission.CLUSTER_REGISTER)
    @tenant_scoped()
    def astrolift_cluster_health(
        self,
        info: Info,
        cluster_id: GUID,
        event_limit: int = 50,
    ) -> ClusterHealthType | None:
        """Driver-backed pod-phase rollup + recent K8s Warning events
        for ``cluster_id`` (#68 slice 1).

        Calls into the driver's ``list_pod_phase_summary`` +
        ``list_events`` methods. Defaults to the ``astrolift-system``
        namespace; broader scoping happens once the workflow layer
        knows which app namespaces are bound.

        Returns ``None`` when the cluster row is missing; returns a
        summary with empty pods/events lists when the driver can't
        reach the apiserver (no creds, unreachable). The UI surfaces
        that case as "no health data available" without erroring.
        """
        from core.cluster_management import (
            ClusterManagementError,
            cluster_health_dispatch,
        )

        cluster = (
            TenantCluster.objects.filter(
                guid=str(cluster_id),
                deleted_at__isnull=True,
            )
            .select_related("provider_plugin")
            .first()
        )
        if cluster is None:
            return None
        try:
            payload = cluster_health_dispatch(
                cluster=cluster,
                event_limit=event_limit,
            )
        except ClusterManagementError:
            payload = {"pods": [], "events": []}
        return ClusterHealthType(
            cluster_id=GUID(str(cluster.guid)),
            pods=[
                PodPhaseSummaryType(
                    namespace=p["namespace"],
                    phase=p["phase"],
                    count=int(p["count"]),
                )
                for p in payload["pods"]
            ],
            events=[
                ClusterEventType(
                    namespace=e["namespace"],
                    name=e["name"],
                    reason=e["reason"],
                    message=e["message"],
                    type=e["type"],
                    count=int(e["count"]),
                    first_seen=e["first_seen"],
                    last_seen=e["last_seen"],
                    involved_object=e["involved_object"],
                )
                for e in payload["events"]
            ],
        )

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

    @strawberry.field
    @require_permission(Permission.CLUSTER_REGISTER)
    @tenant_scoped()
    def astrolift_cluster_workload_health(
        self,
        info: Info,
        cluster_id: GUID,
    ) -> list[ClusterWorkloadHealthType]:
        """Per-Deployment health rollup for the Status tab (#362).

        The pod-phase card answers "is anything red"; this resolver
        answers "which workload is red" — desired vs ready replicas,
        restart counts in the trailing 24h, last completed rollout
        timestamp. Driver-resolution or driver-call failure yields an
        empty list (no creds / unreachable / plugin missing); the UI
        renders an empty-state card rather than erroring out the
        whole tab. Soft-deleted / missing clusters also yield an
        empty list (defense in depth — the caller's permission gate
        already protects access, but mirroring the cluster_health
        contract keeps the resolver layer symmetric).
        """
        from core.cluster_management import (
            ClusterManagementError,
            cluster_workload_health_dispatch,
        )

        cluster = (
            TenantCluster.objects.filter(
                guid=str(cluster_id),
                deleted_at__isnull=True,
            )
            .select_related("provider_plugin")
            .first()
        )
        if cluster is None:
            return []
        try:
            rows = cluster_workload_health_dispatch(cluster=cluster)
        except ClusterManagementError:
            return []
        return [
            ClusterWorkloadHealthType(
                namespace=row["namespace"],
                workload_name=row["name"],
                desired_replicas=int(row["desired_replicas"]),
                ready_replicas=int(row["ready_replicas"]),
                restart_count_24h=int(row["restart_count_24h"]),
                last_image_deployed_at=row["last_image_deployed_at"],
            )
            for row in rows
        ]
