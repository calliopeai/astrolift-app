import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import Client

from astrolift_agents.models import AgentTask
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.tests import test_agent_task_events as event_fixtures
from astrolift_agents.tests import test_team_isolation_1866 as team_fixtures
from astrolift_agents.tests.test_agent_task_events import event, post
from astrolift_agents.tests.test_team_isolation_1866 import grant, member, team_token
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db(transaction=True)
task = event_fixtures.task
world = team_fixtures.world
no_opensearch = team_fixtures.no_opensearch


def snapshot(revision=1, items=None):
    return {
        "revision": revision,
        "harness": "claude-code-cli",
        "session_id": "session-123",
        "items": items
        if items is not None
        else [
            {
                "id": "1",
                "text": "Verify deployment",
                "status": "in_progress",
                "active_form": "Verifying",
                "details": "Read the actual result",
            }
        ],
    }


def read(task):
    with tenant_context(TenantContext(organization_id=task.organization_id)):
        return AgentsQuery().agent_task_backlog(
            info=SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            org_id=str(task.organization.guid),
            task_id=str(task.guid),
        )


def test_snapshot_survives_terminal_result_and_pod_cleanup(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    assert read(task) is None
    assert post(task, [], backlog=snapshot()).json()["backlog_revision"] == 1
    assert post(task, [], status="completed", result="done").status_code == 200
    AgentTask.objects.filter(pk=task.pk).update(pod_name="", external_id="")
    value = read(task)
    assert value.session_id == "session-123" and value.harness == "claude-code-cli"
    assert value.items[0].active_form == "Verifying"
    assert value.revision == 1 and value.updated_at is not None
    assert task.events.count() == 0


def test_explicit_empty_clears_and_old_retry_cannot_restore(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    post(task, [], backlog=snapshot())
    response = post(task, [], backlog=snapshot(2, []))
    assert response.status_code == 200 and response.json()["backlog_revision"] == 2
    cleared = read(task)
    assert cleared.items == []
    task.refresh_from_db()
    version = task.version
    assert post(task, [], backlog=snapshot(2, [])).status_code == 200
    task.refresh_from_db()
    assert task.version == version and read(task).updated_at == cleared.updated_at
    assert post(task, [], backlog=snapshot()).status_code == 409
    assert post(task, [], backlog=snapshot(2)).status_code == 409
    assert read(task).items == []


def test_atomic_backlog_and_events_terminal_callback(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    response = post(task, [event()], backlog=snapshot(), status="completed", result="done")
    assert response.status_code == 200
    assert read(task).revision == 1
    task.refresh_from_db()
    assert task.status == "completed" and task.events.count() == 1
    assert post(task, [], backlog=snapshot(2, [])).status_code == 409


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        snapshot(True),
        snapshot(0),
        snapshot(items=[{"id": "1", "text": "x", "status": "failed"}]),
        snapshot(items=[{"id": "1", "text": "x", "status": "pending"}] * 2),
        snapshot(items=[{"id": "1", "text": "x" * 8193, "status": "pending"}]),
        snapshot() | {"session_id": "../foreign"},
        snapshot() | {"harness": "unknown"},
    ],
)
def test_invalid_snapshot_rejects_whole_callback(task, bad):
    assert post(task, [event()], backlog=bad, status="completed").status_code == 400
    task.refresh_from_db()
    assert task.status == "running" and task.backlog_snapshot is None and not task.events.exists()


def test_aggregate_bound_and_session_identity(task):
    many = [{"id": str(i), "text": "x" * 8192, "status": "pending"} for i in range(17)]
    assert post(task, [], backlog=snapshot(items=many)).status_code == 413
    post(task, [], backlog=snapshot())
    assert post(task, [], backlog=snapshot(2) | {"session_id": "other"}).status_code == 409


def test_read_denies_permission_and_foreign_org(task, permission_resolver):
    post(task, [], backlog=snapshot())
    with pytest.raises(PermissionDenied):
        read(task)
    permission_resolver.grant(Permission.AGENT_READ)
    foreign = Organization.objects.create(name="Foreign backlog", slug="foreign-backlog")
    with tenant_context(TenantContext(organization_id=foreign.pk)):
        assert (
            AgentsQuery().agent_task_backlog(
                info=SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
                org_id=str(foreign.guid),
                task_id=str(task.guid),
            )
            is None
        )


def test_real_team_grants_and_token_ceiling_match_event_read(world):
    grant(world, Permission.AGENT_READ)
    own = AgentTask.objects.create(
        organization=world.org,
        team=world.medops,
        backlog_snapshot=snapshot() | {"updated_at": "2026-09-30T00:00:00+00:00"},
    )
    other = AgentTask.objects.create(
        organization=world.org, team=world.platform, backlog_snapshot=own.backlog_snapshot
    )

    def lookup(row):
        return AgentsQuery().agent_task_backlog(
            info=world.info, org_id=str(world.org.guid), task_id=str(row.guid)
        )

    with member(world):
        assert lookup(own).items[0].id == "1"
        with pytest.raises(PermissionDenied):
            lookup(other)
    with member(world), team_token(world, scopes=("read:clusters",)):
        with pytest.raises(PermissionDenied):
            lookup(own)
    for scope in ("mcp:read", "read:apps"):
        with member(world), team_token(world, scopes=(scope,)):
            assert lookup(own).session_id == "session-123"


def test_task_token_cannot_write_another_task(task):
    token = "alft_cb_backlog_fixture"
    AgentTask.objects.filter(pk=task.pk).update(
        callback_token_hash=hashlib.sha256(token.encode()).hexdigest()
    )
    other = AgentTask.objects.create(organization=task.organization, status="running")
    response = Client().post(
        f"/api/dispatch/v1/agents/{other.guid}/callback/",
        data=json.dumps({"backlog": snapshot()}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 401
    other.refresh_from_db()
    assert other.backlog_snapshot is None


def test_graphql_contract_and_no_snapshot_probe(task, permission_resolver):
    from config.schema import schema

    permission_resolver.grant(Permission.AGENT_READ)
    assert post(task, []).json()["task_backlog_protocol_version"] == 1
    assert post(task, []).json()["backlog_revision"] == 0
    post(task, [], backlog=snapshot())
    with tenant_context(TenantContext(organization_id=task.organization_id)):
        result = schema.execute_sync(
            """query($org: ID!, $task: ID!) { agentTaskBacklog(orgId: $org, taskId: $task) { harness sessionId revision updatedAt items { id text status activeForm details } } }""",
            variable_values={"org": str(task.organization.guid), "task": str(task.guid)},
            context_value=SimpleNamespace(user=None, request=None),
        )
    assert result.errors is None
    assert result.data["agentTaskBacklog"]["items"][0]["activeForm"] == "Verifying"


def test_runner_parser_snapshot_roundtrip(task, permission_resolver):
    # Captured from the runner's ClaudeStream -> TaskEventPublisher test path,
    # after a successful TaskCreate result, rather than a requested tool call.
    value = json.loads((Path(__file__).parent / "fixtures/task_backlog_1972.json").read_text())
    permission_resolver.grant(Permission.AGENT_READ)
    response = post(task, [], backlog=value)
    assert response.status_code == 200 and response.json()["backlog_revision"] == value["revision"]
    result = read(task)
    assert result.session_id == value["session_id"] and result.harness == value["harness"]
    assert result.items[0].id == "7" and result.items[0].text == value["items"][0]["text"]
    assert result.items[0].details == value["items"][0]["details"]
