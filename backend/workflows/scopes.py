"""Scope resolvers for workflow-keyed permission gates (#1731).

A ``WorkflowDefinition`` optionally belongs to a project -- the project
packet that owns the repository workflow -- and is otherwise a reusable
org-level template. So a definition-keyed gate checks against that
project when there is one, and falls back to the org check when the
definition is a template, which is the right scope for a shared one.

Workflows, stages and runs all reach a definition, so they resolve
through it.
"""

from __future__ import annotations

from typing import Any

from core.permissions import PermissionScope, ScopeKind
from core.scope_args import read_arg, read_guid
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _project_scope(model_label: str, project_path: str, **lookup: Any) -> PermissionScope | None:
    org_id = _org_id()
    if org_id is None:
        return None
    from django.apps import apps

    model = apps.get_model(model_label)
    project_id = model.objects.filter(**lookup).values_list(f"{project_path}__id", flat=True).first()
    return PermissionScope(kind=ScopeKind.PROJECT, id=project_id) if project_id else None


def definition_scope_by_slug(field: str = "definition_slug"):
    """Scope on the project owning the workflow definition ``field`` names.

    Slugs are unique per org, so the org filter is what makes the lookup
    unambiguous as well as tenant-safe.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        slug = read_arg(args, field)
        org_id = _org_id()
        if not slug or org_id is None:
            return None
        return _project_scope(
            "workflows.WorkflowDefinition",
            "project",
            slug=str(slug),
            organization_id=org_id,
        )

    return _scope


def definition_scope_by_stage_guid(field: str = "stage_guid"):
    """Scope on the project owning the definition a stage belongs to."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_guid(args, field)
        org_id = _org_id()
        if not guid or org_id is None:
            return None
        return _project_scope(
            "workflows.WorkflowStage",
            "definition__project",
            guid=guid,
            definition__organization_id=org_id,
        )

    return _scope


def workflow_scope_by_guid(field: str = "workflow_id"):
    """Scope on the project owning a workflow's definition."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_guid(args, field)
        org_id = _org_id()
        if not guid or org_id is None:
            return None
        return _project_scope(
            "workflows.Workflow",
            "definition__project",
            guid=guid,
            organization_id=org_id,
        )

    return _scope


def workflow_scope_by_slug(field: str = "slug"):
    """Scope on the project owning the workflow ``field`` names."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        slug = read_arg(args, field)
        org_id = _org_id()
        if not slug or org_id is None:
            return None
        return _project_scope(
            "workflows.Workflow",
            "definition__project",
            slug=str(slug),
            organization_id=org_id,
        )

    return _scope


def instance_scope_by_id(field: str = "instance_id"):
    """Scope on the project owning the definition a run belongs to.

    A ``WorkflowInstance`` is addressed by primary key rather than guid,
    and a non-numeric route param must read as absent rather than reach
    the lookup and raise.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        raw = read_arg(args, field)
        org_id = _org_id()
        if raw is None or org_id is None:
            return None
        text = str(raw)
        if not text.isdigit():
            return None
        return _project_scope(
            "workflows.WorkflowInstance",
            "workflow__project",
            pk=int(text),
            organization_id=org_id,
        )

    return _scope
