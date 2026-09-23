"""MutationAuditExtension must not crash on a variable-less mutation (#1882).

A GraphQL mutation sent with inline literals and no `variables` key leaves
`request.variables` as `None`. `_redact(None)` returned `None` unchanged, and
`MutationAuditLog.variables` is NOT NULL — `JSONField(default=dict)` only
backfills an *omitted* kwarg, not an explicit `None` — so the INSERT raised
`IntegrityError`. The extension's `except Exception` swallowed that and only
logged a warning, but the failed INSERT had already poisoned the surrounding
transaction: the next query in the same transaction raised
`TransactionManagementError`. This module reproduces the exact repro from the
issue (`setOrganizationModule` with no variables) through the real schema.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from core.permissions import Permission
from core.schema.audit import MutationAuditLog
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def test_mutation_without_variables_is_audited_as_an_empty_dict(permission_resolver):
    from config.schema import schema

    permission_resolver.grant(Permission.ORG_UPDATE)
    org = Organization.objects.create(name="Audit Org", slug="audit-novars-org")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = schema.execute_sync(
            'mutation { setOrganizationModule(input: {key: "chat_studio_integration", enabled: true}) { ok } }',
            context_value=SimpleNamespace(user=None, request=None),
        )

    assert result.errors is None, result.errors
    assert result.data["setOrganizationModule"]["ok"] is True

    log = MutationAuditLog.objects.get(operation="org.module.set")
    assert log.variables == {}
    assert log.success is True

    # The bug poisoned the surrounding transaction: the failed INSERT left
    # every later query in this test raising TransactionManagementError.
    # A follow-up query has to still work.
    assert MutationAuditLog.objects.count() == 1


def test_mutation_with_explicit_variables_still_redacts_and_records_them(permission_resolver):
    """Companion to the fix above: a mutation that DOES send variables must
    keep recording them (not silently collapse everyone to `{}`)."""
    from config.schema import schema

    permission_resolver.grant(Permission.ORG_UPDATE)
    org = Organization.objects.create(name="Audit Org 2", slug="audit-withvars-org")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = schema.execute_sync(
            "mutation($input: SetOrganizationModuleInput!) { "
            "setOrganizationModule(input: $input) { ok } }",
            variable_values={"input": {"key": "chat_studio_integration", "enabled": True}},
            context_value=SimpleNamespace(user=None, request=None),
        )

    assert result.errors is None, result.errors
    log = MutationAuditLog.objects.get(operation="org.module.set")
    assert log.variables == {"input": {"key": "chat_studio_integration", "enabled": True}}
