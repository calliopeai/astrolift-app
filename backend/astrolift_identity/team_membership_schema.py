"""First-class paged user ↔ team membership API (#2273)."""

from datetime import datetime

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType, PageType, failure, success
from astrolift_graphql.pagination import numbered_page, search_q
from astrolift_identity.models import Member, Team
from astrolift_identity.scopes import team_scope_by_guid
from astrolift_identity.step_up import requires_elevation
from astrolift_identity.team_membership_projection import membership_page, team_page
from astrolift_identity.team_membership_types import (
    ChangeAstroliftTeamMembershipInput,
    TeamAccessSourceType,
    TeamMembershipChangeKind,
    TeamMembershipChangeType,
    TeamMembershipPersonType,
    TeamMembershipReviewType,
    TeamMembershipRoleType,
    TeamMembershipTeamType,
    TeamMembershipType,
)
from astrolift_identity.team_memberships import (
    READ,
    WRITE,
    MembershipRefusal,
    admitted,
    change,
    eligible_roles,
    people_search,
    person_target,
    person_teams,
    person_type,
    refuse,
    review,
    roster,
    team_gate,
    team_target,
    team_type,
)
from astrolift_identity.visibility import visible_identity_teams
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


def _person_admission(person):
    tenant = get_current_tenant()
    if person.user_id == tenant.actor_user_id:
        return
    try:
        check_permission(
            Permission.ORG_MANAGE_MEMBERS,
            scope=PermissionScope(kind=ScopeKind.ORG, id=tenant.organization_id),
        )
    except PermissionDenied:
        # A team reader can inspect people only through a visible roster.
        if not person_teams(person).exists():
            refuse()


def _empty():
    return PageType(items=[], total_count=0, page=1, page_size=25)


def _target(*_args, **kwargs):
    input = kwargs.get("input")
    return ("team", str(input.team_id)) if input else None


@strawberry.type
class TeamMembershipQuery:
    @strawberry.field
    @require_permission(READ, any_scope=True)
    @tenant_scoped()
    def astrolift_team_membership_team(
        self, info: Info, team_id: GUID | None = None, slug: str | None = None
    ) -> TeamMembershipTeamType | None:
        """Exact org-scoped selector; choose one GUID or current route slug."""
        try:
            with admitted(info):
                if (team_id is None) == (slug is None):
                    refuse("choose exactly one team selector", ErrorCode.VALIDATION)
                team = team_target(team_id, slug=slug)
                team_gate(team)
                return team_type(team)
        except (MembershipRefusal, PermissionDenied):
            return None

    @strawberry.field
    @require_permission(READ, any_scope=True)
    @tenant_scoped()
    def astrolift_team_membership_person(
        self, info: Info, org_member_id: GUID
    ) -> TeamMembershipPersonType | None:
        try:
            with admitted(info):
                person = person_target(org_member_id)
                _person_admission(person)
                return person_type(person)
        except (MembershipRefusal, PermissionDenied):
            return None

    @strawberry.field
    @require_permission(READ, scope=team_scope_by_guid(permission=READ))
    @tenant_scoped()
    def astrolift_team_memberships_page(
        self, info: Info, team_id: GUID, search: str | None = None, page: int = 1, page_size: int = 25
    ) -> PageType[TeamMembershipType]:
        with admitted(info):
            team = team_target(team_id)
            team_gate(team)
            page_result = numbered_page(
                people_search(roster(team), search),
                order_by=["user__username", "guid"],
                page=page,
                page_size=page_size,
            )
            return membership_page(page_result, ((team, person) for person in page_result.rows))

    @strawberry.field
    @require_permission(READ, any_scope=True)
    @tenant_scoped()
    def astrolift_person_team_memberships_page(
        self, info: Info, org_member_id: GUID, search: str | None = None, page: int = 1, page_size: int = 25
    ) -> PageType[TeamMembershipType]:
        with admitted(info):
            person = person_target(org_member_id)
            _person_admission(person)
            teams = person_teams(person)
            if search:
                teams = teams.filter(search_q(search.strip()[:200], "name", "slug"))
            page_result = numbered_page(teams, order_by=["name", "guid"], page=page, page_size=page_size)
            return membership_page(page_result, ((team, person) for team in page_result.rows))

    @strawberry.field
    @require_permission(WRITE, scope=team_scope_by_guid(permission=WRITE))
    @tenant_scoped()
    def astrolift_team_member_candidates_page(
        self, info: Info, team_id: GUID, search: str | None = None, page: int = 1, page_size: int = 25
    ) -> PageType[TeamMembershipPersonType]:
        with admitted(info):
            team = team_target(team_id)
            team_gate(team, WRITE)
            people = Member.objects.select_related("user").filter(
                scope_kind="ORG", scope_id=team.organization_id, is_active=True, user__is_active=True
            )
            return numbered_page(
                people_search(people, search),
                order_by=["user__username", "guid"],
                page=page,
                page_size=page_size,
            ).map(person_type)

    @strawberry.field
    @require_permission(WRITE, scope=team_scope_by_guid(permission=WRITE))
    @tenant_scoped()
    def astrolift_team_membership_roles_page(
        self,
        info: Info,
        team_id: GUID,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> PageType[TeamMembershipRoleType]:
        with admitted(info):
            team = team_target(team_id)
            team_gate(team, WRITE)
            roles = eligible_roles(team)
            if search:
                roles = roles.filter(search_q(search.strip()[:200], "name", "slug"))
            return numbered_page(roles, order_by=["name", "guid"], page=page, page_size=page_size).map(
                lambda role: TeamMembershipRoleType(
                    id=GUID(str(role.guid)),
                    version=role.version,
                    name=role.name,
                    permissions=role.permissions,
                )
            )

    @strawberry.field
    @require_permission(WRITE, any_scope=True)
    @tenant_scoped()
    def astrolift_membership_teams_page(
        self, info: Info, search: str | None = None, page: int = 1, page_size: int = 25
    ) -> PageType[TeamMembershipTeamType]:
        """Paged add-to-team targets, limited to current manage authority."""
        with admitted(info):
            tenant = get_current_tenant()
            teams = visible_identity_teams(Team.objects.filter(organization_id=tenant.organization_id), WRITE)
            if search:
                teams = teams.filter(search_q(search.strip()[:200], "name", "slug"))
            return team_page(numbered_page(teams, order_by=["name", "guid"], page=page, page_size=page_size))

    @strawberry.field
    @require_permission(WRITE, scope=team_scope_by_guid(permission=WRITE))
    @tenant_scoped()
    def astrolift_team_membership_review(
        self,
        info: Info,
        team_id: GUID,
        org_member_id: GUID,
        kind: TeamMembershipChangeKind,
        role_id: GUID | None = None,
    ) -> TeamMembershipReviewType | None:
        try:
            with admitted(info):
                return review(team_target(team_id), person_target(org_member_id), kind, role_id)
        except (MembershipRefusal, PermissionDenied):
            return None


@strawberry.type
class TeamMembershipMutation:
    @strawberry.field
    @mutation_audit(action="team.membership.change", target=_target)
    @requires_elevation(action_label="team.membership.change")
    @require_permission(WRITE, scope=team_scope_by_guid("input.team_id", permission=WRITE))
    @tenant_scoped()
    def change_astrolift_team_membership(
        self, info: Info, input: ChangeAstroliftTeamMembershipInput
    ) -> MutationResultType[TeamMembershipChangeType]:
        try:
            result = change(info, input)
            if not isinstance(result, tuple):
                return result
            receipt, replayed = result
            data = receipt.result
            sources = []
            for source in data["remainingSources"]:
                source = dict(source)
                source["id"] = GUID(source["id"])
                source["role_id"] = GUID(source["role_id"])
                source["expires_at"] = (
                    datetime.fromisoformat(source["expires_at"]) if source["expires_at"] else None
                )
                sources.append(TeamAccessSourceType(**source))
            return success(
                TeamMembershipChangeType(
                    request_id=GUID(str(receipt.request_id)),
                    change_id=GUID(str(receipt.guid)),
                    committed=True,
                    replayed=replayed,
                    team_id=GUID(data["teamId"]),
                    org_member_id=GUID(data["orgMemberId"]),
                    team_member_id=GUID(data["teamMemberId"]) if data["teamMemberId"] else None,
                    removed_binding_ids=[GUID(value) for value in data["removedBindingIds"]],
                    remaining_sources=sources,
                )
            )
        except MembershipRefusal as exc:
            return failure(exc.code.value, exc.message)
        except PermissionDenied as exc:
            return failure(ErrorCode.PERMISSION_DENIED.value, exc.reason)
