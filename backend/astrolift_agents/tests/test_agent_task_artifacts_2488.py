"""Structured evidence on task results (#2488).

A run attaches named artifacts (a JUnit report, coverage, a conformance
report) to its terminal callback. The controller validates the whole list
before writing anything, stores it with the result, and serves it through
``agentTask.artifacts`` (metadata) and ``agentTaskArtifact`` (content).
"""

import hashlib
import json
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from graphql import GraphQLError

from astrolift_agents.models import AgentTask, AgentTaskArtifact, DispatcherInstance
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.schema.types import agent_tasks_to_types
from astrolift_agents.services.agent_task_artifacts import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    MAX_TASK_ARTIFACT_BYTES,
)
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db(transaction=True)

CALLBACK_KEY = "alft_cb_artifacts_fixture"
DISPATCHER_KEY = "artifacts-dispatcher-key"
JUNIT = (
    '<testsuites><testsuite name="commit" tests="1"><testcase name="cas_conflict"/></testsuite></testsuites>'
)
CONFORMANCE = json.dumps({"suite": "conformance-fast", "passed": 41, "failed": 0})
_DIGEST = object()


@pytest.fixture
def task():
    org = Organization.objects.create(name="Artifacts", slug="artifacts")
    dispatcher = DispatcherInstance.objects.create(
        organization=org,
        name="Dispatcher",
        slug="artifacts-dispatcher",
        endpoint="https://dispatcher.example",
        api_key_hash=hashlib.sha256(DISPATCHER_KEY.encode()).hexdigest(),
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )
    return AgentTask.objects.create(
        organization=org,
        dispatcher=dispatcher,
        status=AgentTask.Status.RUNNING,
        callback_token_hash=hashlib.sha256(CALLBACK_KEY.encode()).hexdigest(),
    )


def artifact(
    name="test-report.xml", kind="junit", media_type="application/xml", content=JUNIT, sha256=_DIGEST
):
    if sha256 is _DIGEST:
        data = content.encode("utf-8", "surrogatepass") if isinstance(content, str) else b""
        sha256 = hashlib.sha256(data).hexdigest()
    return {"name": name, "kind": kind, "media_type": media_type, "content": content, "sha256": sha256}


def conformance():
    return artifact("conformance-report.json", "conformance-json", "application/json", CONFORMANCE)


def post(task, body, key=CALLBACK_KEY):
    return Client().post(
        f"/api/dispatch/v1/agents/{task.guid}/callback/",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {key}",
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def read_artifact(task, name, *, tenant=None, org=None):
    tenant = tenant or task.organization
    org = org or tenant
    with tenant_context(TenantContext(organization_id=tenant.pk)):
        return AgentsQuery().agent_task_artifact(
            info=_info(), org_id=str(org.guid), task_id=str(task.guid), name=name
        )


def _assert_nothing_recorded(task):
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING
    assert task.result is None
    assert not AgentTaskArtifact.all_objects.exists()


# ---------------------------------------------------------------------------
# The terminal callback
# ---------------------------------------------------------------------------


def test_terminal_callback_stores_artifacts_with_the_result(task):
    sent = [artifact(), conformance()]
    response = post(task, {"status": "completed", "result": "pr_opened", "artifacts": sent})

    assert response.status_code == 200, response.content
    assert response.json()["artifact_count"] == 2
    task.refresh_from_db()
    assert task.status == AgentTask.Status.COMPLETED
    assert task.result == {"output": "pr_opened"}
    rows = list(AgentTaskArtifact.objects.filter(agent_task=task).order_by("pk"))
    assert [(row.name, row.kind, row.media_type, row.content) for row in rows] == [
        ("test-report.xml", "junit", "application/xml", JUNIT),
        ("conformance-report.json", "conformance-json", "application/json", CONFORMANCE),
    ]
    assert [row.sha256 for row in rows] == [item["sha256"] for item in sent]
    assert [row.size_bytes for row in rows] == [len(JUNIT.encode()), len(CONFORMANCE.encode())]
    assert {row.organization_id for row in rows} == {task.organization_id}


def test_failed_result_keeps_its_evidence(task):
    response = post(task, {"status": "failed", "error": "2 tests failed", "artifacts": [artifact()]})

    assert response.status_code == 200, response.content
    assert response.json()["artifact_count"] == 1
    task.refresh_from_db()
    assert task.status == AgentTask.Status.FAILED
    assert list(task.artifacts.values_list("name", flat=True)) == ["test-report.xml"]


def test_edge_shapes_are_accepted(task):
    sent = [
        artifact("x" * 128, "other", "application/vnd.astrolift.report+json; charset=utf-8", '{"a": 1}'),
        artifact("empty.txt", "other", "text/plain", ""),
        artifact("lcov.info", "coverage", "text/plain", "SF:src/lib.rs\nDA:1,1\nend_of_record\n"),
        artifact("notes.md", "other", "text/markdown", "Résumé: 🌲 green"),
    ]
    response = post(task, {"status": "completed", "artifacts": sent})

    assert response.status_code == 200, response.content
    stored = {row.name: row for row in task.artifacts.all()}
    assert stored["notes.md"].content == "Résumé: 🌲 green"
    assert stored["notes.md"].size_bytes == len("Résumé: 🌲 green".encode())
    assert stored["empty.txt"].sha256 == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("status", ["running", None])
def test_artifacts_need_a_terminal_status(task, status):
    body = {"artifacts": [artifact()]} | ({"status": status} if status else {})
    response = post(task, body)

    assert response.status_code == 400
    assert "terminal status" in response.json()["error"]
    _assert_nothing_recorded(task)


def test_empty_list_probes_support_without_writing(task):
    version = task.version
    response = post(task, {"status": "running", "artifacts": []})

    assert response.status_code == 200
    assert response.json()["artifact_count"] == 0
    task.refresh_from_db()
    assert task.version == version
    assert "artifact_count" not in post(task, {"status": "running"}).json()


@pytest.mark.parametrize(
    "artifacts",
    [
        None,
        {},
        "test-report.xml",
        [None],
        [artifact() | {"extra": 1}],
        [{key: value for key, value in artifact().items() if key != "sha256"}],
        [artifact(name="")],
        [artifact(name=7)],
        [artifact(name="../report.xml")],
        [artifact(name=".hidden")],
        [artifact(name="reports/test.xml")],
        [artifact(name="x" * 129)],
        [artifact(), artifact()],
        [artifact(kind="xml")],
        [artifact(kind="JUNIT")],
        [artifact(media_type=None)],
        [artifact(media_type="xml")],
        [artifact(media_type="application/xml\r\nX-Injected: 1")],
        [artifact(media_type="application/" + "x" * 250)],
        [artifact(content={"tests": 1})],
        [artifact(content="before\x00after")],
        [artifact(content="\ud800")],
        [artifact(sha256=None)],
        [artifact(sha256="abc")],
        [artifact(sha256=hashlib.sha256(JUNIT.encode()).hexdigest().upper())],
        [artifact(sha256=hashlib.sha256(b"something else").hexdigest())],
        [artifact("conformance-report.json", "conformance-json", "application/json", '{"passed": 4')],
        [artifact("conformance-report.json", "conformance-json", "application/json", '{"score": NaN}')],
        [artifact(f"report-{index}.xml") for index in range(MAX_ARTIFACTS + 1)],
    ],
)
def test_malformed_artifacts_refuse_the_whole_callback(task, artifacts):
    response = post(task, {"status": "completed", "result": "done", "artifacts": artifacts})

    assert response.status_code == 400, response.content
    _assert_nothing_recorded(task)


def test_artifact_limit_counts_utf8_bytes(task):
    # Fewer characters than the limit, more UTF-8 bytes.
    content = "🌲" * (MAX_ARTIFACT_BYTES // 4 + 1)
    response = post(
        task, {"status": "completed", "artifacts": [artifact("big.txt", "other", "text/plain", content)]}
    )

    assert response.status_code == 413
    _assert_nothing_recorded(task)


def test_total_limit_spans_every_artifact(task):
    part = "x" * (MAX_TASK_ARTIFACT_BYTES // 3 + 1)
    sent = [artifact(f"part-{index}.txt", "other", "text/plain", part) for index in range(3)]
    response = post(task, {"status": "completed", "artifacts": sent})

    assert response.status_code == 413
    _assert_nothing_recorded(task)


def test_artifacts_at_the_limits_are_accepted(task):
    full = "x" * MAX_ARTIFACT_BYTES
    sent = [artifact(f"part-{index}.txt", "other", "text/plain", full) for index in range(2)]
    assert sum(len(item["content"]) for item in sent) == MAX_TASK_ARTIFACT_BYTES

    response = post(task, {"status": "completed", "artifacts": sent})

    assert response.status_code == 200, response.content
    assert response.json()["artifact_count"] == 2


def test_a_refused_result_stores_no_artifacts(task):
    AgentTask.objects.filter(pk=task.pk).update(cancel_requested_at=timezone.now())
    response = post(task, {"status": "completed", "artifacts": [artifact()]})

    assert response.status_code == 409
    assert not AgentTaskArtifact.all_objects.exists()


def test_artifacts_roll_back_with_the_result(task, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("deadline reservation unavailable")

    monkeypatch.setattr("astrolift_agents.services.task_timeout.reserve_input_wait", unavailable)
    response = post(task, {"status": "completed", "result": "done", "artifacts": [artifact()]})

    assert response.status_code == 503
    _assert_nothing_recorded(task)


def test_a_late_terminal_callback_cannot_replace_the_evidence(task):
    assert post(task, {"status": "completed", "artifacts": [artifact()]}).status_code == 200

    late = post(task, {"status": "completed", "artifacts": [artifact("other.xml")]}, key=DISPATCHER_KEY)

    assert late.status_code == 409
    assert list(AgentTaskArtifact.all_objects.values_list("name", flat=True)) == ["test-report.xml"]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_task_read_lists_artifacts_and_the_content_read_returns_one(task, permission_resolver):
    from config.schema import schema

    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.APP_READ)
    assert post(task, {"status": "completed", "artifacts": [artifact(), conformance()]}).status_code == 200

    with tenant_context(TenantContext(organization_id=task.organization_id)):
        result = schema.execute_sync(
            """query($org: ID!, $task: ID!) {
              agentTask(id: $task) { artifacts { name kind mediaType sizeBytes sha256 createdAt } }
              agentTaskArtifact(orgId: $org, taskId: $task, name: "conformance-report.json") {
                name kind mediaType sizeBytes sha256 content
              }
            }""",
            variable_values={"org": str(task.organization.guid), "task": str(task.guid)},
            context_value=_info().context,
        )

    assert result.errors is None
    listed = result.data["agentTask"]["artifacts"]
    assert [(item["name"], item["kind"], item["mediaType"]) for item in listed] == [
        ("test-report.xml", "junit", "application/xml"),
        ("conformance-report.json", "conformance-json", "application/json"),
    ]
    assert listed[0]["sizeBytes"] == len(JUNIT.encode())
    assert listed[0]["sha256"] == hashlib.sha256(JUNIT.encode()).hexdigest()
    assert listed[0]["createdAt"]
    assert result.data["agentTaskArtifact"] == {
        "name": "conformance-report.json",
        "kind": "conformance-json",
        "mediaType": "application/json",
        "sizeBytes": len(CONFORMANCE.encode()),
        "sha256": hashlib.sha256(CONFORMANCE.encode()).hexdigest(),
        "content": CONFORMANCE,
    }


def test_content_read_is_null_for_an_unknown_name_or_task(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    assert post(task, {"status": "completed", "artifacts": [artifact()]}).status_code == 200

    assert read_artifact(task, "test-report.xml").content == JUNIT
    assert read_artifact(task, "missing.xml") is None
    with tenant_context(TenantContext(organization_id=task.organization_id)):
        assert (
            AgentsQuery().agent_task_artifact(
                info=_info(), org_id=str(task.organization.guid), task_id="overview", name="test-report.xml"
            )
            is None
        )


def test_content_read_requires_agent_read(task, permission_resolver):
    assert post(task, {"status": "completed", "artifacts": [artifact()]}).status_code == 200
    permission_resolver.grant(Permission.APP_READ)

    with pytest.raises(PermissionDenied):
        read_artifact(task, "test-report.xml")


def test_another_tenant_cannot_read_the_artifacts(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    assert post(task, {"status": "completed", "artifacts": [artifact()]}).status_code == 200
    foreign = Organization.objects.create(name="Other", slug="other")

    assert read_artifact(task, "test-report.xml", tenant=foreign) is None
    with pytest.raises(GraphQLError, match="organization mismatch"):
        read_artifact(task, "test-report.xml", tenant=foreign, org=task.organization)


def test_an_artifact_row_of_another_organization_is_never_served(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    foreign = Organization.objects.create(name="Other", slug="other")
    AgentTaskArtifact.objects.create(
        organization=foreign,
        agent_task=task,
        name="planted.xml",
        kind="junit",
        media_type="application/xml",
        content=JUNIT,
        size_bytes=len(JUNIT),
        sha256=hashlib.sha256(JUNIT.encode()).hexdigest(),
    )

    assert read_artifact(task, "planted.xml") is None
    [shaped] = agent_tasks_to_types([AgentTask.objects.get(pk=task.pk)])
    assert shaped.artifacts == []


def test_soft_deleted_artifacts_are_not_served(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    coverage = artifact("coverage.json", "coverage", "application/json", '{"lines": 91.5}')
    assert post(task, {"status": "completed", "artifacts": [artifact(), coverage]}).status_code == 200

    task.artifacts.get(name="coverage.json").soft_delete()

    assert read_artifact(task, "coverage.json") is None
    [shaped] = agent_tasks_to_types([AgentTask.objects.get(pk=task.pk)])
    assert [item.name for item in shaped.artifacts] == ["test-report.xml"]


def test_task_lists_carry_artifact_metadata_without_loading_content(task):
    assert post(task, {"status": "completed", "artifacts": [artifact(), conformance()]}).status_code == 200
    AgentTask.objects.create(organization=task.organization, status=AgentTask.Status.RUNNING)
    rows = list(AgentTask.objects.filter(organization=task.organization).order_by("pk"))

    with CaptureQueriesContext(connection) as queries:
        shaped = agent_tasks_to_types(rows)

    reads = [query["sql"] for query in queries.captured_queries if "agenttaskartifact" in query["sql"]]
    assert len(reads) == 1
    assert '."content"' not in reads[0]
    assert [item.name for item in shaped[0].artifacts] == ["test-report.xml", "conformance-report.json"]
    assert shaped[1].artifacts == []
