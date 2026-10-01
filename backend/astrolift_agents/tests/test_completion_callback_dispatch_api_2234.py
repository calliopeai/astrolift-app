"""The public dispatch mutation registers callbacks before any terminal failure."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, AgentTaskCompletionCallback
from astrolift_agents.schema.mutations import AgentsMutation, RunAstroliftAgentInput
from astrolift_agents.schema.types import AgentTaskCallbackMode
from astrolift_agents.services.task_completion_callbacks import configure_policy, set_callback_secret
from astrolift_agents.tests.test_run_agent_mutation import _agent_workload
from astrolift_agents.tests.test_run_agent_mutation import temporal_recorder as _temporal_recorder
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def temporal_recorder(monkeypatch):
    return _temporal_recorder.__wrapped__(monkeypatch)


@pytest.fixture
def agent(permission_resolver):
    org = Organization.objects.create(name="Callback dispatch", slug="callback-dispatch")
    workload = _agent_workload(org, app_slug="reports", workload_slug="report")
    configure_policy(org.pk, ["hooks.example.org"])
    set_callback_secret(org.pk, "receiver", "callback-api-fixture-signing-key-123456")
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    permission_resolver.grant(Permission.SECRET_READ)
    permission_resolver.grant(Permission.APP_READ)
    return org, workload


def dispatch(agent, **options):
    org, workload = agent
    with tenant_context(TenantContext(organization_id=org.pk)):
        return AgentsMutation().run_astrolift_agent(
            SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            input=RunAstroliftAgentInput(
                agent_slug=workload.slug,
                callback_url="https://hooks.example.org/finished",
                callback_secret_ref="receiver",
                correlation_id="external-report-123",
                **options,
            ),
        )


def test_dispatch_registers_pending_callback_before_workflow_start(agent, temporal_recorder):
    result = dispatch(agent)
    assert result.ok, result.errors
    assert result.data.callback_status == "pending"
    assert result.data.callback_attempts == 0
    task = AgentTask.objects.get(guid=str(result.data.id))
    callback = AgentTaskCompletionCallback.objects.get(task=task)
    assert callback.organization_id == agent[0].pk
    assert callback.mode == "FULL"
    assert callback.correlation_id == "external-report-123"
    assert callback.secret_ref == "receiver"
    assert callback.final_event_id is None
    assert bytes(callback.payload_ciphertext) == b""
    assert task.status == "queued"
    assert [row[0] for row in temporal_recorder] == ["DispatchAgentTaskWorkflow"]


def test_existing_dispatch_can_be_recovered_after_callback_allow_list_is_revoked(agent, temporal_recorder):
    key = str(uuid4())
    first = dispatch(agent, client_request_id=key)
    assert first.ok, first.errors
    configure_policy(agent[0].pk, [])
    recovered = dispatch(agent, client_request_id=key)
    assert recovered.ok, recovered.errors
    assert recovered.data.id == first.data.id
    assert AgentTask.objects.count() == 1
    assert AgentTaskCompletionCallback.objects.count() == 1
    assert len(temporal_recorder) == 1


@pytest.mark.parametrize("changed", ["url", "correlation", "secret", "mode"])
def test_idempotent_dispatch_includes_callback_configuration(agent, temporal_recorder, changed):
    key = str(uuid4())
    first = dispatch(agent, client_request_id=key)
    same = dispatch(agent, client_request_id=key)
    assert first.ok and same.ok
    assert first.data.id == same.data.id
    org, workload = agent
    set_callback_secret(org.pk, "other-receiver", "other-callback-api-fixture-signing-key-123456")
    options = {
        "agent_slug": workload.slug,
        "callback_url": "https://hooks.example.org/finished",
        "callback_secret_ref": "receiver",
        "correlation_id": "external-report-123",
        "client_request_id": key,
    }
    options.update(
        {
            "url": {"callback_url": "https://hooks.example.org/other"},
            "correlation": {"correlation_id": "external-report-456"},
            "secret": {"callback_secret_ref": "other-receiver"},
            "mode": {"callback_mode": AgentTaskCallbackMode.NOTIFY},
        }[changed]
    )
    with tenant_context(TenantContext(organization_id=org.pk)):
        conflict = AgentsMutation().run_astrolift_agent(
            SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            input=RunAstroliftAgentInput(**options),
        )
    assert not conflict.ok
    assert conflict.errors[0].code == "PRECONDITION"
    assert conflict.errors[0].field == "clientRequestId"
    assert AgentTask.objects.count() == 1
    assert AgentTaskCompletionCallback.objects.count() == 1
    assert len(temporal_recorder) == 1


def test_preparation_failure_still_freezes_one_final_callback(agent, temporal_recorder):
    org, workload = agent
    AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Invalid environment",
        slug=workload.slug,
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        env_vars={"ASTROLIFT_TASK_ID": "invalid-control-plane-override"},
    )
    result = dispatch(agent)
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    task = AgentTask.objects.get()
    assert task.status == "failed"
    callback = AgentTaskCompletionCallback.objects.get(task=task)
    assert callback.final_event_id is not None
    assert callback.event_metadata["status"] == "failed"
    assert callback.event_metadata["correlation_id"] == "external-report-123"
    assert callback.generation == 1
    assert bytes(callback.payload_ciphertext)
    assert callback.status == "pending"
    assert not temporal_recorder


def test_callback_dispatch_requires_org_secret_read_before_creating_task(
    agent, permission_resolver, temporal_recorder
):
    permission_resolver.deny(Permission.SECRET_READ)
    result = dispatch(agent)
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert not AgentTask.objects.exists()
    assert not AgentTaskCompletionCallback.objects.exists()
    assert not temporal_recorder


def test_full_callback_requires_result_read_before_creating_task(
    agent, permission_resolver, temporal_recorder
):
    permission_resolver.deny(Permission.APP_READ)
    result = dispatch(agent)
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert not AgentTask.objects.exists()
    assert not AgentTaskCompletionCallback.objects.exists()
    assert not temporal_recorder


def test_notify_callback_can_be_dispatched_without_result_read(agent, permission_resolver, temporal_recorder):
    permission_resolver.deny(Permission.APP_READ)
    result = dispatch(agent, callback_mode=AgentTaskCallbackMode.NOTIFY)
    assert result.ok, result.errors
    callback = AgentTaskCompletionCallback.objects.get()
    assert callback.mode == "NOTIFY"
    assert [row[0] for row in temporal_recorder] == ["DispatchAgentTaskWorkflow"]


@pytest.mark.parametrize(
    "change",
    [
        {"callback_url": "http://hooks.example.org/finished"},
        {"callback_url": "https://unapproved.example.org/finished"},
        {"correlation_id": "x" * 129},
        {"callback_secret_ref": "missing"},
    ],
)
def test_invalid_callback_is_rejected_before_dispatch(agent, temporal_recorder, change):
    org, workload = agent
    options = {
        "callback_url": "https://hooks.example.org/finished",
        "callback_secret_ref": "receiver",
        "correlation_id": "id",
    }
    options.update(change)
    with tenant_context(TenantContext(organization_id=org.pk)):
        result = AgentsMutation().run_astrolift_agent(
            SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            input=RunAstroliftAgentInput(agent_slug=workload.slug, **options),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert not AgentTask.objects.exists()
    assert not temporal_recorder
