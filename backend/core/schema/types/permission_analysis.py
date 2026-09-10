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
    return f"{binding.role.slug}@{binding.scope_kind}:{binding.scope_id}"


def _held_slugs(user, organization_id: int | None) -> dict[str, list[str]]:
    """Slug -> the bindings that carry it, for one user in one org."""

    from core.permissions import Permission

    if getattr(user, "is_superuser", False) and getattr(user, "is_active", True):
        return {p.value: ["django superuser"] for p in Permission}
    out: dict[str, list[str]] = {}
    for binding in _active_bindings(user, organization_id):
        for slug in binding.role.permissions or ():
            out.setdefault(slug, []).append(_binding_label(binding))
    return out


def _require_self_or_superuser(info: Info, target_pk: int | str | None) -> None:
    """Permission analysis MUST be either self-service or superuser.

    #537 (tenant-isolation sweep): the prior implementation accepted any
    ``user_id`` from an unauthenticated caller and returned that user's
    full effective permission set, which is a cross-tenant role leak.
    Gate on self-or-superuser at every resolver entry.
    """
    caller = info.context.user
    if not getattr(caller, "is_authenticated", False):
        raise GraphQLError("Authentication required")
    if getattr(caller, "is_superuser", False):
        return
    if target_pk is not None and str(target_pk) == str(caller.pk):
        return
    raise GraphQLError("Permission analysis is restricted to self or superuser")


@strawberry.type
class PermissionAnalysisQuery:
    """Permission analysis and debugging queries."""

    @strawberry.field(
        description="List all effective permissions for a user, with the role bindings that grant each one."
    )
    def effective_permissions(self, info: Info, user_id: strawberry.ID) -> list[PermissionEntry]:
        from core.schema.common import GlobalIDUtils

        pk = GlobalIDUtils.get_pk_flexible(user_id)
        _require_self_or_superuser(info, pk)
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

    @strawberry.field(description="Diagnose why a user can or can't perform a specific permission.")
    def permission_diagnose(
        self, info: Info, user_id: strawberry.ID, permission: str
    ) -> PermissionDiagnosis | None:
        from astrolift_identity.permission_resolver import resolve
        from core.permissions import Permission
        from core.schema.common import GlobalIDUtils

        pk = GlobalIDUtils.get_pk_flexible(user_id)
        _require_self_or_superuser(info, pk)
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
        granted, reason = resolve(_tenant_for(user, org_id), Permission(permission), None)
        if not granted and carriers:
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
        step("resolver_verdict", granted, reason)
        return finish(granted)

    @strawberry.field(description="Compare effective permissions between two users.")
    def permission_compare(
        self, info: Info, user_id_a: strawberry.ID, user_id_b: strawberry.ID
    ) -> PermissionComparison | None:
        # #537: compare is meaningful across two arbitrary users — only
        # superusers may run it.
        caller = info.context.user
        if not getattr(caller, "is_authenticated", False):
            raise GraphQLError("Authentication required")
        if not getattr(caller, "is_superuser", False):
            raise GraphQLError("permission_compare is restricted to superuser")

        from core.schema.common import GlobalIDUtils

        pk_a = GlobalIDUtils.get_pk_flexible(user_id_a)
        pk_b = GlobalIDUtils.get_pk_flexible(user_id_b)
        user_a = User.objects.filter(pk=pk_a).first()
        user_b = User.objects.filter(pk=pk_b).first()
        if not user_a or not user_b:
            return None

        org_id = _caller_org_id()
        perms_a = set(_held_slugs(user_a, org_id))
        perms_b = set(_held_slugs(user_b, org_id))

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
