"""Mutations for the registry app: register/update/soft-delete app."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Project, Team
from astrolift_registry.cron import CronValidationError, validate_cron_expression
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import RegisteredAppType, app_to_type
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.input
class RegisterAppInput:
    project_id: GUID
    name: str
    slug: str
    description: str | None = None
    source_kind: str = "github"
    source_repo: str
    source_url: str | None = None
    manifest_path: str | None = None
    default_branch: str | None = None
    deploy_branch: str | None = None
    trigger_mode: str | None = None
    # Five-field cron expression. Required when ``trigger_mode == 'cron'``
    # and ignored otherwise; the resolver enforces both rules.
    cron_expression: str | None = None
    # Approval policy (#291). ``approver_team_id`` is the team GUID;
    # ``approver_user_ids`` are Django user PKs as strings (matching
    # ``AstroliftUser.id`` shape — the User model is the stock Django
    # one with integer PKs). An empty approver set with
    # ``requires_approval=True`` means anyone with the
    # ``app.approve_deploy`` permission on the org may approve.
    requires_approval: bool | None = None
    approver_team_id: GUID | None = None
    approver_user_ids: list[str] | None = None
    minimum_approvals: int | None = None
    # Optional: the wizard may pre-fetch the manifest (via
    # astroliftSourceFile) and pass the body here so the new app
    # boots with manifest_raw already populated. The onboarding
    # workflow still resyncs from the repo's default branch — this is
    # just the seed that lets ManifestParse activities run before the
    # first GitHub/GitLab API call lands.
    manifest_raw: str | None = None


@strawberry.input
class UpdateAppInput:
    id: GUID
    name: str | None = None
    description: str | None = None
    source_url: str | None = None
    manifest_path: str | None = None
    default_branch: str | None = None
    deploy_branch: str | None = None
    trigger_mode: str | None = None
    cron_expression: str | None = None
    preview_enabled: bool | None = None
    is_active: bool | None = None
    # Approval policy (#291). See ``RegisterAppInput`` for semantics.
    # ``approver_user_ids`` is treated as a full replacement set when
    # provided (None leaves the existing set untouched).
    requires_approval: bool | None = None
    approver_team_id: GUID | None = None
    approver_user_ids: list[str] | None = None
    minimum_approvals: int | None = None
    # ``cron_paused`` controls scheduled-deploy dispatch without
    # touching ``trigger_mode``. Pausing keeps the cron expression
    # intact so resume re-enables fire-on-schedule immediately.
    cron_paused: bool | None = None


@strawberry.input
class SetAppSubdomainInput:
    id: GUID
    subdomain: str


@strawberry.input
class SoftDeleteAppInput:
    id: GUID


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


@strawberry.input
class UpdateManifestInput:
    """Stage an edit to the source astrolift.toml.

    Writes to ``manifest_raw_staged`` rather than ``manifest_raw`` —
    the editor is a draft buffer until ``pushManifestToRepo`` (which
    opens a PR) or ``syncManifestFromRepo`` (which discards the
    draft) is called.
    """

    id: GUID
    raw_manifest: str


@strawberry.input
class SyncManifestFromRepoInput:
    """Re-fetch ``astrolift.toml`` from the source repo's default
    branch, overwriting both ``manifest_raw`` AND any unsaved
    ``manifest_raw_staged`` draft."""

    id: GUID


@strawberry.input
class TransferAppInput:
    """Move a registered app to a different team or project.

    Both targets are optional — at least one must be provided. When
    only ``target_team_id`` is given, the app moves under that team
    and re-anchors its project to a project under the new team
    (callers normally pair this with ``target_project_id``).
    Transfers across organizations are NOT permitted: source and
    target must share an org. Use a separate workflow (federation)
    for cross-org moves.
    """

    app_id: GUID
    target_team_id: GUID | None = None
    target_project_id: GUID | None = None


@strawberry.input
class PushManifestToRepoInput:
    """Open a PR against the source repo with the staged TOML.

    No-op (returns ok + 'nothing_to_push' note) when there's no
    pending staged change."""

    id: GUID
    pr_title: str | None = None
    pr_body: str | None = None
    branch_name: str | None = None


@strawberry.type
class _ManifestStagePayload:
    id: GUID
    sync_state: str
    raw_manifest: str
    raw_manifest_staged: str


@strawberry.type
class _ManifestPushPayload:
    id: GUID
    pr_url: str
    branch_name: str
    note: str


def _actor():
    tenant = get_current_tenant()
    actor_id = tenant.actor_user_id if tenant else None
    if actor_id is None:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=actor_id).first()


def _resolve_approval_inputs(
    *,
    organization,
    requires_approval,
    approver_team_id,
    approver_user_ids,
    minimum_approvals,
):
    """Resolve the approval-policy input set against the org scope.

    Returns a ``(values, error)`` tuple. On success, ``values`` is a
    dict of resolved values: ``requires_approval`` (bool), ``team``
    (Team | None), ``user_ids`` (tuple[int, ...] | None — None means
    'leave untouched'), ``minimum_approvals`` (int). On failure,
    ``error`` is the MutationResult failure envelope.

    Each cross-org reference (team / user) is refused with a clear
    field-tagged validation error rather than silently dropped — the
    wizard surfaces these inline.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()

    team = None
    if approver_team_id is not None:
        team = (
            Team.objects.filter(guid=str(approver_team_id), deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if team is None:
            return None, gql_failure(
                ErrorCode.NOT_FOUND.value,
                "approver team not found",
                field="approverTeamId",
            )
        if team.organization_id != organization.id:
            return None, gql_failure(
                ErrorCode.PRECONDITION.value,
                "approver team must belong to the same organization as the app",
                field="approverTeamId",
            )

    user_ids: tuple[int, ...] | None = None
    if approver_user_ids is not None:
        # Empty list is meaningful ("clear approver users"); leave as
        # the empty tuple. Otherwise parse each value as an integer
        # User pk (matching ``AstroliftUser.id``) and refuse unknowns.
        raw = list(approver_user_ids)
        parsed: list[int] = []
        for raw_id in raw:
            try:
                parsed.append(int(str(raw_id).strip()))
            except (TypeError, ValueError):
                return None, gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"approverUserIds entry {raw_id!r} is not a valid user id",
                    field="approverUserIds",
                )
        if parsed:
            found = set(User.objects.filter(pk__in=parsed).values_list("pk", flat=True))
            missing = [pk for pk in parsed if pk not in found]
            if missing:
                return None, gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"approver users not found: {sorted(missing)}",
                    field="approverUserIds",
                )
            user_ids = tuple(parsed)
        else:
            user_ids = ()

    if minimum_approvals is not None and minimum_approvals < 1:
        return None, gql_failure(
            ErrorCode.VALIDATION.value,
            "minimumApprovals must be at least 1",
            field="minimumApprovals",
        )

    resolved = {
        "requires_approval": bool(requires_approval) if requires_approval is not None else None,
        "team": team,
        "team_provided": approver_team_id is not None,
        "user_ids": user_ids,
        "minimum_approvals": minimum_approvals,
    }
    return resolved, None


@strawberry.type
class RegistryMutation:
    @strawberry.field
    @mutation_audit(action="app.create")
    @require_permission(Permission.APP_CREATE)
    @tenant_scoped()
    def register_app(self, info: Info, input: RegisterAppInput) -> MutationResultType[RegisteredAppType]:
        project = (
            Project.objects.select_related("organization", "team").filter(guid=str(input.project_id)).first()
        )
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found", field="projectId")

        # An app is a deployment target — without a connected cluster
        # the platform has nowhere to roll the workload to and the
        # downstream deploy fails with an opaque "no cluster available"
        # error. Reject up front instead. Soft-deleted and inactive
        # clusters don't count: they can't accept a deploy.
        cluster_count = TenantCluster.objects.filter(
            organization=project.organization,
            deleted_at__isnull=True,
            is_active=True,
        ).count()
        if cluster_count == 0:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "No cluster connected. Visit /clusters to register one before adding apps.",
                field=None,
            )

        if RegisteredApp.objects.filter(organization=project.organization, slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"app with slug {input.slug!r} already exists in this organization",
                field="slug",
            )

        if input.source_repo and input.manifest_path:
            if RegisteredApp.objects.filter(
                source_repo=input.source_repo,
                manifest_path=input.manifest_path,
            ).exists():
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    "this repo + manifest path is already registered",
                    field="sourceRepo",
                )

        trigger_mode = input.trigger_mode or "auto_on_push"
        cron_expression = ""
        if trigger_mode == RegisteredApp.TriggerMode.CRON.value:
            raw_cron = (input.cron_expression or "").strip()
            if not raw_cron:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "cron expression is required when triggerMode is 'cron'",
                    field="cronExpression",
                )
            try:
                cron_expression = validate_cron_expression(raw_cron)
            except CronValidationError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    str(exc),
                    field="cronExpression",
                )

        approval, err = _resolve_approval_inputs(
            organization=project.organization,
            requires_approval=input.requires_approval,
            approver_team_id=input.approver_team_id,
            approver_user_ids=input.approver_user_ids,
            minimum_approvals=input.minimum_approvals,
        )
        if err is not None:
            return err

        app = RegisteredApp.objects.create(
            organization=project.organization,
            team=project.team,
            project=project,
            name=input.name.strip(),
            slug=input.slug,
            description=input.description or "",
            source_kind=input.source_kind or "github",
            source_repo=input.source_repo or "",
            source_url=input.source_url or "",
            manifest_path=input.manifest_path or "astrolift.toml",
            manifest_raw=input.manifest_raw or "",
            default_branch=input.default_branch or "main",
            deploy_branch=input.deploy_branch or input.default_branch or "main",
            trigger_mode=trigger_mode,
            cron_expression=cron_expression,
            k8s_namespace=f"{project.organization.slug}-{input.slug}",
            subdomain=input.slug,
            requires_approval=bool(approval["requires_approval"])
            if approval["requires_approval"] is not None
            else False,
            approver_team=approval["team"],
            minimum_approvals=approval["minimum_approvals"]
            if approval["minimum_approvals"] is not None
            else 1,
        )
        if approval["user_ids"] is not None:
            app.approver_users.set(approval["user_ids"])
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.update")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_app(self, info: Info, input: UpdateAppInput) -> MutationResultType[RegisteredAppType]:
        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

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
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.set_subdomain")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_app_subdomain(
        self, info: Info, input: SetAppSubdomainInput
    ) -> MutationResultType[RegisteredAppType]:
        """Edit a registered app's subdomain without redeploy.

        The platform's hostname computation re-derives from
        ``app.subdomain`` on the next render — for live ingress
        traffic this needs an Ingress patch (handled by the
        SyncAppDomainWorkflow, separately tracked). This mutation is
        the source-of-truth update + collision check.

        Validation:
        - DNS label rules (lowercase letters, digits, hyphens)
        - Reserved name check (api / admin / etc — see hostname.py)
        - Within-org collision check (no two active apps in the same
          org may claim the same subdomain)

        Per spec 13 §6.
        """
        from astrolift_manifest.hostname import validate_subdomain_label

        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
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

        if app.subdomain != new_subdomain:
            app.subdomain = new_subdomain
            app.save(update_fields=["subdomain", "updated_at", "version"])
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.delete")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def soft_delete_app(
        self, info: Info, input: SoftDeleteAppInput
    ) -> MutationResultType[_SoftDeletePayload]:
        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        app.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    @strawberry.field
    @mutation_audit(action="app.transfer")
    @require_permission(Permission.APP_TRANSFER, Permission.APP_CREATE)
    @tenant_scoped()
    def transfer_app(self, info: Info, input: TransferAppInput) -> MutationResultType[RegisteredAppType]:
        """Re-parent an app to a different team / project within the
        same org.

        Permission contract (matches monorail):
        - ``app.transfer`` on the source app (caller is moving it OUT)
        - ``app.create`` on the destination team/project (caller is
          claiming a new home)

        Both checks are enforced by the ``@require_permission`` stack
        at the top — the scope-aware variant would tighten this further
        but isn't yet wired through the resolver decorator surface.
        Cross-org transfers are refused; use the federation flow for
        those instead.
        """
        from astrolift_identity.models import Project, Team

        app = (
            RegisteredApp.objects.select_related("organization", "team", "project")
            .filter(guid=str(input.app_id), deleted_at__isnull=True)
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
        if next_project.team_id != next_team.id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "project does not belong to the target team — specify targetProjectId",
                field="targetProjectId",
            )

        if app.team_id == next_team.id and app.project_id == next_project.id:
            # No-op: nothing to do. Return success so callers can
            # treat the mutation as idempotent.
            return gql_success(app_to_type(app))

        app.team = next_team
        app.project = next_project
        app.save(update_fields=["team", "project", "updated_at", "version"])
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.update_manifest")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_manifest(
        self,
        info: Info,
        input: UpdateManifestInput,
    ) -> MutationResultType[_ManifestStagePayload]:
        """Stage a manifest edit. Writes to ``manifest_raw_staged``.

        Validates the TOML parses before staging — bad TOML never
        lands in the buffer. Empty input clears the staging buffer.
        """
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.sync_state import (
            SyncSnapshot,
            classify_state,
        )

        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        text = input.raw_manifest or ""
        if text.strip():
            try:
                parse_raw(text)
            except ManifestError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"manifest parse failed: {exc}",
                    field="rawManifest",
                )

        # Identity: if the staged content matches the synced content,
        # clear the staging buffer rather than carrying a redundant
        # copy.
        if text == (app.manifest_raw or ""):
            app.manifest_raw_staged = ""
        else:
            app.manifest_raw_staged = text
        app.save(
            update_fields=[
                "manifest_raw_staged",
                "updated_at",
                "version",
            ]
        )

        sync_state = classify_state(
            SyncSnapshot(
                db_hash=app.manifest_hash or "",
                repo_hash=app.last_synced_hash or app.manifest_hash or "",
                last_synced_hash=app.last_synced_hash or "",
            )
        )
        return gql_success(
            _ManifestStagePayload(
                id=input.id,
                sync_state=sync_state.value,
                raw_manifest=app.manifest_raw or "",
                raw_manifest_staged=app.manifest_raw_staged or "",
            )
        )

    @strawberry.field
    @mutation_audit(action="app.sync_manifest_from_repo")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def sync_manifest_from_repo(
        self,
        info: Info,
        input: SyncManifestFromRepoInput,
    ) -> MutationResultType[_ManifestStagePayload]:
        """Re-fetch the manifest from the source repo + recompute
        the hash anchor.

        Discards any staged edits — sync is destructive on purpose,
        the UI is expected to confirm before calling.

        Production wires this into the SCM provider's read-file path
        (GitHub Contents API, GitLab files, etc.). Until that flow is
        connected at this resolver, the mutation simply re-anchors
        ``last_synced_hash`` to the current ``manifest_hash`` so the
        sync_state classifier reads as IN_SYNC.
        """
        from astrolift_manifest.sync_state import (
            SyncSnapshot,
            classify_state,
        )

        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        # TODO: wire SCM provider .read_file(source_repo, manifest_path)
        # via astrolift_scm.providers when the SCM activity is exposed.
        # For now we drop the staging buffer + reset the anchor so the
        # UI's sync state is consistent.
        app.manifest_raw_staged = ""
        app.last_synced_hash = app.manifest_hash or ""
        app.save(
            update_fields=[
                "manifest_raw_staged",
                "last_synced_hash",
                "updated_at",
                "version",
            ]
        )

        sync_state = classify_state(
            SyncSnapshot(
                db_hash=app.manifest_hash or "",
                repo_hash=app.last_synced_hash or "",
                last_synced_hash=app.last_synced_hash or "",
            )
        )
        return gql_success(
            _ManifestStagePayload(
                id=input.id,
                sync_state=sync_state.value,
                raw_manifest=app.manifest_raw or "",
                raw_manifest_staged="",
            )
        )

    @strawberry.field
    @mutation_audit(action="app.push_manifest_to_repo")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def push_manifest_to_repo(
        self,
        info: Info,
        input: PushManifestToRepoInput,
    ) -> MutationResultType[_ManifestPushPayload]:
        """Open a PR with the staged manifest.

        Returns ok + ``note='nothing_to_push'`` when there's no
        staged change. The actual PR creation goes through the SCM
        provider; until that flow is wired here, returns
        ``note='scm_pending'`` with an empty pr_url so the UI can
        show 'PR opening...' state without exploding.
        """
        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        staged = app.manifest_raw_staged or ""
        if not staged or staged == (app.manifest_raw or ""):
            return gql_success(
                _ManifestPushPayload(
                    id=input.id,
                    pr_url="",
                    branch_name="",
                    note="nothing_to_push",
                )
            )

        branch = input.branch_name or f"astrolift/manifest-{app.slug}"
        # TODO: wire astrolift_scm.providers.<source_kind>.open_pull_request
        # to take (source_repo, branch, base=default_branch, file_changes,
        # title, body) and return the PR URL. The SCM-side abstraction
        # already exists for status posts; PR creation is a sibling.
        return gql_success(
            _ManifestPushPayload(
                id=input.id,
                pr_url="",
                branch_name=branch,
                note="scm_pending",
            )
        )
