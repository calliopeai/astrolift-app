"""AppSettingMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Project
from astrolift_identity.step_up import requires_elevation
from astrolift_registry.models import RegisteredApp, RetentionPolicy
from astrolift_registry.schema.mutations.helpers import (
    _actor,
    _bootstrap_app_environments,
    _viewer_can_access_project,
)
from astrolift_registry.schema.mutations.types import (
    AssignAppToProjectInput,
    PauseAppWebhookDeploysInput,
    ResumeAppWebhookDeploysInput,
    ResyncManifestFromRepoInput,
    ResyncManifestPayload,
    SetRetentionPolicyInput,
    UpdateSecurityPolicyInput,
)
from astrolift_registry.schema.types import (
    RegisteredAppType,
    RetentionPolicyType,
    app_to_type,
    retention_policy_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class AppSettingMutations:
    @strawberry.field
    @mutation_audit(action="app.manifest.resync_from_repo")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def resync_astrolift_manifest_from_repo(
        self,
        info: Info,
        input: ResyncManifestFromRepoInput,
    ) -> MutationResultType[ResyncManifestPayload]:
        """Re-fetch the manifest from the source repo + reconcile (#386).

        Wraps the ``manifest_sync.resync_app_manifest_from_repo``
        service in the standard tenant/permission/audit envelope.
        Returns a payload the UI uses to render a summary toast and
        update the last-sync timestamp inline.
        """
        from astrolift_registry.services.manifest_sync import (
            resync_app_manifest_from_repo,
            summarize_changes,
        )

        # Org-scope the app lookup to the caller's tenant before the repo
        # re-fetch + reconcile (SCM call). Slugs are unique only within an
        # org. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(
            slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")

        is_agent = app.workloads.filter(kind="agent", deleted_at__isnull=True).exists()
        if is_agent:
            from astrolift_registry.services.manifest_sync import register_agent_repo

            if app.project_id is None:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "agent has no project binding",
                )
            agent_result = register_agent_repo(
                project=app.project,
                source_kind=app.source_kind,
                source_repo=app.source_repo,
                ref=app.deploy_branch or app.default_branch or "main",
                source_url=app.source_url,
                default_branch=app.default_branch or "main",
                deploy_branch=app.deploy_branch or app.default_branch or "main",
                default_cluster=app.default_tenant_cluster,
                manifest_paths=[app.manifest_path],
            )
            if agent_result.status != "ok":
                code = (
                    ErrorCode.NOT_FOUND.value
                    if agent_result.status in {"no_agents", "no_match"}
                    else ErrorCode.INTERNAL.value
                )
                return gql_failure(code, agent_result.error or agent_result.status)
            slugs = [row.slug for row in agent_result.agents]
            return gql_success(
                ResyncManifestPayload(
                    sync_state="applied",
                    summary=(
                        f"Agent package re-synced: {', '.join(slugs)}."
                        if slugs
                        else "Agent package is already in sync."
                    ),
                    workloads_added=[],
                    workloads_removed=[],
                    workloads_changed=slugs,
                    managed_services_added=[],
                    managed_services_removed=[],
                    env_keys_changed=0,
                    schedules_changed=0,
                )
            )

        result = resync_app_manifest_from_repo(app)
        # ``fetch_failed`` and ``diverged`` are envelope-level errors —
        # nothing was applied, the UI should surface the message in
        # an error toast.
        if result.status == "fetch_failed":
            return gql_failure(
                ErrorCode.INTERNAL.value,
                result.error or "fetch failed",
            )
        if result.status == "diverged":
            return gql_failure(
                ErrorCode.CONFLICT.value,
                result.error or "manifest diverged from repo",
            )

        # Bootstrap AppEnvironment records and OnboardAppWorkflow for apps
        # that were registered before their manifest existed in the repo.
        # This is the "connect source post-registration" case: the wizard
        # normally creates environments at register time; for apps that
        # skipped that step, resync is the natural recovery path.
        #
        # NOTE: do not gate on ``result.env_names`` here. A manifest whose
        # [environments.*] table uses a non-canonical key produces an empty
        # list, but _bootstrap_app_environments still needs to run so that
        # OnboardAppWorkflow fires for apps still in ``pending`` state.
        # The function's own cluster guard and ``provisioning_pending`` flag
        # make it safe to call unconditionally on a successful sync.
        if result.status in ("applied", "in_sync"):
            _bootstrap_app_environments(app, result.env_names)

        summary = "Already in sync." if result.status == "in_sync" else summarize_changes(result.changes)
        return gql_success(
            ResyncManifestPayload(
                sync_state=result.status,
                summary=summary,
                workloads_added=list(result.changes.workloads_added),
                workloads_removed=list(result.changes.workloads_removed),
                workloads_changed=list(result.changes.workloads_changed),
                managed_services_added=list(result.changes.managed_services_added),
                managed_services_removed=list(result.changes.managed_services_removed),
                env_keys_changed=int(result.changes.env_keys_changed),
                schedules_changed=int(result.changes.schedules_changed),
            )
        )

    @strawberry.field
    @mutation_audit(action="app.assign_project")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def assign_astrolift_app_to_project(
        self,
        info: Info,
        input: AssignAppToProjectInput,
    ) -> MutationResultType[RegisteredAppType]:
        """Re-parent an app to a project, or unassign it (#391).

        Distinct from ``transferApp``: this mutation focuses on the
        nav-tree + RBAC-scoping use case the Settings "Assign project"
        card surfaces. It only moves ``project`` (and follows the
        project's team) and only refuses cross-org targets — it does
        not touch ``AppTeamAccess`` grants or the approval policy.

        Permission contract:
        - ``app.update`` on the source app (decorator gate).
        - Resolver-body check: the actor must have an active
          RoleBinding that reaches the destination project (ORG,
          TEAM, or PROJECT scope). Without that, the operator could
          park an app under a project they have no other visibility
          into — a silent privilege-escalation surface.

        ``project_guid=None`` unassigns the app. The team FK is
        preserved on unassign because removing both would orphan the
        app from the home-team workflow entirely; the nav tree
        already handles "team has unassigned apps" as a first-class
        bucket.
        """
        # Org-scope the SOURCE app to the caller's tenant — the target
        # project below is validated against ``app.organization_id``, so
        # scoping the source binds the assignment to the caller's org. Fails
        # closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.select_related("organization", "team", "project")
            .filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")

        viewer = _actor()

        if input.project_guid is None:
            # Unassign — clear the FK. No project-side permission check
            # is needed: removing scope is the safer direction. Idempotent
            # when already unassigned.
            if app.project_id is not None:
                app.project = None
                app.save(update_fields=["project", "updated_at", "version"])
            return gql_success(app_to_type(app))

        project = (
            Project.objects.select_related("organization", "team")
            .filter(guid=str(input.project_guid), deleted_at__isnull=True)
            .first()
        )
        if project is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "project not found",
                field="projectGuid",
            )
        if project.organization_id != app.organization_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "project must belong to the same organization as the app",
                field="projectGuid",
            )

        if not _viewer_can_access_project(project=project, viewer=viewer):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "you do not have access to the target project",
                field="projectGuid",
            )

        # Idempotent: re-assigning to the current project is a no-op
        # and we don't gratuitously bump updated_at.
        if app.project_id == project.id and app.team_id == project.team_id:
            return gql_success(app_to_type(app))

        app.project = project
        # Follow the project's team so the tree stays coherent — an app
        # under project P should live under P.team in the nav tree,
        # never dangle under a different team's branch.
        app.team = project.team
        app.save(update_fields=["project", "team", "updated_at", "version"])
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.security_policy.update")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_astrolift_security_policy(
        self,
        info: Info,
        input: UpdateSecurityPolicyInput,
    ) -> MutationResultType[RegisteredAppType]:
        """Persist the supply-chain / scanner policy for an app (#313).

        The PromoteDeploymentWorkflow reads
        ``app.security_policy_resolved`` at deploy-gate time and short-
        circuits the promote when any active knob would block (emitting
        a ``SupplyChainBlockedPayload`` event). This mutation is the
        only sanctioned writer of that JSON blob — it goes through the
        standard tenant / permission / audit envelope so the audit log
        captures who tightened or loosened the gate.

        Threshold semantics: ``block_on_high_cve_threshold`` of None
        clears the count-based gate entirely; a non-null integer must
        be >= 1. A threshold of 0 would block every image and is
        almost certainly an input error — refuse with VALIDATION.
        """
        # Org-scope the app lookup to the caller's tenant. This is the only
        # sanctioned writer of the supply-chain gate the promote workflow
        # enforces, so an unscoped slug lookup would let a caller loosen
        # (or tighten) a sibling org's deploy gate. Fails closed (NOT_FOUND)
        # when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(
            slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")

        if input.block_on_high_cve_threshold is not None and input.block_on_high_cve_threshold < 1:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "blockOnHighCveThreshold must be at least 1 (use null to clear the threshold)",
                field="blockOnHighCveThreshold",
            )

        app.security_policy = {
            "block_on_critical_cves": bool(input.block_on_critical_cves),
            "block_on_missing_signature": bool(input.block_on_missing_signature),
            "block_on_high_cve_threshold": (
                int(input.block_on_high_cve_threshold)
                if input.block_on_high_cve_threshold is not None
                else None
            ),
        }
        app.save(update_fields=["security_policy", "updated_at", "version"])
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.webhook_deploys.pause")
    @requires_elevation(action_label="app.webhook_deploys.pause")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def pause_astrolift_app_webhook_deploys(
        self,
        info: Info,
        input: PauseAppWebhookDeploysInput,
    ) -> MutationResultType[RegisteredAppType]:
        """Pause app-global webhook-fired deploys (#399).

        Stops the deploy storm from CI / push / scheduled triggers
        across every environment without paging through each env's
        deploys_paused toggle and without taking ingress down. Manual
        operator deploys (``trigger_kind == "manual"``) continue to
        flow — the explicit on-call escape valve. The gate is
        consulted by ``start_deployment`` and the CI REST endpoint;
        forward-only (re-deploys remain blocked but teardown +
        rollback are unaffected, matching the per-env semantics).

        Idempotent: re-firing pause on an already-paused app no-ops
        beyond a fresh actor stamp would be — we explicitly skip the
        save when the flag is already set so the timestamp + reason
        on the row reflect the *original* pause, not the latest
        re-confirmation. Operators get reliable "paused 3h ago by X"
        copy that way.
        """

        # Org-scope the app lookup to the caller's tenant before pausing
        # webhook-fired deploys. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(
            slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")

        if app.webhook_deploys_paused:
            # Already paused — keep the original actor / timestamp /
            # reason so the UI's "paused 3h ago" copy stays accurate.
            return gql_success(app_to_type(app))

        actor = _actor()
        reason = (input.reason or "").strip()[:512]
        app.webhook_deploys_paused = True
        app.webhook_deploys_paused_at = timezone.now()
        app.webhook_deploys_paused_by = actor
        app.webhook_deploys_pause_reason = reason
        app.save(
            update_fields=[
                "webhook_deploys_paused",
                "webhook_deploys_paused_at",
                "webhook_deploys_paused_by",
                "webhook_deploys_pause_reason",
                "updated_at",
                "version",
            ]
        )
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.webhook_deploys.resume")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def resume_astrolift_app_webhook_deploys(
        self,
        info: Info,
        input: ResumeAppWebhookDeploysInput,
    ) -> MutationResultType[RegisteredAppType]:
        """Lift the app-global webhook-deploy pause (#399).

        Resumes acceptance of webhook-fired deploys; the next push /
        CI event lands a Deployment row as normal. Clears the
        actor / timestamp / reason on the row so a subsequent pause
        records fresh context — keeping stale state would mislead
        the Settings card's "Paused by X" copy.

        Idempotent on an already-resumed app.
        """
        # Org-scope the app lookup to the caller's tenant before resuming
        # webhook-fired deploys. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(
            slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")

        if not app.webhook_deploys_paused:
            return gql_success(app_to_type(app))

        app.webhook_deploys_paused = False
        app.webhook_deploys_paused_at = None
        app.webhook_deploys_paused_by = None
        app.webhook_deploys_pause_reason = ""
        app.save(
            update_fields=[
                "webhook_deploys_paused",
                "webhook_deploys_paused_at",
                "webhook_deploys_paused_by",
                "webhook_deploys_pause_reason",
                "updated_at",
                "version",
            ]
        )
        return gql_success(app_to_type(app))

    @strawberry.mutation
    @mutation_audit(action="app.retention_policy.set")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_retention_policy(
        self, info: Info, input: SetRetentionPolicyInput
    ) -> MutationResultType[RetentionPolicyType]:
        tenant = get_current_tenant()
        app = RegisteredApp.objects.filter(
            organization_id=tenant.organization_id,
            slug=input.app_slug,
            deleted_at__isnull=True,
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")

        valid_signals = {s.value for s in RetentionPolicy.Signal}
        if input.signal not in valid_signals:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"signal must be one of: {', '.join(sorted(valid_signals))}",
                field="signal",
            )
        if input.retention_days < 1:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "retentionDays must be at least 1",
                field="retentionDays",
            )

        actor = info.context.request.user

        existing = RetentionPolicy.objects.filter(
            registered_app=app,
            signal=input.signal,
            deleted_at__isnull=True,
        ).first()

        if existing is not None:
            existing.retention_days = input.retention_days
            existing.updated_by = actor
            existing.save(update_fields=["retention_days", "updated_by", "updated_at", "version"])
            return gql_success(retention_policy_to_type(existing))

        policy = RetentionPolicy.objects.create(
            registered_app=app,
            signal=input.signal,
            retention_days=input.retention_days,
            created_by=actor,
            updated_by=actor,
        )
        return gql_success(retention_policy_to_type(policy))
