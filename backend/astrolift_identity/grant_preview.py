"""
The grant preview: who a draft grant, change or removal affects (#2126).

Read-only; nothing is written. The answer is worked out from the grants the
permission resolver reads (``access.access_on``: user and group bindings on
the scope and its inheriting ancestors, the org's group mappings, and on an
app the team shares), at the draft's scope, now:

* GRANT: the draft role on the draft scope for each principal. A user is
  one person; an IdP group is its members today (the members of the org
  whose stored groups carry it), and the binding keeps reaching whoever
  joins; a team is its members, since a team cannot hold a binding and
  granting to it means one binding per member.
* CHANGE: the binding's role (and expiry) replaced, for whoever holds it.
* REMOVE: the binding gone, for whoever holds it.

For each person it compares the permissions in play (the roles' slugs)
before and after: **gaining** people get at least one they did not have,
**losing** people lose at least one, **unchanged** people already had them
(or keep them) through another grant, which is listed with its source.
ABAC policies are not applied: a preview says what RBAC would grant.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from django.utils import timezone

from astrolift_graphql import GUID

MAX_PRINCIPALS = 50

ACTIONS = ("GRANT", "CHANGE", "REMOVE")


@dataclasses.dataclass
class _Person:
    user_id: int
    through: list[str] = dataclasses.field(default_factory=list)


def preview(tenant, org_id: int | None, draft, *, limit: int):
    from astrolift_identity.access import resolve_target
    from astrolift_identity.models import Member, RoleBinding, Team
    from astrolift_identity.schema.identity_lists import visible_role
    from astrolift_identity.schema.queries import _org_scope_q

    action = (draft.action or "").strip().upper()
    if org_id is None:
        return _failed(action, "no active organization")
    if action not in ACTIONS:
        return _failed(action, "action must be GRANT, CHANGE or REMOVE")

    notes: list[str] = []
    people: dict[int, _Person] = {}
    groups: list[str] = []
    changed_guid = ""
    new_role = None
    old_role = None
    new_live = True

    def reach(user_id: int, how: str) -> None:
        people.setdefault(user_id, _Person(user_id)).through.append(how)

    if action == "GRANT":
        new_role = visible_role(org_id, draft.role_id) if draft.role_id is not None else None
        if new_role is None:
            return _failed(action, "role not found")
        target = resolve_target(draft.scope_kind, draft.scope_id, org_id)
        if target is None:
            return _failed(action, "scope not found")
        refs = list(draft.principals or [])
        if not refs:
            return _failed(action, "give at least one principal")
        if len(refs) > MAX_PRINCIPALS:
            return _failed(action, f"at most {MAX_PRINCIPALS} principals at once")
        expires_at = draft.expires_at if draft.expires_at is not _unset() else None
        if expires_at is not None and expires_at <= timezone.now():
            return _failed(action, "expiresAt must be in the future")
        if expires_at is not None:
            notes.append(f"The grant ends at {expires_at.isoformat()}.")
        members = Member.objects.filter(scope_kind=Member.ScopeKind.ORG, scope_id=org_id)
        errors: list[str] = []
        for ref in refs:
            kind = (ref.kind or "").strip().upper()
            ident = (ref.id or "").strip()
            if kind == "USER":
                user_id = int(ident) if ident.isdigit() else None
                if user_id is None or not members.filter(user_id=user_id).exists():
                    errors.append(f"user {ident!r} is not a member of this organization")
                    continue
                reach(user_id, "direct")
            elif kind == "GROUP":
                if not ident or len(ident) > 255:
                    errors.append("a group id is required and at most 255 characters")
                    continue
                groups.append(ident)
                for user_id in members.filter(is_active=True, idp_groups__contains=[ident]).values_list(
                    "user_id", flat=True
                ):
                    reach(user_id, f"group {ident}")
            elif kind == "TEAM":
                team = resolve_target("TEAM", ident, org_id)
                if team is None:
                    errors.append(f"team {ident!r} not found")
                    continue
                slug = (
                    Team.objects.filter(pk=team.pk, organization_id=org_id)
                    .values_list("slug", flat=True)
                    .first()
                )
                on_team = Member.objects.filter(scope_kind=Member.ScopeKind.TEAM, scope_id=team.pk).values(
                    "user_id"
                )
                for user_id in members.filter(user_id__in=on_team).values_list("user_id", flat=True):
                    reach(user_id, f"team {slug}")
                notes.append(
                    f"A team cannot hold a binding: granting to team {slug} writes one binding per member, "
                    "and people who join it later get nothing."
                )
            else:
                errors.append(f"principal kind {kind!r} must be USER, GROUP or TEAM")
        if errors:
            return _failed(action, *errors)
        in_play = set(new_role.permissions or ())
        duplicates = _existing(new_role, target, people, groups)
        if duplicates:
            notes.append(
                f"{duplicates} of these already hold {new_role.slug} on this scope; granting it again is refused."
            )
    else:
        if draft.binding_id is None:
            return _failed(action, "bindingId is required")
        binding = (
            RoleBinding.objects.select_related("role", "user")
            .filter(guid=str(draft.binding_id))
            .filter(_org_scope_q(org_id))
            .first()
        )
        if binding is None:
            return _failed(action, "role binding not found")
        target = resolve_target(binding.scope_kind, str(binding.scope_id), org_id)
        if target is None:
            return _failed(action, "the binding's scope no longer exists, so it grants nothing")
        changed_guid = str(binding.guid)
        old_role = binding.role
        in_play = set(old_role.permissions or ())
        if binding.user_id is not None:
            reach(binding.user_id, "direct")
        else:
            groups.append(binding.group_external_id)
            for user_id in Member.objects.filter(
                scope_kind=Member.ScopeKind.ORG,
                scope_id=org_id,
                is_active=True,
                idp_groups__contains=[binding.group_external_id],
            ).values_list("user_id", flat=True):
                reach(user_id, f"group {binding.group_external_id}")
        if action == "CHANGE":
            expiry_given = draft.expires_at is not _unset()
            if draft.role_id is None and not expiry_given:
                return _failed(action, "give roleId or expiresAt")
            new_role = old_role
            if draft.role_id is not None:
                new_role = visible_role(org_id, draft.role_id)
                if new_role is None:
                    return _failed(action, "role not found")
            expires_at = draft.expires_at if expiry_given else binding.expires_at
            if expiry_given and expires_at is not None and expires_at <= timezone.now():
                return _failed(action, "expiresAt must be in the future")
            new_live = expires_at is None or expires_at > timezone.now()
            in_play |= set(new_role.permissions or ())
            if expiry_given:
                notes.append(
                    "The binding becomes permanent."
                    if expires_at is None
                    else f"The binding ends at {expires_at.isoformat()}."
                )

    return _answer(
        tenant,
        org_id,
        action=action,
        target=target,
        people=people,
        groups=groups,
        in_play=in_play,
        changed_guid=changed_guid,
        new_role=new_role if new_live else None,
        old_role=old_role,
        notes=notes,
        limit=limit,
    )


def _unset():
    import strawberry

    return strawberry.UNSET


def _existing(role, target, people: dict[int, _Person], groups: list[str]) -> int:
    from django.db.models import Q

    from astrolift_identity.models import RoleBinding

    direct = [p.user_id for p in people.values() if "direct" in p.through]
    q = Q(pk__in=[])
    if direct:
        q |= Q(user_id__in=direct)
    if groups:
        q |= Q(user__isnull=True, group_external_id__in=groups)
    return RoleBinding.objects.filter(q, role=role, scope_kind=target.kind, scope_id=target.pk).count()


def _row_permissions(row) -> set[str]:
    from astrolift_identity.permission_resolver import SHARE_READ_PERMISSIONS

    perms = set(row.role.permissions or ()) if row.role is not None else set()
    if row.source != "TEAM_SHARE":
        return perms
    if row.access_level in ("deployer", "owner"):
        return perms
    if row.access_level == "viewer":
        return perms & SHARE_READ_PERMISSIONS
    return set()


def _answer(
    tenant,
    org_id,
    *,
    action,
    target,
    people,
    groups,
    in_play,
    changed_guid,
    new_role,
    old_role,
    notes,
    limit,
):
    from django.contrib.auth import get_user_model

    from astrolift_identity.access import access_on
    from astrolift_identity.grants import REFUSAL, grant_ceiling
    from astrolift_identity.idp_groups import group_member_counts
    from astrolift_identity.models import Member
    from astrolift_identity.schema.access_ux import (
        GrantPreviewGroupType,
        GrantPreviewPersonType,
        GrantPreviewSourceType,
        GrantPreviewType,
    )
    from astrolift_identity.schema.queries import _resolve_source_scope_labels, _scope_guids
    from astrolift_identity.schema.types import user_to_type

    rows = [r for r in access_on(org_id, target) if r.role is not None]
    ids = sorted(people)
    stored = dict(
        Member.objects.filter(
            scope_kind=Member.ScopeKind.ORG, scope_id=org_id, is_active=True, user_id__in=ids
        ).values_list("user_id", "idp_groups")
    )
    member_guids = dict(
        Member.objects.filter(scope_kind=Member.ScopeKind.ORG, scope_id=org_id, user_id__in=ids).values_list(
            "user_id", "guid"
        )
    )
    users = {u.pk: u for u in get_user_model().objects.filter(pk__in=ids)}
    labels_for = {(r.scope_kind, r.scope_id) for r in rows} | {(target.kind, target.pk)}
    guids = _scope_guids(labels_for)
    labels = _resolve_source_scope_labels([SimpleNamespace(scope_kind=k, scope_id=i) for k, i in labels_for])

    def mine(row, user_id: int) -> bool:
        if row.user is not None:
            return row.user.pk == user_id
        groups_of = stored.get(user_id) or []
        return bool(row.group_external_id) and row.group_external_id in groups_of

    def source(row, perms: set[str]) -> GrantPreviewSourceType:
        return GrantPreviewSourceType(
            source=row.source,
            binding_id=GUID(row.binding_guid),
            role_slug=row.role.slug,
            role_name=row.role.name,
            scope_kind=row.scope_kind,
            scope_guid=GUID(guids[(row.scope_kind, row.scope_id)])
            if (row.scope_kind, row.scope_id) in guids
            else None,
            source_scope_label=labels.get((row.scope_kind, row.scope_id), ""),
            group_external_id=row.group_external_id or None,
            team_slug=row.team.slug if row.team is not None else None,
            inherited=row.inherited,
            permissions=sorted(perms),
        )

    buckets: dict[str, list[GrantPreviewPersonType]] = {"gaining": [], "losing": [], "unchanged": []}
    counts = {"gaining": 0, "losing": 0, "unchanged": 0}
    for user_id in ids:
        user = users.get(user_id)
        if user is None:
            continue
        person = people[user_id]
        others = [r for r in rows if mine(r, user_id) and r.binding_guid != changed_guid]
        held_elsewhere = set()
        via = []
        for row in others:
            carried = _row_permissions(row) & in_play
            if carried:
                held_elsewhere |= carried
                via.append(source(row, carried))
        if user.is_superuser and user.is_active:
            held_elsewhere = set(in_play)
            via.append(
                GrantPreviewSourceType(
                    source="SUPERUSER",
                    binding_id=None,
                    role_slug=None,
                    role_name=None,
                    scope_kind=None,
                    scope_guid=None,
                    source_scope_label="platform operator",
                    group_external_id=None,
                    team_slug=None,
                    inherited=False,
                    permissions=sorted(in_play),
                )
            )
        changed_before = set()
        if action != "GRANT":
            changed = [r for r in rows if r.binding_guid == changed_guid and mine(r, user_id)]
            changed_before = set(old_role.permissions or ()) if changed else set()
        after_draft = set(new_role.permissions or ()) if new_role is not None else set()
        before = (held_elsewhere | changed_before) & in_play
        after = (held_elsewhere | after_draft) & in_play
        gained, lost = after - before, before - after
        guid = member_guids.get(user_id)
        entry = GrantPreviewPersonType(
            user=user_to_type(user),
            member_id=GUID(str(guid)) if guid else None,
            through=list(dict.fromkeys(person.through)),
            gained=sorted(gained),
            lost=sorted(lost),
            kept=sorted(held_elsewhere & in_play),
            via=via,
        )
        # A change from one role to another can both add and take away, so
        # one person may be in gaining and in losing.
        for bucket in [b for b, hit in (("gaining", gained), ("losing", lost)) if hit] or ["unchanged"]:
            counts[bucket] += 1
            if len(buckets[bucket]) < limit:
                buckets[bucket].append(entry)

    ceiling = grant_ceiling(tenant, scope_kind=target.kind, scope_id=target.pk)
    allowed = ceiling.allows(in_play)
    group_counts = group_member_counts(org_id, groups) if groups else {}
    label = labels.get((target.kind, target.pk), target.kind.lower())
    if groups:
        notes.append("Group members are read from the groups the IdP asserted at each member's last sign-in.")
    notes.append("ABAC policies are not applied; they can still deny what this grants.")
    return GrantPreviewType(
        ok=True,
        errors=[],
        action=action,
        permissions=sorted(in_play),
        scope_kind=target.kind,
        scope_guid=GUID(guids[(target.kind, target.pk)]) if (target.kind, target.pk) in guids else None,
        source_scope_label=label,
        summary=_summary(action, counts, label),
        gaining_count=counts["gaining"],
        losing_count=counts["losing"],
        unchanged_count=counts["unchanged"],
        gaining=buckets["gaining"],
        losing=buckets["losing"],
        unchanged=buckets["unchanged"],
        groups=[
            GrantPreviewGroupType(group_external_id=g, member_count=group_counts.get(g, 0)) for g in groups
        ],
        allowed=allowed,
        refusal=None if allowed else REFUSAL,
        notes=notes,
    )


def _people(n: int) -> str:
    return "1 person" if n == 1 else f"{n} people"


def _summary(action: str, counts: dict[str, int], label: str) -> str:
    parts = []
    if counts["gaining"]:
        parts.append(f"{_people(counts['gaining'])} gain access on {label}")
    if counts["losing"]:
        parts.append(f"{_people(counts['losing'])} lose access on {label}")
    if counts["unchanged"]:
        verb = "already have it" if action == "GRANT" else "keep it through another grant or are unaffected"
        parts.append(f"{_people(counts['unchanged'])} {verb}")
    if not parts:
        return f"Nobody's access on {label} changes."
    return "; ".join(parts) + "."


def _failed(action: str, *errors: str):
    from astrolift_identity.schema.access_ux import GrantPreviewType

    return GrantPreviewType(
        ok=False,
        errors=list(errors),
        action=action,
        permissions=[],
        scope_kind=None,
        scope_guid=None,
        source_scope_label="",
        summary="",
        gaining_count=0,
        losing_count=0,
        unchanged_count=0,
        gaining=[],
        losing=[],
        unchanged=[],
        groups=[],
        allowed=False,
        refusal=None,
        notes=[],
    )
