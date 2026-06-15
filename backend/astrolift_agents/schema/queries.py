"""Read-only queries for the Agent Dispatch Layer.

Skills, ToolDefs, Briefs, and AgentTasks are org-scoped; the
``dispatchers`` resolver is platform-level (the routing fabric spans
tenants) and is staff/superuser-only.

Every resolver carries ``@require_permission`` + ``@tenant_scoped`` per
the tenancy guardrail. ``@tenant_scoped`` only asserts a tenant context
exists — it does not filter — so each resolver applies its own org
``Q`` and rejects an ``org_id`` argument that doesn't match the caller's
active tenant (a non-superuser may not read another org's rows).
"""

from __future__ import annotations

import strawberry
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_agents.models import (
    AgentEnvironmentSpec,
    AgentTask,
    Brief,
    DispatcherInstance,
    Skill,
    ToolDef,
)
from astrolift_agents.schema.types import (
    AgentEnvironmentSpecType,
    AgentRuntimeType,
    AgentTaskType,
    BriefType,
    DispatcherInstanceType,
    SkillType,
    ToolDefType,
    agent_env_spec_to_type,
    agent_task_to_type,
    brief_to_type,
    dispatcher_to_type,
    skill_to_type,
    tool_def_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


def _caller_org_id(info: Info, org_id: strawberry.ID) -> int:
    """Resolve the caller's active organization, asserting it matches
    the requested ``org_id`` GUID.

    ``@tenant_scoped`` guarantees a tenant context exists; this enforces
    that the explicit ``org_id`` argument refers to that same org so a
    caller can't read another tenant's rows by passing a foreign GUID.
    Superusers bypass the match (their tenant context may differ).
    """
    tenant = get_current_tenant()
    org = tenant.organization_id if tenant else None
    if org is None:
        raise GraphQLError("no active organization")

    from astrolift_identity.models import Organization

    requested = Organization.objects.filter(guid=str(org_id), deleted_at__isnull=True).first()
    if requested is None:
        raise GraphQLError("organization not found")

    user = getattr(getattr(info.context, "request", None), "user", None)
    is_super = bool(getattr(user, "is_superuser", False))
    if requested.pk != org and not is_super:
        raise GraphQLError("organization mismatch")
    return requested.pk


@strawberry.type
class AgentsQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def skills(self, info: Info, org_id: strawberry.ID, is_global: bool = False) -> list[SkillType]:
        """Org skills plus all global skills.

        ``is_global=True`` narrows to global skills only; otherwise the
        org's own skills are unioned with the global catalog (both
        readable by every org).
        """
        from django.db.models import Q

        org_pk = _caller_org_id(info, org_id)
        if is_global:
            scope = Q(is_global=True)
        else:
            scope = Q(organization_id=org_pk) | Q(is_global=True)
        qs = Skill.objects.filter(scope, deleted_at__isnull=True).order_by("-is_global", "slug")[:200]
        return [skill_to_type(s) for s in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def skill(self, info: Info, id: strawberry.ID) -> SkillType | None:
        """One skill by GUID, scoped to the caller's org or the global
        catalog. Foreign-org skills resolve to null (not an error) so
        the surface doesn't leak existence across tenants."""
        from django.db.models import Q

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = Skill.objects.filter(
            Q(organization_id=org_pk) | Q(is_global=True),
            guid=str(id),
            deleted_at__isnull=True,
        ).first()
        return skill_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def tool_defs(self, info: Info, skill_id: strawberry.ID) -> list[ToolDefType]:
        """ToolDefs attached to ``skill_id``. The parent skill must be
        readable by the caller's org (or global), else an empty list."""
        from django.db.models import Q

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        skill = Skill.objects.filter(
            Q(organization_id=org_pk) | Q(is_global=True),
            guid=str(skill_id),
            deleted_at__isnull=True,
        ).first()
        if skill is None:
            return []
        qs = ToolDef.objects.filter(skill=skill, deleted_at__isnull=True).order_by("slug")[:200]
        return [tool_def_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def org_tool_defs(self, info: Info, org_id: strawberry.ID) -> list[ToolDefType]:
        """All ToolDefs across every skill visible to ``org_id`` (own +
        global). Allows the Tool Registry UI to list all tools without
        fetching per-skill. Capped at 500 rows."""
        from django.db.models import Q

        org_pk = _caller_org_id(info, org_id)
        skill_scope = Q(skill__organization_id=org_pk) | Q(skill__is_global=True)
        qs = (
            ToolDef.objects.filter(skill_scope, deleted_at__isnull=True)
            .select_related("skill")
            .order_by("skill__slug", "slug")[:500]
        )
        return [tool_def_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def brief(self, info: Info, id: strawberry.ID) -> BriefType | None:
        """One Brief by GUID, scoped to the caller's org."""
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = Brief.objects.filter(guid=str(id), organization_id=org_pk, deleted_at__isnull=True).first()
        return brief_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def agent_tasks(
        self, info: Info, org_id: strawberry.ID, status: str | None = None
    ) -> list[AgentTaskType]:
        """The org's AgentTasks, newest first, optionally filtered by
        status. An unknown status string yields an empty list rather
        than an error."""
        org_pk = _caller_org_id(info, org_id)
        qs = AgentTask.objects.filter(organization_id=org_pk, deleted_at__isnull=True)
        if status:
            qs = qs.filter(status=status)
        # select_related the org so snapshot_url presigning (per RUNNING vnc
        # row) doesn't fire a query per task — the org is the only related
        # object agent_task_to_type touches.
        qs = qs.select_related("organization").order_by("-created_at")[:200]
        return [agent_task_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.AGENT_TASK_WATCH)
    @tenant_scoped()
    def agent_gallery(self, info: Info, org_id: strawberry.ID) -> list[AgentTaskType]:
        """The org's *watchable* agent tasks — the VNC theatre roster.

        Narrows to RUNNING, VNC-capable tasks that have a published relay
        path (``vnc_url``), newest first. Each row carries ``vnc_url`` (the
        live RFB relay the theatre connects to) and ``snapshot_url`` (a
        short-lived presigned GET for the latest framebuffer JPEG the gallery
        tiles poll). Gated on ``agent_task.watch`` — the same operator-grade
        permission the live VNC relay enforces — rather than plain
        ``app.read``, so the gallery never lists a session the caller could
        not actually open.
        """
        org_pk = _caller_org_id(info, org_id)
        qs = (
            AgentTask.objects.filter(
                organization_id=org_pk,
                status=AgentTask.Status.RUNNING,
                vnc_enabled=True,
                deleted_at__isnull=True,
            )
            .exclude(vnc_url="")
            .select_related("organization")
            .order_by("-started_at", "-created_at")[:200]
        )
        return [agent_task_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def agent_task(self, info: Info, id: strawberry.ID) -> AgentTaskType | None:
        """One AgentTask by GUID, scoped to the caller's org."""
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = (
            AgentTask.objects.filter(guid=str(id), organization_id=org_pk, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        return agent_task_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def agent_environment_specs(self, info: Info, org_id: strawberry.ID) -> list[AgentEnvironmentSpecType]:
        """The org's AgentEnvironmentSpecs, ordered by slug.

        Org-scoped: ``org_id`` must match the caller's active tenant
        (superusers excepted) — the spec carries secret *references* the
        dispatcher resolves at launch, so it must never leak across orgs.
        """
        org_pk = _caller_org_id(info, org_id)
        qs = AgentEnvironmentSpec.objects.filter(organization_id=org_pk, deleted_at__isnull=True).order_by(
            "slug"
        )[:200]
        return [agent_env_spec_to_type(s) for s in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def agent_environment_spec(self, info: Info, slug: str) -> AgentEnvironmentSpecType | None:
        """One AgentEnvironmentSpec by slug, scoped to the caller's org.

        A spec in another org resolves to null (not an error) so the
        surface doesn't leak existence across tenants.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = AgentEnvironmentSpec.objects.filter(
            slug=slug, organization_id=org_pk, deleted_at__isnull=True
        ).first()
        return agent_env_spec_to_type(row) if row is not None else None

    @strawberry.field
    def agent_runtimes(self, info: Info) -> list[AgentRuntimeType]:
        # Platform-level reference data: the public runtime catalog is
        # install-wide (not per-tenant) — the same 12 published images are
        # selectable by every org, so this intentionally escapes
        # @tenant_scoped (same shape as astrolift_provider_plugins /
        # form_field_types). Requires an authenticated caller inline so the
        # catalog doesn't leak to anonymous probes. See EXEMPT entry in
        # test_tenancy_guardrail.py.
        from astrolift_agents.runtime_catalog import catalog_entries

        user = getattr(getattr(info.context, "request", None), "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise GraphQLError("authentication required")
        return [AgentRuntimeType(name=e["name"], image=e["image"]) for e in catalog_entries()]

    @strawberry.field
    def dispatchers(self, info: Info) -> list[DispatcherInstanceType]:
        # Platform-level routing fabric: DispatcherInstances span tenants
        # (one per cluster/cloud/region), so this resolver intentionally
        # escapes @tenant_scoped — same shape as astrolift_provider_plugins.
        # Staff/superuser only; the api_key_hash is never surfaced (the
        # type omits it). See EXEMPT entry in test_tenancy_guardrail.py.
        user = getattr(getattr(info.context, "request", None), "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise GraphQLError("authentication required")
        if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
            raise GraphQLError("staff access required")
        qs = DispatcherInstance.objects.filter(deleted_at__isnull=True).order_by("slug")[:200]
        return [dispatcher_to_type(d) for d in qs]
