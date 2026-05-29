from __future__ import annotations

import logging

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
    ClusterPrometheusMetricsType,
    ClusterPrometheusRangeMetricsType,
    ClusterPrometheusRangePointType,
    ClusterPrometheusRangeSeriesType,
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

log = logging.getLogger(__name__)


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
    def astrolift_provider_plugins(self, info: Info) -> list[ProviderPluginType]:
        # Driver reference is platform-level public info — surfaced on
        # /resources/drivers to any authed operator so they can see what
        # the install's capability surface looks like. No permission
        # gate (everyone needs to see what's supported); no @tenant_scoped
        # because ProviderPlugin is a platform-level resource, not
        # tenant-scoped — tenant_scoped() filtered it to nothing.
        user = getattr(info.context.request, "user", None)
        if not user or not user.is_authenticated:
            return []
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

        # Operations we care about for cluster lifecycle, matched either
        # by dot-notation prefix (the ``@mutation_audit`` ``action`` value
        # the extension records when the thread-local is populated) OR
        # by raw GraphQL operation name (what the extension falls back
        # to when ``_mutation_action_local.action`` isn't set on this
        # thread — e.g. a sibling code path bypasses the decorator
        # chain, or the wrapper short-circuits before the assignment).
        # Matching both means a regression on either path still surfaces
        # the timeline rather than silently emptying the Status tab card.
        prefixes = (
            "cluster.",
            "managed_domain.",
            "provider_plugin.",
        )
        # GraphQL operation names (PascalCase) for the same mutations.
        # Synchronised with the ``@mutation_audit`` annotations on
        # ``ClustersMutation`` — keep these in lockstep when a new
        # cluster / domain / provider mutation lands.
        operation_names = frozenset(
            (
                "RegisterTenantCluster",
                "UpdateTenantCluster",
                "UnregisterTenantCluster",
                "BringClusterIntoManagement",
                "RefreshClusterManagement",
                "DecommissionCluster",
                "InstallClusterPrereqs",
                "RecordClusterBootstrapRun",
                "CreateManagedDomain",
                "UpdateManagedDomain",
                "SoftDeleteManagedDomain",
                "ConfigureProviderPlugin",
            ),
        )
        qs = MutationAuditLog.objects.select_related("user").order_by("-timestamp")
        cluster_guid = str(cluster.guid)
        cluster_slug = cluster.slug
        out: list[ClusterLifecycleAuditEntryType] = []
        for log in qs.iterator(chunk_size=200):
            if not (any(log.operation.startswith(p) for p in prefixes) or log.operation in operation_names):
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
    def astrolift_cluster_prometheus_metrics(
        self,
        info: Info,
        cluster_id: GUID,
    ) -> ClusterPrometheusMetricsType:
        """Prometheus-sourced cluster saturation metrics for the Status
        tab Metrics card (#771).

        Reads five instant PromQL queries against the cluster's
        configured Prometheus endpoint (``provider_config
        ['prometheus_endpoint']``). Returns ``available=False`` with a
        ``reason`` string when no endpoint is configured or the endpoint
        is unreachable — the UI degrades gracefully in both cases.

        Queries are cached 30s by the existing prometheus_client TTL
        cache so repeated tab opens don't hammer Prometheus.
        """
        from astrolift_operations.prometheus_client import (
            PrometheusError,
            query_instant,
        )

        _unavailable = ClusterPrometheusMetricsType(
            available=False,
            reason=None,
            node_count=None,
            pod_running_ratio=None,
            cpu_utilization=None,
            memory_utilization=None,
            deployment_ready_ratio=None,
        )

        cluster = TenantCluster.objects.filter(
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return _unavailable

        pc = cluster.provider_config or {}
        endpoint = (pc.get("prometheus_endpoint") or "").strip()
        if not endpoint:
            # Fall back to probe-discovered endpoint. Auto-populated when
            # the capability probe runs (bringClusterIntoManagement /
            # refreshClusterManagement). No operator config required.
            caps = cluster.capabilities or {}
            endpoint = (caps.get("prometheus_endpoint") or "").strip()
        if not endpoint:
            log.warning(
                "prometheus_metrics: no endpoint for cluster %s (provider_config=%s caps=%s)",
                cluster_id,
                bool(pc.get("prometheus_endpoint")),
                bool((cluster.capabilities or {}).get("prometheus_endpoint")),
            )
            return ClusterPrometheusMetricsType(
                available=False,
                reason="no_endpoint",
                node_count=None,
                pod_running_ratio=None,
                cpu_utilization=None,
                memory_utilization=None,
                deployment_ready_ratio=None,
            )

        log.info("prometheus_metrics: cluster=%s endpoint=%s", cluster_id, endpoint)
        try:
            node_count = int(query_instant(endpoint=endpoint, query="count(kube_node_info)"))
            pod_running_ratio = query_instant(
                endpoint=endpoint,
                query='sum(kube_pod_status_phase{phase="Running"}) / sum(kube_pod_status_phase)',
            )
            cpu_utilization = query_instant(
                endpoint=endpoint,
                query='sum(kube_pod_container_resource_requests{resource="cpu"}) / sum(kube_node_status_allocatable{resource="cpu"})',
            )
            memory_utilization = query_instant(
                endpoint=endpoint,
                query='sum(kube_pod_container_resource_requests{resource="memory"}) / sum(kube_node_status_allocatable{resource="memory"})',
            )
            deployment_ready_ratio = query_instant(
                endpoint=endpoint,
                query="sum(kube_deployment_status_replicas_ready) / sum(kube_deployment_spec_replicas)",
            )
        except PrometheusError as exc:
            log.warning(
                "prometheus_metrics: unreachable cluster=%s endpoint=%s err=%s",
                cluster_id,
                endpoint,
                exc,
            )
            return ClusterPrometheusMetricsType(
                available=False,
                reason="unreachable",
                node_count=None,
                pod_running_ratio=None,
                cpu_utilization=None,
                memory_utilization=None,
                deployment_ready_ratio=None,
            )

        return ClusterPrometheusMetricsType(
            available=True,
            reason=None,
            node_count=node_count,
            pod_running_ratio=pod_running_ratio,
            cpu_utilization=cpu_utilization,
            memory_utilization=memory_utilization,
            deployment_ready_ratio=deployment_ready_ratio,
        )

    @strawberry.field
    @require_permission(Permission.CLUSTER_REGISTER)
    @tenant_scoped()
    def astrolift_cluster_prometheus_range_metrics(
        self,
        info: Info,
        cluster_id: GUID,
        range_seconds: int = 3600,
        step_seconds: int = 60,
    ) -> ClusterPrometheusRangeMetricsType:
        """Prometheus range queries for the Status tab sparkline charts.

        Returns one ``ClusterPrometheusRangeSeriesType`` per golden
        signal (node count, pod running ratio, CPU / memory
        utilization, deployment ready ratio, apiserver p99 latency,
        network receive rate, container restart rate). Each series
        carries a dense point array at ``step_seconds`` resolution
        over the trailing ``range_seconds`` window.

        Endpoint resolution mirrors ``astroliftClusterPrometheusMetrics``:
        ``provider_config['prometheus_endpoint']`` → capability-probe
        fallback → ``no_endpoint``.

        Inputs are clamped (range: 5m–30d; step: 15s–3600s) so a rogue
        caller can't DOS Prometheus with an absurdly fine step.
        """
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from astrolift_operations.prometheus_client import (
            PrometheusError,
            query_range,
        )

        cluster = TenantCluster.objects.filter(
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return ClusterPrometheusRangeMetricsType(
                available=False,
                reason=None,
                range_seconds=range_seconds,
                step_seconds=step_seconds,
                series=[],
            )

        pc = cluster.provider_config or {}
        endpoint = (pc.get("prometheus_endpoint") or "").strip()
        if not endpoint:
            caps = cluster.capabilities or {}
            endpoint = (caps.get("prometheus_endpoint") or "").strip()
        if not endpoint:
            log.warning(
                "prometheus_range_metrics: no endpoint for cluster %s",
                cluster_id,
            )
            return ClusterPrometheusRangeMetricsType(
                available=False,
                reason="no_endpoint",
                range_seconds=range_seconds,
                step_seconds=step_seconds,
                series=[],
            )

        log.info(
            "prometheus_range_metrics: cluster=%s endpoint=%s range=%ss step=%ss",
            cluster_id,
            endpoint,
            range_seconds,
            step_seconds,
        )
        # Clamp: range 5m–7d, step 15s–3600s.
        range_seconds = max(300, min(int(range_seconds), 30 * 86400))
        step_seconds = max(15, min(int(step_seconds), 3600))

        end_unix = int(_time.time())
        start_unix = end_unix - range_seconds

        _METRICS = [
            {
                "metric": "node_count",
                "label": "Nodes",
                "unit": "count",
                "query": "count(kube_node_info)",
            },
            {
                "metric": "pod_running_ratio",
                "label": "Pods running",
                "unit": "ratio",
                "query": 'sum(kube_pod_status_phase{phase="Running"}) / sum(kube_pod_status_phase)',
            },
            {
                "metric": "cpu_utilization",
                "label": "CPU utilization",
                "unit": "ratio",
                "query": 'sum(kube_pod_container_resource_requests{resource="cpu"}) / sum(kube_node_status_allocatable{resource="cpu"})',
            },
            {
                "metric": "memory_utilization",
                "label": "Memory utilization",
                "unit": "ratio",
                "query": 'sum(kube_pod_container_resource_requests{resource="memory"}) / sum(kube_node_status_allocatable{resource="memory"})',
            },
            {
                "metric": "deployment_ready_ratio",
                "label": "Deployments ready",
                "unit": "ratio",
                "query": "sum(kube_deployment_status_replicas_ready) / sum(kube_deployment_spec_replicas)",
            },
            {
                "metric": "latency_p99",
                "label": "Apiserver p99",
                "unit": "seconds",
                "query": 'histogram_quantile(0.99, sum(rate(apiserver_request_duration_seconds_bucket{verb!~"WATCH|WATCHLIST|LIST|PROXY|CONNECT"}[5m])) by (le))',
            },
            {
                "metric": "network_rx",
                "label": "Network receive",
                "unit": "bytes_per_sec",
                "query": "sum(rate(container_network_receive_bytes_total[5m]))",
            },
            {
                "metric": "restart_rate",
                "label": "Restarts / min",
                "unit": "count",
                "query": "sum(rate(kube_pod_container_status_restarts_total[5m])) * 60",
            },
        ]

        _PROM_ERROR = object()  # sentinel: query failed with PrometheusError

        def _fetch_series(m: dict) -> ClusterPrometheusRangeSeriesType | object:
            """Fetch one golden-signal range series. Runs in a thread pool
            so all eight queries execute concurrently — worst-case latency
            is one timeout (10s) rather than eight in sequence (80s).
            Returns _PROM_ERROR sentinel on PrometheusError so the caller
            can distinguish "endpoint unreachable" from "no data yet"."""
            try:
                rows = query_range(
                    endpoint=endpoint,
                    query=m["query"],
                    start_unix=start_unix,
                    end_unix=end_unix,
                    step_seconds=step_seconds,
                    timeout=10.0,
                )
            except PrometheusError:
                return _PROM_ERROR
            ts_map: dict[float, float] = {}
            for row in rows:
                for ts, val in row.values:
                    ts_map[ts] = ts_map.get(ts, 0.0) + val
            points = [ClusterPrometheusRangePointType(ts=ts, value=val) for ts, val in sorted(ts_map.items())]
            current_val: float | None = points[-1].value if points else None
            return ClusterPrometheusRangeSeriesType(
                metric=m["metric"],
                label=m["label"],
                unit=m["unit"],
                current=current_val,
                points=points,
            )

        # Run all eight queries concurrently — worst-case latency is one
        # timeout (10s) rather than eight in sequence (80s).
        raw_results: list[ClusterPrometheusRangeSeriesType | object] = [None] * len(_METRICS)  # type: ignore[list-item]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_series, m): i for i, m in enumerate(_METRICS)}
            for fut in as_completed(futures):
                raw_results[futures[fut]] = fut.result()

        # If every query errored, Prometheus is unreachable — report
        # available=False so the UI shows the correct error state rather
        # than "No data in window" for each card.
        errors = [r for r in raw_results if r is _PROM_ERROR]
        if len(errors) == len(_METRICS):
            log.warning(
                "prometheus_range_metrics: all queries failed — unreachable cluster=%s endpoint=%s",
                cluster_id,
                endpoint,
            )
            return ClusterPrometheusRangeMetricsType(
                available=False,
                reason="unreachable",
                range_seconds=range_seconds,
                step_seconds=step_seconds,
                series=[],
            )

        # Partial failures: return empty series for the failed metrics so
        # the available cards still render.
        series = [
            r
            if r is not _PROM_ERROR
            else ClusterPrometheusRangeSeriesType(
                metric=_METRICS[i]["metric"],
                label=_METRICS[i]["label"],
                unit=_METRICS[i]["unit"],
                current=None,
                points=[],
            )
            for i, r in enumerate(raw_results)
        ]

        return ClusterPrometheusRangeMetricsType(
            available=True,
            reason=None,
            range_seconds=range_seconds,
            step_seconds=step_seconds,
            series=series,  # type: ignore[arg-type]
        )

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
