"""AppMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Project, Team
from astrolift_registry.cron import CronValidationError, validate_cron_expression
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations.helpers import (
    _actor,
    _normalize_build_args,
    _resolve_approval_inputs,
    _validate_build_mode,
    _validate_build_path_field,
    _validate_build_strategy,
    _validate_effective_approval_policy,
)
from astrolift_registry.schema.mutations.types import (
    SetAppSubdomainInput,
    SoftDeleteAppInput,
    TearDownAppInput,
    TransferAppInput,
    UpdateAppInput,
    _SoftDeletePayload,
)
from astrolift_registry.schema.types import (
    RegisteredAppType,
    app_to_type,
)
from astrolift_registry.scopes import app_scope_by_guid, transfer_destination_scope
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.optimistic import check_version_match as _check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class AppMutations:
    @strawberry.field
    @mutation_audit(action="app.update")
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_guid("input.id", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def update_app(self, info: Info, input: UpdateAppInput) -> MutationResultType[RegisteredAppType]:
        # Org-scope the by-guid lookup to the caller's tenant. Fails closed
        # (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        # #497 — optimistic-concurrency gate. Refuse to apply changes
        # when the caller's cached ``ifMatchVersion`` is stale; the FE
        # then refetches and re-prompts the operator instead of
        # silently overwriting a concurrent edit.
        mismatch = _check_version_match(app, if_match_version=input.if_match_version, kind="App")
        if mismatch is not None:
            return mismatch

        # Resolve the effective post-update trigger_mode + cron_expression
        # together so we can enforce the 'cron mode requires expression'
        # invariant whether the operator is flipping mode, expression,
        # or both in the same call.
        next_trigger_mode = input.trigger_mode if input.trigger_mode is not None else app.trigger_mode
        if input.cron_expression is not None:
            next_cron_raw = input.cron_expression.strip()
        else:
            next_cron_raw = app.cron_expression or ""

        if next_trigger_mode == RegisteredApp.TriggerMode.CRON.value:
            if not next_cron_raw:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "cron expression is required when triggerMode is 'cron'",
                    field="cronExpression",
                )
            try:
                next_cron_raw = validate_cron_expression(next_cron_raw)
            except CronValidationError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    str(exc),
                    field="cronExpression",
                )
        else:
            # Flipping away from cron clears the expression — the field
            # is only meaningful under cron mode and stale values would
            # silently re-activate if mode flipped back.
            next_cron_raw = ""

        approval, err = _resolve_approval_inputs(
            organization=app.organization,
            requires_approval=input.requires_approval,
            approver_team_id=input.approver_team_id,
            approver_user_ids=input.approver_user_ids,
            minimum_approvals=input.minimum_approvals,
        )
        if err is not None:
            return err

        # Compute the effective post-update approval policy and run the
        # cross-field validator. Any field the caller didn't touch falls
        # back to the persisted value so an UPDATE call that only flips
        # one knob is still validated against the *whole* policy shape.
        eff_requires_approval = (
            bool(approval["requires_approval"])
            if approval["requires_approval"] is not None
            else bool(app.requires_approval)
        )
        if approval["team_provided"]:
            eff_team = approval["team"]
        else:
            eff_team = app.approver_team
        if approval["user_ids"] is not None:
            eff_user_ids = approval["user_ids"]
        else:
            eff_user_ids = tuple(app.approver_users.values_list("pk", flat=True))
        eff_minimum_approvals = (
            approval["minimum_approvals"]
            if approval["minimum_approvals"] is not None
            else app.minimum_approvals
        )
        cross_err = _validate_effective_approval_policy(
            requires_approval=eff_requires_approval,
            effective_team=eff_team,
            effective_user_ids=eff_user_ids,
            effective_minimum_approvals=eff_minimum_approvals,
        )
        if cross_err is not None:
            return cross_err

        # Build config. ``build_mode`` is validated against the choices;
        # ``build_args`` is normalised to a str→str map. Both are
        # None-sentinel on update — a field left None stays as-is.
        build_mode, build_mode_err = _validate_build_mode(input.build_mode)
        if build_mode_err is not None:
            return build_mode_err
        build_strategy, build_strategy_err = _validate_build_strategy(input.build_strategy)
        if build_strategy_err is not None:
            return build_strategy_err
        # #1756 adversarial review: dockerfile_path/build_context are
        # already relative to the repo root directly (no further base to
        # combine with, unlike a container-level offset), so an absolute
        # path or a leading ".." is always wrong here.
        dockerfile_path, dockerfile_path_err = _validate_build_path_field(
            input.dockerfile_path, field="dockerfilePath"
        )
        if dockerfile_path_err is not None:
            return dockerfile_path_err
        build_context, build_context_err = _validate_build_path_field(
            input.build_context, field="buildContext"
        )
        if build_context_err is not None:
            return build_context_err

        for field in (
            "name",
            "description",
            "source_url",
            "manifest_path",
            "default_branch",
            "deploy_branch",
            "preview_enabled",
            "is_active",
            "cron_paused",
        ):
            new_value = getattr(input, field)
            if new_value is not None:
                setattr(app, field, new_value)
        if dockerfile_path is not None:
            app.dockerfile_path = dockerfile_path
        if build_context is not None:
            app.build_context = build_context
        if build_mode is not None:
            app.build_mode = build_mode
        if build_strategy is not None:
            app.build_strategy = build_strategy
        if input.build_args is not None:
            build_args, build_args_err = _normalize_build_args(input.build_args)
            if build_args_err is not None:
                return build_args_err
            app.build_args = build_args
        app.trigger_mode = next_trigger_mode
        app.cron_expression = next_cron_raw
        # Flipping away from cron implicitly clears the paused flag —
        # the field is only meaningful while we're actually firing on
        # schedule.
        if next_trigger_mode != RegisteredApp.TriggerMode.CRON.value:
            app.cron_paused = False
        if approval["requires_approval"] is not None:
            app.requires_approval = bool(approval["requires_approval"])
        if approval["team_provided"]:
            app.approver_team = approval["team"]
        if approval["minimum_approvals"] is not None:
            app.minimum_approvals = approval["minimum_approvals"]
        app.save()
        if approval["user_ids"] is not None:
            app.approver_users.set(approval["user_ids"])
        return gql_success(app_to_type(app, info=info))

    @strawberry.field
    @mutation_audit(action="app.set_subdomain")
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_guid("input.id", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def set_app_subdomain(
        self, info: Info, input: SetAppSubdomainInput
    ) -> MutationResultType[RegisteredAppType]:
        """Edit a registered app's subdomain without redeploy.

        This mutation is the source-of-truth update + collision
        check; ``SyncAppDomainWorkflow`` (#143) is what makes live
        traffic follow it, re-applying DNS + the Ingress host rule
        for the new hostname and rolling back to the old subdomain if
        a step fails part-way.

        Validation:
        - DNS label rules (lowercase letters, digits, hyphens)
        - Reserved name check (api / admin / etc — see hostname.py)
        - Within-org collision check (no two active apps in the same
          org may claim the same subdomain)

        Per spec 13 §6.
        """
        from astrolift_manifest.hostname import validate_subdomain_label

        # Org-scope the by-guid lookup to the caller's tenant. Fails closed
        # (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        try:
            new_subdomain = validate_subdomain_label(input.subdomain)
        except ValueError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="subdomain")

        # Within-org collision: another active app already owning
        # this subdomain is a footgun (DNS would race for the same
        # label). Refuse with CONFLICT.
        clash = (
            RegisteredApp.objects.filter(
                organization_id=app.organization_id,
                subdomain=new_subdomain,
                deleted_at__isnull=True,
            )
            .exclude(pk=app.pk)
            .first()
        )
        if clash is not None:
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"another app in this org already uses {new_subdomain!r}",
                field="subdomain",
            )

        from astrolift_registry.hostname_claims import hostname_claim_refusal, hostname_label_refusal

        # Across orgs: a shared zone has one namespace of labels (#1930).
        refusal = (
            hostname_label_refusal(new_subdomain, organization=app.organization)
            if app.subdomain != new_subdomain
            else None
        )
        if refusal is not None:
            return gql_failure(ErrorCode.CONFLICT.value, refusal, field="subdomain")

        # The label check above can't see a multi-workload app's suffixed
        # hostname (``<subdomain>-<workload>``) colliding with another app's
        # plain claim of that name (#2012); this app's live workload set is
        # already known here, so render every hostname the rename would
        # produce and refuse if the ledger says another app holds one.
        claim_refusal = (
            hostname_claim_refusal(app, subdomain=new_subdomain) if app.subdomain != new_subdomain else None
        )
        if claim_refusal is not None:
            return gql_failure(ErrorCode.CONFLICT.value, claim_refusal, field="subdomain")

        if app.subdomain != new_subdomain:
            from astrolift_registry.hostname_claims import sync_workload_hostname_claims
            from astrolift_workflows.client import start_workflow
            from astrolift_workflows.inputs import Actor, SyncAppDomainInput

            previous_subdomain = app.subdomain
            app.subdomain = new_subdomain
            app.save(update_fields=["subdomain", "updated_at", "version"])
            sync_workload_hostname_claims(app)
            # Deterministic id: re-firing for the same app supersedes the
            # in-flight sync rather than racing a second one onto the same
            # Ingress. The workflow needs the pre-write value to diff the
            # hostnames and to restore routing if a step fails.
            start_workflow(
                "SyncAppDomainWorkflow",
                args=[
                    SyncAppDomainInput(
                        registered_app_id=app.pk,
                        previous_subdomain=previous_subdomain,
                        actor=Actor(kind="system", display="app-subdomain-sync"),
                    ),
                ],
                workflow_id=f"SyncAppDomainWorkflow-{app.guid}",
            )
        return gql_success(app_to_type(app, info=info))

    @strawberry.field
    @mutation_audit(action="app.delete")
    @require_permission(
        Permission.APP_DELETE, scope=app_scope_by_guid("input.id", permission=Permission.APP_DELETE)
    )
    @tenant_scoped()
    def soft_delete_app(
        self, info: Info, input: SoftDeleteAppInput
    ) -> MutationResultType[_SoftDeletePayload]:
        # Org-scope the by-guid lookup to the caller's tenant before the
        # soft-delete. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        app.soft_delete(by=_actor())
        from astrolift_registry.hostname_claims import release_app_hostname_claims

        release_app_hostname_claims(app)
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    @strawberry.field
    @mutation_audit(action="app.tear_down")
    @require_permission(
        Permission.APP_DELETE, scope=app_scope_by_guid("input.id", permission=Permission.APP_DELETE)
    )
    @tenant_scoped()
    def tear_down_app(self, info: Info, input: TearDownAppInput) -> MutationResultType[_SoftDeletePayload]:
        """Fires ``TearDownAppWorkflow`` (#358) — symmetric inverse
        of onboarding. Fans out per-binding ``DeprovisionManagedService``
        workflows, deletes app's k8s namespaces, revokes deploy
        tokens, and soft-deletes the platform rows.

        Returns immediately with ``deleted=false``; the workflow
        flips the row to ``deregistered`` once teardown converges.
        """
        from astrolift_registry.models import RegisteredApp
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            TearDownAppInput as TearDownInput,
        )

        # Org-scope the by-guid lookup to the caller's tenant before the
        # teardown workflow (tears down every per-app cloud resource). Fails
        # closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(
            guid=str(input.id),
            organization_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        actor = Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(
                getattr(user, "email", "") or getattr(user, "username", ""),
            ),
        )
        start_workflow(
            "TearDownAppWorkflow",
            args=[
                TearDownInput(
                    registered_app_id=app.pk,
                    actor=actor,
                    delete_data=bool(input.delete_data),
                    force_destroy=bool(input.force_destroy),
                ),
            ],
            workflow_id=f"TearDownAppWorkflow-{app.guid}",
        )
        return gql_success(
            _SoftDeletePayload(id=input.id, deleted=False),
        )

    @strawberry.field
    @mutation_audit(action="app.transfer")
    @require_permission(
        Permission.APP_TRANSFER, scope=app_scope_by_guid("input.app_id", permission=Permission.APP_TRANSFER)
    )
    @require_permission(Permission.APP_CREATE, scope=transfer_destination_scope())
    @tenant_scoped()
    def transfer_app(self, info: Info, input: TransferAppInput) -> MutationResultType[RegisteredAppType]:
        """Re-parent an app to a different team / project within the
        same org.

        Permission contract:
        - ``app.transfer`` on the source app (caller is moving it OUT)
        - ``app.create`` on the destination team/project (caller is
          claiming a new home)

        Both scoped checks are enforced by the decorator stack.
        Cross-org transfers are refused; use the federation flow for
        those instead.
        """

        # Org-scope the SOURCE app to the caller's tenant — the target
        # team/project below are validated against ``app.organization_id``,
        # so without scoping the source a cross-org caller could re-parent
        # another org's app. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.select_related("organization", "team", "project")
            .filter(guid=str(input.app_id), organization_id=org_id, deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        if input.target_team_id is None and input.target_project_id is None:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "provide at least one of targetTeamId or targetProjectId",
                field="targetTeamId",
            )

        next_team = app.team
        next_project = app.project

        if input.target_team_id is not None:
            team = (
                Team.objects.select_related("organization")
                .filter(guid=str(input.target_team_id), deleted_at__isnull=True)
                .first()
            )
            if team is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "target team not found",
                    field="targetTeamId",
                )
            if team.organization_id != app.organization_id:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "cross-organization transfer is not permitted",
                    field="targetTeamId",
                )
            next_team = team

        if input.target_project_id is not None:
            project = (
                Project.objects.select_related("organization", "team")
                .filter(guid=str(input.target_project_id), deleted_at__isnull=True)
                .first()
            )
            if project is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "target project not found",
                    field="targetProjectId",
                )
            if project.organization_id != app.organization_id:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "cross-organization transfer is not permitted",
                    field="targetProjectId",
                )
            next_project = project
            # If the caller didn't pin a team, follow the project's
            # team (otherwise the app would dangle out-of-tree).
            if input.target_team_id is None:
                next_team = project.team

        # When only target_team_id was set, the existing project must
        # belong to the new team or the tree breaks. Refuse rather
        # than silently re-anchoring the project to the new team.
        if next_project is not None and next_project.team_id != next_team.id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "project does not belong to the target team — specify targetProjectId",
                field="targetProjectId",
            )

        if app.team_id == next_team.id and app.project_id == next_project.id:
            # No-op: nothing to do. Return success so callers can
            # treat the mutation as idempotent.
            return gql_success(app_to_type(app, info=info))

        app.team = next_team
        app.project = next_project
        app.save(update_fields=["team", "project", "updated_at", "version"])
        return gql_success(app_to_type(app, info=info))
