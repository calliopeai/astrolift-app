"""Tests for the agent trigger binding (spec 33, PR-6).

A ``WorkflowWebhook`` can bind to an agent ``Workload(kind=agent)`` instead
of a ``WorkflowDefinition``; when such a webhook fires (via the SCM fan-out)
it dispatches an AgentTask through the PR-1 ``runAstroliftAgent`` path with
the binding's ``input_mapping`` applied to the incoming payload.

Layers:
  * ``apply_input_mapping`` — pure-Python payload shaping (passthrough,
    dotted-path projection, missing-path → None, literal injection).
  * ``dispatch_agent_task_from_webhook`` — DB-backed: creates the AgentTask
    + enqueues DispatchAgentTaskWorkflow with the mapped payload (Temporal
    stubbed); honours run_paused + soft-delete; rejects a non-agent target.
  * SCM fan-out routing — both the app-scoped ``_dispatch_workflow_webhooks``
    (the live ``/api/webhooks/github/<guid>/`` receiver) and the org+SCM
    ``route_scm_push_to_workflow_webhooks`` route an agent-bound webhook to
    the agent dispatch, leaving definition-bound webhooks on the workflow
    path.
  * The workflow-vs-agent XOR (model ``clean`` + DB CheckConstraint).

Acceptance cases (spec §PR-6):
  (2) a Trigger agent dispatches a Task when its bound webhook fires with the
      mapped input;
  (3) pausing the run-spec (run_paused=True) halts trigger dispatch;
  (4) condition-triggers are explicitly out of scope — there is no condition
      target column, and a webhook bound to NEITHER target is rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client

from astrolift_agents.models import AgentTask, WorkflowWebhook
from astrolift_agents.services.workflow_triggers import (
    apply_input_mapping,
    dispatch_agent_task_from_webhook,
)
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# apply_input_mapping (pure policy — no DB)
# ---------------------------------------------------------------------------


def test_mapping_empty_passes_whole_payload_through():
    payload = {"ref": "refs/heads/main", "repository": {"full_name": "acme/x"}}
    assert apply_input_mapping(payload, {}) == payload
    assert apply_input_mapping(payload, None) == payload


def test_mapping_projects_dotted_paths():
    payload = {
        "pull_request": {"number": 42, "head": {"ref": "feature/x"}},
        "repository": {"full_name": "acme/x"},
    }
    mapping = {"pr": "pull_request.number", "branch": "pull_request.head.ref", "repo": "repository.full_name"}
    assert apply_input_mapping(payload, mapping) == {"pr": 42, "branch": "feature/x", "repo": "acme/x"}


def test_mapping_missing_path_yields_none_not_error():
    payload = {"a": {"b": 1}}
    assert apply_input_mapping(payload, {"x": "a.missing", "y": "nope.deep.path"}) == {"x": None, "y": None}


def test_mapping_only_emits_mapped_keys():
    """A non-empty mapping does NOT leak unmapped payload fields."""
    payload = {"keep": 1, "drop": 2}
    assert apply_input_mapping(payload, {"keep": "keep"}) == {"keep": 1}


def test_mapping_non_string_value_is_a_literal():
    """A non-string mapping value is injected verbatim (constant)."""
    assert apply_input_mapping({"a": 1}, {"const": 7, "from_payload": "a"}) == {"const": 7, "from_payload": 1}


# ---------------------------------------------------------------------------
# Fixtures for the DB-backed paths
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture(autouse=True)
def _temporal_disabled(settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False


@pytest.fixture
def temporal_recorder(monkeypatch):
    """Record DispatchAgentTaskWorkflow starts without a Temporal server.

    The helper imports ``start_workflow`` lazily from
    ``astrolift_workflows.client``; patch it at the source module.
    """
    starts: list[tuple] = []

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, list(args), workflow_id))
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(workflow_id=workflow_id, run_id="r", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)
    return starts


@pytest.fixture
def agent(db):
    org = Organization.objects.create(name="Trig Co", slug="trig-co")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-trig")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-trig")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Trigger App",
        slug="trigger-app",
        provisioning_status="ready",
    )
    workload = Workload.objects.create(
        registered_app=app,
        name="Trigger Agent",
        slug="trigger-agent",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
        run_mode=Workload.RunMode.TRIGGER.value,
    )
    return {"org": org, "app": app, "workload": workload}


def _agent_webhook(agent, *, slug, input_mapping=None, enabled=True):
    return WorkflowWebhook.objects.create(
        agent_definition=agent["workload"],
        registered_app=agent["app"],
        organization=agent["org"],
        slug=slug,
        secret_hash=hashlib.sha256(b"x").hexdigest(),
        input_mapping=input_mapping or {},
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# dispatch_agent_task_from_webhook
# ---------------------------------------------------------------------------


def test_dispatch_from_webhook_creates_task_with_mapped_input(agent, temporal_recorder):
    """Acceptance (2): a bound webhook firing dispatches a Task — an
    AgentTask(agent_definition=...) QUEUED + DispatchAgentTaskWorkflow keyed
    to its guid, carrying the mapped payload as trigger_payload."""
    hook = _agent_webhook(agent, slug="agent-hook", input_mapping={"pr": "pull_request.number"})
    payload = {"pull_request": {"number": 99}, "extra": "ignored-by-mapping"}

    task = dispatch_agent_task_from_webhook(hook, payload)

    assert task is not None
    assert task.agent_definition_id == agent["workload"].id
    assert task.organization_id == agent["org"].id
    assert task.status == AgentTask.Status.QUEUED
    assert task.queued_at is not None
    # A webhook started it and no person did (#2152).
    assert task.trigger_kind == "webhook"
    assert task.triggered_by_user_id is None

    # Exactly one DispatchAgentTaskWorkflow enqueued, keyed to the task guid,
    # carrying the task pk AND the mapped trigger payload.
    assert len(temporal_recorder) == 1
    name, args, workflow_id = temporal_recorder[0]
    assert name == "DispatchAgentTaskWorkflow"
    assert workflow_id == f"DispatchAgentTaskWorkflow-{task.guid}"
    assert args[0].agent_task_id == task.pk
    assert args[0].actor.kind == "system"
    # The input mapping was applied — only the mapped key, projected value.
    assert args[0].trigger_payload == {"pr": 99}
    # ...and the mapped payload is ALSO frozen on the task itself so the
    # spawner surfaces it to the pod as ASTROLIFT_TRIGGER_PAYLOAD (#930).
    assert task.dispatch_input == {"pr": 99}


def test_dispatch_from_webhook_empty_mapping_passes_whole_payload(agent, temporal_recorder):
    hook = _agent_webhook(agent, slug="agent-hook-raw", input_mapping={})
    payload = {"ref": "refs/heads/main", "n": 1}

    task = dispatch_agent_task_from_webhook(hook, payload)

    assert task is not None
    assert temporal_recorder[0][1][0].trigger_payload == payload


def test_dispatch_from_webhook_start_failure_terminalizes_task(agent, monkeypatch):
    hook = _agent_webhook(agent, slug="agent-hook-start-fails")

    def _fail_start(*_args, **_kwargs):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _fail_start)

    task = dispatch_agent_task_from_webhook(hook, {"x": 1})

    task.refresh_from_db()
    assert task.status == AgentTask.Status.FAILED
    assert "workflow failed to start" in task.failure["message"]


def test_dispatch_from_webhook_paused_agent_is_noop(agent, temporal_recorder):
    """Acceptance (3): a paused agent's bound webhook dispatches nothing."""
    agent["workload"].run_paused = True
    agent["workload"].save(update_fields=["run_paused", "updated_at", "version"])
    hook = _agent_webhook(agent, slug="agent-hook-paused")

    task = dispatch_agent_task_from_webhook(hook, {"x": 1})

    assert task is None
    assert AgentTask.objects.count() == 0
    assert temporal_recorder == []


def test_dispatch_from_webhook_soft_deleted_agent_is_noop(agent, temporal_recorder):
    """A torn-down (soft-deleted) agent stops dispatching even though the FK
    row survives — matches the loop/cron selectors' soft-delete guard."""
    agent["workload"].soft_delete()
    hook = WorkflowWebhook.objects.create(
        agent_definition_id=agent["workload"].id,
        registered_app=agent["app"],
        organization=agent["org"],
        slug="agent-hook-deleted",
        secret_hash=hashlib.sha256(b"x").hexdigest(),
        input_mapping={},
        enabled=True,
    )

    task = dispatch_agent_task_from_webhook(hook, {"x": 1})

    assert task is None
    assert AgentTask.objects.count() == 0
    assert temporal_recorder == []


def test_dispatch_from_webhook_non_agent_target_is_noop(agent, temporal_recorder):
    """A webhook whose bound Workload is not kind=agent dispatches nothing
    (defends the binding at fire time)."""
    deployment = Workload.objects.create(
        registered_app=agent["app"],
        name="Web",
        slug="web",
        kind=Workload.Kind.DEPLOYMENT.value,
    )
    hook = WorkflowWebhook.objects.create(
        agent_definition=deployment,
        registered_app=agent["app"],
        organization=agent["org"],
        slug="agent-hook-nonagent",
        secret_hash=hashlib.sha256(b"x").hexdigest(),
        input_mapping={},
        enabled=True,
    )

    task = dispatch_agent_task_from_webhook(hook, {"x": 1})

    assert task is None
    assert AgentTask.objects.count() == 0


# ---------------------------------------------------------------------------
# Workflow-vs-agent XOR (clean + DB CheckConstraint) — incl. condition OOS
# ---------------------------------------------------------------------------


def _definition(slug):
    from workflows.models import WorkflowDefinition

    return WorkflowDefinition.objects.create(
        name=f"Def {slug}",
        slug=slug,
        model_label="workflows.workflowdefinition",
        states=[{"name": "s", "label": "S", "is_initial": True, "is_final": True}],
        transitions=[],
        is_enabled=True,
    )


def test_clean_rejects_both_targets(agent):
    """A webhook bound to BOTH a definition and an agent fails full_clean."""
    defn = _definition("both-def")
    hook = WorkflowWebhook(
        workflow_definition=defn,
        agent_definition=agent["workload"],
        slug="both",
        secret_hash="x",
    )
    with pytest.raises(ValidationError):
        hook.clean()


def test_clean_rejects_neither_target():
    """Acceptance (4): a webhook bound to NEITHER target is rejected. This is
    where a hypothetical 'condition' trigger would live — there is no
    condition column, so condition binding has no representation and an
    untargeted webhook is invalid."""
    hook = WorkflowWebhook(slug="neither", secret_hash="x")
    with pytest.raises(ValidationError):
        hook.clean()


def test_db_constraint_rejects_both_targets(agent):
    """The DB CheckConstraint is the hard guarantee behind ``clean``."""
    defn = _definition("both-db-def")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            # Bypass full_clean by going straight to create — the DB constraint
            # must still refuse two targets.
            WorkflowWebhook.objects.create(
                workflow_definition=defn,
                agent_definition=agent["workload"],
                slug="both-db",
                secret_hash="x",
            )


def test_db_constraint_rejects_neither_target():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorkflowWebhook.objects.create(slug="neither-db", secret_hash="x")


def test_agent_only_webhook_is_valid(agent):
    """The happy binding: agent set, definition null — clean + persists."""
    hook = WorkflowWebhook(
        agent_definition=agent["workload"],
        slug="agent-only",
        secret_hash="x",
    )
    hook.clean()  # no raise
    hook.save()
    assert WorkflowWebhook.objects.filter(slug="agent-only").exists()


# ---------------------------------------------------------------------------
# SCM fan-out routing → agent dispatch (org+SCM matcher)
# ---------------------------------------------------------------------------


def test_route_scm_dispatches_agent_for_bound_webhook(agent, temporal_recorder):
    """Acceptance (2) via the org+SCM fan-out: a push event matching an
    agent-bound webhook dispatches a Task (not a WorkflowInstance)."""
    from astrolift_agents.services.workflow_triggers import (
        ScmEvent,
        route_scm_push_to_workflow_webhooks,
    )

    _agent_webhook(agent, slug="scm-agent-hook", input_mapping={"branch": "branch"})

    event = ScmEvent(
        organization_id=agent["org"].id,
        repo_full_name="acme/x",
        branch="main",
        head_sha="f" * 40,
        event_kind="push",
    )
    instances = route_scm_push_to_workflow_webhooks(event)

    # Agent dispatch is not a WorkflowInstance, so none returned...
    assert instances == []
    # ...but a Task WAS dispatched with the mapped input.
    assert AgentTask.objects.filter(agent_definition=agent["workload"]).count() == 1
    assert len(temporal_recorder) == 1
    assert temporal_recorder[0][1][0].trigger_payload == {"branch": "main"}

    # last_triggered_at stamped on the fired webhook.
    hook = WorkflowWebhook.objects.get(slug="scm-agent-hook")
    assert hook.last_triggered_at is not None


def test_route_scm_paused_agent_not_stamped(agent, temporal_recorder):
    """Acceptance (3) via the fan-out: a paused agent's webhook does not fire
    and is not stamped last_triggered_at."""
    from astrolift_agents.services.workflow_triggers import (
        ScmEvent,
        route_scm_push_to_workflow_webhooks,
    )

    agent["workload"].run_paused = True
    agent["workload"].save(update_fields=["run_paused", "updated_at", "version"])
    _agent_webhook(agent, slug="scm-agent-paused")

    route_scm_push_to_workflow_webhooks(
        ScmEvent(
            organization_id=agent["org"].id,
            repo_full_name="acme/x",
            branch="main",
            head_sha="f" * 40,
            event_kind="push",
        )
    )

    assert AgentTask.objects.count() == 0
    assert temporal_recorder == []
    assert WorkflowWebhook.objects.get(slug="scm-agent-paused").last_triggered_at is None


def test_route_scm_mixed_definition_and_agent_webhooks(agent, temporal_recorder):
    """A definition-bound and an agent-bound webhook on the same org both
    fire on a matching push — the definition launches an instance, the agent
    dispatches a Task. Routing keys on which target is set."""
    from astrolift_agents.services.workflow_triggers import (
        ScmEvent,
        route_scm_push_to_workflow_webhooks,
    )
    from workflows.models import WorkflowInstance

    defn = _definition("mixed-def")
    WorkflowWebhook.objects.create(
        workflow_definition=defn,
        organization=agent["org"],
        slug="mixed-def-hook",
        secret_hash="x",
        input_mapping={},
        enabled=True,
    )
    _agent_webhook(agent, slug="mixed-agent-hook")

    instances = route_scm_push_to_workflow_webhooks(
        ScmEvent(
            organization_id=agent["org"].id,
            repo_full_name="acme/x",
            branch="main",
            head_sha="f" * 40,
            event_kind="push",
        )
    )

    # The definition-bound webhook launched one WorkflowInstance.
    assert len(instances) == 1
    assert WorkflowInstance.objects.filter(workflow=defn).count() == 1
    # The agent-bound webhook dispatched one Task.
    assert AgentTask.objects.filter(agent_definition=agent["workload"]).count() == 1


# ---------------------------------------------------------------------------
# SCM fan-out routing → agent dispatch (app-scoped live receiver)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_debug_toolbar(settings):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


@pytest.fixture
def receiver_stack(agent):
    """Add a SourceConnection so the live ``/api/webhooks/github/<guid>/``
    receiver can verify the signature, reusing the agent fixture's app/org."""
    from astrolift_scm.models import SourceConnection
    from core.secrets import encrypt_at_rest

    secret = "whsec_agent_trigger"
    encrypted = encrypt_at_rest(secret.encode("utf-8"))
    SourceConnection.objects.create(
        organization=agent["org"],
        kind="github_pat",
        display_name="acme",
        account_login="acme-org",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"",
        webhook_secret_backend_kind=encrypted.backend_kind,
        webhook_secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )
    # The receiver resolves the app by guid + needs a source repo set.
    agent["app"].source_kind = "github"
    agent["app"].source_repo = "acme-org/trigger"
    agent["app"].deploy_branch = "main"
    agent["app"].save(update_fields=["source_kind", "source_repo", "deploy_branch"])
    return {**agent, "secret": secret}


def _push_body(repo="acme-org/trigger"):
    return json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {"full_name": repo},
            "after": "f" * 40,
            "pull_request": {"number": 7},
        }
    ).encode("utf-8")


def _sig(secret, body):
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_live_receiver_dispatches_agent_for_bound_webhook(receiver_stack, temporal_recorder):
    """Acceptance (2) end-to-end through the live receiver: a signed push to
    an app with an agent-bound WorkflowWebhook dispatches a Task and the ack
    counts it as one dispatch."""
    _agent_webhook(receiver_stack, slug="live-agent-hook", input_mapping={"pr": "pull_request.number"})

    body = _push_body()
    resp = Client().post(
        f"/api/webhooks/github/{receiver_stack['app'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_sig(receiver_stack["secret"], body),
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_GITHUB_DELIVERY="33333333-3333-3333-3333-333333333333",
    )

    assert resp.status_code == 200, resp.content
    assert resp.json()["workflows_dispatched"] == 1
    # A Task was dispatched with the mapped input.
    assert AgentTask.objects.filter(agent_definition=receiver_stack["workload"]).count() == 1
    assert temporal_recorder[0][1][0].trigger_payload == {"pr": 7}


def test_live_receiver_paused_agent_not_counted(receiver_stack, temporal_recorder):
    """Acceptance (3) end-to-end: a paused agent's bound webhook is a no-op
    through the live receiver — 0 dispatched, no Task."""
    receiver_stack["workload"].run_paused = True
    receiver_stack["workload"].save(update_fields=["run_paused", "updated_at", "version"])
    _agent_webhook(receiver_stack, slug="live-agent-paused")

    body = _push_body()
    resp = Client().post(
        f"/api/webhooks/github/{receiver_stack['app'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_sig(receiver_stack["secret"], body),
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_GITHUB_DELIVERY="44444444-4444-4444-4444-444444444444",
    )

    assert resp.status_code == 200
    assert resp.json()["workflows_dispatched"] == 0
    assert AgentTask.objects.count() == 0
