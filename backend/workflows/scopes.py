"""Scope resolvers for workflow-keyed permission gates (#1731, #1965).

A ``WorkflowDefinition`` optionally belongs to a project -- the project
packet that owns the repository workflow. Configured workflows, stages
and runs all reach a definition, so they resolve through it, and a run
that records an app (deploys, previews and the other app operations)
resolves to that app first.

Everything else belongs to the organization: a definition without a
project, a platform template, a run that records neither owner, and any
key that does not name a live row in the caller's organization. Those
resolve to an explicit org scope. Returning ``None`` instead would fall
back to the caller's selected team or project (#1743), so a team token
or an ``X-Astrolift-Team`` header would authorize any object in the org
(#1965). A recorded owner that is stale -- deleted, or in another org --
resolves to the org as well, never to a different owner, which keeps
org-level access to historical runs.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q

from core.permissions import Permission, PermissionScope, ScopeKind, granted_scopes
from core.scope_args import read_arg, read_guid
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def org_scope(org_id: int | None) -> PermissionScope:
    return PermissionScope(kind=ScopeKind.ORG, id=org_id or 0)


def project_scope(project, org_id: int | None) -> PermissionScope:
    if project is not None and project.organization_id == org_id and project.deleted_at is None:
        return PermissionScope(kind=ScopeKind.PROJECT, id=project.pk)
    return org_scope(org_id)


def definition_scope(definition, org_id: int | None) -> PermissionScope:
    """A definition's project; :func:`project_scope` confines it to the org."""
    return project_scope(definition.project if definition is not None else None, org_id)


def run_scope(run, org_id: int | None) -> PermissionScope:
    """An ``astrolift_operations.WorkflowRun``'s owner: its app, else its definition's project."""
    if run.registered_app_id is not None:
        app = run.registered_app
        if app.organization_id == org_id and app.deleted_at is None:
            return PermissionScope(kind=ScopeKind.APP, id=app.pk)
        return org_scope(org_id)
    return definition_scope(run.workflow_definition, org_id)


def workflow_run_scope(workflow_id: str, org_id: int | None, run_id: str | None = None) -> PermissionScope:
    """Scope of the run a Temporal workflow id names in the caller's org.

    The operations ``WorkflowRun`` mirror records the app, so it is read
    first; a tier-3 ``WorkflowInstance`` supplies the definition otherwise.
    """
    if not workflow_id or org_id is None:
        return org_scope(org_id)
    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowInstance

    runs = WorkflowRun.objects.filter(workflow_id=workflow_id, organization_id=org_id)
    if run_id is not None:
        runs = runs.filter(run_id=run_id)
    run = runs.select_related("registered_app", "workflow_definition__project").order_by("-pk").first()
    if run is not None:
        return run_scope(run, org_id)
    instance = (
        WorkflowInstance.objects.filter(
            temporal_workflow_id=workflow_id, organization_id=org_id, deleted_at__isnull=True
        )
        .select_related("workflow__project")
        .order_by("-pk")
        .first()
    )
    return definition_scope(instance.workflow, org_id) if instance is not None else org_scope(org_id)


def workflow_run_scope_by_id(field: str = "workflow_id", run_field: str | None = None):
    """Scope on the run a Temporal workflow id (and optionally run id) names."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        run_id = read_arg(args, run_field) if run_field else None
        return workflow_run_scope(
            str(read_arg(args, field) or ""), _org_id(), None if run_id is None else str(run_id)
        )

    return _scope


def execution_scope_by_id(field: str = "execution_id"):
    def _scope(args: dict[str, Any]) -> PermissionScope:
        from astrolift_workflows.execution_controls import find_execution

        org_id = _org_id()
        run = find_execution(org_id, str(read_arg(args, field) or ""))
        return run_scope(run, org_id) if run is not None else org_scope(org_id)

    return _scope


def definition_scope_by_slug(field: str = "definition_slug", *, enabled_only: bool = False):
    """Scope on the definition ``field`` names.

    Handlers resolve the org's own live definition before a platform
    template with the same slug, so this does too; the template, and a
    miss, are org-level. ``enabled_only`` matches a handler that skips a
    disabled definition of the org's and runs the template instead.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope:
        from workflows.models import WorkflowDefinition

        slug = read_arg(args, field)
        org_id = _org_id()
        if not slug or org_id is None:
            return org_scope(org_id)
        rows = WorkflowDefinition.objects.filter(
            slug=str(slug), organization_id=org_id, deleted_at__isnull=True
        )
        if enabled_only:
            rows = rows.filter(is_enabled=True)
        return definition_scope(rows.select_related("project").first(), org_id)

    return _scope


def definition_scope_by_stage_guid(field: str = "stage_guid"):
    """Scope on the definition a stage belongs to."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        from workflows.models import WorkflowStage

        guid = read_guid(args, field)
        org_id = _org_id()
        if not guid or org_id is None:
            return org_scope(org_id)
        stage = (
            WorkflowStage.objects.filter(
                guid=guid, deleted_at__isnull=True, definition__organization_id=org_id
            )
            .select_related("definition__project")
            .first()
        )
        return definition_scope(stage.definition, org_id) if stage is not None else org_scope(org_id)

    return _scope


def _workflow_scope(org_id: int | None, **lookup: Any) -> PermissionScope:
    from workflows.models import Workflow

    workflow = (
        Workflow.objects.filter(organization_id=org_id, deleted_at__isnull=True, **lookup)
        .select_related("definition__project")
        .first()
    )
    return definition_scope(workflow.definition, org_id) if workflow is not None else org_scope(org_id)


def workflow_scope_by_guid(field: str = "workflow_id"):
    """Scope on a configured workflow's definition."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        guid = read_guid(args, field)
        org_id = _org_id()
        if not guid or org_id is None:
            return org_scope(org_id)
        return _workflow_scope(org_id, guid=guid)

    return _scope


def workflow_scope_by_slug(field: str = "slug"):
    """Scope on the definition of the configured workflow ``field`` names."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        slug = read_arg(args, field)
        org_id = _org_id()
        if not slug or org_id is None:
            return org_scope(org_id)
        return _workflow_scope(org_id, slug=str(slug))

    return _scope


def instance_scope_by_id(field: str = "instance_id"):
    """Scope on the definition a tier-3 ``WorkflowInstance`` runs.

    An instance is addressed by primary key rather than guid, and a
    non-numeric route param must read as absent rather than reach the
    lookup and raise.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope:
        from workflows.models import WorkflowInstance

        raw = read_arg(args, field)
        org_id = _org_id()
        text = "" if raw is None else str(raw)
        if not text.isdigit() or org_id is None:
            return org_scope(org_id)
        instance = (
            WorkflowInstance.objects.filter(pk=int(text), organization_id=org_id)
            .select_related("workflow__project")
            .first()
        )
        return definition_scope(instance.workflow, org_id) if instance is not None else org_scope(org_id)

    return _scope


# ---- Row filters for the any-scope collection gates -------------------


def covered_project_ids(org_id: int | None, permission: Permission):
    """Projects in the org the caller holds ``permission`` on, as a subquery.

    ``None`` means the grant is org-wide and nothing needs narrowing.
    Project-less and template rows are org-level, so a narrowed list
    leaves them out.
    """
    from astrolift_identity.models import Project
    from astrolift_identity.scope_visibility import visible_projects

    if granted_scopes(get_current_tenant(), permission).org:
        return None
    return visible_projects(Project.objects.filter(organization_id=org_id), permission).values("pk")


def visible_runs(qs, org_id: int | None, permission: Permission):
    """Narrow ``WorkflowRun`` rows to the runs whose owner the caller covers.

    Same precedence as :func:`run_scope`: a run that records an app is
    covered through that app alone.
    """
    from astrolift_identity.scope_visibility import visible_apps
    from astrolift_registry.models import RegisteredApp

    projects = covered_project_ids(org_id, permission)
    if projects is None:
        return qs
    apps = visible_apps(RegisteredApp.objects.filter(organization_id=org_id), permission)
    return qs.filter(
        Q(registered_app_id__in=apps.values("pk"))
        | Q(registered_app__isnull=True, workflow_definition__project_id__in=projects)
    )
