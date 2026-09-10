"""Scope resolvers for agent-keyed permission gates (#1731).

Two kinds of object live in this module and only one of them has a
sub-org scope at all.

Agent *workloads* are workloads like any other -- they belong to an app,
so a gate that names one checks against that app.

Agent *tasks* record the team and project they ran for, so a gate that
names one checks against the project (or, for a task with no project,
the team).

Skills, tool definitions, environment specs and their secrets are
org-level objects with no team, project or app of their own. There is no
scope to resolve for those, and inventing one would be worse than the
org check they already run: they are deliberately not covered here.
"""

from __future__ import annotations

from typing import Any

from core.permissions import PermissionScope, ScopeKind
from core.scope_args import read_guid
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def agent_workload_app_scope(field: str = "agent_slug"):
    """Scope on the app owning the agent workload ``field`` names.

    An agent is a workload, so this is
    :func:`astrolift_registry.scopes.app_scope_by_workload_slug` under
    the name the agent gates read by.
    """
    from astrolift_registry.scopes import app_scope_by_workload_slug

    return app_scope_by_workload_slug(field)


def agent_task_scope(field: str = "id"):
    """Scope on the project (or team) an agent task ran for."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_guid(args, field)
        if not guid:
            return None
        org_id = _org_id()
        if org_id is None:
            return None
        from astrolift_agents.models import AgentTask

        row = (
            AgentTask.objects.filter(guid=str(guid), organization_id=org_id)
            .values_list("project_id", "team_id")
            .first()
        )
        if row is None:
            return None
        project_id, team_id = row
        if project_id:
            return PermissionScope(kind=ScopeKind.PROJECT, id=project_id)
        if team_id:
            return PermissionScope(kind=ScopeKind.TEAM, id=team_id)
        return None

    return _scope
