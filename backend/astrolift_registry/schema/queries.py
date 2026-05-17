"""Read-only queries for the registry app."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

import strawberry
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from strawberry.types import Info

from astrolift_identity.schema.types import ProjectType, project_to_type
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import AppTeamAccess, Container, RegisteredApp, Workload
from astrolift_registry.schema.types import (
    AppFreshness,
    AppTeamAccessType,
    ContainerType,
    RegisteredAppType,
    RenderedManifestType,
    WorkloadManifestType,
    WorkloadScalingStatus,
    WorkloadType,
    app_team_access_to_type,
    app_to_type,
    build_app_freshness,
    build_config_drift,
    build_settings_last_modified,
    container_to_type,
    workload_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


def _scaling_environment_for_workload(workload, environment_name: str | None):
    """Pick the env to drive the scaling policy off of (#430).

    Mirrors the same resolution rules ``k8s_ops._primary_environment_for_workload``
    uses — the first active env on the app, or the env named
    explicitly. Returns ``None`` when nothing matches so
    ``resolve_replica_bounds`` falls back to the platform default.
    """
    qs = AppEnvironment.objects.filter(
        registered_app=workload.registered_app,
        deleted_at__isnull=True,
    ).order_by("id")
    if environment_name:
        qs = qs.filter(name=environment_name)
    return qs.first()


def _read_live_replicas(*, workload, environment_name: str | None) -> tuple[int, int]:
    """Best-effort live (current, desired) read from the cluster.

    Calls into ``ClusterDriver.get_workload_status`` on the Deployment
    backing ``workload`` and returns the ``(ready_replicas,
    desired_replicas)`` tuple. Raises on any error — the caller
    swallows so an unreachable cluster degrades to the manifest-side
    replica count, not a 500.
    """
    from core.app_deploy import namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    env = _scaling_environment_for_workload(workload, environment_name)
    if env is None or env.tenant_cluster_id is None:
        raise RuntimeError("no env / cluster")
    cluster = env.tenant_cluster
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    namespace = namespace_for_app(workload.registered_app)
    get_status = getattr(driver, "get_workload_status", None)
    if not callable(get_status):
        raise RuntimeError("driver lacks get_workload_status")
    status = get_status(ctx.slug, namespace, "Deployment", workload.slug)
    return int(status.ready_replicas or 0), int(status.desired_replicas or 0)


def _filter_resources_for_workload(resources: list[dict], workload_slug: str) -> list[dict]:
    """Filter a rendered manifest down to one workload's resources (#430).

    The renderer tags every workload resource with
    ``metadata.labels['astrolift.dev/workload']``. Anything missing
    that label (cluster-scoped resources, app-wide ConfigMaps) is
    excluded — those don't belong on the per-workload page.
    """
    out: list[dict] = []
    for resource in resources:
        metadata = resource.get("metadata") or {}
        labels = metadata.get("labels") or {}
        if labels.get("astrolift.dev/workload") == workload_slug:
            out.append(resource)
    return out


def _previous_deployment_for(*, app, environment, current_image_tag: str):
    """Find the most recent deployment to render a diff against (#430).

    Strategy:
      - Restrict to the same (app, env) pair.
      - Only ``RUNNING``, ``SUPERSEDED``, or ``ROLLED_BACK`` count — a
        deploy that never reached running can't serve as a baseline.
      - Skip rows whose ``image_tag`` matches the current image tag —
        diff-against-self is noise, not signal.

    Returns the Deployment row or ``None`` when no candidate exists.
    """
    qs = Deployment.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
        status__in=[
            Deployment.Status.RUNNING.value,
            Deployment.Status.SUPERSEDED.value,
            Deployment.Status.ROLLED_BACK.value,
        ],
    ).order_by("-created_at")
    if environment is not None:
        qs = qs.filter(app_environment=environment)
    if current_image_tag:
        qs = qs.exclude(image_tag=current_image_tag)
    return qs.first()


def _freshness_for_apps(apps: Iterable[RegisteredApp]) -> dict[int, AppFreshness]:
    """Bulk-build the per-app freshness payload for a list of apps (#405).

    Avoids the N+1 the per-row resolver would otherwise spawn: two
    queries total regardless of list size — one for the most-recent
    ``Deployment`` per app, one for the most-recent ``running``
    deploy per app. Returns a dict keyed by ``RegisteredApp.pk`` so
    callers can map back without re-fetching.

    Apps with no deployment rows still appear in the result — the
    builder emits a ``never`` pulse for those.
    """
    app_list = list(apps)
    if not app_list:
        return {}

    app_ids = [a.pk for a in app_list]
    now = timezone.now()

    base = (
        Deployment.objects.filter(
            registered_app_id__in=app_ids,
            deleted_at__isnull=True,
        )
        .select_related("triggered_by_user", "app_environment")
        .order_by("registered_app_id", "-created_at")
    )

    # Two passes — one for the most recent row per app, one for the
    # most recent ``running`` row per app. Each pass scans the
    # ordered queryset and keeps the first hit per ``registered_app_id``.
    latest_by_app: dict[int, Deployment] = {}
    for d in base:
        if d.registered_app_id not in latest_by_app:
            latest_by_app[d.registered_app_id] = d

    success_dt_by_app: dict[int, dt.datetime] = {}
    for d in base.filter(status=Deployment.Status.RUNNING.value):
        if d.registered_app_id not in success_dt_by_app:
            success_dt_by_app[d.registered_app_id] = d.created_at

    freshness_by_app: dict[int, AppFreshness] = {}
    for app in app_list:
        freshness_by_app[app.pk] = build_app_freshness(
            latest_deployment=latest_by_app.get(app.pk),
            last_success_at=success_dt_by_app.get(app.pk),
            now=now,
        )
    return freshness_by_app


def _viewer_permissions_for_apps(apps: Iterable[RegisteredApp]) -> dict[int, set[str]]:
    """Bulk-resolve the active viewer's effective permissions per app (#478).

    Resolver-local thin wrapper around
    :func:`astrolift_identity.permission_resolver.resolve_effective_permissions_for_apps`
    that pulls the active ``TenantContext`` from the request-scoped
    context var so the per-row serialisers don't have to.

    Returns an empty dict (and never raises) when there's no tenant or
    no actor — the caller's ``perms_by_app.get(app.pk)`` then resolves
    to ``None``, which ``app_to_type`` collapses into the empty-list
    field default. That keeps the cheap pre-auth schema-introspection
    paths working without special-casing every call site.
    """
    from astrolift_identity.permission_resolver import resolve_effective_permissions_for_apps

    tenant = get_current_tenant()
    if tenant is None or tenant.actor_user_id is None:
        return {}
    return resolve_effective_permissions_for_apps(tenant, apps)


@strawberry.type
class RegistryQuery:
    @strawberry.field
    def astrolift_platform_api_url(self, info: Info) -> str:
        """Public base URL the platform's REST API answers at.

        Surfaced to the Settings page CI-setup section (#382) so the
        operator can paste it verbatim into ``ASTROLIFT_API_URL`` on
        their CI side. Platform-level value — not tenant-scoped — but
        still gated to authenticated callers so we don't leak the
        install's API origin to anonymous probes.
        """
        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not getattr(viewer, "is_authenticated", False):
            raise PermissionError("astrolift_platform_api_url requires an authenticated viewer")
        return (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_apps(
        self,
        info: Info,
        include_freshness: bool = False,
    ) -> list[RegisteredAppType]:
        """Org-scoped list of registered apps.

        ``include_freshness`` (default False, #405) opts the row into
        the deployment-freshness rollup — ``latestDeployment``,
        ``lastDeployedAt``, and ``healthPulse``. Off by default so
        callers that only need the cheap registry fields don't pay
        the two-query freshness join.
        """

        qs = RegisteredApp.objects.select_related("organization", "team", "project").order_by("-created_at")[
            :200
        ]
        apps = list(qs)
        perms_by_app = _viewer_permissions_for_apps(apps)
        if not include_freshness:
            return [app_to_type(a, viewer_permissions=perms_by_app.get(a.pk)) for a in apps]
        freshness_by_app = _freshness_for_apps(apps)
        return [
            app_to_type(
                a,
                freshness=freshness_by_app.get(a.pk),
                viewer_permissions=perms_by_app.get(a.pk),
            )
            for a in apps
        ]

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_apps(
        self,
        info: Info,
        include_freshness: bool = False,
    ) -> list[RegisteredAppType]:
        """Apps the viewer can reach by any RoleBinding on the app or
        an ancestor (project / team / org).

        Self-service surface — no ``@require_permission``: the viewer's
        own bindings are what gate visibility. Returns an empty list
        when there's no authenticated actor. Listed in the tenancy
        guardrail EXEMPT set with this rationale.

        Implementation: collect every active RoleBinding for the
        caller, project the (scope_kind, scope_id) tuples, then
        union-resolve to the set of app ids that are visible. An
        ORG-scoped binding grants visibility to every app in the org;
        TEAM/PROJECT-scoped grants visibility to every app under that
        sub-tree; APP-scoped grants visibility to just that app.
        Superusers see every app in their active tenant (matches the
        permission resolver's superuser short-circuit).
        """
        from django.contrib.auth import get_user_model
        from django.utils import timezone

        from astrolift_identity.models import RoleBinding

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []

        User = get_user_model()
        viewer = User.objects.filter(pk=tenant.actor_user_id).first()
        if viewer is None:
            return []

        base_qs = RegisteredApp.objects.select_related("organization", "team", "project").filter(
            deleted_at__isnull=True
        )
        if tenant.organization_id is not None:
            base_qs = base_qs.filter(organization_id=tenant.organization_id)

        if getattr(viewer, "is_superuser", False) and getattr(viewer, "is_active", True):
            superuser_apps = list(base_qs.order_by("slug")[:200])
            perms_by_app = _viewer_permissions_for_apps(superuser_apps)
            if not include_freshness:
                return [app_to_type(a, viewer_permissions=perms_by_app.get(a.pk)) for a in superuser_apps]
            freshness_by_app = _freshness_for_apps(superuser_apps)
            return [
                app_to_type(
                    a,
                    freshness=freshness_by_app.get(a.pk),
                    viewer_permissions=perms_by_app.get(a.pk),
                )
                for a in superuser_apps
            ]

        now = timezone.now()
        bindings = list(
            RoleBinding.objects.filter(
                user_id=tenant.actor_user_id,
                deleted_at__isnull=True,
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        )
        if not bindings:
            return []

        org_ids: set[int] = set()
        team_ids: set[int] = set()
        project_ids: set[int] = set()
        app_ids: set[int] = set()
        for b in bindings:
            if b.scope_kind == RoleBinding.ScopeKind.ORG:
                org_ids.add(b.scope_id)
            elif b.scope_kind == RoleBinding.ScopeKind.TEAM:
                team_ids.add(b.scope_id)
            elif b.scope_kind == RoleBinding.ScopeKind.PROJECT:
                project_ids.add(b.scope_id)
            elif b.scope_kind == RoleBinding.ScopeKind.APP:
                app_ids.add(b.scope_id)

        scope_filter = Q()
        if org_ids:
            scope_filter |= Q(organization_id__in=org_ids)
        if team_ids:
            scope_filter |= Q(team_id__in=team_ids)
        if project_ids:
            scope_filter |= Q(project_id__in=project_ids)
        if app_ids:
            scope_filter |= Q(pk__in=app_ids)

        if not scope_filter.children:
            return []

        qs = base_qs.filter(scope_filter).order_by("slug")[:200]
        scoped_apps = list(qs)
        perms_by_app = _viewer_permissions_for_apps(scoped_apps)
        if not include_freshness:
            return [app_to_type(a, viewer_permissions=perms_by_app.get(a.pk)) for a in scoped_apps]
        freshness_by_app = _freshness_for_apps(scoped_apps)
        return [
            app_to_type(
                a,
                freshness=freshness_by_app.get(a.pk),
                viewer_permissions=perms_by_app.get(a.pk),
            )
            for a in scoped_apps
        ]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app(
        self,
        info: Info,
        slug: str,
        include_drift: bool = False,
    ) -> RegisteredAppType | None:
        """Single app by slug.

        ``include_drift`` (default False, #407 C) opts the row into
        the config-drift rollup — ``configDrift`` is left null when
        False so the cheap header query stays cheap. The app overview
        passes True; sidebar / breadcrumb queries pass False.
        """
        app = (
            RegisteredApp.objects.select_related("organization", "team", "project").filter(slug=slug).first()
        )
        if app is None:
            return None
        drift = build_config_drift(app) if include_drift else None
        # Per-section "Modified N ago" timestamps power the Settings
        # landing card grid (#454). Computed only on the detail path —
        # the list resolvers leave the wrapper None.
        settings_last_modified = build_settings_last_modified(app)
        perms_by_app = _viewer_permissions_for_apps([app])
        return app_to_type(
            app,
            drift=drift,
            settings_last_modified=settings_last_modified,
            viewer_permissions=perms_by_app.get(app.pk),
        )

    @strawberry.field
    @tenant_scoped()
    def astrolift_app_permissions(self, info: Info, app_slug: str) -> list[str]:
        """Effective permission slugs the viewer holds on ``app_slug`` (#478).

        Read-side mirror of the resolver chain used by
        ``@require_permission`` decorators, scoped to a single app
        instead of the whole tenant. Mobile / web render only the
        actions the viewer can take — the alternative (try-and-403)
        is bad UX on a phone (tap → toast).

        Self-service: not gated by ``@require_permission``. Listed in
        the tenancy-guardrail EXEMPT set with this rationale. Returns
        an empty list when:

          * No authenticated viewer.
          * The app slug doesn't resolve in the active tenant.
          * The viewer has no RoleBinding that reaches the app.

        Empty list is a legitimate signal, not an error — the FE will
        disable every action button when it sees one.
        """
        from astrolift_identity.permission_resolver import (
            resolve_effective_permissions_for_apps,
        )

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []

        qs = RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
        if tenant.organization_id is not None:
            qs = qs.filter(organization_id=tenant.organization_id)
        app = qs.first()
        if app is None:
            return []

        perms_by_app = resolve_effective_permissions_for_apps(tenant, [app])
        return sorted(perms_by_app.get(app.pk, set()))

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_team_accesses(self, info: Info, app_slug: str) -> list[AppTeamAccessType]:
        """List every team that holds active access to ``app_slug``.

        Includes the home-team row (``is_home=true``) plus any
        additional teams granted via ``grantTeamAccessToApp``.
        Backfilled deployments will show exactly one row (the home
        team at ``OWNER``) until the operator grants more teams.
        """

        app = (
            RegisteredApp.objects.select_related("team")
            .filter(slug=app_slug, deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return []
        rows = (
            AppTeamAccess.objects.select_related("registered_app", "team")
            .filter(registered_app=app, deleted_at__isnull=True)
            .order_by("team__slug")
        )
        return [app_team_access_to_type(r, home_team_id=app.team_id) for r in rows]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_workloads(self, info: Info, app_slug: str | None = None) -> list[WorkloadType]:
        qs = Workload.objects.select_related("registered_app")
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [workload_to_type(w) for w in qs[:200]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_workload(self, info: Info, app_slug: str, slug: str) -> WorkloadType | None:
        """Single workload by (app_slug, slug).

        Workloads are scoped under the registered app — the same
        workload slug can recur across orgs without colliding because
        the app+slug compound is unique within tenant.
        """
        w = (
            Workload.objects.select_related("registered_app")
            .filter(registered_app__slug=app_slug, slug=slug)
            .first()
        )
        return workload_to_type(w) if w else None

    # ----------------------------------------------------------------
    # Live workload scaling status (#430)
    # ----------------------------------------------------------------
    #
    # Combines the manifest-side HPA configuration (Workload row) with
    # the live ``WorkloadStatus`` read from the cluster driver. Powers
    # the scaling card on the workload-detail page — the slider, the
    # HPA gauge, and the "Scaling…" indicator.
    #
    # Live read degrades to manifest-only when the driver doesn't
    # surface ``get_workload_status`` (older provider plugins) or the
    # cluster is unreachable; the resolver never raises — empty-state
    # rendering happens client-side off the same single round-trip.

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_workload_scaling_status(
        self,
        info: Info,
        app_slug: str,
        workload_slug: str,
        environment_name: str | None = None,
    ) -> WorkloadScalingStatus | None:
        """Live + configured scaling status for one workload.

        Returns ``None`` when the workload doesn't exist for the
        current tenant. Returns a populated value with zero live
        replica counts when the cluster is unreachable / unwired so
        the FE can still render the slider (the manual-scale mutation
        will fail loudly if the driver can't reach the cluster, which
        is the right place to surface that error).
        """
        from astrolift_lifecycle.services.k8s_ops import resolve_replica_bounds

        workload = (
            Workload.objects.filter(
                registered_app__slug=app_slug,
                slug=workload_slug,
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
        )
        if workload is None:
            return None

        # Manifest-side HPA fields — always present.
        hpa_min = workload.hpa_min_replicas
        hpa_max = workload.hpa_max_replicas
        hpa_target = workload.hpa_target_cpu_pct
        # The renderer only emits an HPA when both min + max are set
        # (and positive); mirror that here so the FE doesn't render a
        # gauge for an HPA the cluster doesn't actually have.
        hpa_enabled = bool(hpa_min and hpa_max and hpa_min > 0 and hpa_max > 0)

        # Live read — best effort. We honour the same cluster
        # resolution rules as the pod-list query so an environment
        # override picks the same target.
        live_current = workload.replicas
        live_desired = workload.replicas
        try:
            live_current, live_desired = _read_live_replicas(
                workload=workload,
                environment_name=environment_name,
            )
        except Exception:  # noqa: BLE001 — cluster-side failures are non-fatal
            pass

        # Replica bounds — share the same policy the scale mutation
        # uses so the slider can't propose an out-of-range value.
        env = _scaling_environment_for_workload(workload, environment_name)
        lower, upper = resolve_replica_bounds(env)
        # When HPA is enabled, the slider caps at the HPA max so manual
        # scales don't immediately get reconciled away.
        if hpa_enabled and hpa_max is not None:
            upper = min(upper, hpa_max)

        return WorkloadScalingStatus(
            hpa_enabled=hpa_enabled,
            hpa_min_replicas=hpa_min,
            hpa_max_replicas=hpa_max,
            hpa_target_cpu_pct=hpa_target,
            current_replicas=live_current,
            desired_replicas=live_desired,
            is_scaling=live_current != live_desired,
            replica_lower_bound=lower,
            replica_upper_bound=upper,
            sourced_at=timezone.now(),
        )

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_containers(self, info: Info, workload_slug: str | None = None) -> list[ContainerType]:
        qs = Container.objects.select_related("workload")
        if workload_slug:
            qs = qs.filter(workload__slug=workload_slug)
        return [container_to_type(c) for c in qs[:500]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_rendered_manifest(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
        image_tag: str | None = None,
    ) -> RenderedManifestType | None:
        """Return the rendered Kubernetes resources for an app+env.

        The renderer is called with the same inputs the deploy
        activity uses, so what users see here is exactly what would
        land in the cluster. ``image_tag`` defaults to ``"preview"``
        when omitted so the output is meaningful even before any
        deploy has been issued.

        Manifest parse / normalize errors surface in the ``error``
        field rather than as a GraphQL exception — that lets the UI
        render a friendly editor diagnostic without losing the
        environment + namespace context.
        """
        from astrolift_manifest.normalize import (
            NormalizationDefaults,
            normalize,
        )
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.render import render_manifests

        app = RegisteredApp.objects.select_related("organization").filter(slug=app_slug).first()
        if app is None:
            return None

        env = (
            AppEnvironment.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
                **({"name": environment_name} if environment_name else {}),
            )
            .order_by("created_at")
            .first()
        )
        env_name = env.name if env else (environment_name or "preview")
        namespace = app.k8s_namespace or f"{app.organization.slug}-{app.slug}"
        image = image_tag or "preview"

        # Apps registered before the wizard's manifest step shipped, or
        # whose `astrolift.toml` failed to fetch from the source repo,
        # have empty `manifest_raw`. Surface a friendlier error than
        # the parser's "required string 'name' is missing or empty" so
        # the UI can point the operator at the manifest editor.
        if not (app.manifest_raw or "").strip():
            return RenderedManifestType(
                app_slug=app.slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                error=(
                    "No manifest saved for this app yet. Open the Manifest "
                    "tab and paste your astrolift.toml, or re-run the app "
                    "registration wizard to fetch from the source repo."
                ),
                error_path=None,
                error_line=None,
                error_column=None,
            )

        try:
            normalized = normalize(
                parse_raw(app.manifest_raw),
                defaults=NormalizationDefaults(),
            )
        except ManifestError as exc:
            from astrolift_manifest.parser import locate_in_source

            error_path = getattr(exc, "path", None) or None
            line, column = exc.line, exc.column
            # Semantic errors (kind=spaceship, healthcheck typo) don't
            # carry source positions — best-effort locate by leaf key.
            if line is None and error_path:
                line, column = locate_in_source(app.manifest_raw, error_path)
            return RenderedManifestType(
                app_slug=app.slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                error=str(exc),
                error_path=error_path,
                error_line=line,
                error_column=column,
            )
        except Exception as exc:  # defensive: never blow up the resolver
            return RenderedManifestType(
                app_slug=app.slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                error=f"unexpected error: {exc}",
                error_path=None,
                error_line=None,
                error_column=None,
            )

        resources = render_manifests(
            normalized,
            namespace=namespace,
            image_tag=image,
            image_repository=app.registry_repo_uri or app.slug,
            environment_name=env_name,
        )
        return RenderedManifestType(
            app_slug=app.slug,
            environment_name=env_name,
            image_tag=image,
            namespace=namespace,
            resources=resources,
            error=None,
            error_path=None,
            error_line=None,
            error_column=None,
        )

    # ----------------------------------------------------------------
    # Per-workload manifest preview + diff (#430)
    # ----------------------------------------------------------------
    #
    # Renders the platform's full manifest for the app, then filters
    # resources down to ones labelled ``astrolift.dev/workload=<slug>``
    # — the per-workload subset of what the deploy activity would
    # apply.
    #
    # For the diff side: render the same manifest twice — once at the
    # current image tag, once at the prior deployment's tag — and
    # surface both side-by-side. v1 limitation: the platform doesn't
    # store the raw ``astrolift.toml`` per deployment, so a structural
    # change to the manifest between deploys won't show up in the
    # diff — only the image-tag delta will. Full historical TOML diff
    # is a follow-up backend ticket.

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_workload_manifest(
        self,
        info: Info,
        app_slug: str,
        workload_slug: str,
        environment_name: str | None = None,
        image_tag: str | None = None,
    ) -> WorkloadManifestType | None:
        """Return the rendered Kubernetes resources for one workload.

        Filters the app-wide manifest to resources whose
        ``metadata.labels['astrolift.dev/workload']`` matches
        ``workload_slug``. Populates ``previous_image_tag`` +
        ``resources_previous`` from the most-recent prior deployment
        for the same (app, environment) so the FE can render an
        image-tag diff without a second round-trip.

        Returns ``None`` when the app doesn't exist for the current
        tenant. Returns a populated result with an empty
        ``resources`` list when the workload isn't in the manifest
        yet (the FE renders the "no resources" empty state).
        """
        from astrolift_manifest.normalize import (
            NormalizationDefaults,
            normalize,
        )
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.render import render_manifests

        app = RegisteredApp.objects.select_related("organization").filter(slug=app_slug).first()
        if app is None:
            return None

        env = (
            AppEnvironment.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
                **({"name": environment_name} if environment_name else {}),
            )
            .order_by("created_at")
            .first()
        )
        env_name = env.name if env else (environment_name or "preview")
        namespace = app.k8s_namespace or f"{app.organization.slug}-{app.slug}"
        image = image_tag or "preview"

        prior = _previous_deployment_for(app=app, environment=env, current_image_tag=image)
        previous_image_tag = prior.image_tag if prior else ""
        previous_deployment_id = str(prior.guid) if prior else ""

        if not (app.manifest_raw or "").strip():
            return WorkloadManifestType(
                app_slug=app.slug,
                workload_slug=workload_slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                previous_image_tag=previous_image_tag,
                previous_deployment_id=previous_deployment_id,
                resources_previous=[],
                error=(
                    "No manifest saved for this app yet. Open the Manifest "
                    "tab and paste your astrolift.toml, or re-run the app "
                    "registration wizard to fetch from the source repo."
                ),
                error_path=None,
                error_line=None,
                error_column=None,
            )

        try:
            normalized = normalize(
                parse_raw(app.manifest_raw),
                defaults=NormalizationDefaults(),
            )
        except ManifestError as exc:
            from astrolift_manifest.parser import locate_in_source

            error_path = getattr(exc, "path", None) or None
            line, column = exc.line, exc.column
            if line is None and error_path:
                line, column = locate_in_source(app.manifest_raw, error_path)
            return WorkloadManifestType(
                app_slug=app.slug,
                workload_slug=workload_slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                previous_image_tag=previous_image_tag,
                previous_deployment_id=previous_deployment_id,
                resources_previous=[],
                error=str(exc),
                error_path=error_path,
                error_line=line,
                error_column=column,
            )
        except Exception as exc:  # noqa: BLE001 — defensive: never blow up the resolver
            return WorkloadManifestType(
                app_slug=app.slug,
                workload_slug=workload_slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                previous_image_tag=previous_image_tag,
                previous_deployment_id=previous_deployment_id,
                resources_previous=[],
                error=f"unexpected error: {exc}",
                error_path=None,
                error_line=None,
                error_column=None,
            )

        rendered = render_manifests(
            normalized,
            namespace=namespace,
            image_tag=image,
            image_repository=app.registry_repo_uri or app.slug,
            environment_name=env_name,
        )
        scoped = _filter_resources_for_workload(rendered, workload_slug)
        scoped_previous: list[dict[str, object]] = []
        if previous_image_tag and previous_image_tag != image:
            rendered_previous = render_manifests(
                normalized,
                namespace=namespace,
                image_tag=previous_image_tag,
                image_repository=app.registry_repo_uri or app.slug,
                environment_name=env_name,
            )
            scoped_previous = _filter_resources_for_workload(rendered_previous, workload_slug)

        return WorkloadManifestType(
            app_slug=app.slug,
            workload_slug=workload_slug,
            environment_name=env_name,
            image_tag=image,
            namespace=namespace,
            resources=scoped,
            previous_image_tag=previous_image_tag,
            previous_deployment_id=previous_deployment_id,
            resources_previous=scoped_previous,
            error=None,
            error_path=None,
            error_line=None,
            error_column=None,
        )

    @strawberry.field
    @tenant_scoped()
    def assignable_astrolift_projects(self, info: Info) -> list[ProjectType]:
        """Projects in the current tenant org the viewer can assign
        apps to (#391).

        Self-service: no ``@require_permission`` gate — the viewer's
        own bindings are the gate, same shape as ``astrolift_my_apps``.
        Returns projects reachable by an active RoleBinding at ORG /
        TEAM / PROJECT scope. APP-scope bindings don't surface
        projects: a user with app-only access on one app shouldn't be
        offered the underlying project as an assignment target for
        OTHER apps.

        Superusers see every active project in the tenant.
        """
        from django.db.models import Q
        from django.utils import timezone

        from astrolift_identity.models import Project, RoleBinding

        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return []

        from django.contrib.auth import get_user_model

        User = get_user_model()
        viewer = (
            User.objects.filter(pk=tenant.actor_user_id).first() if tenant.actor_user_id is not None else None
        )

        base_qs = Project.objects.select_related("organization", "team").filter(
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        )

        if (
            viewer is not None
            and getattr(viewer, "is_superuser", False)
            and getattr(viewer, "is_active", True)
        ):
            return [project_to_type(p) for p in base_qs.order_by("name")[:500]]

        if viewer is None:
            return []

        now = timezone.now()
        bindings = list(
            RoleBinding.objects.filter(
                user_id=viewer.pk,
                deleted_at__isnull=True,
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        )
        if not bindings:
            return []

        org_ids: set[int] = set()
        team_ids: set[int] = set()
        project_ids: set[int] = set()
        for b in bindings:
            if b.scope_kind == RoleBinding.ScopeKind.ORG:
                org_ids.add(b.scope_id)
            elif b.scope_kind == RoleBinding.ScopeKind.TEAM:
                team_ids.add(b.scope_id)
            elif b.scope_kind == RoleBinding.ScopeKind.PROJECT:
                project_ids.add(b.scope_id)

        scope_filter = Q()
        if org_ids:
            scope_filter |= Q(organization_id__in=org_ids)
        if team_ids:
            scope_filter |= Q(team_id__in=team_ids)
        if project_ids:
            scope_filter |= Q(pk__in=project_ids)
        if not scope_filter.children:
            return []

        qs = base_qs.filter(scope_filter).order_by("team__name", "name")[:500]
        return [project_to_type(p) for p in qs]
