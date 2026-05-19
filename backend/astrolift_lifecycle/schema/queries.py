"""Read-only queries for the lifecycle app."""

from __future__ import annotations

from datetime import timedelta

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID
from astrolift_lifecycle.models import (
    AppEnvironment,
    CommandRun,
    CustomDomain,
    Deployment,
    DeploymentLog,
    DeployToken,
    PreviewEnvironment,
    ScheduledJobRun,
)
from astrolift_lifecycle.schema.types import (
    AppCertificateType,
    AppDnsRecordType,
    AppDomainType,
    AppEnvironmentType,
    AppHealthSummaryType,
    AppIdentityBindingType,
    AppPodType,
    CommandRunType,
    DeploymentApprovalHistoryEntryType,
    DeploymentLogEntryType,
    DeploymentMetricsType,
    DeploymentType,
    DeployTokenType,
    DeregisterPreviewType,
    ForceRedeployPreviewType,
    PreviewEnvironmentType,
    ScheduledJobRunType,
    WorkloadPodStatusBucketType,
    WorkloadPodSummaryType,
    app_domain_to_type,
    app_env_to_type,
    certificate_info_to_type,
    command_run_to_type,
    deploy_token_to_type,
    deployment_log_to_type,
    deployment_to_type,
    dns_record_to_type,
    identity_binding_to_type,
    pod_info_to_type,
    preview_to_type,
    scheduled_job_run_to_type,
)
from astrolift_registry.models import RegisteredApp
from core.cluster_observability import (
    ClusterObservabilityError,
    list_app_pods,
    namespace_for_app,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission

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


def _list_pods_for_app(app_slug: str, *, environment_name: str | None = None) -> list:
    """Resolve cluster + namespace for an app and ask the driver for
    live pods.

    Extracted from ``astrolift_app_pods`` (#429) so the workload-
    detail breakdown resolver shares the exact same resolution rules
    (env-named cluster preferred → default cluster, ``namespace_for_app``).
    Returns an empty list on any kind of cluster-side failure so the
    UI stays renderable.
    """
    app = (
        RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
        .filter(slug=app_slug, deleted_at__isnull=True)
        .first()
    )
    if app is None:
        return []

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
        return []

    namespace = namespace_for_app(app)
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


@strawberry.type
class LifecycleQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_environments(self, info: Info, app_slug: str | None = None) -> list[AppEnvironmentType]:
        qs = AppEnvironment.objects.select_related(
            "registered_app", "tenant_cluster", "managed_domain"
        ).order_by("registered_app__slug", "name")
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [app_env_to_type(e) for e in qs[:300]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_deployments(
        self,
        info: Info,
        app_slug: str | None = None,
        environment_name: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentType]:
        qs = Deployment.objects.select_related("registered_app", "app_environment", "workload").order_by(
            "-created_at"
        )
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        if environment_name:
            qs = qs.filter(app_environment__name=environment_name)
        viewer = _viewer_user_id(info)
        return [deployment_to_type(d, viewer_user_id=viewer) for d in qs[: max(1, min(limit, 200))]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_deployment(self, info: Info, id: str) -> DeploymentType | None:
        """Single deployment by guid. Tenant-scoped via the manager."""
        d = (
            Deployment.objects.select_related("registered_app", "app_environment", "workload")
            .filter(guid=id)
            .first()
        )
        return deployment_to_type(d, viewer_user_id=_viewer_user_id(info)) if d else None

    @strawberry.field
    @require_permission(Permission.APP_READ)
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
        leaking row counts.
        """
        from django.db.models import Q

        from astrolift_operations.models import AuditEvent

        deployment = (
            Deployment.objects.filter(guid=deployment_id, deleted_at__isnull=True)
            .only("id", "guid", "aborted_reason")
            .first()
        )
        if deployment is None:
            return []

        deployment_guid = str(deployment.guid)
        events = (
            AuditEvent.objects.filter(action__in=_APPROVAL_LIFECYCLE_ACTIONS)
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
    @require_permission(Permission.APP_READ_LOGS)
    @tenant_scoped()
    def astrolift_deployment_log(self, info: Info, deployment_id: str) -> list[DeploymentLogEntryType]:
        # Look up the deployment by guid then return its log entries.
        deployment = Deployment.objects.filter(guid=deployment_id).first()
        if deployment is None:
            return []
        qs = DeploymentLog.objects.filter(deployment=deployment).order_by("occurred_at")
        return [deployment_log_to_type(e) for e in qs[:1000]]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS)
    @tenant_scoped()
    def astrolift_scheduled_job_runs(
        self,
        info: Info,
        app_slug: str | None = None,
        environment_name: str | None = None,
        limit: int = 100,
    ) -> list[ScheduledJobRunType]:
        qs = ScheduledJobRun.objects.select_related(
            "workload", "workload__registered_app", "app_environment"
        ).order_by("-created_at")
        if app_slug:
            qs = qs.filter(workload__registered_app__slug=app_slug)
        if environment_name:
            qs = qs.filter(app_environment__name=environment_name)
        return [scheduled_job_run_to_type(r) for r in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS)
    @tenant_scoped()
    def astrolift_command_runs(
        self,
        info: Info,
        app_slug: str | None = None,
        limit: int = 100,
    ) -> list[CommandRunType]:
        qs = CommandRun.objects.select_related("registered_app", "workload", "invoked_by").order_by(
            "-created_at"
        )
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [command_run_to_type(r) for r in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_preview_environments(
        self, info: Info, app_slug: str | None = None
    ) -> list[PreviewEnvironmentType]:
        qs = PreviewEnvironment.objects.select_related(
            "registered_app",
            "registered_app__organization",
            "registered_app__default_tenant_cluster",
            "app_environment__tenant_cluster",
        ).order_by("-created_at")
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        rows = list(qs[:200])
        return [_preview_with_cost(p) for p in rows]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_deployment_metrics(self, info: Info, window_days: int = 30) -> DeploymentMetricsType:
        """Aggregate rollout health for the last N days.

        Inputs are clamped to [1, 365] so callers can't ask for an
        unbounded scan. ``mean`` and ``p95`` use durations from
        terminal-state deployments only — in-flight rows have no
        duration yet.
        """
        window_days = max(1, min(int(window_days), 365))
        since = timezone.now() - timedelta(days=window_days)

        qs = Deployment.objects.filter(created_at__gte=since, deleted_at__isnull=True)

        in_flight_statuses = {
            Deployment.Status.PENDING_APPROVAL.value,
            Deployment.Status.PENDING.value,
            Deployment.Status.DEPLOYING.value,
            Deployment.Status.REDEPLOYING.value,
        }
        terminal_succeeded = {Deployment.Status.RUNNING.value}
        terminal_failed = {Deployment.Status.FAILED.value}
        terminal_rollback = {Deployment.Status.ROLLED_BACK.value}

        # Single pass over the queryset; we need both counts and
        # duration samples so a values_list is the right shape.
        rows = list(qs.values_list("status", "duration_seconds"))
        total = len(rows)
        succeeded = sum(1 for s, _ in rows if s in terminal_succeeded)
        failed = sum(1 for s, _ in rows if s in terminal_failed)
        rolled_back = sum(1 for s, _ in rows if s in terminal_rollback)
        in_flight = sum(1 for s, _ in rows if s in in_flight_statuses)

        durations = [d for s, d in rows if d is not None and d >= 0]
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
        )

    @strawberry.field
    @require_permission(Permission.APP_READ)
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
        out: list[AppHealthSummaryType] = []
        apps = RegisteredApp.objects.filter(deleted_at__isnull=True).order_by("slug")
        for app in apps[:300]:
            env_count = AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).count()
            latest = (
                Deployment.objects.filter(registered_app=app, deleted_at__isnull=True)
                .order_by("-created_at")
                .first()
            )
            has_recent_failure = Deployment.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
                created_at__gte=recent_window,
                status__in=[
                    Deployment.Status.FAILED.value,
                    Deployment.Status.ROLLED_BACK.value,
                ],
            ).exists()
            out.append(
                AppHealthSummaryType(
                    app_slug=app.slug,
                    app_name=app.name,
                    environment_count=env_count,
                    latest_deployment_status=(latest.status if latest else None),
                    latest_image_tag=(latest.image_tag if latest else ""),
                    last_deployed_at=(latest.created_at if latest else None),
                    has_recent_failure=has_recent_failure,
                )
            )
        return out

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_domains(
        self,
        info: Info,
        app_slug: str,
    ) -> list[AppDomainType]:
        qs = (
            CustomDomain.objects.select_related("registered_app")
            .filter(
                registered_app__slug=app_slug,
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
        _refresh_cert_metadata_if_stale(domains, app_slug=app_slug)
        return [app_domain_to_type(d) for d in domains]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS)
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
        pods = _list_pods_for_app(app_slug, environment_name=environment_name)
        return [pod_info_to_type(p) for p in pods]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS)
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
        pods = _list_pods_for_app(app_slug, environment_name=environment_name)
        scoped = [p for p in pods if (p.workload or "") == workload_slug]
        return _bucket_pods_by_status(scoped)

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_deploy_tokens(
        self,
        info: Info,
        app_slug: str,
    ) -> list[DeployTokenType]:
        qs = (
            DeployToken.objects.select_related("registered_app")
            .filter(
                registered_app__slug=app_slug,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")[:100]
        )
        return [deploy_token_to_type(t) for t in qs]

    # ---- #377 observability cards (DNS / TLS / Workload identity) ----
    #
    # The three resolvers below back the operator-facing cards on the
    # app detail page. Each one resolves the app + cluster (same shape
    # as ``astrolift_app_pods`` above), then dispatches to the cluster's
    # provider plugin via ``driver_for_capability``. Drivers that don't
    # implement the read method (the SDK default raises
    # ``NotImplementedError``) degrade to an empty list / null —
    # the FE renders that as the "not yet supported on this cloud"
    # empty state with a deep-link to set things up. Per the workspace
    # convention queries don't return MutationResult envelopes, so
    # NotImplementedError + driver-side errors are swallowed in the
    # resolver rather than translated to ``gql_failure``.

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_dns_records(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> list[AppDnsRecordType]:
        from core.app_deploy import AppDeployError, driver_for_capability

        cluster = _resolve_app_cluster(app_slug=app_slug, environment_name=environment_name)
        if cluster is None:
            return []
        try:
            driver = driver_for_capability(cluster, "dns")
        except AppDeployError:
            return []
        try:
            records = driver.list_records_for_app(app_slug)
        except NotImplementedError:
            return []
        except Exception:  # noqa: BLE001 — driver-side errors degrade
            return []
        return [dns_record_to_type(r) for r in records]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_certificates(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> list[AppCertificateType]:
        from core.app_deploy import AppDeployError, driver_for_capability

        cluster = _resolve_app_cluster(app_slug=app_slug, environment_name=environment_name)
        if cluster is None:
            return []
        try:
            driver = driver_for_capability(cluster, "tls")
        except AppDeployError:
            return []
        try:
            certs = driver.list_certificates(filter_hostname=app_slug)
        except NotImplementedError:
            return []
        except Exception:  # noqa: BLE001 — driver-side errors degrade
            return []
        return [certificate_info_to_type(c) for c in certs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_identity_binding(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> AppIdentityBindingType | None:
        from core.app_deploy import AppDeployError, driver_for_capability

        cluster = _resolve_app_cluster(app_slug=app_slug, environment_name=environment_name)
        if cluster is None:
            return None
        try:
            driver = driver_for_capability(cluster, "identity")
        except AppDeployError:
            return None
        try:
            binding = driver.describe_identity(app_slug)
        except NotImplementedError:
            return None
        except Exception:  # noqa: BLE001 — driver-side errors degrade
            return None
        if binding is None:
            return None
        return identity_binding_to_type(binding)

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
    @require_permission(Permission.APP_DELETE)
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
        from core.app_deploy import namespace_for_app

        app = (
            RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
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

        namespace = namespace_for_app(app)
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
    @require_permission(Permission.APP_DEPLOY, Permission.APP_UPDATE)
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

        app = (
            RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
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


def _preview_with_cost(p) -> PreviewEnvironmentType:
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
        estimate_daily_cost_usd,
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
    cost: float | None = None
    if cluster is not None and aggregate.pod_count > 0:
        cost = estimate_daily_cost_usd(
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
        estimated_daily_cost_usd=cost,
    )


_CERT_METADATA_TTL_SECONDS = 60 * 60  # 1h cache, per #731 acceptance


def _refresh_cert_metadata_if_stale(domains: list, *, app_slug: str) -> None:
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
            cluster = _resolve_app_cluster(app_slug=app.slug, environment_name=None)
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


def _resolve_app_cluster(*, app_slug: str, environment_name: str | None):
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
        .filter(slug=app_slug, deleted_at__isnull=True)
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
