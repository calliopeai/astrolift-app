"""
Tests for the Loop-dispatch tick (spec 33, PR-6).

Two layers, mirroring ``test_agent_cron_dispatch`` / ``test_scale_tick``:

  * ``select_loop_dispatches`` policy — pure-Python; the cap arithmetic
    (headroom = cap - in_flight) is asserted here with no DB or clock, incl.
    the ``run_max_parallel is None`` default-cap resolution and the
    ``run_paused`` halt.
  * ``dispatch_agent_loops`` activity — talks to the real DB, selects
    ``run_family='task', run_mode='loop'`` agent Workloads, counts in-flight
    (non-terminal) AgentTasks per agent under a per-agent row lock, and
    dispatches up to the cap through the PR-1 path (Temporal client stubbed).

The four acceptance cases (spec §PR-6) for Loop:
  1. a Loop agent re-dispatches on terminal up to the concurrency cap and
     NEVER exceeds it — incl. a back-to-back double-tick test and a real
     two-thread concurrent-tick test proving the cap holds under the lock;
  (2 = Trigger, covered in astrolift_agents/tests/test_agent_trigger_dispatch.py)
  3. pausing the run-spec (run_paused=True) halts the loop;
  (4 = condition-trigger out of scope, covered in the trigger test)

Plus mutual-exclusivity: the loop selector ignores schedule agents, service
agents, once agents, and plain cron-deploy apps.
"""

from __future__ import annotations

import dataclasses
import threading
from unittest.mock import patch

import pytest

from astrolift_workflows.cron_deploy import (
    DEFAULT_LOOP_MAX_PARALLEL,
    LoopDispatchAction,
    LoopDispatchCandidate,
    effective_loop_cap,
    select_loop_dispatches,
)

# ---- select_loop_dispatches (pure policy) ---------------------------


def _loop_candidate(**kw):
    base = {
        "workload_id": 1,
        "workload_slug": "looper",
        "workload_guid": "guid",
        "organization_id": 1,
        "run_paused": False,
        "run_max_parallel": 3,
        "in_flight": 0,
    }
    base.update(kw)
    return LoopDispatchCandidate(**base)


def test_effective_cap_none_is_default_not_infinite():
    """A null cap maps to the platform default (NOT unbounded) — Loop is
    never truly uncapped (acceptance: cap is always enforced)."""
    assert effective_loop_cap(None) == DEFAULT_LOOP_MAX_PARALLEL
    assert DEFAULT_LOOP_MAX_PARALLEL >= 1


def test_effective_cap_explicit_value_honoured():
    assert effective_loop_cap(5) == 5
    assert effective_loop_cap(0) == 0  # explicit 0 = soft pause, honoured


def test_dispatch_fills_to_cap_from_empty():
    """Acceptance (1): with 0 in flight and cap 3, dispatch exactly 3 — the
    post-dispatch in-flight count equals the cap, never above."""
    out = select_loop_dispatches(candidates=[_loop_candidate(run_max_parallel=3, in_flight=0)])
    assert len(out) == 1
    assert out[0].to_dispatch == 3
    assert out[0].workload_slug == "looper"


def test_dispatch_tops_up_partial_headroom():
    """1 in flight, cap 3 → dispatch only the 2-slot headroom (re-dispatch on
    terminal: as runs finish, the next tick refills to the cap)."""
    out = select_loop_dispatches(candidates=[_loop_candidate(run_max_parallel=3, in_flight=1)])
    assert out[0].to_dispatch == 2


def test_dispatch_nothing_when_at_cap():
    """Acceptance (1): at the cap, dispatch 0 — NEVER exceed."""
    out = select_loop_dispatches(candidates=[_loop_candidate(run_max_parallel=3, in_flight=3)])
    assert out == []


def test_dispatch_nothing_when_over_cap():
    """Over cap (e.g. the cap was just lowered) → dispatch 0; the agent
    drains naturally as runs finish. Headroom is clamped at 0, never
    negative."""
    out = select_loop_dispatches(candidates=[_loop_candidate(run_max_parallel=2, in_flight=5)])
    assert out == []


def test_default_cap_applied_when_unset():
    """A Loop agent with run_max_parallel=None uses the default cap of 1 —
    so a single run stays in flight (serial re-dispatch), not unbounded."""
    out = select_loop_dispatches(candidates=[_loop_candidate(run_max_parallel=None, in_flight=0)])
    assert len(out) == 1
    assert out[0].to_dispatch == DEFAULT_LOOP_MAX_PARALLEL
    # And once the single default slot is filled, nothing more dispatches.
    assert select_loop_dispatches(candidates=[_loop_candidate(run_max_parallel=None, in_flight=1)]) == []


def test_paused_dispatches_nothing():
    """Acceptance (3) at the policy layer: run_paused halts the loop —
    dispatch 0 regardless of headroom."""
    out = select_loop_dispatches(
        candidates=[_loop_candidate(run_paused=True, run_max_parallel=3, in_flight=0)]
    )
    assert out == []


def test_explicit_zero_cap_dispatches_nothing():
    """An explicit cap of 0 is a soft pause that keeps the agent in Loop
    mode but dispatches nothing."""
    out = select_loop_dispatches(candidates=[_loop_candidate(run_max_parallel=0, in_flight=0)])
    assert out == []


def test_loop_action_is_frozen():
    a = LoopDispatchAction(
        workload_id=1, workload_slug="s", workload_guid="g", organization_id=1, to_dispatch=2
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.to_dispatch = 3  # type: ignore[misc]


# ---- activity wiring (DB-backed) -----------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


def _stub_start(starts):
    from astrolift_workflows.client import WorkflowHandle

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, list(args), workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    return _start


@pytest.fixture
def loop_agent(db):
    """An org with one app carrying a single agent Workload in Loop mode with
    a concurrency cap of 3, not paused, so the tick fills to the cap."""
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    org = Organization.objects.create(name="Loop Co", slug="loop-co")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-loop")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-loop")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Loop App",
        slug="loop-app",
        provisioning_status="ready",
    )
    workload = Workload.objects.create(
        registered_app=app,
        name="Loop Agent",
        slug="loop-agent",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
        run_mode=Workload.RunMode.LOOP.value,
        run_max_parallel=3,
        run_paused=False,
    )
    return {"org": org, "app": app, "workload": workload}


@pytest.mark.django_db
def test_tick_fills_to_cap_and_enqueues_dispatch(loop_agent, settings):
    """Acceptance (1): a Loop agent with cap=3 and 0 in flight dispatches
    exactly 3 Tasks — each an AgentTask(agent_definition=...) QUEUED + a
    DispatchAgentTaskWorkflow enqueued keyed to its guid."""
    from astrolift_agents.models import AgentTask
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_loops_sync()

    assert summary.candidates_count == 1
    assert summary.fired_count == 1
    assert summary.fired_workload_slugs == ("loop-agent",)
    assert len(summary.fired_task_guids) == 3

    tasks = AgentTask.objects.filter(agent_definition=loop_agent["workload"])
    assert tasks.count() == 3
    for t in tasks:
        assert t.status == AgentTask.Status.QUEUED
        assert t.queued_at is not None
        assert t.organization_id == loop_agent["org"].id
    # Loop is a Task dispatch, NOT an app deploy.
    assert Deployment.objects.count() == 0

    # Three DispatchAgentTaskWorkflow starts, each keyed to its task guid and
    # carrying that task's pk (the PR-1 dispatch path, mirrored).
    assert len(starts) == 3
    guids_by_pk = {t.pk: str(t.guid) for t in tasks}
    for name, args, workflow_id in starts:
        assert name == "DispatchAgentTaskWorkflow"
        assert workflow_id == f"DispatchAgentTaskWorkflow-{guids_by_pk[args[0].agent_task_id]}"
        assert args[0].actor.kind == "system"
        # Loop dispatch carries no ad-hoc input (matches the cron tick + PR-1).
        assert args[0].trigger_payload is None
    # The cap was respected exactly — never exceeded.
    assert AgentTask.objects.filter(
        agent_definition=loop_agent["workload"],
        status__in=AgentTask.NON_TERMINAL_STATUSES,
    ).count() == 3


@pytest.mark.django_db
def test_tick_tops_up_only_headroom(loop_agent, settings):
    """Acceptance (1): with 2 already in flight and cap=3, the tick dispatches
    exactly 1 (the headroom), bringing the total to the cap — not above."""
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = loop_agent["workload"]
    # Seed 2 in-flight (non-terminal) tasks.
    for _ in range(2):
        t = AgentTask.objects.create(
            organization=loop_agent["org"],
            agent_definition=workload,
            status=AgentTask.Status.DRAFT,
        )
        t.transition_to(AgentTask.Status.QUEUED)

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_loops_sync()

    assert summary.fired_count == 1
    assert len(summary.fired_task_guids) == 1  # only the 1-slot headroom
    assert len(starts) == 1
    assert (
        AgentTask.objects.filter(
            agent_definition=workload, status__in=AgentTask.NON_TERMINAL_STATUSES
        ).count()
        == 3  # exactly the cap
    )


@pytest.mark.django_db
def test_tick_dispatches_nothing_when_at_cap(loop_agent, settings):
    """Acceptance (1): a Loop agent already at the cap dispatches nothing —
    it's still a candidate, just full."""
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = loop_agent["workload"]
    for _ in range(3):  # fill to cap
        t = AgentTask.objects.create(
            organization=loop_agent["org"], agent_definition=workload, status=AgentTask.Status.DRAFT
        )
        t.transition_to(AgentTask.Status.QUEUED)

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_loops_sync()

    assert summary.candidates_count == 1
    assert summary.fired_count == 0
    assert starts == []
    assert AgentTask.objects.filter(agent_definition=workload).count() == 3


@pytest.mark.django_db
def test_terminal_runs_free_a_slot_for_redispatch(loop_agent, settings):
    """Acceptance (1) — 're-dispatches on terminal': a finished (terminal)
    run no longer occupies a cap slot, so the next tick refills it. Seed
    cap=3 with 2 RUNNING + 1 COMPLETED → headroom 1 → dispatch 1."""
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = loop_agent["workload"]

    def _mk(status):
        t = AgentTask.objects.create(
            organization=loop_agent["org"], agent_definition=workload, status=AgentTask.Status.DRAFT
        )
        t.transition_to(AgentTask.Status.QUEUED)
        if status in (AgentTask.Status.RUNNING, AgentTask.Status.COMPLETED, AgentTask.Status.FAILED):
            t.transition_to(AgentTask.Status.PROVISIONING)
            t.transition_to(AgentTask.Status.RUNNING)
        if status in (AgentTask.Status.COMPLETED, AgentTask.Status.FAILED):
            t.transition_to(status)
        return t

    _mk(AgentTask.Status.RUNNING)
    _mk(AgentTask.Status.RUNNING)
    _mk(AgentTask.Status.COMPLETED)  # terminal — frees a slot

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_loops_sync()

    # 2 non-terminal in flight, cap 3 → 1 dispatched.
    assert summary.fired_count == 1
    assert len(starts) == 1
    assert (
        AgentTask.objects.filter(
            agent_definition=workload, status__in=AgentTask.NON_TERMINAL_STATUSES
        ).count()
        == 3
    )


@pytest.mark.django_db
def test_double_tick_back_to_back_never_exceeds_cap(loop_agent, settings):
    """Acceptance (1) — 'never exceeds the cap even under concurrent ticks',
    realistic-coalescing form: two ticks fire back-to-back (the same way a
    Temporal restart can replay a tick). The first fills to the cap; the
    second sees the cap-full in-flight count and dispatches nothing. Total
    in-flight is exactly the cap, never 2x."""
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = loop_agent["workload"]
    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        first = _dispatch_agent_loops_sync()
        second = _dispatch_agent_loops_sync()

    assert first.fired_count == 1 and len(first.fired_task_guids) == 3
    assert second.fired_count == 0 and second.fired_task_guids == ()
    # Exactly the cap of in-flight tasks exist — the back-to-back tick did
    # NOT double-dispatch.
    assert (
        AgentTask.objects.filter(
            agent_definition=workload, status__in=AgentTask.NON_TERMINAL_STATUSES
        ).count()
        == 3
    )
    assert len(starts) == 3


@pytest.mark.django_db(transaction=True)
def test_concurrent_ticks_hold_the_cap_under_lock(loop_agent, settings):
    """Acceptance (1) — 'never exceeds the cap even under CONCURRENT ticks',
    true-parallel form: two threads run the tick simultaneously. The
    per-agent ``select_for_update`` lock serialises the count-then-dispatch
    critical section, so the second thread re-counts AFTER the first commits
    and sees no headroom. The total in-flight is exactly the cap — the
    read-modify-write race that would otherwise dispatch 2x is closed.

    Uses ``transaction=True`` so each thread's ``transaction.atomic()`` +
    row lock behaves against the real (committed) DB the way it does in
    production, rather than inside one shared test transaction.
    """
    from astrolift_agents.models import AgentTask
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = loop_agent["workload"]

    # Stub Temporal start for BOTH threads (module-level patch, no monkeypatch
    # fixture because this isn't a function-scoped transaction).
    import astrolift_workflows.client as wf_client

    orig_start = wf_client.start_workflow
    lock = threading.Lock()
    starts: list = []

    def _start(name, args, *, workflow_id, task_queue=None):
        with lock:
            starts.append(workflow_id)
        return wf_client.WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    wf_client.start_workflow = _start

    summaries: list = []
    errors: list = []

    def _run():
        from django.db import connection

        try:
            summaries.append(_dispatch_agent_loops_sync())
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(exc)
        finally:
            # Each thread holds its own DB connection; close it so the
            # transaction=True teardown can TRUNCATE without a held lock.
            connection.close()

    try:
        t1 = threading.Thread(target=_run)
        t2 = threading.Thread(target=_run)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)
    finally:
        wf_client.start_workflow = orig_start

    assert not errors, f"tick thread raised: {errors}"
    # Whichever thread won the lock dispatched the full cap; the other saw a
    # full agent and dispatched nothing. Combined, exactly the cap of tasks
    # were created — NEVER 2x the cap.
    total_dispatched = sum(s.fired_count and len(s.fired_task_guids) for s in summaries)
    in_flight = AgentTask.objects.filter(
        agent_definition=workload, status__in=AgentTask.NON_TERMINAL_STATUSES
    ).count()
    assert in_flight == 3, f"cap violated: {in_flight} in flight (expected 3)"
    assert total_dispatched == 3
    assert len(starts) == 3

    # Clean up the rows this transaction=True test committed (the test DB is
    # not rolled back for transaction=True cases between assertions; the
    # fixture teardown TRUNCATEs, but be explicit about what we created).
    AgentTask.objects.filter(agent_definition=workload).delete()
    Workload.all_objects.filter(pk=workload.pk).delete()


# ---- mutual exclusivity (the loop selector is disjoint) -------------


@pytest.mark.django_db
def test_loop_tick_skips_schedule_agent(loop_agent, settings):
    """A Schedule-mode agent (the agent-cron tick's candidate) is NOT a loop
    candidate — run_mode is the discriminator between the two Task selectors."""
    from astrolift_agents.models import AgentTask
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = loop_agent["workload"]
    workload.run_mode = Workload.RunMode.SCHEDULE.value
    workload.run_cron_expression = "* * * * *"
    workload.save(update_fields=["run_mode", "run_cron_expression", "updated_at", "version"])

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_loops_sync()

    assert summary.candidates_count == 0
    assert summary.fired_count == 0
    assert AgentTask.objects.count() == 0


@pytest.mark.django_db
def test_loop_tick_skips_service_agent(loop_agent, settings):
    """A Service-family agent (the scale tick's domain) is NOT a loop
    candidate — run_family excludes it."""
    from astrolift_agents.models import AgentTask
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = loop_agent["workload"]
    workload.run_family = Workload.RunFamily.SERVICE.value
    workload.save(update_fields=["run_family", "updated_at", "version"])

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_loops_sync()

    assert summary.candidates_count == 0
    assert AgentTask.objects.count() == 0


@pytest.mark.django_db
def test_loop_tick_skips_once_agent(loop_agent, settings):
    """An Once-mode agent (the PR-1 default) never loops."""
    from astrolift_agents.models import AgentTask
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = loop_agent["workload"]
    workload.run_mode = Workload.RunMode.ONCE.value
    workload.save(update_fields=["run_mode", "updated_at", "version"])

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_loops_sync()

    assert summary.candidates_count == 0
    assert AgentTask.objects.count() == 0


@pytest.mark.django_db
def test_loop_tick_skips_paused_agent(loop_agent, settings):
    """Acceptance (3): a paused Loop agent dispatches nothing — the operator
    kill-switch halts the loop at the activity layer too (not just policy)."""
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = loop_agent["workload"]
    workload.run_paused = True
    workload.save(update_fields=["run_paused", "updated_at", "version"])

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_loops_sync()

    # Still selected as a candidate (it IS a loop agent) but dispatches nothing.
    assert summary.candidates_count == 1
    assert summary.fired_count == 0
    assert starts == []
    assert AgentTask.objects.count() == 0


@pytest.mark.django_db
def test_loop_tick_skips_soft_deleted_agent(loop_agent, settings):
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    loop_agent["workload"].soft_delete()

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_loops_sync()

    assert summary.candidates_count == 0
    assert summary.fired_count == 0
    assert AgentTask.objects.count() == 0


@pytest.mark.django_db
def test_loop_tick_ignores_cron_deploy_app(settings):
    """Mutual exclusivity vs the deploy selector: a plain app in
    trigger_mode='cron' is invisible to the loop tick — it only reads agent
    Workloads, never RegisteredApp.trigger_mode."""
    from astrolift_agents.models import AgentTask
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.activities.cron_deploy import _dispatch_agent_loops_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    org = Organization.objects.create(name="Deploy Co3", slug="deploy-co3")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-deploy3")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-deploy3")
    RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Cron Deploy App3",
        slug="cron-deploy-app3",
        trigger_mode=RegisteredApp.TriggerMode.CRON.value,
        cron_expression="* * * * *",
        provisioning_status="ready",
    )

    summary = _dispatch_agent_loops_sync()
    assert summary.candidates_count == 0
    assert summary.fired_count == 0
    assert AgentTask.objects.count() == 0


# ---- schedule_registry catalog entry --------------------------------


def test_loop_tick_is_registered_at_60_seconds():
    from astrolift_workflows.schedule_registry import (
        DEFAULT_SCHEDULES,
        ScheduleKind,
        get_schedule,
    )

    sched = get_schedule(kind=ScheduleKind.LOOP_TICK)
    assert sched.interval_seconds == 60
    assert sched.workflow_name == "AgentLoopTickWorkflow"
    assert any(s.kind == ScheduleKind.LOOP_TICK for s in DEFAULT_SCHEDULES)
