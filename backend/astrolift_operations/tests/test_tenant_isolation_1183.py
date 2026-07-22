"""Cross-org tenant-isolation regression tests for the operations
mutation + query surface (#1183).

``@tenant_scoped()`` asserts a tenant context exists but does NOT filter
any queryset, and ``@require_permission`` checks the caller's role in
their OWN org. So an operations resolver that fetches its target by guid
without an org clause leaks (and mutates) another tenant's data.

Proven here, each with a cross-org NOT-FOUND / no-leak case and an
in-org success case:

  * ``rotate_outbound_webhook_secret`` — returns a fresh HMAC secret in
    plaintext; a cross-org guid must not hand it over or rotate the
    victim's secret.
  * ``export_audit_events`` — streamed artifact must carry only the
    caller's org rows (bulk cross-org PII exfil otherwise).
  * ``astrolift_audit_events`` — the audit list must be caller-org only.
  * ``astrolift_workflow_runs`` — evidence that WorkflowRun's own
    ``organization`` FK scopes the mirror list.

Real Postgres, no DB mocks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.test import RequestFactory, override_settings
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from astrolift_operations.models import AuditEvent, WebhookSubscription, WorkflowRun
from astrolift_operations.schema.mutations import (
    ExportAuditEventsInput,
    OperationsMutation,
    RotateOutboundWebhookSecretInput,
)
from astrolift_operations.schema.queries import OperationsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(request=None):
    rf = RequestFactory()
    return SimpleNamespace(
        context=SimpleNamespace(
            user=None,
            request=request or rf.get("/app/gql/config/"),
        )
    )


def _mkorg(slug: str) -> Organization:
    return Organization.objects.create(name=f"Org {slug}", slug=slug)


def _mkwebhook(org: Organization, *, secret_hash: str, url: str) -> WebhookSubscription:
    return WebhookSubscription.objects.create(
        organization=org,
        url=url,
        secret_hash=secret_hash,
        events=["deploy.succeeded"],
        is_active=True,
    )


def _mkaudit(org: Organization, action: str) -> AuditEvent:
    return AuditEvent.objects.create(
        organization=org,
        actor_kind="user",
        actor_id="42",
        actor_display="op",
        action=action,
        decision=AuditEvent.Decision.ALLOW,
        target_kind="Team",
        target_id="t-1",
        target_slug="alpha",
        data={"reason": "ok"},
    )


# ======================================================================
# rotate_outbound_webhook_secret — plaintext HMAC secret, org-scoped
# ======================================================================


def test_rotate_outbound_webhook_secret_cross_org_not_found(permission_resolver):
    a = _mkorg("rot-a")
    b = _mkorg("rot-b")
    sub = _mkwebhook(a, secret_hash="ORIGINAL_A_HASH", url="https://a.example/hook")
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)

    with tenant_context(TenantContext(organization_id=b.id)):
        leaked = OperationsMutation().rotate_outbound_webhook_secret(
            _info(),
            RotateOutboundWebhookSecretInput(id=GUID(str(sub.guid))),
        )
    assert leaked.ok is False
    assert leaked.errors[0].code == "NOT_FOUND"
    # No fresh secret disclosed, and A's hash must be untouched.
    assert leaked.data is None
    sub.refresh_from_db()
    assert sub.secret_hash == "ORIGINAL_A_HASH"


def test_rotate_outbound_webhook_secret_same_org_works(permission_resolver):
    a = _mkorg("rot-a")
    sub = _mkwebhook(a, secret_hash="ORIGINAL_A_HASH", url="https://a.example/hook")
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)

    with tenant_context(TenantContext(organization_id=a.id)):
        mine = OperationsMutation().rotate_outbound_webhook_secret(
            _info(),
            RotateOutboundWebhookSecretInput(id=GUID(str(sub.guid))),
        )
    assert mine.ok, mine.errors
    assert mine.data.plaintext_secret
    sub.refresh_from_db()
    assert sub.secret_hash != "ORIGINAL_A_HASH"


# ======================================================================
# export_audit_events — artifact must carry caller-org rows only
# ======================================================================


def test_export_audit_events_is_caller_org_scoped(tmp_path, permission_resolver):
    a = _mkorg("exp-a")
    b = _mkorg("exp-b")
    _mkaudit(a, "a.action.one")
    _mkaudit(a, "a.action.two")
    _mkaudit(b, "b.action.one")
    _mkaudit(b, "b.action.two")
    _mkaudit(b, "b.action.three")
    permission_resolver.grant(Permission.AUDIT_LOG_EXPORT)

    # Caller in B exports: must see exactly B's 3 rows, NOT the 5 total.
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=b.id)):
            b_result = OperationsMutation().export_audit_events(
                _info(),
                ExportAuditEventsInput(format="ndjson"),
            )
    assert b_result.ok, b_result.errors
    assert b_result.data.row_count == 3

    # And A sees only its own 2 rows — symmetric proof of scoping.
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=a.id)):
            a_result = OperationsMutation().export_audit_events(
                _info(),
                ExportAuditEventsInput(format="ndjson"),
            )
    assert a_result.ok, a_result.errors
    assert a_result.data.row_count == 2


# ======================================================================
# astrolift_audit_events — caller-org only (cross-org audit PII)
# ======================================================================


def test_astrolift_audit_events_cross_org_scoped(permission_resolver):
    a = _mkorg("aud-a")
    b = _mkorg("aud-b")
    _mkaudit(a, "a.only")
    _mkaudit(b, "b.only")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)

    with tenant_context(TenantContext(organization_id=b.id)):
        b_rows = OperationsQuery().astrolift_audit_events(_info(), limit=50)
    assert {r.action for r in b_rows} == {"b.only"}

    with tenant_context(TenantContext(organization_id=a.id)):
        a_rows = OperationsQuery().astrolift_audit_events(_info(), limit=50)
    assert {r.action for r in a_rows} == {"a.only"}


# ======================================================================
# astrolift_workflow_runs — evidence WorkflowRun.organization scopes it
# ======================================================================


def test_astrolift_workflow_runs_cross_org_scoped(permission_resolver):
    a = _mkorg("wfr-a")
    b = _mkorg("wfr-b")
    WorkflowRun.objects.create(
        workflow_kind="DeployWorkflow",
        workflow_id="wf-a-1",
        run_id="run-a-1",
        organization=a,
        status=WorkflowRun.Status.RUNNING,
        started_at=timezone.now(),
    )
    WorkflowRun.objects.create(
        workflow_kind="DeployWorkflow",
        workflow_id="wf-b-1",
        run_id="run-b-1",
        organization=b,
        status=WorkflowRun.Status.RUNNING,
        started_at=timezone.now(),
    )
    permission_resolver.grant(Permission.AUDIT_LOG_READ)

    with tenant_context(TenantContext(organization_id=b.id)):
        b_rows = OperationsQuery().astrolift_workflow_runs(_info(), limit=50)
    assert {r.workflow_id for r in b_rows} == {"wf-b-1"}

    with tenant_context(TenantContext(organization_id=a.id)):
        a_rows = OperationsQuery().astrolift_workflow_runs(_info(), limit=50)
    assert {r.workflow_id for r in a_rows} == {"wf-a-1"}
