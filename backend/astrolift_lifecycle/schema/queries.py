"""Read-only queries for the lifecycle app."""

from __future__ import annotations

import logging
from datetime import timedelta

import strawberry
from django.db.models import Count, Q
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import (
    GUID,
    PageType,
    filter_q,
    keyset_page,
    numbered_page,
    resolve_list_sort,
    search_q,
)
from astrolift_graphql.sorting import NAMED_MODEL_SORTS, ListSortKey, resolve_sort
from astrolift_identity.operation_context import deployment_operation, named_environment, row_operation
from astrolift_identity.operation_visibility import require_app_collection_scope, visible_operation_rows
from astrolift_identity.scope_visibility import visible_apps
from astrolift_lifecycle.models import (
    AgentRun,
    AppEnvironment,
    CommandRun,
    CustomDomain,
    Deployment,
    DeploymentLog,
    DeployToken,
    PreviewEnvironment,
    ScheduledJobRun,
    TaskRun,
)
from astrolift_lifecycle.schema.list_contract import (
    COMMAND_RUNS_FILTERS,
    DEPLOYMENTS_DEFAULT_SORT,
    DEPLOYMENTS_FILTERS,
    DEPLOYMENTS_SORT_FIELDS,
    ENVIRONMENTS_DEFAULT_SORT,
    ENVIRONMENTS_FILTERS,
    ENVIRONMENTS_SORT_KEYS,
    PREVIEW_FILTERS,
    PREVIEW_SORT_FIELDS,
    PREVIEWS_DEFAULT_SORT,
    SCHEDULED_JOB_RUNS_FILTERS,
    CommandRunsFilterInput,
    DeploymentsListFilterInput,
    EnvironmentsListFilterInput,
    PreviewEnvironmentsFilterInput,
    ScheduledJobRunsFilterInput,
    annotate_deployments,
    annotate_environments,
    annotate_previews,
    cursor_sort,
)
from astrolift_lifecycle.schema.types import (
    AgentRunType,
    AppCertificatesResult,
    AppDnsRecordsResult,
    AppDomainType,
    AppEnvironmentType,
    AppHealthSummaryType,
    AppIdentityBindingResult,
    AppPodEventType,
    AppPodType,
    CommandRunType,
    DeploymentApprovalHistoryEntryType,
    DeploymentComparisonType,
    DeploymentLogEntryType,
    DeploymentMetricsType,
    DeploymentType,
    DeployTokenType,
    DeregisterPreviewType,
    ForceRedeployPreviewType,
    ManifestDiffEntryType,
    PreviewEnvironmentCountsType,
    PreviewEnvironmentType,
    ReleaseNotesType,
    ScheduledJobRunType,
    TaskRunType,
    WorkloadPodStatusBucketType,
    WorkloadPodSummaryType,
    agent_run_to_type,
    app_domain_to_type,
    app_env_to_type,
    certificate_info_to_type,
    command_run_to_type,
    deploy_token_to_type,
    deployment_log_to_type,
    deployment_status_reason,
    deployment_to_type,
    dns_record_to_type,
    identity_binding_to_type,
    pod_info_to_type,
    preview_to_type,
    release_notes_to_type,
    scheduled_job_run_to_type,
    task_run_to_type,
)
from astrolift_lifecycle.scopes import (
    command_run_app_scope,
    deployment_app_scope,
    scheduled_job_run_app_scope,
    task_run_app_scope,
)
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from core.cluster_observability import (
    ClusterObservabilityError,
    list_app_pods,
    namespace_for_app,
    namespace_for_environment,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, check_platform_operator, require_permission
from core.schema.enums import ObservabilityPanelReason
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)

# Audit-log ``action`` values that make up the approval timeline for a
# deployment (#419). Kept here (rather than scraping all
# ``deployment.*`` events) so promotion / rollback / redeploy don't
# pollute the approval-history panel.
_APPROVAL_LIFECYCLE_ACTIONS = (
    "deployment.start",
    "deployment.approve",
    "deployment.approve_by_token",
    "deployment.reject",
    "deployment.reject_by_token",
    "deployment.abort",
)


def _actor_user_id() -> int | None:
    """The tenant's acting user, for callers with no request (token, test)."""
    tenant = get_current_tenant()
    return tenant.actor_user_id if tenant else None


def _viewer_user_id(info: Info) -> int | None:
    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) if request else None
    if user is not None and getattr(user, "is_authenticated", False):
        return user.pk
    return None


# Ordering for the workload status grid (#429). Worst-first so the
# incident-responder sees the actionable buckets at the top. Anything
# not in the list sorts to the tail in alphabetical order.
_WORKLOAD_STATUS_ORDER = (
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "CreateContainerError",
    "InvalidImageName",
    "OOMKilled",
    "Error",
    "Unknown",
    "Pending",
    "Terminating",
    "ContainerCreating",
    "Running",
    "Succeeded",
)


def _event_to_type(ev) -> AppPodEventType | None:
    """Project a ``ClusterEvent`` (provider SDK dataclass) onto the
    GraphQL ``AppPodEventType`` (#666).  Returns None for a null input
    so the caller can hand the result straight to ``pod_info_to_type``.
    """
    if ev is None:
        return None
    return AppPodEventType(
        reason=getattr(ev, "reason", "") or "",
        message=getattr(ev, "message", "") or "",
        type=getattr(ev, "type", "") or "Warning",
        count=int(getattr(ev, "count", 0) or 0),
        last_seen=getattr(ev, "last_seen", "") or "",
    )


def _list_pods_for_app(app_slug: str, *, org_id: int | None, environment_name: str | None = None) -> list:
    """Resolve cluster + namespace for an app and ask the driver for
    live pods.

    Extracted from ``astrolift_app_pods`` (#429) so the workload-
    detail breakdown resolver shares the exact same resolution rules
    (env-named cluster and namespace preferred → default cluster and
    ``namespace_for_app``).
    Returns an empty list on any kind of cluster-side failure so the
    UI stays renderable.

    Apps only. An ``AgentBox`` slug is answered by ``_list_pods_for_box``
    behind its own resolver (#129), because the two surfaces answer to
    different grants and the callers of this one are all gated on
    ``app.read_logs``.
    """
    app = (
        RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
        .filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
        .first()
    )
    if app is None:
        return []

    cluster = None
    namespace = namespace_for_app(app)
    if environment_name:
        env = (
            AppEnvironment.objects.select_related("tenant_cluster")
            .filter(
                registered_app=app,
                name=environment_name,
                deleted_at__isnull=True,
            )
            .first()
        )
        cluster = env.tenant_cluster if env and env.tenant_cluster_id else None
        if cluster is not None:
            # The environment's own namespace when it has one (#1922).
            namespace = namespace_for_environment(env)
    if cluster is None:
        cluster = app.default_tenant_cluster
    if cluster is None or not getattr(cluster, "is_active", True):
        return []

    try:
        return list(
            list_app_pods(
                cluster=cluster,
                namespace=namespace,
                app_slug=app.slug,
            )
        )
    except ClusterObservabilityError:
        return []
    except Exception:  # noqa: BLE001 — k8s lib raises many subtypes
        # Cluster transient errors (timeouts, 5xx) keep the UI alive;
        # the platform-event log carries the diagnostic.
        return []


def _list_pods_for_box(box_slug: str, *, org_id: int | None) -> list:
    """Live pods for an agent box (#129).

    A box's pod carries ``astrolift.dev/app=<box.guid>`` — the box render
    keys that label to the guid precisely so the platform's existing pod
    and log surfaces find it with no box-specific selector. So the guid,
    not the slug, is what goes to the driver here.

    Org-filtered explicitly: ``@tenant_scoped`` asserts a tenant, it does
    not filter, and a by-slug fetch that trusted the slug alone would
    list another org's pods. Degrades to ``[]`` on every cluster-side
    failure, exactly as the app path does.
    """
    from astrolift_agents.models import AgentBox
    from astrolift_agents.services.agent_box import box_namespace
    from astrolift_agents.services.agent_cluster import resolve_agent_cluster

    box = (
        AgentBox.objects.select_related("organization")
        .filter(slug=box_slug, organization_id=org_id, deleted_at__isnull=True)
        .first()
    )
    if box is None:
        return []

    try:
        cluster = resolve_agent_cluster(box.organization)
    except Exception:  # noqa: BLE001 — an org with no agent cluster has no pods
        return []
    if cluster is None or not getattr(cluster, "is_active", True):
        return []

    try:
        return list(
            list_app_pods(
                cluster=cluster,
                namespace=box.namespace or box_namespace(box),
                app_slug=str(box.guid),
            )
        )
    except ClusterObservabilityError:
        return []
    except Exception:  # noqa: BLE001 — k8s lib raises many subtypes
        return []


def _recent_pod_warnings_for_app(
    app_slug: str,
    *,
    org_id: int | None,
    environment_name: str | None = None,
) -> dict[str, object]:
    """Build a ``pod_name → most-recent Warning event`` map for an app
    (#666).

    Resolves the same cluster + namespace as ``_list_pods_for_app`` then
    calls the cluster's ``list_events`` driver method, filtering to
    events whose ``involved_object`` is a ``Pod/...`` string.  Returns
    the map keyed by pod name (the segment after the slash); when two
    events target the same pod the highest ``last_seen`` wins.

    Driver failures degrade to an empty dict — the page renders pods
    without inline error chips rather than 502ing the whole list.
    """
    app = (
        RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
        .filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
        .first()
    )
    if app is None:
        return {}

    cluster = None
    namespace = namespace_for_app(app)
    if environment_name:
        env = (
            AppEnvironment.objects.select_related("tenant_cluster")
            .filter(
                registered_app=app,
                name=environment_name,
                deleted_at__isnull=True,
            )
            .first()
        )
        cluster = env.tenant_cluster if env and env.tenant_cluster_id else None
        if cluster is not None:
            namespace = namespace_for_environment(env)
    if cluster is None:
        cluster = app.default_tenant_cluster
    if cluster is None or not getattr(cluster, "is_active", True):
        return {}

    from core.cluster_observability import (
        ClusterObservabilityError,
        list_app_pod_warning_events,
    )

    try:
        events = list_app_pod_warning_events(cluster=cluster, namespace=namespace)
    except ClusterObservabilityError:
        return {}
    except Exception:  # noqa: BLE001 — k8s lib raises many subtypes
        return {}

    out: dict[str, object] = {}
    for ev in events or []:
        involved = getattr(ev, "involved_object", "") or ""
        if "/" not in involved:
            continue
        kind, name = involved.split("/", 1)
        if kind.strip().lower() != "pod" or not name.strip():
            continue
        existing = out.get(name)
        # Newer last_seen wins.  String comparison is correct for
        # RFC 3339 timestamps.
        if existing is None or getattr(existing, "last_seen", "") < getattr(ev, "last_seen", ""):
            out[name] = ev
    return out


def _bucket_pods_by_status(pods: list) -> list[WorkloadPodStatusBucketType]:
    """Group ``PodInfo`` rows into status buckets for the workload
    status grid (#429). ``percent`` is rounded to one decimal place so
    the UI doesn't render fractions like ``33.3333%``."""
    total = len(pods)
    by_status: dict[str, list] = {}
    for pod in pods:
        key = (pod.status or "Unknown").strip() or "Unknown"
        by_status.setdefault(key, []).append(pod)

    def _sort_key(status: str) -> tuple[int, str]:
        if status in _WORKLOAD_STATUS_ORDER:
            return (_WORKLOAD_STATUS_ORDER.index(status), status)
        return (len(_WORKLOAD_STATUS_ORDER) + 1, status)

    buckets: list[WorkloadPodStatusBucketType] = []
    for status in sorted(by_status, key=_sort_key):
        rows = by_status[status]
        count = len(rows)
        percent = round((count / total) * 100.0, 1) if total else 0.0
        # Sort pods within a bucket by age (oldest first) — operators
        # usually want the long-running pods at the top so a fresh
        # pod thrashing into CrashLoopBackOff is visually separable.
        rows = sorted(rows, key=lambda p: (p.age is None, p.age))
        buckets.append(
            WorkloadPodStatusBucketType(
                status=status,
                count=count,
                percent=percent,
                pods=[
                    WorkloadPodSummaryType(
                        name=p.name,
                        age=p.age,
                        ready=bool(p.ready),
                    )
                    for p in rows
                ],
            )
        )
    return buckets


def _compute_manifest_diff(snap_a: dict, snap_b: dict) -> list[ManifestDiffEntryType]:
    """Flat JSON-patch list for the manifest diff surface (#737).

    Walks the top-level keys of both snapshots.  For each key:
    - missing in A, present in B → ``add``
    - present in A, missing in B → ``remove``
    - different value → ``replace``

    Sub-document diffing is deferred to a future iteration; the FE
    renders the per-key diff in a ``<pre>`` block for now."""
    entries: list[ManifestDiffEntryType] = []
    all_keys = sorted(set(snap_a) | set(snap_b))
    for key in all_keys:
        in_a = key in snap_a
        in_b = key in snap_b
        if in_a and not in_b:
            entries.append(ManifestDiffEntryType(op="remove", path=key, before=snap_a[key], after=None))
        elif not in_a and in_b:
            entries.append(ManifestDiffEntryType(op="add", path=key, before=None, after=snap_b[key]))
        elif snap_a[key] != snap_b[key]:
            entries.append(
                ManifestDiffEntryType(op="replace", path=key, before=snap_a[key], after=snap_b[key])
            )
    return entries


@strawberry.type
class CloudOrphanType:
    """A platform-owned cloud resource with no live owning DB row (#995).

    Carries enough identity to reap it: the cloud ``identifier`` (handle),
    ``kind``, which managed ``cluster_slug`` can reap it, the ``reap_key`` the
    ``reapCloudOrphan`` mutation dispatches on, and an operator-facing
    ``reason``.
    """

    kind: str
    identifier: str
    classification: str
    cluster_slug: str
    reap_key: str
    reason: str


@strawberry.type
class CloudOrphanReportType:
    orphans: list[CloudOrphanType]
    scanned_kinds: list[str]
    # Kinds that couldn't be fully enumerated (unsupported driver / list
    # error). When non-empty the scan is partial — do NOT read it as clean.
    incomplete_kinds: list[str]
    complete: bool


def _deployments_qs(
    *,
    app_slug: str | None,
    environment_name: str | None,
    status: str | None = None,
    statuses: list[str] | None = None,
    is_preview: bool | None = None,
    search: str | None = None,
):
    """Filtered, unordered deployment stream for the caller's org.

    Shared by the list field and its paginated sibling so the two can
    never disagree about what a deployment row is. Ordering is
    deliberately not applied here — ``keyset_page`` imposes it from
    the seek key.
    """
    # Org-scope to the caller's tenant (Deployment reaches the org via
    # registered_app; the default manager is not tenant-aware).
    # Fails closed (empty) when org_id is None (#1183).
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = Deployment.objects.select_related("registered_app", "app_environment", "workload").filter(
        # Deregistered / torn-down apps are soft-deleted; their
        # deployment rows aren't, so without this they'd keep showing
        # in the list. Single-deployment + approval-history queries are
        # id-scoped (a direct link the operator already has), so they
        # intentionally stay fetchable and don't need this filter.
        registered_app__deleted_at__isnull=True,
        registered_app__organization_id=org_id,
    )
    if app_slug:
        qs = qs.filter(registered_app__slug=app_slug)
    if environment_name:
        qs = qs.filter(app_environment__name=environment_name)
    if status:
        qs = qs.filter(status=status)
    if statuses:
        qs = qs.filter(status__in=statuses)
    if is_preview is not None:
        # A preview deployment is one raised from a pull request; the
        # /deployments tabs split on exactly this (`prNumber > 0`) and
        # could not express it server-side before.
        qs = qs.filter(pr_number__gt=0) if is_preview else qs.filter(pr_number=0)
    if search:
        qs = qs.filter(
            search_q(
                search,
                "registered_app__slug",
                "registered_app__name",
                "app_environment__name",
                "commit_sha",
                "commit_message",
                "branch",
                "image_tag",
            )
        )
    return visible_operation_rows(
        qs, Permission.APP_READ, app_path="registered_app", approvals_field="approvals_received"
    )


def _scheduled_job_runs_qs(
    *,
    app_slug: str | None,
    environment_name: str | None,
    search: str | None = None,
    workload_slug: str | None = None,
):
    """Filtered, unordered cron-run stream for the caller's org.

    Shared by the list field and its paginated sibling so the two can
    never disagree about what a run row is. Ordering is deliberately
    not applied here — ``keyset_page`` imposes it from the seek key.

    ``workload_slug`` narrows to one scheduled job's own history, which
    is what a cronjob's page shows. Without it that surface fetched the
    app's newest runs and kept this workload's in the browser, so a job
    firing less often than its neighbours read as never having fired
    (#1512). ``app_slug`` alone is not enough: workload slugs are unique
    within an app, not across the org.
    """
    # Org-scope to the active tenant. ScheduledJobRun has no org FK of
    # its own (it hangs off workload → registered_app) and its default
    # manager is not tenant-aware, so without this a caller could read
    # another org's runs via a known app slug — slugs are unique only
    # within a tenant. Applied unconditionally so a null org matches
    # nothing (#1183 deny-by-default). Mirrors astrolift_task_runs
    # (#801, #1118).
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = ScheduledJobRun.objects.select_related(
        "workload", "workload__registered_app", "app_environment"
    ).filter(workload__registered_app__organization_id=org_id)
    if app_slug:
        qs = qs.filter(workload__registered_app__slug=app_slug)
    if workload_slug:
        qs = qs.filter(workload__slug=workload_slug)
    if environment_name:
        qs = qs.filter(app_environment__name=environment_name)
    if search:
        qs = qs.filter(
            search_q(
                search,
                "workload__registered_app__slug",
                "workload__registered_app__name",
                "workload__slug",
                "app_environment__name",
                "status",
                "k8s_job_name",
            )
        )
    return visible_operation_rows(qs, Permission.APP_READ_LOGS, app_path="workload__registered_app")


def _command_runs_qs(*, app_slug: str | None, search: str | None = None):
    """Filtered, unordered one-off-exec stream for the caller's org.

    Shared by the list field and its paginated sibling; ordering is
    left to ``keyset_page``.
    """
    # Org-scope to the active tenant — CommandRun's default manager is
    # not tenant-aware and its registered_app slug is unique only
    # within a tenant, so an unscoped query would leak other orgs' runs
    # (#1118). Reaches org via the direct registered_app FK; applied
    # unconditionally so a null org matches nothing (#1183).
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = CommandRun.objects.select_related("registered_app", "workload", "invoked_by").filter(
        registered_app__organization_id=org_id
    )
    if app_slug:
        qs = qs.filter(registered_app__slug=app_slug)
    if search:
        qs = qs.filter(
            search_q(
                search,
                "registered_app__slug",
                "registered_app__name",
                "workload__slug",
                "invoked_by__username",
            )
        )
    return qs


def _preview_environments_qs(
    *,
    app_slug: str | None,
    search: str | None = None,
    statuses: list[str] | None = None,
):
    """Filtered, unordered preview stream for the caller's org.

    Shared by the list field and its paginated sibling; ordering is
    left to ``keyset_page``. Cost enrichment (``_preview_with_cost``)
    deliberately happens on the *sliced* rows, never here — it makes
    a live cluster call per row.
    """
    # Org-scope to the caller's tenant (PreviewEnvironment reaches the
    # org via registered_app). Fails closed when org_id is None (#1183).
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = PreviewEnvironment.objects.select_related(
        "registered_app",
        "registered_app__organization",
        "registered_app__default_tenant_cluster",
        "app_environment__tenant_cluster",
        # ``pinned_by_email`` on the type joins to the actor who placed
        # the pin (#1399); without this the page fans out one extra
        # query per pinned row.
        "pinned_by",
    ).filter(registered_app__organization_id=org_id)
    if app_slug:
        qs = qs.filter(registered_app__slug=app_slug)
    if statuses:
        # The Previews tab's status pills (#1241). The default view is
        # "everything except torn_down", which is a negation and had no
        # expression at all before this, so the migration to server pagination
        # had to drop the pills rather than page a client-side predicate.
        qs = qs.filter(status__in=statuses)
    if search:
        qs = qs.filter(
            search_q(
                search,
                "registered_app__slug",
                "registered_app__name",
                "branch",
                "hostname",
                "commit_sha",
                "status",
            )
        )
    return visible_operation_rows(qs, Permission.APP_READ, app_path="registered_app")


def _app_deploy_tokens_qs(*, app_slug: str, search: str | None = None):
    """Filtered, unordered deploy-token list for one app in the
    caller's org.

    Shared by the list field and its paginated sibling; ordering is
    left to ``keyset_page``.
    """
    # Org-scope to the caller's tenant (DeployToken reaches the org via
    # registered_app; slugs are unique only within an org). Fails closed
    # when org_id is None (#1183).
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = DeployToken.objects.select_related("registered_app").filter(
        registered_app__slug=app_slug,
        registered_app__organization_id=org_id,
        deleted_at__isnull=True,
    )
    if search:
        qs = qs.filter(
            search_q(
                search,
                "name",
                "token_last_4",
                "last_used_ip",
                "last_used_agent",
            )
        )
    return qs


def _task_runs_qs(
    *,
    app_slug: str | None,
    workload_slug: str | None,
    status: str | None = None,
    search: str | None = None,
):
    """Filtered, unordered task-run stream for the caller's org.

    Shared by the list field and its paginated sibling so the two can
    never disagree about what a run row is. Ordering is deliberately
    not applied here — ``keyset_page`` imposes it from the seek key.
    """
    # Org-scope to the active tenant. TaskRun has no organization FK
    # of its own (it hangs off workload → registered_app), and
    # @tenant_scoped only asserts a tenant exists — it does not filter.
    # Without this a caller could read another org's runs by passing a
    # known app/workload slug (mirrors astrolift_agent_runs #798).
    # Applied unconditionally so a null org matches nothing (#1183).
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = TaskRun.objects.select_related(
        "workload",
        "workload__registered_app",
        "app_environment",
        "triggered_by_user",
    ).filter(workload__registered_app__organization_id=org_id)
    if app_slug:
        qs = qs.filter(workload__registered_app__slug=app_slug)
    if workload_slug:
        qs = qs.filter(workload__slug=workload_slug)
    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(
            search_q(
                search,
                "workload__registered_app__slug",
                "workload__registered_app__name",
                "workload__slug",
                "status",
                "k8s_job_name",
                "triggered_by_user__username",
            )
        )
    return visible_operation_rows(qs, Permission.APP_READ_LOGS, app_path="workload__registered_app")


def _agent_runs_qs(
    *,
    app_slug: str | None,
    workload_slug: str | None,
    project_slug: str | None,
    status: str | None = None,
    search: str | None = None,
):
    """Filtered, unordered agent-run stream for the caller's org.

    Shared by the list field and its paginated sibling; ordering is
    left to ``keyset_page``.

    Org-scoped: AgentRun has no organization FK of its own (it hangs
    off ``workload → registered_app``), so the queryset is filtered
    to the caller's active tenant via ``registered_app__organization``.
    Without it the ``app_slug`` / ``project_slug`` filters would leak
    across orgs — both slugs are only unique *within* a tenant, so a
    caller could read another org's runs by passing a known slug
    (``@tenant_scoped`` asserts a tenant exists but does not filter).
    The clause is unconditional, so a null org matches nothing (#1183).
    """
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = AgentRun.objects.select_related(
        "workload",
        "workload__registered_app",
        "app_environment",
        "triggered_by_user",
    ).filter(workload__registered_app__organization_id=org_id)
    if app_slug:
        qs = qs.filter(workload__registered_app__slug=app_slug)
    if workload_slug:
        qs = qs.filter(workload__slug=workload_slug)
    if project_slug:
        qs = qs.filter(workload__registered_app__project__slug=project_slug)
    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(
            search_q(
                search,
                "workload__registered_app__slug",
                "workload__registered_app__name",
                "workload__slug",
                "status",
                "k8s_pod_name",
                "triggered_by_user__username",
            )
        )
    return visible_operation_rows(qs, Permission.APP_READ, app_path="workload__registered_app")


@strawberry.type
class LifecycleQuery:
    @strawberry.field
    @require_permission(Permission.APP_DELETE)
    def scan_cloud_orphans(self, info: Info) -> CloudOrphanReportType:
        """Read-only orphan-detection scan (#995): platform-owned cloud
        resources with no live owner row. Install-wide (not tenant-scoped):
        it reads every org's managed clusters and services, so only the
        platform operator may run it. APP_DELETE alone is not enough, since
        every org's admins hold it (#1978). Reaping is a separate, guarded
        follow-up; this never deletes."""
        check_platform_operator(info.context.request.user, gate=Permission.APP_DELETE)
        from astrolift_operations.services.orphan_reaper import scan_orphans

        report = scan_orphans()
        return CloudOrphanReportType(
            orphans=[
                CloudOrphanType(
                    kind=o.kind,
                    identifier=o.identifier,
                    classification=o.classification,
                    cluster_slug=o.cluster_slug,
                    reap_key=o.reap_key,
                    reason=o.reason,
                )
                for o in report.orphans
            ],
            scanned_kinds=report.scanned_kinds,
            incomplete_kinds=report.incomplete_kinds,
            complete=report.complete,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_environments(self, info: Info, app_slug: str | None = None) -> list[AppEnvironmentType]:
        # Org-scope to the caller's tenant: AppEnvironment reaches the org
        # through registered_app, and @tenant_scoped only asserts a tenant
        # exists — it does not filter. Fails closed (empty) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        qs = (
            AppEnvironment.objects.select_related(
                "registered_app", "tenant_cluster", "tenant_cluster__provider_plugin", "managed_domain"
            )
            .prefetch_related("settings")
            .filter(registered_app__organization_id=org_id)
            .order_by("registered_app__slug", "name")
        )
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        qs = visible_operation_rows(qs, Permission.APP_READ, environment_path="self")
        return [app_env_to_type(e) for e in qs[:300]]

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_environments_page(
        self,
        info: Info,
        app_slug: str | None = None,
        search: str | None = None,
        filter: EnvironmentsListFilterInput | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> PageType[AppEnvironmentType]:
        """Environments on the list contract, numbered (spec 44 §5.1, #2155).

        ``astroliftEnvironments`` caps at 300 with no filter or search. This
        is the same org-scoped set of live environments, with ``filter``
        (kind, app, cluster, region, owner), ``search`` over the name, the
        app's slug and name and the cluster's slug and region, ``sort`` a
        multi-key spec over name, app, kind, cluster, region and created
        (default ``app,name``, the flat list's order), and an exact
        filtered ``totalCount``. Fails closed (empty) without a tenant.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        qs = (
            AppEnvironment.objects.select_related(
                "registered_app", "tenant_cluster", "tenant_cluster__provider_plugin", "managed_domain"
            )
            .prefetch_related("settings")
            .filter(registered_app__organization_id=org_id, registered_app__deleted_at__isnull=True)
            if org_id is not None
            else AppEnvironment.objects.none()
        )
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        if search and search.strip():
            qs = qs.filter(
                search_q(
                    search.strip(),
                    "name",
                    "registered_app__slug",
                    "registered_app__name",
                    "tenant_cluster__slug",
                    "tenant_cluster__region",
                )
            )
        qs = annotate_environments(qs).filter(
            filter_q(filter, ENVIRONMENTS_FILTERS, me=tenant.actor_user_id if tenant else None)
        )
        order_by = resolve_list_sort(sort, ENVIRONMENTS_SORT_KEYS, default=ENVIRONMENTS_DEFAULT_SORT)
        qs = visible_operation_rows(qs, Permission.APP_READ, environment_path="self")
        return numbered_page(qs, order_by=order_by, page=page, page_size=page_size).map(app_env_to_type)

    @strawberry.field(
        deprecation_reason=("Caps at 200 rows with no way to reach the 201st. Use astroliftDeploymentsPage.")
    )
    @require_permission(Permission.APP_READ, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_deployments(
        self,
        info: Info,
        app_slug: str | None = None,
        environment_name: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentType]:
        qs = _deployments_qs(app_slug=app_slug, environment_name=environment_name).order_by("-created_at")
        viewer = _viewer_user_id(info)
        return [deployment_to_type(d, viewer_user_id=viewer) for d in qs[: max(1, min(limit, 200))]]

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_deployments_page(
        self,
        info: Info,
        app_slug: str | None = None,
        environment_name: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        is_preview: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
        filter: DeploymentsListFilterInput | None = None,
        sort: str | None = None,
    ) -> PageType[DeploymentType]:
        """Cursor-paginated deployment history (#1235).

        Replaces ``astroliftDeployments``, whose 200-row cap made the
        201st deployment unreachable from the UI rather than merely
        slow to reach — the operator-visible bug that motivated #1230.

        Seek key is ``(-created_at, -guid)``; ``search`` matches the app,
        environment, branch, image tag, and commit the operator is most
        likely to be hunting for.

        ``statuses`` and ``is_preview`` exist because the /deployments
        tabs are status *groups* ("active" is in-flight plus running,
        minus the approval queue and previews), which the singular
        ``status`` cannot express. Without them the surface has to fetch
        everything and split it client-side — which is exactly the
        capped-then-filtered pattern #1230 is removing.

        The list contract (#2155): ``filter`` takes who started it
        (``triggeredBy``, ``"me"`` is the viewer), the trigger kind and a
        start-time window; ``sort`` is ``-created`` (the default), ``created``,
        ``-started`` or ``started``, where started reads the creation time
        for a deploy that has not started. Any other key is refused.
        """
        sort_field, descending, cursor_scope = cursor_sort(
            sort, DEPLOYMENTS_SORT_FIELDS, default=DEPLOYMENTS_DEFAULT_SORT, list_name="deployments"
        )
        qs = annotate_deployments(
            _deployments_qs(
                app_slug=app_slug,
                environment_name=environment_name,
                status=status,
                statuses=statuses,
                is_preview=is_preview,
                search=search,
            )
        ).filter(filter_q(filter, DEPLOYMENTS_FILTERS, me=_viewer_user_id(info) or _actor_user_id()))
        page = keyset_page(
            qs,
            cursor=after,
            limit=limit,
            sort_field=sort_field,
            descending=descending,
            cursor_scope=cursor_scope,
        )
        viewer = _viewer_user_id(info)
        return page.map(lambda d: deployment_to_type(d, viewer_user_id=viewer))

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=deployment_app_scope("id"), operation=deployment_operation("id")
    )
    @tenant_scoped()
    def astrolift_deployment(self, info: Info, id: str) -> DeploymentType | None:
        """Single deployment by guid, scoped to the caller's org (#1118).

        Deployment has no organization FK of its own (it reaches the
        tenant through ``registered_app``) and its default manager is not
        tenant-aware, so an unscoped ``filter(guid=id)`` would return
        another org's deployment — guids are globally unique. The join
        filter is what enforces tenancy here (``@tenant_scoped`` only
        asserts a tenant exists); it fails closed to ``None`` on a
        sibling-org or unknown id. Deregistered (soft-deleted) apps' rows
        stay fetchable by direct link — the join reads the joined row
        regardless of the app's ``deleted_at``.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return None
        d = (
            Deployment.objects.select_related("registered_app", "app_environment", "workload")
            .filter(guid=id, registered_app__organization_id=org_id)
            .first()
        )
        return deployment_to_type(d, viewer_user_id=_viewer_user_id(info)) if d else None

    @strawberry.field
    @require_permission(
        Permission.APP_READ,
        scope=deployment_app_scope("deployment_id"),
        operation=deployment_operation("deployment_id"),
    )
    @tenant_scoped()
    def astrolift_deployment_approval_history(
        self,
        info: Info,
        deployment_id: str,
    ) -> list[DeploymentApprovalHistoryEntryType]:
        """Approve / reject / abort audit trail for one deployment (#419).

        Returns ``AuditEvent`` rows whose ``action`` is one of the
        deployment-lifecycle actions and whose ``target_id`` (set by
        the resolver's audit-decorator ``target`` hook) matches the
        deployment guid, plus the ``deployment.start`` row whose
        extras payload stamped this deployment's guid under
        ``data['deployment_id']`` (the start row can't carry the guid
        as ``target_id`` because the guid is minted inside the
        resolver). Sorted oldest-first so the UI can render a
        chronological timeline.

        Tenant scoping rides on the deployment lookup — a request for
        a sibling-org deployment returns an empty list rather than
        leaking row counts. The audit rows are also held to the caller's
        org (#1955): ``@mutation_audit`` records the target from the
        input before the resolver's org check runs, so another org's
        refused ``approveDeployment`` on this guid writes a row with this
        ``target_id`` in that org.
        """
        from django.db.models import Q

        from astrolift_operations.models import AuditEvent

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        deployment = (
            Deployment.objects.filter(
                guid=deployment_id, deleted_at__isnull=True, registered_app__organization_id=org_id
            )
            .only("id", "guid", "aborted_reason")
            .first()
        )
        if deployment is None:
            return []

        deployment_guid = str(deployment.guid)
        events = (
            AuditEvent.objects.filter(action__in=_APPROVAL_LIFECYCLE_ACTIONS, organization_id=org_id)
            .filter(
                Q(target_id=deployment_guid) | Q(data__deployment_id=deployment_guid),
            )
            .order_by("occurred_at")[:200]
        )
        out: list[DeploymentApprovalHistoryEntryType] = []
        for e in events:
            data = e.data or {}
            reason = ""
            # abort/reject mutations stuff the operator's free-form
            # reason onto ``data['reason']`` via the @mutation_audit
            # extras hook. We also fall back to the deployment row's
            # ``aborted_reason`` for the terminal abort entry so the
            # panel reads correctly even when the audit row was
            # written by an older code path.
            if isinstance(data.get("reason"), str):
                reason = data["reason"]
            elif e.action in {
                "deployment.abort",
                "deployment.reject",
                "deployment.reject_by_token",
            }:
                reason = deployment.aborted_reason or ""
            out.append(
                DeploymentApprovalHistoryEntryType(
                    id=GUID(str(e.guid)),
                    action=e.action,
                    decision=e.decision,
                    actor_kind=e.actor_kind,
                    actor_id=e.actor_id or "",
                    actor_display=e.actor_display or "",
                    occurred_at=e.occurred_at,
                    reason=reason,
                )
            )
        return out

    @strawberry.field
    @require_permission(
        Permission.APP_READ,
        scope=deployment_app_scope("deployment_id"),
        operation=deployment_operation("deployment_id"),
    )
    @tenant_scoped()
    def astrolift_deployment_release_notes(self, info: Info, deployment_id: str) -> ReleaseNotesType | None:
        """Merged-PR descriptions + non-merge commit subjects between the
        previous-successful deploy's SHA and this deploy's SHA (#738).

        Returns None when:
        - the deployment has no commit_sha
        - there is no prior successful deployment to diff against
        - the app has no usable source connection
        - the SCM call fails (logged; caller falls back to commitMessage)
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return None
        deployment = (
            Deployment.objects.select_related(
                "registered_app",
                "registered_app__organization",
                "app_environment",
            )
            .filter(guid=deployment_id, deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if deployment is None or not deployment.commit_sha:
            return None

        head_sha = deployment.commit_sha
        app = deployment.registered_app
        env = deployment.app_environment

        # The diff base is the most-recent PRIOR deploy that was live.
        # A prior live deploy is now flipped to SUPERSEDED when a newer
        # one reaches running (#1103), so filtering on RUNNING alone found
        # nothing and broke release notes — include SUPERSEDED too.
        prior = (
            Deployment.objects.filter(
                registered_app=app,
                app_environment=env,
                status__in=[Deployment.Status.RUNNING, Deployment.Status.SUPERSEDED],
                commit_sha__gt="",
                succeeded_at__lt=deployment.created_at,
                deleted_at__isnull=True,
            )
            .exclude(guid=deployment.guid)
            .order_by("-created_at")
            .values_list("commit_sha", flat=True)
            .first()
        )
        if not prior:
            return None
        base_sha = prior

        from astrolift_registry.services.manifest_sync import _pick_source_connection
        from astrolift_scm.providers.release_notes import fetch_release_notes

        connection = _pick_source_connection(app)
        if connection is None:
            return None

        rn = fetch_release_notes(connection, app=app, base_sha=base_sha, head_sha=head_sha)
        return release_notes_to_type(rn) if rn else None

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_compare_deployments(
        self, info: Info, id_a: str, id_b: str
    ) -> DeploymentComparisonType | None:
        """Commit range + manifest diff + image diff between two deploys (#737).

        Both deployments must belong to the same app (within the caller's
        tenant).  Returns None when either deployment is not found.

        Commit range / compare URL require both deploys to have a
        ``commit_sha``; falls back to empty strings when not available.

        Manifest diff is a JSON-patch list derived from the
        ``rendered_manifest_snapshot`` fields (null on pre-#737 rows →
        empty diff).

        Image diff summary is best-effort: compared ``image_tag`` +
        ``image_digest`` values from both deployments."""
        from astrolift_lifecycle.models import Deployment

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None

        def _get_deploy(guid: str) -> Deployment | None:
            qs = Deployment.objects.filter(
                guid=guid, deleted_at__isnull=True, registered_app__organization_id=org_id
            ).select_related("registered_app", "app_environment")
            return visible_operation_rows(
                qs, Permission.APP_READ, approvals_field="approvals_received"
            ).first()

        dep_a = _get_deploy(id_a)
        dep_b = _get_deploy(id_b)
        if dep_a is None or dep_b is None:
            return None

        # Commit range -----------------------------------------------
        base_sha = dep_a.commit_sha or ""
        head_sha = dep_b.commit_sha or ""
        compare_url = ""
        if base_sha and head_sha:
            from astrolift_registry.services.manifest_sync import _pick_source_connection

            conn = _pick_source_connection(dep_a.registered_app)
            if conn is not None and hasattr(conn, "compare_url"):
                try:
                    compare_url = conn.compare_url(base_sha=base_sha, head_sha=head_sha) or ""
                except Exception:  # noqa: BLE001
                    compare_url = ""

        # Manifest diff ----------------------------------------------
        snap_a: dict = dep_a.rendered_manifest_snapshot or {}
        snap_b: dict = dep_b.rendered_manifest_snapshot or {}
        manifest_diff = _compute_manifest_diff(snap_a, snap_b)

        # Image diff summary -----------------------------------------
        image_diff_summary = ""
        if dep_a.image_digest or dep_b.image_digest:
            a_tag = dep_a.image_tag or dep_a.image_digest or "(unknown)"
            b_tag = dep_b.image_tag or dep_b.image_digest or "(unknown)"
            if a_tag != b_tag:
                image_diff_summary = f"{a_tag} → {b_tag}"
            else:
                image_diff_summary = f"same image ({a_tag})"

        return DeploymentComparisonType(
            deployment_a_id=GUID(str(dep_a.guid)),
            deployment_b_id=GUID(str(dep_b.guid)),
            base_sha=base_sha,
            head_sha=head_sha,
            compare_url=compare_url,
            manifest_diff=manifest_diff,
            image_diff_summary=image_diff_summary,
        )

    @strawberry.field
    @require_permission(
        Permission.APP_READ_LOGS,
        scope=deployment_app_scope("deployment_id"),
        operation=deployment_operation("deployment_id"),
    )
    @tenant_scoped()
    def astrolift_deployment_log(self, info: Info, deployment_id: str) -> list[DeploymentLogEntryType]:
        # Look up the deployment by guid, scoped to the caller's org (the
        # default manager is not tenant-aware, so an unscoped guid lookup
        # would expose another org's deploy log; #1118), then return its
        # log entries.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        deployment = Deployment.objects.filter(
            guid=deployment_id, registered_app__organization_id=org_id
        ).first()
        if deployment is None:
            return []
        qs = DeploymentLog.objects.filter(deployment=deployment).order_by("occurred_at")
        return [deployment_log_to_type(e) for e in qs[:1000]]

    @strawberry.field(
        deprecation_reason=(
            "Caps at 500 rows with no way to reach the 501st. Use astroliftScheduledJobRunsPage."
        )
    )
    @require_permission(Permission.APP_READ_LOGS, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ_LOGS)
    @tenant_scoped()
    def astrolift_scheduled_job_runs(
        self,
        info: Info,
        app_slug: str | None = None,
        environment_name: str | None = None,
        limit: int = 100,
    ) -> list[ScheduledJobRunType]:
        qs = _scheduled_job_runs_qs(app_slug=app_slug, environment_name=environment_name).order_by(
            "-created_at"
        )
        return [scheduled_job_run_to_type(r) for r in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ_LOGS)
    @tenant_scoped()
    def astrolift_scheduled_job_runs_page(
        self,
        info: Info,
        app_slug: str | None = None,
        environment_name: str | None = None,
        workload_slug: str | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
        filter: ScheduledJobRunsFilterInput | None = None,
    ) -> PageType[ScheduledJobRunType]:
        """Cursor-paginated cron-run history (#1235).

        Replaces ``astroliftScheduledJobRuns``, whose 500-row cap put the
        older history of a frequently-scheduled job out of reach.

        Seek key is ``(-created_at, -guid)``; ``search`` matches the app,
        workload, environment, status, and the ``batch/v1`` Job name an
        operator reads off ``kubectl``.

        ``workload_slug`` narrows to one scheduled job, which is what a
        cronjob's own page shows (#1512). Pass ``app_slug`` with it:
        workload slugs are unique within an app, not across the org.

        ``filter`` (#2155) takes the run status, the trigger (``scheduled``
        or ``manual``) and who ran it now (``"me"`` is the viewer).
        """
        page = keyset_page(
            _scheduled_job_runs_qs(
                app_slug=app_slug,
                environment_name=environment_name,
                workload_slug=workload_slug,
                search=search,
            ).filter(
                filter_q(filter, SCHEDULED_JOB_RUNS_FILTERS, me=_viewer_user_id(info) or _actor_user_id())
            ),
            cursor=after,
            limit=limit,
        )
        return page.map(scheduled_job_run_to_type)

    @strawberry.field
    @require_permission(
        Permission.APP_READ_LOGS,
        scope=scheduled_job_run_app_scope("id"),
        operation=row_operation(
            "astrolift_lifecycle.ScheduledJobRun", "id", app_path="app_environment__registered_app"
        ),
    )
    @tenant_scoped()
    def astrolift_scheduled_job_run(self, info: Info, id: str) -> ScheduledJobRunType | None:
        """Single scheduled-job run by guid, for cold detail deep-links (#1118).

        Org-scoped through ``workload → registered_app`` — the join filter
        (not the model manager) is what enforces tenancy; it fails closed
        to ``None`` on a sibling-org or unknown id. See
        ``astrolift_scheduled_job_runs`` for the full rationale.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return None
        r = (
            ScheduledJobRun.objects.select_related("workload", "workload__registered_app", "app_environment")
            .filter(guid=id, workload__registered_app__organization_id=org_id)
            .first()
        )
        return scheduled_job_run_to_type(r) if r else None

    @strawberry.field(
        deprecation_reason=("Caps at 500 rows with no way to reach the 501st. Use astroliftCommandRunsPage.")
    )
    @require_permission(Permission.APP_READ_LOGS, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_command_runs(
        self,
        info: Info,
        app_slug: str | None = None,
        limit: int = 100,
    ) -> list[CommandRunType]:
        qs = _command_runs_qs(app_slug=app_slug).order_by("-created_at")
        return [command_run_to_type(r) for r in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_command_runs_page(
        self,
        info: Info,
        app_slug: str | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
        filter: CommandRunsFilterInput | None = None,
    ) -> PageType[CommandRunType]:
        """Cursor-paginated ``astro app exec`` history (#1235).

        Replaces ``astroliftCommandRuns``, whose 500-row cap made the
        older half of a busy app's exec audit trail unreachable — the
        forensic question this table exists to answer ("who ran what,
        when") is exactly the one that reaches back past the window.

        Seek key is ``(-created_at, -guid)``; ``search`` matches the app,
        workload, and the operator who invoked the command. ``filter``
        (#2155) takes who ran it (``invokedBy``, ``"me"`` is the viewer).
        """
        page = keyset_page(
            _command_runs_qs(app_slug=app_slug, search=search).filter(
                filter_q(filter, COMMAND_RUNS_FILTERS, me=_viewer_user_id(info) or _actor_user_id())
            ),
            cursor=after,
            limit=limit,
        )
        return page.map(command_run_to_type)

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS, scope=command_run_app_scope("id"))
    @tenant_scoped()
    def astrolift_command_run(self, info: Info, id: str) -> CommandRunType | None:
        """Single command (one-off exec) run by guid, for cold detail
        deep-links (#1118). Org-scoped via the direct ``registered_app`` FK;
        fails closed to ``None`` on a sibling-org or unknown id."""
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return None
        r = (
            CommandRun.objects.select_related("registered_app", "workload", "invoked_by")
            .filter(guid=id, registered_app__organization_id=org_id)
            .first()
        )
        return command_run_to_type(r) if r else None

    @strawberry.field(
        deprecation_reason=(
            "Caps at 200 rows with no way to reach the 201st, and prices "
            "every one of them on read. Use astroliftPreviewEnvironmentsPage."
        )
    )
    @require_permission(Permission.APP_READ, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_preview_environments(
        self, info: Info, app_slug: str | None = None
    ) -> list[PreviewEnvironmentType]:
        qs = _preview_environments_qs(app_slug=app_slug).order_by("-created_at")
        rows = list(qs[:200])
        return [_preview_with_cost(p) for p in rows]

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_preview_environments_page(
        self,
        info: Info,
        app_slug: str | None = None,
        search: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
        after: str | None = None,
        filter: PreviewEnvironmentsFilterInput | None = None,
        sort: str | None = None,
    ) -> PageType[PreviewEnvironmentType]:
        """Cursor-paginated preview environments (#1235).

        Replaces ``astroliftPreviewEnvironments``, whose 200-row cap hid
        older previews outright. The cap was also load-bearing for a
        second reason: every returned row costs one live pod listing plus
        a pricing lookup (``_preview_with_cost``), so the page limit —
        applied *before* that enrichment — is what bounds the fan-out.

        Seek key is ``(-created_at, -guid)``; ``search`` matches the app,
        branch, hostname, commit, and status.

        The list contract (#2155): ``filter`` takes the status, who opened
        it (``"me"`` is the viewer) and manual-or-PR; ``sort`` is one of
        ``created``, ``deployed`` (the last deploy, else creation) and
        ``ttl``, either direction, default ``-created``. Rows carry who
        opened them and, when failed, why; ``astroliftPreviewEnvironmentCounts``
        has the per-status totals.
        """
        sort_field, descending, cursor_scope = cursor_sort(
            sort, PREVIEW_SORT_FIELDS, default=PREVIEWS_DEFAULT_SORT, list_name="previews"
        )
        qs = annotate_previews(
            _preview_environments_qs(app_slug=app_slug, search=search, statuses=statuses)
        ).filter(filter_q(filter, PREVIEW_FILTERS, me=_viewer_user_id(info) or _actor_user_id()))
        page = keyset_page(
            qs,
            cursor=after,
            limit=limit,
            sort_field=sort_field,
            descending=descending,
            cursor_scope=cursor_scope,
        )
        reasons = _preview_deploy_failure_reasons(page.rows)
        return page.map(lambda p: _preview_with_cost(p, failure_reason=reasons.get(p.pk)))

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_preview_environment_counts(
        self,
        info: Info,
        app_slug: str | None = None,
        search: str | None = None,
    ) -> PreviewEnvironmentCountsType:
        """Previews per status over the page's app and search (#2155).

        One aggregate over the same org-scoped set the page walks, before
        any status filter, so the status pills can show their counts.
        """
        rows = dict(
            _preview_environments_qs(app_slug=app_slug, search=search)
            .order_by()
            .values_list("status")
            .annotate(n=Count("pk"))
        )
        return PreviewEnvironmentCountsType(
            total=sum(rows.values()),
            building=rows.get(PreviewEnvironment.Status.BUILDING.value, 0),
            running=rows.get(PreviewEnvironment.Status.RUNNING.value, 0),
            failed=rows.get(PreviewEnvironment.Status.FAILED.value, 0),
            torn_down=rows.get(PreviewEnvironment.Status.TORN_DOWN.value, 0),
        )

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_deployment_metrics(self, info: Info, window_days: int = 30) -> DeploymentMetricsType:
        """Aggregate rollout health for the last N days.

        Inputs are clamped to [1, 365] so callers can't ask for an
        unbounded scan. ``mean`` and ``p95`` use durations from
        terminal-state deployments only — in-flight rows have no
        duration yet.
        """
        window_days = max(1, min(int(window_days), 365))
        # One ``now`` for both the window cutoff and the day bucketing so the
        # per-day arrays partition exactly the same [since, now) span the
        # aggregates scan.
        now = timezone.now()
        since = now - timedelta(days=window_days)

        # Org-scope the aggregate to the caller's tenant — without this the
        # rollout-health numbers pool every org's deployments. Fails closed
        # (empty aggregate) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        visible_app_ids = visible_apps(
            RegisteredApp.objects.filter(organization_id=org_id, deleted_at__isnull=True),
            Permission.APP_READ,
        ).values_list("id", flat=True)
        qs = Deployment.objects.filter(
            created_at__gte=since,
            deleted_at__isnull=True,
            registered_app_id__in=visible_app_ids,
        )
        qs = visible_operation_rows(qs, Permission.APP_READ, approvals_field="approvals_received")

        in_flight_statuses = {
            Deployment.Status.PENDING_APPROVAL.value,
            Deployment.Status.PENDING.value,
            Deployment.Status.DEPLOYING.value,
            Deployment.Status.REDEPLOYING.value,
        }
        terminal_succeeded = {Deployment.Status.RUNNING.value}
        terminal_failed = {Deployment.Status.FAILED.value}
        terminal_rollback = {Deployment.Status.ROLLED_BACK.value}

        # Single pass over the queryset; we need counts, duration samples, and
        # each row's day bucket, so a values_list carrying created_at is the
        # right shape — one query backs every field below.
        rows = list(qs.values_list("status", "duration_seconds", "created_at"))
        total = len(rows)
        succeeded = sum(1 for s, _, _ in rows if s in terminal_succeeded)
        failed = sum(1 for s, _, _ in rows if s in terminal_failed)
        rolled_back = sum(1 for s, _, _ in rows if s in terminal_rollback)
        in_flight = sum(1 for s, _, _ in rows if s in in_flight_statuses)

        durations = [d for s, d, _ in rows if d is not None and d >= 0]
        mean_duration = sum(durations) / len(durations) if durations else None
        p95_duration: float | None = None
        if len(durations) >= 5:
            ordered = sorted(durations)
            idx = max(0, int(round(0.95 * (len(ordered) - 1))))
            p95_duration = float(ordered[idx])
        elif durations:
            # Small sample: fall back to the slowest observed value.
            p95_duration = float(max(durations))

        if total == 0:
            success_rate = -1.0
        else:
            success_rate = succeeded / total

        # Per-day buckets, oldest → newest. Bucket i spans
        # [since + i*day, since + (i+1)*day); created_at maps to
        # int((created_at - since)/day). created_at >= since (the filter), so
        # the index is never negative; clamp the created_at==now edge into the
        # last bucket so every counted row lands in exactly one.
        day = timedelta(days=1)
        daily_succeeded = [0] * window_days
        daily_failed = [0] * window_days
        daily_duration_sum = [0.0] * window_days
        daily_duration_count = [0] * window_days
        for status, duration, created_at in rows:
            bucket = min(int((created_at - since) / day), window_days - 1)
            if status in terminal_succeeded:
                daily_succeeded[bucket] += 1
            elif status in terminal_failed:
                daily_failed[bucket] += 1
            if duration is not None and duration >= 0:
                daily_duration_sum[bucket] += duration
                daily_duration_count[bucket] += 1
        daily_mean_duration_seconds = [
            (daily_duration_sum[i] / daily_duration_count[i]) if daily_duration_count[i] else None
            for i in range(window_days)
        ]

        return DeploymentMetricsType(
            window_days=window_days,
            total=total,
            succeeded=succeeded,
            failed=failed,
            rolled_back=rolled_back,
            in_flight=in_flight,
            success_rate=success_rate,
            mean_duration_seconds=mean_duration,
            p95_duration_seconds=p95_duration,
            daily_succeeded=daily_succeeded,
            daily_failed=daily_failed,
            daily_mean_duration_seconds=daily_mean_duration_seconds,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_app_health_summary(self, info: Info) -> list[AppHealthSummaryType]:
        """Per-app health rollup for the metrics dashboard.

        For every registered app in the org, returns the latest
        deployment's status + image tag, the env count, and a
        recent-failure flag (any non-running terminal deploy in the
        last 7 days). Apps with no deployments still appear, marked
        ``latest_deployment_status=None``.
        """
        recent_window = timezone.now() - timedelta(days=7)
        # Org-scope the per-app rollup to the caller's tenant — without this
        # it scans every org's apps. Fails closed (empty) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        apps = list(
            visible_apps(
                RegisteredApp.objects.filter(deleted_at__isnull=True, organization_id=org_id),
                Permission.APP_READ,
            ).order_by("slug")[:300]
        )
        if not apps:
            return []
        app_ids = [a.id for a in apps]
        from astrolift_registry.models import Workload

        workload_kinds = (
            Workload.objects.filter(
                registered_app_id__in=app_ids,
                deleted_at__isnull=True,
            )
            .values("registered_app_id")
            .annotate(
                workload_count=Count("id"),
                agent_count=Count("id", filter=Q(kind=Workload.Kind.AGENT)),
            )
        )
        agent_app_ids = {
            row["registered_app_id"] for row in workload_kinds if row["workload_count"] == row["agent_count"]
        }

        # Five queries total rather than three per app (#1237). This is a
        # dashboard-path resolver capped at 300 apps, so the loop form was
        # up to 901 round-trips on one page render. Each rollup is gathered
        # in one pass and joined in Python; separate queries rather than
        # annotations on the app queryset so the counts can't multiply
        # against each other across joins.
        visible_environments = visible_operation_rows(
            AppEnvironment.objects.filter(registered_app_id__in=app_ids, deleted_at__isnull=True),
            Permission.APP_READ,
            environment_path="self",
        )
        visible_deployments = visible_operation_rows(
            Deployment.objects.filter(registered_app_id__in=app_ids, deleted_at__isnull=True),
            Permission.APP_READ,
            approvals_field="approvals_received",
        )
        env_counts = dict(
            visible_environments.values_list("registered_app_id")
            .annotate(n=Count("id"))
            .values_list("registered_app_id", "n")
        )

        # DISTINCT ON gives the newest deployment per app in one query.
        # Postgres requires the ORDER BY to lead with the DISTINCT ON
        # expression, hence registered_app_id first.
        latest_by_app = {
            d.registered_app_id: d
            for d in visible_deployments.order_by("registered_app_id", "-created_at").distinct(
                "registered_app_id"
            )
        }

        failed_app_ids = set(
            visible_deployments.filter(
                created_at__gte=recent_window,
                status__in=[
                    Deployment.Status.FAILED.value,
                    Deployment.Status.ROLLED_BACK.value,
                ],
            ).values_list("registered_app_id", flat=True)
        )

        out: list[AppHealthSummaryType] = []
        for app in apps:
            latest = latest_by_app.get(app.id)
            out.append(
                AppHealthSummaryType(
                    app_slug=app.slug,
                    app_name=app.name,
                    primitive_kind=("agent" if app.id in agent_app_ids else "app"),
                    environment_count=env_counts.get(app.id, 0),
                    latest_deployment_status=(latest.status if latest else None),
                    latest_image_tag=(latest.image_tag if latest else ""),
                    last_deployed_at=(latest.created_at if latest else None),
                    has_recent_failure=app.id in failed_app_ids,
                )
            )
        return out

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_app_domains(
        self,
        info: Info,
        app_slug: str,
    ) -> list[AppDomainType]:
        # Org-scope to the caller's tenant (CustomDomain reaches the org via
        # registered_app; slugs are unique only within an org). Fails closed
        # when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        qs = (
            CustomDomain.objects.select_related("registered_app")
            .filter(
                registered_app__slug=app_slug,
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")[:100]
        )
        domains = list(qs)
        # #731 — lazy refresh of cached cert observability metadata.
        # Best-effort + bounded by a 1h TTL so the page render isn't
        # gated on a cloud round-trip for every read.  Errors swallow:
        # operators see the previously-cached value (or no chip on
        # first refresh failure).
        _refresh_cert_metadata_if_stale(domains, app_slug=app_slug, org_id=org_id)
        # #1621 — edge_auth_state is a function of (cluster, hostname), and
        # the domain row has no FK to a cluster. Resolve it once for the app
        # rather than per domain; the same helper the cert refresh above
        # uses, so both agree on which cluster an app's domains live on.
        cluster = _resolve_app_cluster(app_slug=app_slug, org_id=org_id, environment_name=None)
        return [app_domain_to_type(d, cluster=cluster) for d in domains]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_app_pods(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> list[AppPodType]:
        """Live pod state for an app from the runtime cluster.

        Cluster resolution prefers the named ``environment_name``'s
        ``tenant_cluster`` when given, falling back to the app's
        ``default_tenant_cluster``. Namespace mirrors what the
        manifest renderer uses (``app.k8s_namespace`` when set,
        else ``f"{org_slug}-{app_slug}"``).

        Returns an empty list (no GraphQL error) when:
          - the app has no cluster wired yet
          - the cluster row is misconfigured (missing kubeconfig,
            unknown auth method, etc.)
          - the K8s API call fails (cluster offline, network)

        The UI renders the empty list as "no pods yet" rather than
        an error state — there's no actionable thing for the user
        to do about a transient cluster outage and we don't want
        to break the page over it. Cluster outages surface via
        platform-event alerts instead.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        pods = _list_pods_for_app(app_slug, org_id=org_id, environment_name=environment_name)
        # #666 — surface most-recent Warning event per pod for inline
        # ImagePullBackOff / CrashLoopBackOff / OOMKilled triage.  The
        # event lookup is best-effort: empty dict on driver failure
        # means the rows render without chips.
        warnings = _recent_pod_warnings_for_app(app_slug, org_id=org_id, environment_name=environment_name)
        return [pod_info_to_type(p, recent_error_event=_event_to_type(warnings.get(p.name))) for p in pods]

    @strawberry.field
    @require_permission(Permission.AGENT_BOX_ATTACH)
    @tenant_scoped()
    def agent_box_pods(self, info: Info, slug: str) -> list[AppPodType]:
        """Live pods for an agent box (#129).

        Its own field rather than a branch inside ``astroliftAppPods``
        because the two answer to different grants, and the caller who
        needs this one is precisely the caller the app gate excludes.
        ``app_deployer`` holds ``agent.dispatch`` (so it may start a box)
        and ``agent_box.attach`` (so the relay admits it) but not
        ``app.read_logs``. The CLI resolves a pod before it dials, so
        routing that resolution through the app gate would leave that
        role able to start a box it can never reach — the complaint the
        ticket opens with, one layer down. ``require_permission`` ANDs,
        so admitting the box grant on the app resolver would mean
        weakening the app-pod gate to fix a box problem.

        Same degrade-to-empty contract as the app surface: a retired
        box, an org with no agent cluster, or an unreachable cluster
        renders as "no pods" rather than erroring the client.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        return [pod_info_to_type(p) for p in _list_pods_for_box(slug, org_id=org_id)]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_workload_pod_status_breakdown(
        self,
        info: Info,
        app_slug: str,
        workload_slug: str,
        environment_name: str | None = None,
    ) -> list[WorkloadPodStatusBucketType]:
        """Aggregate live pods for one workload into status buckets
        (#429).

        Powers the status grid at the top of the workload detail
        page. Filters the same ``list_app_pods`` payload the
        observability surface consumes (so a single k8s API hit
        backs both views) by ``PodInfo.workload`` — which the SDK
        pulls from the ``astrolift.io/workload`` label or the pod's
        owner-reference.

        Returns buckets ordered worst-first
        (``CrashLoopBackOff`` / ``ImagePullBackOff`` →
        ``Pending`` / ``Terminating`` → ``Running`` → other) so the
        UI's incident-response framing surfaces actionable rows at
        the top without sorting client-side.

        Behaviour mirrors ``astrolift_app_pods`` — empty list when
        the cluster is unwired / unreachable rather than raising,
        so the workload page stays renderable during a cluster
        outage. ``APP_READ_LOGS`` gates both surfaces so an operator
        with read-only access to deploys (but not logs) doesn't see
        pod names they couldn't tail anyway.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        pods = _list_pods_for_app(app_slug, org_id=org_id, environment_name=environment_name)
        scoped = [p for p in pods if (p.workload or "") == workload_slug]
        return _bucket_pods_by_status(scoped)

    @strawberry.field(
        deprecation_reason=(
            "Caps at 100 rows with no way to reach the 101st. Use astroliftAppDeployTokensPage."
        )
    )
    @require_permission(Permission.APP_READ, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_app_deploy_tokens(
        self,
        info: Info,
        app_slug: str,
    ) -> list[DeployTokenType]:
        qs = _app_deploy_tokens_qs(app_slug=app_slug).order_by("-created_at")[:100]
        return [deploy_token_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_app_deploy_tokens_page(
        self,
        info: Info,
        app_slug: str,
        search: str | None = None,
        sort_by: ListSortKey | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[DeployTokenType]:
        """Cursor-paginated deploy tokens for one app (#1235).

        Replaces ``astroliftAppDeployTokens`` and its 100-row cap. Tokens
        are never hard-deleted (revoked ones stay for the audit trail), so
        a long-lived app with rotating CI credentials accumulates rows the
        capped field could not reach.

        Seek key is ``(-created_at, -guid)``; ``search`` matches the token
        name plus the ``last 4`` / IP / user-agent forensic columns (#425)
        an operator uses to trace a token back to the runner that used it.
        """
        order, scope = resolve_sort(sort_by, NAMED_MODEL_SORTS)
        page = keyset_page(
            _app_deploy_tokens_qs(app_slug=app_slug, search=search),
            cursor=after,
            limit=limit,
            sort_field=order.sort_field,
            tiebreak_field=order.tiebreak_field,
            descending=order.descending,
            cursor_scope=scope,
        )
        return page.map(deploy_token_to_type)

    # ---- #377 / #1111 observability cards (DNS / TLS / Workload identity) ----
    #
    # The three resolvers below back the operator-facing cards on the
    # app detail page. Each one resolves the app + cluster (same shape
    # as ``astrolift_app_pods`` above), then dispatches to the cluster's
    # provider plugin via ``driver_for_capability``. Rather than
    # collapsing every empty outcome to ``[]`` / ``null`` (which forced
    # the FE to hedge "either not configured OR unsupported OR no
    # data"), each returns a reason-discriminated envelope
    # (:class:`ObservabilityPanelReason`) labelling the exact branch:
    #
    #   * NOT_CONFIGURED           — no cluster, or the capability driver
    #                                isn't wired on this cluster's plugin.
    #   * NOT_SUPPORTED_BY_PROVIDER — the driver raised
    #                                NotImplementedError /
    #                                UnsupportedOperationError (a read
    #                                only some clouds implement).
    #   * NO_DATA_YET              — the read succeeded, returned nothing.
    #   * ERROR                    — an unexpected driver-side failure
    #                                (logged; the FE offers a retry).
    #   * OK                       — non-empty data.
    #
    # ``UnsupportedOperationError`` subclasses ``NotImplementedError``,
    # so the single ``except NotImplementedError`` catches both.

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_app_dns_records(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> AppDnsRecordsResult:
        from core.app_deploy import AppDeployError, driver_for_capability

        # Org-scope to the caller's tenant: cluster resolution and the app
        # re-fetch below must be constrained to the caller's org, else a
        # known sibling-org slug would drive a live cloud read against that
        # org's app. Fails closed (NOT_CONFIGURED) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        cluster = _resolve_app_cluster(app_slug=app_slug, org_id=org_id, environment_name=environment_name)
        if cluster is None:
            return AppDnsRecordsResult(reason=ObservabilityPanelReason.NOT_CONFIGURED, records=[])
        try:
            driver = driver_for_capability(cluster, "dns")
        except AppDeployError:
            return AppDnsRecordsResult(reason=ObservabilityPanelReason.NOT_CONFIGURED, records=[])

        # Multi-app installs share one hosted zone (#1114); scope the read
        # to the app's public FQDN so the card shows only this app's records,
        # not every app's. On a dedicated-per-app zone the host-filter is a
        # no-op (every record is already the app's). No host -> pass None and
        # the driver returns the whole resolved zone (unchanged behavior).
        from astrolift_observability.url_resolution import resolved_public_host

        app = RegisteredApp.objects.filter(
            slug=app_slug, organization_id=org_id, deleted_at__isnull=True
        ).first()
        app_host = resolved_public_host(app) if app is not None else None
        try:
            records = driver.list_records_for_app(app_slug, app_host=app_host)
        except NotImplementedError:
            return AppDnsRecordsResult(reason=ObservabilityPanelReason.NOT_SUPPORTED_BY_PROVIDER, records=[])
        except Exception:  # noqa: BLE001 — unexpected driver failure
            log.exception("astrolift_app_dns_records: driver read failed for app %s", app_slug)
            return AppDnsRecordsResult(reason=ObservabilityPanelReason.ERROR, records=[])
        out = [dns_record_to_type(r) for r in records]
        reason = ObservabilityPanelReason.OK if out else ObservabilityPanelReason.NO_DATA_YET
        return AppDnsRecordsResult(reason=reason, records=out)

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_app_certificates(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> AppCertificatesResult:
        from astrolift_observability.url_resolution import resolved_public_host
        from core.app_deploy import AppDeployError, driver_for_capability

        # Org-scope to the caller's tenant: cluster resolution and the app
        # re-fetch below must be constrained to the caller's org, else a
        # known sibling-org slug would drive a live cloud read against that
        # org's app. Fails closed (NOT_CONFIGURED) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        cluster = _resolve_app_cluster(app_slug=app_slug, org_id=org_id, environment_name=environment_name)
        if cluster is None:
            return AppCertificatesResult(reason=ObservabilityPanelReason.NOT_CONFIGURED, certificates=[])

        # Match the cert against the app's real public FQDN, not its
        # slug (#1111). The slug never matched a wildcard/SAN cert:
        # a ``*.<zone>`` cert covers the host ``<subdomain>.<zone>``,
        # which is what ``resolved_public_host`` returns. No host ⇒ the
        # app has no public URL to hold a cert ⇒ not configured.
        app = (
            RegisteredApp.objects.filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        host = resolved_public_host(app) if app is not None else None
        if host is None:
            return AppCertificatesResult(reason=ObservabilityPanelReason.NOT_CONFIGURED, certificates=[])

        try:
            driver = driver_for_capability(cluster, "tls")
        except AppDeployError:
            return AppCertificatesResult(reason=ObservabilityPanelReason.NOT_CONFIGURED, certificates=[])
        try:
            certs = driver.list_certificates(filter_hostname=host)
        except NotImplementedError:
            return AppCertificatesResult(
                reason=ObservabilityPanelReason.NOT_SUPPORTED_BY_PROVIDER, certificates=[]
            )
        except Exception:  # noqa: BLE001 — unexpected driver failure
            log.exception("astrolift_app_certificates: driver read failed for app %s", app_slug)
            return AppCertificatesResult(reason=ObservabilityPanelReason.ERROR, certificates=[])
        out = [certificate_info_to_type(c) for c in certs]
        reason = ObservabilityPanelReason.OK if out else ObservabilityPanelReason.NO_DATA_YET
        return AppCertificatesResult(reason=reason, certificates=out)

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def astrolift_app_identity_binding(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> AppIdentityBindingResult:
        from core.app_deploy import AppDeployError, driver_for_capability

        # Org-scope to the caller's tenant: cluster resolution must be
        # constrained to the caller's org, else a known sibling-org slug
        # would drive a live cloud read against that org's app. Fails closed
        # (NOT_CONFIGURED) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        cluster = _resolve_app_cluster(app_slug=app_slug, org_id=org_id, environment_name=environment_name)
        if cluster is None:
            return AppIdentityBindingResult(reason=ObservabilityPanelReason.NOT_CONFIGURED, binding=None)
        try:
            driver = driver_for_capability(cluster, "identity")
        except AppDeployError:
            return AppIdentityBindingResult(reason=ObservabilityPanelReason.NOT_CONFIGURED, binding=None)
        try:
            binding = driver.describe_identity(app_slug)
        except NotImplementedError:
            return AppIdentityBindingResult(
                reason=ObservabilityPanelReason.NOT_SUPPORTED_BY_PROVIDER, binding=None
            )
        except Exception:  # noqa: BLE001 — unexpected driver failure
            log.exception("astrolift_app_identity_binding: driver read failed for app %s", app_slug)
            return AppIdentityBindingResult(reason=ObservabilityPanelReason.ERROR, binding=None)
        if binding is None:
            return AppIdentityBindingResult(reason=ObservabilityPanelReason.NO_DATA_YET, binding=None)
        return AppIdentityBindingResult(
            reason=ObservabilityPanelReason.OK,
            binding=identity_binding_to_type(binding),
        )

    # ----------------------------------------------------------------
    # #436 A — blast-radius preview for the deregister modal.
    # ----------------------------------------------------------------
    #
    # The deregister modal used to enumerate generic resource-class
    # labels ("k8s namespace", "managed services"). That made it hard to
    # spot the case where a teardown was about to remove something the
    # operator didn't realize was bound to the app (a prod RDS instance,
    # an IRSA role another workload squatted on, etc.). This resolver
    # returns the actual object names so the modal renders an auditable
    # tree — grouped by k8s / managed-services / identity / network — on
    # open. The data shape mirrors the workflow's per-step teardown order
    # so a future "show what failed" view can re-use the same projection.
    #
    # Gate: ``app.delete`` — same permission the deregister mutation
    # requires. A user who can read the app but not delete it has no
    # business previewing the destructive list.

    @strawberry.field
    @require_permission(Permission.APP_DELETE, scope=app_scope_by_slug("app_slug"))
    @tenant_scoped()
    def preview_astrolift_deregister(
        self,
        info: Info,
        app_slug: str,
    ) -> DeregisterPreviewType | None:
        """Blast-radius read for the deregister modal (#436 A).

        Resolves the app + every per-app resource that the deregister
        workflow will tear down. Returns None on not-found so the FE
        renders an empty-state without leaking row counts (tenant
        scoping already filters cross-org rows out of the lookup).

        Read-only — no side effects. Safe to fire on every modal-open
        without changing platform state.
        """
        from astrolift_lifecycle.schema.types import (
            DeregisterPreviewDeployTokenType,
            DeregisterPreviewIdentityRoleType,
            DeregisterPreviewK8sObjectType,
            DeregisterPreviewManagedServiceType,
            DeregisterPreviewSecretRefType,
            DeregisterPreviewSourceWebhookType,
        )
        from astrolift_registry.models import Workload
        from astrolift_services.models import AppSecretBundleRef, ManagedService

        # Org-scope the lookup to the caller's tenant — slugs are unique only
        # within an org, so an unscoped fetch would leak a sibling org's
        # teardown blast-radius. Fails closed to None when org_id is None
        # (#1183); this is what makes the docstring's tenant-scoping claim true.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return None

        # ---- k8s objects across every active env+cluster pair --------
        envs = list(
            AppEnvironment.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
            ).select_related("tenant_cluster"),
        )
        workloads = list(
            Workload.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
            ),
        )
        # Per-workload canonical names + bare-slug fallback mirrors what
        # the force-redeploy delete path targets — keep the two paths
        # aligned so the preview list matches what will actually run.
        names: list[str] = []
        seen: set[str] = set()
        for w in workloads:
            n = f"{app.slug}-{w.slug}" if w.slug else app.slug
            if n and n not in seen:
                names.append(n)
                seen.add(n)
        if app.slug and app.slug not in seen:
            names.append(app.slug)
            seen.add(app.slug)

        # apiVersion / kind pairs the renderer emits for a typical app.
        # Same list the force-redeploy delete path targets so the preview
        # honestly reflects what gets deleted; the namespace cascade
        # picks up Pods/RS/ConfigMaps/PVCs that aren't explicitly named.
        _RENDER_KINDS = (
            ("v1", "Namespace"),
            ("apps/v1", "Deployment"),
            ("v1", "Service"),
            ("networking.k8s.io/v1", "Ingress"),
            ("batch/v1", "CronJob"),
            ("v1", "Secret"),
        )
        k8s_objects: list[DeregisterPreviewK8sObjectType] = []
        for env in envs:
            cluster = env.tenant_cluster
            if cluster is None:
                continue
            # Each environment's own namespace when it has one (#1922),
            # which is what teardown deletes.
            namespace = namespace_for_environment(env)
            for api_version, kind in _RENDER_KINDS:
                if kind == "Namespace":
                    k8s_objects.append(
                        DeregisterPreviewK8sObjectType(
                            cluster_slug=cluster.slug,
                            namespace=namespace,
                            api_version=api_version,
                            kind=kind,
                            name=namespace,
                        ),
                    )
                    continue
                for name in names:
                    k8s_objects.append(
                        DeregisterPreviewK8sObjectType(
                            cluster_slug=cluster.slug,
                            namespace=namespace,
                            api_version=api_version,
                            kind=kind,
                            name=name,
                        ),
                    )

        # ---- managed services -----------------------------------------
        ms_rows = list(
            ManagedService.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
            ).select_related("app_environment"),
        )
        managed_services = [
            DeregisterPreviewManagedServiceType(
                id=str(m.guid),
                name=m.name or m.kind,
                kind=m.kind,
                variant=m.variant or "",
                environment_name=(m.app_environment.name if m.app_environment_id else ""),
                status=m.status,
            )
            for m in ms_rows
        ]

        # ---- materialized secret refs ---------------------------------
        ref_rows = list(
            AppSecretBundleRef.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
            ).select_related(
                "app_environment__tenant_cluster",
                "secret_bundle",
            ),
        )
        secret_refs = [
            DeregisterPreviewSecretRefType(
                id=str(r.guid),
                bundle_slug=r.secret_bundle.slug,
                environment_name=(r.app_environment.name if r.app_environment_id else ""),
                cluster_slug=(
                    r.app_environment.tenant_cluster.slug
                    if r.app_environment_id and r.app_environment.tenant_cluster_id
                    else None
                ),
                prefix=r.prefix or "",
            )
            for r in ref_rows
        ]

        # ---- deploy tokens --------------------------------------------
        # ``DeployToken`` is app-scoped (no env FK); ``environment_name``
        # is intentionally None on the preview type so the FE can render
        # token rows under a flat "all environments" header.
        token_rows = list(
            DeployToken.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
            ),
        )
        deploy_tokens = [
            DeregisterPreviewDeployTokenType(
                id=str(t.guid),
                name=t.name,
                last4=t.token_last_4,
                environment_name=None,
            )
            for t in token_rows
        ]

        # ---- source webhook -------------------------------------------
        source_webhook: DeregisterPreviewSourceWebhookType | None = None
        if app.source_repo:
            source_webhook = DeregisterPreviewSourceWebhookType(
                installed=bool(app.source_webhook_id),
                repo=app.source_repo,
                hook_id=app.source_webhook_id or "",
            )

        # ---- identity role binding ------------------------------------
        # Best-effort per-cluster identity lookup. Falls back to an
        # empty list when no driver implements describe_identity; the
        # deregister workflow's identity-role step is itself best-effort
        # so the preview matches its reach.
        from core.app_deploy import AppDeployError, driver_for_capability

        identity_roles: list[DeregisterPreviewIdentityRoleType] = []
        for env in envs:
            cluster = env.tenant_cluster
            if cluster is None or not getattr(cluster, "is_active", True):
                continue
            try:
                driver = driver_for_capability(cluster, "identity")
            except AppDeployError:
                continue
            try:
                binding = driver.describe_identity(app.slug)
            except NotImplementedError:
                continue
            except Exception:  # noqa: BLE001 — degrade silently
                continue
            if binding is None:
                continue
            identity_roles.append(
                DeregisterPreviewIdentityRoleType(
                    cluster_slug=cluster.slug,
                    kind=binding.kind,
                    role_arn_or_principal=binding.role_arn_or_principal,
                ),
            )

        total = (
            len(k8s_objects)
            + len(managed_services)
            + len(secret_refs)
            + len(deploy_tokens)
            + len(identity_roles)
            + (1 if source_webhook and source_webhook.installed else 0)
            + (1 if app.registry_repo_uri else 0)
        )

        return DeregisterPreviewType(
            app_slug=app.slug,
            app_name=app.name,
            k8s_objects=k8s_objects,
            managed_services=managed_services,
            secret_refs=secret_refs,
            deploy_tokens=deploy_tokens,
            source_webhook=source_webhook,
            identity_roles=identity_roles,
            registry_repo_uri=app.registry_repo_uri or "",
            total_resource_count=total,
        )

    # ----------------------------------------------------------------
    # #436 D — force-redeploy in-flight deployment preview.
    # ----------------------------------------------------------------
    #
    # Force-redeploy will transition every in-flight Deployment row on
    # the (app, env) pair to FAILED before re-firing the CI workflow
    # (``_cancel_in_flight_deploys_sync``). This resolver returns the
    # same row set so the operator sees what they're about to interrupt
    # — timestamps, who triggered, image tag — and can decide to wait
    # rather than blow it away. Gate matches the mutation: ``app.deploy``
    # + ``app.update`` (both required for the destructive surface).

    @strawberry.field
    @require_permission(
        Permission.APP_DEPLOY,
        Permission.APP_UPDATE,
        scope=app_scope_by_slug("app_slug"),
        operation=named_environment("app_slug", "environment_name", all_if_absent=True),
    )
    @tenant_scoped()
    def preview_astrolift_force_redeploy(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> ForceRedeployPreviewType | None:
        """In-flight deployment list a force-redeploy will cancel (#436 D)."""
        from astrolift_lifecycle.schema.types import (
            ForceRedeployPreviewDeploymentType,
        )
        from astrolift_workflows.activities.force_redeploy import (
            _IN_FLIGHT_STATUSES,
        )

        # Org-scope the lookup to the caller's tenant — slugs are unique only
        # within an org, so an unscoped fetch would leak a sibling org's
        # in-flight deployments. Fails closed to None when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return None

        qs = (
            Deployment.objects.filter(
                registered_app=app,
                status__in=_IN_FLIGHT_STATUSES,
                deleted_at__isnull=True,
            )
            .select_related("app_environment", "workload", "triggered_by_user")
            .order_by("-created_at")
        )
        if environment_name:
            qs = qs.filter(app_environment__name=environment_name)

        rows: list[ForceRedeployPreviewDeploymentType] = []
        for d in qs[:50]:
            user = d.triggered_by_user
            if user is not None:
                triggered_by = str(
                    getattr(user, "email", "") or getattr(user, "username", "") or "",
                )
            elif d.triggered_by_token_kind:
                triggered_by = f"token:{d.triggered_by_token_kind}"
            elif d.ci_actor_kind:
                triggered_by = f"ci:{d.ci_actor_kind}"
            else:
                triggered_by = "system"
            rows.append(
                ForceRedeployPreviewDeploymentType(
                    id=str(d.guid),
                    environment_name=(d.app_environment.name if d.app_environment_id else ""),
                    workload_slug=(d.workload.slug if d.workload_id else None),
                    status=d.status,
                    image_tag=d.image_tag or "",
                    started_at=d.started_at,
                    created_at=d.created_at,
                    trigger_kind=d.trigger_kind,
                    triggered_by_display=triggered_by,
                    ci_actor_kind=d.ci_actor_kind or "",
                    ci_run_url=d.ci_run_url or "",
                ),
            )

        return ForceRedeployPreviewType(
            app_slug=app.slug,
            environment_name=environment_name,
            in_flight_deployments=rows,
        )

    # ----------------------------------------------------------------
    # TaskRun + AgentRun fleet queries (#801, #798)
    # ----------------------------------------------------------------

    @strawberry.field(
        deprecation_reason=("Caps at 500 rows with no way to reach the 501st. Use astroliftTaskRunsPage.")
    )
    @require_permission(Permission.APP_READ_LOGS, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ_LOGS)
    @tenant_scoped()
    def astrolift_task_runs(
        self,
        info: Info,
        app_slug: str | None = None,
        workload_slug: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[TaskRunType]:
        """Operator-initiated task executions, most-recent-first.

        Filtered by app, workload, and/or status. Capped at 500 to keep
        the response bounded.
        """
        qs = _task_runs_qs(app_slug=app_slug, workload_slug=workload_slug, status=status).order_by(
            "-created_at"
        )
        return [task_run_to_type(r) for r in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ_LOGS)
    @tenant_scoped()
    def astrolift_task_runs_page(
        self,
        info: Info,
        app_slug: str | None = None,
        workload_slug: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[TaskRunType]:
        """Cursor-paginated task-run history (#1235).

        Replaces ``astroliftTaskRuns`` and the 500-row cap its own
        docstring flagged as needing pagination: a migration task that
        runs on every deploy buries its own history within a release or
        two, and the older runs were unreachable rather than merely slow
        to reach.

        Seek key is ``(-created_at, -guid)``; ``search`` matches the app,
        workload, status, the ``batch/v1`` Job name, and the operator who
        triggered the run.
        """
        page = keyset_page(
            _task_runs_qs(
                app_slug=app_slug,
                workload_slug=workload_slug,
                status=status,
                search=search,
            ),
            cursor=after,
            limit=limit,
        )
        return page.map(task_run_to_type)

    @strawberry.field
    @require_permission(
        Permission.APP_READ_LOGS,
        scope=task_run_app_scope("id"),
        operation=row_operation(
            "astrolift_lifecycle.TaskRun", "id", app_path="app_environment__registered_app"
        ),
    )
    @tenant_scoped()
    def astrolift_task_run(self, info: Info, id: str) -> TaskRunType | None:
        """Single task run by guid, for cold detail deep-links (#1118).

        Org-scoped through ``workload → registered_app`` — TaskRun has no
        org FK of its own and its default manager is not tenant-aware, so
        an unscoped ``filter(guid=id)`` would return another org's run
        (guids are globally unique). Mirrors ``astrolift_task_runs``
        scoping and fails closed to ``None`` on a sibling-org or unknown id.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return None
        r = (
            TaskRun.objects.select_related(
                "workload",
                "workload__registered_app",
                "app_environment",
                "triggered_by_user",
            )
            .filter(guid=id, workload__registered_app__organization_id=org_id)
            .first()
        )
        return task_run_to_type(r) if r else None

    @strawberry.field(
        deprecation_reason=("Caps at 500 rows with no way to reach the 501st. Use astroliftAgentRunsPage.")
    )
    @require_permission(Permission.APP_READ, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_agent_runs(
        self,
        info: Info,
        app_slug: str | None = None,
        workload_slug: str | None = None,
        project_slug: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AgentRunType]:
        """AI agent dispatch executions, most-recent-first.

        Filtered by app, workload, project, and/or status.
        ``status="running"`` is the Active tab query; omitting status
        gives the full History. ``project_slug`` (spec 33 PR-2) narrows
        to runs whose agent workload belongs to that project — the
        per-project Agents detail surface uses it.
        """
        qs = _agent_runs_qs(
            app_slug=app_slug,
            workload_slug=workload_slug,
            project_slug=project_slug,
            status=status,
        ).order_by("-created_at")
        return [agent_run_to_type(r) for r in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @require_app_collection_scope(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_agent_runs_page(
        self,
        info: Info,
        app_slug: str | None = None,
        workload_slug: str | None = None,
        project_slug: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[AgentRunType]:
        """Cursor-paginated agent-run history (#1235).

        Replaces ``astroliftAgentRuns`` and its 500-row cap. A looping or
        scheduled agent produces runs continuously, so the History tab
        outran the cap fastest of any list on the platform.

        Seek key is ``(-created_at, -guid)``; ``search`` matches the app,
        workload, status, dispatch pod name, and the operator who
        triggered the run.
        """
        page = keyset_page(
            _agent_runs_qs(
                app_slug=app_slug,
                workload_slug=workload_slug,
                project_slug=project_slug,
                status=status,
                search=search,
            ),
            cursor=after,
            limit=limit,
        )
        return page.map(agent_run_to_type)


def _preview_deploy_failure_reasons(previews) -> dict[int, str]:
    """For failed previews that recorded no reason, their latest deployment's (#2155).

    Rows from before ``failure_reason`` was kept, and failures the build
    workflow did not describe, still say why when the deploy did. One query
    per page, over the page's own environments.
    """
    wanted = {p.app_environment_id: p.pk for p in previews if p.status == "failed" and not p.failure_reason}
    if not wanted:
        return {}
    latest = (
        Deployment.objects.filter(app_environment_id__in=list(wanted), deleted_at__isnull=True)
        .order_by("app_environment_id", "-created_at")
        .distinct("app_environment_id")
    )
    return {
        wanted[d.app_environment_id]: deployment_status_reason(d)
        for d in latest
        if d.status == Deployment.Status.FAILED
    }


def _preview_with_cost(p, *, failure_reason: str | None = None) -> PreviewEnvironmentType:
    """Project a ``PreviewEnvironment`` row, attaching live pod-resource
    aggregates + a daily cost estimate from the cluster's provider
    plugin (#431).

    Cluster resolution mirrors ``_list_pods_for_app`` — prefer the
    preview's bound ``app_environment.tenant_cluster``, fall back to
    the app's ``default_tenant_cluster``. When the cluster is unwired
    / unreachable the resolver still returns the row with zeroed
    resources + null cost so the page stays renderable.

    Cost is fetched on a per-call basis (no cache) — pricing changes
    frequently and the read volume is bounded by the 200-row cap on
    the list resolver. If this becomes a hotspot, the right answer is
    a short-TTL cache inside the provider plugin's cost driver, not a
    cache here (we don't want to cache stale numbers across orgs)."""
    from astrolift_lifecycle.preview_cost import (
        aggregate_pod_resources,
        estimate_daily_cost,
    )
    from astrolift_lifecycle.schema.types import PreviewAggregateResourcesType

    cluster = None
    if p.app_environment_id and p.app_environment.tenant_cluster_id:
        cluster = p.app_environment.tenant_cluster
    elif p.registered_app.default_tenant_cluster_id:
        cluster = p.registered_app.default_tenant_cluster

    pods: list = []
    if cluster is not None and getattr(cluster, "is_active", True):
        try:
            pods = list(
                list_app_pods(
                    cluster=cluster,
                    namespace=p.namespace,
                    app_slug=p.registered_app.slug,
                )
            )
        except ClusterObservabilityError:
            pods = []
        except Exception:  # noqa: BLE001 — k8s lib raises many subtypes
            pods = []

    aggregate = aggregate_pod_resources(pods)
    estimate = None
    if cluster is not None and aggregate.pod_count > 0:
        estimate = estimate_daily_cost(
            cluster=cluster,
            aggregate=aggregate,
            region=getattr(cluster, "region", "") or "",
        )

    return preview_to_type(
        p,
        aggregate_resources=PreviewAggregateResourcesType(
            cpu_cores=aggregate.cpu_cores,
            memory_bytes=aggregate.memory_bytes,
            pod_count=aggregate.pod_count,
        ),
        estimated_daily_cost_usd=estimate.daily_usd if estimate else None,
        estimated_cost_notes=list(estimate.notes) if estimate else [],
        estimated_cost_approximate=bool(estimate and estimate.approximate),
        failure_reason=failure_reason,
    )


_CERT_METADATA_TTL_SECONDS = 60 * 60  # 1h cache, per #731 acceptance


def _refresh_cert_metadata_if_stale(domains: list, *, app_slug: str, org_id: int | None) -> None:
    """Best-effort lazy refresh of cached TLS cert metadata (#731).

    Walks ``domains`` and, for any row whose ``cert_metadata_refreshed_at``
    is older than the 1h TTL (or null), asks the TLS driver for the
    current cert info and persists the snapshot back to the row.  Errors
    are swallowed — the FE renders the previously-cached value (or no
    chip on first failure) rather than 502-ing the whole page.

    Resolved once per call: the driver_for_capability lookup is cached
    under the cluster instance, and the list_certificates call accepts
    a hostname filter so we only fetch the rows we care about per
    domain.  When multiple domains share a cluster we batch by hostname
    rather than per-cluster (drivers vary in how they implement the
    filter; AWS ACM matches against DomainName + SANs).
    """
    from datetime import timedelta

    from django.utils import timezone

    from core.app_deploy import AppDeployError, driver_for_capability

    now = timezone.now()
    ttl = timedelta(seconds=_CERT_METADATA_TTL_SECONDS)
    # Cache driver lookups per (cluster_id) — multiple domains often
    # share a cluster so we don't want N driver instantiations.
    driver_cache: dict[int, object] = {}
    cluster_cache: dict[int, object] = {}

    for d in domains:
        if d.certificate_state in ("not_requested", "issuing", "byo"):
            # No upstream cert to query yet (or operator-managed BYO);
            # skip refresh so we don't carry stale ACM data into a row
            # whose lifecycle hasn't reached issuance.
            continue
        if d.cert_metadata_refreshed_at and (now - d.cert_metadata_refreshed_at) < ttl:
            continue

        # Resolve the cluster lazily; the domain doesn't carry a direct
        # FK so we go through the registered app + default cluster.
        app = getattr(d, "registered_app", None)
        if app is None:
            continue
        cluster = cluster_cache.get(app.pk)
        if cluster is None:
            cluster = _resolve_app_cluster(app_slug=app.slug, org_id=org_id, environment_name=None)
            cluster_cache[app.pk] = cluster
        if cluster is None:
            continue

        driver = driver_cache.get(cluster.pk)
        if driver is None:
            try:
                driver = driver_for_capability(cluster, "tls")
            except AppDeployError:
                driver_cache[cluster.pk] = False  # type: ignore[assignment]
                continue
            driver_cache[cluster.pk] = driver
        if driver is False:
            continue

        try:
            certs = driver.list_certificates(filter_hostname=d.hostname)
        except (NotImplementedError, Exception):  # noqa: BLE001
            continue

        # Find the row matching this domain's hostname.  list_certificates
        # may return multiple (wildcards, SANs); prefer an exact match
        # then fall back to the first row.
        match = next((c for c in certs if getattr(c, "hostname", "") == d.hostname), None)
        if match is None and certs:
            match = certs[0]
        if match is None:
            # Driver returned no rows — still refresh the timestamp so
            # we don't hot-loop on a hostname the driver can't answer.
            d.cert_metadata_refreshed_at = now
            d.save(update_fields=["cert_metadata_refreshed_at", "updated_at", "version"])
            continue

        try:
            from datetime import datetime

            not_after_raw = getattr(match, "not_after", "") or ""
            if not_after_raw:
                parsed = datetime.fromisoformat(not_after_raw.replace("Z", "+00:00"))
                d.cert_expires_at = parsed
            d.cert_issuer_serial = getattr(match, "id", "") or ""
            d.cert_observability_status = getattr(match, "renewal_status", "") or ""
            d.cert_metadata_refreshed_at = now
            d.save(
                update_fields=[
                    "cert_expires_at",
                    "cert_issuer_serial",
                    "cert_observability_status",
                    "cert_metadata_refreshed_at",
                    "updated_at",
                    "version",
                ]
            )
        except (ValueError, TypeError):
            # Bad timestamp shape — refresh the timestamp anyway so we
            # don't hot-loop, but leave the other fields untouched.
            d.cert_metadata_refreshed_at = now
            d.save(update_fields=["cert_metadata_refreshed_at", "updated_at", "version"])


def _resolve_app_cluster(*, app_slug: str, org_id: int | None, environment_name: str | None):
    """Return the TenantCluster the observability cards should query.

    Mirrors the resolution shape of ``astrolift_app_pods``:
      1. If ``environment_name`` is given, prefer that env's cluster.
      2. Else fall back to the app's ``default_tenant_cluster``.
      3. Inactive cluster rows are skipped — same UX outcome as no
         cluster wired (empty card with a deep-link).

    Lives at module scope so the three observability resolvers stay
    short + the app/cluster lookup is testable without a strawberry
    Info object."""
    app = (
        RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
        .filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
        .first()
    )
    if app is None:
        return None
    cluster = None
    if environment_name:
        env = (
            AppEnvironment.objects.select_related("tenant_cluster")
            .filter(
                registered_app=app,
                name=environment_name,
                deleted_at__isnull=True,
            )
            .first()
        )
        cluster = env.tenant_cluster if env and env.tenant_cluster_id else None
    if cluster is None:
        cluster = app.default_tenant_cluster
    if cluster is None or not getattr(cluster, "is_active", True):
        return None
    return cluster
