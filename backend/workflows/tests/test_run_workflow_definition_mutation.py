"""Legacy reviewed Definition start compatibility (#2236).

This mutation is permission- and tenant-gated, validates the visible
WorkflowDefinition (exists / enabled / has stages), then starts the shared
stage executor (WorkflowDefinitionRunWorkflow) via
the reviewed durable start service — creating the
WorkflowRun mirror + WorkflowInstance. These tests pin that contract without
a real Temporal worker: ``start_workflow_once`` is patched at its canonical
module. Separate real Temporal tests cover the durable executor.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_operations.models import WorkflowRun
from core.permissions import Permission, PermissionDenied
from core.run_input_contract import digest
from core.tenancy import TenantContext, tenant_context
from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage
from workflows.reviewed_starts import definition_revision
from workflows.schema.mutations import Mutation

pytestmark = pytest.mark.django_db

MIN_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
    {"name": "done", "label": "Done", "is_initial": False, "is_final": True},
]
MIN_TRANSITIONS = [{"from_state": "pending", "to_state": "done", "label": "Complete"}]


def _info():
    User = get_user_model()
    user = User.objects.create(username="member@test", email="member@test")
    return SimpleNamespace(context=SimpleNamespace(user=user)), user


def _organization(slug="run-org"):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name=slug, slug=slug)


def _definition(*, organization, slug="wf-run-test", enabled=True, with_stage=True):
    wd = WorkflowDefinition.objects.create(
        name="Run test",
        slug=slug,
        organization=organization,
        model_label="workflows.workflowdefinition",
        pattern_kind=WorkflowDefinition.PatternKind.SINGLE,
        states=MIN_STATES,
        transitions=MIN_TRANSITIONS,
        is_enabled=enabled,
    )
    if with_stage:
        WorkflowStage.objects.create(
            definition=wd,
            order=0,
            kind=WorkflowStage.StageKind.CHECKPOINT,
        )
    return wd


@pytest.fixture
def patched_start(monkeypatch):
    """Patch the canonical Temporal entry point run_service imports locally."""
    calls = []

    def _fake(name, args=None, *, workflow_id=None, **kw):
        calls.append({"name": name, "args": args, "workflow_id": workflow_id})
        return SimpleNamespace(enqueued=True, run_id="test-run-id", workflow_id=workflow_id)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow_once", _fake)
    return calls


def test_happy_path_starts_executor_and_creates_rows(patched_start, permission_resolver):
    org = _organization()
    wd = _definition(organization=org)
    info, _user = _info()
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)

    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=_user.pk)):
        result = Mutation().run_workflow_definition(
            info,
            workflow_slug=wd.slug,
            definition_id=str(wd.guid),
            expected_revision=definition_revision(wd),
            expected_input_schema_digest=digest(wd.input_schema),
            request_id="legacy-reviewed-start",
            confirmed=True,
        )

    assert result.ok is True
    # WorkflowRun mirror created, kind + derived workflow id correct.
    run = WorkflowRun.objects.get(pk=int(result.workflow_run_id))
    assert run.workflow_kind == "WorkflowDefinitionRunWorkflow"
    assert run.workflow_definition_id == wd.pk
    assert run.organization_id == org.pk
    assert run.trigger_kind == "manual"  # a session call (#2152)
    assert result.temporal_workflow_id == f"WorkflowDefinitionRunWorkflow-{run.pk}"
    assert run.workflow_id == result.temporal_workflow_id
    # The executor was actually enqueued with the right id + definition slug.
    assert len(patched_start) == 1
    assert patched_start[0]["name"] == "WorkflowDefinitionRunWorkflow"
    assert patched_start[0]["workflow_id"] == result.temporal_workflow_id
    assert patched_start[0]["args"][0].workflow_definition_slug == wd.slug
    # UI mirror instance created and pointed at the temporal id.
    inst = WorkflowInstance.objects.get(workflow=wd)
    assert inst.temporal_workflow_id == result.temporal_workflow_id
    assert inst.organization_id == org.pk


def test_missing_trigger_permission_is_denied_and_starts_nothing(patched_start, permission_resolver):
    org = _organization()
    wd = _definition(organization=org)
    info, user = _info()

    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        with pytest.raises(PermissionDenied):
            Mutation().run_workflow_definition(info, workflow_slug=wd.slug)

    assert patched_start == []
    assert not WorkflowRun.objects.exists()


def test_unknown_slug_returns_error_no_start(patched_start, permission_resolver):
    org = _organization()
    info, user = _info()
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        result = Mutation().run_workflow_definition(info, workflow_slug="does-not-exist")
    assert result.ok is False
    assert result.errors[0].messages[0].startswith("PRECONDITION:")
    assert patched_start == []


def test_disabled_definition_returns_error(patched_start, permission_resolver):
    org = _organization()
    wd = _definition(organization=org, slug="wf-disabled", enabled=False)
    info, user = _info()
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        result = Mutation().run_workflow_definition(info, workflow_slug=wd.slug)
    assert result.ok is False
    assert result.errors[0].messages[0].startswith("PRECONDITION:")
    assert patched_start == []


def test_definition_with_no_stages_returns_error(patched_start, permission_resolver):
    org = _organization()
    wd = _definition(organization=org, slug="wf-no-stages", with_stage=False)
    info, user = _info()
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        result = Mutation().run_workflow_definition(info, workflow_slug=wd.slug)
    assert result.ok is False
    assert "PRECONDITION" in result.errors[0].messages[0]
    assert patched_start == []
    assert not WorkflowRun.objects.exists()


def test_foreign_org_definition_is_not_runnable(patched_start, permission_resolver):
    caller = _organization("run-caller")
    foreign = _organization("run-foreign")
    wd = _definition(organization=foreign, slug="foreign-workflow")
    info, user = _info()
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)

    with tenant_context(TenantContext(organization_id=caller.pk, actor_user_id=user.pk)):
        result = Mutation().run_workflow_definition(info, workflow_slug=wd.slug)

    assert result.ok is False
    assert "PRECONDITION" in result.errors[0].messages[0]
    assert patched_start == []
    assert not WorkflowRun.objects.exists()
