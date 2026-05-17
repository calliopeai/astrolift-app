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

import datetime as dt

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID
from astrolift_identity.models import (
    ApiToken,
    IdentityProvider,
    Invitation,
    Member,
    Organization,
    OrganizationAllowlistedDomain,
    Policy,
    Project,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.types import (
    ActiveSessionType,
    ApiTokenType,
    ApproverUserType,
    IdentityProviderType,
    InvitationType,
    MemberType,
    MyProfileType,
    NavTreeProjectType,
    NavTreeTeamType,
    NavTreeType,
    OrganizationAllowlistedDomainType,
    OrganizationType,
    PolicyType,
    ProjectType,
    RoleBindingType,
    RoleType,
    SearchableUserType,
    TeamType,
    _resolve_display_name,
    api_token_to_type,
    app_to_summary,
    approver_user_to_type,
    identity_provider_to_type,
    invitation_to_type,
    member_to_type,
    organization_allowlisted_domain_to_type,
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

    @strawberry.field
    @tenant_scoped()
    def astrolift_nav_tree(self, info: Info) -> NavTreeType | None:
        """Hierarchical Org -> Team -> Project -> App view for the sidebar.

        Self-service: no ``@require_permission`` gate -- tenant scope
        already binds the request to a single organization, which is
        the visibility boundary. Mirrors ``astrolift_my_permissions``
        in that respect; every authed user is allowed to see the shape
        of the tenant they belong to. Resource-level reads continue to
        flow through the per-domain permission-gated resolvers.

        Composes three batched queries (teams, projects, apps) and
        groups in Python so the tree degrades to O(N) on the app count
        regardless of how nested the hierarchy gets. Soft-deleted rows
        are excluded by the default managers; ``RegisteredApp`` rows
        with no project (unlikely after recent migrations, but defended
        for legacy data) surface under the team's ``unassigned_apps``
        bucket or the org's ``unassigned_apps`` bucket so they stay
        visible until reassigned.
        """
        from astrolift_registry.models import RegisteredApp
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return None
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return None

        teams = list(
            Team.objects.filter(organization_id=org_id).select_related("organization").order_by("name")
        )
        projects = list(
            Project.objects.filter(organization_id=org_id)
            .select_related("organization", "team")
            .order_by("name")
        )
        apps = list(
            RegisteredApp.objects.filter(organization_id=org_id)
            .only(
                "id",
                "guid",
                "slug",
                "name",
                "provisioning_status",
                "team_id",
                "project_id",
            )
            .order_by("name")
        )

        projects_by_team: dict[int, list] = {}
        for project in projects:
            projects_by_team.setdefault(project.team_id, []).append(project)

        apps_by_project: dict[int | None, list] = {}
        apps_by_team_no_project: dict[int | None, list] = {}
        unassigned_org_apps: list = []
        for app in apps:
            if app.project_id is not None:
                apps_by_project.setdefault(app.project_id, []).append(app)
            elif app.team_id is not None:
                apps_by_team_no_project.setdefault(app.team_id, []).append(app)
            else:
                unassigned_org_apps.append(app)

        team_nodes: list[NavTreeTeamType] = []
        for team in teams:
            project_nodes: list[NavTreeProjectType] = []
            for project in projects_by_team.get(team.id, []):
                project_nodes.append(
                    NavTreeProjectType(
                        project=project_to_type(project),
                        apps=[app_to_summary(a) for a in apps_by_project.get(project.id, [])],
                    )
                )
            team_nodes.append(
                NavTreeTeamType(
                    team=team_to_type(team),
                    projects=project_nodes,
                    unassigned_apps=[app_to_summary(a) for a in apps_by_team_no_project.get(team.id, [])],
                )
            )

        return NavTreeType(
            organization=organization_to_type(org),
            teams=team_nodes,
            unassigned_apps=[app_to_summary(a) for a in unassigned_org_apps],
        )

    # ---- RBAC queries ------------------------------------------------

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_members(self, info: Info, search: str | None = None) -> list[MemberType]:
        """Org-member listing with discoverability affordances.

        ``search`` filters case-insensitively across username, email,
        first_name, and last_name. The filter runs at the DB layer so
        big orgs don't pull 500 rows just to grep them client-side.

        ``last_active_at`` is computed per-member as the most recent
        ``AuditEvent.occurred_at`` where the actor matches this member's
        user, scoped to the current organization. Resolved in one
        aggregate query so the field doesn't fan out N+1 on member count.
        """
        from django.db.models import Max, Q

        from astrolift_operations.models.audit_event import AuditEvent
        from core.tenancy import get_current_tenant

        qs = Member.objects.select_related("user").order_by("-created_at")
        term = (search or "").strip()
        if term:
            qs = qs.filter(
                Q(user__username__icontains=term)
                | Q(user__email__icontains=term)
                | Q(user__first_name__icontains=term)
                | Q(user__last_name__icontains=term)
            )
        members = list(qs[:500])
        if not members:
            return []

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        user_ids = [m.user_id for m in members if m.user_id is not None]
        last_active_by_user_id: dict[int, dt.datetime] = {}
        if user_ids and org_id is not None:
            actor_id_strs = [str(uid) for uid in user_ids]
            ae_rows = (
                AuditEvent.objects.filter(
                    organization_id=org_id,
                    actor_kind="user",
                    actor_id__in=actor_id_strs,
                )
                .values("actor_id")
                .annotate(last_at=Max("occurred_at"))
            )
            for row in ae_rows:
                try:
                    last_active_by_user_id[int(row["actor_id"])] = row["last_at"]
                except (TypeError, ValueError):
                    continue

        return [member_to_type(m, last_active_at=last_active_by_user_id.get(m.user_id)) for m in members]

    @strawberry.field
    @require_permission(Permission.TEAM_READ)
    @tenant_scoped()
    def astrolift_team_members(self, info: Info, team_id: GUID) -> list[MemberType]:
        """Members attached to one team, by team GUID.

        The Member.scope_id column stores the team's integer pk, so a
        consumer that only has the team's GUID can't filter via the
        generic ``astrolift_members`` resolver — this resolver does the
        GUID→pk hop server-side. Used by the team detail page's bulk
        role-assign panel (#416).
        """
        team = Team.objects.filter(guid=str(team_id), deleted_at__isnull=True).first()
        if team is None:
            return []
        qs = (
            Member.objects.select_related("user")
            .filter(
                scope_kind=Member.ScopeKind.TEAM,
                scope_id=team.pk,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")[:500]
        )
        return [member_to_type(m) for m in qs]

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_org_members_for_approval_picker(self, info: Info, org_slug: str) -> list[ApproverUserType]:
        """Active org members shaped for the approval-policy picker (#410).

        Returns the de-duplicated set of users with an ACTIVE
        ``Member(scope_kind=ORG, scope_id=<org>)`` row, projected as
        ``ApproverUserType`` (id + email + display name + avatar URL).
        Reused by the register-app wizard, the per-app Settings page,
        and any later surface that needs to render an org-scoped user
        picker — keeping the projection in one place keeps the picker
        UX consistent.

        Org-scoped: ``orgSlug`` is resolved + cross-tenant-checked
        against the active ``TenantContext.organization_id``. A slug
        belonging to a different org returns an empty list rather than
        leaking that the org exists. Anonymous callers are gated by
        ``@tenant_scoped`` (no tenant -> no result).
        """
        from auth1.models import UserInfo
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        tenant_org_id = tenant.organization_id if tenant else None
        if tenant_org_id is None:
            return []

        org = Organization.objects.filter(slug=org_slug, deleted_at__isnull=True).first()
        if org is None or org.id != tenant_org_id:
            # Cross-tenant or unknown slug — fail closed without leaking
            # whether the slug exists.
            return []

        member_user_ids = list(
            Member.objects.filter(
                scope_kind=Member.ScopeKind.ORG,
                scope_id=org.id,
                deleted_at__isnull=True,
                is_active=True,
            )
            .values_list("user_id", flat=True)
            .distinct()
        )
        if not member_user_ids:
            return []

        from django.contrib.auth import get_user_model

        User = get_user_model()
        users = list(
            User.objects.filter(pk__in=member_user_ids, is_active=True).order_by(
                "first_name", "last_name", "username", "email"
            )
        )
        userinfo_by_user_id: dict[int, object] = {}
        for ui in UserInfo.objects.filter(internal_user_id__in=member_user_ids):
            # A user can have multiple Auth0 UserInfo rows (one per
            # subject claim) — last write wins. The picker only needs a
            # representative avatar, so any is fine.
            userinfo_by_user_id[ui.internal_user_id] = ui

        return [approver_user_to_type(u, userinfo=userinfo_by_user_id.get(u.pk)) for u in users]

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_invitations(self, info: Info, status: str | None = None) -> list[InvitationType]:
        """Org-scoped invitation list. Filter by status (pending /
        accepted / expired / revoked); default surfaces every status
        so the UI can show full history without an extra round-trip.

        Pre-fetches Auth0 ``UserInfo`` rows for every distinct inviter
        in one query so the invitation row can render the inviter's
        avatar URL (#418) without a per-row lookup.
        """
        from auth1.models import UserInfo
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        qs = (
            Invitation.objects.filter(
                scope_kind=Invitation.ScopeKind.ORG,
                scope_id=org_id,
                deleted_at__isnull=True,
            )
            .select_related("role", "invited_by")
            .order_by("-created_at")
        )
        if status:
            qs = qs.filter(status=status)
        rows = list(qs[:500])
        inviter_ids = {r.invited_by_id for r in rows if r.invited_by_id}
        userinfo_by_user_id: dict[int, object] = {}
        if inviter_ids:
            for ui in UserInfo.objects.filter(internal_user_id__in=inviter_ids):
                # A user can have multiple UserInfo rows (one per Auth0
                # subject claim) — last write wins; the picker only
                # needs a representative avatar, so any is fine.
                userinfo_by_user_id[ui.internal_user_id] = ui
        return [invitation_to_type(r, userinfo_by_user_id=userinfo_by_user_id) for r in rows]

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_roles(self, info: Info) -> list[RoleType]:
        qs = Role.objects.order_by("scope_level", "slug")[:200]
        return [role_to_type(r) for r in qs]

    # ---- Invite-flow polish (#418) -------------------------------------

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_searchable_users(self, info: Info, query: str) -> list[SearchableUserType]:
        """De-dupe search the InviteDialog runs before letting the
        operator dispatch ``create_invitation`` (#418).

        Returns up to 10 matches across two sources:

        * Existing active org members (``Member.lifecycle==active``,
          ``is_active=True``, scope=ORG) whose username, email,
          first_name or last_name contains ``query``.
        * Pending invitations at the org scope (``status==pending``,
          ``deleted_at is null``) whose email contains ``query``.

        Both share a single row shape (``match_kind`` discriminates)
        so the FE renders one combobox with a small badge instead of
        two separate sections. Cross-tenant rows are excluded — the
        tenant context is the visibility boundary; a leaked org_id
        from a different tenant would just produce an empty list.

        Empty / whitespace ``query`` returns an empty list (don't dump
        every member into the dropdown — that's what
        ``astrolift_members`` is for).
        """
        from django.contrib.auth import get_user_model
        from django.db.models import Q

        from auth1.models import UserInfo
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []

        needle = (query or "").strip()
        if not needle:
            return []
        # Cap the LIKE input so a runaway operator paste can't blow
        # the planner's pattern budget. 254 = max email length.
        needle = needle[:254]

        User = get_user_model()
        # Phase 1: collect candidate user ids from members of this org.
        # We restrict the user table search to those ids so that an
        # operator can't probe global username uniqueness across orgs
        # by typing fragments (PII leak vector).
        member_user_ids = list(
            Member.objects.filter(
                scope_kind=Member.ScopeKind.ORG,
                scope_id=org_id,
                deleted_at__isnull=True,
                is_active=True,
                lifecycle=Member.Lifecycle.ACTIVE,
            )
            .values_list("user_id", flat=True)
            .distinct()
        )

        rows: list[SearchableUserType] = []
        if member_user_ids:
            matched_users = list(
                User.objects.filter(pk__in=member_user_ids)
                .filter(
                    Q(username__icontains=needle)
                    | Q(email__icontains=needle)
                    | Q(first_name__icontains=needle)
                    | Q(last_name__icontains=needle)
                )
                .order_by("first_name", "last_name", "username", "email")[:10]
            )
            userinfo_by_user_id: dict[int, object] = {}
            if matched_users:
                for ui in UserInfo.objects.filter(internal_user_id__in=[u.pk for u in matched_users]):
                    userinfo_by_user_id[ui.internal_user_id] = ui
            for u in matched_users:
                ui = userinfo_by_user_id.get(u.pk)
                rows.append(
                    SearchableUserType(
                        match_kind="MEMBER",
                        email=u.email or "",
                        display_label=_resolve_display_name(u, userinfo=ui),
                        avatar_url=(getattr(ui, "picture", "") or "") if ui is not None else "",
                        user_id=str(u.pk),
                        invitation_id=None,
                        invitation_status=None,
                        expires_at=None,
                    )
                )

        # Phase 2: pending invitations at this org scope.
        invs = list(
            Invitation.objects.filter(
                scope_kind=Invitation.ScopeKind.ORG,
                scope_id=org_id,
                status=Invitation.Status.PENDING,
                deleted_at__isnull=True,
                email__icontains=needle,
            ).order_by("-created_at")[:10]
        )
        for inv in invs:
            rows.append(
                SearchableUserType(
                    match_kind="INVITATION",
                    email=inv.email,
                    display_label=inv.email,
                    avatar_url="",
                    user_id=None,
                    invitation_id=GUID(str(inv.guid)),
                    invitation_status=inv.status,
                    expires_at=inv.expires_at,
                )
            )

        return rows

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_roles_i_can_grant(self, info: Info) -> list[RoleType]:
        """Roles the active viewer is permitted to grant on invite (#418).

        A role is grantable iff its permission set is a (non-strict)
        subset of the viewer's effective permissions at the active
        org scope. Rationale: an operator who lacks ``app.deploy``
        can't legitimately promote someone else into a role that
        carries it — the deferred grant would just be rejected by
        the resolver chain at use time, and surfacing it in the
        picker is misleading. Django superusers see every role.

        Empty list is a legitimate result and tells the FE to disable
        the picker with an explainer; not the same as
        PERMISSION_DENIED (which still surfaces if the caller lacks
        ``org.manage_members`` entirely).

        System roles + this org's custom roles are both considered.
        Soft-deleted roles are skipped. Bound to the org scope of the
        active tenant; cross-tenant custom roles are excluded.
        """
        from django.contrib.auth import get_user_model
        from django.db.models import Q

        from core.permissions import Permission as _Permission
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        actor_id = tenant.actor_user_id if tenant else None
        if org_id is None or actor_id is None:
            return []

        User = get_user_model()
        is_superuser = User.objects.filter(pk=actor_id, is_superuser=True, is_active=True).exists()

        # System roles have ``organization_id is null``; custom roles
        # are bound to the org. Either is a candidate.
        candidates = list(
            Role.objects.filter(deleted_at__isnull=True)
            .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
            .order_by("scope_level", "slug")
        )

        if is_superuser:
            return [role_to_type(r) for r in candidates]

        # Compute the viewer's effective permission set once; mirrors
        # the logic in ``astrolift_my_permissions`` but kept inline so
        # this resolver is self-contained.
        from django.utils import timezone

        now = timezone.now()
        candidate_scopes: list[tuple[str, int]] = []
        if tenant.project_id is not None:
            candidate_scopes.append(("PROJECT", tenant.project_id))
        if tenant.team_id is not None:
            candidate_scopes.append(("TEAM", tenant.team_id))
        candidate_scopes.append(("ORG", org_id))
        scope_kinds = {k for k, _ in candidate_scopes}
        scope_ids_by_kind: dict[str, set[int]] = {}
        for k, sid in candidate_scopes:
            scope_ids_by_kind.setdefault(k, set()).add(sid)

        bindings = RoleBinding.objects.select_related("role").filter(
            user_id=actor_id, scope_kind__in=scope_kinds, deleted_at__isnull=True
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

        # Reject roles that name a permission not in the catalog so a
        # corrupt role row can never be promoted; a strict subset
        # check on a smaller-than-catalog effective set is meaningless.
        catalog = {p.value for p in _Permission}
        grantable: list[Role] = []
        for r in candidates:
            perms = set(r.permissions or ())
            if not perms.issubset(catalog):
                continue
            if perms.issubset(effective):
                grantable.append(r)
        return [role_to_type(r) for r in grantable]

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_role_bindings(self, info: Info) -> list[RoleBindingType]:
        """Org-wide role bindings with human-readable source-scope labels.

        Each binding's (scope_kind, scope_id) pair is resolved to a
        label like ``"team payments"`` or ``"app web-api"`` so the FE
        can render the role-source tooltip without a parallel
        teams / projects / apps round-trip. Scope rows are loaded in
        one batch per kind to keep this O(scope-kinds) rather than
        O(bindings).
        """
        qs = list(RoleBinding.objects.select_related("user", "role").order_by("-granted_at")[:500])
        labels = _resolve_source_scope_labels(qs)
        return [
            role_binding_to_type(rb, source_scope_label=labels.get((rb.scope_kind, rb.scope_id), ""))
            for rb in qs
        ]

    @strawberry.field
    @require_permission(Permission.API_TOKEN_CREATE)
    @tenant_scoped()
    def astrolift_api_tokens(self, info: Info) -> list[ApiTokenType]:
        qs = ApiToken.objects.select_related("user", "team").order_by("-created_at")[:200]
        return [api_token_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_policies(self, info: Info) -> list[PolicyType]:
        # ``created_by`` / ``updated_by`` are FK columns on the Tracking
        # mixin; ``select_related`` keeps the per-row username lookup
        # inside the same query (no N+1 on the policies table — #466).
        qs = Policy.objects.select_related("created_by", "updated_by").order_by("scope_level", "slug")[:200]
        return [policy_to_type(p) for p in qs]

    # ---- Domain allowlist --------------------------------------------

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_organization_allowlist_domains(self, info: Info) -> list[OrganizationAllowlistedDomainType]:
        """Trusted email domains that auto-join SSO users into this org."""
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        qs = (
            OrganizationAllowlistedDomain.objects.filter(
                organization_id=org_id,
                deleted_at__isnull=True,
            )
            .select_related("default_role")
            .order_by("domain")[:500]
        )
        return [organization_allowlisted_domain_to_type(r) for r in qs]

    # ---- Identity providers ------------------------------------------

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_identity_providers(self, info: Info) -> list[IdentityProviderType]:
        # Prefetch ``last_switched_by`` (FK on the IdP row) so the per-row
        # username lookup folds into the same query — the FE renders the
        # "by <operator>" caption on every active IdP, so a naive lookup
        # would fan out N+1 on the list (#467).
        qs = IdentityProvider.objects.select_related("organization", "last_switched_by").order_by(
            "-is_default", "kind"
        )[:50]
        active_id = _active_idp_pk()
        return [identity_provider_to_type(idp, is_active=(idp.pk == active_id)) for idp in qs]

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_profile(self, info: Info) -> MyProfileType | None:
        """The signed-in viewer's profile + IdP lock policy.

        Returns None for anonymous calls (the @tenant_scoped already
        rejects them, but this guard keeps the resolver safe). Every
        authed user gets their own row regardless of permissions —
        you're always allowed to know what *you* look like."""
        from astrolift_identity.models import Organization
        from astrolift_identity.schema.mutations import (
            _idp_locked_fields,
            _my_profile_payload,
        )
        from astrolift_identity.schema.types import MyProfileType  # noqa: F401
        from core.tenancy import get_current_tenant

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return None
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        org = Organization.objects.filter(pk=org_id).first() if org_id is not None else None
        if org is None:
            return None
        session = getattr(request, "session", None) if request else None
        locked = _idp_locked_fields(viewer, session=session)
        return _my_profile_payload(viewer, org, locked)

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

        Django superusers get every permission slug, mirroring the
        bypass in ``astrolift_identity.permission_resolver.resolve``
        — the bootstrap admin path doesn't need explicit role bindings.
        """
        from django.contrib.auth import get_user_model

        from core.permissions import Permission
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []

        if (
            get_user_model()
            .objects.filter(pk=tenant.actor_user_id, is_superuser=True, is_active=True)
            .exists()
        ):
            return sorted(p.value for p in Permission)

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

        bindings = RoleBinding.objects.select_related("role").filter(
            user_id=tenant.actor_user_id, scope_kind__in=scope_kinds
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
    def astrolift_active_identity_provider(self, info: Info) -> IdentityProviderType | None:
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
            IdentityProvider.objects.select_related("organization", "last_switched_by")
            .filter(pk=active_id)
            .first()
        )
        if idp is None:
            return None
        return identity_provider_to_type(idp, is_active=True)

    @strawberry.field
    @tenant_scoped()
    def astrolift_active_sessions(self, info: Info) -> list[ActiveSessionType]:
        """The signed-in viewer's own active sessions.

        Walks ``django_session`` decoding each row's session_data to
        find the ones bound to this user. Self-only — every authed
        user can list their own sessions; no permission gate.

        v1 surfaces what django_session natively tracks: session_key
        (suffix), expire_date, is_current. IP / UA / created_at /
        last_seen_at land once a SessionMetadata model + middleware
        track them per-request (#289 follow-up).
        """
        from django.contrib.sessions.models import Session
        from django.utils import timezone

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return []

        current_key = getattr(getattr(request, "session", None), "session_key", None)
        viewer_pk = str(viewer.pk)
        now = timezone.now()
        out: list[ActiveSessionType] = []
        for s in Session.objects.filter(expire_date__gt=now):
            try:
                data = s.get_decoded()
            except Exception:
                # Corrupt session row — skip rather than 500 the page.
                continue
            if str(data.get("_auth_user_id", "")) != viewer_pk:
                continue
            # Return only the last 8 chars of the session key as a
            # display-safe identifier. The full key never leaves the
            # cookie jar; logout_all_sessions doesn't need it.
            out.append(
                ActiveSessionType(
                    id=s.session_key[-8:],
                    expires_at=s.expire_date,
                    is_current=(s.session_key == current_key),
                    created_at=None,
                    last_seen_at=None,
                    ip_address=None,
                    user_agent=None,
                )
            )
        return out


def _active_idp_pk() -> int | None:
    """The Organization.identity_provider_id for the active tenant context."""
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return None
    return Organization.objects.filter(pk=org_id).values_list("identity_provider_id", flat=True).first()


def _resolve_source_scope_labels(bindings) -> dict[tuple[str, int], str]:
    """Build a `(scope_kind, scope_id) -> label` map for a binding set.

    Returns labels like:
      * ORG     -> "organization <slug>"
      * TEAM    -> "team <slug>"
      * PROJECT -> "project <team-slug>/<project-slug>"
      * APP     -> "app <slug>"

    Falls back to a bare scope-kind label when a referenced row is
    missing (soft-deleted or migrated away) so the FE never sees an
    empty string. One batch per scope-kind keeps the round-trip count
    bounded regardless of how many bindings come in.
    """
    grouped: dict[str, set[int]] = {}
    for rb in bindings:
        try:
            sid = int(rb.scope_id)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(rb.scope_kind, set()).add(sid)

    labels: dict[tuple[str, int], str] = {}

    if "ORG" in grouped:
        for row in Organization.objects.filter(pk__in=grouped["ORG"]).values("pk", "slug"):
            labels[("ORG", row["pk"])] = f"organization {row['slug']}"
    if "TEAM" in grouped:
        for row in Team.objects.filter(pk__in=grouped["TEAM"]).values("pk", "slug"):
            labels[("TEAM", row["pk"])] = f"team {row['slug']}"
    if "PROJECT" in grouped:
        for row in (
            Project.objects.filter(pk__in=grouped["PROJECT"])
            .select_related("team")
            .values("pk", "slug", "team__slug")
        ):
            labels[("PROJECT", row["pk"])] = f"project {row['team__slug']}/{row['slug']}"
    if "APP" in grouped:
        from astrolift_registry.models import RegisteredApp

        for row in RegisteredApp.objects.filter(pk__in=grouped["APP"]).values("pk", "slug"):
            labels[("APP", row["pk"])] = f"app {row['slug']}"

    # Fill in fallbacks for any scope row that didn't resolve (deleted
    # or absent). Iterate the bindings rather than the grouped sets so
    # we preserve the original scope_id integer type that came in.
    for rb in bindings:
        try:
            sid = int(rb.scope_id)
        except (TypeError, ValueError):
            continue
        key = (rb.scope_kind, sid)
        if key not in labels:
            labels[key] = rb.scope_kind.lower()
    return labels
