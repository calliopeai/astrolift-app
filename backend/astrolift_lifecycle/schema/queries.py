"""Read-only queries for the lifecycle app."""

from __future__ import annotations

from datetime import timedelta

import strawberry
from django.utils import timezone
from strawberry.types import Info

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
    AppDomainType,
    AppEnvironmentType,
    AppHealthSummaryType,
    AppPodType,
    CommandRunType,
    DeploymentLogEntryType,
    DeploymentMetricsType,
    DeploymentType,
    DeployTokenType,
    PreviewEnvironmentType,
    ScheduledJobRunType,
    app_domain_to_type,
    app_env_to_type,
    command_run_to_type,
    deploy_token_to_type,
    deployment_log_to_type,
    deployment_to_type,
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
        return [deployment_to_type(d) for d in qs[: max(1, min(limit, 200))]]

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
        return deployment_to_type(d) if d else None

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
        qs = PreviewEnvironment.objects.select_related("registered_app").order_by("-created_at")
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [preview_to_type(p) for p in qs[:200]]

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
        return [app_domain_to_type(d) for d in qs]

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
            pods = list_app_pods(
                cluster=cluster,
                namespace=namespace,
                app_slug=app.slug,
            )
        except ClusterObservabilityError:
            return []
        except Exception:  # noqa: BLE001 — k8s lib raises many subtypes
            # Cluster transient errors (timeouts, 5xx) keep the UI
            # alive; the platform-event log carries the diagnostic.
            return []
        return [pod_info_to_type(p) for p in pods]

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
