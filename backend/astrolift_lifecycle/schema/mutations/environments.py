"""EnvironmentMutations — split from the monolithic mutations module."""

from __future__ import annotations

import re

import strawberry
from django.db import IntegrityError, transaction
from django.db.models import Q
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.operation_context import environment_operation
from astrolift_lifecycle.models import (
    AppEnvironment,
)
from astrolift_lifecycle.schema.mutations.types import (
    CreateAppEnvironmentInput,
    EnvironmentByIdInput,
)
from astrolift_lifecycle.schema.types import (
    AppEnvironmentType,
    app_env_to_type,
)
from astrolift_lifecycle.scopes import environment_app_scope
from astrolift_lifecycle.visibility import live_lifecycle_rows
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

# One DNS label: the name lands in the environment's namespace and hostname.
_ENV_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


@strawberry.type
class EnvironmentMutations:
    @strawberry.field
    @mutation_audit(action="environment.create")
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def create_app_environment(
        self, info: Info, input: CreateAppEnvironmentInput
    ) -> MutationResultType[AppEnvironmentType]:
        """Add an environment to a registered app after registration (#2398).

        Before this an app's environments came only from registration and
        repo resync, so an app whose manifest was a staged draft could not
        gain one at all (DV-9250). The environment is placed the way the
        bootstrap places one (namespace per #1922, hostname label, managed
        domain), and the applied manifest's managed services that target it
        are reconciled. Its namespace is created by its first deploy.
        """
        from astrolift_clusters.models import TenantCluster, resolve_managed_domain
        from astrolift_lifecycle.services.preview_lineage import PREVIEW_ENV_PREFIX
        from astrolift_manifest.normalize import normalize
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.persist import (
            actor_may_attach_project_services,
            allow_project_attach,
            reconcile_managed_services,
        )
        from astrolift_registry.hostname_claims import sync_workload_hostname_claims
        from astrolift_registry.models import RegisteredApp
        from astrolift_registry.schema.mutations.helpers import _place_app_environment

        # Slugs are unique only within an org. Fails closed (NOT_FOUND) when
        # org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
            .filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")
        if app.provisioning_status in (
            RegisteredApp.ProvisioningStatus.TEARING_DOWN,
            RegisteredApp.ProvisioningStatus.DEREGISTERED,
        ):
            return gql_failure(ErrorCode.PRECONDITION.value, "app is being deregistered", field="appSlug")

        name = (input.name or "").strip()
        if not _ENV_NAME.match(name):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "environment name must be lowercase letters, digits and hyphens, at most 63 characters",
                field="name",
            )
        if name.startswith(PREVIEW_ENV_PREFIX):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"names starting with {PREVIEW_ENV_PREFIX!r} are reserved for previews",
                field="name",
            )
        if AppEnvironment.objects.filter(registered_app=app, name=name, deleted_at__isnull=True).exists():
            return gql_failure(ErrorCode.CONFLICT.value, f"environment {name!r} already exists", field="name")

        if input.cluster_slug:
            # Org-scope the cluster: the caller's org or a platform-shared
            # null-org cluster, never another org's private one (#1183).
            cluster = TenantCluster.objects.filter(
                Q(organization_id=org_id) | Q(organization_id__isnull=True),
                slug=input.cluster_slug,
                deleted_at__isnull=True,
                is_active=True,
            ).first()
            if cluster is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterSlug")
        else:
            cluster = app.default_tenant_cluster
            if cluster is None:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "app has no default cluster; choose one",
                    field="clusterSlug",
                )
        if cluster.lifecycle != TenantCluster.Lifecycle.MANAGED.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value, f"cluster {cluster.slug!r} is not managed", field="clusterSlug"
            )

        services = ()
        if app.manifest_raw:
            try:
                services = normalize(parse_raw(app.manifest_raw)).managed_services
            except ManifestError:
                services = ()

        try:
            with transaction.atomic():
                # Bootstrap places every environment on one cluster; this one
                # may land on another, where the app namespace would serve
                # the app's bare hostname a second time.
                env = _place_app_environment(
                    app,
                    name,
                    cluster,
                    resolve_managed_domain(app.organization, for_preview=False),
                    any_cluster=True,
                )
                if any(service.environment == name for service in services):
                    # Reconcile only what has somewhere to land: a service
                    # targeting an environment the app still lacks would
                    # fail the whole reconcile.
                    live = set(
                        AppEnvironment.objects.filter(
                            registered_app=app, deleted_at__isnull=True
                        ).values_list("name", flat=True)
                    )
                    with allow_project_attach(actor_may_attach_project_services(app)):
                        reconcile_managed_services(app, tuple(s for s in services if s.environment in live))
        except IntegrityError:
            # A concurrent create of the same name won the unique constraint.
            return gql_failure(ErrorCode.CONFLICT.value, f"environment {name!r} already exists", field="name")
        except ValueError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc))

        sync_workload_hostname_claims(app)
        return gql_success(app_env_to_type(env))

    @strawberry.field
    @mutation_audit(action="environment.pause")
    @require_permission(
        Permission.APP_DEPLOY,
        scope=environment_app_scope("input.id", permission=Permission.APP_DEPLOY),
        operation=environment_operation("input.id"),
    )
    @tenant_scoped()
    def pause_environment(
        self, info: Info, input: EnvironmentByIdInput
    ) -> MutationResultType[AppEnvironmentType]:
        """Pause reconciliation for an environment.

        While paused, ``startDeployment`` rejects with PRECONDITION
        (the same gate the deploy mutation already checks against
        ``env.deploys_paused``). Operators use this when an env is
        misconfigured or under maintenance and we don't want CI or
        push triggers to land deploys mid-investigation.
        """
        # Org-scope the by-guid lookup: AppEnvironment reaches the org via
        # registered_app. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        env = (
            live_lifecycle_rows(AppEnvironment.objects.all())
            .select_related("registered_app", "tenant_cluster", "managed_domain")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found")
        if not env.deploys_paused:
            env.deploys_paused = True
            env.save(update_fields=["deploys_paused", "updated_at", "version"])
        return gql_success(app_env_to_type(env))

    @strawberry.field
    @mutation_audit(action="environment.resume")
    @require_permission(
        Permission.APP_DEPLOY,
        scope=environment_app_scope("input.id", permission=Permission.APP_DEPLOY),
        operation=environment_operation("input.id"),
    )
    @tenant_scoped()
    def resume_environment(
        self, info: Info, input: EnvironmentByIdInput
    ) -> MutationResultType[AppEnvironmentType]:
        """Lift the pause flag — does NOT replay queued deploys; the
        next CI/push trigger or manual ``startDeployment`` proceeds
        as usual."""
        # Org-scope the by-guid lookup: AppEnvironment reaches the org via
        # registered_app. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        env = (
            live_lifecycle_rows(AppEnvironment.objects.all())
            .select_related("registered_app", "tenant_cluster", "managed_domain")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found")
        if env.deploys_paused:
            env.deploys_paused = False
            env.save(update_fields=["deploys_paused", "updated_at", "version"])
        return gql_success(app_env_to_type(env))

    @strawberry.field
    @mutation_audit(action="environment.pause_ingress")
    @require_permission(
        Permission.APP_DEPLOY,
        scope=environment_app_scope("input.id", permission=Permission.APP_DEPLOY),
        operation=environment_operation("input.id"),
    )
    @tenant_scoped()
    def pause_app_ingress(
        self, info: Info, input: EnvironmentByIdInput
    ) -> MutationResultType[AppEnvironmentType]:
        """Pause live traffic at the Ingress layer for an env (#378).

        Independent of ``deploys_paused``: the renderer keeps emitting
        the per-CustomDomain Ingress on every deploy, but tagged so
        the controller serves a 503 instead of the app. Lets an
        operator put an app in maintenance ("we'll be right back")
        without freezing the deploy pipeline, and lets them keep
        shipping fixes while traffic stays parked. The flag is
        consulted by ``_render_app_ingresses_and_tls`` on the next
        deploy render — re-deploy or wait for the next CI push for it
        to take effect cluster-side.
        """
        # Org-scope the by-guid lookup: AppEnvironment reaches the org via
        # registered_app. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        env = (
            live_lifecycle_rows(AppEnvironment.objects.all())
            .select_related("registered_app", "tenant_cluster", "managed_domain")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found")
        if not env.ingress_paused:
            env.ingress_paused = True
            env.save(update_fields=["ingress_paused", "updated_at", "version"])
        return gql_success(app_env_to_type(env))

    @strawberry.field
    @mutation_audit(action="environment.resume_ingress")
    @require_permission(
        Permission.APP_DEPLOY,
        scope=environment_app_scope("input.id", permission=Permission.APP_DEPLOY),
        operation=environment_operation("input.id"),
    )
    @tenant_scoped()
    def resume_app_ingress(
        self, info: Info, input: EnvironmentByIdInput
    ) -> MutationResultType[AppEnvironmentType]:
        """Lift the ingress-pause flag — restores normal routing on
        the next render. As with ``pause_app_ingress``, the change is
        picked up by the next deploy."""
        # Org-scope the by-guid lookup: AppEnvironment reaches the org via
        # registered_app. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        env = (
            live_lifecycle_rows(AppEnvironment.objects.all())
            .select_related("registered_app", "tenant_cluster", "managed_domain")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found")
        if env.ingress_paused:
            env.ingress_paused = False
            env.save(update_fields=["ingress_paused", "updated_at", "version"])
        return gql_success(app_env_to_type(env))
