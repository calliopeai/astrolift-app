"""Permission analysis: why can this user do this, or why not.

Answers against the permission system Astrolift actually authorizes on
(#1730). Every resolver here used to be written against
``django.contrib.auth`` ``Group`` / ``Permission``:

    perm_obj = Permission.objects.filter(codename=permission).first()
    matching_groups = org_groups.filter(permissions=perm_obj)
    granted = len(matching_names) > 0

Astrolift does not authorize on Django groups. It authorizes on
``RoleBinding`` -> ``Role.permissions``, resolved by
``astrolift_identity.permission_resolver.resolve`` walking ORG -> TEAM ->
PROJECT -> APP. So the old answers were not merely stale, they were about
a different question: ``permission`` was matched against
``auth_permission.codename`` and never against the ``Permission`` StrEnum
slugs (``team.read``, ``app.deploy``) the gates read, and ``granted`` came
out of group membership -- able to report *denied* for a permission the
caller demonstrably holds, and *granted* for one they do not.

This is the built-in tool for answering "why was this denied", and the
first thing an operator reaches for when RBAC misbehaves. Answering
confidently from the wrong table is worse than not shipping it.

The load-bearing property, asserted in the tests: ``granted`` is whatever
``resolve()`` returns for the same tenant context, because it is computed
by calling it. The diagnostic cannot disagree with the gate.
"""

from __future__ import annotations

import strawberry
from django.contrib.auth import get_user_model
from graphql import GraphQLError
from strawberry.types import Info

User = get_user_model()


@strawberry.type
class PermissionEntry:
    """One permission slug the viewer holds, and what granted it."""

    slug: str
    """The catalog slug, e.g. ``team.read``."""

    resource: str
    """Left half of the slug — ``team`` for ``team.read``."""

    action: str
    """Right half of the slug — ``read`` for ``team.read``."""

    granted_via: list[str]
    """Every binding that carries it, as ``<role-slug>@<SCOPE>:<id>``.
    More than one is normal: a permission held at both org and team scope
    is listed twice, which is what an operator needs to see before
    removing one of them."""


@strawberry.type
class PermissionTraceStep:
    """One step in a permission diagnosis trace."""

    check: str
    result: bool
    detail: str


@strawberry.type
class PermissionDiagnosis:
    """Full diagnosis of why a user can or can't perform an action."""

    user_id: strawberry.ID
    username: str
    permission: str
    granted: bool
    is_superuser: bool
    steps: list[PermissionTraceStep]


@strawberry.type
class PermissionDiff:
    """A permission that differs between two users."""

    slug: str
    user_a_has: bool
    user_b_has: bool


@strawberry.type
class PermissionComparison:
    """Side-by-side permission comparison between two users."""

    user_a_username: str
    user_b_username: str
    only_a: list[str]
    only_b: list[str]
    shared: list[str]
    differences: list[PermissionDiff]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _caller_org_id() -> int | None:
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _tenant_for(user, organization_id: int | None):
    """The context the gate would see for ``user`` in this org.

    The diagnosis is always asked inside one organization -- the caller's
    -- so it answers for that one rather than picking some org the target
    happens to belong to.
    """
    from core.tenancy import TenantContext

    return TenantContext(organization_id=organization_id, actor_user_id=user.pk)


def _active_bindings(user, organization_id: int | None) -> list:
    """The user's live bindings, confined to this org."""

    if organization_id is None:
        return []
    from astrolift_identity.permission_resolver import _org_confined_bindings

    return _org_confined_bindings(_tenant_for(user, organization_id))


def _binding_label(binding) -> str:
    label = getattr(binding, "label", None)
    return label if isinstance(label, str) else f"{binding.role.slug}@{binding.scope_kind}:{binding.scope_id}"


def _held_slugs(user, organization_id: int | None) -> dict[str, list[str]]:
    """Slug -> the grants that carry it, for one user in one org.

    Grants are user bindings, group bindings and group mappings (#2157);
    a slug a policy denies everywhere in the org is left out, as the
    resolver leaves it out of "held anywhere" answers.
    """

    from astrolift_identity.permission_resolver import _denied_everywhere
    from core.permissions import Permission

    if getattr(user, "is_superuser", False) and getattr(user, "is_active", True):
        return {p.value: ["django superuser"] for p in Permission}
    out: dict[str, list[str]] = {}
    for binding in _active_bindings(user, organization_id):
        for slug in binding.role.permissions or ():
            out.setdefault(slug, []).append(_binding_label(binding))
    if organization_id is not None and out:
        for slug in _denied_everywhere(_tenant_for(user, organization_id), list(out)):
            out.pop(slug, None)
    return out


def _is_org_manager(organization_id: int | None) -> bool:
    """Whether the caller holds org.manage_members in the active org,
    through the real gate (bearer ceiling and policies included)."""

    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_identity.models import Organization
    from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission

    token = get_current_api_token()
    if organization_id is None or not Organization.objects.filter(pk=organization_id).exists():
        return False
    if token is not None and (token.organization_id != organization_id or token.team_id is not None):
        return False
    try:
        check_permission(
            Permission.ORG_MANAGE_MEMBERS,
            scope=PermissionScope(kind=ScopeKind.ORG, id=organization_id),
        )
    except PermissionDenied:
        return False
    return True


def _is_org_member(user_pk, organization_id: int | None) -> bool:
    from astrolift_identity.models import Member

    if organization_id is None:
        return False
    return Member.objects.filter(
        user_id=user_pk,
        scope_kind="ORG",
        scope_id=organization_id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
        user__is_active=True,
    ).exists()


def _require_analysis_access(info: Info, *target_pks) -> bool:
    """Permission analysis is self-service, superuser, or org.manage_members.

    #537 (tenant-isolation sweep): the prior implementation accepted any
    ``user_id`` from an unauthenticated caller and returned that user's
    full effective permission set, which is a cross-tenant role leak.
    #2157 opens it to org.manage_members, still scoped to the org: every
    target has to be a member of the active org, or the answer is empty,
    so a manager learns nothing about anyone outside it.

    Returns whether every target may be answered for; raises when the
    caller may not ask at all.
    """
    from core.schema.legacy_access import is_operator_with_credential, require_account_access

    caller = require_account_access(info)
    if is_operator_with_credential(caller):
        return True
    if target_pks and all(pk is not None and str(pk) == str(caller.pk) for pk in target_pks):
        return True
    org_id = _caller_org_id()
    if not _is_org_manager(org_id):
        raise GraphQLError(
            "Permission analysis is restricted to self or superuser, or to org.manage_members in the org"
        )
    return all(_is_org_member(pk, org_id) for pk in target_pks)


def _target_scope(scope_type: str | None, scope_id: str | None, organization_id: int | None):
    """``(PermissionScope | None, label, ok)`` for an optional target scope."""

    from astrolift_identity.access import resolve_target
    from core.permissions import PermissionScope, ScopeKind

    if not scope_type and not scope_id:
        return None, "", True
    target = resolve_target(scope_type, scope_id, organization_id)
    if target is None:
        return None, f"{(scope_type or '').upper()} {scope_id} is not a scope of this organization", False
    label = f"{target.kind}:{target.guid}"
    if target.requested_kind == "AGENT":
        label = f"AGENT {scope_id} on {label}"
    return PermissionScope(kind=ScopeKind(target.kind), id=target.pk), label, True


@strawberry.type
class PermissionAnalysisQuery:
    """Permission analysis and debugging queries."""

    @strawberry.field(
        description="List all effective permissions for a user, with the role bindings that grant each one."
    )
    def effective_permissions(self, info: Info, user_id: strawberry.ID) -> list[PermissionEntry]:
        from core.schema.common import GlobalIDUtils

        pk = GlobalIDUtils.get_pk_flexible(user_id)
        if not _require_analysis_access(info, pk):
            return []
        user = User.objects.filter(pk=pk).first()
        if not user:
            return []

        held = _held_slugs(user, _caller_org_id())
        entries: list[PermissionEntry] = []
        for slug in sorted(held):
            resource, _, action = slug.partition(".")
            entries.append(
                PermissionEntry(
                    slug=slug,
                    resource=resource,
                    action=action,
                    granted_via=held[slug],
                )
            )
        return entries

    @strawberry.field(
        description=(
            "Diagnose why a user can or can't perform a specific permission, optionally on a target "
            "scope (scopeType ORG, TEAM, PROJECT, APP or AGENT; scopeId its guid). Self, superuser "
            "or org.manage_members; the user must be a member of the active organization."
        )
    )
    def permission_diagnose(
        self,
        info: Info,
        user_id: strawberry.ID,
        permission: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> PermissionDiagnosis | None:
        from astrolift_identity.permission_resolver import decide
        from core.permissions import Permission
        from core.schema.common import GlobalIDUtils

        pk = GlobalIDUtils.get_pk_flexible(user_id)
        if not _require_analysis_access(info, pk):
            return None
        user = User.objects.filter(pk=pk).first()
        if not user:
            return None

        steps: list[PermissionTraceStep] = []

        def step(check: str, result: bool, detail: str) -> None:
            steps.append(PermissionTraceStep(check=check, result=result, detail=detail))

        def finish(granted: bool, is_superuser: bool = False) -> PermissionDiagnosis:
            return PermissionDiagnosis(
                user_id=str(user.pk),
                username=user.username,
                permission=permission,
                granted=granted,
                is_superuser=is_superuser,
                steps=steps,
            )

        is_active = bool(getattr(user, "is_active", True))
        step("is_active", is_active, f"User {user.username} is {'' if is_active else 'NOT '}active")
        if not is_active:
            return finish(False)

        is_superuser = bool(getattr(user, "is_superuser", False))
        step(
            "is_superuser",
            is_superuser,
            f"User {user.username} is {'' if is_superuser else 'NOT '}a Django superuser"
            + (" — the resolver short-circuits and grants everything" if is_superuser else ""),
        )
        if is_superuser:
            return finish(True, is_superuser=True)

        # A slug nothing declares can never be granted, and is almost
        # always a typo or a Django codename. Say which.
        known = {p.value for p in Permission}
        slug_known = permission in known
        step(
            "permission_is_declared",
            slug_known,
            f"{permission!r} is a declared Astrolift permission"
            if slug_known
            else (
                f"{permission!r} is not in the Astrolift permission catalog. "
                f"Slugs are <resource>.<action>, e.g. 'team.read' — a Django "
                f"codename like 'view_team' is a different system and is never granted here."
            ),
        )

        org_id = _caller_org_id()
        step(
            "has_active_organization",
            org_id is not None,
            f"Active organization id: {org_id}"
            if org_id is not None
            else "No active organization in this request — every scope resolves empty",
        )

        target, target_label, target_ok = _target_scope(scope_type, scope_id, org_id)
        if scope_type or scope_id:
            step(
                "target_scope",
                target_ok,
                f"Checked on {target_label}" if target_ok else target_label,
            )
            if not target_ok:
                return finish(False)

        tenant = _tenant_for(user, org_id)
        from astrolift_identity.permission_resolver import actor_groups

        groups = sorted(actor_groups(tenant)) if org_id is not None else []
        step(
            "idp_groups",
            bool(groups),
            "IdP groups at last sign-in: " + ", ".join(groups)
            if groups
            else "No IdP groups recorded for this user in this org",
        )

        bindings = _active_bindings(user, org_id)
        labels = [_binding_label(b) for b in bindings]
        step(
            "role_bindings_in_this_org",
            bool(bindings),
            ", ".join(labels) if labels else "NONE — this user holds no live role binding in this org",
        )

        carriers = [_binding_label(b) for b in bindings if permission in (b.role.permissions or ())]
        step(
            "bindings_carrying_this_permission",
            bool(carriers),
            ", ".join(carriers) if carriers else f"No binding in this org carries {permission!r}",
        )

        # The answer is the gate's own, not a re-derivation. A diagnostic
        # that can disagree with the thing it explains is worse than none.
        if not slug_known:
            return finish(False)
        decision = decide(tenant, Permission(permission), target)

        if decision.chain and decision.chain[0][0] == "APP":
            share_labels = [s.label for s in decision.shares]
            carrying = [s.label for s in decision.shares if permission in _share_perms(s)]
            step(
                "team_shares",
                bool(carrying),
                (
                    "Through team shares: " + ", ".join(carrying)
                    if carrying
                    else (
                        "Team shares reach this app but none carries it: " + ", ".join(share_labels)
                        if share_labels
                        else "No team share on this app reaches this user"
                    )
                ),
            )

        step(
            "rbac",
            decision.rbac_granted,
            decision.reason if not decision.rbac_granted else f"granted by {decision.reason}",
        )
        if decision.abac is None:
            step("abac_policies", True, "not evaluated: RBAC did not grant it")
        else:
            applied = decision.abac.applied
            detail = (
                decision.abac.reason
                if decision.abac.denied
                else ("; ".join(o.detail for o in applied) if applied else "no policy of this org applies")
            )
            step("abac_policies", not decision.abac.denied, detail)

        reason = decision.reason
        if not decision.granted and carriers and target is None and not decision.rbac_granted:
            # The #1717 shape, and the one most worth naming: she does
            # hold it, at a scope this check did not ask about. An
            # unqualified check resolves against the tenant context, and
            # with no team or project selected that is the org alone --
            # so a TEAM-scoped binding cannot satisfy it. This is the
            # answer to "I have the role, why am I denied".
            reason = (
                f"{reason}. Held at {', '.join(carriers)}, but this check was made at "
                f"organization scope — an unqualified check resolves against the active "
                f"tenant context, which selects no team or project here. A resolver that "
                f"names its target passes that scope; a collection resolver gates on "
                f"holding the permission at any scope and filters its rows."
            )
        step("resolver_verdict", decision.granted, reason)
        return finish(decision.granted)

    @strawberry.field(
        description=(
            "Compare effective permissions between two users, anywhere in the active organization or "
            "on a target scope (scopeType, scopeId). Superuser or org.manage_members; both users must "
            "be members of the active organization."
        )
    )
    def permission_compare(
        self,
        info: Info,
        user_id_a: strawberry.ID,
        user_id_b: strawberry.ID,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> PermissionComparison | None:
        # #537: compare is meaningful across two arbitrary users, so it is
        # never self-service. #2157: an org.manage_members holder may run it
        # for two members of the org.
        from core.schema.legacy_access import is_operator_with_credential, require_account_access

        caller = require_account_access(info)

        from core.schema.common import GlobalIDUtils

        pk_a = GlobalIDUtils.get_pk_flexible(user_id_a)
        pk_b = GlobalIDUtils.get_pk_flexible(user_id_b)
        org_id = _caller_org_id()
        if not is_operator_with_credential(caller):
            if not _is_org_manager(org_id):
                raise GraphQLError("permission_compare is restricted to superuser or org.manage_members")
            if not (_is_org_member(pk_a, org_id) and _is_org_member(pk_b, org_id)):
                return None
        # tenancy: both users were confirmed members of the caller's org
        # above (or the caller is the platform operator); User has no org.
        user_a = User.objects.filter(pk=pk_a).first()
        user_b = User.objects.filter(pk=pk_b).first()
        if not user_a or not user_b:
            return None

        target, _label, target_ok = _target_scope(scope_type, scope_id, org_id)
        if not target_ok:
            return None
        if target is None:
            perms_a = set(_held_slugs(user_a, org_id))
            perms_b = set(_held_slugs(user_b, org_id))
        else:
            from astrolift_identity.permission_resolver import resolve_effective_permissions

            extra = (target.kind.value, target.id)
            perms_a = resolve_effective_permissions(_tenant_for(user_a, org_id), extra_scope=extra)
            perms_b = resolve_effective_permissions(_tenant_for(user_b, org_id), extra_scope=extra)

        only_a = sorted(perms_a - perms_b)
        only_b = sorted(perms_b - perms_a)
        shared = sorted(perms_a & perms_b)
        differences = [
            PermissionDiff(slug=slug, user_a_has=slug in perms_a, user_b_has=slug in perms_b)
            for slug in sorted(perms_a.symmetric_difference(perms_b))
        ]

        return PermissionComparison(
            user_a_username=user_a.username,
            user_b_username=user_b.username,
            only_a=only_a,
            only_b=only_b,
            shared=shared,
            differences=differences,
        )


def _share_perms(share) -> set[str]:
    from astrolift_identity.permission_resolver import _share_permissions

    return _share_permissions(share)
