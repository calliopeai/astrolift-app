"""EnvironmentMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.models import (
    AppEnvironment,
)
from astrolift_lifecycle.schema.mutations.types import (
    EnvironmentByIdInput,
)
from astrolift_lifecycle.schema.types import (
    AppEnvironmentType,
    app_env_to_type,
)
from astrolift_lifecycle.scopes import environment_app_scope
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class EnvironmentMutations:
    @strawberry.field
    @mutation_audit(action="environment.pause")
    @require_permission(Permission.APP_DEPLOY, scope=environment_app_scope("input.id"))
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
            AppEnvironment.objects.select_related("registered_app", "tenant_cluster", "managed_domain")
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
    @require_permission(Permission.APP_DEPLOY, scope=environment_app_scope("input.id"))
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
            AppEnvironment.objects.select_related("registered_app", "tenant_cluster", "managed_domain")
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
    @require_permission(Permission.APP_DEPLOY, scope=environment_app_scope("input.id"))
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
            AppEnvironment.objects.select_related("registered_app", "tenant_cluster", "managed_domain")
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
    @require_permission(Permission.APP_DEPLOY, scope=environment_app_scope("input.id"))
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
            AppEnvironment.objects.select_related("registered_app", "tenant_cluster", "managed_domain")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found")
        if env.ingress_paused:
            env.ingress_paused = False
            env.save(update_fields=["ingress_paused", "updated_at", "version"])
        return gql_success(app_env_to_type(env))
