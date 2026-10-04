"""Reviewed direct TEAM attachment and grants, with fresh scoped admission.

An attachment is not an access decision. Removing it cannot revoke ORG,
other-scope, group or IdP authority. Receipts store only public result metadata.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from contextlib import contextmanager
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from astrolift_graphql import GUID
from astrolift_identity.abac import RequestAttributes, request_attributes
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.grants import grant_ceiling, require_grantable
from astrolift_identity.idp_groups import member_groups
from astrolift_identity.models import (
    ApiToken,
    AstroliftSession,
    GroupRoleMapping,
    Member,
    Organization,
    Role,
    RoleBinding,
    ScimGroup,
    Team,
    TeamMembershipAction,
)
from astrolift_identity.step_up import check_elevation
from astrolift_identity.team_membership_types import (
    TeamAccessSourceType,
    TeamMembershipChangeKind,
    TeamMembershipPersonType,
    TeamMembershipReviewType,
    TeamMembershipRoleType,
    TeamMembershipTeamType,
    TeamMembershipType,
)
from astrolift_identity.visibility import visible_identity_teams
from core import permissions as permission_state
from core.mutations import ErrorCode
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    check_permission,
    is_platform_operator,
)
from core.tenancy import get_current_tenant

READ = Permission.TEAM_READ
WRITE = Permission.TEAM_MANAGE_MEMBERS


class MembershipRefusal(Exception):
    def __init__(self, code: ErrorCode, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def refuse(message="membership target is unavailable", code=ErrorCode.NOT_FOUND):
    raise MembershipRefusal(code, message)


def _guid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        refuse("invalid public identifier", ErrorCode.VALIDATION)


@contextmanager
def admitted(info, *, locked=False):
    """Refresh persisted authentication before RBAC/ABAC, including after waits.

    The first tracked browser request may not yet have its sidecar. Its live
    Django session is still required. A present revoked sidecar never passes.
    No caller-supplied authentication flag or synthetic session bag is proof.
    """
    tenant = get_current_tenant()
    request = getattr(getattr(info, "context", None), "request", None)
    if (
        tenant is None
        or request is None
        or tenant.actor_user_id is None
        or not getattr(getattr(request, "user", None), "is_authenticated", False)
        or getattr(request.user, "pk", None) != tenant.actor_user_id
    ):
        refuse("authentication required", ErrorCode.PERMISSION_DENIED)
    users = get_user_model().objects
    tokens = ApiToken.objects
    memberships = Member.objects
    if locked:
        users, tokens, memberships = (
            users.select_for_update(),
            tokens.select_for_update(),
            memberships.select_for_update(),
        )
    actor = users.filter(pk=tenant.actor_user_id, is_active=True).first()
    organization = Organization.objects.filter(pk=tenant.organization_id).first()
    if actor is None or organization is None:
        refuse("actor or organization is unavailable", ErrorCode.PERMISSION_DENIED)
    credential = get_current_api_token()
    request_credential = getattr(request, "_api_token", None)
    if getattr(request_credential, "pk", None) != getattr(credential, "pk", None):
        refuse("credential context does not match authentication", ErrorCode.PERMISSION_DENIED)
    token_state = None
    original_session = getattr(request, "session", None)
    if credential is not None:
        fresh = (
            tokens.filter(
                pk=credential.pk,
                user=actor,
                organization=organization,
                is_revoked=False,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .first()
        )
        if fresh is None or (
            fresh.team_id is not None
            and not Team.objects.filter(pk=fresh.team_id, organization=organization).exists()
        ):
            refuse("credential is no longer valid", ErrorCode.PERMISSION_DENIED)
        token_state = set_current_api_token(fresh)
    else:
        key = getattr(original_session, "session_key", None)
        if locked:
            # Preserve user-before-session lock order and pin both authorities
            # before the shared helper re-reads actual Django authentication.
            list(Session.objects.select_for_update().filter(session_key=key))
            list(AstroliftSession.all_objects.select_for_update().filter(session_key=key))
        from core.current_session import fresh_authenticated_session

        request.session = fresh_authenticated_session(request, actor_user_id=actor.pk, permission=WRITE)
    try:
        if not memberships.filter(
            user=actor, scope_kind="ORG", scope_id=organization.pk, is_active=True
        ).exists():
            if credential is not None or not is_platform_operator(actor):
                refuse("active organization membership required", ErrorCode.PERMISSION_DENIED)
        memo = permission_state._scopes_memo.set({})
        with request_attributes(
            RequestAttributes(
                actor_user_id=actor.pk, request=request, client_ip=request.META.get("REMOTE_ADDR")
            )
        ):
            try:
                yield actor, organization
            finally:
                permission_state._scopes_memo.reset(memo)
    finally:
        if token_state is not None:
            reset_current_api_token(token_state)
        if credential is None:
            request.session = original_session


def scope(team):
    return PermissionScope(kind=ScopeKind.TEAM, id=team.pk)


def team_target(team_id=None, *, slug=None):
    tenant = get_current_tenant()
    query = Team.objects.filter(
        organization_id=tenant.organization_id if tenant else None, organization__deleted_at__isnull=True
    )
    team = (
        query.filter(guid=_guid(team_id)).first() if team_id is not None else query.filter(slug=slug).first()
    )
    if team is None:
        refuse()
    return team


def team_gate(team, permission=READ):
    token = get_current_api_token()
    if token is not None and token.team_id is not None and token.team_id != team.pk:
        refuse("team is outside the credential ceiling", ErrorCode.PERMISSION_DENIED)
    check_permission(permission, scope=scope(team))


def person_target(org_member_id):
    tenant = get_current_tenant()
    person = (
        Member.objects.select_related("user")
        .filter(
            guid=_guid(org_member_id),
            scope_kind="ORG",
            scope_id=tenant.organization_id if tenant else None,
        )
        .first()
    )
    if person is None:
        refuse()
    return person


def can_manage(team):
    try:
        team_gate(team, WRITE)
        return True
    except (PermissionDenied, MembershipRefusal):
        return False


def team_type(team):
    return TeamMembershipTeamType(
        id=GUID(str(team.guid)),
        version=team.version,
        name=team.name,
        slug=team.slug,
        can_manage_members=can_manage(team),
    )


def person_type(person):
    return TeamMembershipPersonType(
        org_member_id=GUID(str(person.guid)),
        name=person.user.get_full_name() or person.user.username,
        email=person.user.email,
        active=person.is_active and person.user.is_active,
    )


def direct_member(team, person):
    return Member.objects.filter(user_id=person.user_id, scope_kind="TEAM", scope_id=team.pk).first()


def direct_bindings(team, person):
    return (
        RoleBinding.objects.filter(user_id=person.user_id, scope_kind="TEAM", scope_id=team.pk)
        .select_related("role")
        .order_by("guid")
    )


def _source_link(role, group_id=""):
    tenant = get_current_tenant()
    credential = get_current_api_token()
    if credential is not None and credential.team_id is not None:
        return None
    org_scope = PermissionScope(kind=ScopeKind.ORG, id=tenant.organization_id)
    if group_id:
        try:
            check_permission(Permission.ORG_MANAGE_MEMBERS, scope=org_scope)
            return "/administration/access/people/" + quote("group:" + group_id, safe="")
        except PermissionDenied:
            return None
    try:
        check_permission(Permission.ORG_READ, scope=org_scope)
        return "/administration/permissions/roles/" + str(role.guid)
    except PermissionDenied:
        return None


def access_sources(team, person):
    """Role provenance, including expired direct grants; not ABAC approval."""
    groups = member_groups(person.user_id, team.organization_id)
    principal = Q(user_id=person.user_id)
    if groups:
        principal |= Q(user__isnull=True, group_external_id__in=groups)
    owners = Q(scope_kind="TEAM", scope_id=team.pk) | Q(
        scope_kind="ORG", scope_id=team.organization_id, inherits=True
    )
    bindings = (
        RoleBinding.objects.select_related("role")
        .filter(principal, owners)
        .filter(Q(role__organization_id=team.organization_id) | Q(role__organization__isnull=True))
    )
    mappings = (
        GroupRoleMapping.objects.select_related("role")
        .filter(
            Q(role__organization_id=team.organization_id) | Q(role__organization__isnull=True),
            organization_id=team.organization_id,
            group_external_id__in=groups,
        )
        .filter(Q(scope_kind="TEAM", scope_id=team.pk) | Q(scope_kind="ORG", scope_id=team.organization_id))
        if groups
        else []
    )
    return sources_to_types(team, bindings.order_by("guid"), mappings, source_link=_source_link)


def sources_to_types(team, bindings, mappings, *, source_link):
    """Pure metadata projection shared by single review and paged reads."""
    result = []
    for binding in bindings:
        if binding.role.organization_id not in (None, team.organization_id):
            continue
        if binding.scope_kind == "ORG" and READ.value not in (binding.role.permissions or []):
            continue
        direct = binding.user_id is not None and binding.scope_kind == "TEAM"
        source = "DIRECT" if direct else "INHERITED" if binding.user_id is not None else "IDP_GROUP"
        result.append(
            TeamAccessSourceType(
                id=GUID(str(binding.guid)),
                source=source,
                scope_kind=binding.scope_kind,
                role_name=binding.role.name,
                role_id=GUID(str(binding.role.guid)),
                source_href=source_link(binding.role, binding.group_external_id),
                expires_at=binding.expires_at,
                expired=binding.role.deleted_at is not None
                or (binding.expires_at is not None and binding.expires_at <= timezone.now()),
                removable=direct,
            )
        )
    for mapping in mappings:
        if mapping.role.organization_id not in (None, team.organization_id):
            continue
        if mapping.scope_kind == "ORG" and READ.value not in (mapping.role.permissions or []):
            continue
        result.append(
            TeamAccessSourceType(
                id=GUID(str(mapping.guid)),
                source="IDP_MAPPING",
                scope_kind=mapping.scope_kind,
                role_name=mapping.role.name,
                role_id=GUID(str(mapping.role.guid)),
                source_href=source_link(mapping.role, mapping.group_external_id),
                expires_at=None,
                expired=mapping.role.deleted_at is not None,
                removable=False,
            )
        )
    return result


def membership_type(team, person):
    member = direct_member(team, person)
    bindings = list(direct_bindings(team, person))
    manageable = can_manage(team)
    if manageable:
        ceiling = grant_ceiling(get_current_tenant(), scope_kind="TEAM", scope_id=team.pk)
        manageable = all(
            binding.role.organization_id in (None, team.organization_id)
            and ceiling.allows(binding.role.permissions)
            for binding in bindings
        )
    return TeamMembershipType(
        team=team_type(team),
        person=person_type(person),
        team_member_id=GUID(str(member.guid)) if member else None,
        lifecycle=member.lifecycle if member else None,
        sources=access_sources(team, person),
        can_remove=manageable and (member is not None or bool(bindings)),
    )


def _group_members(organization_id, group_ids):
    """Batch SCIM authorities, including retired identifiers and tombstones."""
    requested = set(group_ids)
    authorities = {}
    retired = set()
    for row in ScimGroup.all_objects.filter(organization_id=organization_id).values(
        "pk", "external_id", "guid", "deleted_at", "retired_external_ids"
    ):
        identity = row["external_id"] or str(row["guid"])
        authorities.setdefault(identity, []).append(row)
        retired.update(row["retired_external_ids"] or [])
    live_ids = set()
    unmanaged = []
    for group_id in requested:
        if group_id in authorities:
            live_ids.update(row["pk"] for row in authorities[group_id] if row["deleted_at"] is None)
        elif group_id not in retired:
            unmanaged.append(group_id)
    predicate = Q(scim_groups__pk__in=live_ids)
    for group_id in unmanaged:
        predicate |= Q(idp_groups__contains=[group_id])
    return predicate


def roster(team):
    """Bounded SQL pagination over ORG identities, including derived sources."""
    direct_users = Member.objects.filter(scope_kind="TEAM", scope_id=team.pk).values("user_id")
    bindings = RoleBinding.objects.filter(role__deleted_at__isnull=True).filter(
        Q(scope_kind="TEAM", scope_id=team.pk)
        | Q(
            scope_kind="ORG",
            scope_id=team.organization_id,
            inherits=True,
            role__permissions__contains=[READ.value],
        )
    )
    mappings = GroupRoleMapping.objects.filter(
        organization_id=team.organization_id, role__deleted_at__isnull=True
    ).filter(
        Q(scope_kind="TEAM", scope_id=team.pk)
        | Q(scope_kind="ORG", scope_id=team.organization_id, role__permissions__contains=[READ.value])
    )
    # Direct expired bindings remain reviewable. Derived expired grants do not
    # currently confer membership/access and must not manufacture roster rows.
    live = bindings.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    mappings = mappings.filter(
        Q(role__organization_id=team.organization_id) | Q(role__organization__isnull=True)
    )
    live = live.filter(Q(role__organization_id=team.organization_id) | Q(role__organization__isnull=True))
    groups = list(live.filter(user__isnull=True).values_list("group_external_id", flat=True))
    groups.extend(mappings.values_list("group_external_id", flat=True))
    return (
        Member.objects.select_related("user")
        .filter(scope_kind="ORG", scope_id=team.organization_id)
        .filter(
            Q(user_id__in=direct_users)
            | Q(user_id__in=bindings.filter(user__isnull=False, scope_kind="TEAM").values("user_id"))
            | Q(user_id__in=live.filter(user__isnull=False, scope_kind="ORG").values("user_id"))
            | (Q(is_active=True, user__is_active=True) & _group_members(team.organization_id, groups))
        )
        .distinct()
    )


def people_search(qs, search):
    term = (search or "").strip()[:200]
    return (
        qs.filter(
            Q(user__username__icontains=term)
            | Q(user__email__icontains=term)
            | Q(user__first_name__icontains=term)
            | Q(user__last_name__icontains=term)
        )
        if term
        else qs
    )


def person_teams(person):
    teams = visible_identity_teams(Team.objects.filter(organization_id=person.scope_id), READ)
    member_teams = Member.objects.filter(user_id=person.user_id, scope_kind="TEAM").values("scope_id")
    bindings = RoleBinding.objects.filter(user_id=person.user_id, role__deleted_at__isnull=True)
    group_ids = member_groups(person.user_id, person.scope_id)
    group_bindings = RoleBinding.objects.filter(
        user__isnull=True, group_external_id__in=group_ids, role__deleted_at__isnull=True
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    mappings = GroupRoleMapping.objects.filter(
        organization_id=person.scope_id, group_external_id__in=group_ids, role__deleted_at__isnull=True
    )
    group_bindings = group_bindings.filter(
        Q(role__organization_id=person.scope_id) | Q(role__organization__isnull=True)
    )
    mappings = mappings.filter(Q(role__organization_id=person.scope_id) | Q(role__organization__isnull=True))
    inherited = (
        bindings.filter(
            scope_kind="ORG",
            scope_id=person.scope_id,
            inherits=True,
            role__permissions__contains=[READ.value],
        )
        .filter(Q(role__organization_id=person.scope_id) | Q(role__organization__isnull=True))
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .exists()
    )
    inherited |= group_bindings.filter(
        scope_kind="ORG", scope_id=person.scope_id, inherits=True, role__permissions__contains=[READ.value]
    ).exists()
    inherited |= mappings.filter(
        scope_kind="ORG", scope_id=person.scope_id, role__permissions__contains=[READ.value]
    ).exists()
    if inherited:
        return teams
    return teams.filter(
        Q(pk__in=member_teams)
        | Q(pk__in=bindings.filter(scope_kind="TEAM").values("scope_id"))
        | Q(pk__in=group_bindings.filter(scope_kind="TEAM").values("scope_id"))
        | Q(pk__in=mappings.filter(scope_kind="TEAM").values("scope_id"))
    )


def eligible_roles(team):
    """SQL ceiling projection for bounded role pickers (not action approval)."""
    ceiling = grant_ceiling(get_current_tenant(), scope_kind="TEAM", scope_id=team.pk)
    roles = Role.objects.filter(
        Q(organization_id=team.organization_id) | Q(organization__isnull=True), scope_level="TEAM"
    )
    return roles if ceiling.unrestricted else roles.filter(permissions__contained_by=sorted(ceiling.held))


def _metadata(row):
    if row is None:
        return None
    metadata = [str(row.guid), row.version]
    if isinstance(row, Member):
        metadata.extend([row.user_id, row.scope_kind, row.scope_id, row.is_active, row.lifecycle])
    return metadata


def _hmac(namespace, value):
    return salted_hmac(
        namespace, json.dumps(value, sort_keys=True, separators=(",", ":")), algorithm="sha256"
    ).hexdigest()


def reviewed_source(team, person, kind, role=None):
    """Public keyed metadata receipt; no unhashed profile/auth snapshots."""
    bindings = list(direct_bindings(team, person))
    return _hmac(
        "team.membership.review.v1",
        {
            "org": _metadata(Organization.objects.get(pk=team.organization_id)),
            "team": _metadata(team),
            "person": _metadata(person),
            "user": [person.user_id, person.user.is_active],
            "attachment": _metadata(direct_member(team, person)),
            "kind": kind.value,
            "role": [_metadata(role), role.permissions] if role else None,
            "bindings": [
                [
                    _metadata(b),
                    _metadata(b.role),
                    b.role.permissions,
                    b.expires_at.isoformat() if b.expires_at else None,
                ]
                for b in bindings
            ],
        },
    )


def review(team, person, kind, role_id=None):
    team_gate(team, WRITE)
    choices = eligible_roles(team)
    role = choices.filter(guid=_guid(role_id)).first() if role_id else None
    roles = [role] if role is not None else list(choices.order_by("name", "guid")[:25])
    if kind == TeamMembershipChangeKind.ADD:
        if not person.is_active or not person.user.is_active:
            refuse("only active organization members can be added", ErrorCode.PRECONDITION)
        target = direct_member(team, person)
        if target is not None and not target.is_active:
            refuse("inactive team attachment requires lifecycle review", ErrorCode.PRECONDITION)
        if role_id is not None and role is None:
            refuse("role is not grantable at this team", ErrorCode.PERMISSION_DENIED)
    elif role_id is not None:
        refuse("removal does not accept a role", ErrorCode.VALIDATION)
    member = membership_type(team, person)
    if kind == TeamMembershipChangeKind.REMOVE and not member.can_remove:
        refuse("direct membership or grants are not manageable", ErrorCode.PERMISSION_DENIED)
    return TeamMembershipReviewType(
        kind=kind,
        expected_source=reviewed_source(team, person, kind, role),
        membership=member,
        roles=[
            TeamMembershipRoleType(
                id=GUID(str(r.guid)), version=r.version, name=r.name, permissions=r.permissions
            )
            for r in roles
        ],
        remaining_sources=[s for s in member.sources if not s.removable],
    )


def _request_digest(input, credential, session_key):
    return _hmac(
        "team.membership.request.v1",
        {
            "request": str(input.request_id),
            "kind": input.kind.value,
            "team": str(input.team_id),
            "person": str(input.org_member_id),
            "role": str(input.role_id) if input.role_id else None,
            "source": input.expected_source,
            "credential": credential.pk if credential else None,
            "credential_scopes": sorted(credential.scopes or []) if credential else None,
            "credential_team": credential.team_id if credential else None,
            "session": session_key if credential is None else None,
        },
    )


def change(info, input):
    request_id = _guid(input.request_id)
    session_key = getattr(getattr(info.context.request, "session", None), "session_key", None)
    tenant = get_current_tenant()
    with transaction.atomic():
        # Match existing organization owner/privacy serialization, then owner,
        # users, attachments/roles/grants, finally fresh credential/session.
        org = (
            Organization.objects.select_for_update()
            .filter(pk=tenant.organization_id if tenant else None)
            .first()
        )
        if org is None:
            refuse()
        team = Team.objects.select_for_update().filter(guid=_guid(input.team_id), organization=org).first()
        person = person_target(input.org_member_id)
        if team is None:
            refuse()
        list(
            get_user_model()
            .objects.select_for_update()
            .filter(pk__in=[tenant.actor_user_id, person.user_id])
            .order_by("pk")
        )
        list(
            Member.objects.select_for_update()
            .filter(
                Q(scope_kind="ORG", scope_id=org.pk, user_id__in=[tenant.actor_user_id, person.user_id])
                | Q(scope_kind="TEAM", scope_id=team.pk, user_id__in=[tenant.actor_user_id, person.user_id])
            )
            .order_by("pk")
        )
        owners = Q(scope_kind="ORG", scope_id=org.pk) | Q(scope_kind="TEAM", scope_id=team.pk)
        bindings = list(RoleBinding.objects.select_for_update().filter(owners).order_by("pk"))
        mappings = list(
            GroupRoleMapping.objects.select_for_update().filter(owners, organization=org).order_by("pk")
        )
        role_ids = {b.role_id for b in bindings} | {m.role_id for m in mappings}
        if input.role_id:
            role_ids.update(Role.objects.filter(guid=_guid(input.role_id)).values_list("pk", flat=True))
        list(Role.all_objects.select_for_update().filter(pk__in=role_ids).order_by("pk"))
        with admitted(info, locked=True) as (actor, _organization):
            current_person = person_target(input.org_member_id)
            if current_person.user_id != person.user_id:
                refuse("reviewed person identity changed", ErrorCode.CONFLICT)
            person = current_person
            team_gate(team, WRITE)
            deny = check_elevation(info, action_label="team.membership.change")
            if deny is not None:
                return deny
            current_credential = get_current_api_token()
            digest = _request_digest(input, current_credential, session_key)
            receipt = TeamMembershipAction.objects.filter(
                organization=org, actor=actor, request_id=request_id
            ).first()
            if receipt is not None:
                if receipt.subject_id != person.user_id:
                    refuse("reviewed person identity changed", ErrorCode.CONFLICT)
                if not constant_time_compare(receipt.request_digest, digest):
                    refuse("request identifier belongs to a different reviewed action", ErrorCode.CONFLICT)
                return receipt, True
            person = person_target(input.org_member_id)
            reviewed = review(team, person, input.kind, input.role_id)
            if not constant_time_compare(reviewed.expected_source, input.expected_source):
                refuse("membership changed; review the current grants again", ErrorCode.VERSION_MISMATCH)
            target = direct_member(team, person)
            removed = []
            if input.kind == TeamMembershipChangeKind.ADD:
                if input.role_id is None:
                    refuse("select a team role", ErrorCode.VALIDATION)
                role = Role.objects.get(guid=_guid(input.role_id))
                require_grantable(role.permissions, scope_kind="TEAM", scope_id=team.pk, gate=WRITE)
                if target is None:
                    target = Member.objects.create(
                        user_id=person.user_id,
                        scope_kind="TEAM",
                        scope_id=team.pk,
                        joined_at=timezone.now(),
                        created_by=actor,
                    )
                binding, created = RoleBinding.objects.get_or_create(
                    user_id=person.user_id,
                    role=role,
                    scope_kind="TEAM",
                    scope_id=team.pk,
                    defaults={"granted_by": actor, "created_by": actor},
                )
                if not created and binding.expires_at is not None:
                    refuse("existing timed grant requires explicit grant editing", ErrorCode.PRECONDITION)
            else:
                for binding in direct_bindings(team, person):
                    require_grantable(
                        binding.role.permissions, scope_kind="TEAM", scope_id=team.pk, gate=WRITE
                    )
                    removed.append(str(binding.guid))
                    binding.soft_delete(by=actor)
                if target is not None:
                    target.soft_delete(by=actor)
            result = {
                "teamId": str(team.guid),
                "orgMemberId": str(person.guid),
                "teamMemberId": str(target.guid) if target else None,
                "removedBindingIds": removed,
                "remainingSources": [dataclasses.asdict(s) for s in reviewed.remaining_sources],
            }
            # JSON datetime serialization is explicit, no arbitrary model state.
            for source in result["remainingSources"]:
                source["id"] = str(source["id"])
                source["role_id"] = str(source["role_id"])
                source["expires_at"] = source["expires_at"].isoformat() if source["expires_at"] else None
            receipt = TeamMembershipAction.objects.create(
                organization=org,
                actor=actor,
                subject=person.user,
                request_id=request_id,
                request_digest=digest,
                result=result,
                created_by=actor,
            )
            return receipt, False


def navigation(info):
    from astrolift_identity.team_membership_types import TeamAccessNavigationType
    from core.permissions import check_permission_any_scope

    result = {
        "can_view_teams": False,
        "can_manage_team_members": False,
        "can_view_people": False,
        "can_view_roles": False,
        "can_view_policies": False,
        "can_check_access": False,
    }
    try:
        with admitted(info):
            tenant = get_current_tenant()
            try:
                check_permission_any_scope(READ)
                result["can_view_teams"] = visible_identity_teams(
                    Team.objects.filter(organization_id=tenant.organization_id), READ
                ).exists()
            except PermissionDenied:
                pass
            try:
                check_permission_any_scope(WRITE)
                result["can_manage_team_members"] = visible_identity_teams(
                    Team.objects.filter(organization_id=tenant.organization_id), WRITE
                ).exists()
            except PermissionDenied:
                pass
            credential = get_current_api_token()
            if credential is None or credential.team_id is None:
                org_scope = PermissionScope(kind=ScopeKind.ORG, id=tenant.organization_id)
                try:
                    check_permission(Permission.ORG_MANAGE_MEMBERS, scope=org_scope)
                    result["can_view_people"] = True
                    result["can_check_access"] = True
                except PermissionDenied:
                    pass
                try:
                    check_permission(Permission.ORG_READ, scope=org_scope)
                    result["can_view_roles"] = True
                    result["can_view_policies"] = True
                except PermissionDenied:
                    pass
    except (MembershipRefusal, PermissionDenied):
        pass
    return TeamAccessNavigationType(**result)


def team_access_entitlement(info):
    from core.permissions import ModuleEntitlement

    nav = navigation(info)
    can_view = nav.can_view_teams
    can_manage_members = nav.can_manage_team_members
    return ModuleEntitlement(
        key="team_access",
        can_view=can_view,
        can_create=False,
        can_manage=can_manage_members,
        can_run=False,
        enabled=True,
    )
