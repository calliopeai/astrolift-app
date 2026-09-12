"""Scope resolvers for managed-service-keyed permission gates (#1731).

Same contract as ``astrolift_registry.scopes``: turn the object a request
names into the scope its permission check runs against, confined to the
caller's org, and return ``None`` for anything that does not resolve so
the stricter org-scope check stays in place.

A managed service is owned either by one app (``registered_app``, the
app-private case) or by a project (``project``, the shared case), so the
scope it checks against follows whichever owns it.
"""

from __future__ import annotations

from typing import Any

from core.permissions import PermissionScope, ScopeKind
from core.scope_args import read_guid
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def managed_service_scope_by_guid(field: str = "managed_service_id"):
    """Scope on the app -- or, for a shared resource, the project --
    owning the managed service named by ``field`` (a GUID argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_guid(args, field)
        if not guid:
            return None
        org_id = _org_id()
        if org_id is None:
            return None
        # No org column on the row: ownership is via the app or the
        # project, and both carry one. Either side matching confines the
        # lookup to the caller's org.
        from django.db.models import Q

        from astrolift_services.models import ManagedService

        row = (
            ManagedService.objects.filter(
                Q(registered_app__organization_id=org_id) | Q(project__organization_id=org_id),
                guid=str(guid),
                deleted_at__isnull=True,
            )
            .values_list("registered_app_id", "project_id")
            .first()
        )
        if row is None:
            return None
        app_id, project_id = row
        if app_id:
            return PermissionScope(kind=ScopeKind.APP, id=app_id)
        if project_id:
            return PermissionScope(kind=ScopeKind.PROJECT, id=project_id)
        return None

    return _scope


def _app_scope_via(model_label: str, field: str, app_path: str = "registered_app"):
    """Scope on the app owning the row ``field`` names (a GUID)."""

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
            model.objects.filter(guid=str(key), **{f"{app_path}__organization_id": org_id})
            .values_list(f"{app_path}__id", flat=True)
            .first()
        )
        return PermissionScope(kind=ScopeKind.APP, id=app_id) if app_id else None

    return _scope


def secret_bundle_project_scope(field: str = "bundle_id"):
    """Scope on the project owning the secret bundle ``field`` names.

    A project bundle is shared across the project's apps, so the scope
    it checks against is the project -- an APP-scoped binding on one
    consumer is not authority over the bundle itself.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        key = read_guid(args, field)
        if not key:
            return None
        org_id = _org_id()
        if org_id is None:
            return None
        from astrolift_services.models import SecretBundle

        project_id = (
            SecretBundle.objects.filter(guid=str(key), project__organization_id=org_id)
            .values_list("project__id", flat=True)
            .first()
        )
        return PermissionScope(kind=ScopeKind.PROJECT, id=project_id) if project_id else None

    return _scope


def bundle_attachment_app_scope(field: str = "input.attachment_id"):
    """Scope on the app an attached bundle is attached to."""
    return _app_scope_via("astrolift_services.AppSecretBundleRef", field)


def secret_change_proposal_app_scope(field: str = "input.proposal_id"):
    """Scope on the app a secret-change proposal is against."""
    return _app_scope_via("astrolift_services.SecretChangeProposal", field)
