"""MutationAuditExtension records the org whose session ran the mutation (#1955).

The variables alone cannot say whose row it is, so tenant-facing readers
filter on this column; a row written without a tenant carries none.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from core.permissions import Permission
from core.schema.audit import MutationAuditLog
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

SET_MODULE = (
    'mutation { setOrganizationModule(input: {key: "chat_studio_integration", enabled: true}) { ok } }'
)


def test_row_carries_the_tenant_org(permission_resolver):
    from config.schema import schema

    permission_resolver.grant(Permission.ORG_UPDATE)
    org = Organization.objects.create(name="Audit Org", slug="audit-org-1955")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = schema.execute_sync(SET_MODULE, context_value=SimpleNamespace(user=None, request=None))

    assert result.errors is None, result.errors
    assert MutationAuditLog.objects.get(operation="org.module.set").organization_id == org.id


def test_row_written_without_a_tenant_has_no_org():
    """The token is the credential on this public mutation, so it runs
    without a tenant."""
    from config.schema import schema

    result = schema.execute_sync(
        'mutation { approveDeploymentByToken(input: {token: "not-a-token"}) { ok } }',
        context_value=SimpleNamespace(user=None, request=None),
    )

    assert result.errors is None, result.errors
    assert MutationAuditLog.objects.get(operation="deployment.approve_by_token").organization_id is None
