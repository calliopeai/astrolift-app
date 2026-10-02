from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
from config.schema import schema
from core.permissions import Permission
from core.run_input_contract import digest
from core.schema.audit import MutationAuditLog
from core.tenancy import TenantContext, tenant_context
from workflows.models import WorkflowDefinition, WorkflowDefinitionStart, WorkflowStage
from workflows.reviewed_starts import definition_revision

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def configuration(settings, monkeypatch):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    monkeypatch.setattr("constance.settings.DATABASE_CACHE_BACKEND", None)
    settings.ASTROLIFT_TEMPORAL_ENABLED = False


@pytest.fixture
def world(permission_resolver):
    org = Organization.objects.create(name="Reviewed API", slug="reviewed-api")
    user = get_user_model().objects.create_user(username="reviewed-api-actor")
    definition = WorkflowDefinition.objects.create(
        name="Reviewed API workflow", slug="reviewed-api-workflow", organization=org, model_label=""
    )
    WorkflowStage.objects.create(definition=definition, slug="reviewed-api-stage", order=0, kind="checkpoint")
    permission_resolver.grant(Permission.WORKFLOW_READ)
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    return SimpleNamespace(org=org, user=user, definition=definition)


def execute(world, query, variables=None):
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        return schema.execute_sync(
            query, variable_values=variables, context_value=SimpleNamespace(user=world.user, request=None)
        )


REVIEW = "query($id:GUID!){workflowDefinitionById(id:$id){guid revision definition{guid isEnabled organizationGuid} inputContract{schema digest supported acceptsInputs supportsSimpleForm fields{name kind required hasDefault default enumValues constraints sensitive simple}}}}"
START = "mutation($input:StartWorkflowDefinitionInput!){startWorkflowDefinition(input:$input){ok errors{code message} data{id requestId definitionId organizationId executionId temporalWorkflowId temporalRunId dispatchStatus}}}"


def test_exact_review_has_explicit_no_input_contract(world):
    result = execute(world, REVIEW, {"id": str(world.definition.guid)})
    assert result.errors is None
    review = result.data["workflowDefinitionById"]
    assert review["guid"] == str(world.definition.guid)
    assert review["revision"] == definition_revision(world.definition)
    assert review["inputContract"]["acceptsInputs"] is False
    assert review["inputContract"]["fields"] == []
    assert review["inputContract"]["supported"] is True


def test_confirmation_precedes_durable_creation_and_uncertain_result_reconciles(world):
    data = {
        "definitionId": str(world.definition.guid),
        "expectedRevision": definition_revision(world.definition),
        "expectedInputSchemaDigest": digest(world.definition.input_schema),
        "requestId": "api-stable-key",
    }
    result = execute(world, START, {"input": data})
    assert result.errors is None
    assert result.data["startWorkflowDefinition"]["errors"][0]["code"] == "PRECONDITION"
    assert not WorkflowDefinitionStart.objects.exists()
    data["confirmed"] = True
    first = execute(world, START, {"input": data}).data["startWorkflowDefinition"]
    assert first["ok"] is False
    assert first["data"]["dispatchStatus"] == "uncertain"
    assert first["data"]["executionId"]
    assert first["data"]["temporalRunId"] is None
    second = execute(world, START, {"input": data}).data["startWorkflowDefinition"]
    assert second["data"]["executionId"] == first["data"]["executionId"]
    query = "query($key:String!){workflowDefinitionStartRequest(requestId:$key){executionId requestId definitionId dispatchStatus}}"
    recovered = execute(world, query, {"key": "api-stable-key"})
    assert recovered.errors is None
    assert recovered.data["workflowDefinitionStartRequest"]["executionId"] == first["data"]["executionId"]


def test_sensitive_input_validation_and_audit_do_not_leak_marker(world, caplog, settings):
    world.definition.input_schema = {
        "type": "object",
        "properties": {"label": {"type": "string", "enum": ["permitted"]}},
        "required": ["label"],
        "additionalProperties": False,
    }
    world.definition.save()
    marker = "PRIVATE-WORKFLOW-INPUT-2236"
    data = {
        "definitionId": str(world.definition.guid),
        "expectedRevision": definition_revision(world.definition),
        "expectedInputSchemaDigest": digest(world.definition.input_schema),
        "requestId": "private-validation",
        "confirmed": True,
        "inputs": {"label": marker},
    }
    result = execute(world, START, {"input": data})
    assert result.errors is None
    envelope = result.data["startWorkflowDefinition"]
    assert envelope["errors"][0]["code"] == "VALIDATION"
    assert marker not in str(envelope)
    assert marker not in caplog.text
    assert marker not in str(list(MutationAuditLog.objects.values("variables", "errors")))
    from core.tests.test_secret_leak_channels_1920 import _log_request

    settings.DEBUG = True
    logged = _log_request(START, {"input": data}, {"data": {"startWorkflowDefinition": envelope}})
    assert marker not in logged


def test_unsupported_schema_read_exposes_handoff_instead_of_false_empty_inputs(world):
    world.definition.input_schema = {
        "type": "object",
        "properties": {"regex": {"type": "string", "pattern": "a+"}},
    }
    world.definition.save()
    result = execute(world, REVIEW, {"id": str(world.definition.guid)})
    assert result.errors is None
    assert result.data["workflowDefinitionById"]["inputContract"]["supported"] is False
    assert result.data["workflowDefinitionById"]["inputContract"]["schema"] is None
