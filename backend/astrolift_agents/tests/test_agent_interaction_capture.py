"""Tests for control-plane agent-interaction capture (#1216).

Covers the three pieces of the V1 capture surface:

* **Dispatch-boundary capture** — an authenticated agent->controller call
  records an :class:`AgentInteraction` attributed to the right AgentTask.
  ``callback`` / ``status`` / ``checkin`` resolve the task directly; ``meter``
  / ``logs`` key off the fleet-history ``AgentRun`` and bridge to the task via
  the same ``(workload, external_id == k8s_pod_name)`` join the platform's
  own reconciler uses.
* **Defensiveness** — capture is additive/side-effect-only, so a write
  failure must never break the endpoint (mock the write to raise → still 200).
* **The read surface** — ``agentTaskInteractions`` is org-scoped fail-closed
  (a task in another org reads empty), rejects a non-UUID / foreign ``org_id``,
  is deny-by-default on ``agent.read``, and drains forward via the ``since``
  cursor oldest-first.

Runs against real Postgres (pytest-django). No mocks except the one that
proves the defensive swallow.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import Client
from django.utils import timezone
from graphql import GraphQLError

from astrolift_agents.models import (
    AgentInteraction,
    AgentTask,
    Brief,
    DispatcherInstance,
)
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_dispatch.views import _agent_task_for_run, _record_run_interaction
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import AgentRun
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

CHECKIN = "/api/dispatch/v1/agents/{}/checkin/"
CALLBACK = "/api/dispatch/v1/agents/{}/callback/"
STATUS = "/api/dispatch/v1/tasks/{}/status/"
METER = "/api/dispatch/v1/tasks/{}/meter/"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def org():
    return Organization.objects.create(name="IxnOrg", slug="ixn-org")


@pytest.fixture
def raw_key() -> str:
    return "z" * 64


@pytest.fixture
def dispatcher(org, raw_key):
    return DispatcherInstance.objects.create(
        organization=org,
        name="Ixn Dispatcher",
        slug="ixn-dispatcher",
        endpoint="https://dispatch.invalid/",
        api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )


@pytest.fixture
def brief(org):
    return Brief.objects.create(
        organization=org,
        content_hash="a" * 64,
        manifest_snapshot={"system_prompt": "do the thing", "tools": []},
        context={},
    )


def _auth(raw_key: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


def _make_task(org, dispatcher=None, brief=None, status=AgentTask.Status.RUNNING):
    task = AgentTask.objects.create(organization=org, dispatcher=dispatcher, brief=brief)
    if status != AgentTask.Status.DRAFT:
        AgentTask.all_objects.filter(pk=task.pk).update(status=status)
        task.refresh_from_db()
    return task


def _agent_workload(org, *, slug: str) -> Workload:
    """Minimal ``kind: agent`` Workload scaffold (team → app → workload).

    Enough for the AgentRun -> AgentTask bridge: both sides reference the
    same Workload pk. Project is nullable on RegisteredApp, so it's omitted.
    """
    team = Team.objects.create(organization=org, name=f"team-{slug}", slug=f"team-{slug}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name=f"app-{slug}",
        slug=f"app-{slug}",
    )
    return Workload.objects.create(
        registered_app=app,
        kind=Workload.Kind.AGENT,
        name=f"wl-{slug}",
        slug=slug,
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _ixn(org, task, *, name="callback", kind=None, status="ok", occurred_at=None, detail=None):
    return AgentInteraction.objects.create(
        organization=org,
        agent_task=task,
        kind=kind or AgentInteraction.Kind.CONTROL_API,
        name=name,
        status=status,
        occurred_at=occurred_at or timezone.now(),
        detail=detail or {},
    )


# ---------------------------------------------------------------------------
# Dispatch-boundary capture (task resolved directly)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_callback_records_interaction(org, dispatcher, brief, raw_key):
    """A terminal callback records one control_api interaction attributed to
    the task, with the reported status in ``detail`` and outcome ``ok``."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)

    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps({"status": "completed", "result": "all green"}),
        content_type="application/json",
        **_auth(raw_key),
    )
    assert resp.status_code == 200, resp.content

    ixns = list(AgentInteraction.objects.filter(agent_task=task))
    assert len(ixns) == 1
    row = ixns[0]
    assert row.kind == AgentInteraction.Kind.CONTROL_API
    assert row.name == "callback"
    assert row.status == "ok"
    assert row.organization_id == org.id
    assert row.detail == {"new_status": "completed"}


@pytest.mark.django_db(transaction=True)
def test_callback_failed_marks_error(org, dispatcher, brief, raw_key):
    """A failed callback records the interaction with status ``error``."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)

    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps({"status": "failed", "error": "boom"}),
        content_type="application/json",
        **_auth(raw_key),
    )
    assert resp.status_code == 200

    row = AgentInteraction.objects.get(agent_task=task)
    assert row.name == "callback"
    assert row.status == "error"
    assert row.detail == {"new_status": "failed"}


@pytest.mark.django_db(transaction=True)
def test_callback_heartbeat_records_interaction(org, dispatcher, brief, raw_key):
    """A non-terminal heartbeat is still a control-plane call — recorded."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)

    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps({"status": "running", "partial": "thinking"}),
        content_type="application/json",
        **_auth(raw_key),
    )
    assert resp.status_code == 200

    row = AgentInteraction.objects.get(agent_task=task)
    assert row.name == "callback"
    assert row.status == "ok"
    assert row.detail == {"new_status": "running"}


@pytest.mark.django_db(transaction=True)
def test_callback_finding_records_queryable_telemetry(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)

    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps(
            {
                "status": "running",
                "finding": {"item": "feedback-1", "decision": "filed", "jira_key": "DV-123"},
            }
        ),
        content_type="application/json",
        **_auth(raw_key),
    )
    assert resp.status_code == 200

    row = AgentInteraction.objects.get(agent_task=task)
    assert row.name == "finding"
    assert row.detail == {
        "new_status": "running",
        "finding_index": 0,
        "finding_keys": ["decision", "item", "jira_key"],
    }


@pytest.mark.django_db(transaction=True)
def test_status_update_records_interaction(org, dispatcher, raw_key):
    """The dispatcher status endpoint records a control_api interaction."""
    task = _make_task(org, dispatcher, status=AgentTask.Status.QUEUED)

    resp = Client().post(
        STATUS.format(task.guid),
        data=json.dumps({"status": "running"}),
        content_type="application/json",
        **_auth(raw_key),
    )
    assert resp.status_code == 200, resp.content

    row = AgentInteraction.objects.get(agent_task=task)
    assert row.kind == AgentInteraction.Kind.CONTROL_API
    assert row.name == "status"
    assert row.status == "ok"
    assert row.detail == {"new_status": "running"}


@pytest.mark.django_db(transaction=True)
def test_checkin_records_interaction(org, dispatcher, brief, raw_key):
    """A thread-mode checkin records a control_api interaction with the
    (post-advance) task status in ``detail``."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.QUEUED)

    resp = Client().post(CHECKIN.format(task.guid), **_auth(raw_key))
    assert resp.status_code == 200, resp.content

    row = AgentInteraction.objects.get(agent_task=task)
    assert row.kind == AgentInteraction.Kind.CONTROL_API
    assert row.name == "checkin"
    # checkin walks QUEUED -> PROVISIONING -> RUNNING.
    assert row.detail == {"status": AgentTask.Status.RUNNING}


@pytest.mark.django_db(transaction=True)
def test_capture_failure_does_not_break_callback(org, dispatcher, brief, raw_key, monkeypatch):
    """Capture is defensive: if the interaction write raises, the endpoint
    still succeeds and does its real work — the failure is swallowed."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)

    def _boom(*args, **kwargs):
        raise RuntimeError("interaction store unavailable")

    monkeypatch.setattr(AgentInteraction.objects, "create", _boom)

    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps({"status": "completed", "result": "ok"}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 200  # capture failure did not break the callback
    task.refresh_from_db()
    assert task.status == AgentTask.Status.COMPLETED  # endpoint still did its job
    assert AgentInteraction.objects.filter(agent_task=task).count() == 0  # nothing captured


# ---------------------------------------------------------------------------
# Dispatch-boundary capture (run-keyed endpoints -> task via bridge)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_meter_records_interaction_via_run_bridge(org):
    """The metering endpoint keys off AgentRun.guid; the interaction is
    bridged onto the AgentTask the run dispatched."""
    workload = _agent_workload(org, slug="meter-wl")
    run = AgentRun.objects.create(
        workload=workload,
        status=AgentRun.Status.RUNNING,
        k8s_pod_name="pod-meter-1",
    )
    task = AgentTask.objects.create(
        organization=org,
        agent_definition=workload,
        external_id="pod-meter-1",
    )

    resp = Client().post(
        METER.format(run.guid),
        data=json.dumps({"cpu_seconds": 1.5, "source": "wall_time_estimate"}),
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content

    row = AgentInteraction.objects.get(agent_task=task)
    assert row.kind == AgentInteraction.Kind.CONTROL_API
    assert row.name == "meter"
    assert row.organization_id == org.id
    assert row.detail == {"metering_source": "wall_time_estimate"}


@pytest.mark.django_db(transaction=True)
def test_logs_capture_records_interaction_via_bridge(org):
    """The logs path records via the same run->task bridge (exercised through
    the exact helper the ingest_task_logs view calls)."""
    workload = _agent_workload(org, slug="logs-wl")
    run = AgentRun.objects.create(
        workload=workload,
        status=AgentRun.Status.RUNNING,
        k8s_pod_name="pod-logs-1",
    )
    task = AgentTask.objects.create(
        organization=org,
        agent_definition=workload,
        external_id="pod-logs-1",
    )

    _record_run_interaction(str(run.guid), name="logs", detail={"lines": 3, "stored": 3}, run=run)

    row = AgentInteraction.objects.get(agent_task=task)
    assert row.name == "logs"
    assert row.kind == AgentInteraction.Kind.CONTROL_API
    assert row.organization_id == org.id
    assert row.detail == {"lines": 3, "stored": 3}


@pytest.mark.django_db(transaction=True)
def test_run_bridge_skips_when_no_linked_task(org):
    """Fail-safe: a run with no pod name / no matching task yields no bridge
    and records nothing (never a mis-attributed FK)."""
    workload = _agent_workload(org, slug="nolink-wl")

    # No k8s_pod_name -> no bridge target.
    run = AgentRun.objects.create(workload=workload, status=AgentRun.Status.RUNNING, k8s_pod_name="")
    assert _agent_task_for_run(run) is None
    _record_run_interaction(str(run.guid), name="logs", run=run)
    assert AgentInteraction.objects.count() == 0

    # Pod name present but no AgentTask carries that external_id.
    run2 = AgentRun.objects.create(
        workload=workload, status=AgentRun.Status.RUNNING, k8s_pod_name="ghost-pod"
    )
    assert _agent_task_for_run(run2) is None
    _record_run_interaction(str(run2.guid), name="logs", run=run2)
    assert AgentInteraction.objects.count() == 0


# ---------------------------------------------------------------------------
# AgentRun -> AgentTask resolver: FK-first, fuzzy-join fallback (#1217)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_resolver_prefers_explicit_fk(org):
    """The resolver returns the FK-linked task directly — even when the
    historical (workload, external_id == k8s_pod_name) join could not match
    (no pod name here), the explicit FK still resolves it."""
    from astrolift_agents.models import resolve_agent_task_for_run

    workload = _agent_workload(org, slug="fk-wl")
    run = AgentRun.objects.create(workload=workload, status=AgentRun.Status.RUNNING, k8s_pod_name="")
    task = AgentTask.objects.create(organization=org, agent_definition=workload, agent_run=run)

    assert resolve_agent_task_for_run(run) == task


@pytest.mark.django_db(transaction=True)
def test_resolver_falls_back_to_fuzzy_join_when_fk_null(org):
    """With no FK set, the resolver falls back to the historical
    (workload, external_id == k8s_pod_name) join so pre-FK rows still resolve."""
    from astrolift_agents.models import resolve_agent_task_for_run

    workload = _agent_workload(org, slug="fuzzy-wl")
    run = AgentRun.objects.create(
        workload=workload, status=AgentRun.Status.RUNNING, k8s_pod_name="pod-fuzzy-1"
    )
    task = AgentTask.objects.create(organization=org, agent_definition=workload, external_id="pod-fuzzy-1")
    assert task.agent_run_id is None  # not linked by FK yet

    assert resolve_agent_task_for_run(run) == task


@pytest.mark.django_db(transaction=True)
def test_resolver_returns_none_for_no_run_and_no_match(org):
    """None run, and a run with neither an FK nor a fuzzy match, resolve to
    None so callers skip rather than mis-attribute."""
    from astrolift_agents.models import resolve_agent_task_for_run

    assert resolve_agent_task_for_run(None) is None
    workload = _agent_workload(org, slug="nomatch-wl")
    run = AgentRun.objects.create(workload=workload, status=AgentRun.Status.RUNNING, k8s_pod_name="ghost")
    assert resolve_agent_task_for_run(run) is None


@pytest.mark.django_db(transaction=True)
def test_meter_records_interaction_via_fk_link(org):
    """The run-keyed bridge resolves the task through the explicit FK too — no
    pod-name fuzzy match required once the FK is set."""
    workload = _agent_workload(org, slug="fk-meter-wl")
    run = AgentRun.objects.create(workload=workload, status=AgentRun.Status.RUNNING, k8s_pod_name="")
    task = AgentTask.objects.create(organization=org, agent_definition=workload, agent_run=run)

    resp = Client().post(
        METER.format(run.guid),
        data=json.dumps({"cpu_seconds": 2.0, "source": "wall_time_estimate"}),
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content

    row = AgentInteraction.objects.get(agent_task=task)
    assert row.name == "meter"
    assert row.organization_id == org.id


# ---------------------------------------------------------------------------
# Read surface: agentTaskInteractions
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_interactions_scoped_to_caller_org(permission_resolver):
    """FAIL-CLOSED org scope: a task in another org (even with a valid guid)
    reads empty — the foreign task's interactions WOULD leak without the
    org clause on the task lookup + interaction query."""
    mine = Organization.objects.create(name="Mine", slug="ixn-mine")
    theirs = Organization.objects.create(name="Theirs", slug="ixn-theirs")
    my_task = AgentTask.objects.create(organization=mine)
    their_task = AgentTask.objects.create(organization=theirs)
    _ixn(mine, my_task, name="callback")
    _ixn(theirs, their_task, name="callback")  # foreign — must not leak

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(mine):
        mine_rows = AgentsQuery().agent_task_interactions(
            info=_info(), org_id=str(mine.guid), task_id=str(my_task.guid)
        )
        foreign_rows = AgentsQuery().agent_task_interactions(
            info=_info(), org_id=str(mine.guid), task_id=str(their_task.guid)
        )

    assert [r.name for r in mine_rows] == ["callback"]
    assert foreign_rows == []


@pytest.mark.django_db(transaction=True)
def test_interactions_isolated_per_task(permission_resolver):
    """Only the requested task's interactions come back — not sibling tasks
    in the same org."""
    org = Organization.objects.create(name="TwoTasks", slug="ixn-two")
    task_a = AgentTask.objects.create(organization=org)
    task_b = AgentTask.objects.create(organization=org)
    _ixn(org, task_a, name="callback")
    _ixn(org, task_b, name="status")

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        rows = AgentsQuery().agent_task_interactions(
            info=_info(), org_id=str(org.guid), task_id=str(task_a.guid)
        )

    assert [r.name for r in rows] == ["callback"]


@pytest.mark.django_db(transaction=True)
def test_interactions_non_uuid_task_id_returns_empty(permission_resolver):
    """A non-UUID task id resolves to [] (not a 500) — guards route params
    like ``/agents/runs/overview``."""
    org = Organization.objects.create(name="NonUuid", slug="ixn-nonuuid")
    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        rows = AgentsQuery().agent_task_interactions(info=_info(), org_id=str(org.guid), task_id="overview")
    assert rows == []


@pytest.mark.django_db(transaction=True)
def test_interactions_unknown_task_returns_empty(permission_resolver):
    """A well-formed but unknown task guid reads empty."""
    org = Organization.objects.create(name="Unknown", slug="ixn-unknown")
    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        rows = AgentsQuery().agent_task_interactions(
            info=_info(),
            org_id=str(org.guid),
            task_id="00000000-0000-0000-0000-000000000000",
        )
    assert rows == []


@pytest.mark.django_db(transaction=True)
def test_interactions_foreign_org_id_raises(permission_resolver):
    """Passing another org's ``orgId`` while scoped to your own tenant is
    rejected by the explicit-arg gate."""
    mine = Organization.objects.create(name="M3", slug="ixn-m3")
    theirs = Organization.objects.create(name="T3", slug="ixn-t3")
    their_task = AgentTask.objects.create(organization=theirs)

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(mine), pytest.raises(GraphQLError):
        AgentsQuery().agent_task_interactions(
            info=_info(), org_id=str(theirs.guid), task_id=str(their_task.guid)
        )


@pytest.mark.django_db(transaction=True)
def test_interactions_requires_agent_read():
    """Deny-by-default: without ``agent.read`` the resolver raises before any
    query (queries surface permission failures by raising)."""
    org = Organization.objects.create(name="Deny", slug="ixn-deny")
    task = AgentTask.objects.create(organization=org)
    with _tenant(org), pytest.raises(PermissionDenied):
        AgentsQuery().agent_task_interactions(info=_info(), org_id=str(org.guid), task_id=str(task.guid))


@pytest.mark.django_db(transaction=True)
def test_interactions_since_cursor_and_ascending_order(permission_resolver):
    """Rows come back oldest-first; ``since`` returns only strictly-later
    rows so the map can fold batches and advance its cursor."""
    org = Organization.objects.create(name="Cursor", slug="ixn-cursor")
    task = AgentTask.objects.create(organization=org)
    now = timezone.now()
    i1 = _ixn(org, task, name="checkin", occurred_at=now - timedelta(minutes=3))
    _ixn(org, task, name="callback", occurred_at=now - timedelta(minutes=2))
    _ixn(org, task, name="status", occurred_at=now - timedelta(minutes=1))

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        all_rows = AgentsQuery().agent_task_interactions(
            info=_info(), org_id=str(org.guid), task_id=str(task.guid)
        )
        after_i1 = AgentsQuery().agent_task_interactions(
            info=_info(), org_id=str(org.guid), task_id=str(task.guid), since=i1.occurred_at
        )

    assert [r.name for r in all_rows] == ["checkin", "callback", "status"]
    assert [r.name for r in after_i1] == ["callback", "status"]


@pytest.mark.django_db(transaction=True)
def test_interactions_limit_caps_result(permission_resolver):
    """``limit`` bounds the page and keeps the oldest rows (cursor drains
    forward across polls)."""
    org = Organization.objects.create(name="Cap", slug="ixn-cap")
    task = AgentTask.objects.create(organization=org)
    now = timezone.now()
    for i in range(3):
        _ixn(org, task, name=f"n{i}", occurred_at=now - timedelta(minutes=3 - i))

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        limited = AgentsQuery().agent_task_interactions(
            info=_info(), org_id=str(org.guid), task_id=str(task.guid), limit=2
        )

    assert [r.name for r in limited] == ["n0", "n1"]


# ---------------------------------------------------------------------------
# Model invariant
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_interaction_is_append_only(org):
    """AgentInteraction rows are append-only: no update, no delete."""
    task = AgentTask.objects.create(organization=org)
    row = _ixn(org, task, name="callback")

    row.status = "error"
    with pytest.raises(RuntimeError):
        row.save()
    with pytest.raises(RuntimeError):
        row.delete()
