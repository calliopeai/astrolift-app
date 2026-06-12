"""Read-only queries for the agent platform.

Skill + ToolDef registry
------------------------
* ``skills(agentType?, scaffoldingTag?, includeGlobal?)`` — list the
  caller's org skills plus the platform-global catalog, newest-edit
  first. Optional filters narrow by target agent runtime and by a
  single scaffolding tag.
* ``skill(slug)`` — a single skill by slug, resolved against the org
  catalog first and the global catalog second.

Both resolvers gate on ``Permission.SKILL_READ`` and carry
``@tenant_scoped`` so a tenant context is required. ``@tenant_scoped``
only *asserts* a context exists ([[tenant_scoped_asserts_not_filters]]);
the actual scoping is the inline ``organization_id`` / ``is_global``
filter in the resolver body, so global skills (org=None) stay visible
to every org without leaking one org's private skills to another.

AgentTask
---------
* ``agentTasks(status?)`` — list org-scoped tasks, most-recently-created
  first. Optional ``status`` filter narrows by lifecycle state.
* ``agentTask(id)`` — single task by GUID.

Brief
-----
* ``agentBriefs(status?)`` — list org-scoped briefs, newest first.
* ``agentBrief(id)`` — single brief by GUID.

AgentEnvironmentSpec
--------------------
* ``agentEnvironmentSpecs()`` — list active env specs for the org.
* ``agentEnvironmentSpec(slug)`` — single spec by slug.
"""

from __future__ import annotations

import strawberry
from django.db.models import Q
from strawberry.types import Info

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Brief, Skill
from astrolift_agents.schema.types import (
    AgentEnvironmentSpecType,
    AgentTaskType,
    BriefType,
    SkillType,
    agent_environment_spec_to_type,
    agent_task_to_type,
    brief_to_type,
    skill_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

_LIST_LIMIT = 200


def _org_or_global(org_id: int) -> Q:
    """Rows the caller may read: their own org's skills + global ones."""
    return Q(organization_id=org_id) | Q(is_global=True)


@strawberry.type
class AgentsQuery:
    @strawberry.field
    @require_permission(Permission.SKILL_READ)
    @tenant_scoped()
    def skills(
        self,
        info: Info,
        agent_type: str | None = None,
        scaffolding_tag: str | None = None,
        include_global: bool = True,
    ) -> list[SkillType]:
        tenant = get_current_tenant()
        if include_global:
            scope = _org_or_global(tenant.organization_id)
        else:
            scope = Q(organization_id=tenant.organization_id)
        qs = Skill.objects.filter(scope, is_active=True)
        if agent_type:
            # "any"/"" skills are runtime-agnostic and always match a
            # specific-runtime filter so a claude query still surfaces a
            # shared skill.
            qs = qs.filter(Q(agent_type=agent_type) | Q(agent_type="") | Q(agent_type="any"))
        if scaffolding_tag:
            qs = qs.filter(scaffolding_tags__contains=[scaffolding_tag])
        qs = qs.prefetch_related("tool_defs").order_by("-updated_at")[:_LIST_LIMIT]
        return [skill_to_type(s) for s in qs]

    @strawberry.field
    @require_permission(Permission.SKILL_READ)
    @tenant_scoped()
    def skill(
        self,
        info: Info,
        slug: str,
    ) -> SkillType | None:
        tenant = get_current_tenant()
        skill = (
            Skill.objects.filter(_org_or_global(tenant.organization_id), slug=slug)
            .prefetch_related("tool_defs")
            # Prefer the org's own skill over a global of the same slug.
            .order_by("organization_id")
            .first()
        )
        if skill is None:
            return None
        return skill_to_type(skill)

    # -----------------------------------------------------------------------
    # AgentTask queries
    # -----------------------------------------------------------------------

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def agent_tasks(
        self,
        info: Info,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AgentTaskType]:
        """List agent tasks for the current org, newest-created first."""
        tenant = get_current_tenant()
        qs = AgentTask.objects.filter(
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).order_by("-created_at")
        if status:
            qs = qs.filter(status=status)
        return [agent_task_to_type(t) for t in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def agent_task(
        self,
        info: Info,
        id: str,
    ) -> AgentTaskType | None:
        """Single agent task by GUID, scoped to the current org."""
        tenant = get_current_tenant()
        task = AgentTask.objects.filter(
            guid=id,
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        return agent_task_to_type(task) if task else None

    # -----------------------------------------------------------------------
    # Brief queries
    # -----------------------------------------------------------------------

    @strawberry.field
    @require_permission(Permission.SKILL_READ)
    @tenant_scoped()
    def agent_briefs(
        self,
        info: Info,
        status: str | None = None,
        limit: int = 100,
    ) -> list[BriefType]:
        """List briefs for the current org, newest-created first."""
        tenant = get_current_tenant()
        qs = Brief.objects.filter(
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).order_by("-created_at")
        if status:
            qs = qs.filter(status=status)
        return [brief_to_type(b) for b in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.SKILL_READ)
    @tenant_scoped()
    def agent_brief(
        self,
        info: Info,
        id: str,
    ) -> BriefType | None:
        """Single brief by GUID, scoped to the current org."""
        tenant = get_current_tenant()
        brief = Brief.objects.filter(
            guid=id,
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        return brief_to_type(brief) if brief else None

    # -----------------------------------------------------------------------
    # AgentEnvironmentSpec queries
    # -----------------------------------------------------------------------

    @strawberry.field
    @require_permission(Permission.AGENT_ENV_SPEC_READ)
    @tenant_scoped()
    def agent_environment_specs(
        self,
        info: Info,
        limit: int = 100,
    ) -> list[AgentEnvironmentSpecType]:
        """List active environment specs for the current org."""
        tenant = get_current_tenant()
        qs = AgentEnvironmentSpec.objects.filter(
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).order_by("name")
        return [agent_environment_spec_to_type(s) for s in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.AGENT_ENV_SPEC_READ)
    @tenant_scoped()
    def agent_environment_spec(
        self,
        info: Info,
        slug: str,
    ) -> AgentEnvironmentSpecType | None:
        """Single environment spec by slug, scoped to the current org."""
        tenant = get_current_tenant()
        spec = AgentEnvironmentSpec.objects.filter(
            slug=slug,
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        return agent_environment_spec_to_type(spec) if spec else None
