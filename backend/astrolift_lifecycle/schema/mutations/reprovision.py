"""ReprovisionMutations: re-apply an app's platform half against its running release (#2573)."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql.errors import MutationErrorType
from astrolift_identity.operation_context import named_environment
from astrolift_lifecycle.schema.mutations.helpers import (
    _DEPLOY_PIPELINE_DISABLED_MSG,
    _actor_from_request,
    _deploy_pipeline_disabled,
)
from astrolift_lifecycle.schema.mutations.types import (
    ReprovisionAppInput,
    ReprovisionAppPayload,
    ReprovisionEnvironmentResult,
)
from astrolift_lifecycle.schema.types import ReachabilityStatus
from astrolift_lifecycle.visibility import live_app_rows
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


def _target(*args, **kwargs):
    input = kwargs.get("input") or (args[2] if len(args) > 2 else None)
    return ("RegisteredApp", input.app_slug) if input is not None else None


def _extras(result) -> dict | None:
    data = getattr(result, "data", None)
    if data is None:
        return None
    return {
        "environments": [
            {
                "environment": env.environment_name,
                "deployment_id": str(env.deployment_id) if env.deployment_id else None,
                "reachability_status": env.reachability_status.value,
                "objects_created": len(env.objects_created),
                "objects_updated": len(env.objects_updated),
                "unchanged": env.unchanged,
            }
            for env in data.environments
        ]
    }


@strawberry.type
class ReprovisionMutations:
    @strawberry.field(
        description=(
            "Re-render and re-apply an app's environments (or the one named) against the release each is "
            "running (#2573): zone binding, URL, hostname claims, Ingress and certificate request, DNS and "
            "the rest of the platform half. No build and no new image; pods restart only when the rendered "
            "workload differs. Records a reprovision deployment per environment with a running release. "
            "Returns within seconds: an environment still being applied reads PROVISIONING and settles on "
            "its own. Calling it again while one runs joins that run."
        )
    )
    @mutation_audit(action="app.reprovision", target=_target, extras=_extras)
    @require_permission(
        Permission.APP_DEPLOY,
        scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_DEPLOY),
        operation=named_environment(all_if_absent=True),
    )
    @tenant_scoped()
    def reprovision_app(
        self, info: Info, input: ReprovisionAppInput
    ) -> MutationResultType[ReprovisionAppPayload]:
        from astrolift_lifecycle.reprovision import ReprovisionRefused, reprovision

        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)
        slug = (input.app_slug or "").strip()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "appSlug is required", field="appSlug")
        environment_name = (input.environment_name or "").strip() or None
        # Org-scoped before anything is read or written: slugs are unique only
        # within an organization (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(slug=slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, f"app {slug!r} not found", field="appSlug")
        try:
            results = reprovision(
                app,
                environment_name=environment_name,
                actor=_actor_from_request(info),
                organization_id=org_id,
            )
        except ReprovisionRefused as exc:
            return gql_failure(exc.code, exc.message, field=exc.field)
        payload = ReprovisionAppPayload(
            app_slug=app.slug,
            environments=[
                ReprovisionEnvironmentResult(
                    environment_name=r.environment_name,
                    deployment_id=strawberry.ID(r.deployment_guid) if r.deployment_guid else None,
                    reachability_status=ReachabilityStatus(r.status),
                    objects_created=r.created,
                    objects_updated=r.updated,
                    unchanged=r.unchanged,
                )
                for r in results
            ],
        )
        # A run that failed still returns every environment's result, so the
        # caller sees what did happen next to why the rest did not.
        errors = [
            MutationErrorType(
                code=ErrorCode.INTERNAL.value,
                message=f"environment {r.environment_name!r}: {r.error}",
                field="environmentName",
            )
            for r in results
            if r.error
        ]
        return MutationResultType(ok=not errors, errors=errors, data=payload)
