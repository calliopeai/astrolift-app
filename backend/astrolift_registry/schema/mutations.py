"""Mutations for the registry app: register/update/soft-delete app."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Project, Team
from astrolift_identity.step_up import requires_elevation
from astrolift_registry.cron import CronValidationError, validate_cron_expression
from astrolift_registry.models import AppTeamAccess, RegisteredApp
from astrolift_registry.schema.types import (
    AppTeamAccessType,
    RegisteredAppType,
    app_team_access_to_type,
    app_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.optimistic import check_version_match as _check_version_match
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
    # Optimistic-concurrency gate (#497). Null = skip the check
    # (back-compat). When present, the resolver compares against
    # ``RegisteredApp.version`` and returns ``VERSION_MISMATCH`` if
    # the persisted row has moved on.
    if_match_version: int | None = None


@strawberry.input
class SetAppSubdomainInput:
    id: GUID
    subdomain: str


@strawberry.input
class SoftDeleteAppInput:
    id: GUID


@strawberry.input
class TearDownAppInput:
    """Two-axis safety for ``tearDownApp`` (#358).

    ``deleteData`` False (default): graceful per-binding deprovision —
    final snapshots taken, retained buckets, etc. The artifacts
    survive for operator-initiated restore.

    ``deleteData`` True: irreversibly delete persistent state on
    every bound managed service.

    ``forceDestroy`` False (default): respect cloud-side deletion
    protection. Bound services with protection on will refuse and
    surface to the operator via the workflow result.

    ``forceDestroy`` True: bypass guards (--atomic cleanup).

    UI surfaces both as separate explicit checkboxes.
    """

    id: GUID
    delete_data: bool = False
    force_destroy: bool = False


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
class MoveAppToTeamInput:
    """Change the app's primary / home team.

    Pairs with ``transferApp`` for the FK move; the difference here is
    that ``moveAppToTeam`` also keeps the ``AppTeamAccess`` join table
    coherent: the target team gets an active ``OWNER`` row materialized
    if it didn't already have one, and the *previous* home team is
    downgraded to ``DEPLOYER`` so its existing access isn't silently
    revoked. A direct revoke would be the wrong default — the
    operator can call ``revokeTeamAccessFromApp`` explicitly when
    they actually want the old team to lose access. Downgrade-only is
    safer than revoke-on-move and preserves the audit trail.
    """

    app_id: GUID
    target_team_id: GUID


@strawberry.input
class GrantTeamAccessInput:
    """Grant or update a team's access to an app.

    ``accessLevel`` is one of ``viewer`` / ``deployer`` / ``owner``;
    the mutation upserts so re-granting at the same level is
    idempotent and changing the level on an existing row is a
    one-call update.
    """

    app_id: GUID
    team_id: GUID
    access_level: str


@strawberry.input
class RevokeTeamAccessInput:
    app_id: GUID
    team_id: GUID


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


def _ensure_owner_access(app, team_id: int, *, actor=None) -> None:
    """Idempotently materialize an active ``AppTeamAccess(OWNER)``
    row for ``(app, team_id)``.

    - If no row exists, create one at OWNER.
    - If a soft-deleted row exists, restore + promote to OWNER.
    - If a live row exists at a lower level, promote it to OWNER.
    - If a live row at OWNER exists, no-op.
    """

    live = AppTeamAccess.objects.filter(registered_app=app, team_id=team_id, deleted_at__isnull=True).first()
    if live is not None:
        if live.access_level != AppTeamAccess.AccessLevel.OWNER.value:
            live.access_level = AppTeamAccess.AccessLevel.OWNER.value
            live.save(update_fields=["access_level", "updated_at", "version"])
        return

    soft_deleted = (
        AppTeamAccess.all_objects.filter(registered_app=app, team_id=team_id)
        .exclude(deleted_at__isnull=True)
        .order_by("-deleted_at")
        .first()
    )
    if soft_deleted is not None:
        soft_deleted.deleted_at = None
        soft_deleted.deleted_by = None
        soft_deleted.access_level = AppTeamAccess.AccessLevel.OWNER.value
        soft_deleted.save(
            update_fields=[
                "deleted_at",
                "deleted_by",
                "access_level",
                "updated_at",
                "version",
            ]
        )
        return

    AppTeamAccess.objects.create(
        registered_app=app,
        team_id=team_id,
        access_level=AppTeamAccess.AccessLevel.OWNER.value,
    )


def _downgrade_to_deployer(app, team_id: int, *, actor=None) -> None:
    """Move the previous home team's grant to ``DEPLOYER`` rather
    than revoke it on move. The previous team keeps write+deploy
    access until the operator explicitly revokes — preserving
    in-flight humans' access and the audit trail.

    Creates a new DEPLOYER row when no active grant existed (the FK
    was the only thing pointing at that team).
    """

    if team_id is None:
        return
    live = AppTeamAccess.objects.filter(registered_app=app, team_id=team_id, deleted_at__isnull=True).first()
    if live is None:
        AppTeamAccess.objects.create(
            registered_app=app,
            team_id=team_id,
            access_level=AppTeamAccess.AccessLevel.DEPLOYER.value,
        )
        return
    if live.access_level == AppTeamAccess.AccessLevel.OWNER.value:
        live.access_level = AppTeamAccess.AccessLevel.DEPLOYER.value
        live.save(update_fields=["access_level", "updated_at", "version"])


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


def _validate_effective_approval_policy(
    *,
    requires_approval: bool,
    effective_team,
    effective_user_ids: tuple[int, ...] | list[int] | set[int],
    effective_minimum_approvals: int,
):
    """Cross-field validation of the resolved approval policy (#410).

    Distinct from ``_resolve_approval_inputs`` which validates each
    incoming field on its own. This gate runs over the *post-update*
    effective state so the rules are equivalent on register_app and
    update_app whether the caller passed every field or only a delta.

    Rules:
      - When ``requires_approval`` is True, the policy must name at
        least one approver path — users OR team. Empty approver_users
        AND no team would fail-open on the caller side (any deploy
        would need any user with ``app.approve_deploy``), which is the
        documented behavior of the resolver but a bad default to land
        from the wizard — the operator clearly wanted a specific
        approver set.
      - Users and team are mutually exclusive on the picker: a single
        policy can't gate on both "this team's members" and "these
        specific users". Two approver paths in one app makes the
        approval-counting math ambiguous; pick one.
      - When approver_users is non-empty, ``minimumApprovals`` may not
        exceed the user count — otherwise the gate would never satisfy.
    """
    if not requires_approval:
        return None

    users_set = bool(effective_user_ids)
    team_set = effective_team is not None
    if not users_set and not team_set:
        return gql_failure(
            ErrorCode.VALIDATION.value,
            "requireApproval is on but no approvers selected — pick a team or one or more users",
            field="approverUserIds",
        )
    if users_set and team_set:
        return gql_failure(
            ErrorCode.VALIDATION.value,
            "approverTeamId and approverUserIds are mutually exclusive — pick a team OR specific users",
            field="approverTeamId",
        )
    if users_set and effective_minimum_approvals > len(list(effective_user_ids)):
        return gql_failure(
            ErrorCode.VALIDATION.value,
            "minimumApprovals cannot exceed the number of approver users",
            field="minimumApprovals",
        )
    return None


@strawberry.input
class AssignAppToProjectInput:
    """Re-assign an app to a project, or unassign it (#391).

    ``project_guid`` is None to unassign — the app's ``project`` FK
    goes to NULL and the app falls into the team's "Unassigned" nav
    bucket. Otherwise the project is resolved by GUID and must share
    the app's organization. Cross-org assignment is refused.
    """

    app_slug: str
    project_guid: GUID | None = None


def _viewer_can_access_project(*, project, viewer) -> bool:
    """True when ``viewer`` has any active RoleBinding that reaches
    ``project`` — directly or via an ancestor scope.

    Mirrors the read-side resolution done in ``astrolift_my_apps``
    (queries.py): a binding at ORG level covers every project in the
    org; TEAM covers every project under the team; PROJECT covers the
    project itself. APP-scope bindings don't cover the project — a
    user with app-only access to one app under a project shouldn't be
    able to retarget OTHER apps onto that project.

    Superusers short-circuit to True so a platform operator can fix
    nav scoping without needing an explicit grant.
    """
    if viewer is None:
        return False
    if getattr(viewer, "is_superuser", False) and getattr(viewer, "is_active", True):
        return True

    from django.db.models import Q
    from django.utils import timezone

    from astrolift_identity.models import RoleBinding

    now = timezone.now()
    bindings = RoleBinding.objects.filter(
        user_id=viewer.pk,
        deleted_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    for binding in bindings:
        if binding.scope_kind == RoleBinding.ScopeKind.ORG and binding.scope_id == project.organization_id:
            return True
        if binding.scope_kind == RoleBinding.ScopeKind.TEAM and binding.scope_id == project.team_id:
            return True
        if binding.scope_kind == RoleBinding.ScopeKind.PROJECT and binding.scope_id == project.id:
            return True
    return False


@strawberry.input
class PauseAppWebhookDeploysInput:
    """Pause app-global webhook-fired deploys (#399).

    ``reason`` is optional but encouraged — it lands on the audit log
    AND the row itself so the Settings card can render "Paused by X
    · 5m ago — reason: storm" without an audit-log join. Empty / null
    is accepted (the operator may pause without recording context).
    Capped at 512 chars on the model — anything longer is truncated
    at the resolver boundary to keep the audit row reasonable.
    """

    app_slug: str
    reason: str | None = None


@strawberry.input
class ResumeAppWebhookDeploysInput:
    """Lift the app-global webhook-deploy pause (#399).

    No reason field on resume — the audit log captures who flipped it
    back and the timestamp, which is enough provenance for the
    inverse operation. (The original pause's reason stays on the
    audit history; we clear it from the row so a stale string
    doesn't read as the *current* reason on the next pause.)
    """

    app_slug: str


@strawberry.input
class UpdateSecurityPolicyInput:
    """Update the supply-chain / scanner policy for an app (#313).

    Every knob is required on the wire: the form on
    ``/apps/[slug]/security`` always submits the full effective
    policy. ``block_on_high_cve_threshold`` is the one nullable knob —
    None clears the count-based gate; a non-null integer must be at
    least 1 (a threshold of 0 would block every image and is almost
    certainly an input error).
    """

    app_slug: str
    block_on_critical_cves: bool
    block_on_missing_signature: bool
    block_on_high_cve_threshold: int | None


@strawberry.input
class ResyncManifestFromRepoInput:
    """Re-fetch ``astrolift.toml`` from the deploy branch and
    reconcile workloads / env / managed services / schedules.

    Non-destructive on staged drafts: when the DB has an unpushed
    staged manifest AND the repo has new content, the mutation
    refuses (status="diverged") rather than clobbering the draft.
    The destructive equivalent is ``syncManifestFromRepo`` (#277).
    """

    app_slug: str


@strawberry.type
class ResyncManifestPayload:
    """Payload of ``resyncAstroliftManifestFromRepo`` (#386).

    ``sync_state`` is one of ``in_sync``, ``applied``, ``diverged``,
    ``fetch_failed``. ``summary`` is the human-readable one-liner
    the UI surfaces in the success toast. The per-bucket diff
    fields let the FE render a more detailed breakdown when
    ``applied``.
    """

    sync_state: str
    summary: str
    workloads_added: list[str]
    workloads_removed: list[str]
    workloads_changed: list[str]
    managed_services_added: list[str]
    managed_services_removed: list[str]
    env_keys_changed: int
    schedules_changed: int


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

        # An app is a deployment target — without a managed cluster
        # the platform has nowhere to roll the workload to and the
        # downstream deploy fails with an opaque "no cluster available"
        # error. Reject up front instead. Soft-deleted, inactive, and
        # not-yet-managed clusters don't count: only ``lifecycle =
        # "managed"`` rows (#316) have platform RBAC + a passing
        # preflight and can actually accept a deploy.
        cluster_count = TenantCluster.objects.filter(
            organization=project.organization,
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        ).count()
        if cluster_count == 0:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "No managed cluster connected. Visit /clusters and finish bringing a cluster into management before adding apps.",
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

        eff_requires_approval = (
            bool(approval["requires_approval"]) if approval["requires_approval"] is not None else False
        )
        eff_team = approval["team"]
        eff_user_ids = approval["user_ids"] or ()
        eff_minimum_approvals = (
            approval["minimum_approvals"] if approval["minimum_approvals"] is not None else 1
        )
        cross_err = _validate_effective_approval_policy(
            requires_approval=eff_requires_approval,
            effective_team=eff_team,
            effective_user_ids=eff_user_ids,
            effective_minimum_approvals=eff_minimum_approvals,
        )
        if cross_err is not None:
            return cross_err

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
    @mutation_audit(action="app.tear_down")
    @require_permission(Permission.APP_DELETE)
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

        app = RegisteredApp.objects.filter(
            guid=str(input.id),
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
    @require_permission(Permission.APP_TRANSFER, Permission.APP_CREATE)
    @tenant_scoped()
    def transfer_app(self, info: Info, input: TransferAppInput) -> MutationResultType[RegisteredAppType]:
        """Re-parent an app to a different team / project within the
        same org.

        Permission contract:
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

    @strawberry.field
    @mutation_audit(action="app.move_to_team")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def move_app_to_team(
        self, info: Info, input: MoveAppToTeamInput
    ) -> MutationResultType[RegisteredAppType]:
        """Move the app's primary / home team to ``targetTeamId``.

        Distinct from ``transferApp`` (which re-parents to a target
        team *and* project): this mutation focuses on the team
        membership semantics. It re-points ``RegisteredApp.team`` to
        the target team and keeps the ``AppTeamAccess`` join table
        in sync — the target team gains an OWNER row (idempotent) and
        the previous home team is downgraded to DEPLOYER (or
        materialized at DEPLOYER if no row existed) rather than
        revoked. Downgrade-only is the safer default; the previous
        team's existing humans don't lose access mid-flight, and the
        operator can ``revokeTeamAccessFromApp`` explicitly later.
        """

        app = (
            RegisteredApp.objects.select_related("organization", "team", "project")
            .filter(guid=str(input.app_id), deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        target = (
            Team.objects.select_related("organization")
            .filter(guid=str(input.target_team_id), deleted_at__isnull=True)
            .first()
        )
        if target is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "target team not found",
                field="targetTeamId",
            )
        if target.organization_id != app.organization_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cross-organization move is not permitted",
                field="targetTeamId",
            )

        # The existing project must belong to the new team or the tree
        # breaks. ``transferApp`` allows callers to specify a new
        # project alongside; ``moveAppToTeam`` keeps the API narrow
        # (team-only) and refuses orphaning the project.
        if app.project.team_id != target.id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "the app's project belongs to a different team — use transferApp to move both",
                field="targetTeamId",
            )

        previous_team_id = app.team_id
        if previous_team_id == target.id:
            # No-op when already on the target team — but still ensure
            # an OWNER row exists for it.
            _ensure_owner_access(app, target.id, actor=_actor())
            return gql_success(app_to_type(app))

        app.team = target
        app.save(update_fields=["team", "updated_at", "version"])

        _ensure_owner_access(app, target.id, actor=_actor())
        _downgrade_to_deployer(app, previous_team_id, actor=_actor())

        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.grant_team_access")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def grant_team_access_to_app(
        self, info: Info, input: GrantTeamAccessInput
    ) -> MutationResultType[AppTeamAccessType]:
        """Upsert a team's access to an app at the requested level.

        Re-granting at the same level is idempotent (no row change);
        changing the level updates the existing row in place. The
        unique constraint over ``(app, team) WHERE deleted_at IS
        NULL`` prevents duplicate active grants.
        """

        normalized_level = (input.access_level or "").strip().lower()
        valid_levels = {choice.value for choice in AppTeamAccess.AccessLevel}
        if normalized_level not in valid_levels:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"accessLevel must be one of {sorted(valid_levels)}",
                field="accessLevel",
            )

        app = (
            RegisteredApp.objects.select_related("organization", "team")
            .filter(guid=str(input.app_id), deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        team = (
            Team.objects.select_related("organization")
            .filter(guid=str(input.team_id), deleted_at__isnull=True)
            .first()
        )
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamId")

        if team.organization_id != app.organization_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "team must belong to the same organization as the app",
                field="teamId",
            )

        access = (
            AppTeamAccess.objects.select_related("registered_app", "team")
            .filter(registered_app=app, team=team, deleted_at__isnull=True)
            .first()
        )
        if access is None:
            access = AppTeamAccess.objects.create(
                registered_app=app,
                team=team,
                access_level=normalized_level,
            )
        elif access.access_level != normalized_level:
            access.access_level = normalized_level
            access.save(update_fields=["access_level", "updated_at", "version"])

        # Refresh so app + team relations are populated for the
        # type-conversion path.
        access = AppTeamAccess.objects.select_related("registered_app", "team").filter(pk=access.pk).first()
        return gql_success(app_team_access_to_type(access, home_team_id=app.team_id))

    @strawberry.field
    @mutation_audit(action="app.revoke_team_access")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def revoke_team_access_from_app(
        self, info: Info, input: RevokeTeamAccessInput
    ) -> MutationResultType[_SoftDeletePayload]:
        """Soft-delete the team's access grant to the app.

        Refuses when the grant being revoked is the last active OWNER
        row — that would orphan the app. Operators must promote
        another team to OWNER or move the app to a different home
        team first.
        """

        app = (
            RegisteredApp.objects.select_related("organization", "team")
            .filter(guid=str(input.app_id), deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        team = (
            Team.objects.select_related("organization")
            .filter(guid=str(input.team_id), deleted_at__isnull=True)
            .first()
        )
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamId")

        access = AppTeamAccess.objects.filter(registered_app=app, team=team, deleted_at__isnull=True).first()
        if access is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "team has no active access grant on this app",
                field="teamId",
            )

        if access.access_level == AppTeamAccess.AccessLevel.OWNER.value:
            remaining_owners = (
                AppTeamAccess.objects.filter(
                    registered_app=app,
                    access_level=AppTeamAccess.AccessLevel.OWNER.value,
                    deleted_at__isnull=True,
                )
                .exclude(pk=access.pk)
                .count()
            )
            if remaining_owners == 0:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "cannot revoke the last OWNER grant — promote another team first or move the app",
                    field="teamId",
                )

        access.soft_delete(by=_actor())
        return gql_success(
            _SoftDeletePayload(id=GUID(str(access.guid)), deleted=True),
        )

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

        app = RegisteredApp.objects.filter(slug=input.app_slug, deleted_at__isnull=True).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")

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
        app = (
            RegisteredApp.objects.select_related("organization", "team", "project")
            .filter(slug=input.app_slug, deleted_at__isnull=True)
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
        app = RegisteredApp.objects.filter(slug=input.app_slug, deleted_at__isnull=True).first()
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
        from django.utils import timezone

        app = RegisteredApp.objects.filter(slug=input.app_slug, deleted_at__isnull=True).first()
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
        app = RegisteredApp.objects.filter(slug=input.app_slug, deleted_at__isnull=True).first()
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
