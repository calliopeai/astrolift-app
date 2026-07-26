"""PreviewMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db import transaction
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.models import (
    AppEnvironment,
    Deployment,
    PreviewEnvironment,
)
from astrolift_lifecycle.schema.mutations.helpers import (
    _DEPLOY_PIPELINE_DISABLED_MSG,
    _actor_from_request,
    _build_preview_workflow_id,
    _deploy_pipeline_disabled,
    _manual_preview_namespace,
    _record_workflow_run,
    _slugify_branch,
    _teardown_workflow_id,
)
from astrolift_lifecycle.schema.mutations.types import (
    CreatePreviewEnvironmentInput,
    ExtendPreviewTtlInputGql,
    TearDownPreviewInputGql,
)
from astrolift_lifecycle.schema.types import (
    DeploymentType,
    PreviewEnvironmentType,
    deployment_to_type,
    preview_to_type,
)
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.client import (
    start_workflow,
)
from astrolift_workflows.inputs import (
    BuildPreviewInput,
    TearDownPreviewInput,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class PreviewMutations:
    @strawberry.field
    @mutation_audit(action="preview.tear_down")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def tear_down_preview(
        self, info: Info, input: TearDownPreviewInputGql
    ) -> MutationResultType[DeploymentType]:
        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)
        # Org-scope the by-guid lookup to the caller's tenant before the
        # teardown workflow side effect. PreviewEnvironment reaches the org
        # via registered_app. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        preview = (
            PreviewEnvironment.objects.select_related("registered_app")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if preview is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "preview environment not found")

        actor = _actor_from_request(info)

        handle = start_workflow(
            "TearDownPreviewWorkflow",
            args=[
                TearDownPreviewInput(
                    preview_environment_id=preview.pk,
                    actor=actor,
                )
            ],
            workflow_id=_teardown_workflow_id(str(preview.guid)),
        )
        if handle.enqueued:
            _record_workflow_run(
                kind="TearDownPreviewWorkflow",
                workflow_id=handle.workflow_id,
                run_id=handle.run_id,
                organization_id=tenant.organization_id if tenant else None,
                registered_app_id=preview.registered_app_id,
                app_environment_id=None,
                actor=actor,
            )

        latest = (
            Deployment.objects.filter(
                registered_app_id=preview.registered_app_id,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if latest is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "no deployment exists for this app yet",
            )
        return gql_success(deployment_to_type(latest))

    @strawberry.field
    @mutation_audit(action="preview.extend_ttl")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def extend_preview_ttl(
        self, info: Info, input: ExtendPreviewTtlInputGql
    ) -> MutationResultType[PreviewEnvironmentType]:
        """Push the preview's auto-teardown out by ``days`` (#431).

        ``days`` must be one of ``{1, 7, 30}``. The new ``ttl_until``
        is capped at +30 days from now even on a chained set of
        extensions — see :meth:`PreviewEnvironment.extend_ttl`.

        Returns the updated preview type with the new ``ttl_until``
        echoed back so the UI can refresh the countdown without a
        second round-trip. No workflow is started — the GC sweep
        consults ``ttl_until`` on its own cadence.
        """
        from astrolift_lifecycle.models.preview_environment import (
            PREVIEW_TTL_EXTEND_DAYS,
        )

        if input.days not in PREVIEW_TTL_EXTEND_DAYS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"days must be one of {list(PREVIEW_TTL_EXTEND_DAYS)}; got {input.days}",
            )
        # Org-scope the by-guid lookup: PreviewEnvironment reaches the org via
        # registered_app. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        preview = (
            PreviewEnvironment.objects.select_related(
                "registered_app",
                "registered_app__organization",
                "registered_app__default_tenant_cluster",
                "app_environment__tenant_cluster",
            )
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if preview is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "preview environment not found")
        if preview.status == PreviewEnvironment.Status.TORN_DOWN.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cannot extend a torn-down preview",
            )

        try:
            preview.extend_ttl(days=input.days)
        except ValueError as exc:
            # Model layer mirrors the resolver's validation — keep
            # both so programmatic callers (workflows, fixtures) hit
            # the same guard.
            return gql_failure(ErrorCode.VALIDATION.value, str(exc))

        preview.save(update_fields=["ttl_until", "updated_at", "version"])
        return gql_success(preview_to_type(preview))

    @strawberry.field
    @mutation_audit(action="preview.create_manual")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def create_preview_environment(
        self,
        info: Info,
        input: CreatePreviewEnvironmentInput,
    ) -> MutationResultType[PreviewEnvironmentType]:
        """Manually spin up a preview environment from a branch (#751).

        Companion to the auto path (PR webhook → BuildPreviewWorkflow).
        Operators hit this from the Previews page when they want to
        burn a preview for a long-lived branch, a force-pushed fork, or
        a draft PR the webhook isn't watching.

        Idempotent on ``(app, branch)``: a re-fire when an active
        manual preview already exists for the branch returns that row
        as success (matches ``addAppDomain``'s idempotent re-add).

        Refuses to start when:

        * The app's ``preview_enabled`` flag is off (operator already
          opted out of previews at the app level).
        * The deploy pipeline feature is gated off.
        * The app isn't bound to a tenant cluster (no place to land
          the namespace).
        * ``branch`` doesn't shake out to a non-empty RFC 1123 label
          (k8s namespace constraint).
        """
        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)

        # Org-scope the app lookup to the caller's tenant before the
        # preview-build side effect (creates env + preview rows, fires
        # BuildPreviewWorkflow). Slugs are unique only within an org.
        # Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization", "default_tenant_cluster")
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        if not app.preview_enabled:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"app {app.slug!r} has preview environments disabled",
            )

        branch = (input.branch or "").strip()
        if not branch:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "branch is required",
                field="branch",
            )

        branch_slug = _slugify_branch(branch)
        if not branch_slug:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"branch {branch!r} does not contain any RFC 1123 label characters",
                field="branch",
            )

        cluster = app.default_tenant_cluster
        if cluster is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"app {app.slug!r} has no default tenant cluster bound — provision one first",
            )

        # Idempotent re-fire: existing active manual preview for the
        # same (app, branch) returns success rather than racing a
        # second namespace through the unique index.
        existing = (
            PreviewEnvironment.objects.select_related("registered_app")
            .filter(
                registered_app=app,
                branch=branch,
                is_manual=True,
                deleted_at__isnull=True,
            )
            .first()
        )
        if existing is not None:
            return gql_success(preview_to_type(existing))

        environment_name = (input.environment_name or "").strip() or f"preview-{branch_slug}"
        org_slug = (
            getattr(app.organization, "slug", None) or getattr(app.organization, "name", "") or "org"
        ).lower()
        namespace = _manual_preview_namespace(org_slug=org_slug, app_slug=app.slug, branch_slug=branch_slug)
        # Hostname follows the platform's preview wildcard convention
        # but keyed on the branch slug (no PR number). The cluster's
        # ingress-target resolution happens at apply time in the
        # BuildPreviewWorkflow; here we just record the stable name
        # the operator-facing surfaces (#751 FE, audit log) cite.
        from astrolift_clusters.models import resolve_managed_domain

        _managed_domain = resolve_managed_domain(app.organization, for_preview=True)
        # ``preview-<branch>.<app>.<org>`` plus the install's managed zone
        # when one exists; without a zone, stop at the org slug rather than
        # repeating it (the old ``... or org_slug`` fallback doubled it).
        _base = f"preview-{branch_slug}.{app.slug}.{org_slug}"
        _zone = getattr(_managed_domain, "zone", None)
        hostname = (f"{_base}.{_zone}" if _zone else _base).lower()

        with transaction.atomic():
            env = AppEnvironment.objects.create(
                registered_app=app,
                tenant_cluster=cluster,
                name=environment_name,
                url=f"https://{hostname}",
                managed_domain=_managed_domain,
                required_approvals=0,
            )
            preview = PreviewEnvironment.objects.create(
                registered_app=app,
                pr_number=None,
                branch=branch,
                is_manual=True,
                status=PreviewEnvironment.Status.BUILDING,
                hostname=hostname,
                namespace=namespace,
                app_environment=env,
            )

        actor = _actor_from_request(info)
        handle = start_workflow(
            "BuildPreviewWorkflow",
            args=[
                BuildPreviewInput(
                    preview_environment_id=preview.pk,
                    actor=actor,
                ),
            ],
            workflow_id=_build_preview_workflow_id(str(preview.guid)),
        )
        if handle.enqueued:
            _record_workflow_run(
                kind="BuildPreviewWorkflow",
                workflow_id=handle.workflow_id,
                run_id=handle.run_id,
                organization_id=tenant.organization_id if tenant else None,
                registered_app_id=preview.registered_app_id,
                app_environment_id=env.pk,
                actor=actor,
            )

        return gql_success(preview_to_type(preview))
