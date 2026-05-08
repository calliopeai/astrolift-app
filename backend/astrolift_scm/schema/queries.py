"""Read-only SCM queries."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_scm.models import SourceConnection, SshDeployKey
from astrolift_scm.schema.types import (
    SourceConnectionType,
    SshDeployKeyType,
    source_connection_to_type,
    ssh_key_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class ScmQuery:
    @strawberry.field
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_source_connections(
        self, info: Info
    ) -> list[SourceConnectionType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        qs = (
            SourceConnection.objects.filter(organization_id=org_id)
            .order_by("-is_active", "kind", "account_login")[:200]
        )
        return [source_connection_to_type(c) for c in qs]

    @strawberry.field
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def astrolift_ssh_deploy_keys(
        self, info: Info, app_slug: str | None = None
    ) -> list[SshDeployKeyType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        qs = SshDeployKey.objects.filter(
            organization_id=org_id
        ).select_related("registered_app")
        if app_slug == "":
            # Caller asked for org-scoped only.
            qs = qs.filter(registered_app__isnull=True)
        elif app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        qs = qs.order_by("registered_app__slug", "name")
        return [ssh_key_to_type(k) for k in qs[:200]]
