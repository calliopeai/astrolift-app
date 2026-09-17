from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.utils import timezone

from astrolift_agents.models import AgentTask, AgentTaskEvent, AgentTaskInputReply
from astrolift_agents.services.task_timeout import INPUT_WAIT_BUDGET_SECONDS, task_timeout_reason
from astrolift_agents.tests import test_agent_task_events as event_fixtures
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner
from astrolift_workflows.activities.agent_stage import _expire_agent_task

pytestmark = pytest.mark.django_db(transaction=True)

task = event_fixtures.task
event = event_fixtures.event
post = event_fixtures.post

APPROVAL = {"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"command": "true"}}}


def setup_clock(task, monkeypatch, *, budget=INPUT_WAIT_BUDGET_SECONDS):
    start = timezone.now()
    task.provisioning_at = start
    task.timeout_seconds = 30
    task.input_wait_budget_seconds = budget
    task.save()
    clock = [start]
    monkeypatch.setattr(timezone, "now", lambda: clock[0])
    return start, clock


def request(task, sequence, message="approval"):
    return AgentTaskEvent.objects.create(
        organization=task.organization,
        agent_task=task,
        sequence=sequence,
        turn_id="turn",
        message_id=message,
        kind="approval_required",
        request=APPROVAL,
    )


def test_wait_survives_reload_then_reply_resumes_remaining_runtime(task, monkeypatch):
    start, clock = setup_clock(task, monkeypatch)
    clock[0] = start + timedelta(seconds=10)
    opening = request(task, 1)
    clock[0] = start + timedelta(minutes=10)
    reloaded = AgentTask.objects.get(pk=task.pk)
    assert _expire_agent_task(reloaded.pk) is None
    AgentTaskInputReply.objects.create(
        organization=task.organization, request_event=opening, response={"decision": "allow"}
    )
    clock[0] += timedelta(seconds=19)
    assert _expire_agent_task(task.pk) is None
    clock[0] += timedelta(seconds=1)
    assert _expire_agent_task(task.pk) == {"status": "timed_out", "terminal": True}
    task.refresh_from_db()
    assert task.failure == {"message": "agent exceeded its 30s timeout"}
    # No input_resolved was needed to restart the clock.


def test_overlapping_requests_count_wall_time_once_and_repeated_waits_accumulate(task, monkeypatch):
    start, clock = setup_clock(task, monkeypatch)
    clock[0] = start + timedelta(seconds=5)
    first = request(task, 1, "one")
    clock[0] = start + timedelta(seconds=10)
    second = request(task, 2, "two")
    clock[0] = start + timedelta(seconds=20)
    AgentTaskInputReply.objects.create(
        organization=task.organization, request_event=first, response={"decision": "deny"}
    )
    clock[0] = start + timedelta(seconds=25)
    AgentTaskInputReply.objects.create(
        organization=task.organization, request_event=second, response={"decision": "allow"}
    )
    clock[0] = start + timedelta(seconds=30)
    request(task, 3, "three")
    clock[0] = start + timedelta(seconds=50)
    AgentTaskEvent.objects.create(
        organization=task.organization,
        agent_task=task,
        sequence=4,
        turn_id="turn",
        message_id="three",
        kind="input_resolved",
    )
    clock[0] = start + timedelta(seconds=69)
    assert task_timeout_reason(task) is None
    clock[0] += timedelta(seconds=1)
    assert task_timeout_reason(task) == "agent exceeded its 30s timeout"


def test_input_wait_is_bounded_and_legacy_attention_does_not_pause(task, monkeypatch):
    start, clock = setup_clock(task, monkeypatch, budget=60)
    clock[0] += timedelta(seconds=5)
    request(task, 1)
    clock[0] = start + timedelta(seconds=65)
    assert task_timeout_reason(task) == "agent exceeded its 60s input-wait budget"
    task.input_wait_budget_seconds = 0
    assert task_timeout_reason(task) == "agent exceeded its 30s timeout"
    task.input_wait_budget_seconds = 60
    other = AgentTask.objects.create(
        organization=task.organization,
        status="running",
        provisioning_at=start,
        timeout_seconds=30,
        input_wait_budget_seconds=60,
    )
    AgentTaskEvent.objects.create(
        organization=task.organization,
        agent_task=other,
        sequence=1,
        turn_id="turn",
        message_id="legacy",
        kind="input_required",
    )
    assert task_timeout_reason(other) == "agent exceeded its 30s timeout"


def test_callback_reserves_once_before_commit_and_retries_failed_reservation(task, monkeypatch):
    from astrolift_agents.services import task_target

    task.dispatch_target = {"version": 1, "backend": "k8s_job"}
    task.save()
    spawner = Mock()
    monkeypatch.setattr(task_target, "spawner_for_task", lambda _: spawner)
    batch = [event(kind="approval_required") | {"request": APPROVAL}]
    spawner.reserve_input_wait.side_effect = RuntimeError("unavailable")
    assert post(task, batch, finding={"should": "roll back"}).status_code == 503
    task.refresh_from_db()
    assert task.input_wait_budget_seconds == 0
    assert task.result is None and not task.events.exists()
    spawner.reserve_input_wait.side_effect = None
    assert post(task, batch).status_code == 200
    task.refresh_from_db()
    assert task.input_wait_budget_seconds == INPUT_WAIT_BUDGET_SECONDS
    assert spawner.reserve_input_wait.call_count == 2
    assert post(task, batch).status_code == 200
    assert spawner.reserve_input_wait.call_count == 2
    assert task.events.count() == 1


def test_expired_task_cannot_gain_allowance_by_late_question(task, monkeypatch):
    from astrolift_agents.services import task_target

    start, clock = setup_clock(task, monkeypatch, budget=0)
    task.dispatch_target = {"version": 1, "backend": "k8s_job"}
    task.save()
    spawner = Mock()
    monkeypatch.setattr(task_target, "spawner_for_task", lambda _: spawner)
    clock[0] = start + timedelta(seconds=31)
    assert post(task, [event(kind="approval_required") | {"request": APPROVAL}]).status_code == 409
    assert not task.events.exists()
    spawner.reserve_input_wait.assert_not_called()


@pytest.mark.parametrize(
    "condition",
    [
        "valid",
        "retry",
        "foreign",
        "missing",
        "deleted",
        "finished",
        "no_version",
        "conflict",
        "wrong_name",
        "wrong_namespace",
    ],
)
def test_kubernetes_wait_reservation_preserves_job_and_uses_identity_preconditions(
    task, monkeypatch, condition
):
    from core import cluster_management

    task.external_id = "agent-task-fixture"
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": task.external_id,
            "namespace": "fixture",
            "uid": "owned-uid",
            "resourceVersion": "42",
            "labels": {"astrolift.dev/task-id": str(task.guid)},
            "managedFields": [],
        },
        "spec": {
            "activeDeadlineSeconds": 300,
            "backoffLimit": 0,
            "template": {"spec": {"containers": [{"name": "agent", "image": "fixture"}]}},
        },
        "status": {},
    }
    if condition == "retry":
        job["spec"]["activeDeadlineSeconds"] += INPUT_WAIT_BUDGET_SECONDS
    elif condition == "foreign":
        job["metadata"]["labels"]["astrolift.dev/task-id"] = "another-task"
    elif condition == "deleted":
        job["metadata"]["deletionTimestamp"] = "now"
    elif condition == "finished":
        job["status"]["conditions"] = [{"type": "Failed", "status": "True"}]
    elif condition == "no_version":
        del job["metadata"]["resourceVersion"]
    elif condition == "wrong_name":
        job["metadata"]["name"] = "another-job"
    elif condition == "wrong_namespace":
        job["metadata"]["namespace"] = "another-namespace"
    driver = Mock()
    driver.get_manifest.return_value = None if condition == "missing" else job
    driver.apply_manifests.return_value = SimpleNamespace(ok=condition != "conflict")
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda _: driver)
    monkeypatch.setattr(cluster_management, "_context_for_cluster", lambda _: SimpleNamespace(slug="fixture"))
    spawner = K8sJobSpawner(None, "fixture")
    if condition in {"valid", "retry"}:
        spawner.reserve_input_wait(task, INPUT_WAIT_BUDGET_SECONDS)
    else:
        with pytest.raises(RuntimeError):
            spawner.reserve_input_wait(task, INPUT_WAIT_BUDGET_SECONDS)
    if condition in {"valid", "conflict"}:
        manifest = driver.apply_manifests.call_args.args[2][0]
        assert manifest["spec"] == job["spec"] | {"activeDeadlineSeconds": 300 + INPUT_WAIT_BUDGET_SECONDS}
        assert manifest["metadata"]["uid"] == "owned-uid"
        assert manifest["metadata"]["resourceVersion"] == "42"
        assert "status" not in manifest and "managedFields" not in manifest["metadata"]
        assert job["spec"]["activeDeadlineSeconds"] == 300
    else:
        driver.apply_manifests.assert_not_called()


def test_expiry_retries_failed_stop_and_blocks_new_input_until_confirmed(task, monkeypatch):
    from astrolift_dispatch.spawners import registry
    from astrolift_workflows.activities import agent_stage

    start, clock = setup_clock(task, monkeypatch)
    task.external_id = "owned-job"
    task.dispatch_target = {"version": 1, "backend": "k8s_job"}
    task.save()
    clock[0] = start + timedelta(seconds=31)
    spawner = Mock()
    spawner.stop.side_effect = RuntimeError("cluster unavailable")
    monkeypatch.setattr(registry, "get_spawner", lambda *args, **kwargs: spawner)
    monkeypatch.setattr(agent_stage, "_placement_for_task", lambda _: ("k8s_job", None, "test"))
    assert _expire_agent_task(task.pk) == {"status": "running", "terminal": False}
    response = post(task, [event(kind="approval_required") | {"request": APPROVAL}])
    assert response.status_code == 409 and response.json()["continue"] is False
    assert not task.events.exists()
    spawner.stop.side_effect = None
    spawner.confirm_stopped.return_value = False
    assert _expire_agent_task(task.pk) == {"status": "running", "terminal": False}
    spawner.confirm_stopped.return_value = True
    assert _expire_agent_task(task.pk) == {"status": "timed_out", "terminal": True}
    spawner.stop.assert_called_with("owned-job", expected_task_guid=str(task.guid))


def test_slow_deadline_reservation_cannot_acknowledge_expired_question(task, monkeypatch):
    from astrolift_agents.services import task_target

    start, clock = setup_clock(task, monkeypatch, budget=0)
    task.dispatch_target = {"version": 1, "backend": "k8s_job"}
    task.save()
    spawner = Mock()
    spawner.reserve_input_wait.side_effect = lambda *args: clock.__setitem__(0, start + timedelta(seconds=31))
    monkeypatch.setattr(task_target, "spawner_for_task", lambda _: spawner)
    assert post(task, [event(kind="approval_required") | {"request": APPROVAL}]).status_code == 503
    task.refresh_from_db()
    assert not task.events.exists() and task.input_wait_budget_seconds == 0
