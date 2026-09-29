"""Scope resolvers for agent-keyed permission gates (#1731).

Agent *workloads* are workloads like any other -- they belong to an app,
so a gate that names one checks against that app.

Agent tasks and boxes retain their recorded project/team scope. Without
either, a live registered-agent definition supplies the app scope; rows
with no usable owner require an explicit org grant.

Environment specs are owned by a project or a team, or shared by the org
(#1866): a spec's writes check at its owner, and an org-shared spec's at
the org.

Skills, tool definitions, briefs and org skill repos are the org's
catalog, with no team, project or app of their own, so their gates take
the explicit org scope (``agent_org_scope``), never the selected team.
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


def _owner_scope(row, org_id: int, permission) -> PermissionScope:
    """A task's or box's scope: recorded project, else recorded team, else
    its live agent definition's app, else explicit org. A stale recorded
    owner never falls through to the next one."""
    from astrolift_agents.visibility import app_permission_scope
    from astrolift_registry.models import Workload

    if row.project_id:
        project = row.project
        if project.organization_id == org_id and project.deleted_at is None:
            return PermissionScope(kind=ScopeKind.PROJECT, id=project.pk)
        return _org_scope()
    if row.team_id:
        team = row.team
        if team.organization_id == org_id and team.deleted_at is None:
            return PermissionScope(kind=ScopeKind.TEAM, id=team.pk)
        return _org_scope()
    definition = row.agent_definition
    if definition is not None and definition.deleted_at is None and definition.kind == Workload.Kind.AGENT:
        app = definition.registered_app
        if app.organization_id == org_id and app.deleted_at is None:
            return app_permission_scope(app, permission) or _org_scope()
    return _org_scope()


def agent_task_scope(field: str = "id", permission=Permission.AGENT_READ):
    """Recorded project/team, else live definition's app, else explicit org."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_guid(args, field)
        if not guid:
            return _org_scope()
        org_id = _org_id()
        if org_id is None:
            return _org_scope()
        from astrolift_agents.visibility import agent_tasks

        task = agent_tasks(org_id, permission).filter(guid=str(guid)).first()
        if task is None:
            return _org_scope()
        return _owner_scope(task, org_id, permission)

    return _scope


def agent_box_scope(field: str = "slug", permission=Permission.AGENT_READ):
    """The box's scope, resolved through the rows ``permission`` reaches (#1866).

    Same priority as a task. A box the caller cannot reach, and a miss,
    take the explicit org scope.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope:
        slug = read_arg(args, field)
        org_id = _org_id()
        if not slug or org_id is None:
            return _org_scope()
        from astrolift_agents.visibility import agent_boxes

        box = agent_boxes(org_id, permission).filter(slug=str(slug)).first()
        if box is None:
            return _org_scope()
        return _owner_scope(box, org_id, permission)

    return _scope


def agent_box_ensure_scope(field: str = "input"):
    """Where ``ensureAgentBox`` dispatches (#1866): the named agent's app,
    else the named spec's owner, else the org.

    A box runs in the scope of what launched it, so an image-only box and
    a box on an org-shared spec are org-level boxes and need an org grant.
    Everything resolves through the rows ``agent.dispatch`` reaches; the
    service re-resolves the same rows and checks the spec against the agent.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope:
        org_id = _org_id()
        if org_id is None:
            return _org_scope()
        from astrolift_agents.visibility import (
            agent_by_slug,
            app_permission_scope,
            environment_specs,
            spec_owner_scope,
        )

        agent_slug = read_arg(args, f"{field}.agent_slug")
        if agent_slug:
            workload = agent_by_slug(org_id, str(agent_slug).strip(), Permission.AGENT_DISPATCH)
            if workload is None:
                return _org_scope()
            return app_permission_scope(workload.registered_app, Permission.AGENT_DISPATCH) or _org_scope()
        spec_slug = read_arg(args, f"{field}.environment_spec_slug")
        if spec_slug:
            spec = (
                environment_specs(org_id, Permission.AGENT_DISPATCH)
                .filter(slug=str(spec_slug).strip())
                .first()
            )
            return spec_owner_scope(spec) if spec is not None else _org_scope()
        return _org_scope()

    return _scope


def agent_env_spec_scope(field: str = "slug", permission=Permission.AGENT_ENV_SPEC_UPDATE):
    """The spec's owner scope, resolved through the rows ``permission``
    reaches (#1866). An org-shared spec, one the caller cannot reach, and a
    miss take the explicit org scope."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        slug = read_arg(args, field)
        org_id = _org_id()
        if not slug or org_id is None:
            return _org_scope()
        from astrolift_agents.visibility import (
            check_org_shared_spec_write,
            environment_specs,
            spec_owner_scope,
        )

        spec = environment_specs(org_id, permission).filter(slug=str(slug)).first()
        if spec is not None and spec.team_id is None and spec.project_id is None:
            return check_org_shared_spec_write(org_id, permission)
        return spec_owner_scope(spec) if spec is not None else _org_scope()

    return _scope


def agent_env_spec_owner_scope(field: str = "input"):
    """Where a new spec will be owned (#1866): the named project, else the
    named team, else the org (an org-shared spec). An owner that is not a
    live row of the caller's org takes the explicit org scope."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        org_id = _org_id()
        if org_id is None:
            return _org_scope()
        from astrolift_identity.models import Project, Team

        project_guid = read_guid(args, f"{field}.project_id")
        if project_guid:
            project = Project.objects.filter(guid=project_guid, organization_id=org_id).first()
            return PermissionScope(kind=ScopeKind.PROJECT, id=project.pk) if project else _org_scope()
        team_guid = read_guid(args, f"{field}.team_id")
        if team_guid:
            team = Team.objects.filter(guid=team_guid, organization_id=org_id).first()
            return PermissionScope(kind=ScopeKind.TEAM, id=team.pk) if team else _org_scope()
        if read_arg(args, field) is not None:
            from astrolift_agents.visibility import check_org_shared_spec_write

            return check_org_shared_spec_write(org_id, Permission.AGENT_ENV_SPEC_CREATE)
        return _org_scope()

    return _scope


def agent_project_scope(field: str = "project_id"):
    """The live project a request names, in the caller's org, else the
    explicit org scope."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        guid = read_guid(args, field)
        org_id = _org_id()
        if not guid or org_id is None:
            return _org_scope()
        from astrolift_identity.models import Project

        project = Project.objects.filter(guid=guid, organization_id=org_id).first()
        return PermissionScope(kind=ScopeKind.PROJECT, id=project.pk) if project else _org_scope()

    return _scope
