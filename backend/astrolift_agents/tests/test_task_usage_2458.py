"""Real PostgreSQL fixtures exercise the public typed snapshot and its boundaries."""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_agents.models import AgentTask, TaskMeteringRecord
from astrolift_agents.schema.task_usage import read_task_usage
from astrolift_agents.schema.types import agent_task_to_type
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member
from astrolift_lifecycle.models import AgentRun
from astrolift_registry.models import Workload
from config.schema import schema
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

QUERY = """query Usage($id: ID!) {
  agentTask(id: $id) { usageSnapshot {
    taskId taskVersion taskStatus asOf aggregationScope linkedRunId linkedRunRetryCount
    inputTokens { value unit source sourceRecordedAt completeness reason }
    outputTokens { value unit source sourceRecordedAt completeness reason }
    duration { value unit source sourceRecordedAt completeness reason }
    reportedCost { amount currency source sourceRecordedAt completeness reconciliation reason }
    actualCost { amount currency source sourceRecordedAt completeness reconciliation reason }
    model modelSource modelSourceRecordedAt
  } }
}"""


@pytest.fixture(autouse=True)
def no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


@pytest.fixture
def data():
    w = ScopeWorld("usage2458")
    user = make_user("usage2458")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=w.org.pk)
    agent = Workload.objects.create(registered_app=w.medops_app, name="worker", slug="worker", kind="agent")
    other = Workload.objects.create(registered_app=w.platform_app, name="other", slug="other", kind="agent")
    now = timezone.now()
    run = AgentRun.objects.create(
        workload=agent,
        status="succeeded",
        retry_count=2,
        started_at=now - timedelta(seconds=5),
        ended_at=now - timedelta(seconds=1),
    )
    task = AgentTask.objects.create(organization=w.org, agent_definition=agent, agent_run=run)
    # Persist real authoritative rows without invoking runtime dispatch side effects.
    AgentTask.objects.filter(pk=task.pk).update(
        status="completed",
        created_at=now - timedelta(seconds=6),
        started_at=now - timedelta(seconds=5),
        ended_at=now - timedelta(seconds=1),
        completion_usage={
            "input_tokens": 9007199254740993,
            "output_tokens": 0,
            "total_cost_usd": 0.125,
            "model": "runner-model",
        },
    )
    task.refresh_from_db()
    sibling = AgentTask.objects.create(organization=w.org, agent_definition=other)
    return SimpleNamespace(w=w, user=user, info=make_info(user), task=task, run=run, sibling=sibling)


def grant(d, permissions=(Permission.AGENT_READ, Permission.APP_READ), kind="APP", target=None):
    return bind_role(
        d.user,
        permissions=list(permissions),
        kind=kind,
        scope_id=(target or d.w.medops_app).pk,
        slug=f"usage-{uuid4().hex}",
    )


def tenant(d, **kwargs):
    return tenant_context(TenantContext(organization_id=d.w.org.pk, actor_user_id=d.user.pk, **kwargs))


def execute(d):
    result = schema.execute_sync(
        QUERY, variable_values={"id": str(d.task.guid)}, context_value=d.info.context
    )
    assert result.errors is None, result.errors
    return result.data["agentTask"]["usageSnapshot"]


@contextmanager
def token(d, **kwargs):
    row = ApiToken.objects.create(
        user=d.user,
        organization=d.w.org,
        name="usage",
        token_hash=uuid4().hex * 2,
        token_last_4="test",
        scopes=["admin"],
        **kwargs,
    )
    marker = set_current_api_token(row)
    try:
        yield row
    finally:
        reset_current_api_token(marker)


def test_public_schema_preserves_large_counts_zero_and_honest_cost(data):
    grant(data)
    with tenant(data):
        snap = execute(data)
    assert snap["inputTokens"]["value"] == "9007199254740993"
    assert snap["outputTokens"]["value"] == "0"
    assert snap["inputTokens"]["source"] == "COMPLETION_REPORT"
    assert snap["inputTokens"]["sourceRecordedAt"] is None
    assert snap["reportedCost"] == {
        "amount": "0.125",
        "currency": "USD",
        "source": "COMPLETION_REPORT",
        "sourceRecordedAt": None,
        "completeness": "REPORTED",
        "reconciliation": "NOT_RECONCILED",
        "reason": None,
    }
    assert snap["actualCost"]["amount"] is None
    assert snap["actualCost"]["currency"] is None
    assert snap["actualCost"]["reason"] == "NO_TASK_BILLING_SOURCE"
    assert snap["duration"]["value"] == "4000"
    assert snap["model"] == "runner-model"
    assert snap["modelSourceRecordedAt"] is None
    assert snap["linkedRunRetryCount"] == 2
    assert "NO_SUM" in snap["aggregationScope"]


def test_completion_and_meter_are_not_added_and_retry_tasks_are_excluded(data):
    grant(data)
    TaskMeteringRecord.objects.create(
        agent_run=data.run, token_input=101, token_output=202, metering_source="agent_reported"
    )
    AgentTask.objects.create(
        organization=data.w.org,
        agent_definition=data.task.agent_definition,
        completion_usage={"input_tokens": 777, "total_cost_usd": 99},
    )
    data.task.completion_usage["output_tokens"] = None
    data.task.save(update_fields=["completion_usage"])
    with tenant(data):
        snap = execute(data)
    assert snap["inputTokens"]["value"] == "9007199254740993"
    assert snap["outputTokens"]["value"] == "202"
    assert snap["outputTokens"]["source"] == "AGENT_REPORTED"
    assert snap["outputTokens"]["completeness"] == "REPORTED"
    assert snap["reportedCost"]["amount"] == "0.125"


@pytest.mark.parametrize("bad", [True, -1, 2**63, "100", {}, 1.5])
def test_malformed_or_overflow_tokens_are_unknown(data, bad):
    grant(data)
    data.task.completion_usage = {"input_tokens": bad}
    data.task.save(update_fields=["completion_usage"])
    with tenant(data):
        snap = execute(data)
    assert snap["inputTokens"]["value"] is None
    assert snap["inputTokens"]["reason"] == "INVALID_VALUE"
    assert snap["reportedCost"]["amount"] is None


@pytest.mark.parametrize("bad", [True, -1, 10**16, "0.1", {}])
def test_malformed_cost_is_not_zero_or_billing(data, bad):
    grant(data)
    data.task.completion_usage = {"total_cost_usd": bad}
    data.task.save(update_fields=["completion_usage"])
    with tenant(data):
        snap = execute(data)
    assert snap["reportedCost"]["amount"] is None
    assert snap["reportedCost"]["currency"] is None
    assert snap["reportedCost"]["reason"] == "INVALID_VALUE"


def test_running_and_premature_meter_never_look_complete(data):
    grant(data)
    meter = TaskMeteringRecord.objects.create(
        agent_run=data.run, token_input=23, metering_source="k8s_metrics"
    )
    TaskMeteringRecord.objects.filter(pk=meter.pk).update(created_at=data.task.started_at)
    AgentTask.objects.filter(pk=data.task.pk).update(status="running", ended_at=None)
    with tenant(data):
        snap = execute(data)
    assert snap["inputTokens"]["value"] == "23"
    assert snap["inputTokens"]["completeness"] == "PARTIAL"
    assert snap["inputTokens"]["reason"] == "PREMATURE_METER"
    assert snap["reportedCost"]["amount"] is None
    assert snap["model"] is None
    assert snap["duration"]["value"] is None
    AgentTask.objects.filter(pk=data.task.pk).update(status="completed", ended_at=timezone.now())
    with tenant(data):
        snap = execute(data)
    # Terminal completion cannot turn a previously premature meter into final data.
    assert snap["outputTokens"]["value"] == "0"
    data.task.completion_usage = {}
    data.task.save(update_fields=["completion_usage"])
    with tenant(data):
        snap = execute(data)
    assert snap["inputTokens"]["completeness"] == "PARTIAL"


def test_corrupt_cross_scope_run_binding_is_not_exposed(data):
    grant(data)
    other_run = AgentRun.objects.create(workload=data.sibling.agent_definition, retry_count=99)
    TaskMeteringRecord.objects.create(agent_run=other_run, token_input=444)
    AgentTask.objects.filter(pk=data.task.pk).update(agent_run=other_run, completion_usage={})
    with tenant(data):
        snap = execute(data)
    assert snap["linkedRunId"] is None
    assert snap["linkedRunRetryCount"] is None
    assert snap["inputTokens"]["value"] is None
    assert snap["inputTokens"]["reason"] == "UNVERIFIED_RUN_BINDING"


def test_app_read_does_not_grant_nested_agent_read(data):
    grant(data, [Permission.APP_READ])
    with tenant(data):
        result = schema.execute_sync(
            QUERY, variable_values={"id": str(data.task.guid)}, context_value=data.info.context
        )
    assert result.errors
    assert result.data["agentTask"]["usageSnapshot"] is None


def test_revoked_role_is_rechecked_on_previously_projected_task(data):
    binding = grant(data)
    with tenant(data):
        projected = agent_task_to_type(data.task)
        assert projected.usage_snapshot(data.info) is not None
        binding.soft_delete()
        with pytest.raises(PermissionDenied):
            projected.usage_snapshot(data.info)


@pytest.mark.parametrize("change", ["revoke", "expire", "delete", "scope", "team", "rotate"])
def test_bearer_changes_are_rechecked_even_with_stale_token_context(data, change):
    grant(data, kind="ORG", target=data.w.org)
    with tenant(data), token(data) as credential:
        assert read_task_usage(data.info, str(data.task.guid)) is not None
        changes = {
            "revoke": {"is_revoked": True},
            "expire": {"expires_at": timezone.now()},
            "delete": {"deleted_at": timezone.now()},
            "scope": {"scopes": ["read:clusters"]},
            "team": {"team": data.w.platform},
            "rotate": {"token_hash": uuid4().hex * 2},
        }[change]
        ApiToken.all_objects.filter(pk=credential.pk).update(**changes)
        if change == "team":
            assert read_task_usage(data.info, str(data.task.guid)) is None
        else:
            with pytest.raises(PermissionDenied):
                read_task_usage(data.info, str(data.task.guid))


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT"])
def test_sibling_and_cross_org_tasks_are_hidden(data, kind):
    target = {"APP": data.w.medops_app, "TEAM": data.w.medops, "PROJECT": data.w.medops_project}[kind]
    grant(data, kind=kind, target=target)
    other = ScopeWorld("usageother2458")
    foreign = AgentTask.objects.create(organization=other.org)
    with tenant(data):
        for task in [data.sibling, foreign]:
            with pytest.raises(PermissionDenied):
                read_task_usage(data.info, str(task.guid))


def test_selected_project_and_team_cannot_cross_scope(data):
    grant(data, kind="ORG", target=data.w.org)
    with tenant(data, team_id=data.w.platform.pk, project_id=data.w.platform_project.pk):
        # Org grants intentionally span selection; bearer team ceilings do not.
        with token(data, team=data.w.platform):
            assert read_task_usage(data.info, str(data.task.guid)) is None


@pytest.mark.parametrize("owner", ["project", "team"])
def test_recorded_owner_does_not_fall_through_to_agent_app(data, owner):
    grant(data)
    AgentTask.objects.filter(pk=data.task.pk).update(
        **{owner: getattr(data.w, f"platform_{owner}", data.w.platform)}
    )
    with tenant(data), pytest.raises(PermissionDenied):
        read_task_usage(data.info, str(data.task.guid))


def test_inconsistent_terminal_timestamps_do_not_claim_final_usage(data):
    grant(data)
    AgentTask.objects.filter(pk=data.task.pk).update(ended_at=timezone.now() + timedelta(days=1))
    with tenant(data):
        snap = execute(data)
    assert snap["inputTokens"]["value"] is None
    assert snap["inputTokens"]["reason"] == "INCONSISTENT_TIMESTAMPS"
    assert snap["reportedCost"]["amount"] is None
    assert snap["reportedCost"]["reason"] == "INCONSISTENT_TIMESTAMPS"
    assert snap["duration"]["value"] is None


def test_snapshot_read_does_not_modify_sources_or_allocate_attempt_cost(data):
    grant(data)
    version = data.task.version
    usage = data.task.completion_usage.copy()
    with tenant(data):
        first = execute(data)
        second = execute(data)
    data.task.refresh_from_db()
    assert data.task.version == version
    assert data.task.completion_usage == usage
    assert first["inputTokens"] == second["inputTokens"]
    assert TaskMeteringRecord.objects.filter(agent_run=data.run).count() == 0


def test_deactivated_session_viewer_is_rechecked(data):
    grant(data)
    with tenant(data):
        assert read_task_usage(data.info, str(data.task.guid)) is not None
        type(data.user).objects.filter(pk=data.user.pk).update(is_active=False)
        with pytest.raises(PermissionDenied):
            read_task_usage(data.info, str(data.task.guid))


def test_changed_bearer_organization_is_rechecked(data):
    grant(data)
    foreign = ScopeWorld("tokenother2458")
    with tenant(data), token(data) as credential:
        ApiToken.objects.filter(pk=credential.pk).update(organization=foreign.org)
        with pytest.raises(PermissionDenied):
            read_task_usage(data.info, str(data.task.guid))


@pytest.mark.parametrize("bearer", [False, True])
@pytest.mark.parametrize("change", ["inactive", "deleted"])
def test_removed_membership_leaves_no_access_despite_retained_role(data, bearer, change):
    from contextlib import nullcontext

    grant(data)
    with tenant(data), token(data) if bearer else nullcontext():
        assert read_task_usage(data.info, str(data.task.guid)) is not None
        changes = {"is_active": False} if change == "inactive" else {"deleted_at": timezone.now()}
        Member.objects.filter(user=data.user, scope_kind="ORG", scope_id=data.w.org.pk).update(**changes)
        with pytest.raises(PermissionDenied):
            read_task_usage(data.info, str(data.task.guid))


@pytest.mark.parametrize("period", ["historical", "future", "reversed"])
def test_contradictory_linked_run_period_cannot_finalize_meter(data, period):
    grant(data)
    now = timezone.now()
    periods = {
        "historical": (data.task.created_at - timedelta(days=2), data.task.created_at - timedelta(days=1)),
        "future": (now + timedelta(days=1), now + timedelta(days=2)),
        "reversed": (now, data.task.created_at),
    }
    start, end = periods[period]
    AgentRun.objects.filter(pk=data.run.pk).update(started_at=start, ended_at=end)
    AgentTask.objects.filter(pk=data.task.pk).update(completion_usage={})
    TaskMeteringRecord.objects.create(agent_run=data.run, token_input=55, metering_source="agent_reported")
    with tenant(data):
        snap = execute(data)
    assert snap["inputTokens"]["value"] == "55"
    assert snap["inputTokens"]["completeness"] == "PARTIAL"
    assert snap["inputTokens"]["reason"] == "PREMATURE_METER"


def test_reported_zero_cost_is_distinct_from_missing_actual_cost(data):
    grant(data)
    data.task.completion_usage = {"total_cost_usd": 0}
    data.task.save(update_fields=["completion_usage"])
    with tenant(data):
        snap = execute(data)
    assert snap["reportedCost"]["amount"] == "0"
    assert snap["reportedCost"]["currency"] == "USD"
    assert snap["actualCost"]["amount"] is None


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_numbers_are_rejected_before_decimal_transport(value):
    # PostgreSQL JSON rejects these; also guard any in-memory normalization path.
    from astrolift_agents.schema.task_usage import _cost

    assert _cost(value) is None


def test_result_and_event_shaped_payload_are_not_billing_sources(data):
    grant(data)
    AgentTask.objects.filter(pk=data.task.pk).update(
        completion_usage=[],
        result={"usage": {"input_tokens": 500, "total_cost_usd": 42}},
    )
    with tenant(data):
        snap = execute(data)
    assert snap["inputTokens"]["value"] is None
    assert snap["reportedCost"]["amount"] is None
    assert snap["actualCost"]["amount"] is None


@pytest.mark.parametrize("bearer", [False, True])
def test_new_policy_is_not_hidden_by_an_existing_request_cache(data, bearer):
    from contextlib import nullcontext

    from astrolift_identity import abac
    from astrolift_identity.models import Policy
    from core.permissions import PermissionScope, ScopeKind, check_permission

    grant(data)
    attrs = abac.RequestAttributes(actor_user_id=data.user.pk)
    with tenant(data), token(data) if bearer else nullcontext(), abac.request_attributes(attrs):
        check_permission(Permission.AGENT_READ, scope=PermissionScope(ScopeKind.APP, data.w.medops_app.pk))
        assert attrs.cache
        assert read_task_usage(data.info, str(data.task.guid)) is not None
        Policy.objects.create(
            organization=data.w.org,
            name="Withdraw usage",
            slug="withdraw-usage",
            scope_level="ORG",
            effect="DENY",
            action_pattern="agent.read",
        )
        with pytest.raises(PermissionDenied):
            read_task_usage(data.info, str(data.task.guid))


@pytest.mark.parametrize("bearer", [False, True])
def test_removed_idp_group_is_not_retained_by_an_existing_request_cache(data, bearer):
    from contextlib import nullcontext

    from astrolift_identity import abac
    from astrolift_identity.models import RoleBinding
    from core.permissions import PermissionScope, ScopeKind, check_permission

    binding = grant(data)
    RoleBinding.objects.filter(pk=binding.pk).update(user=None, group_external_id="usage-readers")
    members = Member.objects.filter(user=data.user, scope_kind="ORG", scope_id=data.w.org.pk)
    members.update(idp_groups=["usage-readers"])
    attrs = abac.RequestAttributes(actor_user_id=data.user.pk)
    with tenant(data), token(data) if bearer else nullcontext(), abac.request_attributes(attrs):
        check_permission(Permission.AGENT_READ, scope=PermissionScope(ScopeKind.APP, data.w.medops_app.pk))
        assert attrs.cache
        assert read_task_usage(data.info, str(data.task.guid)) is not None
        members.update(idp_groups=[])
        with pytest.raises(PermissionDenied):
            read_task_usage(data.info, str(data.task.guid))


def test_capability_is_publicly_advertised():
    from core.capabilities_registry import shipped_capabilities

    assert "agents.task_usage_snapshot" in shipped_capabilities()
