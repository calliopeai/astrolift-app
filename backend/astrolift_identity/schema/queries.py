"""
Identity queries.

Field names are prefixed with ``astrolift`` so they don't shadow (or
get shadowed by) the legacy ``organization`` app's queries when both
classes merge into the root schema via multiple inheritance.

Each resolver is decorated with ``@require_permission(...)`` first and
``@tenant_scoped(...)`` second so the tenant context is resolved before
the permission check.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_identity.models import (
    ApiToken,
    IdentityProvider,
    Member,
    Organization,
    Policy,
    Project,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.types import (
    ApiTokenType,
    IdentityProviderType,
    MemberType,
    OrganizationType,
    PolicyType,
    ProjectType,
    RoleBindingType,
    RoleType,
    TeamType,
    api_token_to_type,
    identity_provider_to_type,
    member_to_type,
    organization_to_type,
    policy_to_type,
    project_to_type,
    role_binding_to_type,
    role_to_type,
    team_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


@strawberry.type
class IdentityQuery:
    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_organization(self, info: Info, slug: str) -> OrganizationType | None:
        org = Organization.objects.filter(slug=slug).first()
        if org is None:
            return None
        return organization_to_type(org)

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_organizations(self, info: Info) -> list[OrganizationType]:
        return [organization_to_type(o) for o in Organization.objects.all()[:100]]

    @strawberry.field
    @require_permission(Permission.TEAM_READ)
    @tenant_scoped()
    def astrolift_teams(self, info: Info) -> list[TeamType]:
        return [team_to_type(t) for t in Team.objects.select_related("organization")[:200]]

    @strawberry.field
    @require_permission(Permission.PROJECT_READ)
    @tenant_scoped()
    def astrolift_projects(self, info: Info) -> list[ProjectType]:
        qs = Project.objects.select_related("organization", "team")[:200]
        return [project_to_type(p) for p in qs]

    # ---- RBAC queries ------------------------------------------------

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_members(self, info: Info) -> list[MemberType]:
        qs = Member.objects.select_related("user").order_by("-created_at")[:500]
        return [member_to_type(m) for m in qs]

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_roles(self, info: Info) -> list[RoleType]:
        qs = Role.objects.order_by("scope_level", "slug")[:200]
        return [role_to_type(r) for r in qs]

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_role_bindings(self, info: Info) -> list[RoleBindingType]:
        qs = (
            RoleBinding.objects.select_related("user", "role")
            .order_by("-granted_at")[:500]
        )
        return [role_binding_to_type(rb) for rb in qs]

    @strawberry.field
    @require_permission(Permission.API_TOKEN_CREATE)
    @tenant_scoped()
    def astrolift_api_tokens(self, info: Info) -> list[ApiTokenType]:
        qs = (
            ApiToken.objects.select_related("user", "team")
            .order_by("-created_at")[:200]
        )
        return [api_token_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_policies(self, info: Info) -> list[PolicyType]:
        qs = Policy.objects.order_by("scope_level", "slug")[:200]
        return [policy_to_type(p) for p in qs]

    # ---- Identity providers ------------------------------------------

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_identity_providers(self, info: Info) -> list[IdentityProviderType]:
        qs = (
            IdentityProvider.objects.select_related("organization")
            .order_by("-is_default", "kind")[:50]
        )
        active_id = _active_idp_pk()
        return [
            identity_provider_to_type(idp, is_active=(idp.pk == active_id)) for idp in qs
        ]

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_permissions(self, info: Info) -> list[str]:
        """Effective Astrolift permission slugs for the current viewer.

        This is the read-side mirror of the resolver chain used by
        ``@require_permission`` decorators. The UI calls this once on
        layout mount to know which nav items to show, which destructive
        actions to render, and which pages to fail-closed before even
        firing the page query.

        Intentionally not gated by ``@require_permission`` — every
        signed-in user is allowed to know what *they* can do. We do
        require a valid tenant context (organization scoped via
        ``@tenant_scoped``), so anonymous callers get an empty list
        instead of leaking the slug catalog.
        """
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []

        # Walk the user's RoleBindings in the current org and union
        # the permission slugs each role grants. Mirrors the logic in
        # astrolift_identity.permission_resolver.resolve(), but returns
        # the full set instead of checking a single permission.
        from django.utils import timezone

        now = timezone.now()
        candidate_scopes: list[tuple[str, int]] = []
        if tenant.project_id is not None:
            candidate_scopes.append(("PROJECT", tenant.project_id))
        if tenant.team_id is not None:
            candidate_scopes.append(("TEAM", tenant.team_id))
        if tenant.organization_id is not None:
            candidate_scopes.append(("ORG", tenant.organization_id))
        if not candidate_scopes:
            return []

        scope_kinds = {k for k, _ in candidate_scopes}
        scope_ids_by_kind: dict[str, set[int]] = {}
        for k, sid in candidate_scopes:
            scope_ids_by_kind.setdefault(k, set()).add(sid)

        bindings = (
            RoleBinding.objects.select_related("role")
            .filter(user_id=tenant.actor_user_id, scope_kind__in=scope_kinds)
        )
        effective: set[str] = set()
        for binding in bindings:
            if binding.expires_at is not None and binding.expires_at <= now:
                continue
            ids = scope_ids_by_kind.get(binding.scope_kind, set())
            if binding.scope_id not in ids:
                continue
            for slug in binding.role.permissions or ():
                effective.add(slug)

        return sorted(effective)

    @strawberry.field
    @tenant_scoped()
    def astrolift_active_identity_provider(
        self, info: Info
    ) -> IdentityProviderType | None:
        """The IdP currently bound to the active organization (or None).

        Intentionally not gated by a high permission — the login screen
        needs to call this **before** the user authenticates to know
        which CTA to render. The response is shape-only (no client
        secrets, no config that could leak credentials).
        """
        active_id = _active_idp_pk()
        if active_id is None:
            return None
        idp = (
            IdentityProvider.objects.select_related("organization")
            .filter(pk=active_id)
            .first()
        )
        if idp is None:
            return None
        return identity_provider_to_type(idp, is_active=True)


def _active_idp_pk() -> int | None:
    """The Organization.identity_provider_id for the active tenant context."""
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return None
    return (
        Organization.objects.filter(pk=org_id)
        .values_list("identity_provider_id", flat=True)
        .first()
    )
