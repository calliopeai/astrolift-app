from __future__ import annotations

import datetime as dt
import logging
import uuid

import strawberry
from django.db import models
from django.db.models import Case, ExpressionWrapper, F, Q, Value, When
from django.db.models.functions import Greatest, Lower
from django.utils import timezone
from strawberry.types import Info

from astrolift_clusters.heartbeat_status import (
    CONNECTED_GRACE_FACTOR,
    MIN_INTERVAL_SECONDS,
    OFFLINE_MISS_THRESHOLD,
    HeartbeatStatus,
)
from astrolift_clusters.models import (
    ManagedDomain,
    ProviderPlugin,
    TenantCluster,
)
from astrolift_clusters.schema.types import (
    BootstrapPlanType,
    ClusterCertificatesType,
    ClusterCertificateType,
    ClusterEventType,
    ClusterHealthType,
    ClusterLifecycleAuditEntryType,
    ClusterLiveStateType,
    ClusterPrometheusMetricsType,
    ClusterPrometheusRangeMetricsType,
    ClusterPrometheusRangePointType,
    ClusterPrometheusRangeSeriesType,
    ClustersListFilterInput,
    ClusterSystemMetricPointType,
    ClusterSystemMetricSeriesType,
    ClusterSystemMetricsType,
    ClusterWorkflowRunType,
    ClusterWorkloadHealthType,
    CognitoUserPoolClientType,
    CognitoUserPoolType,
    DnsZonesType,
    DnsZoneType,
    ManagedDomainType,
    PodPhaseSummaryType,
    ProviderPluginType,
    ProviderRegionType,
    TenantClusterType,
    bootstrap_plan_to_type,
    cluster_live_state_to_type,
    cluster_to_type,
    domain_to_type,
    plugin_to_type,
)
from astrolift_clusters.scopes import cluster_catalog_org_scope, cluster_org_scope
from astrolift_graphql import (
    GUID,
    FilterField,
    PageType,
    SortKey,
    filter_q,
    keyset_page,
    numbered_page,
    resolve_list_sort,
    search_q,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


def _unreachable_reason(endpoint: str) -> str:
    """Why the control plane could not reach ``endpoint``.

    A connection error against an in-cluster Service address is not
    "Prometheus is down" — the control plane queries Prometheus over
    HTTP and a ClusterIP is not routable from outside the cluster.
    Saying which of the two it is saves the operator an investigation
    that ends at a healthy Prometheus (#1711).
    """
    from astrolift_observability.prom_client import is_cluster_internal_endpoint

    return "cluster_internal_endpoint" if is_cluster_internal_endpoint(endpoint) else "unreachable"


def _operator(info: Info) -> bool:
    """The platform operator, bearer admin scope included (#1949)."""
    from core.permissions import require_platform_operator

    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) or getattr(info.context, "user", None)
    try:
        require_platform_operator(user)
    except Exception:  # noqa: BLE001 - Django PermissionDenied, or no user
        return False
    return True


def _tenant_view_of_shared(info: Info, cluster) -> bool:
    """A tenant (not the operator) looking at a shared, org-NULL cluster.

    Such a cluster runs every org's workloads and sits in the platform's
    cloud account, so a tenant's live reads are confined to its own app
    namespaces and never list account-wide resources (#1967).
    """
    return cluster.organization_id is None and not _operator(info)


def _org_app_namespaces(cluster, organization_id) -> list[str]:
    """Namespaces of ``organization_id``'s app environments bound to
    ``cluster``: the app namespace, or an environment's own (#1922)."""
    from astrolift_lifecycle.models.app_environment import AppEnvironment
    from core.cluster_observability import namespace_for_environment

    envs = (
        AppEnvironment.objects.filter(
            tenant_cluster=cluster,
            registered_app__deleted_at__isnull=True,
            registered_app__organization_id=organization_id,
        )
        .select_related("registered_app__organization")
        .only(
            "k8s_namespace",
            "registered_app__slug",
            "registered_app__k8s_namespace",
            "registered_app__organization__slug",
        )
    )
    return sorted({namespace_for_environment(env) for env in envs})


def _primary_app_namespace(cluster) -> str:
    """Alphabetically-first app namespace bound to *cluster*, or
    ``astrolift-system`` when the cluster hosts no managed apps yet.

    Lets ``astroliftClusterSystemMetrics`` default to a namespace that
    actually fronts an ingress ALB (a deployed app) so the platform
    metrics panel shows real traffic instead of the empty system
    namespace. Mirrors the namespace collection
    ``astroliftClusterWorkloadHealth`` already does."""
    from astrolift_lifecycle.models.app_environment import AppEnvironment
    from core.cluster_observability import namespace_for_environment

    envs = (
        AppEnvironment.objects.filter(
            tenant_cluster=cluster,
            registered_app__deleted_at__isnull=True,
        )
        .select_related("registered_app__organization")
        .only(
            "k8s_namespace",
            "registered_app__slug",
            "registered_app__k8s_namespace",
            "registered_app__organization__slug",
        )
    )
    namespaces = sorted({namespace_for_environment(ae) for ae in envs})
    return namespaces[0] if namespaces else "astrolift-system"


def _clusters_qs(*, search: str | None = None):
    """Filtered, unordered cluster inventory visible to the caller.

    Shared by the list field and its paginated sibling so the two can
    never disagree about which clusters exist. Platform-level rows
    (``organization`` null) are readable by every org; org-owned rows
    only by their own org — the same union ``astroliftClusterCount``
    and the per-cluster resolvers apply. Ordering is deliberately not
    applied here; ``keyset_page`` imposes it from the seek key.
    """
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        # Fail closed (#1183). Defence in depth behind @tenant_scoped:
        # with a null org the union below would degrade to "every
        # platform-level cluster" rather than to nothing.
        return TenantCluster.objects.none()
    qs = TenantCluster.objects.filter(
        Q(organization_id=org_id) | Q(organization_id__isnull=True),
    ).select_related("organization", "provider_plugin", "created_by")
    if search:
        qs = qs.filter(
            search_q(
                search,
                "name",
                "slug",
                "endpoint",
                "region",
                "provider_plugin__slug",
            )
        )
    return qs


# ---------------------------------------------------------------------------
# The list contract on the Clusters list (spec 44 §5.1, #2150)
# ---------------------------------------------------------------------------
#
# What /clusters used to work out in the browser over the whole fleet
# (provider, lifecycle, the Offline and Mine views, the column sorts,
# numbered pages) is a column or an annotation here, so OFFSET and
# totalCount are exact. See astrolift_graphql/README.md.

#: Lifecycles in the order the Status column sorts them.
_LIFECYCLE_ORDER = [choice.value for choice in TenantCluster.Lifecycle]

#: Heartbeat statuses in the order the Live column sorts them: reachable first.
_HEARTBEAT_ORDER = [
    HeartbeatStatus.CONNECTED.value,
    HeartbeatStatus.DEGRADED.value,
    HeartbeatStatus.OFFLINE.value,
    HeartbeatStatus.NEVER_SEEN.value,
]

_CLUSTERS_DEFAULT_SORT = "name"

_CLUSTERS_SORT_KEYS = {
    "name": SortKey(Lower("name")),
    "slug": SortKey("slug"),
    "created": SortKey("created_at"),
    "status": SortKey("_lifecycle_rank"),
    "provider": SortKey("provider_plugin__slug"),
    "region": SortKey(Lower("region")),
    "live": SortKey("_heartbeat_rank"),
    # Never probed sorts below the oldest probe, as it did in the browser.
    "lastProbe": SortKey("capabilities_probed_at", nulls_low=True),
}

_CLUSTERS_FILTERS = {
    "provider": FilterField("provider_plugin__slug"),
    "status": FilterField("lifecycle"),
    "live": FilterField("_heartbeat_status"),
    "registered_by": FilterField("created_by__username", me=True),
}


def _annotate_clusters_list(qs, *, now: dt.datetime | None = None):
    """Annotate the heartbeat status and the sort ranks the list needs.

    ``_heartbeat_status`` is ``heartbeat_status.resolve`` in SQL: the same
    floored interval and the same bands, so the Offline view counts the
    rows the Live column paints red.
    """
    now = now or timezone.now()
    interval = ExpressionWrapper(
        Greatest(F("heartbeat_interval_seconds"), Value(MIN_INTERVAL_SECONDS))
        * Value(dt.timedelta(seconds=1)),
        output_field=models.DurationField(),
    )

    def seen_since(factor: float):
        return ExpressionWrapper(Value(now) - interval * Value(factor), output_field=models.DateTimeField())

    qs = qs.annotate(
        _heartbeat_status=Case(
            When(last_heartbeat_at__isnull=True, then=Value(HeartbeatStatus.NEVER_SEEN.value)),
            When(
                last_heartbeat_at__gte=seen_since(CONNECTED_GRACE_FACTOR),
                then=Value(HeartbeatStatus.CONNECTED.value),
            ),
            When(
                last_heartbeat_at__gt=seen_since(OFFLINE_MISS_THRESHOLD),
                then=Value(HeartbeatStatus.DEGRADED.value),
            ),
            default=Value(HeartbeatStatus.OFFLINE.value),
            output_field=models.CharField(),
        ),
        _lifecycle_rank=Case(
            *(When(lifecycle=value, then=Value(rank)) for rank, value in enumerate(_LIFECYCLE_ORDER)),
            default=Value(len(_LIFECYCLE_ORDER)),
            output_field=models.IntegerField(),
        ),
    )
    return qs.annotate(
        _heartbeat_rank=Case(
            *(When(_heartbeat_status=value, then=Value(rank)) for rank, value in enumerate(_HEARTBEAT_ORDER)),
            default=Value(len(_HEARTBEAT_ORDER)),
            output_field=models.IntegerField(),
        )
    )


def _viewer_username(info: Info) -> str | None:
    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) or getattr(info.context, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user.get_username()
    return None


# Mutations whose input names the cluster by slug: registration runs before
# the guid exists, and the CLI reports a bootstrap run by slug.
_SLUG_ADDRESSED_CLUSTER_OPERATIONS = frozenset(
    (
        "cluster.register",
        "RegisterTenantCluster",
        "cluster.record_bootstrap_run",
        "RecordClusterBootstrapRun",
    ),
)


def _variables_name_cluster(variables, *, guid: uuid.UUID, slug: str | None) -> bool:
    """Whether some value in ``variables`` is the cluster's guid, or ``slug``.

    Compares whole values, never a substring of the serialised payload
    (#1955): a short slug such as ``prod`` is part of unrelated values, and
    a guid inside a longer string is not the id the mutation addressed.
    The guid is compared as a UUID so a caller's spelling of it (case,
    hyphens) does not hide the row.
    """
    stack = [variables]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str):
            if slug is not None and value == slug:
                return True
            try:
                if uuid.UUID(value) == guid:
                    return True
            except ValueError:
                continue
    return False


@strawberry.type
class ClustersQuery:
    @strawberry.field(
        deprecation_reason="Caps at 200 rows with no way to reach the 201st. Use astroliftClustersPage."
    )
    @require_permission(
        Permission.CLUSTER_REGISTER, scope=cluster_catalog_org_scope(Permission.CLUSTER_REGISTER)
    )
    @tenant_scoped()
    def astrolift_clusters(self, info: Info) -> list[TenantClusterType]:
        qs = _clusters_qs().order_by("slug")[:200]
        return [cluster_to_type(c) for c in qs]

    @strawberry.field
    @require_permission(
        Permission.CLUSTER_REGISTER, scope=cluster_catalog_org_scope(Permission.CLUSTER_REGISTER)
    )
    @tenant_scoped()
    def astrolift_clusters_page(
        self,
        info: Info,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
        filter: ClustersListFilterInput | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> PageType[TenantClusterType]:
        """Cursor-paginated cluster inventory (#1235).

        Replaces ``astroliftClusters``, whose 200-row cap makes the 201st
        cluster unreachable from /clusters rather than merely slow to
        reach — the operator-visible bug behind #1230.

        Seek key is ``(slug, guid)`` ASCENDING, not the default
        ``(-created_at, -guid)``: the list field serves clusters
        alphabetically and /clusters is an inventory an operator scans by
        name, so the surface's ordering survives the cutover. ``slug``
        carries a partial unique index over live rows and the default
        manager hides soft-deleted ones, so the key is unique across the
        whole walk; ``guid`` is the tiebreak of record regardless.

        ``search`` matches what an operator types into the /clusters
        search box — name, slug, endpoint, region, and the provider
        plugin's slug.

        The list contract (spec 44 §5.1, #2150): ``filter`` takes the
        declared filters (provider, status, live, registeredBy), ``sort`` a
        multi-key spec over name, slug, created, status, provider, region,
        live and lastProbe (``-lastProbe,name``), and ``page`` /
        ``pageSize`` a numbered page. Any of ``sort``, ``page`` or
        ``pageSize`` selects numbered paging: an exact ``totalCount``,
        ``page`` and ``pageSize`` echoed, ``nextCursor`` null, default
        order ``name``. Otherwise the cursor walk runs unchanged, with
        ``filter`` applied first. An undeclared sort key is an error.
        """
        qs = _clusters_qs(search=search)
        if filter is not None:
            qs = _annotate_clusters_list(qs).filter(
                filter_q(filter, _CLUSTERS_FILTERS, me=_viewer_username(info))
            )
        if page is not None or page_size is not None or sort is not None:
            order_by = resolve_list_sort(sort, _CLUSTERS_SORT_KEYS, default=_CLUSTERS_DEFAULT_SORT)
            if filter is None:
                qs = _annotate_clusters_list(qs)
            return numbered_page(qs, order_by=order_by, page=page, page_size=page_size).map(cluster_to_type)
        result = keyset_page(
            qs,
            cursor=after,
            limit=limit,
            sort_field="slug",
            tiebreak_field="guid",
            descending=False,
        )
        return result.map(cluster_to_type)

    @strawberry.field
    @require_permission(
        Permission.CLUSTER_REGISTER,
        scope=cluster_org_scope(Permission.CLUSTER_REGISTER, "slug", by_slug=True),
    )
    @tenant_scoped()
    def astrolift_cluster(self, info: Info, slug: str) -> TenantClusterType | None:
        """One cluster by slug, or null when the caller cannot see it (#2150).

        The detail page and its tabs used to find the cluster in the
        deprecated ``astroliftClusters`` list, which stops at 200 rows, so
        a cluster past the 200th alphabetically read as "not found" on its
        own page. Same visibility as the list: the caller's org plus shared
        clusters, never another org's.
        """
        cluster = _clusters_qs().filter(slug=slug).first()
        return cluster_to_type(cluster) if cluster is not None else None

    @strawberry.field
    @require_permission(Permission.APP_CREATE, scope=cluster_catalog_org_scope(Permission.APP_CREATE))
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
        # Platform-level clusters (organization=None) are available to all
        # orgs. Org-scoped clusters are only available to their own org.

        return TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        ).count()

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def astrolift_managed_domains(self, info: Info) -> list[ManagedDomainType]:
        tenant = get_current_tenant()
        qs = (
            ManagedDomain.objects.filter(
                Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            )
            .select_related("organization")
            .order_by("zone")[:200]
        )
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
    @require_permission(
        Permission.CLUSTER_REGISTER, scope=cluster_catalog_org_scope(Permission.CLUSTER_REGISTER)
    )
    @tenant_scoped()
    def astrolift_provider_regions(
        self,
        info: Info,
        provider_plugin_slug: str,
    ) -> list[ProviderRegionType]:
        """Selectable cloud regions for ``provider_plugin_slug`` (#860).

        Backs the region picker on the cluster-register dialog. Called
        with the plugin slug alone (no cluster row exists yet — the
        operator is mid-register), so the dispatch builds the driver
        from a minimal bootstrap config and calls its ``list_regions``.
        AWS goes live (``ec2:DescribeRegions``, with its own static
        fallback); GCP / Azure return curated static lists;
        ``k8s_native`` has no region concept and returns an empty list
        (the UI hides the field).

        Driver-resolution failure (plugin not loaded) yields an empty
        list rather than an error — the frontend keeps free-text entry
        layered on top of the picker, so an empty list degrades to the
        old free-entry behavior instead of blocking registration.
        """
        from core.cluster_management import (
            ClusterManagementError,
            provider_regions_dispatch,
        )

        try:
            rows = provider_regions_dispatch(provider_plugin_slug=provider_plugin_slug)
        except ClusterManagementError:
            return []
        return [ProviderRegionType(id=r["id"], label=r["label"], continent=r["continent"]) for r in rows]

    @strawberry.field
    @require_permission(Permission.CLUSTER_REGISTER, scope=cluster_org_scope(Permission.CLUSTER_REGISTER))
    @tenant_scoped()
    def astrolift_cluster_lifecycle_audit(
        self,
        info: Info,
        cluster_id: GUID,
        limit: int = 50,
    ) -> list[ClusterLifecycleAuditEntryType]:
        """Cluster-scoped slice of the mutation audit log (#68 slice 2).

        Filters ``MutationAuditLog`` by cluster-targeted operations
        whose ``variables`` name this cluster. The resolver surfaces a
        flat list of "what happened to this cluster, in what order, by
        whom" — the workhorse for the cluster-detail Status tab's
        lifecycle timeline card.

        Only rows written in the caller's org come back (#1955). A shared
        cluster resolves for every org, so the cluster lookup alone would
        hand one org another org's mutations against it: operation,
        variables, errors and actor.
        """

        from core.schema.audit import MutationAuditLog

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            # Fail closed. With a null org the shared-cluster union below
            # would match every platform cluster, and the row filter would
            # match every row written without a tenant.
            return []
        cluster = TenantCluster.objects.filter(
            Q(organization_id=org_id) | Q(organization_id__isnull=True),
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
                "IssueClusterAgentKey",
                "CreateManagedDomain",
                "UpdateManagedDomain",
                "SoftDeleteManagedDomain",
                "ConfigureProviderPlugin",
            ),
        )
        operation_q = Q(operation__in=operation_names)
        for prefix in prefixes:
            operation_q |= Q(operation__startswith=prefix)
        qs = (
            MutationAuditLog.objects.filter(operation_q, organization_id=org_id)
            .select_related("user")
            .order_by("-timestamp", "-pk")
        )
        cluster_guid = uuid.UUID(str(cluster.guid))
        limit = max(1, min(limit, 200))
        out: list[ClusterLifecycleAuditEntryType] = []
        for log in qs.iterator(chunk_size=200):
            slug = cluster.slug if log.operation in _SLUG_ADDRESSED_CLUSTER_OPERATIONS else None
            if not _variables_name_cluster(log.variables, guid=cluster_guid, slug=slug):
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
    @require_permission(Permission.CLUSTER_REGISTER, scope=cluster_org_scope(Permission.CLUSTER_REGISTER))
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

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None or _tenant_view_of_shared(info, cluster):
            # A shared cluster's install and bootstrap workflows are the
            # platform's, and name other orgs' activity (#1967).
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
    @require_permission(Permission.CLUSTER_REGISTER, scope=cluster_org_scope(Permission.CLUSTER_REGISTER))
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

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
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
    @require_permission(Permission.CLUSTER_REGISTER, scope=cluster_org_scope(Permission.CLUSTER_REGISTER))
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

        tenant = get_current_tenant()
        cluster = (
            TenantCluster.objects.filter(
                Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
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
    @require_permission(Permission.CLUSTER_REGISTER, scope=cluster_org_scope(Permission.CLUSTER_REGISTER))
    @tenant_scoped()
    def astrolift_cluster_live_state(
        self,
        info: Info,
        cluster_id: GUID,
    ) -> ClusterLiveStateType | None:
        """Cheap keep-alive liveness snapshot for ``cluster_id`` (#808).

        Reads only the persisted heartbeat fields — NO driver /
        Prometheus / Temporal call — so it returns instantly even when
        the apiserver is unreachable. This is the resolver the UI hits
        first to decide whether to render the live cards or the targeted
        'cluster offline' empty-state. Returns ``None`` when the cluster
        row is missing or is outside the caller's tenant scope.
        """

        tenant = get_current_tenant()
        # Platform-level clusters (organization=None) are visible to all
        # orgs; org-scoped clusters only to their own org. Same scope
        # filter as astrolift_cluster_count — a caller must never read
        # the live state of a cluster in another tenant.
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return None
        return cluster_live_state_to_type(cluster)

    @strawberry.field
    @require_permission(Permission.CLUSTER_UPDATE, scope=cluster_org_scope(Permission.CLUSTER_UPDATE))
    @tenant_scoped()
    def astrolift_cognito_user_pools(
        self,
        info: Info,
        cluster_id: GUID,
    ) -> list[CognitoUserPoolType]:
        """Cognito user pools reachable in ``cluster_id``'s region (#859).

        Backs the user-pool picker in the ingress auth-gate card,
        replacing the free-text pool-ARN / domain inputs. Gated on
        ``cluster.update`` — the same permission the auth-gate save
        path requires — so the picker is only offered to operators who
        can actually persist the resulting config.

        Calls the driver's ``list_cognito_user_pools``
        (``cognito-idp:ListUserPools`` + per-pool DescribeUserPool for
        the hosted domain) using the cluster's IAM role (the ambient
        credential chain, same path the provisioner uses). AWS-only;
        non-AWS clusters return an empty list. Missing / soft-deleted
        cluster or any driver / credential failure yields an empty list
        so the picker degrades to free-entry rather than erroring the
        card.
        """

        from core.cluster_management import (
            ClusterManagementError,
            cognito_user_pools_dispatch,
        )

        tenant = get_current_tenant()
        cluster = (
            TenantCluster.objects.filter(
                Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
                guid=str(cluster_id),
                deleted_at__isnull=True,
            )
            .select_related("provider_plugin")
            .first()
        )
        if cluster is None:
            return []
        if _tenant_view_of_shared(info, cluster):
            # Account-wide cloud resources of the platform's account list
            # every org's pools and certificate domains (#1967).
            return []
        try:
            rows = cognito_user_pools_dispatch(cluster=cluster)
        except ClusterManagementError:
            return []
        return [
            CognitoUserPoolType(
                pool_id=r["pool_id"],
                pool_arn=r["pool_arn"],
                name=r["name"],
                domain=r["domain"],
                region=r["region"],
            )
            for r in rows
        ]

    @strawberry.field
    @require_permission(Permission.CLUSTER_UPDATE, scope=cluster_org_scope(Permission.CLUSTER_UPDATE))
    @tenant_scoped()
    def astrolift_cognito_user_pool_clients(
        self,
        info: Info,
        cluster_id: GUID,
        pool_id: str,
    ) -> list[CognitoUserPoolClientType]:
        """App clients within Cognito user pool ``pool_id`` on
        ``cluster_id`` (#859).

        Populates the dependent client picker after the operator picks
        a pool. Same permission gate, credential path, and
        degrade-to-empty contract as ``astroliftCognitoUserPools``.
        """

        from core.cluster_management import (
            ClusterManagementError,
            cognito_user_pool_clients_dispatch,
        )

        tenant = get_current_tenant()
        cluster = (
            TenantCluster.objects.filter(
                Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
                guid=str(cluster_id),
                deleted_at__isnull=True,
            )
            .select_related("provider_plugin")
            .first()
        )
        if cluster is None:
            return []
        if _tenant_view_of_shared(info, cluster):
            # Account-wide cloud resources of the platform's account list
            # every org's pools and certificate domains (#1967).
            return []
        try:
            rows = cognito_user_pool_clients_dispatch(cluster=cluster, pool_id=pool_id)
        except ClusterManagementError:
            return []
        return [
            CognitoUserPoolClientType(
                client_id=r["client_id"],
                client_name=r["client_name"],
            )
            for r in rows
        ]

    @strawberry.field
    @require_permission(Permission.CLUSTER_MANAGE, scope=cluster_org_scope(Permission.CLUSTER_MANAGE))
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

        tenant = get_current_tenant()
        cluster = (
            TenantCluster.objects.filter(
                Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
                guid=str(cluster_id),
                deleted_at__isnull=True,
            )
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
    @require_permission(Permission.CLUSTER_REGISTER, scope=cluster_org_scope(Permission.CLUSTER_REGISTER))
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

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
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
                reason=_unreachable_reason(endpoint),
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
    @require_permission(Permission.CLUSTER_REGISTER, scope=cluster_org_scope(Permission.CLUSTER_REGISTER))
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

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
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
                reason=_unreachable_reason(endpoint),
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
    @require_permission(Permission.CLUSTER_REGISTER, scope=cluster_org_scope(Permission.CLUSTER_REGISTER))
    @tenant_scoped()
    def astrolift_cluster_system_metrics(
        self,
        info: Info,
        cluster_id: GUID,
        app_namespace: str | None = None,
        range_seconds: int = 3600,
        step_seconds: int = 300,
    ) -> ClusterSystemMetricsType:
        """Cloud-provider system/ingress metrics for the platform metrics
        dashboard's "System metrics" panel.

        Sources request rate, error rate, and p95 latency from the cloud
        provider's own monitoring service via the cluster driver's
        ``get_alb_http_metrics`` — on AWS this is CloudWatch ALB metrics
        (RequestCount / HTTPCode_Target_5XX_Count / TargetResponseTime),
        which every managed app emits automatically with no in-app
        instrumentation. This complements the in-cluster
        ``astroliftClusterPrometheusMetrics`` with a source that works even
        when Prometheus isn't scraping HTTP series yet.

        AWS-only today: non-AWS providers (GCP / Azure / k8s_native) have
        no cloud-metrics driver wired and return ``available=False`` with
        ``reason='not_supported'`` so the UI degrades to a clear
        "not available for this provider" state rather than a blank panel.
        A driver-call failure yields ``reason='unreachable'``.

        ``app_namespace`` selects which ingress ALB to measure; when
        omitted the resolver picks the alphabetically-first app namespace
        bound to the cluster (falling back to ``astrolift-system``) so the
        default view reflects real ingress traffic. The resolved namespace
        is echoed back so the UI can label the panel with the scope it
        actually measured. Inputs are clamped (range 5m–7d, step 60s–3600s)
        so a rogue caller can't ask CloudWatch for an absurd datapoint
        count."""
        import time as _time

        from core.cluster_management import (
            ClusterManagementError,
            cluster_alb_http_metrics_dispatch,
        )

        tenant = get_current_tenant()
        cluster = (
            TenantCluster.objects.filter(
                Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
                guid=str(cluster_id),
                deleted_at__isnull=True,
            )
            .select_related("provider_plugin")
            .first()
        )
        if cluster is None:
            return ClusterSystemMetricsType(
                available=False,
                reason=None,
                source="",
                app_namespace="",
                range_seconds=range_seconds,
                step_seconds=step_seconds,
                series=[],
            )

        provider_slug = (
            cluster.provider_plugin.slug if cluster.provider_plugin_id and cluster.provider_plugin else ""
        ).lower()
        if provider_slug != "aws":
            # Only the AWS/EKS driver implements get_alb_http_metrics today.
            # GCP / Azure / k8s_native have no cloud-metrics driver wired —
            # surface a clear not-supported state instead of an empty panel.
            return ClusterSystemMetricsType(
                available=False,
                reason="not_supported",
                source="",
                app_namespace="",
                range_seconds=range_seconds,
                step_seconds=step_seconds,
                series=[],
            )

        namespace = (app_namespace or "").strip() or _primary_app_namespace(cluster)
        if _tenant_view_of_shared(info, cluster):
            # A tenant measures only its own app's ingress on a shared
            # cluster: an explicit namespace must be one of its apps', and
            # the default is its own first app, not the cluster's (#1967).
            own = _org_app_namespaces(cluster, get_current_tenant().organization_id)
            requested = (app_namespace or "").strip()
            namespace = requested if requested in own else (own[0] if own and not requested else "")
            if not namespace:
                return ClusterSystemMetricsType(
                    available=False,
                    reason="namespace_not_found",
                    source="",
                    app_namespace="",
                    range_seconds=range_seconds,
                    step_seconds=step_seconds,
                    series=[],
                )

        # Clamp: range 5m–7d, step 60s–3600s. CloudWatch bills per datapoint
        # and rejects requests over its per-call datapoint ceiling.
        range_seconds = max(300, min(int(range_seconds), 7 * 86400))
        step_seconds = max(60, min(int(step_seconds), 3600))

        end_unix = int(_time.time())
        start_unix = end_unix - range_seconds

        log.info(
            "system_metrics: cluster=%s ns=%s range=%ss step=%ss",
            cluster_id,
            namespace,
            range_seconds,
            step_seconds,
        )
        try:
            raw = cluster_alb_http_metrics_dispatch(
                cluster=cluster,
                app_namespace=namespace,
                start_unix=start_unix,
                end_unix=end_unix,
                step_seconds=step_seconds,
            )
        except ClusterManagementError as exc:
            log.warning(
                "system_metrics: dispatch failed cluster=%s ns=%s err=%s",
                cluster_id,
                namespace,
                exc,
            )
            return ClusterSystemMetricsType(
                available=False,
                reason="unreachable",
                source="",
                app_namespace=namespace,
                range_seconds=range_seconds,
                step_seconds=step_seconds,
                series=[],
            )

        # (machine key, display label, unit, CloudWatch dispatch key). We
        # surface p95 latency rather than every quantile the driver returns
        # to keep the platform panel to three legible golden-signal charts;
        # the per-app Observability tab carries the full latency spread.
        _SPECS = (
            ("request_rate", "Request rate", "rps", "rps"),
            ("error_rate", "Error rate", "ratio", "error_rate"),
            ("latency_p95", "Latency p95", "seconds", "latency_p95"),
        )
        series: list[ClusterSystemMetricSeriesType] = []
        for metric, label, unit, cw_key in _SPECS:
            pairs = raw.get(cw_key) or []
            points = [ClusterSystemMetricPointType(ts=float(ts), value=float(val)) for ts, val in pairs]
            series.append(
                ClusterSystemMetricSeriesType(
                    metric=metric,
                    label=label,
                    unit=unit,
                    current=points[-1].value if points else None,
                    points=points,
                ),
            )

        return ClusterSystemMetricsType(
            available=True,
            reason=None,
            source="cloudwatch",
            app_namespace=namespace,
            range_seconds=range_seconds,
            step_seconds=step_seconds,
            series=series,
        )

    @strawberry.field
    @require_permission(Permission.CLUSTER_REGISTER, scope=cluster_org_scope(Permission.CLUSTER_REGISTER))
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

        tenant = get_current_tenant()
        cluster = (
            TenantCluster.objects.filter(
                Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
                guid=str(cluster_id),
                deleted_at__isnull=True,
            )
            .select_related("provider_plugin")
            .first()
        )
        if cluster is None:
            return []

        # Collect the Kubernetes namespaces for every app environment bound to
        # this cluster so the workload-health table includes app workloads, not
        # just the astrolift-system namespace.
        from astrolift_lifecycle.models.app_environment import AppEnvironment
        from core.cluster_observability import namespace_for_environment

        app_envs = (
            AppEnvironment.objects.filter(
                tenant_cluster=cluster,
                registered_app__deleted_at__isnull=True,
            )
            .select_related("registered_app__organization")
            .only(
                "k8s_namespace",
                "registered_app__slug",
                "registered_app__k8s_namespace",
                "registered_app__organization__slug",
            )
        )
        app_namespaces = list({namespace_for_environment(ae) for ae in app_envs})
        if _tenant_view_of_shared(info, cluster):
            # Only this org's own namespaces on a shared cluster, never the
            # platform's or another org's; and never "all namespaces" (the
            # driver's meaning of ``None``) for an org with no app there.
            namespaces = _org_app_namespaces(cluster, tenant.organization_id)
            if not namespaces:
                return []
        else:
            namespaces = ["astrolift-system", *app_namespaces] if app_namespaces else None

        try:
            rows = cluster_workload_health_dispatch(
                cluster=cluster,
                namespaces=namespaces,
            )
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

    @strawberry.field
    @require_permission(Permission.APP_DEPLOY, scope=cluster_org_scope(Permission.APP_DEPLOY))
    @tenant_scoped()
    def astrolift_cluster_certificates(
        self,
        info: Info,
        cluster_id: GUID,
    ) -> ClusterCertificatesType:
        """TLS certificates the cluster's provider can offer for an SNI /
        custom-domain binding (#858).

        Backs the cert picker on the app Domains page: rather than make
        the operator paste an ACM ARN, the picker offers the certs the
        platform's IAM role can already see. Gated on ``APP_DEPLOY``
        (not ``CLUSTER_REGISTER``) because the natural caller is the app
        author configuring a domain, mirroring the page's other
        domain-mutation gates.

        For AWS the certs come from ACM via the EKS driver. GCP / Azure
        / k8s_native return ``supported=False`` (empty list) until their
        cert APIs land — the UI falls back to a free-text ARN field.
        Missing / soft-deleted clusters and driver-resolution failures
        also yield ``supported=False`` rather than erroring, so the form
        degrades gracefully.
        """

        from core.cluster_management import (
            ClusterManagementError,
            cluster_certificates_dispatch,
        )

        tenant = get_current_tenant()
        cluster = (
            TenantCluster.objects.filter(
                Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
                guid=str(cluster_id),
                deleted_at__isnull=True,
            )
            .select_related("provider_plugin")
            .first()
        )
        if cluster is None:
            return ClusterCertificatesType(supported=False, certificates=[])
        if _tenant_view_of_shared(info, cluster):
            # Account-wide cloud resources of the platform's account list
            # every org's pools and certificate domains (#1967).
            return ClusterCertificatesType(supported=False, certificates=[])
        try:
            payload = cluster_certificates_dispatch(cluster=cluster)
        except ClusterManagementError:
            # Driver implements the method but the cloud call blew up
            # (no creds / throttled). The capability exists, so keep
            # supported=True with an empty list — the picker shows an
            # empty state rather than silently reverting to manual entry.
            return ClusterCertificatesType(supported=True, certificates=[])
        return ClusterCertificatesType(
            supported=bool(payload["supported"]),
            certificates=[
                ClusterCertificateType(
                    arn=c["arn"],
                    name=c["name"],
                    domain_name=c["domain_name"],
                    status=c["status"],
                )
                for c in payload["certificates"]
            ],
        )

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def astrolift_dns_zones(
        self,
        info: Info,
        dns_driver: str,
    ) -> DnsZonesType:
        """Discoverable DNS hosted zones for ``dns_driver`` (#861).

        Backs the zone picker on the "Add managed domain" dialog —
        selecting a zone auto-fills the dialog's DNS-config textarea
        from the zone's pre-serialized ``config_json``. Keyed by the
        DNS-driver slug (``route53`` / ``cloud_dns`` / ``azure_dns``)
        rather than a cluster, because the dialog runs before any
        cluster is in the loop; route53 lists hosted zones through the
        platform's ambient AWS credentials.

        Gated on ``PROVIDER_PLUGIN_READ`` to match the managed-domains
        admin surface. ``cloud_dns`` / ``azure_dns`` return
        ``supported=False`` until their list APIs land — the UI disables
        the picker and leaves the textarea editable for manual entry.
        """
        from core.dns_discovery import dns_zones_dispatch

        if not _operator(info):
            # The listing runs on the platform's ambient credentials, whose
            # account holds every tenant's zones; a tenant enters its own (#1932).
            return DnsZonesType(supported=False, zones=[])
        payload = dns_zones_dispatch(dns_driver=dns_driver)
        return DnsZonesType(
            supported=bool(payload["supported"]),
            zones=[
                DnsZoneType(
                    id=z["id"],
                    name=z["name"],
                    private=bool(z["private"]),
                    config_json=z["config_json"],
                )
                for z in payload["zones"]
            ],
        )

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def astrolift_dns_certificates(
        self,
        info: Info,
        dns_driver: str,
    ) -> ClusterCertificatesType:
        """Discoverable TLS certificates for ``dns_driver`` (#858).

        Driver-keyed analog of ``astroliftClusterCertificates`` for the
        managed-domain dialog, which has no cluster context. Backs the
        cert picker that fills the ``certificate_arn`` key in the
        dialog's DNS-config JSON. For ``route53`` the certs come from
        the region-scoped ACM client; other drivers report
        ``supported=False``.
        """
        from core.dns_discovery import dns_certificates_dispatch

        if not _operator(info):
            # Same account as the zone picker: every tenant's certificates (#1932).
            return ClusterCertificatesType(supported=False, certificates=[])
        payload = dns_certificates_dispatch(dns_driver=dns_driver)
        return ClusterCertificatesType(
            supported=bool(payload["supported"]),
            certificates=[
                ClusterCertificateType(
                    arn=c["arn"],
                    name=c["name"],
                    domain_name=c["domain_name"],
                    status=c["status"],
                )
                for c in payload["certificates"]
            ],
        )
