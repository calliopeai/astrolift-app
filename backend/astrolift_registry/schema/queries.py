"""Read-only queries for the registry app."""

from __future__ import annotations

import strawberry
from django.conf import settings
from django.db.models import Q
from strawberry.types import Info

from astrolift_identity.schema.types import ProjectType, project_to_type
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import AppTeamAccess, Container, RegisteredApp, Workload
from astrolift_registry.schema.types import (
    AppTeamAccessType,
    ContainerType,
    RegisteredAppType,
    RenderedManifestType,
    WorkloadType,
    app_team_access_to_type,
    app_to_type,
    container_to_type,
    workload_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


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
    def astrolift_apps(self, info: Info) -> list[RegisteredAppType]:
        qs = RegisteredApp.objects.select_related("organization", "team", "project").order_by("-created_at")[
            :200
        ]
        return [app_to_type(a) for a in qs]

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_apps(self, info: Info) -> list[RegisteredAppType]:
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
            return [app_to_type(a) for a in base_qs.order_by("slug")[:200]]

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
        return [app_to_type(a) for a in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app(self, info: Info, slug: str) -> RegisteredAppType | None:
        app = (
            RegisteredApp.objects.select_related("organization", "team", "project").filter(slug=slug).first()
        )
        return app_to_type(app) if app else None

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
