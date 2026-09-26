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
from django.db.models import Q
from strawberry.types import Info

from astrolift_graphql import GUID, PageType, keyset_page, search_q
from astrolift_graphql.sorting import NAMED_MODEL_SORTS, ListSortKey, resolve_sort
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
    AppSummaryType,
    ElevationStatusType,
    IdentityProviderType,
    InvitationType,
    MemberType,
    MyProfileType,
    NavTreeProjectType,
    NavTreeTeamType,
    NavTreeType,
    NavTreeWorkflowType,
    OrganizationAllowlistedDomainType,
    OrganizationType,
    PolicyType,
    ProjectType,
    RoleBindingType,
    RoleType,
    SearchableUserType,
    TeamType,
    _resolve_display_name,
    active_session_to_type,
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
from astrolift_identity.scope_visibility import visible_projects, visible_teams
from astrolift_identity.scopes import team_scope_by_guid
from core.decorators import tenant_scoped
from core.naming import PROJECT_SLUG, TEAM_SLUG, NamingViolation
from core.permissions import (
    Permission,
    granted_scopes,
    is_platform_operator,
    module_entitlements,
    require_permission,
)


@strawberry.type(name="AstroliftUserProfile")
class UserProfileType:
    id: str
    username: str | None


@strawberry.type(name="AstroliftModuleEntitlement")
class ModuleEntitlementType:
    """Coarse, server-computed capability for one entity module
    (spec 34/36 §0.3). The shell reads ``me.modules`` to decide which
    top-level modules to render and which in-module actions to surface;
    it never re-derives capability from raw permission slugs.
    """

    key: str
    can_view: bool
    can_create: bool
    can_manage: bool
    can_run: bool
    enabled: bool = strawberry.field(
        description=(
            "Whether the module is switched on for the active organization. "
            "Always true for apps, agents, workflows and admin. For the per-org "
            "modules (chat_studio_integration, agent_live_attach) it is true only "
            "when an org admin turned the module on and the install has not "
            "forced it off. Independent of the can* fields."
        )
    )


@strawberry.type(name="AstroliftMe")
class MeType:
    id: str
    profile: UserProfileType | None

    @strawberry.field
    def modules(self, info: Info) -> list[ModuleEntitlementType]:
        """Capability manifest for the active tenant (spec 34/36 §0.3).

        Computed from the viewer's effective permission slugs via the
        single source-of-truth mapping
        (:func:`core.permissions.module_entitlements`), which reuses
        :func:`~astrolift_identity.permission_resolver.resolve_effective_permissions_anywhere`
        so the manifest and the ``@require_permission`` gates stay in
        lockstep.

        The slug set is the union over every scope in the active org, not
        the org scope alone (#1717): a viewer whose only binding is
        team-scoped resolves to nothing at org scope, and the shell would
        hide Apps, Agents and Workflows from someone who can use all
        three on her own team. This is a visibility decision -- every
        action behind these modules still runs its own scoped check.

        Anonymous / no-tenant returns ``[]`` (no slug catalog leak — the
        shell renders only Dashboard). Superuser → every capability true.
        ``dashboard`` is never listed; the shell always renders it.

        ``enabled`` on the per-org modules comes from
        :func:`astrolift_identity.org_modules.enabled_modules`, the same
        source the builder API gate reads (#1859).

        ``chat_studio_integration.canCreate`` is the one exception to the
        "single mapping" rule above (#1919): it does not mirror
        ``apps.canCreate`` like the rest of the module, because the Builder
        API's create only ever checks ``app.create`` at a TEAM or the ORG
        scope, never a bare project/app grant, and is recomputed from
        :func:`core.permissions.granted_scopes` accordingly so it matches
        what create will actually allow.
        """
        from astrolift_identity.org_modules import enabled_modules
        from astrolift_identity.permission_resolver import resolve_effective_permissions_anywhere
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []

        request = getattr(info.context, "request", None)
        user = getattr(request, "user", None) if request else None
        is_superuser = bool(getattr(user, "is_superuser", False) and getattr(user, "is_active", True))
        is_staff = bool(getattr(user, "is_staff", False) and getattr(user, "is_active", True))

        perms = resolve_effective_permissions_anywhere(tenant)
        rows = module_entitlements(
            perms,
            is_superuser=is_superuser,
            is_staff=is_staff,
            org_modules_enabled=enabled_modules(tenant.organization_id),
        )
        # The Builder API's create only ever checks ``app.create`` at a TEAM
        # or the ORG scope (#1919): a dev environment always resolves to one
        # of those, never a bare project/app grant. chat_studio_integration's
        # ``canCreate`` has to ask that same, narrower question rather than
        # the flat "anywhere" set ``perms`` holds — otherwise a project- or
        # app-scoped ``app.create`` holder sees Chat Studio's Ship light up
        # and then gets a 403 on every create.
        create_scopes = granted_scopes(tenant, Permission.APP_CREATE)
        chat_studio_can_create = create_scopes.org or bool(create_scopes.team_ids)
        return [
            ModuleEntitlementType(
                key=r.key,
                can_view=r.can_view,
                can_create=(chat_studio_can_create if r.key == "chat_studio_integration" else r.can_create),
                can_manage=r.can_manage,
                can_run=r.can_run,
                enabled=r.enabled,
            )
            for r in rows
        ]


def _teams_qs(*, search: str | None = None):
    """Filtered, unordered team list for the caller's org.

    Shared by the list field and its paginated sibling so the two
    can't drift on what a visible team is. No ``order_by`` here —
    ``keyset_page`` imposes the ordering from its seek key.
    """
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return Team.objects.none()
    qs = visible_teams(
        Team.objects.filter(organization_id=org_id).select_related("organization"),
        Permission.TEAM_READ,
    )
    term = (search or "").strip()
    if term:
        qs = qs.filter(search_q(term, "slug", "name", "description"))
    return qs


def _projects_qs(*, search: str | None = None):
    """Filtered, unordered project list for the caller's org.

    Shared by the list field and its paginated sibling; ordering is
    left to ``keyset_page``.
    """
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return Project.objects.none()
    qs = visible_projects(
        Project.objects.filter(organization_id=org_id).select_related("organization", "team"),
        Permission.PROJECT_READ,
    )
    term = (search or "").strip()
    if term:
        qs = qs.filter(search_q(term, "slug", "name", "description", "team__slug", "team__name"))
    return qs


def _members_qs(*, search: str | None = None):
    """Filtered, unordered member list for the caller's org.

    Shared by the list field and its paginated sibling so the two
    can never disagree about which Member rows the caller may see.
    Ordering is deliberately not applied — ``keyset_page`` imposes
    it from the seek key.

    ``search`` filters case-insensitively across username, email,
    first_name, and last_name. The filter runs at the DB layer so
    big orgs don't pull 500 rows just to grep them client-side.
    """
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return Member.objects.none()

    # Scope to members of the caller org's own scopes (PII): the org
    # itself plus its teams / projects / apps. Without this the
    # resolver returned every Member row across every tenant.
    qs = Member.objects.select_related("user").filter(_org_scope_q(org_id))
    term = (search or "").strip()
    if term:
        qs = qs.filter(search_q(term, "user__username", "user__email", "user__first_name", "user__last_name"))
    return qs


def _invitations_qs(*, status: str | None = None, search: str | None = None):
    """Filtered, unordered invitation list for the caller's org.

    Shared by the list field and its paginated sibling; ordering is
    left to ``keyset_page``. A ``None`` org id yields an empty
    queryset rather than ``scope_id IS NULL`` — same (no) rows, but
    the deny-by-default is stated rather than incidental (#1183).
    """
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return Invitation.objects.none()
    qs = Invitation.objects.filter(
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org_id,
        deleted_at__isnull=True,
    ).select_related("role", "invited_by")
    if status:
        qs = qs.filter(status=status)
    term = (search or "").strip()
    if term:
        qs = qs.filter(
            search_q(
                term,
                "email",
                "role__slug",
                "role__name",
                "invited_by__username",
                "invited_by__email",
            )
        )
    return qs


def _roles_qs(*, search: str | None = None):
    """Roles visible to the caller: this org's custom roles plus the
    system catalog. Unordered — ``keyset_page`` orders the page.
    """
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return Role.objects.none()
    # System roles carry a null organization; custom roles are bound
    # to the org. Another org's custom roles never surface.
    qs = Role.objects.filter(Q(organization_id=org_id) | Q(organization__isnull=True))
    term = (search or "").strip()
    if term:
        qs = qs.filter(search_q(term, "slug", "name", "description"))
    return qs


def _role_bindings_qs(*, search: str | None = None, app_slug: str | None = None):
    """Org-wide role bindings, filtered and unordered.

    Shared by the list field and its paginated sibling. Ordering is
    left to ``keyset_page`` — note the seek column is ``granted_at``
    (the grant's own clock), not ``created_at``.

    ``app_slug`` narrows to the bindings scoped to one app, which the app
    Members tab is built around (#1241). Without it that surface paged the
    whole org and the app's three rows landed on page seven, so the operator
    saw empty pages and concluded the app had no members.
    """
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return RoleBinding.objects.none()
    qs = RoleBinding.objects.select_related("user", "role").filter(_org_scope_q(org_id))
    if app_slug:
        # scope_id is a generic BigInt rather than a FK, so the app has to be
        # resolved to its pk first. Deny-by-default: an unknown slug matches
        # nothing rather than falling through to the org-wide list, which
        # would show every binding on a page titled with one app's name.
        from astrolift_registry.models import RegisteredApp

        app_id = (
            RegisteredApp.objects.filter(organization_id=org_id, slug=app_slug)
            .values_list("id", flat=True)
            .first()
        )
        qs = qs.filter(scope_kind=RoleBinding.ScopeKind.APP, scope_id=app_id or 0)
    term = (search or "").strip()
    if term:
        qs = qs.filter(
            search_q(
                term,
                "user__username",
                "user__email",
                "user__first_name",
                "user__last_name",
                "group_external_id",
                "role__slug",
                "role__name",
            )
        )
    return qs


def _api_tokens_qs(*, search: str | None = None):
    """The caller org's API tokens, filtered and unordered."""
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return ApiToken.objects.none()
    qs = ApiToken.objects.filter(organization_id=org_id).select_related("user", "team")
    term = (search or "").strip()
    if term:
        qs = qs.filter(search_q(term, "name", "user__username", "user__email", "team__slug", "token_last_4"))
    return qs


def _policies_qs(*, search: str | None = None):
    """The caller org's ABAC policies, filtered and unordered."""
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return Policy.objects.none()
    # ``created_by`` / ``updated_by`` are FK columns on the Tracking
    # mixin; ``select_related`` keeps the per-row username lookup
    # inside the same query (no N+1 on the policies table — #466).
    qs = Policy.objects.filter(organization_id=org_id).select_related("created_by", "updated_by")
    term = (search or "").strip()
    if term:
        qs = qs.filter(search_q(term, "slug", "name", "description", "action_pattern"))
    return qs


@strawberry.type
class IdentityQuery:
    @strawberry.field
    def me(self, info: Info) -> MeType | None:
        """Return the currently authenticated user."""
        request = info.context.request
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        return MeType(
            id=str(user.pk),
            profile=UserProfileType(
                id=str(user.pk),
                username=user.get_username() or None,
            ),
        )

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_organization(self, info: Info, slug: str) -> OrganizationType | None:
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return None
        # A slug belonging to any other org reads as not-found — the
        # caller's tenant is a single org, so the only org they may
        # read by slug is their own.
        org = Organization.objects.filter(slug=slug).first()
        if org is None or org.id != org_id:
            return None
        return organization_to_type(org)

    @strawberry.field
    def astrolift_organizations(self, info: Info) -> list[OrganizationType]:
        """Return the organizations accessible to the current user.

        Not wrapped with @tenant_scoped() because this is the bootstrap
        resolver — the frontend calls it to discover which org to set as
        the active tenant. Requiring a tenant context here creates a
        chicken-and-egg: you need the org to get the org.

        Session-authenticated superusers see all orgs. Regular users see
        only their active memberships. API tokens narrow either result to
        their issuing organization.
        """
        request = info.context.request
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return []
        # Every org is the platform operator's list; Django staff is not the
        # operator and sees its own memberships like anyone else (#1978).
        if is_platform_operator(user):
            orgs = Organization.objects.filter(deleted_at__isnull=True)
        else:
            from astrolift_identity.models import Member

            member_org_ids = (
                Member.objects.filter(user_id=user.pk, scope_kind="ORG", is_active=True)
                .values_list("scope_id", flat=True)
                .distinct()
            )
            orgs = Organization.objects.filter(pk__in=member_org_ids, deleted_at__isnull=True)
        api_token = getattr(request, "_api_token", None)
        if api_token is not None:
            orgs = orgs.filter(pk=api_token.organization_id)
        return [organization_to_type(o) for o in orgs.order_by("name")[:100]]

    @strawberry.field(
        deprecation_reason="Caps at 200 rows with no way to reach the 201st. Use astroliftTeamsPage."
    )
    @require_permission(Permission.TEAM_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_teams(self, info: Info) -> list[TeamType]:
        return [team_to_type(t) for t in _teams_qs()[:200]]

    @strawberry.field
    @require_permission(Permission.TEAM_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_teams_page(
        self,
        info: Info,
        search: str | None = None,
        sort_by: ListSortKey | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[TeamType]:
        """Cursor-paginated team list (#1235).

        Replaces ``astroliftTeams``, whose 200-row cap put the 201st
        team out of reach of the UI entirely. Seek key is
        ``(-created_at, -guid)``; the list field left the slice
        unordered, so newest-first is the first stable order this
        surface has had. ``search`` matches slug, name, description.
        """
        order, scope = resolve_sort(sort_by, NAMED_MODEL_SORTS)
        page = keyset_page(
            _teams_qs(search=search),
            cursor=after,
            limit=limit,
            sort_field=order.sort_field,
            tiebreak_field=order.tiebreak_field,
            descending=order.descending,
            cursor_scope=scope,
        )
        return page.map(team_to_type)

    @strawberry.field(
        deprecation_reason="Caps at 200 rows with no way to reach the 201st. Use astroliftProjectsPage."
    )
    @require_permission(Permission.PROJECT_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_projects(self, info: Info) -> list[ProjectType]:
        return [project_to_type(p) for p in _projects_qs()[:200]]

    @strawberry.field
    @require_permission(Permission.PROJECT_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_projects_page(
        self,
        info: Info,
        search: str | None = None,
        sort_by: ListSortKey | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[ProjectType]:
        """Cursor-paginated project list (#1235).

        Replaces ``astroliftProjects`` and its unreachable-past-200 cap.
        Seek key is ``(-created_at, -guid)``. ``search`` matches the
        project's own slug / name / description plus the owning team's
        slug and name, because operators navigate projects by team.
        """
        order, scope = resolve_sort(sort_by, NAMED_MODEL_SORTS)
        page = keyset_page(
            _projects_qs(search=search),
            cursor=after,
            limit=limit,
            sort_field=order.sort_field,
            tiebreak_field=order.tiebreak_field,
            descending=order.descending,
            cursor_scope=scope,
        )
        return page.map(project_to_type)

    @strawberry.field
    @require_permission(Permission.TEAM_UPDATE)
    @tenant_scoped()
    def astrolift_team_slug_available(self, info: Info, slug: str, exclude_id: GUID | None = None) -> bool:
        """Live check backing the team-rename form (debounced as the
        operator types). ``True`` only when ``slug`` is a syntactically
        valid team slug AND free within the caller's org — i.e. the
        rename mutation would accept it. A malformed slug returns
        ``False`` (the mutation would reject it), so the FE treats
        invalid as unavailable and surfaces its own format hint.

        Uniqueness is per-org and scoped to live rows (the default
        manager filters soft-deleted). ``exclude_id`` is the guid of the
        team being edited so keeping its current slug reads as available.
        """
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return False
        try:
            TEAM_SLUG.validate(slug)
        except NamingViolation:
            return False
        qs = Team.objects.filter(organization_id=org_id, slug=slug)
        if exclude_id is not None:
            qs = qs.exclude(guid=str(exclude_id))
        return not qs.exists()

    @strawberry.field
    @require_permission(Permission.PROJECT_UPDATE, scope=team_scope_by_guid("team_id"))
    @tenant_scoped()
    def astrolift_project_slug_available(
        self, info: Info, team_id: GUID, slug: str, exclude_id: GUID | None = None
    ) -> bool:
        """Live check backing the project-rename form. ``True`` only when
        ``slug`` is a valid project slug AND free within ``team_id`` — a
        project slug is unique per team, not per org. The team must
        belong to the caller's org; a foreign or unknown ``team_id``
        returns ``False`` (fail closed), as does a malformed slug.

        ``exclude_id`` is the guid of the project being edited so keeping
        its current slug reads as available. Rows are scoped to live ones
        by the default manager.
        """
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return False
        team = Team.objects.filter(guid=str(team_id), organization_id=org_id).first()
        if team is None:
            return False
        try:
            PROJECT_SLUG.validate(slug)
        except NamingViolation:
            return False
        # Uniqueness is per-team; the explicit ``organization_id`` clause
        # keeps the fetch fail-closed on the caller's org (denormalized and
        # always equal to ``team.organization_id``) so the boundary is
        # visible on the query itself, not only via the org-scoped team above.
        qs = Project.objects.filter(team_id=team.pk, slug=slug, organization_id=org_id)
        if exclude_id is not None:
            qs = qs.exclude(guid=str(exclude_id))
        return not qs.exists()

    @strawberry.field
    @tenant_scoped()
    def astrolift_nav_tree(self, info: Info) -> NavTreeType | None:
        """Permission-shaped project navigation for apps, workflows, and agents.

        Self-service: no ``@require_permission`` gate -- tenant scope
        already binds the request to a single organization, which is
        the visibility boundary. Mirrors ``astrolift_my_permissions``
        in that respect; every authed user is allowed to see the shape
        of the tenant they belong to. Resource-level reads continue to
        flow through the per-domain permission-gated resolvers.

        Composes batched queries (teams, projects, apps, workflows) and
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

        from workflows.composition import resolve_child_definition_from_candidates
        from workflows.models import WorkflowDefinition, WorkflowStage

        project_ids = [project.id for project in projects]
        workflow_definitions = list(
            WorkflowDefinition.objects.filter(
                Q(organization_id=org_id) | Q(organization__isnull=True),
                Q(project_id__in=project_ids) | Q(project__isnull=True),
            )
            .prefetch_related("stages__agent_definition")
            .order_by("name")
        )
        project_workflow_definitions = [
            definition
            for definition in workflow_definitions
            if definition.organization_id == org_id and definition.project_id is not None
        ]

        # Classify each app into a nav primitive from its workloads (one query),
        # so the sidebar picks the right icon + route: agent wins → single-kind
        # → bundle. `primitive_by_app[app_id] = (kind, slug)`.
        from astrolift_registry.models import Workload

        _wl_by_app: dict[int, list[tuple[str, str]]] = {}
        for _app_id, _kind, _wslug in Workload.objects.filter(
            registered_app__organization_id=org_id
        ).values_list("registered_app_id", "kind", "slug"):
            _wl_by_app.setdefault(_app_id, []).append((_kind, _wslug))

        # Single-kind workloads that map 1:1 to a nav primitive. Deployment /
        # statefulset (and the empty/0-workload case) fall through to "app".
        _SINGLE_KIND_PRIMITIVE = {
            Workload.Kind.WORKFLOW.value: "workflow",
            Workload.Kind.FUNCTION.value: "function",
            Workload.Kind.CRONJOB.value: "cronjob",
            Workload.Kind.TASK.value: "task",
        }

        def _nav_primitive(app) -> tuple[str, str]:
            wls = _wl_by_app.get(app.id, [])
            kinds = {k for k, _ in wls}
            if Workload.Kind.AGENT.value in kinds:
                agent_slug = next((s for k, s in wls if k == Workload.Kind.AGENT.value), app.slug)
                return ("agent", agent_slug)
            if len(kinds) > 1:
                return ("bundle", app.slug)
            only = next(iter(kinds), None)
            return (_SINGLE_KIND_PRIMITIVE.get(only, "app"), app.slug)

        def _summ(app):
            kind, slug = _nav_primitive(app)
            return app_to_summary(app, primitive_kind=kind, primitive_slug=slug)

        summaries_by_app_id = {app.id: _summ(app) for app in apps}
        agent_summaries_by_slug = {
            summary.primitive_slug: summary
            for app_id, summary in summaries_by_app_id.items()
            if summary.primitive_kind == "agent"
        }

        definitions_by_slug: dict[str, list[WorkflowDefinition]] = {}
        for definition in workflow_definitions:
            if definition.slug:
                definitions_by_slug.setdefault(definition.slug, []).append(definition)

        def _resolve_loaded_child(
            parent: WorkflowDefinition,
            workflow_ref: str,
        ) -> WorkflowDefinition | None:
            return resolve_child_definition_from_candidates(
                parent,
                workflow_ref,
                definitions_by_slug.get((workflow_ref or "").strip(), []),
            )

        workflow_definitions_by_project: dict[int, list[WorkflowDefinition]] = {}
        for definition in project_workflow_definitions:
            workflow_definitions_by_project.setdefault(definition.project_id, []).append(definition)

        workflow_nodes_by_project: dict[int, list[NavTreeWorkflowType]] = {}
        bound_agent_slugs_by_project: dict[int, set[str]] = {}
        for project_id, root_definitions in workflow_definitions_by_project.items():
            included: dict[int, WorkflowDefinition] = {
                definition.id: definition for definition in root_definitions
            }
            pending = list(root_definitions)
            while pending:
                definition = pending.pop()
                for stage in sorted(definition.stages.all(), key=lambda item: item.order):
                    if stage.deleted_at is not None or stage.kind != WorkflowStage.StageKind.WORKFLOW:
                        continue
                    child = _resolve_loaded_child(definition, stage.workflow_ref)
                    if child is not None and child.id not in included:
                        included[child.id] = child
                        pending.append(child)

            nodes: list[NavTreeWorkflowType] = []
            for definition in sorted(included.values(), key=lambda item: item.name):
                agents: list[AppSummaryType] = []
                child_workflow_ids: list[GUID] = []
                seen_agent_slugs: set[str] = set()
                seen_child_ids: set[int] = set()
                for stage in sorted(definition.stages.all(), key=lambda item: item.order):
                    if stage.deleted_at is not None:
                        continue
                    if stage.kind == WorkflowStage.StageKind.WORKFLOW:
                        child = _resolve_loaded_child(definition, stage.workflow_ref)
                        if child is not None and child.id in included and child.id not in seen_child_ids:
                            child_workflow_ids.append(GUID(str(child.guid)))
                            seen_child_ids.add(child.id)
                        continue
                    agent_slug = (
                        stage.agent_definition.slug
                        if stage.agent_definition_id is not None
                        else stage.agent_ref
                    )
                    if not agent_slug or agent_slug in seen_agent_slugs:
                        continue
                    summary = agent_summaries_by_slug.get(agent_slug)
                    if summary is None:
                        continue
                    seen_agent_slugs.add(agent_slug)
                    agents.append(summary)
                bound_agent_slugs_by_project.setdefault(project_id, set()).update(seen_agent_slugs)
                nodes.append(
                    NavTreeWorkflowType(
                        id=GUID(str(definition.guid)),
                        slug=definition.slug or "",
                        name=definition.name,
                        is_enabled=definition.is_enabled,
                        agents=agents,
                        child_workflow_ids=child_workflow_ids,
                    )
                )
            workflow_nodes_by_project[project_id] = nodes

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
                project_apps = apps_by_project.get(project.id, [])
                bound_agent_slugs = bound_agent_slugs_by_project.get(project.id, set())
                project_nodes.append(
                    NavTreeProjectType(
                        project=project_to_type(project),
                        apps=[
                            summaries_by_app_id[a.id]
                            for a in project_apps
                            if summaries_by_app_id[a.id].primitive_kind != "agent"
                        ],
                        workflows=workflow_nodes_by_project.get(project.id, []),
                        standalone_agents=[
                            summaries_by_app_id[a.id]
                            for a in project_apps
                            if summaries_by_app_id[a.id].primitive_kind == "agent"
                            and summaries_by_app_id[a.id].primitive_slug not in bound_agent_slugs
                        ],
                    )
                )
            team_nodes.append(
                NavTreeTeamType(
                    team=team_to_type(team),
                    projects=project_nodes,
                    unassigned_apps=[_summ(a) for a in apps_by_team_no_project.get(team.id, [])],
                )
            )

        return NavTreeType(
            organization=organization_to_type(org),
            teams=team_nodes,
            unassigned_apps=[_summ(a) for a in unassigned_org_apps],
        )

    # ---- RBAC queries ------------------------------------------------

    @strawberry.field(
        deprecation_reason="Caps at 500 rows with no way to reach the 501st. Use astroliftMembersPage."
    )
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_members(self, info: Info, search: str | None = None) -> list[MemberType]:
        """Org-member listing with discoverability affordances.

        ``search`` filters case-insensitively across username, email,
        first_name, and last_name.

        ``last_active_at`` is computed per-member as the most recent
        ``AuditEvent.occurred_at`` where the actor matches this member's
        user, scoped to the current organization. Resolved in one
        aggregate query so the field doesn't fan out N+1 on member count.
        """
        members = list(_members_qs(search=search).order_by("-created_at")[:500])
        last_active = _last_active_by_user_id(members)
        return [member_to_type(m, last_active_at=last_active.get(m.user_id)) for m in members]

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_members_page(
        self,
        info: Info,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[MemberType]:
        """Cursor-paginated org-member listing (#1235).

        Replaces ``astroliftMembers``, whose 500-row cap made the 501st
        member unreachable from the People table rather than merely slow
        to reach. Seek key is ``(-created_at, -guid)``; ``search``
        matches the same four user columns the list field does.

        The ``last_active_at`` aggregate runs over the page's rows only
        — resolving it across the whole filtered set would scan the
        org's entire audit stream to render fifty rows.
        """
        page = keyset_page(_members_qs(search=search), cursor=after, limit=limit)
        last_active = _last_active_by_user_id(page.rows)
        return page.map(lambda m: member_to_type(m, last_active_at=last_active.get(m.user_id)))

    @strawberry.field
    @require_permission(Permission.TEAM_READ, scope=team_scope_by_guid("team_id"))
    @tenant_scoped()
    def astrolift_team_members(self, info: Info, team_id: GUID) -> list[MemberType]:
        """Members attached to one team, by team GUID.

        The Member.scope_id column stores the team's integer pk, so a
        consumer that only has the team's GUID can't filter via the
        generic ``astrolift_members`` resolver — this resolver does the
        GUID→pk hop server-side. Used by the team detail page's bulk
        role-assign panel (#416).
        """
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        # A team guid from another org reads as empty (PII) rather than
        # exposing that org's team roster.
        team = Team.objects.filter(guid=str(team_id), deleted_at__isnull=True).first()
        if team is None or team.organization_id != org_id:
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

    @strawberry.field(
        deprecation_reason="Caps at 500 rows with no way to reach the 501st. Use astroliftInvitationsPage."
    )
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
        rows = list(_invitations_qs(status=status).order_by("-created_at")[:500])
        userinfo_by_user_id = _userinfo_by_inviter_id(rows)
        return [invitation_to_type(r, userinfo_by_user_id=userinfo_by_user_id) for r in rows]

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_invitations_page(
        self,
        info: Info,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[InvitationType]:
        """Cursor-paginated invitation history (#1235).

        Replaces ``astroliftInvitations`` and its 500-row cap. Because
        the default surfaces every status, an org that has been inviting
        for a year hits that cap on accepted rows alone and can no
        longer see its own pending ones. Seek key is
        ``(-created_at, -guid)``; ``search`` matches the invitee's
        email, the granted role, and the inviter.

        The inviter ``UserInfo`` prefetch runs over the page's rows
        only, so it stays one small query per page instead of one over
        every invitation the org has ever sent.
        """
        page = keyset_page(_invitations_qs(status=status, search=search), cursor=after, limit=limit)
        userinfo_by_user_id = _userinfo_by_inviter_id(page.rows)
        return page.map(lambda r: invitation_to_type(r, userinfo_by_user_id=userinfo_by_user_id))

    @strawberry.field(
        deprecation_reason="Caps at 200 rows with no way to reach the 201st. Use astroliftRolesPage."
    )
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_roles(self, info: Info) -> list[RoleType]:
        return [role_to_type(r) for r in _roles_qs().order_by("scope_level", "slug")[:200]]

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_roles_page(
        self,
        info: Info,
        search: str | None = None,
        sort_by: ListSortKey | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[RoleType]:
        """Cursor-paginated role catalog (#1235).

        Replaces ``astroliftRoles`` and its 200-row cap. Seek key is
        ``(-created_at, -guid)``: the list field's ``(scope_level,
        slug)`` order has no not-null unique tiebreak to hang a cursor
        on, and newest-first surfaces the org's own custom roles above
        the seeded system catalog. ``search`` matches slug, name,
        description — sort by name client-side if the table wants it.
        """
        order, scope = resolve_sort(sort_by, NAMED_MODEL_SORTS)
        page = keyset_page(
            _roles_qs(search=search),
            cursor=after,
            limit=limit,
            sort_field=order.sort_field,
            tiebreak_field=order.tiebreak_field,
            descending=order.descending,
            cursor_scope=scope,
        )
        return page.map(role_to_type)

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
        org scope, the scope an invitation binds at. Rationale: an
        operator who lacks ``app.deploy`` can't legitimately promote
        someone else into a role that carries it. The mutations that
        hand out roles enforce the same ceiling
        (``astrolift_identity.grants``, #1964), so this list is exactly
        what ``createInvitation`` and an org-scope ``grantRole`` accept.
        Django superusers see every role.

        Empty list is a legitimate result and tells the FE to disable
        the picker with an explainer; not the same as
        PERMISSION_DENIED (which still surfaces if the caller lacks
        ``org.manage_members`` entirely).

        System roles + this org's custom roles are both considered.
        Soft-deleted roles are skipped. Bound to the org scope of the
        active tenant; cross-tenant custom roles are excluded.
        """
        from django.db.models import Q

        from astrolift_identity.grants import grant_ceiling
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        actor_id = tenant.actor_user_id if tenant else None
        if org_id is None or actor_id is None:
            return []

        # System roles have ``organization_id is null``; custom roles
        # are bound to the org. Either is a candidate.
        candidates = list(
            Role.objects.filter(deleted_at__isnull=True)
            .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
            .order_by("scope_level", "slug")
        )

        ceiling = grant_ceiling(tenant, scope_kind="ORG", scope_id=org_id)
        return [role_to_type(r) for r in candidates if ceiling.allows(r.permissions)]

    @strawberry.field(
        deprecation_reason="Caps at 500 rows with no way to reach the 501st. Use astroliftRoleBindingsPage."
    )
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
        qs = list(_role_bindings_qs().order_by("-granted_at")[:500])
        labels = _resolve_source_scope_labels(qs)
        return [
            role_binding_to_type(rb, source_scope_label=labels.get((rb.scope_kind, rb.scope_id), ""))
            for rb in qs
        ]

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_role_bindings_page(
        self,
        info: Info,
        search: str | None = None,
        app_slug: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[RoleBindingType]:
        """Cursor-paginated role bindings (#1235).

        Replaces ``astroliftRoleBindings`` and its 500-row cap — a cap
        an org with a few hundred members clears on org-scope grants
        alone, hiding every team / project / app grant behind it.

        Seek key is ``(-granted_at, -guid)``: ``granted_at`` is the
        column the list field sorted on and is a distinct, not-null
        (``auto_now_add``) timestamp from ``created_at``, so paging on
        ``created_at`` could disagree with the order the operator sees.

        The source-scope label batch runs over the page's rows only —
        resolving labels for every binding in the org would defeat the
        pagination it is decorating.
        """
        page = keyset_page(
            _role_bindings_qs(search=search, app_slug=app_slug),
            cursor=after,
            limit=limit,
            sort_field="granted_at",
        )
        labels = _resolve_source_scope_labels(page.rows)
        return page.map(
            lambda rb: role_binding_to_type(
                rb, source_scope_label=labels.get((rb.scope_kind, rb.scope_id), "")
            )
        )

    @strawberry.field(
        deprecation_reason="Caps at 200 rows with no way to reach the 201st. Use astroliftApiTokensPage."
    )
    @require_permission(Permission.API_TOKEN_CREATE)
    @tenant_scoped()
    def astrolift_api_tokens(self, info: Info) -> list[ApiTokenType]:
        return [api_token_to_type(t) for t in _api_tokens_qs().order_by("-created_at")[:200]]

    @strawberry.field
    @require_permission(Permission.API_TOKEN_CREATE)
    @tenant_scoped()
    def astrolift_api_tokens_page(
        self,
        info: Info,
        search: str | None = None,
        sort_by: ListSortKey | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[ApiTokenType]:
        """Cursor-paginated API-token list (#1235).

        Replaces ``astroliftApiTokens`` and its 200-row cap. Seek key is
        ``(-created_at, -guid)``. ``search`` matches the token name, its
        owner, its team, and ``token_last_4`` — the last four is what an
        operator has in hand when chasing a token seen in an audit log.
        """
        order, scope = resolve_sort(sort_by, NAMED_MODEL_SORTS)
        page = keyset_page(
            _api_tokens_qs(search=search),
            cursor=after,
            limit=limit,
            sort_field=order.sort_field,
            tiebreak_field=order.tiebreak_field,
            descending=order.descending,
            cursor_scope=scope,
        )
        return page.map(api_token_to_type)

    @strawberry.field(
        deprecation_reason="Caps at 200 rows with no way to reach the 201st. Use astroliftPoliciesPage."
    )
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_policies(self, info: Info) -> list[PolicyType]:
        return [policy_to_type(p) for p in _policies_qs().order_by("scope_level", "slug")[:200]]

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_policies_page(
        self,
        info: Info,
        search: str | None = None,
        sort_by: ListSortKey | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[PolicyType]:
        """Cursor-paginated ABAC policy list (#1235).

        Replaces ``astroliftPolicies`` and its 200-row cap. Seek key is
        ``(-created_at, -guid)`` rather than the list field's
        ``(scope_level, slug)``, which carries no not-null unique
        tiebreak to anchor a cursor on; newest-first also puts the
        policy an operator just wrote at the top. ``search`` matches
        slug, name, description, and ``action_pattern``.
        """
        order, scope = resolve_sort(sort_by, NAMED_MODEL_SORTS)
        page = keyset_page(
            _policies_qs(search=search),
            cursor=after,
            limit=limit,
            sort_field=order.sort_field,
            tiebreak_field=order.tiebreak_field,
            descending=order.descending,
            cursor_scope=scope,
        )
        return page.map(policy_to_type)

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
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        # Prefetch ``last_switched_by`` (FK on the IdP row) so the per-row
        # username lookup folds into the same query — the FE renders the
        # "by <operator>" caption on every active IdP, so a naive lookup
        # would fan out N+1 on the list (#467).
        qs = (
            IdentityProvider.objects.filter(organization_id=org_id)
            .select_related("organization", "last_switched_by")
            .order_by("-is_default", "kind")[:50]
        )
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

        Routes through
        ``permission_resolver.resolve_effective_permissions_anywhere``
        so the read-side and the gate share the same scope-traversal
        logic (#478) — fix-once-apply-everywhere. That union spans every
        scope in the active org, so a team- or app-scoped viewer sees the
        slugs she actually holds rather than an empty list (#1717); the
        per-app ``viewerPermissions`` field stays point-scoped because it
        answers about one app.
        """
        from astrolift_identity.permission_resolver import resolve_effective_permissions_anywhere
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []
        return sorted(resolve_effective_permissions_anywhere(tenant))

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
    def astrolift_active_sessions(self, info: Info) -> list[ActiveSessionType]:
        """The signed-in viewer's own AstroliftSession rows.

        Self-only — every authed user can list their own sessions;
        no permission gate. Exempt from ``@tenant_scoped`` because a
        session is bound to a user, not an org (see
        ``test_tenancy_guardrail.py`` EXEMPT list).

        Rows come from ``AstroliftSession`` (the sidecar) populated
        by ``SessionTrackingMiddleware``; not from ``django_session``
        directly. That gives us ``client_kind`` + ``last_seen_at`` +
        a stable GUID for the per-row revoke path.
        """
        from astrolift_identity.models import AstroliftSession
        from astrolift_identity.sessions import record_session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return []

        # Defensive: if the middleware hasn't had a chance to write
        # the row for the current request yet (e.g. a brand-new login
        # session being listed before the response cycle completes),
        # touch it now so the caller doesn't see an empty list.
        record_session(request)

        current_key = getattr(getattr(request, "session", None), "session_key", None) or ""
        qs = AstroliftSession.objects.filter(user=viewer).order_by("-last_seen_at", "-created_at")
        return [
            active_session_to_type(row, is_current=(bool(current_key) and row.session_key == current_key))
            for row in qs
        ]

    @strawberry.field
    def astrolift_elevation_status(self, info: Info) -> ElevationStatusType:
        """Snapshot of the current session's step-up elevation (#487).

        Self-only — every authed user sees their own session's
        elevation, no permission gate. Unauthenticated callers get a
        deny-shaped envelope (``elevated=False``, ``required_for=[]``)
        rather than an exception so the FE can render the indicator
        in a logged-out shell without a try/catch.
        """
        from astrolift_identity.session_elevation import get_status
        from astrolift_identity.step_up import list_gated_resolvers

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        session = getattr(request, "session", None) if request else None
        if viewer is None or not viewer.is_authenticated or session is None:
            return ElevationStatusType(
                elevated=False,
                elevated_until=None,
                seconds_remaining=0,
                method=None,
                required_for=[],
            )

        status = get_status(session)
        # ``list_gated_resolvers`` walks the live schema so the FE's
        # ``required_for`` reflects exactly what the backend gates —
        # operators that bolt new sensitive mutations on a fork get
        # them announced automatically once they decorate.
        gated = [p.resolver.split(".", 1)[-1] for p in list_gated_resolvers()]
        return ElevationStatusType(
            elevated=status.elevated,
            elevated_until=status.elevated_until,
            seconds_remaining=status.seconds_remaining,
            method=status.method,
            required_for=gated,
        )


def _org_scope_q(org_id: int | None) -> Q:
    """Q over ``(scope_kind, scope_id)`` matching every scope owned by ``org_id``.

    ``RoleBinding`` / ``Member`` rows key their scope with a generic
    ``(scope_kind, scope_id)`` pair rather than an organization FK, so
    constraining them to a caller's org means enumerating that org's own
    scope rows at all four levels: the org itself, plus its teams,
    projects, and apps.

    Soft-deleted scope rows are included (``all_objects``) so a binding
    whose team/project/app was later removed still resolves to *this*
    org — the row is still org-owned, and the source-scope label
    machinery already renders a fallback for a vanished scope. A ``None``
    org_id (no resolved tenant) yields a Q that matches nothing, i.e.
    fail closed.
    """
    from astrolift_registry.models import RegisteredApp

    team_ids = list(Team.all_objects.filter(organization_id=org_id).values_list("pk", flat=True))
    project_ids = list(Project.all_objects.filter(organization_id=org_id).values_list("pk", flat=True))
    app_ids = list(RegisteredApp.all_objects.filter(organization_id=org_id).values_list("pk", flat=True))
    return (
        Q(scope_kind="ORG", scope_id=org_id)
        | Q(scope_kind="TEAM", scope_id__in=team_ids)
        | Q(scope_kind="PROJECT", scope_id__in=project_ids)
        | Q(scope_kind="APP", scope_id__in=app_ids)
    )


def _last_active_by_user_id(members) -> dict[int, dt.datetime]:
    """Newest ``AuditEvent.occurred_at`` per member, in the caller's org.

    One aggregate query for the whole batch so ``last_active_at``
    doesn't fan out N+1 on member count. Call it with the rows that are
    actually being rendered: for the paginated field that is the page,
    not the filtered set behind it (#1235).

    Rows whose ``actor_id`` isn't an integer are skipped — the column is
    a free-form string shared with service / token actors, so a
    non-user actor id is data, not corruption.
    """
    from django.db.models import Max

    from astrolift_operations.models.audit_event import AuditEvent
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    user_ids = [m.user_id for m in members if m.user_id is not None]
    if org_id is None or not user_ids:
        return {}

    last_active_by_user_id: dict[int, dt.datetime] = {}
    rows = (
        AuditEvent.objects.filter(
            organization_id=org_id,
            actor_kind="user",
            actor_id__in=[str(uid) for uid in user_ids],
        )
        .values("actor_id")
        .annotate(last_at=Max("occurred_at"))
    )
    for row in rows:
        try:
            last_active_by_user_id[int(row["actor_id"])] = row["last_at"]
        except (TypeError, ValueError):
            continue
    return last_active_by_user_id


def _userinfo_by_inviter_id(invitations) -> dict[int, object]:
    """Auth0 ``UserInfo`` rows for a batch of invitations' inviters.

    One query for the batch so an invitation row can render the
    inviter's avatar (#418) without a per-row lookup. Call it with the
    rows being rendered — the page, not the whole stream (#1235).

    A user can have multiple UserInfo rows (one per Auth0 subject
    claim); last write wins, and the row only needs a representative
    avatar, so any is fine.
    """
    from auth1.models import UserInfo

    inviter_ids = {inv.invited_by_id for inv in invitations if inv.invited_by_id}
    if not inviter_ids:
        return {}
    return {ui.internal_user_id: ui for ui in UserInfo.objects.filter(internal_user_id__in=inviter_ids)}


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
