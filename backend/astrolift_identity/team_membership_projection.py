"""Bounded current-page membership provenance and advisory permission facts."""

from django.contrib.auth import get_user_model
from django.db.models import Q

from astrolift_graphql import GUID, PageType
from astrolift_identity.api_tokens import get_current_api_token, token_scope_allows_permission
from astrolift_identity.grants import GrantCeiling
from astrolift_identity.models import GroupRoleMapping, Member, RoleBinding, ScimGroup
from astrolift_identity.permission_resolver import resolve_effective_permissions_for_teams
from astrolift_identity.team_membership_types import TeamMembershipTeamType, TeamMembershipType
from astrolift_identity.team_memberships import WRITE, _source_link, person_type
from core.permissions import Permission, is_platform_operator
from core.tenancy import get_current_tenant


def _groups(people, organization_id):
    """One page of ORG Member snapshots reduced by exact SCIM authority."""
    result = {p.user_id: frozenset() for p in people}
    active = {p.pk: p for p in people if p.is_active}
    managed = set()
    for row in ScimGroup.all_objects.filter(organization_id=organization_id).values(
        "guid", "external_id", "retired_external_ids"
    ):
        managed.add(row["external_id"] or str(row["guid"]))
        managed.update(row["retired_external_ids"] or [])
    provisioned = {pk: set() for pk in active}
    for pk, external_id, guid in ScimGroup.objects.filter(
        organization_id=organization_id, members__pk__in=active
    ).values_list("members__pk", "external_id", "guid"):
        provisioned[pk].add(external_id or str(guid))
    for pk, person in active.items():
        raw = person.idp_groups
        snapshot = {g for g in raw if isinstance(g, str) and g} if isinstance(raw, list) else set()
        result[person.user_id] = frozenset((snapshot - managed) | provisioned[pk])
    return result


def membership_page(page, pairs):
    """Project the actual numbered page with a constant number of DB reads.

    Direct, expired, inherited and managed sources remain distinct. Current
    role ceilings are advisory only; writes re-admit and re-review under locks.
    """
    from astrolift_identity.team_memberships import sources_to_types

    pairs = list(pairs)
    if not pairs:
        return PageType(
            items=[], next_cursor=None, total_count=page.total_count, page=page.page, page_size=page.page_size
        )
    tenant = get_current_tenant()
    teams = {team.pk: team for team, _person in pairs}
    people = {person.user_id: person for _team, person in pairs}
    groups = _groups(people.values(), tenant.organization_id)
    all_groups = {g for values in groups.values() for g in values}
    owners = Q(scope_kind="TEAM", scope_id__in=teams) | Q(scope_kind="ORG", scope_id=tenant.organization_id)
    principals = Q(user_id__in=people) | Q(user__isnull=True, group_external_id__in=all_groups)
    bindings = list(RoleBinding.objects.select_related("role").filter(principals, owners).order_by("guid"))
    mappings = list(
        GroupRoleMapping.objects.select_related("role")
        .filter(
            Q(role__organization_id=tenant.organization_id) | Q(role__organization__isnull=True),
            owners,
            organization_id=tenant.organization_id,
            group_external_id__in=all_groups,
        )
        .order_by("guid")
    )
    members = {
        (m.scope_id, m.user_id): m
        for m in Member.objects.filter(user_id__in=people, scope_kind="TEAM", scope_id__in=teams)
    }
    permissions = resolve_effective_permissions_for_teams(tenant, teams.values())
    credential = get_current_api_token()
    unrestricted = is_platform_operator(get_user_model().objects.filter(pk=tenant.actor_user_id).first())
    catalog = {permission.value for permission in Permission}
    # These two permission decisions are identical for every source link on
    # the page; no per-row permission or organization reads.
    sample_role = (bindings or mappings)[0].role if bindings or mappings else None
    role_link = bool(sample_role and _source_link(sample_role))
    group_link = bool(sample_role and _source_link(sample_role, "group-link-admission"))
    from urllib.parse import quote

    def source_link(role, group_id):
        if group_id:
            return (
                "/administration/access/people/" + quote("group:" + group_id, safe="") if group_link else None
            )
        return "/administration/permissions/roles/" + str(role.guid) if role_link else None

    items = []
    for team, person in pairs:
        direct = [
            b
            for b in bindings
            if b.user_id == person.user_id and b.scope_kind == "TEAM" and b.scope_id == team.pk
        ]
        own_groups = groups[person.user_id]
        sources = [
            b
            for b in bindings
            if (b.user_id == person.user_id or (b.user_id is None and b.group_external_id in own_groups))
            and (
                (b.scope_kind == "TEAM" and b.scope_id == team.pk)
                or (b.scope_kind == "ORG" and b.scope_id == team.organization_id and b.inherits)
            )
        ]
        group_sources = [
            m
            for m in mappings
            if m.group_external_id in own_groups
            and (
                (m.scope_kind == "TEAM" and m.scope_id == team.pk)
                or (m.scope_kind == "ORG" and m.scope_id == team.organization_id)
            )
        ]
        held = permissions.get(team.pk, set())
        manage = WRITE.value in held and (
            credential is None
            or (
                credential.team_id in (None, team.pk)
                and token_scope_allows_permission(credential, WRITE.value)
            )
        )
        ceiling = GrantCeiling(unrestricted=unrestricted, held=frozenset(held & catalog))
        removable = manage and all(
            b.role.organization_id in (None, team.organization_id) and ceiling.allows(b.role.permissions)
            for b in direct
        )
        member = members.get((team.pk, person.user_id))
        items.append(
            TeamMembershipType(
                team=TeamMembershipTeamType(
                    id=GUID(str(team.guid)),
                    version=team.version,
                    name=team.name,
                    slug=team.slug,
                    can_manage_members=manage,
                ),
                person=person_type(person),
                team_member_id=GUID(str(member.guid)) if member else None,
                lifecycle=member.lifecycle if member else None,
                sources=sources_to_types(team, sources, group_sources, source_link=source_link),
                can_remove=removable and (member is not None or bool(direct)),
            )
        )
    return PageType(
        items=items, next_cursor=None, total_count=page.total_count, page=page.page, page_size=page.page_size
    )


def team_page(page):
    """Bounded target chooser permission hints using current exact teams."""
    from astrolift_identity.team_memberships import WRITE

    permissions = resolve_effective_permissions_for_teams(get_current_tenant(), page.rows)
    credential = get_current_api_token()
    return page.map(
        lambda team: TeamMembershipTeamType(
            id=GUID(str(team.guid)),
            version=team.version,
            name=team.name,
            slug=team.slug,
            can_manage_members=WRITE.value in permissions[team.pk]
            and (
                credential is None
                or (
                    credential.team_id in (None, team.pk)
                    and token_scope_allows_permission(credential, WRITE.value)
                )
            ),
        )
    )
