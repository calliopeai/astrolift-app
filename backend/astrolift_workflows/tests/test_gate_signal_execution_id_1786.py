"""A gate signal must target the execution the workflow keys on (#1786).

``WorkflowDefinitionRunWorkflow`` stores decisions under ``str(execution.pk)``
(what ``executionId`` exposes) while the same type also exposes ``guid``. A
signal carrying the guid returned ok and the gate waited forever. The mutation
now resolves either spelling and refuses an id that names nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.schema import mutations as m
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(
        context=SimpleNamespace(user=None, request=RequestFactory().get("/app/gql/config/"))
    )


@pytest.fixture
def execution(db):
    org = Organization.objects.create(name="Acme", slug="acme")
    definition = WorkflowDefinition.objects.create(
        name="Weekly",
        slug="weekly",
        organization=org,
        model_label="",
        pattern_kind="pipeline",
        states=[],
        transitions=[],
        is_enabled=True,
    )
    stage = WorkflowStage.objects.create(
        slug="weekly-gate",
        definition=definition,
        order=1,
        kind=WorkflowStage.StageKind.HUMAN_GATE,
    )
    run = WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="WorkflowDefinitionRunWorkflow-9",
        run_id="",
        status=WorkflowRun.Status.RUNNING,
        organization=org,
    )
    return WorkflowStageExecution.objects.create(
        slug="weekly-gate-exec",
        workflow_run=run,
        stage=stage,
        status=WorkflowStageExecution.Status.RUNNING,
    )


def test_resolves_guid_and_pk_and_refuses_unknown(execution):
    assert m._resolve_execution_id(str(execution.guid)) == str(execution.pk)
    assert m._resolve_execution_id(str(execution.pk)) == str(execution.pk)
    assert m._resolve_execution_id(execution.pk) == str(execution.pk)
    assert m._resolve_execution_id("01a0ffff-0000-7000-8000-000000000000") is None
    assert m._resolve_execution_id("999999") is None
    assert m._resolve_execution_id("") is None


def _grant(permission_resolver):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)


def test_gate_signal_with_guid_is_delivered_as_the_pk(execution, permission_resolver, monkeypatch):
    _grant(permission_resolver)
    sent: list[tuple] = []
    monkeypatch.setattr(m, "signal_workflow", lambda *a: sent.append(a) or True)
    with tenant_context(TenantContext(organization_id=execution.workflow_run.organization_id)):
        result = m.TemporalWorkflowsMutation().signal_workflow_instance(
            _info(),
            "WorkflowDefinitionRunWorkflow-9",
            "human_gate_decision",
            {"execution_id": str(execution.guid), "decision": "approved", "note": "ship it"},
        )
    assert result.ok, result.errors
    ((workflow_id, signal_name, payload),) = sent
    assert (workflow_id, signal_name) == ("WorkflowDefinitionRunWorkflow-9", "human_gate_decision")
    assert payload["execution_id"] == str(execution.pk)
    assert payload["decision"] == "approved"


def test_gate_signal_for_an_unknown_execution_is_refused_not_swallowed(
    execution, permission_resolver, monkeypatch
):
    _grant(permission_resolver)
    sent: list[tuple] = []
    monkeypatch.setattr(m, "signal_workflow", lambda *a: sent.append(a) or True)
    with tenant_context(TenantContext(organization_id=execution.workflow_run.organization_id)):
        result = m.TemporalWorkflowsMutation().signal_workflow_instance(
            _info(),
            "WorkflowDefinitionRunWorkflow-9",
            "human_gate_decision",
            {"execution_id": "01a0ffff-0000-7000-8000-000000000000", "decision": "rejected"},
        )
    assert result.ok is False
    assert result.errors[0].field == "payload"
    assert sent == []


def test_other_signals_pass_their_payload_through(permission_resolver, monkeypatch):
    _grant(permission_resolver)
    sent: list[tuple] = []
    monkeypatch.setattr(m, "signal_workflow", lambda *a: sent.append(a) or True)
    with tenant_context(TenantContext(organization_id=1)):
        result = m.TemporalWorkflowsMutation().signal_workflow_instance(
            _info(), "wf-1", "abort", {"reason": "x"}
        )
    assert result.ok
    assert sent == [("wf-1", "abort", {"reason": "x"})]
