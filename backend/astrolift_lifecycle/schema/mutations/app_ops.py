"""AppOpsMutations — split from the monolithic mutations module."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.operation_context import named_environment, workload_operation
from astrolift_lifecycle.action_preconditions import locked_workload, recheck_action
from astrolift_lifecycle.models import (
    AppEnvironment,
)
from astrolift_lifecycle.schema.mutations.types import (
    CiSecretValidationType,
    InstallSourceWebhookInput,
    InstallSourceWebhookPayload,
    PushCiSecretsPayload,
    PushCiSecretsToRepoInput,
    PushCiWorkflowPayload,
    PushCiWorkflowToRepoInput,
    RestartWorkloadInput,
    RetryAstroliftAutowireInput,
    RetryAstroliftAutowirePayload,
    ScaleWorkloadInput,
    TriggerDeployWorkflowInput,
    TriggerDeployWorkflowPayload,
    ValidateAstroliftCiSecretsInput,
    ValidateAstroliftCiSecretsPayload,
    _WorkloadOpPayload,
)
from astrolift_lifecycle.visibility import live_app_rows, live_lifecycle_rows
from astrolift_lifecycle.workload_targets import (
    action_audit_extras,
    action_audit_target,
    action_target,
    check_target_match,
)
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import workload_action_target_to_type
from astrolift_registry.scopes import app_scope_by_slug, app_scope_by_workload_guid
from astrolift_registry.viewer_actions import ActionPermission
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class AppOpsMutations:
    @strawberry.field
    @mutation_audit(action="app.ci.dispatch")
    @require_permission(
        Permission.APP_DEPLOY,
        scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_DEPLOY),
        operation=named_environment(environment_field="_absent", all_if_absent=True),
    )
    @tenant_scoped()
    def trigger_astrolift_deploy_workflow(
        self,
        info: Info,
        input: TriggerDeployWorkflowInput,
    ) -> MutationResultType[TriggerDeployWorkflowPayload]:
        """Rebuild + deploy by firing the source host's workflow-
        dispatch API for the app's CI workflow (#387).

        Does NOT bypass the env's approval policy: if the app gates
        deploys on human approval (``requires_approval=True``) OR any
        env on the app carries ``required_approvals > 0``, the
        mutation refuses with PRECONDITION so the operator falls back
        to the regular ``startDeployment`` + approver flow rather
        than sneaking a build through CI dispatch.
        """
        from astrolift_scm.services.workflows import (
            WorkflowDispatchError,
            dispatch_astrolift_ci_workflow,
        )

        # Org-scope the app lookup to the caller's tenant before dispatching
        # the CI workflow (external SCM call). Slugs are unique only within
        # an org. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        # Approval-gate. Either app-level (requires_approval=True) or
        # env-level (any active env with required_approvals > 0) is
        # enough to bounce this path. The operator gets a clear hint
        # to take the regular deploy path with approvers.
        gates_on_approval = bool(app.requires_approval)
        if not gates_on_approval:
            gates_on_approval = (
                live_lifecycle_rows(AppEnvironment.objects.all())
                .filter(
                    registered_app=app,
                    required_approvals__gt=0,
                    deleted_at__isnull=True,
                )
                .exists()
            )
        if gates_on_approval:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                ("deploy workflow requires approval — use startDeployment with approver flow"),
            )

        branch_input = (input.branch or "").strip() or None
        try:
            result = dispatch_astrolift_ci_workflow(app, branch=branch_input)
        except NotImplementedError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        except WorkflowDispatchError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message)

        if not result.ok:
            # WORKFLOW_FILE_MISSING gets surfaced as PRECONDITION so
            # the FE can pivot to the #384 "Sync CI workflow" flow.
            # Other host failures (auth, network, generic API) ride
            # the same envelope; the message carries enough for the
            # toast to be useful.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error_message or "workflow dispatch failed",
            )

        dispatched_branch = (branch_input or app.deploy_branch or "main").strip() or "main"
        return gql_success(
            TriggerDeployWorkflowPayload(
                run_url=result.run_url,
                dispatched_branch=dispatched_branch,
            ),
        )

    # ----------------------------------------------------------------
    # Live workload ops (#388): rolling restart + scale replicas
    # ----------------------------------------------------------------
    #
    # First-line incident-response actions. The mutation resolves the
    # Workload row by GUID, dispatches through
    # ``astrolift_lifecycle.services.k8s_ops`` (which speaks to the
    # bound cluster's ClusterDriver), and returns the read-back
    # revision / replica counts. ``app.deploy`` is the gate — these
    # change live runtime state, so we don't expand the permission
    # surface beyond what manual deploys already need.

    @strawberry.field
    @mutation_audit(action="app.workload.restart", target=action_audit_target, extras=action_audit_extras)
    @require_permission(
        Permission.APP_DEPLOY,
        scope=app_scope_by_workload_guid("input.workload_id", permission=Permission.APP_DEPLOY),
        operation=workload_operation(),
    )
    @tenant_scoped()
    def restart_astrolift_workload(
        self,
        info: Info,
        input: RestartWorkloadInput,
        if_match_version: int | None = None,
    ) -> MutationResultType[_WorkloadOpPayload]:
        """Trigger a rolling restart on the workload's Deployment.

        Equivalent to ``kubectl rollout restart deployment/<name>``:
        annotates the pod template with a fresh
        ``kubectl.kubernetes.io/restartedAt`` so the Deployment
        controller rolls a new ReplicaSet. No image change, no
        manifest re-render — fastest way to bounce wedged pods or
        propagate a sidecar update.
        """
        from astrolift_lifecycle.services.k8s_ops import (
            K8sOpError,
            rollout_restart_workload,
        )

        # Org-scope the by-guid lookup before the cluster restart side
        # effect: Workload reaches the org via registered_app. Fails closed
        # (NOT_FOUND) when org_id is None (#1183).
        from core.scope_args import read_guid

        if input.environment_id is not None and read_guid({"id": input.environment_id}, "id") is None:
            return gql_failure(ErrorCode.VALIDATION.value, "environmentId must be a GUID")
        if (
            input.expected_cluster_id is not None
            and read_guid({"id": input.expected_cluster_id}, "id") is None
        ):
            return gql_failure(ErrorCode.VALIDATION.value, "expectedClusterId must be a GUID")
        lock = (
            locked_workload(str(input.workload_id), environment_guid=str(input.environment_id))
            if input.environment_id is not None
            else locked_workload(str(input.workload_id))
        )
        with lock as (workload, environment):
            if workload is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "workload not found")
            mismatch = check_target_match(workload, environment, input, if_match_version=if_match_version)
            if mismatch is not None:
                return mismatch
            if environment is None:
                return cast(
                    MutationResultType[_WorkloadOpPayload],
                    gql_failure(ErrorCode.PRECONDITION.value, "workload has no active environment"),
                )
            recheck_action(Permission.APP_DEPLOY, workload, environment)
            target = workload_action_target_to_type(
                action_target(workload, environment), ActionPermission(True)
            )

            try:
                result = rollout_restart_workload(workload, environment=environment)
            except K8sOpError as exc:
                return gql_failure(exc.code, exc.message)
            workload.save(update_fields=["updated_at", "version"])
            return gql_success(
                _WorkloadOpPayload(
                    workload_id=input.workload_id,
                    new_revision=result.new_revision,
                    desired_replicas=None,
                    ready_replicas=None,
                    operation_id=str(uuid4()),
                    target=target,
                    workload_version=workload.version,
                ),
            )

    @strawberry.field
    @mutation_audit(action="app.workload.scale", target=action_audit_target, extras=action_audit_extras)
    @require_permission(
        Permission.APP_DEPLOY,
        scope=app_scope_by_workload_guid("input.workload_id", permission=Permission.APP_DEPLOY),
        operation=workload_operation(),
    )
    @tenant_scoped()
    def scale_astrolift_workload(
        self,
        info: Info,
        input: ScaleWorkloadInput,
        if_match_version: int | None = None,
    ) -> MutationResultType[_WorkloadOpPayload]:
        """Patch the workload's Deployment ``spec.replicas``.

        Bounds are clamped server-side: ``0 <= replicas <= min(20,
        env.max_replicas)``. Out-of-range scales return
        ``VALIDATION`` with the bounds in the message — the UI surfaces
        the message verbatim so the operator knows the upper they're
        hitting.
        """
        from astrolift_lifecycle.services.k8s_ops import (
            K8sOpError,
            scale_workload,
        )

        # Org-scope the by-guid lookup before the cluster scale side effect:
        # Workload reaches the org via registered_app. Fails closed
        # (NOT_FOUND) when org_id is None (#1183).
        from core.scope_args import read_guid

        if input.environment_id is not None and read_guid({"id": input.environment_id}, "id") is None:
            return gql_failure(ErrorCode.VALIDATION.value, "environmentId must be a GUID")
        if (
            input.expected_cluster_id is not None
            and read_guid({"id": input.expected_cluster_id}, "id") is None
        ):
            return gql_failure(ErrorCode.VALIDATION.value, "expectedClusterId must be a GUID")
        lock = (
            locked_workload(str(input.workload_id), environment_guid=str(input.environment_id))
            if input.environment_id is not None
            else locked_workload(str(input.workload_id))
        )
        with lock as (workload, environment):
            if workload is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "workload not found")
            mismatch = check_target_match(workload, environment, input, if_match_version=if_match_version)
            if mismatch is not None:
                return mismatch
            if environment is None:
                return cast(
                    MutationResultType[_WorkloadOpPayload],
                    gql_failure(ErrorCode.PRECONDITION.value, "workload has no active environment"),
                )
            recheck_action(Permission.APP_DEPLOY, workload, environment)
            target = workload_action_target_to_type(
                action_target(workload, environment), ActionPermission(True)
            )

            try:
                result = scale_workload(workload, int(input.replicas), environment=environment)
            except K8sOpError as exc:
                return gql_failure(exc.code, exc.message)
            workload.save(update_fields=["updated_at", "version"])
            return gql_success(
                _WorkloadOpPayload(
                    workload_id=input.workload_id,
                    new_revision=None,
                    desired_replicas=result.current_replicas,
                    ready_replicas=result.ready_replicas,
                    operation_id=str(uuid4()),
                    target=target,
                    workload_version=workload.version,
                ),
            )

    # ----------------------------------------------------------------
    # #385 — one-click source-webhook install
    # ----------------------------------------------------------------
    #
    # Registers (or refreshes) the push-event webhook on the app's
    # configured source repo. ``app.update`` is the gate — this is a
    # repo-config-touching action, not a deploy. The service layer
    # picks the same connection the manifest sync / workflow dispatch
    # paths use so a single identity drives every SCM call for an app.

    @strawberry.field
    @mutation_audit(action="app.ci.install_webhook")
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def install_astrolift_source_webhook(
        self,
        info: Info,
        input: InstallSourceWebhookInput,
    ) -> MutationResultType[InstallSourceWebhookPayload]:
        """Install (or refresh) the source-host push-event webhook for
        ``app_slug`` pointing at the platform's receiver URL.

        The receiver URL shape is
        ``{PLATFORM_API_URL}/api/webhooks/github/{app.guid}/`` — keyed
        on the app's GUID so deliveries route without grepping the
        payload. The HMAC secret is rotated on every call (created or
        refreshed) and persisted on the picked SourceConnection's
        ``webhook_secret_*`` columns.

        Status codes:
        * ``created`` — a brand-new hook landed on the host; ``hook_id``
          is the host-side identifier.
        * ``refreshed`` — same URL already registered on the host; we
          rotated the secret and advanced the ``installed_at`` marker.
        * ``app_delivers`` — the connection is a github_app_install and
          the org App is installed on the repo, so its own webhook
          already delivers pushes. No per-repo hook exists, so
          ``hook_id`` is empty (honestly, not a masked hook).
        * ``not_installed`` — maps to a PRECONDITION failure: the App is
          NOT installed on the target repo, so nothing hears pushes.
        """
        from astrolift_scm.services.webhooks import (
            install_astrolift_source_webhook as install_webhook_service,
        )

        # Org-scope the app lookup to the caller's tenant before the webhook
        # install side effect (SCM call, rotates the HMAC secret). Slugs are
        # unique only within an org. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        try:
            result = install_webhook_service(app)
        except NotImplementedError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))

        if result.status == "no_connection":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "no source connection available",
            )
        if result.status == "not_installed":
            # The org GitHub App isn't installed on the target repo, so
            # nothing delivers pushes. Report the truth instead of a
            # fake success with an empty hook id.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "the GitHub App is not installed on this repo",
            )
        if result.status == "no_public_url":
            # The hook would point at an address the source host cannot
            # reach (#1693). Say so instead of registering it and
            # reporting a wired webhook that never delivers.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "this install has no publicly reachable API URL",
            )
        if result.status == "fetch_failed":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "webhook install failed",
            )

        return gql_success(
            InstallSourceWebhookPayload(
                status=result.status,
                hook_id=result.hook_id,
                receiver_url=result.receiver_url,
            ),
        )

    # ----------------------------------------------------------------
    # CI secrets push (#383): seal + PUT the five ``ASTROLIFT_*``
    # GitHub Actions secrets onto the app's source repo, rotating the
    # deploy token as part of the round-trip.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.ci.push_secrets")
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def push_astrolift_ci_secrets_to_repo(
        self,
        info: Info,
        input: PushCiSecretsToRepoInput,
    ) -> MutationResultType[PushCiSecretsPayload]:
        """Push the five CI secrets to the app's source repo (#383).

        Auth is the viewer's *personal* GitHub OAuth connection (per
        #395): we want the secret writes attributed to the human who
        clicked the button on GitHub's audit log, not to a shared
        org-level PAT. The mutation rotates the deploy token as part
        of the push — there's no way to recover the existing plaintext
        from the hash, so a rotation is the only way to deliver a
        sealable value. The rotation is *immediate* (no grace window):
        the operator clicked an explicit ``Push & rotate`` affordance
        and was warned in the confirm dialog, so the old hash is
        revoked in the same transaction as the new plaintext lands in
        GitHub. In-flight CI runs holding the previous token will
        need to be re-kicked.
        """
        from django.conf import settings

        from astrolift_scm.services.secrets import (
            PushSecretsError,
            push_astrolift_ci_secrets,
        )

        # Org-scope the app lookup to the caller's tenant before pushing CI
        # secrets + rotating the deploy token (SCM write + secret mint).
        # Slugs are unique only within an org. Fails closed (NOT_FOUND) when
        # org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        platform_api_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")

        try:
            result = push_astrolift_ci_secrets(
                app,
                viewer_user=viewer,
                platform_api_url=platform_api_url,
            )
        except NotImplementedError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        except PushSecretsError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message)

        if not result.ok:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error_message or "couldn't push CI secrets",
            )

        return gql_success(
            PushCiSecretsPayload(
                secret_names=list(result.secret_names),
                rotated_token_last_4=result.new_token_last_4,
                repo=app.source_repo,
            ),
        )

    # ----------------------------------------------------------------
    # CI secrets validate (#693): read-only probe of the app's source
    # repo Actions secrets, reporting which of the canonical five are
    # set + whether they look 'current' relative to the platform's last
    # push.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.ci.validate_secrets")
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def validate_astrolift_ci_secrets(
        self,
        info: Info,
        input: ValidateAstroliftCiSecretsInput,
    ) -> MutationResultType[ValidateAstroliftCiSecretsPayload]:
        """Probe the app's source repo for the five Astrolift CI secrets
        (#693).

        Read-only — never writes; permission gate is ``app.read`` because
        an operator who can see the app should be allowed to verify the
        CI setup is healthy, even if they couldn't push it.  Auth is the
        viewer's personal GitHub OAuth connection (per #395) — same
        scoping as the push side so the validate call doesn't surface
        secrets visible only to an org-level PAT.

        Surfaces non-GitHub source kinds + missing connections through
        the same PRECONDITION envelope shape the push side returns;
        ``UNSUPPORTED_SOURCE`` is the explicit code for non-GitHub.
        """
        from astrolift_scm.services.secrets import validate_astrolift_ci_secrets

        # Org-scope the app lookup to the caller's tenant — even though this
        # probe is read-only, an unscoped slug lookup lets a caller inspect
        # a sibling org's repo CI-secret state via the app's SCM connection.
        # Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None

        result = validate_astrolift_ci_secrets(app, viewer_user=viewer)
        if not result.ok:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error_message or "validate failed",
            )
        return gql_success(
            ValidateAstroliftCiSecretsPayload(
                repo=app.source_repo,
                results=[
                    CiSecretValidationType(
                        secret_name=r.secret_name,
                        is_set=r.is_set,
                        is_current=r.is_current,
                        updated_at=r.updated_at,
                    )
                    for r in result.results
                ],
            ),
        )

    # ----------------------------------------------------------------
    # CI workflow push (#384): render the canonical
    # ``.github/workflows/astrolift-ci.yml`` for the app and commit
    # it to the deploy branch. Pairs with the secrets push (#383) so
    # an operator can go from "fresh repo" to "platform-driven deploy"
    # without copy-pasting the reference YAML by hand.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.ci.push_workflow")
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def push_astrolift_ci_workflow_to_repo(
        self,
        info: Info,
        input: PushCiWorkflowToRepoInput,
    ) -> MutationResultType[PushCiWorkflowPayload]:
        """Render + reconcile the Astrolift CI workflow file onto the
        app's deploy branch (#384).

        Idempotent on no-op: when the file already matches the rendered
        template byte-for-byte the resolver returns ``status="in_sync"``
        and no commit lands. Protected deploy branches fall through to
        a side-branch + PR flow (``status="pr_opened"``) so the change
        respects the repo's review path.
        """
        from astrolift_scm.services.workflow_sync import (
            WorkflowSyncError,
            sync_workflow_file_to_repo,
        )

        # Org-scope the app lookup to the caller's tenant before committing
        # the CI workflow file to the repo (SCM write). Slugs are unique
        # only within an org. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None

        try:
            result = sync_workflow_file_to_repo(app, viewer_user=viewer)
        except NotImplementedError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        except WorkflowSyncError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message)

        if result.status == "fetch_failed":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "couldn't sync CI workflow to repo",
            )

        return gql_success(
            PushCiWorkflowPayload(
                status=result.status,
                commit_sha=result.commit_sha or None,
                pr_url=result.pr_url or None,
            ),
        )

    # ----------------------------------------------------------------
    # #1108 — retry the whole autowire chain from the detail page
    # ----------------------------------------------------------------
    #
    # Register runs this same ``run_autowire`` at onboarding; this
    # mutation re-runs it (and repairs a phantom webhook) so an app that
    # landed half-wired — no CI file, phantom webhook, no deploy secret —
    # can be completed with one click. ``app.update`` is the gate:
    # repo-config-touching, not a deploy. The service resolves org-level
    # connections by purpose (never the viewer's personal token) exactly
    # as the individual steps do.

    @strawberry.field
    @mutation_audit(action="app.ci.retry_autowire")
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def retry_astrolift_autowire(
        self,
        info: Info,
        input: RetryAstroliftAutowireInput,
    ) -> MutationResultType[RetryAstroliftAutowirePayload]:
        """Re-run the full autowire chain for ``app_slug`` (#1108).

        Resilient + idempotent: a failed step is recorded on
        ``autowire_state`` and surfaced in ``detail``, never fatal, and a
        re-run against an already-wired app doesn't duplicate hooks or
        workflow commits. Always returns success — the payload's per-step
        statuses (and ``connected=false`` for the "connect for auto-deploy"
        state) carry the outcome.
        """
        from astrolift_scm.services.autowire import run_autowire

        # Org-scope the app lookup to the caller's tenant before re-running
        # the autowire chain (SCM writes: webhook, CI file, deploy secret).
        # Slugs are unique only within an org. Fails closed (NOT_FOUND) when
        # org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        request = getattr(info.context, "request", None)
        actor = getattr(request, "user", None) if request else None

        outcome = run_autowire(app, actor=actor)
        detail = "; ".join(f"{step.replace('_', ' ')}: {msg}" for step, msg in outcome.errors.items())
        return gql_success(
            RetryAstroliftAutowirePayload(
                connected=outcome.connected,
                all_ok=outcome.all_ok,
                ci_workflow=outcome.steps.get("ci_workflow", "missing"),
                webhook=outcome.steps.get("webhook", "missing"),
                secrets=outcome.steps.get("secrets", "missing"),
                detail=detail,
            ),
        )
