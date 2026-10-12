"""GraphQL for the action-safety gates (#2202): what applies to the viewer, org rules,
and the approval requests a rule can add to a destructive delete."""

from __future__ import annotations

from datetime import datetime

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.action_safety import ACTIONS, APPROVABLE, MAX_APPROVALS, audit, effective
from astrolift_identity.scopes import identity_organization_scope
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    check_permission,
    require_permission,
)
from core.tenancy import get_current_tenant


@strawberry.type(name="AstroliftActionSafetyRequirement")
class ActionSafetyRequirementType:
    action: str
    label: str
    enabled: bool
    confirmation: bool
    verification: bool
    allow_self_approval: bool
    #: Whether a rule can add approval to this action at all.
    approvable: bool
    approval: bool
    minimum_approvals: int
    sources: list[str]


@strawberry.input(name="SetActionSafetyRuleInput")
class SetActionSafetyRuleInput:
    action: str
    scope_kind: str
    #: A team or app guid, or a person's user id; null for the organization.
    scope_id: str | None = None
    enabled: bool | None = None
    allow_self_approval: bool | None = None
    require_approval: bool | None = None
    minimum_approvals: int | None = None


@strawberry.type(name="AstroliftActionSafetyRule")
class ActionSafetyRuleType:
    action: str
    scope_kind: str
    #: A team or app guid, or a person's user id; null for the organization.
    scope_id: str | None
    #: The team, app or person's display name; empty for the organization.
    scope_label: str = ""
    enabled: bool | None
    allow_self_approval: bool | None
    require_approval: bool | None
    minimum_approvals: int | None


@strawberry.type(name="AstroliftActionApprovalRequest")
class ActionApprovalRequestType:
    id: GUID
    action: str
    label: str
    target_kind: str
    target_label: str
    app_slug: str | None
    requester_email: str | None
    viewer_is_requester: bool
    status: str
    required_approvals: int
    approvals: int
    allow_self_approval: bool
    created_at: datetime
    expires_at: datetime


@strawberry.input(name="ClearActionSafetyRuleInput")
class ClearActionSafetyRuleInput:
    action: str
    scope_kind: str
    scope_id: str | None = None


@strawberry.input(name="DecideActionApprovalInput")
class DecideActionApprovalInput:
    id: GUID
    reason: str | None = None


def _app(organization_id: int, app_slug: str | None):
    from astrolift_registry.models import RegisteredApp

    if not app_slug:
        return None
    return RegisteredApp.objects.filter(
        organization_id=organization_id, slug=app_slug, deleted_at__isnull=True
    ).first()


def requirements_for_viewer(app_slug: str | None) -> list[ActionSafetyRequirementType]:
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return []
    app = _app(tenant.organization_id, app_slug)
    out = []
    for action, label in ACTIONS.items():
        r = effective(
            tenant.organization_id,
            action,
            team_id=getattr(app, "team_id", None),
            app_id=getattr(app, "pk", None),
            user_id=tenant.actor_user_id,
        )
        out.append(
            ActionSafetyRequirementType(
                action=action,
                label=label,
                enabled=r.enabled,
                confirmation=r.confirmation,
                verification=r.verification,
                allow_self_approval=r.allow_self_approval,
                approvable=action in APPROVABLE,
                approval=r.approval,
                minimum_approvals=r.minimum_approvals,
                sources=list(r.sources),
            )
        )
    return out


def rules_for_org() -> list[ActionSafetyRuleType]:
    """Every rule row in the organization, with its scope named."""
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import ActionSafetyRule, Team
    from astrolift_registry.models import RegisteredApp

    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return []
    rows = list(
        ActionSafetyRule.objects.filter(organization_id=tenant.organization_id).order_by(
            "scope_kind", "action"
        )
    )
    ids = {kind: {r.scope_id for r in rows if r.scope_kind == kind} for kind in ("TEAM", "APP", "USER")}
    names = {
        "TEAM": {t.pk: (str(t.guid), t.name) for t in Team.objects.filter(pk__in=ids["TEAM"])},
        "APP": {
            a.pk: (str(a.guid), a.name or a.slug) for a in RegisteredApp.objects.filter(pk__in=ids["APP"])
        },
        "USER": {u.pk: (str(u.pk), u.email) for u in get_user_model().objects.filter(pk__in=ids["USER"])},
    }
    out = []
    for r in rows:
        guid, label = names.get(r.scope_kind, {}).get(r.scope_id, (None, ""))
        if r.scope_kind != "ORG" and guid is None:
            continue  # its team, app or person is gone
        out.append(
            ActionSafetyRuleType(
                action=r.action,
                scope_kind=r.scope_kind,
                scope_id=guid,
                scope_label=label,
                enabled=r.enabled,
                allow_self_approval=r.allow_self_approval,
                require_approval=r.require_approval,
                minimum_approvals=r.minimum_approvals,
            )
        )
    return out


def _scope_pk(organization_id: int, kind: str, scope_id) -> int | None:
    """The org-confined primary key for a TEAM or APP guid, or a USER id
    (users are addressed by id across this API), else None."""
    from uuid import UUID

    from astrolift_identity.models import Member, Team
    from astrolift_registry.models import RegisteredApp

    raw = str(scope_id or "").strip()
    if kind == "USER":
        if not raw.isdigit():
            return None
        member = Member.objects.filter(scope_kind="ORG", scope_id=organization_id, user_id=int(raw)).first()
        return member.user_id if member else None
    try:
        guid = str(UUID(raw))
    except ValueError:
        return None
    model = {"TEAM": Team, "APP": RegisteredApp}.get(kind)
    row = model.objects.filter(organization_id=organization_id, guid=guid).first() if model else None
    return row.pk if row else None


@strawberry.type
class ActionSafetyMutations:
    @strawberry.field
    @mutation_audit(action="org.action_safety.set")
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def set_action_safety_rule(
        self, info: Info, input: SetActionSafetyRuleInput
    ) -> MutationResultType[ActionSafetyRuleType]:
        from astrolift_identity.models import ActionSafetyRule
        from astrolift_identity.step_up import check_elevation

        # Loosening or tightening a safety rule is itself a sensitive change.
        deny = check_elevation(
            info,
            action_label="Change action-safety rules",
            resolver_name="set_action_safety_rule",
            required=True,
        )
        if deny is not None:
            return deny
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")
        if input.action not in ACTIONS:
            return gql_failure(ErrorCode.VALIDATION.value, f"unknown action {input.action!r}", field="action")
        kind = (input.scope_kind or "").upper()
        if kind not in ActionSafetyRule.ScopeKind.values:
            return gql_failure(
                ErrorCode.VALIDATION.value, "scope_kind must be ORG, TEAM, APP or USER", field="scopeKind"
            )
        if kind == "ORG":
            scope_pk = None
        else:
            if input.scope_id is None:
                return gql_failure(
                    ErrorCode.VALIDATION.value, "scope_id is required for this scope", field="scopeId"
                )
            scope_pk = _scope_pk(org_id, kind, input.scope_id)
            if scope_pk is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value, "scope not found in this organization", field="scopeId"
                )
            # Strictest wins: only the organization can turn a gate off.
            if input.enabled is False:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "only the organization can turn a gate off; a narrower scope can only add to it",
                    field="enabled",
                )
        if input.require_approval and input.action not in APPROVABLE:
            hint = (
                "; secret changes have their own approval setting on the app"
                if input.action == "secret.change"
                else ""
            )
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"approval cannot be added to {input.action!r}{hint}",
                field="requireApproval",
            )
        if input.minimum_approvals is not None and not 1 <= input.minimum_approvals <= MAX_APPROVALS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"minimum_approvals must be between 1 and {MAX_APPROVALS}",
                field="minimumApprovals",
            )
        row, _ = ActionSafetyRule.objects.update_or_create(
            organization_id=org_id,
            scope_kind=kind,
            scope_id=scope_pk,
            action=input.action,
            deleted_at__isnull=True,
            # Only the switches sent change; the rest of the row stays as it was.
            defaults={
                name: value
                for name, value in {
                    "enabled": input.enabled,
                    "allow_self_approval": input.allow_self_approval,
                    "require_approval": input.require_approval,
                    "minimum_approvals": input.minimum_approvals,
                }.items()
                if value is not None
            },
        )
        return gql_success(
            ActionSafetyRuleType(
                action=row.action,
                scope_kind=row.scope_kind,
                scope_id=input.scope_id if kind != "ORG" else None,
                enabled=row.enabled,
                allow_self_approval=row.allow_self_approval,
                require_approval=row.require_approval,
                minimum_approvals=row.minimum_approvals,
            )
        )

    @strawberry.field
    @mutation_audit(action="org.action_safety.clear")
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def clear_action_safety_rule(
        self, info: Info, input: ClearActionSafetyRuleInput
    ) -> MutationResultType[ActionSafetyRuleType]:
        """Remove one scope's rule, so what applies there is inherited again."""
        from astrolift_identity.models import ActionSafetyRule
        from astrolift_identity.step_up import check_elevation

        deny = check_elevation(
            info,
            action_label="Change action-safety rules",
            resolver_name="clear_action_safety_rule",
            required=True,
        )
        if deny is not None:
            return deny
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        kind = (input.scope_kind or "").upper()
        if org_id is None or kind not in ActionSafetyRule.ScopeKind.values:
            return gql_failure(ErrorCode.NOT_FOUND.value, "rule not found")
        scope_pk = None if kind == "ORG" else _scope_pk(org_id, kind, input.scope_id)
        row = ActionSafetyRule.objects.filter(
            organization_id=org_id, scope_kind=kind, scope_id=scope_pk, action=input.action
        ).first()
        if row is None or (kind != "ORG" and scope_pk is None):
            return gql_failure(ErrorCode.NOT_FOUND.value, "rule not found")
        row.deleted_by_id = tenant.actor_user_id
        row.soft_delete()
        return gql_success(
            ActionSafetyRuleType(
                action=row.action,
                scope_kind=row.scope_kind,
                scope_id=input.scope_id if kind != "ORG" else None,
                enabled=None,
                allow_self_approval=None,
                require_approval=None,
                minimum_approvals=None,
            )
        )

    @strawberry.field
    @mutation_audit(action="action_safety.approval.approve")
    @require_permission(Permission.ORG_READ, scope=identity_organization_scope(Permission.ORG_READ))
    @tenant_scoped()
    def approve_action_request(
        self, info: Info, input: DecideActionApprovalInput
    ) -> MutationResultType[ActionApprovalRequestType]:
        return _decide(info, input, approve=True)

    @strawberry.field
    @mutation_audit(action="action_safety.approval.reject")
    @require_permission(Permission.ORG_READ, scope=identity_organization_scope(Permission.ORG_READ))
    @tenant_scoped()
    def reject_action_request(
        self, info: Info, input: DecideActionApprovalInput
    ) -> MutationResultType[ActionApprovalRequestType]:
        return _decide(info, input, approve=False)

    @strawberry.field
    @mutation_audit(action="action_safety.approval.cancel")
    @require_permission(Permission.ORG_READ, scope=identity_organization_scope(Permission.ORG_READ))
    @tenant_scoped()
    def cancel_action_request(
        self, info: Info, input: DecideActionApprovalInput
    ) -> MutationResultType[ActionApprovalRequestType]:
        from astrolift_identity.models import ActionApprovalRequest

        tenant = get_current_tenant()
        with transaction.atomic():
            row = _locked_request(tenant, input.id)
            if row is None or row.requester_id != tenant.actor_user_id:
                return gql_failure(ErrorCode.NOT_FOUND.value, "approval request not found")
            if row.status not in (
                ActionApprovalRequest.Status.PENDING,
                ActionApprovalRequest.Status.APPROVED,
            ):
                return gql_failure(ErrorCode.PRECONDITION.value, f"the request is already {row.status}")
            # Withdrawing an approved request is allowed too: it was never run.
            row.status = ActionApprovalRequest.Status.CANCELLED
            row.decided_at = row.decided_at or timezone.now()
            row.save(update_fields=["status", "decided_at", "updated_at", "version"])
        return gql_success(request_to_type(row, tenant.actor_user_id))


def request_to_type(row, viewer_id: int | None) -> ActionApprovalRequestType:
    from astrolift_identity.models import ActionApprovalDecision

    approvals = row.decisions.filter(
        decision=ActionApprovalDecision.Decision.APPROVED, deleted_at__isnull=True
    ).count()
    return ActionApprovalRequestType(
        id=row.guid,
        action=row.action,
        label=ACTIONS.get(row.action, row.action),
        target_kind=row.target_kind,
        target_label=row.target_label,
        app_slug=getattr(row.registered_app, "slug", None),
        requester_email=getattr(row.requester, "email", None),
        viewer_is_requester=row.requester_id == viewer_id,
        status=row.status,
        required_approvals=row.required_approvals,
        approvals=approvals,
        allow_self_approval=row.allow_self_approval,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


def requests_for_viewer(status: str | None) -> list[ActionApprovalRequestType]:
    from astrolift_identity.models import ActionApprovalRequest

    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return []
    qs = ActionApprovalRequest.objects.filter(
        organization_id=tenant.organization_id, deleted_at__isnull=True
    ).select_related("registered_app", "requester")
    if status:
        qs = qs.filter(status=status)
    return [request_to_type(row, tenant.actor_user_id) for row in qs.order_by("-created_at")[:100]]


def _locked_request(tenant, guid):
    from astrolift_identity.models import ActionApprovalRequest

    if tenant is None or tenant.organization_id is None:
        return None
    return (
        ActionApprovalRequest.objects.select_for_update(of=("self",))
        .select_related("registered_app", "requester")
        .filter(organization_id=tenant.organization_id, guid=str(guid), deleted_at__isnull=True)
        .first()
    )


def _approver_permission(row) -> tuple[Permission, PermissionScope]:
    """What deciding ``row`` asks of the approver: the authority the action itself needs."""
    org = PermissionScope(kind=ScopeKind.ORG, id=row.organization_id)
    app = PermissionScope(kind=ScopeKind.APP, id=row.registered_app_id) if row.registered_app_id else None
    if row.action == "app.deregister":
        return Permission.APP_DELETE, app or org
    if row.action == "cluster.decommission":
        return Permission.CLUSTER_UNREGISTER, org
    return (Permission.APP_UPDATE, app) if app else (Permission.ORG_UPDATE, org)


def _decide(info: Info, input: DecideActionApprovalInput, *, approve: bool):
    from astrolift_identity.models import ActionApprovalDecision, ActionApprovalRequest
    from astrolift_identity.step_up import check_elevation

    Status = ActionApprovalRequest.Status
    tenant = get_current_tenant()
    actor = tenant.actor_user_id if tenant else None
    if actor is None:
        return gql_failure(ErrorCode.PERMISSION_DENIED.value, "a signed-in user is required")
    with transaction.atomic():
        row = _locked_request(tenant, input.id)
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "approval request not found")
        if row.status == Status.PENDING and row.expires_at <= timezone.now():
            row.transition_to(Status.EXPIRED)
            row.save(update_fields=["status", "decided_at", "updated_at", "version"])
            audit("action_safety.approval.expire", row, actor_user_id=actor)
        if row.status != Status.PENDING:
            return gql_failure(ErrorCode.PRECONDITION.value, f"the request is already {row.status}")
        permission, scope = _approver_permission(row)
        own = row.requester_id == actor
        try:
            check_permission(permission, scope=scope)
            if own and approve:
                # Approving your own request takes organization-admin authority
                # now, on top of the rule allowing it, then and now.
                check_permission(
                    Permission.ORG_UPDATE, scope=PermissionScope(kind=ScopeKind.ORG, id=row.organization_id)
                )
        except PermissionDenied:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "you cannot decide this request")
        if own and approve:
            now_allowed = effective(
                row.organization_id,
                row.action,
                team_id=getattr(row.registered_app, "team_id", None),
                app_id=row.registered_app_id,
                user_id=actor,
            ).allow_self_approval
            if not (row.allow_self_approval and now_allowed):
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "this rule does not let you approve your own request; another approver is needed",
                )
        deny = check_elevation(
            info,
            action_label=f"{'Approve' if approve else 'Reject'}: {ACTIONS.get(row.action, row.action)}",
            resolver_name="approve_action_request" if approve else "reject_action_request",
            required=effective(row.organization_id, row.action, app_id=row.registered_app_id).verification,
        )
        if deny is not None:
            return deny
        if row.decisions.filter(approver_id=actor, deleted_at__isnull=True).exists():
            return gql_failure(ErrorCode.CONFLICT.value, "you have already decided this request")
        ActionApprovalDecision.objects.create(
            request=row,
            approver_id=actor,
            decision=ActionApprovalDecision.Decision.APPROVED
            if approve
            else ActionApprovalDecision.Decision.REJECTED,
            self_approval=own,
            reason=(input.reason or "").strip()[:2000],
            created_by_id=actor,
            updated_by_id=actor,
        )
        if own and approve:
            audit("action_safety.approval.self_approve", row, actor_user_id=actor)
        if not approve:
            row.transition_to(Status.REJECTED)
            row.save(update_fields=["status", "decided_at", "updated_at", "version"])
        else:
            approvals = row.decisions.filter(
                decision=ActionApprovalDecision.Decision.APPROVED, deleted_at__isnull=True
            ).count()
            if approvals >= row.required_approvals:
                row.transition_to(Status.APPROVED)
                row.save(update_fields=["status", "decided_at", "updated_at", "version"])
    return gql_success(request_to_type(row, actor))
