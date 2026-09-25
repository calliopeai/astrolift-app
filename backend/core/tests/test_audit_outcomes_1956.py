"""Both audit writers record what actually happened (#1956, #1968).

* ``@mutation_audit`` read the outcome only from the ``MutationResult``
  dataclass, but nearly every resolver returns ``MutationResultType`` via
  ``gql_failure``, so a refusal was recorded as ALLOW with no error code
  (#1968).
* ``MutationAuditExtension`` recorded a mutation that failed validation as a
  success, filed it under the previous request's action (the thread-local was
  never cleared), and skipped documents not starting with ``mutation`` (#1956).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from core.mutations import AuditEntry, _mutation_action_local, mutation_audit, register_audit_writer
from core.permissions import Permission
from core.schema.audit import MutationAuditLog
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def captured():
    rows: list[AuditEntry] = []
    from core.mutations import _audit_writer as original

    register_audit_writer(rows.append)
    yield rows
    register_audit_writer(original)


@pytest.mark.parametrize(
    ("code", "decision"),
    [
        ("PERMISSION_DENIED", "DENY"),
        ("STEP_UP_REQUIRED", "DENY"),
        ("SECRET_APPROVAL_REQUIRED", "DENY"),
        ("VALIDATION", "ALLOW"),
    ],
)
def test_a_gql_failure_is_recorded_with_its_code(captured, code, decision):
    @mutation_audit(action="probe.fail")
    def resolver():
        return gql_failure(code, "no")

    resolver()

    [entry] = captured
    assert (entry.decision, entry.error_code, entry.error_message) == (decision, code, "no")


def test_a_gql_success_is_an_allow_with_no_error(captured):
    @mutation_audit(action="probe.ok")
    def resolver():
        return gql_success(None)

    resolver()

    [entry] = captured
    assert (entry.decision, entry.error_code) == ("ALLOW", None)


def _run(query, **kwargs):
    from config.schema import schema

    return schema.execute_sync(query, context_value=SimpleNamespace(user=None, request=None), **kwargs)


def test_a_mutation_that_fails_validation_is_not_a_success_and_has_no_stale_action(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org = Organization.objects.create(name="Audit", slug="audit-1956")
    _mutation_action_local.action = "cluster.install_prereqs"  # left over from an earlier request

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _run("mutation Probe { setOrganizationModule(input: {nope: 1}) { ok } }")

    assert result.errors
    log = MutationAuditLog.objects.get()
    assert log.success is False
    assert log.operation != "cluster.install_prereqs"
    assert getattr(_mutation_action_local, "action", None) is None


def test_an_envelope_failure_is_not_a_success(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org = Organization.objects.create(name="Audit", slug="audit-1956b")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _run(
            'mutation { setOrganizationModule(input: {key: "no_such_module", enabled: true}) { ok } }'
        )

    assert result.errors is None, result.errors
    assert result.data["setOrganizationModule"]["ok"] is False
    assert MutationAuditLog.objects.get().success is False


def test_a_mutation_document_with_a_leading_comment_is_audited(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org = Organization.objects.create(name="Audit", slug="audit-1956c")

    with tenant_context(TenantContext(organization_id=org.id)):
        _run(
            '# a comment\nmutation { setOrganizationModule(input: {key: "chat_studio_integration", enabled: true}) { ok } }'
        )

    assert MutationAuditLog.objects.filter(operation="org.module.set").exists()
