"""Scope resolvers for lifecycle permission gates (#1731).

Nearly every gate in this module acts on one app, but names it through a
row that belongs to the app rather than by slug: a deployment, an
environment, a custom domain, a deploy token, a run. Bindings stop at
APP, so all of those check against the app that owns the row.

One factory covers them because the shape is always the same -- resolve
a GUID to its live owning app, confined to the caller's org. Unresolved
owners take explicit organization scope, independent of selected headers.
"""

from __future__ import annotations

from typing import Any

from astrolift_lifecycle.visibility import live_lifecycle_rows
from astrolift_registry.scopes import _app_scope, _credential_scope, app_scope_by_slug, registry_org_scope
from core.permissions import Permission
from core.scope_args import read_arg, read_guid
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
    permission: Permission | None = None,
):
    """Scope on the app owning the row ``field`` names.

    ``model_label`` is ``"<app_label>.<Model>"``; ``app_path`` is the
    query path from that model to :class:`RegisteredApp` (rows hung off
    an environment reach it as ``app_environment__registered_app``).
    ``field`` is a dotted path into the resolver's bound arguments, so a
    mutation carrying its target on an input object passes
    ``"input.id"``.
    """

    def _scope(args: dict[str, Any]):
        key = read_guid(args, field)
        if not key:
            return _credential_scope(registry_org_scope(), permission)
        org_id = _org_id()
        if org_id is None:
            return _credential_scope(registry_org_scope(), permission)
        from django.apps import apps

        model = apps.get_model(model_label)
        app_id = (
            live_lifecycle_rows(model.objects.all(), app_path=app_path)
            .filter(
                **{guid_field: str(key), f"{app_path}__organization_id": org_id},
            )
            .values_list(f"{app_path}__id", flat=True)
            .first()
        )
        return (
            _app_scope(pk=app_id, permission=permission)
            if app_id
            else _credential_scope(registry_org_scope(), permission)
        )

    return _scope


def deployment_app_scope(field: str = "input.id", *, permission=None):
    return app_scope_via("astrolift_lifecycle.Deployment", field, permission=permission)


def environment_app_scope(field: str = "input.id", *, permission=None):
    return app_scope_via("astrolift_lifecycle.AppEnvironment", field, permission=permission)


def custom_domain_app_scope(field: str = "input.id", *, permission=None):
    return app_scope_via("astrolift_lifecycle.CustomDomain", field, permission=permission)


def deploy_token_app_scope(field: str = "input.id", *, permission=None):
    return app_scope_via("astrolift_lifecycle.DeployToken", field, permission=permission)


def preview_environment_app_scope(field: str = "input.id", *, permission=None):
    return app_scope_via("astrolift_lifecycle.PreviewEnvironment", field, permission=permission)


def scheduled_job_run_app_scope(field: str = "id", *, permission=None):
    return app_scope_via(
        "astrolift_lifecycle.ScheduledJobRun",
        field,
        app_path="app_environment__registered_app",
        permission=permission,
    )


def command_run_app_scope(field: str = "id", *, permission=None):
    return app_scope_via("astrolift_lifecycle.CommandRun", field, permission=permission)


def task_run_app_scope(field: str = "id", *, permission=None):
    return app_scope_via(
        "astrolift_lifecycle.TaskRun",
        field,
        app_path="workload__registered_app",
        permission=permission,
    )


def live_app_scope(field: str = "app_slug", *, permission=None):
    """Scope on the live app named by slug, for the WebSocket surfaces
    that stream from or shell into one: the log subscriptions and the exec
    relay (#1866).

    Resolves the same row those handlers act on: the live app with this
    slug in the caller's org. A miss is an explicit org scope, not
    ``None``: ``None`` runs the targetless check, where a selected team
    stands in for the target (#1745), and a stream must never be
    authorized by a team the app does not belong to.
    """

    return app_scope_by_slug(field, permission=permission)


def deregister_workflow_scope(args):
    from uuid import UUID

    key = read_arg(args, "input.workflow_id") or ""
    prefix = "DeregisterAppWorkflow-"
    try:
        guid = UUID(key[len(prefix) :]) if key.startswith(prefix) else None
    except (ValueError, TypeError, AttributeError):
        guid = None
    if guid is None:
        return _credential_scope(registry_org_scope(), Permission.APP_DELETE)
    return _app_scope(guid=guid, permission=Permission.APP_DELETE)
