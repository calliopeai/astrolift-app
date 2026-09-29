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
    ingress_mode: str
    alb_auth_config: JSON | None
    oidc_auth_config: JSON | None
    is_active: bool
    capabilities: JSON
    capabilities_probed_at: dt.datetime | None
    created_at: dt.datetime
    lifecycle: str
    last_management_error: str
    managed_at: dt.datetime | None
    secrets_backend_provisioned_at: dt.datetime | None

    # ---- Keep-alive agent heartbeat (#808) ----------------------------
    last_heartbeat_at: dt.datetime | None
    heartbeat_interval_seconds: int
    heartbeat_status: str
    """Derived live status — never_seen | connected | degraded | offline.
    Computed from ``last_heartbeat_at`` + ``heartbeat_interval_seconds``
    at query time (see ``heartbeat_status`` policy)."""

    heartbeat_age_seconds: float | None
    """Seconds since the last heartbeat, or null when never seen. Lets
    the UI render 'last seen X ago' without re-deriving from the
    timestamp."""

    agent_provisioned: bool
    """Whether a scoped agent key has been issued for this cluster.
    Surfaced (not the key itself) so the settings UI can show
    issue-vs-rotate affordances."""

    created_by_username: str | None = None
    """Who registered the cluster (#2150), the list's Registered by column
    and what its Mine view matches. Null when registered before the
    platform recorded it, by the CLI with no user, and on a shared
    cluster: that row belongs to the platform, not to anyone in the
    viewer's org."""

    @strawberry.field
    def last_bootstrap_run(self) -> ClusterBootstrapRunType | None:
        """Most recent ``astro cluster bootstrap`` invocation for this
        cluster, or ``None`` if the CLI has never reported one. Used
        by the cluster detail page's "Last bootstrap" card (#319)."""
        # Local import to keep this module free of model imports at
        # parse time — the rest of the module is pure type wiring.
        from django.db.models import Q

        from astrolift_clusters.models import (
            ClusterBootstrapRun,
            TenantCluster,
        )
        from core.tenancy import get_current_tenant

        # Re-scope by caller org (#1183). This field inherits the parent
        # cluster's scoping, but a leaked parent must not widen access to
        # another org's bootstrap history. The runs are held to the caller's
        # org too (#1955): a shared cluster resolves for every org, and any
        # of them can record a run on it. Not @tenant_scoped, so a None
        # tenant fails closed here; the union and the NULL-org rows would
        # otherwise both match.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return None
        cluster = TenantCluster.objects.filter(
            Q(organization_id=org_id) | Q(organization_id__isnull=True),
            guid=str(self.id),
        ).first()
        if cluster is None:
            return None
        run = (
            ClusterBootstrapRun.objects.filter(tenant_cluster=cluster, organization_id=org_id)
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
        from django.db.models import Q

        from astrolift_clusters.models import (
            ClusterBootstrapRun,
            TenantCluster,
        )
        from core.tenancy import get_current_tenant

        capped = max(1, min(int(limit or 10), 100))
        # Re-scope by caller org (#1183, #1955) — see last_bootstrap_run.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        cluster = TenantCluster.objects.filter(
            Q(organization_id=org_id) | Q(organization_id__isnull=True),
            guid=str(self.id),
        ).first()
        if cluster is None:
            return []
        qs = (
            ClusterBootstrapRun.objects.filter(tenant_cluster=cluster, organization_id=org_id)
            .select_related("triggered_by")
            .order_by("-ended_at")[:capped]
        )
        return [bootstrap_run_to_type(r) for r in qs]


@strawberry.input(name="AstroliftClustersListFilter")
class ClustersListFilterInput:
    """The Clusters list's declared filters (spec 44 §5.1, #2150).

    Unset fields do not filter; set fields combine with AND, and the
    values of one list field with OR.
    """

    provider: list[str] | None = strawberry.field(
        default=None, description="Provider plugin slugs, as providerPluginSlug."
    )
    status: list[str] | None = strawberry.field(
        default=None, description="Management lifecycles, as lifecycle (registered, managing, managed, ...)."
    )
    live: list[str] | None = strawberry.field(
        default=None,
        description="Heartbeat statuses, as heartbeatStatus: never_seen, connected, degraded, offline.",
    )
    registered_by: list[str] | None = strawberry.field(
        default=None, description='Usernames of who registered the cluster; "me" is the viewer.'
    )


@strawberry.type(name="AstroliftManagedDomain")
class ManagedDomainType:
    id: GUID
    zone: str
    organization_slug: str | None
    dns_driver: str
    default_for: str
    is_wildcard_managed: bool
    created_at: dt.datetime
    # Zone provisioning progress (#781): the ZoneRegistrationStep the
    # workflow has reached ('' = not started, 'mark_active' = done), the
    # NS records the operator must point the registrar at, and the cert
    # DNS-01 validation CNAMEs for zones the platform does not host.
    provision_state: str
    provision_nameservers: JSON
    provision_validation_records: JSON
    # Latest public-DNS delegation finding. Kept in dns_config so old rows
    # remain migration-free while operators can see why activation is gated.
    delegation_check: JSON
    provision_cluster_id: str | None
    # Proof-of-control challenge (#1931). 'pending' means the zone name
    # already existed in the provider (or the caller asked to adopt one) and
    # nothing may write DNS, issue a cert, or stand up an ingress for it
    # until verifyManagedDomain confirms the TXT record below.
    verification_state: str
    challenge_record_name: str
    challenge_record_value: str
    verified_at: dt.datetime | None


# ---- Certificate picker (#858) ------------------------------------


@strawberry.type(name="AstroliftClusterCertificate")
class ClusterCertificateType:
    """One TLS certificate the cluster's (or DNS driver's) provider can
    offer for an SNI / custom-domain binding (#858).

    ``arn`` is the cloud-native identifier the platform persists on the
    domain's SNI cert ref / ``dns_config['certificate_arn']`` — an ACM
    ARN on AWS, a Certificate Manager resource name on GCP, a Key Vault
    cert id on Azure. ``name`` is a short human label, ``domain_name``
    the primary subject, ``status`` the cloud-reported issuance state.
    """

    arn: str
    name: str
    domain_name: str
    status: str


@strawberry.type(name="AstroliftClusterCertificates")
class ClusterCertificatesType:
    """Cert-picker payload (#858). ``supported`` is ``False`` when the
    provider has no cert-listing capability wired yet (GCP / Azure /
    k8s_native, or an unsupported DNS driver) — the UI falls back to a
    free-text ARN field. ``certificates`` is empty when unsupported, or
    when supported-but-unreachable (no creds / throttled); the
    ``supported`` flag lets the UI tell those two cases apart."""

    supported: bool
    certificates: list[ClusterCertificateType]


# ---- DNS hosted-zone picker (#861) --------------------------------


@strawberry.type(name="AstroliftDnsZone")
class DnsZoneType:
    """One discoverable DNS hosted zone for the managed-domain zone
    picker (#861).

    ``id`` is the driver-native zone identifier (Route53 hosted-zone
    id, GCP managed-zone name, Azure zone resource id). ``name`` is the
    human-readable zone FQDN (``example.com.`` with the trailing dot
    Route53 returns). ``private`` flags private/internal zones.
    ``config_json`` is a pre-serialized JSON blob ready to drop into the
    dialog's DNS-config textarea (e.g. ``{"zone_id": "Z1234ABC",
    "certificate_arn": ""}``) — the key insight of #861: the backend
    hands the operator the config shape rather than making them build it.
    """

    id: str
    name: str
    private: bool
    config_json: str


@strawberry.type(name="AstroliftDnsZones")
class DnsZonesType:
    """Zone-picker payload (#861). Same ``supported`` semantics as
    ``ClusterCertificatesType`` — ``False`` for DNS drivers without zone
    discovery wired (``cloud_dns`` / ``azure_dns`` today), which the UI
    renders as a disabled picker + "not yet supported" note while
    leaving the manual textarea editable."""

    supported: bool
    zones: list[DnsZoneType]


@strawberry.type(name="AstroliftProviderPlugin")
class ProviderPluginType:
    id: GUID
    slug: str
    name: str
    version: str
    capabilities_manifest: JSON
    is_enabled: bool


# Keys of oidc_auth_config that are safe to read back. cookie_secret is not
# one of them: it is the signing key for the oauth2-proxy session cookie, so
# anyone who can read it can mint a session. Nor is client_secret, which
# lets its holder complete the OIDC flow as the auth host (#2055).
# alb_auth_config needs no equivalent -- its keys (user_pool_arn,
# user_pool_client_id, user_pool_domain) are identifiers, not credentials.
_OIDC_PUBLIC_KEYS = (
    "discovery_url",
    "client_id",
    "upstream_connector",
    "auth_proxy_host",
    "logout_url",
    "jwks_uri",
)


def redact_oidc_auth_config(config: dict | None) -> dict | None:
    """The readable view of a cluster's OIDC edge-auth config.

    An operator needs to know whether the gate is configured and where it
    points, which is what makes a class flip safe to attempt (#1616). They
    do not need the cookie or client secret, so each is reported as set or
    unset rather than returned.
    """
    if not config:
        return None
    view = {k: config[k] for k in _OIDC_PUBLIC_KEYS if k in config}
    view["cookie_secret_set"] = bool(config.get("cookie_secret"))
    view["client_secret_set"] = bool(config.get("client_secret"))
    view["gateway_secret_set"] = bool(config.get("gateway_secret"))
    return view


def cluster_to_type(cluster) -> TenantClusterType:
    from django.utils import timezone

    from astrolift_clusters.heartbeat_status import (
        heartbeat_age_seconds,
    )
    from astrolift_clusters.heartbeat_status import (
        resolve as resolve_heartbeat_status,
    )

    now = timezone.now()
    status = resolve_heartbeat_status(
        last_heartbeat_at=cluster.last_heartbeat_at,
        interval_seconds=cluster.heartbeat_interval_seconds,
        now=now,
    )
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
        ingress_mode=cluster.ingress_mode,
        oidc_auth_config=redact_oidc_auth_config(cluster.oidc_auth_config),
        alb_auth_config=cluster.alb_auth_config,
        is_active=cluster.is_active,
        capabilities=cluster.capabilities or {},
        capabilities_probed_at=cluster.capabilities_probed_at,
        created_at=cluster.created_at,
        lifecycle=cluster.lifecycle,
        last_management_error=cluster.last_management_error or "",
        managed_at=cluster.managed_at,
        secrets_backend_provisioned_at=cluster.secrets_backend_provisioned_at,
        last_heartbeat_at=cluster.last_heartbeat_at,
        heartbeat_interval_seconds=cluster.heartbeat_interval_seconds,
        heartbeat_status=status.value,
        heartbeat_age_seconds=heartbeat_age_seconds(
            last_heartbeat_at=cluster.last_heartbeat_at,
            now=now,
        ),
        agent_provisioned=bool(cluster.agent_key_hash),
        created_by_username=(
            cluster.created_by.get_username()
            if cluster.organization_id is not None and cluster.created_by_id is not None
            else None
        ),
    )


def domain_to_type(domain) -> ManagedDomainType:
    delegation_check = (domain.dns_config or {}).get("delegation_check", {})
    return ManagedDomainType(
        id=GUID(str(domain.guid)),
        zone=domain.zone,
        organization_slug=domain.organization.slug if domain.organization_id else None,
        dns_driver=domain.dns_driver,
        default_for=domain.default_for,
        is_wildcard_managed=domain.is_wildcard_managed,
        created_at=domain.created_at,
        provision_state=domain.provision_state,
        provision_nameservers=domain.provision_nameservers or [],
        provision_validation_records=domain.provision_validation_records or [],
        delegation_check=delegation_check,
        provision_cluster_id=(
            str((domain.dns_config or {}).get("provision_cluster_id"))
            if (domain.dns_config or {}).get("provision_cluster_id")
            else None
        ),
        verification_state=domain.verification_state,
        challenge_record_name=(f"_astrolift-challenge.{domain.zone}" if domain.verification_token else ""),
        challenge_record_value=domain.verification_token,
        verified_at=domain.verified_at,
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
        # The GraphQL field keeps the name `version`; the column behind it
        # is `plugin_version` since #1517.
        version=plugin.plugin_version,
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
    # The last successful recipe run applied it. It must stay selected: an
    # operator run deletes the release of every component it is not given.
    installed_by_recipe: bool = False
    # The capability probe found it running, and the recipe did not install
    # it (for example the ALB controller installed by hand into kube-system).
    # Never pre-selected: a second copy fights the first (#2119).
    running_outside_recipe: bool = False


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


def _bootstrap_component_to_type(
    component, *, installed_by_recipe: bool = False, running_outside_recipe: bool = False
) -> BootstrapComponentType:
    return BootstrapComponentType(
        installed_by_recipe=installed_by_recipe,
        running_outside_recipe=running_outside_recipe,
        key=component.key,
        title=component.title,
        default_enabled=component.default_enabled,
        rationale=component.rationale,
        helm_values=component.helm_values or {},
        requires=list(component.requires),
        options=[_bootstrap_option_to_type(o) for o in component.options],
    )


def bootstrap_plan_to_type(cluster, components) -> BootstrapPlanType:
    from astrolift_clusters.recipe_detection import components_installed_by_recipe, components_running

    recipe = components_installed_by_recipe(cluster)
    outside = components_running(getattr(cluster, "capabilities", None)) - recipe
    return BootstrapPlanType(
        cluster_id=GUID(str(cluster.guid)),
        provider_plugin_slug=cluster.provider_plugin.slug if cluster.provider_plugin_id else "",
        components=[
            _bootstrap_component_to_type(
                c, installed_by_recipe=c.key in recipe, running_outside_recipe=c.key in outside
            )
            for c in components
        ],
    )


def cluster_live_state_to_type(cluster):
    """Build the cheap liveness snapshot from a cluster row's persisted
    heartbeat fields. No cluster API call — pure read of
    ``last_heartbeat_at`` + ``last_heartbeat_payload``."""
    from django.utils import timezone

    from astrolift_clusters.heartbeat_status import (
        heartbeat_age_seconds,
    )
    from astrolift_clusters.heartbeat_status import (
        resolve as resolve_heartbeat_status,
    )

    now = timezone.now()
    status = resolve_heartbeat_status(
        last_heartbeat_at=cluster.last_heartbeat_at,
        interval_seconds=cluster.heartbeat_interval_seconds,
        now=now,
    )
    payload = cluster.last_heartbeat_payload or {}
    pods_by_ns = payload.get("pods_by_namespace") or {}
    pod_total: int | None = None
    if isinstance(pods_by_ns, dict) and pods_by_ns:
        try:
            pod_total = sum(int(v) for v in pods_by_ns.values())
        except (TypeError, ValueError):
            pod_total = None
    ingress_ips = payload.get("ingress_ips") or []
    if not isinstance(ingress_ips, list):
        ingress_ips = []

    return ClusterLiveStateType(
        cluster_id=GUID(str(cluster.guid)),
        status=status.value,
        last_heartbeat_at=cluster.last_heartbeat_at,
        heartbeat_age_seconds=heartbeat_age_seconds(
            last_heartbeat_at=cluster.last_heartbeat_at,
            now=now,
        ),
        heartbeat_interval_seconds=cluster.heartbeat_interval_seconds,
        agent_provisioned=bool(cluster.agent_key_hash),
        node_count=payload.get("node_count"),
        node_ready_count=payload.get("node_ready_count"),
        cpu_utilization=payload.get("cpu_utilization"),
        memory_utilization=payload.get("memory_utilization"),
        pod_total=pod_total,
        pods_by_namespace=pods_by_ns if isinstance(pods_by_ns, dict) else {},
        app_readiness=payload.get("app_readiness") or {},
        ingress_ips=[str(ip) for ip in ingress_ips],
        agent_version=str(payload.get("agent_version") or ""),
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


# ---- Cluster keep-alive live state (#808) -------------------------


@strawberry.type(name="AstroliftClusterLiveState")
class ClusterLiveStateType:
    """Cheap liveness snapshot from the in-cluster keep-alive agent.

    Unlike every other Status-tab card, this resolver does NO driver /
    Prometheus / Temporal call — it reads one persisted timestamp +
    the last heartbeat payload. That's what lets the UI render a status
    badge (and short-circuit the expensive cards into an offline empty-
    state) even when the apiserver is unreachable."""

    cluster_id: GUID
    status: str
    """never_seen | connected | degraded | offline."""

    last_heartbeat_at: dt.datetime | None
    heartbeat_age_seconds: float | None
    heartbeat_interval_seconds: int
    agent_provisioned: bool

    # Snapshot fields from the most recent heartbeat payload. All
    # nullable because an older agent version may not report them.
    node_count: int | None
    node_ready_count: int | None
    """Ready nodes (#112) — render 'N/M ready'. Null on older agents."""
    cpu_utilization: float | None
    memory_utilization: float | None
    pod_total: int | None
    """Sum of pod counts across all reported namespaces."""

    pods_by_namespace: JSON
    app_readiness: JSON
    """Per-app pod readiness (#112): {app_slug: {ready, total}}. Drives
    the Workloads tab's live readiness when the cluster is online."""
    ingress_ips: list[str]
    agent_version: str


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


# ---- Cloud system metrics (CloudWatch ALB) -------------------------


@strawberry.type(name="AstroliftClusterSystemMetricPoint")
class ClusterSystemMetricPointType:
    """A single (unix timestamp, value) data point in a system-metric
    series — same wire shape as the Prometheus range point."""

    ts: float
    value: float


@strawberry.type(name="AstroliftClusterSystemMetricSeries")
class ClusterSystemMetricSeriesType:
    """One named cloud-provider metric time series (e.g. ALB request
    rate) for the platform metrics dashboard."""

    metric: str
    """Machine key — 'request_rate' | 'error_rate' | 'latency_p95'."""

    label: str
    """Human-readable display label for the chart card header."""

    unit: str
    """'rps' | 'ratio' | 'seconds' — how the UI should format the value."""

    current: float | None
    """Last point's value; None when the provider returned no data."""

    points: list[ClusterSystemMetricPointType]


@strawberry.type(name="AstroliftClusterSystemMetrics")
class ClusterSystemMetricsType:
    """Cloud-provider system/ingress metrics for a cluster, powering the
    platform metrics dashboard's "System metrics" panel.

    Unlike the in-cluster Prometheus metrics, these come from the cloud
    provider's own monitoring service (CloudWatch ALB metrics on AWS) and
    need no in-app instrumentation — every managed app on AWS sits behind
    an ALB that emits RequestCount / 5XX / TargetResponseTime
    automatically. ``available`` is False with:

    * reason='not_supported' — the cluster's provider has no cloud-metrics
      driver wired (GCP / Azure / k8s_native today); the UI shows a
      "not available for this provider" state.
    * reason='unreachable' — the provider API call failed (no creds,
      throttled).

    When ``available`` is True the ``series`` are always present; an empty
    ``points`` list inside a series is the "no traffic in this window"
    state (the queried namespace may not front an internet-facing ALB)."""

    available: bool
    reason: str | None
    source: str
    """Provider metrics source — 'cloudwatch' when available, '' otherwise."""

    app_namespace: str
    """The Kubernetes namespace whose ingress ALB was measured. Echoed so
    the UI can label the panel with the scope it actually reflects."""

    range_seconds: int
    step_seconds: int
    series: list[ClusterSystemMetricSeriesType]


# ---- Provider region picker (#860) ---------------------------------


@strawberry.type(name="AstroliftProviderRegion")
class ProviderRegionType:
    """One selectable cloud region for the cluster-register dialog
    (#860). Replaces the free-text region input with a driver-sourced
    picker."""

    id: str
    """Wire-form region slug persisted on the cluster row —
    'us-west-2' / 'us-central1' / 'eastus'."""

    label: str
    """Operator-facing display name — 'US West (Oregon)'. Falls back to
    the slug when the driver can't map a friendly label."""

    continent: str
    """Optional grouping for long lists — 'Americas' / 'Europe' /
    'Asia Pacific' / 'Middle East' / 'Africa'. Empty when unclassified."""


# ---- Cognito user pool picker (#859) --------------------------------


@strawberry.type(name="AstroliftCognitoUserPool")
class CognitoUserPoolType:
    """One Cognito user pool for the ingress auth-gate picker (#859).
    Replaces the free-text user-pool-ARN / domain inputs."""

    pool_id: str
    pool_arn: str
    """Composed arn:aws:cognito-idp:<region>:<account>:userpool/<pool-id>
    — what the ALB authenticate-cognito annotation consumes."""

    name: str
    domain: str
    """Cognito-hosted domain prefix (without the
    .auth.<region>.amazoncognito.com suffix); empty when the pool has no
    hosted domain. Auto-fills the domain field on pool selection."""

    region: str


@strawberry.type(name="AstroliftCognitoUserPoolClient")
class CognitoUserPoolClientType:
    """One app client within a Cognito user pool (#859). Populates the
    dependent client picker after a pool is selected."""

    client_id: str
    client_name: str
