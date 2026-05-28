from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftClusterBootstrapRun")
class ClusterBootstrapRunType:
    """One ``astro cluster bootstrap`` invocation against the cluster
    (#319). Recorded by the CLI via ``recordClusterBootstrapRun``."""

    id: GUID
    status: str  # "succeeded" | "failed"
    chart_version: str
    installed_releases: JSON
    cli_version: str
    host_info: JSON
    error_message: str
    started_at: dt.datetime
    ended_at: dt.datetime
    triggered_by_username: str | None


@strawberry.type(name="AstroliftTenantCluster")
class TenantClusterType:
    id: GUID
    slug: str
    name: str
    organization_slug: str | None
    provider_plugin_slug: str
    region: str
    endpoint: str
    auth_method: str
    ingress_class: str
    is_active: bool
    capabilities: JSON
    capabilities_probed_at: dt.datetime | None
    created_at: dt.datetime
    lifecycle: str
    last_management_error: str
    managed_at: dt.datetime | None
    secrets_backend_provisioned_at: dt.datetime | None

    @strawberry.field
    def last_bootstrap_run(self) -> ClusterBootstrapRunType | None:
        """Most recent ``astro cluster bootstrap`` invocation for this
        cluster, or ``None`` if the CLI has never reported one. Used
        by the cluster detail page's "Last bootstrap" card (#319)."""
        # Local import to keep this module free of model imports at
        # parse time — the rest of the module is pure type wiring.
        from astrolift_clusters.models import (
            ClusterBootstrapRun,
            TenantCluster,
        )

        cluster = TenantCluster.objects.filter(guid=str(self.id)).first()
        if cluster is None:
            return None
        run = (
            ClusterBootstrapRun.objects.filter(tenant_cluster=cluster)
            .select_related("triggered_by")
            .order_by("-ended_at")
            .first()
        )
        if run is None:
            return None
        return bootstrap_run_to_type(run)

    @strawberry.field
    def bootstrap_runs(self, limit: int = 10) -> list[ClusterBootstrapRunType]:
        """History of bootstrap runs against this cluster, newest first
        (#319). Bounded to ``limit`` rows (default 10, max 100) so the
        cluster detail card stays predictable."""
        from astrolift_clusters.models import (
            ClusterBootstrapRun,
            TenantCluster,
        )

        capped = max(1, min(int(limit or 10), 100))
        cluster = TenantCluster.objects.filter(guid=str(self.id)).first()
        if cluster is None:
            return []
        qs = (
            ClusterBootstrapRun.objects.filter(tenant_cluster=cluster)
            .select_related("triggered_by")
            .order_by("-ended_at")[:capped]
        )
        return [bootstrap_run_to_type(r) for r in qs]


@strawberry.type(name="AstroliftManagedDomain")
class ManagedDomainType:
    id: GUID
    zone: str
    organization_slug: str | None
    dns_driver: str
    default_for: str
    is_wildcard_managed: bool
    created_at: dt.datetime


@strawberry.type(name="AstroliftProviderPlugin")
class ProviderPluginType:
    id: GUID
    slug: str
    name: str
    version: str
    capabilities_manifest: JSON
    is_enabled: bool


def cluster_to_type(cluster) -> TenantClusterType:
    return TenantClusterType(
        id=GUID(str(cluster.guid)),
        slug=cluster.slug,
        name=cluster.name,
        organization_slug=cluster.organization.slug if cluster.organization_id else None,
        provider_plugin_slug=cluster.provider_plugin.slug,
        region=cluster.region or "",
        endpoint=cluster.endpoint or "",
        auth_method=cluster.auth_method,
        ingress_class=cluster.ingress_class,
        is_active=cluster.is_active,
        capabilities=cluster.capabilities or {},
        capabilities_probed_at=cluster.capabilities_probed_at,
        created_at=cluster.created_at,
        lifecycle=cluster.lifecycle,
        last_management_error=cluster.last_management_error or "",
        managed_at=cluster.managed_at,
        secrets_backend_provisioned_at=cluster.secrets_backend_provisioned_at,
    )


def domain_to_type(domain) -> ManagedDomainType:
    return ManagedDomainType(
        id=GUID(str(domain.guid)),
        zone=domain.zone,
        organization_slug=domain.organization.slug if domain.organization_id else None,
        dns_driver=domain.dns_driver,
        default_for=domain.default_for,
        is_wildcard_managed=domain.is_wildcard_managed,
        created_at=domain.created_at,
    )


def bootstrap_run_to_type(run) -> ClusterBootstrapRunType:
    return ClusterBootstrapRunType(
        id=GUID(str(run.guid)),
        status=run.status,
        chart_version=run.chart_version or "",
        installed_releases=run.installed_releases or [],
        cli_version=run.cli_version or "",
        host_info=run.host_info or {},
        error_message=run.error_message or "",
        started_at=run.started_at,
        ended_at=run.ended_at,
        triggered_by_username=(run.triggered_by.username if run.triggered_by_id else None),
    )


def plugin_to_type(plugin) -> ProviderPluginType:
    return ProviderPluginType(
        id=GUID(str(plugin.guid)),
        slug=plugin.slug,
        name=plugin.name,
        version=plugin.version,
        capabilities_manifest=plugin.capabilities_manifest or {},
        is_enabled=plugin.is_enabled,
    )


# ---- Bootstrap plan (driver-recipe surface for the cluster page) ---


@strawberry.type(name="AstroliftClusterBootstrapOptionChoice")
class BootstrapOptionChoiceType:
    """One value + label pair inside a ``BootstrapOptionType.choices``."""

    value: str
    label: str


@strawberry.type(name="AstroliftClusterBootstrapOption")
class BootstrapOptionType:
    """An operator-pickable sub-choice on a BootstrapComponent — e.g.
    ``mode`` on a ``tls_issuer`` component with choices ACM / ACME-LE /
    self-signed."""

    key: str
    label: str
    default: str
    choices: list[BootstrapOptionChoiceType]


@strawberry.type(name="AstroliftClusterBootstrapComponent")
class BootstrapComponentType:
    """One installable prerequisite in the driver's recipe."""

    key: str
    title: str
    default_enabled: bool
    rationale: str
    helm_values: JSON
    requires: list[str]
    options: list[BootstrapOptionType]


@strawberry.type(name="AstroliftClusterBootstrapPlan")
class BootstrapPlanType:
    """Read-only declaration the cluster detail page renders as an
    interactive checklist. The operator picks components + option
    values; the mutation feeds the result to InstallClusterPrereqsWorkflow."""

    cluster_id: GUID
    """The cluster this recipe applies to."""

    provider_plugin_slug: str
    """Provider whose recipe this is — used for "Recipe from aws driver"
    badge in the UI."""

    components: list[BootstrapComponentType]


def _bootstrap_option_to_type(opt) -> BootstrapOptionType:
    return BootstrapOptionType(
        key=opt.key,
        label=opt.label,
        default=opt.default,
        choices=[BootstrapOptionChoiceType(value=value, label=label) for value, label in opt.choices],
    )


def _bootstrap_component_to_type(component) -> BootstrapComponentType:
    return BootstrapComponentType(
        key=component.key,
        title=component.title,
        default_enabled=component.default_enabled,
        rationale=component.rationale,
        helm_values=component.helm_values or {},
        requires=list(component.requires),
        options=[_bootstrap_option_to_type(o) for o in component.options],
    )


def bootstrap_plan_to_type(cluster, components) -> BootstrapPlanType:
    return BootstrapPlanType(
        cluster_id=GUID(str(cluster.guid)),
        provider_plugin_slug=cluster.provider_plugin.slug if cluster.provider_plugin_id else "",
        components=[_bootstrap_component_to_type(c) for c in components],
    )


# ---- Cluster lifecycle audit timeline (#68 slice 2) ---------------


@strawberry.type(name="AstroliftClusterLifecycleAuditEntry")
class ClusterLifecycleAuditEntryType:
    """One row in the cluster's lifecycle timeline — a mutation that
    targeted this cluster, with the operator + outcome attached."""

    operation: str
    """The GraphQL mutation operation name (e.g. ``cluster.bring``)."""

    variables: JSON
    """The mutation's input payload, redacted by the audit middleware
    for any secret-shaped keys."""

    success: bool
    errors: list[str]
    timestamp: dt.datetime
    actor: str | None
    """Username of the operator who fired the mutation; null when the
    mutation was fired by a system / service account or when the user
    row was soft-deleted after the audit landed."""


# ---- Cluster health (#68 slice 1) ---------------------------------


@strawberry.type(name="AstroliftClusterPodPhase")
class PodPhaseSummaryType:
    """Pod-phase rollup row for the Cluster Status tab Live-health
    card. One per (namespace, phase) bucket."""

    namespace: str
    phase: str
    count: int


@strawberry.type(name="AstroliftClusterEvent")
class ClusterEventType:
    """A recent Kubernetes Event surfaced for operator triage.
    Default filter is ``Warning`` events; the resolver's
    ``event_type`` arg can broaden if needed."""

    namespace: str
    name: str
    reason: str
    message: str
    type: str
    count: int
    first_seen: str
    last_seen: str
    involved_object: str


@strawberry.type(name="AstroliftClusterHealth")
class ClusterHealthType:
    """Live-health summary returned by ``astroliftClusterHealth``."""

    cluster_id: GUID
    pods: list[PodPhaseSummaryType]
    events: list[ClusterEventType]


# ---- Recent cluster workflows (#394) -------------------------------


@strawberry.type(name="AstroliftClusterWorkflowRun")
class ClusterWorkflowRunType:
    """One row in the Recent workflows card on the Status tab.
    Sourced from Temporal's visibility API filtered to workflow ids
    that reference this cluster's guid."""

    workflow_id: str
    workflow_type: str
    status: str
    """RUNNING / COMPLETED / FAILED / CANCELED / TERMINATED / etc.
    Mirrored verbatim from Temporal's WorkflowExecutionStatus enum."""

    started_at: str
    closed_at: str
    """Empty when the workflow is still RUNNING."""

    run_id: str


# ---- Cluster workload health (#362) -------------------------------


@strawberry.type(name="AstroliftClusterPrometheusMetrics")
class ClusterPrometheusMetricsType:
    """Prometheus-sourced cluster saturation metrics for the Status tab
    Metrics card (#771). All fields are None when Prometheus is
    unavailable or not configured."""

    available: bool
    reason: str | None
    """Why unavailable: 'no_endpoint' | 'unreachable' | None when ok."""

    node_count: int | None
    pod_running_ratio: float | None
    cpu_utilization: float | None
    memory_utilization: float | None
    deployment_ready_ratio: float | None


@strawberry.type(name="AstroliftClusterWorkloadHealth")
class ClusterWorkloadHealthType:
    """Per-Deployment health row for the Status tab's Workload health
    card (#362). One row per Deployment in the operator-facing
    namespace set; the card sorts by readiness deficit (most-broken
    first) and surfaces restarts as a triage signal."""

    namespace: str
    workload_name: str
    desired_replicas: int
    ready_replicas: int
    restart_count_24h: int
    """Sum of container restart counts across pods owned by this
    Deployment whose last termination fell inside the trailing 24h
    window. Clients without termination-timestamp data contribute
    their full running counter (best-effort)."""

    last_image_deployed_at: str
    """RFC3339 timestamp of the Deployment's last completed rollout
    (Progressing condition with reason NewReplicaSetAvailable).
    Empty string when the Deployment has never rolled or when the
    condition isn't populated by the apiserver."""


# ---- Prometheus range metrics / sparkline charts (#772 charts) -----


@strawberry.type(name="AstroliftClusterPrometheusRangePoint")
class ClusterPrometheusRangePointType:
    """A single (unix timestamp, value) data point in a range series."""

    ts: float
    value: float


@strawberry.type(name="AstroliftClusterPrometheusRangeSeries")
class ClusterPrometheusRangeSeriesType:
    """One named golden-signal time series from a Prometheus range query."""

    metric: str
    """Machine key — 'node_count' | 'pod_running_ratio' | 'cpu_utilization'
    | 'memory_utilization' | 'deployment_ready_ratio'."""

    label: str
    """Human-readable display label for the chart card header."""

    unit: str
    """'count' | 'ratio' — how the UI should format the value."""

    current: float | None
    """Last point's value; None when Prometheus returned no data."""

    points: list[ClusterPrometheusRangePointType]


@strawberry.type(name="AstroliftClusterPrometheusRangeMetrics")
class ClusterPrometheusRangeMetricsType:
    """Range-query (historical) Prometheus metrics for the Status tab
    sparkline charts. All five golden signals share one query call;
    ``series`` is empty and ``available=False`` when Prometheus is
    unconfigured or unreachable."""

    available: bool
    reason: str | None
    """'no_endpoint' | 'unreachable' | None when ok."""

    range_seconds: int
    step_seconds: int
    series: list[ClusterPrometheusRangeSeriesType]
