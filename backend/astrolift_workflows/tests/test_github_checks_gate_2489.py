"""github_checks stages on a real Temporal server with real PostgreSQL activities (#2489).

GitHub is the only fake: an Enterprise API base served in-process through
``urllib.request.urlopen``, including the App installation-token exchange,
so the connection resolver, token minting and client code all run for real.
"""

import asyncio
import functools
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from asgiref.sync import sync_to_async
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.worker import Replayer

from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.models import WorkflowRun
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_scm.models import SourceConnection
from astrolift_workflows.activities import workflow_stage_activities as activities
from astrolift_workflows.activities.workflow_github_checks import (
    _poll_github_checks_sync,
    poll_github_checks,
)
from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput
from astrolift_workflows.worker import ACTIVITIES
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.secrets import encrypt_at_rest
from core.testing.temporal import temporal_worker
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution

pytestmark = pytest.mark.django_db(transaction=True)

API = "https://ghe.example/api/v3"
REPO = "acme/widgets"
PULL = "https://github.com/acme/widgets/pull/7"
FIRST, SECOND = "1" * 40, "2" * 40
CLIENT_ID = "Iv23liGateFixture000"
INSTALLATION = "4242"
INSTALLATION_TOKEN = "ghs_gate_installation"
CONFIG = {
    "repo": REPO,
    "sha_source": "pull_request_head",
    "pull_request_path": "pr",
    "checks": ["lint", "test"],
}
REGISTERED = [
    activities.get_workflow_stages,
    activities.create_stage_execution,
    activities.update_stage_execution,
    activities.mark_workflow_run,
    poll_github_checks,
]


@functools.cache
def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def check(name, status="completed", conclusion="success"):
    return {
        "id": sum(map(ord, name + status)),
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": "github-actions"},
        "html_url": f"https://github.com/{REPO}/runs/{name}",
    }


class FakeGitHub:
    """One repository behind one App installation on a GitHub Enterprise base."""

    def __init__(self):
        self.head = FIRST
        self.listings = {FIRST: [[]], SECOND: [[]]}
        self.pull_errors: list[int] = []
        self.log: list[tuple[str, str]] = []
        self.observe = None

    def __call__(self, request, timeout):
        assert request.full_url.startswith(API + "/")
        url = urllib.parse.urlsplit(request.full_url)
        path = url.path.removeprefix("/api/v3")
        auth = request.get_header("Authorization")
        self.log.append((request.get_method(), path))
        if path == f"/app/installations/{INSTALLATION}/access_tokens":
            claims = jwt.decode(auth.removeprefix("Bearer "), _key().public_key(), algorithms=["RS256"])
            assert claims["iss"] == CLIENT_ID
            return self._json(
                {
                    "token": INSTALLATION_TOKEN,
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                }
            )
        assert auth == f"Bearer {INSTALLATION_TOKEN}"
        if path == f"/repos/{REPO}/pulls/7":
            if self.pull_errors:
                status = self.pull_errors.pop(0)
                raise urllib.error.HTTPError(request.full_url, status, "unavailable", {}, io.BytesIO(b""))
            return self._json({"number": 7, "head": {"sha": self.head}})
        sha = path.removeprefix(f"/repos/{REPO}/commits/").removesuffix("/check-runs")
        assert urllib.parse.parse_qs(url.query)["page"] == ["1"]
        if self.observe is not None:
            self.observe(sha)
        frames = self.listings[sha]
        runs = frames.pop(0) if len(frames) > 1 else frames[0]
        return self._json({"total_count": len(runs), "check_runs": runs})

    @staticmethod
    def _json(payload):
        return io.BytesIO(json.dumps(payload).encode())

    def reads(self, fragment):
        return [path for _, path in self.log if fragment in path]


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


def _org(label):
    return Organization.objects.create(
        name=f"Checks gate {label}", slug=f"checks-{label}-{uuid.uuid4().hex[:8]}"
    )


def _app_connection(org):
    pem = _key().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    secret = encrypt_at_rest(pem)
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="GitHub App",
        account_login="acme",
        installation_id=INSTALLATION,
        app_client_id=CLIENT_ID,
        api_base_url=API,
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
        is_active=True,
    )


def _agent(org):
    team = Team.objects.create(organization=org, name="Factory", slug=f"factory-{uuid.uuid4().hex[:6]}")
    project = Project.objects.create(
        organization=org, team=team, name="Fossil", slug=f"fossil-{uuid.uuid4().hex[:6]}"
    )
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Builder host",
        slug=f"builder-host-{uuid.uuid4().hex[:6]}",
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app, name="Builder", slug="builder", kind=Workload.Kind.AGENT
    )


def create_gate(*, timeout=600, connection=True, builder=False):
    org = _org("run")
    if connection:
        _app_connection(org)
    definition = WorkflowDefinition.objects.create(
        organization=org,
        slug=f"story-{uuid.uuid4().hex[:8]}",
        name="Story",
        model_label="",
        pattern_kind="chained",
    )
    if builder:
        WorkflowStage.objects.create(
            definition=definition,
            slug=f"{definition.slug}-build",
            order=0,
            kind=WorkflowStage.StageKind.AGENT_DISPATCH,
            agent_definition=_agent(org),
            output_key="build",
            timeout_seconds=600,
        )
    gate = WorkflowStage.objects.create(
        definition=definition,
        slug=f"{definition.slug}-ci",
        order=1 if builder else 0,
        kind=WorkflowStage.StageKind.GITHUB_CHECKS,
        output_key="ci",
        timeout_seconds=timeout,
        iteration=CONFIG,
        back_edge=(
            {
                "to": "build",
                "when": "output_equals",
                "path": "github_checks.outcome",
                "value": "failed",
                "max_rounds": 2,
            }
            if builder
            else {}
        ),
    )
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=f"checks-gate-{uuid.uuid4()}",
        run_id="",
        status="running",
    )
    return {"org": org, "definition": definition, "gate": gate, "run": run}


async def run_gate(env, plan, payload, *, extra_activities=()):
    async with temporal_worker(
        env, workflows=[WorkflowDefinitionRunWorkflow], activities=[*REGISTERED, *extra_activities]
    ):
        handle = await env.client.start_workflow(
            WorkflowDefinitionRunWorkflow.run,
            WorkflowDefinitionRunInput(
                workflow_definition_slug=plan["definition"].slug,
                workflow_definition_id=str(plan["definition"].pk),
                workflow_run_id=str(plan["run"].pk),
                trigger_payload=payload,
                actor=Actor(kind="system"),
            ),
            id=plan["run"].workflow_id,
            task_queue="astrolift-test",
        )
        result = await asyncio.wait_for(handle.result(), 60)
        history = await handle.fetch_history()
    await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)
    return result, history


async def gate_rows(plan):
    return await sync_to_async(list)(
        WorkflowStageExecution.objects.filter(workflow_run=plan["run"], stage=plan["gate"]).order_by("pk")
    )


async def run_row(plan):
    return await sync_to_async(WorkflowRun.objects.get)(pk=plan["run"].pk)


def timer_seconds(history):
    return [
        event.timer_started_event_attributes.start_to_fire_timeout.seconds
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
    ]


@pytest.mark.parametrize("timeout,interval", [(600, 60), (120_000, 120)])
async def test_gate_waits_through_a_transient_error_and_pending_checks_then_passes(
    temporal_env, github, timeout, interval
):
    plan = await sync_to_async(create_gate)(timeout=timeout)
    github.pull_errors = [503]
    github.listings[FIRST] = [
        [check("lint", "in_progress", None)],
        [check("lint"), check("test", conclusion="skipped")],
    ]
    seen = []
    github.observe = lambda sha: seen.append(
        WorkflowStageExecution.objects.filter(workflow_run=plan["run"], stage=plan["gate"]).values_list(
            "status", "output"
        )[0]
    )

    result, history = await run_gate(temporal_env, plan, {"pr": PULL})

    assert result.ok, result.message
    [row] = await gate_rows(plan)
    assert row.status == "completed" and row.error_message == ""
    gate = row.output["github_checks"]
    assert (gate["outcome"], gate["repo"], gate["pull_request"], gate["sha"]) == ("passed", REPO, 7, FIRST)
    assert gate["required"] == ["lint", "test"] and "error" not in gate
    assert [(entry["name"], entry["state"], entry["runs"][0]["conclusion"]) for entry in gate["checks"]] == [
        ("lint", "passed", "success"),
        ("test", "passed", "skipped"),
    ]
    assert result.data["named_outputs"]["ci"] == row.output
    # Progress was on the running row while the gate waited: first the
    # transient read error, then the checks of the pending head.
    assert [status for status, _ in seen] == ["running", "running"]
    first, second = (output["github_checks"] for _, output in seen)
    assert (first["outcome"], first["sha"], first["checks"]) == ("pending", None, [])
    assert first["error"] == "GitHub returned 503 while reading pull request #7 in acme/widgets"
    assert (second["outcome"], second["sha"], "error" in second) == ("pending", FIRST, False)
    assert [(entry["name"], entry["state"]) for entry in second["checks"]] == [
        ("lint", "pending"),
        ("test", "missing"),
    ]
    assert len(github.reads("/pulls/7")) == 3
    assert len(github.reads("/access_tokens")) == 1
    assert timer_seconds(history) == [interval, interval]
    assert (await run_row(plan)).status == "completed"


async def test_a_failed_required_check_fails_the_run_at_once(temporal_env, github):
    plan = await sync_to_async(create_gate)()
    github.listings[FIRST] = [[check("lint", "in_progress", None), check("test", conclusion="failure")]]

    result, history = await run_gate(temporal_env, plan, {"pr": 7})

    assert not result.ok
    assert result.message == "required GitHub checks failed: test"
    assert result.data["status"] == "incomplete"
    [row] = await gate_rows(plan)
    assert row.status == "failed"
    assert row.error_message == "required GitHub checks failed: test"
    assert row.output["github_checks"]["outcome"] == "failed"
    assert len(github.reads("/pulls/7")) == 1 and timer_seconds(history) == []
    run = await run_row(plan)
    assert (run.status, run.failure["message"]) == ("failed", "required GitHub checks failed: test")


async def test_checks_still_unfinished_at_the_stage_timeout_fail_as_timed_out(temporal_env, github):
    plan = await sync_to_async(create_gate)(timeout=300)
    github.listings[FIRST] = [[check("lint", "queued", None)]]

    result, history = await run_gate(temporal_env, plan, {"pr": "7"})

    assert not result.ok
    expected = "required GitHub checks did not pass before the stage timeout: lint (pending), test (missing)"
    assert result.message == expected
    [row] = await gate_rows(plan)
    assert (row.status, row.error_message, row.output["github_checks"]["outcome"]) == (
        "failed",
        expected,
        "timed_out",
    )
    # One poll a minute from the open through the deadline itself; the last
    # wait is whatever remains of the timeout.
    assert len(github.reads("/pulls/7")) == 6
    timers = timer_seconds(history)
    assert len(timers) == 5 and timers[:4] == [60, 60, 60, 60] and timers[4] <= 60


async def test_another_organizations_github_connection_is_never_used(temporal_env, github):
    plan = await sync_to_async(create_gate)(connection=False)
    await sync_to_async(lambda: _app_connection(_org("other")))()

    result, _ = await run_gate(temporal_env, plan, {"pr": 7})

    assert not result.ok
    assert "no active GitHub connection for this organization" in result.message
    [row] = await gate_rows(plan)
    gate = row.output["github_checks"]
    assert (row.status, gate["outcome"], gate["sha"]) == ("failed", "error", None)
    assert github.log == []


async def test_a_run_without_its_pull_request_stops_before_opening_the_gate(temporal_env, github):
    plan = await sync_to_async(create_gate)()

    result, _ = await run_gate(temporal_env, plan, {"issue": 12})

    assert not result.ok
    assert result.data["status"] == "unavailable"
    assert result.message == "the preceding output has no pull request at pr"
    assert await gate_rows(plan) == []
    assert github.log == []
    assert (await run_row(plan)).status == "failed"


async def test_a_failed_gate_returns_to_the_builder_with_its_result_as_feedback(temporal_env, github):
    plan = await sync_to_async(create_gate)(builder=True)
    github.listings[FIRST] = [[check("lint"), check("test", conclusion="failure")]]
    github.listings[SECOND] = [[check("lint"), check("test")]]
    dispatched = []

    @activity.defn(name="astrolift.workflow_stage.dispatch_agent_for_stage")
    async def dispatch(stage_id, execution_id, trigger_payload, resolved_config=None):
        dispatched.append(trigger_payload)
        if len(dispatched) == 2:
            github.head = SECOND  # the builder pushed its fix to the pull request
        return f"agent-run-{len(dispatched)}"

    @activity.defn(name="astrolift.workflow_stage.poll_agent_run_progress")
    async def progress(agent_run_id):
        return {"status": "succeeded", "input_wait_seconds": 0}

    @activity.defn(name="astrolift.workflow_stage.load_agent_run_outcome")
    async def outcome(agent_run_id):
        return {"result": {"pr": PULL, "run": agent_run_id}, "failure": None, "task_guid": None}

    result, _ = await run_gate(
        temporal_env, plan, {"story": "commit-endpoint"}, extra_activities=[dispatch, progress, outcome]
    )

    assert result.ok, result.message
    gates = await gate_rows(plan)
    assert [(row.round_number, row.status, row.output["github_checks"]["sha"]) for row in gates] == [
        (1, "failed", FIRST),
        (2, "completed", SECOND),
    ]
    assert gates[1].caused_by["edge"] == "ci->build"
    feedback = dispatched[1]["_astrolift_workflow"]["loop"]
    assert feedback["edge"] == "ci->build" and feedback["round_number"] == 2
    assert feedback["feedback"]["github_checks"]["outcome"] == "failed"
    assert [entry["state"] for entry in feedback["feedback"]["github_checks"]["checks"]] == [
        "passed",
        "failed",
    ]
    assert result.data["named_outputs"]["ci"]["github_checks"]["outcome"] == "passed"


def _closed_gate(output, status="completed"):
    plan = create_gate()
    return plan, WorkflowStageExecution.objects.create(
        slug=f"closed-{uuid.uuid4().hex[:8]}",
        workflow_run=plan["run"],
        stage=plan["gate"],
        status=status,
        output=output,
    )


def test_a_retried_poll_returns_the_recorded_decision_without_reading_github(github):
    decided = {
        "outcome": "passed",
        "repo": REPO,
        "pull_request": 7,
        "sha": FIRST,
        "required": ["lint"],
        "checks": [],
    }
    plan, row = _closed_gate({"github_checks": decided})

    assert _poll_github_checks_sync(str(plan["run"].pk), str(row.pk), CONFIG, 7, False) == decided

    _, closed = _closed_gate(None, status="failed")
    assert _poll_github_checks_sync(str(closed.workflow_run_id), str(closed.pk), CONFIG, 7, False) == {
        "outcome": "error",
        "error": "the stage execution closed before its checks were decided",
    }
    assert github.log == []


def test_a_poll_is_bound_to_its_own_run_and_a_github_checks_stage(github):
    plan, row = _closed_gate(None, status="running")
    other = create_gate()
    with pytest.raises(WorkflowStageExecution.DoesNotExist):
        _poll_github_checks_sync(str(other["run"].pk), str(row.pk), CONFIG, 7, False)

    WorkflowStage.objects.filter(pk=plan["gate"].pk).update(
        kind=WorkflowStage.StageKind.CHECKPOINT, iteration={}
    )
    with pytest.raises(RuntimeError, match="github_checks stage"):
        _poll_github_checks_sync(str(plan["run"].pk), str(row.pk), CONFIG, 7, False)
    assert github.log == []


def test_the_worker_registers_the_poll():
    assert poll_github_checks in ACTIVITIES
