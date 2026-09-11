"""Scope resolvers for agent-keyed permission gates (#1731).

Agent *workloads* are workloads like any other -- they belong to an app,
so a gate that names one checks against that app.

Agent tasks retain their recorded project/team scope. Without either,
a live registered-agent definition supplies the app scope; tasks with
no usable owner require an explicit org grant.

Skills, tool definitions, environment specs and their secrets are
org-level objects with no team, project or app of their own. There is no
scope to resolve for those, and inventing one would be worse than the
org check they already run: they are deliberately not covered here.
"""

from __future__ import annotations

from typing import Any

from core.permissions import Permission, PermissionScope, ScopeKind
from core.scope_args import read_arg, read_guid
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _org_scope():
    # A factory miss must not inherit the selected team/project (#1745).
    return PermissionScope(kind=ScopeKind.ORG, id=_org_id() or 0)


def agent_org_scope(_args):
    return _org_scope()


def agent_workload_app_scope(field: str = "agent_slug", permission=Permission.AGENT_READ):
    """Resolve one authorized agent, using the same filter as its handler."""

    def _scope(args):
        from astrolift_agents.visibility import agent_by_slug, app_permission_scope

        slug = read_arg(args, field)
        if not slug:
            return _org_scope()
        workload = agent_by_slug(_org_id(), str(slug), permission)
        if workload is None:
            return _org_scope()
        return app_permission_scope(workload.registered_app, permission) or _org_scope()

    return _scope


def agent_trigger_scope(field: str = "slug"):
    def _scope(args):
        from astrolift_agents.models.workflow_trigger import WorkflowWebhook
        from astrolift_agents.visibility import agent_workloads, app_permission_scope

        hook = (
            WorkflowWebhook.objects.filter(
                slug=str(read_arg(args, field) or ""),
                organization_id=_org_id(),
                agent_definition__in=agent_workloads(_org_id(), Permission.APP_UPDATE),
            )
            .select_related("agent_definition__registered_app")
            .first()
        )
        if hook is None:
            return _org_scope()
        return (
            app_permission_scope(hook.agent_definition.registered_app, Permission.APP_UPDATE) or _org_scope()
        )

    return _scope


def agent_task_scope(field: str = "id", permission=Permission.AGENT_READ):
    """Recorded project/team, else live definition's app, else explicit org."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_guid(args, field)
        if not guid:
            return _org_scope()
        org_id = _org_id()
        if org_id is None:
            return _org_scope()
        from astrolift_agents.visibility import agent_tasks, app_permission_scope
        from astrolift_registry.models import Workload

        task = agent_tasks(org_id, permission).filter(guid=str(guid)).first()
        if task is None:
            return _org_scope()
        if task.project_id:
            project = task.project
            if project.organization_id == org_id and project.deleted_at is None:
                return PermissionScope(kind=ScopeKind.PROJECT, id=project.pk)
            return _org_scope()
        if task.team_id:
            team = task.team
            if team.organization_id == org_id and team.deleted_at is None:
                return PermissionScope(kind=ScopeKind.TEAM, id=team.pk)
            return _org_scope()
        definition = task.agent_definition
        if (
            definition is not None
            and definition.deleted_at is None
            and definition.kind == Workload.Kind.AGENT
        ):
            app = definition.registered_app
            if app.organization_id == org_id and app.deleted_at is None:
                return app_permission_scope(app, permission) or _org_scope()
        return _org_scope()

    return _scope
