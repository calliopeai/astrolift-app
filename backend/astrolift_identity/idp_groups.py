"""
IdP groups: where a user's group memberships live (#2157).

The groups claim of an
SSO sign-in is written to ``Member.idp_groups`` on each of the user's
active ORG membership rows, replacing whatever was there. The IdP is the
install's (``auth1``), the same one every org trusts for the user's
identity, so its group assertion is recorded for each org the user belongs
to; an org without a group binding or mapping for a group is unaffected by
it.

Group role bindings (``RoleBinding.group_external_id``) and
``GroupRoleMapping`` rows match against these, read per org, so a group
never grants outside the org whose row carries it.

SCIM-managed groups use their explicit org membership relation. Once a
group is SCIM-managed, that authority overrides an SSO snapshot for its
identifier, including after removal or deletion, so stale login claims
cannot restore a grant the IdP just revoked. Groups not managed through
SCIM continue to come from the SSO snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.utils import timezone

# Claims that carry the user's groups, in the order they are read. Auth0
# and most OIDC providers use ``groups``; Cognito uses ``cognito:groups``.
GROUP_CLAIMS = ("groups", "cognito:groups")

MAX_GROUPS = 500
MAX_GROUP_LENGTH = 255


def groups_from_claims(claims: Mapping[str, Any] | None) -> list[str]:
    """The group ids an IdP asserted, deduplicated and bounded.

    A claim that is absent or not a list of strings asserts no groups.
    """

    if not claims:
        return []
    for name in GROUP_CLAIMS:
        raw = claims.get(name)
        if raw is None:
            continue
        values = [raw] if isinstance(raw, str) else raw if isinstance(raw, (list, tuple)) else []
        out: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            value = value.strip()
            if value and len(value) <= MAX_GROUP_LENGTH and value not in out:
                out.append(value)
        return sorted(out)[:MAX_GROUPS]
    return []


def sync_member_groups(user, claims: Mapping[str, Any] | None) -> int:
    """Replace ``user``'s IdP groups on every active ORG membership.

    Called at SSO sign-in. A sign-in whose claims carry no groups clears
    them: an assertion the IdP stopped making stops granting. Returns the
    number of membership rows written.
    """

    from astrolift_identity.models import Member

    if user is None or getattr(user, "pk", None) is None:
        return 0
    groups = groups_from_claims(claims)
    return Member.objects.filter(
        user_id=user.pk,
        scope_kind=Member.ScopeKind.ORG,
        is_active=True,
    ).update(idp_groups=groups, idp_groups_synced_at=timezone.now())


def member_groups(user_id: int, organization_id: int) -> frozenset[str]:
    """The groups on ``user_id``'s active ORG membership of one org."""

    from astrolift_identity.models import Member, ScimGroup

    member = (
        Member.objects.filter(
            user_id=user_id,
            scope_kind=Member.ScopeKind.ORG,
            scope_id=organization_id,
            is_active=True,
        )
        .values("pk", "idp_groups")
        .first()
    )
    if member is None:
        return frozenset()
    raw = member["idp_groups"]
    snapshot = {g for g in raw if isinstance(g, str) and g} if isinstance(raw, list) else set()
    # Deleted groups remain authorities for their identifier. Keeping the
    # tombstone prevents a stale SSO claim from reviving their memberships.
    managed = set()
    for group in ScimGroup.all_objects.filter(organization_id=organization_id):
        managed.add(group.group_external_id)
        managed.update(group.retired_external_ids)
    provisioned = {
        external or str(guid)
        for external, guid in ScimGroup.objects.filter(
            organization_id=organization_id, members__pk=member["pk"]
        ).values_list("external_id", "guid")
    }
    return frozenset((snapshot - managed) | provisioned)


def group_member_counts(organization_id: int, groups: list[str]) -> dict[str, int]:
    """How many active members of the org carry each group."""

    from astrolift_identity.models import Member, ScimGroup

    counts: dict[str, int] = {}
    managed = {
        group.group_external_id: group
        for group in ScimGroup.all_objects.filter(organization_id=organization_id)
    }
    retired = {key for group in managed.values() for key in group.retired_external_ids}
    for group in dict.fromkeys(groups):
        members = Member.objects.filter(
            scope_kind=Member.ScopeKind.ORG,
            scope_id=organization_id,
            is_active=True,
        )
        authority = managed.get(group)
        if authority is not None:
            counts[group] = members.filter(
                scim_groups=authority, scim_groups__deleted_at__isnull=True
            ).count()
        elif group in retired:
            counts[group] = 0
        else:
            counts[group] = members.filter(idp_groups__contains=[group]).count()
    return counts
