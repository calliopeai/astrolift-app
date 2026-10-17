"""Agent runs read semantic layers (#2432).

A run a person launches reads its agent's brains and the ones the launch asks
for, as that person: tokens from the ``issueBrainReadToken`` machinery with
the run's ``agent_task`` claim, signed by a key of the run's own binding and
published while the run is in flight. Real Postgres and real role bindings,
so who can read a brain is decided by the same team shares as the Brains
screen; the cluster driver and the Temporal client are recorders.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import jwt
import pytest
from django.test import Client
from django.utils import timezone

import core.cluster_management as cluster_management
from astrolift_agents.models import AgentTask, DispatcherInstance
from astrolift_agents.schema.mutations import AgentsMutation, RunAstroliftAgentInput
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.services.agent_task_requests import reply_to_request
from astrolift_agents.tests.test_mcp_gateway import _request
from astrolift_agents.views.mcp import mcp_gateway
from astrolift_dispatch.agent_secrets import task_secret_name
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner
from astrolift_dispatch.spawners.local_docker import LocalDockerSpawner
from astrolift_dispatch.tests.test_model_gateway_1851 import _stored_in
from astrolift_identity.api_tokens import (
    SCOPE_MCP_DISPATCH,
    SCOPE_MCP_READ,
    SCOPE_READ_APPS,
    SCOPE_WRITE_APPS,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ApiToken
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_operations.models import AuditEvent
from astrolift_registry import brain_apps, brain_runs, brain_tokens
from astrolift_registry.models import (
    AppTeamAccess,
    BrainRunBinding,
    BrainTokenKey,
    Container,
    RegisteredApp,
    Workload,
)
from astrolift_workflows.activities import agent_stage
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user
from providers.k8s_native.edge_gateway import BRAIN_TOKEN_HEADER

pytestmark = pytest.mark.django_db

ISSUER = "https://astrolift.example.net"
CONTROLLER = "https://astro.example.net"
NAMESPACE = "astrolift-agents-rb2432"
LAUNCHER = [
    Permission.AGENT_DISPATCH,
    Permission.AGENT_READ,
    Permission.AGENT_TASK_SEND_INPUT,
    Permission.APP_READ,
]


@pytest.fixture(autouse=True)
def _env(monkeypatch, settings):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    settings.APP_BASE_URL = ISSUER
    settings.PLATFORM_API_URL = CONTROLLER


@pytest.fixture
def started(monkeypatch):
    calls = []

    def start(name, args, *, workflow_id, task_queue=None, replace_running=True):
        from astrolift_workflows.client import WorkflowHandle

        calls.append((name, args, workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id="r", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", start)
    return calls


@pytest.fixture
def audit(monkeypatch):
    rows = []
    monkeypatch.setattr("core.mutations._audit_writer", rows.append)
    return rows


class _Driver:
    def __init__(self):
        self.applied: list[list[dict]] = []

    def ensure_namespace(self, cluster_slug, namespace, labels, annotations):
        pass

    def apply_manifests(self, cluster_slug, namespace, manifests):
        self.applied.append(json.loads(json.dumps(manifests)))
        return SimpleNamespace(ok=True, summary=lambda: "")

    def delete_manifests(self, cluster_slug, namespace, refs, *, propagation_policy=None):
        return SimpleNamespace(ok=True, summary=lambda: "")


@pytest.fixture
def driver(monkeypatch):
    fake = _Driver()
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda cluster: fake)
    monkeypatch.setattr(
        cluster_management, "_context_for_cluster", lambda cluster: SimpleNamespace(slug="kind")
    )
    return fake


def _world(suffix):
    world = ScopeWorld(suffix)
    world.user = make_user(suffix)
    world.cluster = make_cluster(world, suffix)
    bind_role(
        world.user, permissions=LAUNCHER, kind="TEAM", scope_id=world.medops.pk, slug=f"launch-{suffix}"
    )
    return world


@pytest.fixture
def world():
    return _world("rb2432")


@pytest.fixture
def other():
    return _world("rbx2432")


def _brain(world, slug, *, team=None, served=True):
    team = team or world.medops
    project = world.medops_project if team == world.medops else world.platform_project
    app = RegisteredApp.objects.create(
        organization=world.org,
        team=team,
        project=project,
        name=slug.replace("-", " ").title(),
        slug=slug,
        app_kind="mimwell",
        k8s_namespace=f"{world.org.slug}-{slug}",
        provisioning_status="ready",
    )
    if served:
        AppEnvironment.objects.create(
            registered_app=app,
            tenant_cluster=world.cluster,
            name="production",
            url=f"https://{slug}.acme.test",
        )
    return app


def _shared(world, slug):
    """A brain of the platform team, shared with the launcher's team."""
    brain = _brain(world, slug, team=world.platform)
    share = AppTeamAccess.objects.create(
        registered_app=brain, team=world.medops, access_level=AppTeamAccess.AccessLevel.VIEWER
    )
    return brain, share


def _unshare(share):
    share.deleted_at = timezone.now()
    share.save(update_fields=["deleted_at"])


def _agent(world, *, brains=(), allowed=None, slug="triage"):
    app = RegisteredApp.objects.create(
        organization=world.org,
        team=world.medops,
        project=world.medops_project,
        name=f"Agents {slug}",
        slug=f"agents-{slug}-{world.org.slug}",
        k8s_namespace=f"{world.org.slug}-agents-{slug}",
        provisioning_status="ready",
    )
    workload = Workload.objects.create(
        registered_app=app,
        name=slug.title(),
        slug=slug,
        kind=Workload.Kind.AGENT,
        brains=list(brains),
        brains_allowed=list(allowed) if allowed is not None else None,
    )
    Container.objects.create(
        workload=workload,
        name="agent",
        is_primary=True,
        image_ref="docker.io/calliopeai/astrolift-agent-claude:1.0",
    )
    return workload


@contextmanager
def _as(world, user=None, token=None):
    user = user or world.user
    team_id = token.team_id if token is not None else None
    with tenant_context(TenantContext(organization_id=world.org.pk, team_id=team_id, actor_user_id=user.pk)):
        marker = set_current_api_token(token) if token is not None else None
        try:
            yield make_info(user)
        finally:
            if marker is not None:
                reset_current_api_token(marker)


def _run(world, agent, brains=None, **extra):
    with _as(world) as info:
        return AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=agent.slug, brain_slugs=brains, **extra)
        )


def _task(result):
    assert result.ok, result.errors
    return AgentTask.objects.get(guid=str(result.data.id))


def _spawn(world, task):
    result = K8sJobSpawner(world.cluster, NAMESPACE).spawn(task)
    assert result.ok, result.error
    return result


def _job_and_secret(driver):
    batch = driver.applied[-1]
    [job] = [m for m in batch if m["kind"] == "Job"]
    [secret] = [m for m in batch if m["kind"] == "Secret"]
    return job, secret


def _container_env(job):
    return {entry["name"]: entry for entry in job["spec"]["template"]["spec"]["containers"][0]["env"]}


def _published():
    return {key["kid"] for key in brain_tokens.jwks()["keys"]}


def _verify(token, brain):
    keys = {key["kid"]: key for key in brain_tokens.jwks()["keys"]}
    public = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(keys[jwt.get_unverified_header(token)["kid"]]))
    return jwt.decode(token, public, algorithms=["ES256"], audience=str(brain.guid), issuer=ISSUER)


def _refresh(task, key):
    return Client().post(
        f"/api/dispatch/v1/agents/{task.guid}/brains/",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {key}",
    )


def _running(task):
    task.transition_to(AgentTask.Status.PROVISIONING)
    task.transition_to(AgentTask.Status.RUNNING)


def _binding(task, slug):
    return BrainRunBinding.objects.get(agent_task=task, slug=slug)


def _sub(user):
    return str(getattr(user, "guid", "") or user.pk)


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


def test_a_run_reads_its_agents_brains_then_the_ones_it_asks_for(world, started, audit):
    _brain(world, "kb")
    _brain(world, "docs")
    agent = _agent(world, brains=["kb"])

    result = _run(world, agent, ["docs", "kb", " docs "])

    task = _task(result)
    assert [(b.slug, b.name, b.source, b.revoked_at) for b in result.data.brains] == [
        ("kb", "Kb", "default", None),
        ("docs", "Docs", "requested", None),
    ]
    assert task.triggered_by_user_id == world.user.pk
    [row] = [entry for entry in audit if entry.action == "agents.agent.dispatch"]
    assert row.extra == {"agent_task": str(task.guid), "brains": ["kb", "docs"]}
    with _as(world) as info:
        detail = AgentsQuery().agent_task(info, id=str(task.guid))
    assert [(b.slug, b.source) for b in detail.brains] == [("kb", "default"), ("docs", "requested")]
    assert len(started) == 1


def test_a_run_without_brains_binds_none(world, started):
    task = _task(_run(world, _agent(world)))
    assert not BrainRunBinding.objects.filter(agent_task=task).exists()


def test_another_orgs_brain_or_one_the_launcher_cannot_read_is_not_found(world, other, started):
    _brain(other, "theirs")
    _brain(world, "platform-kb", team=world.platform)
    agent = _agent(world)

    for slug in ("theirs", "platform-kb", "nowhere"):
        result = _run(world, agent, [slug])
        assert not result.ok
        error = result.errors[0]
        assert (error.code, error.field, error.message) == (
            "NOT_FOUND",
            "brainSlugs",
            f"brain {slug!r} not found",
        )

    # The agent's own brains are checked as the launcher too.
    defaults = _agent(world, brains=["platform-kb"], slug="needs-platform")
    assert _run(world, defaults).errors[0].code == "NOT_FOUND"
    assert not AgentTask.objects.exists()
    assert not started


def test_a_brain_outside_the_allow_list_is_refused(world, started):
    for slug in ("kb", "docs", "hr"):
        _brain(world, slug)
    agent = _agent(world, brains=["kb"], allowed=["docs"])

    assert _run(world, agent, ["docs", "kb"]).ok  # its own brains are always accepted

    refused = _run(world, agent, ["hr"])
    assert (refused.errors[0].code, refused.errors[0].field) == ("VALIDATION", "brainSlugs")
    assert refused.errors[0].message == "agent 'triage' does not accept brain 'hr'; it accepts docs"

    closed = _agent(world, slug="closed", allowed=[])
    assert "accepts no brains besides its own" in _run(world, closed, ["docs"]).errors[0].message


def test_an_unserved_brain_or_too_many_are_refused(world, started):
    _brain(world, "dark", served=False)
    agent = _agent(world)

    dark = _run(world, agent, ["dark"])
    assert (dark.errors[0].code, dark.errors[0].field) == ("PRECONDITION", "brainSlugs")
    assert "not served" in dark.errors[0].message

    many = _run(world, agent, [f"kb{i}" for i in range(brain_runs.MAX_RUN_BRAINS + 1)])
    assert (many.errors[0].code, many.errors[0].field) == ("VALIDATION", "brainSlugs")
    assert not AgentTask.objects.exists()


def test_a_replay_compares_the_brains_it_asks_for(world, started):
    _brain(world, "kb")
    _brain(world, "docs")
    agent = _agent(world)
    key = str(uuid.uuid4())

    first = _task(_run(world, agent, ["kb"], client_request_id=key))
    assert _task(_run(world, agent, ["kb"], client_request_id=key)).pk == first.pk

    changed = _run(world, agent, ["docs"], client_request_id=key)
    assert (changed.errors[0].code, changed.errors[0].field) == ("PRECONDITION", "clientRequestId")
    assert AgentTask.objects.count() == 1


def test_a_retry_asks_again_for_the_brains_the_original_asked_for(world, started):
    _brain(world, "kb")
    _brain(world, "docs")
    agent = _agent(world, brains=["kb"])
    original = _task(_run(world, agent, ["docs"]))
    original.transition_to(AgentTask.Status.FAILED)

    with _as(world) as info:
        retried = AgentsMutation().retry_agent_task(info, id=str(original.guid))

    assert retried.ok, retried.errors
    assert [(b.slug, b.source) for b in retried.data.brains] == [("kb", "default"), ("docs", "requested")]


def test_the_manifest_declares_an_agents_brains(world):
    app = _agent(world).registered_app
    manifest = """
name = "agents"

[[workloads]]
name = "triage"
kind = "agent"
brains = ["kb"]
brains_allowed = ["docs"]
"""
    persist_manifest(app, normalize(parse_raw(manifest)), raw_text=manifest)
    workload = Workload.objects.get(registered_app=app, slug="triage")
    assert (workload.brains, workload.brains_allowed) == (["kb"], ["docs"])

    bare = manifest.replace('brains = ["kb"]\nbrains_allowed = ["docs"]\n', "")
    persist_manifest(app, normalize(parse_raw(bare)), raw_text=bare)
    workload.refresh_from_db()
    assert (workload.brains, workload.brains_allowed) == ([], None)


# ---------------------------------------------------------------------------
# Spawn: what the pod gets
# ---------------------------------------------------------------------------


def test_the_pod_reads_each_brain_from_its_env_and_the_index(world, started, driver, audit):
    kb = _brain(world, "kb")
    docs = _brain(world, "docs")
    task = _task(_run(world, _agent(world, brains=["kb"]), ["docs"]))

    _spawn(world, task)

    job, secret = _job_and_secret(driver)
    env = _container_env(job)
    secret_name = task_secret_name(f"agent-task-{task.guid.hex}")
    for slug, brain in (("KB", kb), ("DOCS", docs)):
        assert env[f"BRAIN_{slug}_MCP_URL"]["value"] == f"https://{brain.slug}.acme.test/mcp"
        assert env[f"BRAIN_{slug}_TOKEN_HEADER"]["value"] == BRAIN_TOKEN_HEADER
        assert env[f"BRAIN_{slug}_TOKEN"] == {
            "name": f"BRAIN_{slug}_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": secret_name, "key": f"BRAIN_{slug}_TOKEN"}},
        }
        token = secret["stringData"][f"BRAIN_{slug}_TOKEN"]
        claims = _verify(token, brain)
        assert (claims["sub"], claims["email"], claims["scope"]) == (
            _sub(world.user),
            world.user.email,
            "brain:read",
        )
        assert claims["agent_task"] == str(task.guid)
        assert claims["exp"] - claims["iat"] == 15 * 60
        # The token is in the run's Secret and nowhere else.
        assert token not in json.dumps(job)
        assert _stored_in(token) == []

    index = json.loads(env["BRAINS"]["value"])
    assert index["version"] == 1
    assert index["refresh_url"] == f"{CONTROLLER}/api/dispatch/v1/agents/{task.guid}/brains/"
    assert [
        (entry["slug"], entry["id"], entry["source"], entry["mcp_urls"], entry["header"], entry["env"])
        for entry in index["brains"]
    ] == [
        (
            "kb",
            str(kb.guid),
            "default",
            ["https://kb.acme.test/mcp"],
            BRAIN_TOKEN_HEADER,
            {
                "mcp_url": "BRAIN_KB_MCP_URL",
                "token_header": "BRAIN_KB_TOKEN_HEADER",
                "token": "BRAIN_KB_TOKEN",
            },
        ),
        (
            "docs",
            str(docs.guid),
            "requested",
            ["https://docs.acme.test/mcp"],
            BRAIN_TOKEN_HEADER,
            {
                "mcp_url": "BRAIN_DOCS_MCP_URL",
                "token_header": "BRAIN_DOCS_TOKEN_HEADER",
                "token": "BRAIN_DOCS_TOKEN",
            },
        ),
    ]
    issued = [entry for entry in audit if entry.action == "brain.token.issue"]
    assert sorted(entry.target_id for entry in issued) == ["docs", "kb"]
    assert all(
        entry.extra["agent_task"] == str(task.guid)
        and entry.extra["surface"] == "agent_run"
        and entry.extra["reason"] == "launch"
        and entry.actor_user_id == world.user.pk
        for entry in issued
    )


def test_a_brain_lost_while_the_run_was_queued_fails_the_spawn(world, started, driver):
    _shared_brain, share = _shared(world, "shared")
    task = _task(_run(world, _agent(world), ["shared"]))
    _unshare(share)

    result = K8sJobSpawner(world.cluster, NAMESPACE).spawn(task)

    assert not result.ok
    assert result.error == f"brain 'shared': {brain_runs.ACCESS_LOST}"
    assert not driver.applied
    assert _binding(task, "shared").revoked_reason == brain_runs.ACCESS_LOST


def test_a_local_docker_run_gets_the_same_env_and_its_errors_carry_no_token(world, started, monkeypatch):
    _brain(world, "kb")
    task = _task(_run(world, _agent(world, brains=["kb"])))
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr("astrolift_dispatch.spawners.local_docker.subprocess.run", run)
    result = LocalDockerSpawner().spawn(task)

    env = dict(arg.split("=", 1) for arg in commands[0] if arg.startswith(("BRAIN", "BRAINS=")))
    assert env["BRAIN_KB_MCP_URL"] == "https://kb.acme.test/mcp"
    assert json.loads(env["BRAINS"])["brains"][0]["slug"] == "kb"
    assert not result.ok
    assert env["BRAIN_KB_TOKEN"] not in result.error and "[redacted]" in result.error


# ---------------------------------------------------------------------------
# Refresh, recheck and revocation
# ---------------------------------------------------------------------------


def _launched(world, driver, brains=(), asked=None, slug="triage"):
    """A spawned run and its callback key."""
    task = _task(_run(world, _agent(world, brains=brains, slug=slug), asked))
    _spawn(world, task)
    job, _secret = _job_and_secret(driver)
    return task, _container_env(job)["ASTROLIFT_CLUSTER_KEY"]["value"]


def test_a_refresh_checks_again_and_tells_the_run_what_it_lost(world, started, driver, audit):
    kb = _brain(world, "kb")
    shared, share = _shared(world, "shared")
    task, key = _launched(world, driver, brains=["kb"], asked=["shared"])
    shared_kid = BrainTokenKey.objects.get(run_binding=_binding(task, "shared")).kid

    fresh = _refresh(task, key)
    assert fresh.status_code == 200, fresh.content
    body = fresh.json()
    assert [entry["slug"] for entry in body["brains"]] == ["kb", "shared"]
    assert _verify(body["brains"][1]["token"], shared)["agent_task"] == str(task.guid)
    assert body["revoked"] == []

    _unshare(share)
    after = _refresh(task, key).json()

    assert [entry["slug"] for entry in after["brains"]] == ["kb"]
    assert _verify(after["brains"][0]["token"], kb)["sub"] == _sub(world.user)
    [lost] = after["revoked"]
    assert (lost["slug"], lost["reason"]) == ("shared", brain_runs.ACCESS_LOST)
    assert shared_kid not in _published()
    assert not BrainTokenKey.objects.filter(kid=shared_kid).exists()
    with _as(world) as info:
        detail = AgentsQuery().agent_task(info, id=str(task.guid))
    assert [(b.slug, b.revoked_reason) for b in detail.brains] == [
        ("kb", ""),
        ("shared", brain_runs.ACCESS_LOST),
    ]
    [revoked] = [entry for entry in audit if entry.action == "brain.token.revoke"]
    assert (revoked.target_id, revoked.extra["agent_task"], revoked.extra["reason"]) == (
        "shared",
        str(task.guid),
        brain_runs.ACCESS_LOST,
    )
    assert {entry.extra["reason"] for entry in audit if entry.action == "brain.token.issue"} == {
        "launch",
        "refresh",
    }


def test_ending_the_run_revokes_its_tokens(world, started, driver):
    _brain(world, "kb")
    task, key = _launched(world, driver, brains=["kb"])
    kid = BrainTokenKey.objects.get(run_binding__agent_task=task).kid
    assert kid in _published()

    _running(task)
    task.transition_to(AgentTask.Status.COMPLETED)

    assert kid not in _published()
    assert not BrainTokenKey.objects.filter(kid=kid).exists()
    assert _binding(task, "kb").revoked_reason == brain_runs.ENDED
    assert _refresh(task, key).status_code == 401  # the run's callback key ended with it


def test_a_cancel_revokes_the_tokens_before_the_pod_is_gone(world, started, driver, monkeypatch):
    _brain(world, "kb")
    task, key = _launched(world, driver, brains=["kb"])
    kid = BrainTokenKey.objects.get(run_binding__agent_task=task).kid
    _running(task)
    pending = {
        "ok": False,
        "status": "running",
        "error": "Container deletion is still in progress",
        "pending": True,
    }
    monkeypatch.setattr(agent_stage, "_cancel_agent_task_locked", lambda guid, **kwargs: pending)

    assert agent_stage._cancel_agent_task_sync(str(task.guid)) == pending

    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING and task.cancel_requested_at is not None
    assert kid not in _published()
    assert _binding(task, "kb").revoked_reason == brain_runs.CANCELLED
    refused = _refresh(task, key)
    assert refused.status_code == 409 and refused.json()["continue"] is False


def test_a_steer_checks_the_brains_again(world, started, driver):
    _shared_brain, share = _shared(world, "shared")
    task, _key = _launched(world, driver, asked=["shared"])
    _running(task)
    _unshare(share)

    with _as(world) as info:
        steered = AgentsMutation().send_agent_task_input(
            info, task_id=str(task.guid), message="Also check the logs"
        )

    assert steered.ok, steered.errors
    assert _binding(task, "shared").revoked_reason == brain_runs.ACCESS_LOST


def test_a_gate_answer_checks_the_brains_again(world, started, driver):
    _shared_brain, share = _shared(world, "shared")
    task, key = _launched(world, driver, asked=["shared"])
    _running(task)
    opening = {
        "sequence": 1,
        "turn_id": "turn-1",
        "message_id": "approval-1",
        "kind": "approval_required",
        "text": "Allow this command?",
        "request": {"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"command": "ls"}}},
    }
    asked = Client().post(
        f"/api/dispatch/v1/agents/{task.guid}/callback/",
        content_type="application/json",
        data=json.dumps({"status": "running", "events": [opening]}),
        HTTP_AUTHORIZATION=f"Bearer {key}",
    )
    assert asked.status_code == 200, asked.content
    _unshare(share)

    reply_to_request(task=task, sequence=1, response={"decision": "deny"}, author=world.user)

    assert _binding(task, "shared").revoked_reason == brain_runs.ACCESS_LOST


def test_a_recheck_reads_as_the_launcher_not_as_the_token_that_steers(world, started, driver):
    """A teammate steering through a team-scoped token must not revoke a
    brain the launcher still reads, and must not keep one they lost."""
    launcher = make_user("rb2432-lead")
    bind_role(launcher, permissions=LAUNCHER, kind="ORG", scope_id=world.org.pk, slug="rb-lead")
    _brain(world, "platform-kb", team=world.platform)
    agent = _agent(world)
    with _as(world, user=launcher) as info:
        result = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=agent.slug, brain_slugs=["platform-kb"])
        )
    task = _task(result)
    _running(task)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="steer",
        token_hash=uuid.uuid4().hex,
        token_last_4="2432",
        scopes=[SCOPE_READ_APPS, SCOPE_MCP_DISPATCH],
    )

    with _as(world, token=token) as info:
        assert AgentsMutation().send_agent_task_input(info, task_id=str(task.guid), message="Go on").ok
    assert _binding(task, "platform-kb").revoked_at is None

    launcher.role_bindings.all().delete()
    with _as(world, token=token) as info:
        assert AgentsMutation().send_agent_task_input(info, task_id=str(task.guid), message="Go on").ok
    assert _binding(task, "platform-kb").revoked_reason == brain_runs.ACCESS_LOST


def test_archiving_the_brain_revokes_the_runs_reading_it(world, started, driver):
    kb = _brain(world, "kb")
    task, key = _launched(world, driver, brains=["kb"])

    brain_apps.revoke_app(kb.pk)

    assert _binding(task, "kb").revoked_reason == brain_runs.BRAIN_GONE
    body = _refresh(task, key).json()
    assert body["brains"] == [] and body["revoked"][0]["reason"] == brain_runs.BRAIN_GONE


def test_the_refresh_answers_the_runs_own_key_only(world, started, driver):
    _brain(world, "kb")
    task, _key = _launched(world, driver, brains=["kb"])
    plaintext = "dispatcher-key-2432"
    DispatcherInstance.objects.create(
        organization=world.org,
        name="Dispatcher",
        slug="rb2432-dispatcher",
        endpoint="https://dispatcher.example",
        api_key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )

    assert _refresh(task, plaintext).status_code == 403
    assert _refresh(task, "alft_cb_not-this-runs-key").status_code == 401


def test_a_run_key_is_published_only_while_its_run_is_in_flight(world, started, driver):
    _brain(world, "kb")
    task, _key = _launched(world, driver, brains=["kb"])
    kid = BrainTokenKey.objects.get(run_binding__agent_task=task).kid
    assert kid in _published()

    # Settled without its revocation having run (a crash between the two, say).
    AgentTask.objects.filter(pk=task.pk).update(status=AgentTask.Status.FAILED)
    assert kid not in _published()
    AgentTask.objects.filter(pk=task.pk).update(
        status=AgentTask.Status.RUNNING, cancel_requested_at=timezone.now()
    )
    assert kid not in _published()


def test_the_install_key_is_never_a_run_key(world, started, driver):
    _brain(world, "kb")
    task, _key = _launched(world, driver, brains=["kb"])
    run_key = BrainTokenKey.objects.get(run_binding__agent_task=task)

    install = brain_tokens.signing_key()

    assert install.kid != run_key.kid and install.run_binding_id is None
    run_key.refresh_from_db()
    assert run_key.retired_at is None  # the install's rotation does not touch it
    brain_runs.revoke_run(task, brain_runs.ENDED)
    assert run_key.kid not in _published() and install.kid in _published()


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def _tool(world, token, name, **arguments):
    marker = set_current_api_token(token)
    try:
        with tenant_context(
            TenantContext(organization_id=world.org.pk, team_id=token.team_id, actor_user_id=world.user.pk)
        ):
            response = mcp_gateway(
                _request(world.user, token, "tools/call", {"name": name, "arguments": arguments})
            )
    finally:
        reset_current_api_token(marker)
    return json.loads(response.content)["result"]


def test_mcp_runs_take_the_same_brains_and_the_task_lists_them(world, started):
    _brain(world, "kb")
    _brain(world, "docs")
    _brain(world, "platform-kb", team=world.platform)
    agent = _agent(world, brains=["kb"])
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="mcp",
        token_hash=uuid.uuid4().hex,
        token_last_4="2432",
        scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH, SCOPE_READ_APPS, SCOPE_WRITE_APPS],
    )

    ran = _tool(world, token, "astrolift_run_agent", agent_slug=agent.slug, brain_slugs=["docs"])

    assert ran["isError"] is False, ran
    payload = ran["structuredContent"]
    assert [(b["slug"], b["source"]) for b in payload["brains"]] == [("kb", "default"), ("docs", "requested")]
    got = _tool(world, token, "astrolift_get_task", task_id=payload["task_id"])["structuredContent"]
    assert [b["slug"] for b in got["brains"]] == ["kb", "docs"]
    row = AuditEvent.objects.get(action="mcp.tool.astrolift_run_agent", decision="ALLOW")
    assert row.data["brains"] == ["kb", "docs"] and row.data["agent_task"] == payload["task_id"]

    refused = _tool(world, token, "astrolift_run_agent", agent_slug=agent.slug, brain_slugs=["platform-kb"])
    assert refused["isError"] is True
    assert "brain 'platform-kb' not found" in refused["content"][0]["text"]
