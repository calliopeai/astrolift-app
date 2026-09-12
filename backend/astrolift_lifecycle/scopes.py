"""Scope resolvers for lifecycle permission gates (#1731).

Nearly every gate in this module acts on one app, but names it through a
row that belongs to the app rather than by slug: a deployment, an
environment, a custom domain, a deploy token, a run. Bindings stop at
APP, so all of those check against the app that owns the row.

One factory covers them because the shape is always the same -- resolve
a GUID to its owning app, confined to the caller's org, and return
``None`` for anything that does not resolve so the stricter org-scope
check stays in place.
"""

from __future__ import annotations

from typing import Any

from core.permissions import PermissionScope, ScopeKind
from core.scope_args import read_guid
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def app_scope_via(
    model_label: str,
    field: str,
    *,
    app_path: str = "registered_app",
    guid_field: str = "guid",
):
    """Scope on the app owning the row ``field`` names.

    ``model_label`` is ``"<app_label>.<Model>"``; ``app_path`` is the
    query path from that model to :class:`RegisteredApp` (rows hung off
    an environment reach it as ``app_environment__registered_app``).
    ``field`` is a dotted path into the resolver's bound arguments, so a
    mutation carrying its target on an input object passes
    ``"input.id"``.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        key = read_guid(args, field)
        if not key:
            return None
        org_id = _org_id()
        if org_id is None:
            return None
        from django.apps import apps

        model = apps.get_model(model_label)
        app_id = (
            model.objects.filter(
                **{guid_field: str(key), f"{app_path}__organization_id": org_id},
            )
            .values_list(f"{app_path}__id", flat=True)
            .first()
        )
        return PermissionScope(kind=ScopeKind.APP, id=app_id) if app_id else None

    return _scope


def deployment_app_scope(field: str = "input.id"):
    return app_scope_via("astrolift_lifecycle.Deployment", field)


def environment_app_scope(field: str = "input.id"):
    return app_scope_via("astrolift_lifecycle.AppEnvironment", field)


def custom_domain_app_scope(field: str = "input.id"):
    return app_scope_via("astrolift_lifecycle.CustomDomain", field)


def deploy_token_app_scope(field: str = "input.id"):
    return app_scope_via("astrolift_lifecycle.DeployToken", field)


def preview_environment_app_scope(field: str = "input.id"):
    return app_scope_via("astrolift_lifecycle.PreviewEnvironment", field)


def scheduled_job_run_app_scope(field: str = "id"):
    return app_scope_via(
        "astrolift_lifecycle.ScheduledJobRun",
        field,
        app_path="app_environment__registered_app",
    )


def command_run_app_scope(field: str = "id"):
    return app_scope_via("astrolift_lifecycle.CommandRun", field)


def task_run_app_scope(field: str = "id"):
    return app_scope_via(
        "astrolift_lifecycle.TaskRun",
        field,
        app_path="app_environment__registered_app",
    )
