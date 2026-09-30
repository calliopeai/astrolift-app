# ruff: noqa: F811
"""Real grants gate immutable pod-log pages, including every cursor read."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from _sdk.cluster import PodLogLine
from django.core import signing
from django.core.cache import cache
from django.test import Client
from graphql import GraphQLError

from astrolift_agents.models import AgentTask
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.services.task_target import freeze_task_target
from astrolift_agents.task_log_pages import CURSOR_SALT, SNAPSHOT_LINES
from astrolift_agents.tests.test_agent_task_logs import _cluster, _dispatcher, _fake_pod, _FixedPodBackend
from astrolift_agents.tests.test_fleet_scopes_1745 import (  # noqa: F401
    bind,
    fleet,
    no_opensearch,
    tenant,
    token,
)
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member, Organization, Policy
from astrolift_registry.models import AppTeamAccess
from core.cluster_observability import (
    reset_log_backend_for_tests,
    reset_pod_backend_for_tests,
    set_log_backend_for_tests,
    set_pod_backend_for_tests,
)
from core.permissions import Permission, PermissionDenied

pytestmark = pytest.mark.django_db(transaction=True)


class LogBackend:
    def __init__(self):
        self.messages = [
            "INFO duplicate",
            "INFO duplicate",
            '{"level":"WARNING","message":"careful"}',
            "plain",
            "ERROR failure",
            "DEBUG five",
            "INFO six",
            "INFO seven",
        ]
        self.calls = []
        self.closed = 0

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        try:
            for index, message in enumerate(self.messages):
                yield PodLogLine(
                    pod_name=kwargs["pod_name"],
                    container="agent",
                    timestamp=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=max(0, index - 1)),
                    message=message,
                    stream="stderr" if index == 4 else "stdout",
                )
        finally:
            self.closed += 1


@pytest.fixture
def logs(fleet):
    cache.clear()
    cluster = _cluster(fleet.world.org, slug="paged-2175")
    cluster.region = "us-west-2"
    cluster.save(update_fields=["region"])
    dispatcher = _dispatcher(fleet.world.org, cluster, slug="page-dispatcher")
    for task in fleet.tasks:
        task.dispatcher = dispatcher
        task.namespace = "recorded-agents"
        task.save(update_fields=["dispatcher", "namespace"])
        freeze_task_target(task, backend="k8s_job", cluster=cluster, namespace=task.namespace)
    pods = _FixedPodBackend([_fake_pod("agent-pod-real-suffix")])
    backend = LogBackend()
    set_pod_backend_for_tests(pods)
    set_log_backend_for_tests(backend)
    try:
        yield SimpleNamespace(cluster=cluster, dispatcher=dispatcher, pods=pods, backend=backend)
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()
        cache.clear()


def page(fleet, task=None, **kwargs):
    return AgentsQuery().agent_task_logs_page(fleet.info, id=str((task or fleet.tasks[0]).guid), **kwargs)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_actual_owner_role_reads_own_logs_and_denies_sibling(fleet, logs, kind):
    bind(fleet, kind, permissions=[Permission.AGENT_READ])
    with tenant(fleet):
        assert len(page(fleet).items) == 8
        if kind == "ORG":
            assert len(page(fleet, fleet.tasks[1]).items) == 8
        else:
            with pytest.raises(PermissionDenied):
                page(fleet, fleet.tasks[1])
    assert len(logs.backend.calls) == (2 if kind == "ORG" else 1)


def test_typed_pages_preserve_order_duplicates_metadata_and_one_snapshot(fleet, logs):
    bind(fleet, "ORG")
    with tenant(fleet):
        newest = page(fleet, limit=3)
        logs.backend.messages.append("INFO appended after snapshot")
        middle = page(fleet, cursor=newest.next_cursor, limit=3)
        oldest = page(fleet, cursor=middle.next_cursor, limit=3)
    lines = oldest.items + middle.items + newest.items
    assert [line.message for line in lines] == logs.backend.messages[:-1]
    assert lines[0].message == lines[1].message and lines[0].timestamp == lines[1].timestamp
    assert len({line.id for line in lines}) == 8
    assert lines[2].level == "warn" and lines[3].level is None
    assert lines[4].stream == "stderr" and lines[4].level == "error"
    assert all(line.pod_name == "agent-pod-real-suffix" and line.container == "agent" for line in lines)
    assert newest.has_more and middle.has_more and not oldest.has_more
    assert oldest.next_cursor is None and oldest.page_size == 3
    assert newest.expires_at == oldest.expires_at and newest.live_only and not newest.window_limited
    assert len(logs.backend.calls) == 1 and logs.backend.closed == 1
    assert logs.backend.calls[0]["tail_lines"] == SNAPSHOT_LINES
    assert logs.pods.calls[0]["namespace"] == "recorded-agents"


@pytest.mark.parametrize("requested, expected", [(0, 1), (-10, 1), (999999, 200)])
def test_page_size_is_clamped_on_server(fleet, logs, requested, expected):
    bind(fleet, "ORG")
    with tenant(fleet):
        result = page(fleet, limit=requested)
    assert result.page_size == expected and len(result.items) <= expected


def test_snapshot_cap_is_explicit_and_stream_is_closed(fleet, logs):
    bind(fleet, "ORG")
    logs.backend.messages = ["INFO output"] * (SNAPSHOT_LINES + 10)
    with tenant(fleet):
        result = page(fleet)
    assert result.window_limited and result.live_only and len(result.items) == 100
    assert logs.backend.closed == 1


@pytest.mark.parametrize("failure", ["tampered", "another-task", "expired"])
def test_cursor_is_signed_bound_to_task_and_expires(fleet, logs, failure):
    bind(fleet, "ORG")
    with tenant(fleet):
        first = page(fleet, limit=2)
        marker = first.next_cursor
        task = fleet.tasks[0]
        if failure == "tampered":
            marker = "invalid:" + marker
        elif failure == "another-task":
            task = fleet.tasks[1]
        else:
            snapshot = signing.loads(marker, salt=CURSOR_SALT)["snapshot"]
            cache.delete(f"agent-task-log-page:{snapshot}")
        with pytest.raises(GraphQLError) as exc:
            page(fleet, task, cursor=marker)
    assert exc.value.extensions["code"] == (
        "LOG_CURSOR_EXPIRED" if failure == "expired" else "LOG_CURSOR_INVALID"
    )
    assert len(logs.backend.calls) == 1


def test_cached_page_does_not_bypass_revoked_role(fleet, logs):
    binding = bind(fleet, "ORG")
    with tenant(fleet):
        first = page(fleet, limit=2)
    binding.soft_delete()
    with tenant(fleet), pytest.raises(PermissionDenied):
        page(fleet, cursor=first.next_cursor)
    assert len(logs.backend.calls) == 1


def test_no_grant_is_denied_before_cache_or_driver(fleet, logs):
    with tenant(fleet), pytest.raises(PermissionDenied):
        page(fleet)
    assert not logs.backend.calls and not logs.pods.calls


def test_foreign_bearer_cannot_read_cached_task_logs(fleet, logs):
    bind(fleet, "ORG")
    with tenant(fleet):
        first = page(fleet, limit=2)
    foreign = Organization.objects.create(name="Token org", slug="logs-token-foreign")
    with token(fleet, org=foreign), tenant(fleet):
        assert page(fleet, cursor=first.next_cursor).items == []
    assert len(logs.backend.calls) == 1


def test_token_permission_ceiling_denies_even_an_org_grant(fleet, logs):
    bind(fleet, "ORG")
    with token(fleet, scopes=("read:clusters",)), tenant(fleet), pytest.raises(PermissionDenied):
        page(fleet)
    assert not logs.backend.calls and not logs.pods.calls


@pytest.mark.parametrize("bad_guid", ["not-a-guid", None])
def test_invalid_frozen_target_never_falls_back_to_dispatcher(fleet, logs, bad_guid):
    bind(fleet, "ORG")
    task = fleet.tasks[0]
    task.dispatch_target["cluster_guid"] = bad_guid
    task.save(update_fields=["dispatch_target"])
    with tenant(fleet):
        assert page(fleet).items == []
    assert not logs.backend.calls and not logs.pods.calls


def test_signed_cursor_expiration_is_reported(fleet, logs, monkeypatch):
    import time

    bind(fleet, "ORG")
    with tenant(fleet):
        first = page(fleet, limit=2)
        later = time.time() + 301
        monkeypatch.setattr("django.core.signing.time.time", lambda: later)
        with pytest.raises(GraphQLError) as exc:
            page(fleet, cursor=first.next_cursor)
    assert exc.value.extensions["code"] == "LOG_CURSOR_EXPIRED"
    assert len(logs.backend.calls) == 1


def test_team_bearer_ceiling_and_revoked_viewer_share_apply_to_cached_pages(fleet, logs):
    bind(fleet, "ORG")
    with token(fleet, team=fleet.world.medops), tenant(fleet):
        assert page(fleet, fleet.tasks[1]).items == []
        share = AppTeamAccess.objects.create(
            registered_app=fleet.world.platform_app, team=fleet.world.medops, access_level="viewer"
        )
        first = page(fleet, fleet.tasks[1], limit=2)
        assert len(first.items) == 2
        share.soft_delete()
        assert page(fleet, fleet.tasks[1], cursor=first.next_cursor).items == []
    assert len(logs.backend.calls) == 1


@pytest.mark.parametrize(
    "failure", ["foreign-org", "deleted-task", "deleted-cluster", "inactive-cluster", "changed-endpoint"]
)
def test_missing_or_incoherent_target_never_reads_driver(fleet, logs, failure):
    bind(fleet, "ORG")
    task = fleet.tasks[0]
    if failure == "foreign-org":
        task = AgentTask.objects.create(
            organization=Organization.objects.create(name="Foreign", slug="foreign-page")
        )
    elif failure == "deleted-task":
        task.soft_delete()
    elif failure == "deleted-cluster":
        logs.cluster.soft_delete()
    elif failure == "inactive-cluster":
        logs.cluster.is_active = False
        logs.cluster.save(update_fields=["is_active"])
    else:
        logs.cluster.endpoint = "https://replacement.invalid"
        logs.cluster.save(update_fields=["endpoint"])
    with tenant(fleet):
        assert page(fleet, task).items == []
    assert not logs.backend.calls and not logs.pods.calls


def test_actual_cluster_region_policy_denies_before_driver_and_cached_page(fleet, logs):
    bind(fleet, "ORG")
    with tenant(fleet):
        first = page(fleet, limit=2)
    Policy.objects.create(
        organization=fleet.world.org,
        name="Deny west",
        slug="deny-west-logs",
        scope_level="ORG",
        effect="DENY",
        action_pattern="agent.read",
        resource_pattern={"region": ["us-west-2"]},
        actor_pattern={},
        conditions=[],
    )
    with tenant(fleet), pytest.raises(PermissionDenied):
        page(fleet, cursor=first.next_cursor)
    assert len(logs.backend.calls) == 1


def test_frozen_placement_is_used_after_dispatcher_replacement(fleet, logs):
    bind(fleet, "ORG")
    replacement = _cluster(fleet.world.org, slug="replacement-page")
    logs.dispatcher.tenant_cluster = replacement
    logs.dispatcher.save(update_fields=["tenant_cluster"])
    with tenant(fleet):
        assert page(fleet).items
    assert logs.backend.calls[0]["auth"].slug == logs.cluster.slug


def test_legacy_dispatcher_actual_region_is_rechecked_instead_of_managed_default(fleet, logs):
    bind(fleet, "ORG")
    actual = _cluster(fleet.world.org, slug="legacy-east-page")
    actual.region = "us-east-1"
    actual.save(update_fields=["region"])
    dispatcher = _dispatcher(fleet.world.org, actual, slug="legacy-east-dispatcher")
    task = fleet.tasks[0]
    task.dispatch_target = {}
    task.dispatcher = dispatcher
    task.save(update_fields=["dispatch_target", "dispatcher"])
    Policy.objects.create(
        organization=fleet.world.org,
        name="Deny actual east",
        slug="deny-actual-east",
        scope_level="ORG",
        effect="DENY",
        action_pattern="agent.read",
        resource_pattern={"region": ["us-east-1"]},
        actor_pattern={},
        conditions=[],
    )
    with tenant(fleet), pytest.raises(PermissionDenied):
        page(fleet)
    assert not logs.backend.calls and not logs.pods.calls


def test_foreign_frozen_cluster_never_falls_back_to_owned_dispatcher(fleet, logs):
    bind(fleet, "ORG")
    foreign_org = Organization.objects.create(name="Foreign cluster", slug="foreign-log-cluster")
    cluster = _cluster(foreign_org, slug="foreign-recorded-page")
    task = fleet.tasks[0]
    task.dispatch_target["cluster_guid"] = str(cluster.guid)
    task.dispatch_target["cluster_endpoint"] = cluster.endpoint
    task.save(update_fields=["dispatch_target"])
    with tenant(fleet):
        assert page(fleet).items == []
    assert not logs.backend.calls and not logs.pods.calls


def test_real_http_page_and_legacy_list_remain_available(fleet, logs):
    bind(fleet, "ORG")
    Member.objects.create(user=fleet.user, scope_kind="ORG", scope_id=fleet.world.org.pk)
    minted = mint_token()
    ApiToken.objects.create(
        user=fleet.user,
        organization=fleet.world.org,
        name="Logs",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )
    query = "query($id:ID!,$cursor:String){agentTaskLogsPage(id:$id,cursor:$cursor,limit:3){items{id timestamp level stream message podName container} nextCursor hasMore pageSize liveOnly windowLimited expiresAt} agentTaskLogs(id:$id,tail:3)}"
    client = Client()

    def read(cursor=None):
        response = client.post(
            "/app/gql/config/",
            data=json.dumps(
                {"query": query, "variables": {"id": str(fleet.tasks[0].guid), "cursor": cursor}}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
        )
        assert response.status_code == 200
        payload = response.json()
        assert not payload.get("errors"), payload
        return payload["data"]

    first = read()
    assert first["agentTaskLogs"] == logs.backend.messages[:3]
    assert [line["message"] for line in first["agentTaskLogsPage"]["items"]] == logs.backend.messages[-3:]
    second = read(first["agentTaskLogsPage"]["nextCursor"])
    assert [line["message"] for line in second["agentTaskLogsPage"]["items"]] == logs.backend.messages[2:5]
