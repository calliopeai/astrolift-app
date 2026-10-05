"""Read-only MCP adapters for the native workflow query and manifest contracts."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

from django.core.serializers.json import DjangoJSONEncoder

from core.tenancy import get_current_tenant


def _info(request):
    return SimpleNamespace(context=SimpleNamespace(request=request, user=request.user))


def _payload(value):
    # Serialize declared query types, never ORM objects or their private state.
    return json.loads(json.dumps(dataclasses.asdict(value), cls=DjangoJSONEncoder))


def list_definitions(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    page = WorkflowsQuery().workflow_definitions_page(
        _info(request),
        search=args.get("search"),
        project_id=args.get("project_id"),
        limit=args.get("limit", 50),
        after=args.get("cursor"),
    )
    return _payload(page)


def get_definition(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    reviewed = WorkflowsQuery().workflow_definition_by_id(_info(request), id=args["definition_id"])
    return {"definition": _payload(reviewed) if reviewed is not None else None}


def list_workflows(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    page = WorkflowsQuery().workflows_page(
        _info(request),
        search=args.get("search"),
        limit=args.get("limit", 50),
        after=args.get("cursor"),
    )
    return _payload(page)


def preview_manifest(request, args):
    from astrolift_workflows.schema.manifest import WorkflowManifestQuery

    # Strawberry binds this field to its decorated resolver at runtime.
    preview = cast(Callable[..., object], WorkflowManifestQuery().preview_workflow_manifest)
    return _payload(preview(_info(request), toml=args["toml"]))


def export_manifest(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery
    from workflows.manifest import definition_to_manifest, emit_workflow_manifest
    from workflows.models import WorkflowDefinition

    reviewed = WorkflowsQuery().workflow_definition_by_id(_info(request), id=args["definition_id"])
    if reviewed is None:
        return {"ok": False, "toml": None, "error": "no visible workflow definition with this ID"}
    tenant = get_current_tenant()
    if tenant is None:
        return {"ok": False, "toml": None, "error": "no active organization"}
    # Export the exact authorized ID; a slug lookup could select a different
    # organization's definition or substitute an organization/global template.
    definition = WorkflowDefinition.visible_to_org(tenant.organization_id).get(
        guid=args["definition_id"], deleted_at__isnull=True
    )
    return {"ok": True, "toml": emit_workflow_manifest(definition_to_manifest(definition)), "error": None}
