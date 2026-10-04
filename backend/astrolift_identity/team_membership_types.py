"""Public GUID-only reviewed team membership contracts."""

from datetime import datetime
from enum import Enum

import strawberry

from astrolift_graphql import GUID


@strawberry.enum
class TeamMembershipChangeKind(Enum):
    ADD = "ADD"
    REMOVE = "REMOVE"


@strawberry.type(name="AstroliftTeamAccessSource")
class TeamAccessSourceType:
    id: GUID
    source: str
    scope_kind: str
    role_name: str
    role_id: GUID
    source_href: str | None
    expires_at: datetime | None
    expired: bool
    removable: bool


@strawberry.type(name="AstroliftTeamMembershipRole")
class TeamMembershipRoleType:
    id: GUID
    version: int
    name: str
    permissions: list[str]


@strawberry.type(name="AstroliftTeamMembershipTeam")
class TeamMembershipTeamType:
    id: GUID
    version: int
    name: str
    slug: str
    can_manage_members: bool


@strawberry.type(name="AstroliftTeamMembershipPerson")
class TeamMembershipPersonType:
    org_member_id: GUID
    name: str
    email: str
    active: bool


@strawberry.type(name="AstroliftTeamMembership")
class TeamMembershipType:
    team: TeamMembershipTeamType
    person: TeamMembershipPersonType
    team_member_id: GUID | None
    lifecycle: str | None
    sources: list[TeamAccessSourceType]
    can_remove: bool


@strawberry.type(name="AstroliftTeamMembershipReview")
class TeamMembershipReviewType:
    kind: TeamMembershipChangeKind
    expected_source: str
    membership: TeamMembershipType
    roles: list[TeamMembershipRoleType]
    remaining_sources: list[TeamAccessSourceType]


@strawberry.input
class ChangeAstroliftTeamMembershipInput:
    request_id: GUID
    kind: TeamMembershipChangeKind
    team_id: GUID
    org_member_id: GUID
    expected_source: str
    role_id: GUID | None = None


@strawberry.type(name="AstroliftTeamMembershipChange")
class TeamMembershipChangeType:
    request_id: GUID
    change_id: GUID
    committed: bool
    replayed: bool
    team_id: GUID
    org_member_id: GUID
    team_member_id: GUID | None
    removed_binding_ids: list[GUID]
    remaining_sources: list[TeamAccessSourceType]


@strawberry.type(name="AstroliftTeamAccessNavigation")
class TeamAccessNavigationType:
    """Current credential's landing hints; never executable action proof."""

    can_view_teams: bool
    can_manage_team_members: bool
    can_view_people: bool
    can_view_roles: bool
    can_view_policies: bool
    can_check_access: bool
