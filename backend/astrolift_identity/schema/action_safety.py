"""GraphQL for the action-safety gates (#2202): what applies to the viewer, and org rules."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.action_safety import ACTIONS, effective
from astrolift_identity.scopes import identity_organization_scope
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type(name="AstroliftActionSafetyRequirement")
class ActionSafetyRequirementType:
    action: str
    label: str
    enabled: bool
    confirmation: bool
    verification: bool
    allow_self_approval: bool
    sources: list[str]


@strawberry.input(name="SetActionSafetyRuleInput")
class SetActionSafetyRuleInput:
    action: str
    scope_kind: str
    scope_id: GUID | None = None
    enabled: bool | None = None
    allow_self_approval: bool | None = None


@strawberry.type(name="AstroliftActionSafetyRule")
class ActionSafetyRuleType:
    action: str
    scope_kind: str
    scope_id: GUID | None
    enabled: bool | None
    allow_self_approval: bool | None


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
                sources=list(r.sources),
            )
        )
    return out


def _scope_pk(organization_id: int, kind: str, scope_id) -> int | None:
    """The org-confined primary key for a TEAM, APP or USER guid, else None."""
    from astrolift_identity.models import Member, Team
    from astrolift_registry.models import RegisteredApp

    if kind == "TEAM":
        row = Team.objects.filter(organization_id=organization_id, guid=str(scope_id)).first()
        return row.pk if row else None
    if kind == "APP":
        row = RegisteredApp.objects.filter(organization_id=organization_id, guid=str(scope_id)).first()
        return row.pk if row else None
    if kind == "USER":
        member = (
            Member.objects.filter(scope_kind="ORG", scope_id=organization_id, user__guid=str(scope_id))
            .select_related("user")
            .first()
        )
        return member.user_id if member else None
    return None


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
        row, _ = ActionSafetyRule.objects.update_or_create(
            organization_id=org_id,
            scope_kind=kind,
            scope_id=scope_pk,
            action=input.action,
            deleted_at__isnull=True,
            defaults={"enabled": input.enabled, "allow_self_approval": input.allow_self_approval},
        )
        return gql_success(
            ActionSafetyRuleType(
                action=row.action,
                scope_kind=row.scope_kind,
                scope_id=input.scope_id if kind != "ORG" else None,
                enabled=row.enabled,
                allow_self_approval=row.allow_self_approval,
            )
        )
