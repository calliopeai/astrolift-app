"""Tenant boundaries, public contract and secret-safe completion callback APIs."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from graphql import parse

from astrolift_agents.models import AgentTask, AgentTaskCallbackPolicy, AgentTaskCompletionCallback
from astrolift_agents.schema.mutations import AgentsMutation, RunAstroliftAgentInput
from astrolift_agents.schema.types import AgentTaskCallbackMode, agent_tasks_to_types
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Organization, Team
from config.schema import schema
from core.permissions import Permission, PermissionScope, ScopeKind
from core.schema.audit import MutationAuditLog, redact_operation_variables
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def configuration_cache(settings, monkeypatch):
    import constance.settings

    settings.CONSTANCE_DATABASE_CACHE_BACKEND = None
    monkeypatch.setattr(constance.settings, "DATABASE_CACHE_BACKEND", None)


@pytest.fixture
def org():
    return Organization.objects.create(name="Callback API", slug="callback-api")


def execute(org, query, variables=None):
    with tenant_context(TenantContext(organization_id=org.pk)):
        return schema.execute_sync(
            query,
            variable_values=variables,
            context_value=SimpleNamespace(user=None, request=None),
        )


def test_input_contract_has_full_default_and_no_literal_signing_key():
    inputs = schema._schema.get_type("RunAstroliftAgentInput").fields
    assert {"callbackUrl", "callbackSecretRef", "correlationId", "callbackMode"} <= inputs.keys()
    assert "callbackSecret" not in inputs
    assert inputs["callbackMode"].default_value == AgentTaskCallbackMode.FULL
    assert RunAstroliftAgentInput(agent_slug="report").callback_mode == AgentTaskCallbackMode.FULL
    assert set(schema._schema.get_type("AgentTaskCallbackMode").values) == {"FULL", "NOTIFY"}


def test_policy_read_is_org_scoped_and_empty_until_configured(org, permission_resolver):
    permission_resolver.grant(Permission.ORG_READ, scope=PermissionScope(kind=ScopeKind.ORG, id=org.pk))
    query = "{ agentTaskCallbackPolicy { allowedHosts } }"
    result = execute(org, query)
    assert result.errors is None
    assert result.data["agentTaskCallbackPolicy"] == {"allowedHosts": []}
    other = Organization.objects.create(name="Other callback", slug="other-callback")
    AgentTaskCallbackPolicy.objects.create(organization=other, allowed_hosts=["other.example.org"])
    AgentTaskCallbackPolicy.objects.create(organization=org, allowed_hosts=["*.example.org"])
    assert execute(org, query).data["agentTaskCallbackPolicy"]["allowedHosts"] == ["*.example.org"]


def test_policy_write_normalizes_hosts_and_invalid_policy_keeps_previous_value(org, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    query = "mutation($hosts:[String!]!){configureAgentTaskCallbacks(allowedHosts:$hosts){ok errors{code message} data{allowedHosts}}}"
    result = execute(org, query, {"hosts": ["HOOKS.EXAMPLE.ORG.", "*.example.org", "hooks.example.org"]})
    assert result.errors is None
    assert result.data["configureAgentTaskCallbacks"]["data"] == {
        "allowedHosts": ["hooks.example.org", "*.example.org"]
    }
    denied = execute(org, query, {"hosts": ["*"]}).data["configureAgentTaskCallbacks"]
    assert not denied["ok"]
    assert denied["errors"][0]["code"] == "VALIDATION"
    assert AgentTaskCallbackPolicy.objects.get(organization=org).allowed_hosts == [
        "hooks.example.org",
        "*.example.org",
    ]


def test_signing_secret_is_encrypted_rotatable_and_never_returned_or_audited(
    org, permission_resolver, caplog
):
    from astrolift_agents.services.task_completion_callbacks import SECRET_PREFIX
    from astrolift_lifecycle.models import OrgSecret
    from astrolift_lifecycle.services.secrets import read_org_secret

    permission_resolver.grant(Permission.SECRET_WRITE)
    query = 'mutation($v:String!){setAgentTaskCallbackSecret(name:"receiver",value:$v){ok errors{code} data{name}}}'
    for marker in ("original-signing-key-marker-1234567890", "rotated-signing-key-marker-0987654321"):
        result = execute(org, query, {"v": marker})
        assert result.errors is None
        assert result.data["setAgentTaskCallbackSecret"] == {
            "ok": True,
            "errors": [],
            "data": {"name": "receiver"},
        }
        row = OrgSecret.objects.get(organization=org, key=SECRET_PREFIX + "receiver")
        assert marker.encode() not in bytes(row.ciphertext)
        assert read_org_secret(org.pk, SECRET_PREFIX + "receiver") == marker
        assert marker not in caplog.text
        assert marker not in str(list(MutationAuditLog.objects.values("variables", "errors")))
    assert OrgSecret.objects.filter(organization=org, key=SECRET_PREFIX + "receiver").count() == 1


def test_replay_unknown_or_other_org_task_is_not_found(org, permission_resolver):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    permission_resolver.grant(Permission.APP_READ)
    other = Organization.objects.create(name="Other replay", slug="other-replay")
    task = AgentTask.objects.create(organization=other)
    query = (
        "mutation($task:GUID!){redeliverAgentTaskCallback(taskId:$task){ok errors{code message} data{id}}}"
    )
    for guid in (task.guid, uuid4()):
        result = execute(org, query, {"task": str(guid)})
        assert result.errors is None
        assert result.data["redeliverAgentTaskCallback"]["errors"] == [
            {"code": "NOT_FOUND", "message": "task not found"}
        ]


def test_replay_task_without_callback_returns_precondition(org, permission_resolver):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    permission_resolver.grant(Permission.APP_READ)
    task = AgentTask.objects.create(organization=org)
    result = execute(
        org,
        "mutation($task:GUID!){redeliverAgentTaskCallback(taskId:$task){ok errors{code} data{id}}}",
        {"task": str(task.guid)},
    )
    assert result.errors is None
    assert result.data["redeliverAgentTaskCallback"]["errors"] == [{"code": "PRECONDITION"}]


def test_replay_returns_pending_state_without_rerunning_finished_task(org, permission_resolver):
    from django.utils import timezone

    from astrolift_agents.services.task_completion_callbacks import configure_policy, freeze_callback

    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    configure_policy(org.pk, ["example.org"])
    task = AgentTask.objects.create(
        organization=org, status="completed", ended_at=timezone.now(), result={"report": "final"}
    )
    callback = AgentTaskCompletionCallback.objects.create(
        task=task, organization=org, callback_url="https://example.org/finished", secret_ref="receiver"
    )
    freeze_callback(task)
    callback.refresh_from_db()
    event_id = callback.final_event_id
    callback.status = "delivered"
    callback.payload_ciphertext = b""
    callback.attempts = 3
    callback.save(update_fields=["status", "payload_ciphertext", "attempts"])
    result = execute(
        org,
        "mutation($task:GUID!){redeliverAgentTaskCallback(taskId:$task){ok errors{code} data{status callbackStatus callbackAttempts callbackLastError}}}",
        {"task": str(task.guid)},
    )
    assert result.errors is None
    assert result.data["redeliverAgentTaskCallback"] == {
        "ok": True,
        "errors": [],
        "data": {
            "status": "completed",
            "callbackStatus": "pending",
            "callbackAttempts": 0,
            "callbackLastError": None,
        },
    }
    callback.refresh_from_db()
    assert callback.generation == 2
    assert callback.final_event_id == event_id
    assert AgentTask.objects.count() == 1


@pytest.mark.parametrize("permission", [Permission.APP_READ, Permission.AGENT_DISPATCH])
def test_replay_requires_result_read_and_dispatch_grants(org, permission_resolver, permission):
    permission_resolver.grant(permission)
    task = AgentTask.objects.create(organization=org)
    result = execute(
        org,
        "mutation($task:GUID!){redeliverAgentTaskCallback(taskId:$task){ok errors{code}}}",
        {"task": str(task.guid)},
    )
    assert result.errors is None
    assert result.data["redeliverAgentTaskCallback"]["errors"] == [{"code": "PERMISSION_DENIED"}]


@pytest.mark.parametrize(
    "field,operation",
    [
        ("configureAgentTaskCallbacks", 'configureAgentTaskCallbacks(allowedHosts:["example.org"])'),
        ("setAgentTaskCallbackSecret", 'setAgentTaskCallbackSecret(name:"receiver",value:"key")'),
        ("redeliverAgentTaskCallback", f'redeliverAgentTaskCallback(taskId:"{uuid4()}")'),
    ],
)
def test_callback_mutations_deny_before_touching_configuration(org, permission_resolver, field, operation):
    result = execute(org, f"mutation {{ {operation} {{ ok errors {{ code }} }} }}")
    assert result.errors is None
    assert result.data[field] == {"ok": False, "errors": [{"code": "PERMISSION_DENIED"}]}
    assert not AgentTaskCallbackPolicy.objects.exists()
    assert not AgentTaskCompletionCallback.objects.exists()


def test_callback_state_on_task_and_legacy_unconfigured_task(org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    task = AgentTask.objects.create(organization=org)
    query = "query($id:ID!){agentTask(id:$id){callbackStatus callbackAttempts callbackLastError}}"
    assert execute(org, query, {"id": str(task.guid)}).data["agentTask"] == {
        "callbackStatus": None,
        "callbackAttempts": 0,
        "callbackLastError": None,
    }
    callback = AgentTaskCompletionCallback.objects.create(
        task=task,
        organization=org,
        callback_url="https://example.org/callback",
        secret_ref="receiver",
        attempts=3,
        last_error="receiver returned HTTP 503",
    )
    assert execute(org, query, {"id": str(task.guid)}).data["agentTask"] == {
        "callbackStatus": "pending",
        "callbackAttempts": 3,
        "callbackLastError": "receiver returned HTTP 503",
    }
    callback.status = "delivered"
    callback.last_error = ""
    callback.save(update_fields=["status", "last_error"])
    assert execute(org, query, {"id": str(task.guid)}).data["agentTask"]["callbackStatus"] == "delivered"


def test_deleted_callback_is_not_exposed(org, permission_resolver):
    from django.utils import timezone

    permission_resolver.grant(Permission.APP_READ)
    task = AgentTask.objects.create(organization=org)
    AgentTaskCompletionCallback.objects.create(
        task=task,
        organization=org,
        callback_url="https://example.org/callback",
        secret_ref="receiver",
        deleted_at=timezone.now(),
    )
    result = execute(
        org, "query($id:ID!){agentTask(id:$id){callbackStatus callbackAttempts}}", {"id": str(task.guid)}
    )
    assert result.data["agentTask"] == {"callbackStatus": None, "callbackAttempts": 0}


def test_task_list_does_not_query_callback_once_per_task(org, django_assert_num_queries):
    for _i in range(12):
        task = AgentTask.objects.create(organization=org)
        AgentTaskCompletionCallback.objects.create(
            task=task,
            organization=org,
            callback_url="https://example.org/callback",
            secret_ref="receiver",
        )
    rows = list(
        AgentTask.objects.select_related("organization", "project", "team", "agent_definition", "dispatcher")
    )
    with django_assert_num_queries(1):
        result = agent_tasks_to_types(rows)
    assert len(result) == 12
    assert all(row.callback_status == "pending" for row in result)


@pytest.mark.parametrize("position", ["variable", "literal"])
def test_signing_secret_cannot_appear_in_graphql_error_logs_or_audit(
    org, permission_resolver, caplog, position
):
    marker = "sensitive-callback-signing-marker"
    if position == "variable":
        query = 'mutation($v:String!){setAgentTaskCallbackSecret(name:"receiver",value:$v){ok}}'
        variables = {"v": {"invalid": marker}}
    else:
        query = f'mutation {{setAgentTaskCallbackSecret(name:"receiver",value:{{invalid:"{marker}"}}){{ok}}}}'
        variables = None
    result = execute(org, query, variables)
    assert result.errors
    assert marker not in caplog.text
    for row in MutationAuditLog.objects.all():
        assert marker not in str(row.variables)
        assert marker not in str(row.errors)


def test_dispatch_payload_and_callback_destination_are_redacted_by_schema_position():
    query = "mutation($request:RunAstroliftAgentInput!){runAstroliftAgent(input:$request){ok}}"
    values, sensitive = redact_operation_variables(
        {
            "request": {
                "agentSlug": "report",
                "callbackUrl": "https://example.org/?token=private",
                "triggerPayload": {"report": "private-result"},
            }
        },
        document=parse(query),
        schema=schema._schema,
    )
    assert sensitive
    assert values["request"] == {
        "agentSlug": "report",
        "callbackUrl": "***REDACTED***",
        "triggerPayload": "***REDACTED***",
    }


def test_debug_graphql_request_and_response_logs_exclude_sensitive_dispatch_data(settings):
    from core.tests.test_secret_leak_channels_1920 import _log_request

    settings.DEBUG = True
    marker = "sensitive-callback-result-marker"
    query = (
        "mutation Op($request:RunAstroliftAgentInput!){runAstroliftAgent(input:$request){ok data{result}}}"
    )
    logged = _log_request(
        query,
        {
            "request": {
                "agentSlug": "report",
                "triggerPayload": {"report": marker},
                "callbackUrl": "https://example.org/?private=" + marker,
            }
        },
        {"data": {"runAstroliftAgent": {"ok": True, "data": {"result": {"report": marker}}}}},
    )
    assert marker not in logged
    assert "report" in logged


@pytest.mark.parametrize(
    "method,permission,kwargs",
    [
        ("configure_agent_task_callbacks", Permission.ORG_UPDATE, {"allowed_hosts": ["example.org"]}),
        (
            "set_agent_task_callback_secret",
            Permission.SECRET_WRITE,
            {"name": "receiver", "value": "private-key"},
        ),
    ],
)
def test_team_scoped_token_cannot_write_org_callback_configuration(
    org, permission_resolver, method, permission, kwargs
):
    permission_resolver.grant(permission)
    user = get_user_model().objects.create(username="callback-admin")
    team = Team.objects.create(organization=org, name="Team", slug="callback-team")
    credential = ApiToken.objects.create(
        user=user,
        organization=org,
        team=team,
        name="token",
        token_hash=uuid4().hex * 2,
        token_last_4="test",
        scopes=["admin"],
    )
    token = set_current_api_token(credential)
    try:
        with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
            result = getattr(AgentsMutation(), method)(
                SimpleNamespace(context=SimpleNamespace(user=user, request=None)), **kwargs
            )
    finally:
        reset_current_api_token(token)
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert not AgentTaskCallbackPolicy.objects.exists()
