"""Read-only queries for the registry app."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import Container, RegisteredApp, Workload
from astrolift_registry.schema.types import (
    ContainerType,
    RegisteredAppType,
    RenderedManifestType,
    WorkloadType,
    app_to_type,
    container_to_type,
    workload_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


@strawberry.type
class RegistryQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_apps(self, info: Info) -> list[RegisteredAppType]:
        qs = RegisteredApp.objects.select_related("organization", "team", "project").order_by("-created_at")[
            :200
        ]
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
    def astrolift_workloads(self, info: Info, app_slug: str | None = None) -> list[WorkloadType]:
        qs = Workload.objects.select_related("registered_app")
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [workload_to_type(w) for w in qs[:200]]

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

        app = (
            RegisteredApp.objects.select_related("organization")
            .filter(slug=app_slug)
            .first()
        )
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
        namespace = (
            app.k8s_namespace
            or f"{app.organization.slug}-{app.slug}"
        )
        image = image_tag or "preview"

        try:
            normalized = normalize(
                parse_raw(app.manifest_raw),
                defaults=NormalizationDefaults(),
            )
        except ManifestError as exc:
            return RenderedManifestType(
                app_slug=app.slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                error=str(exc),
                error_path=getattr(exc, "path", None) or None,
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
        )
