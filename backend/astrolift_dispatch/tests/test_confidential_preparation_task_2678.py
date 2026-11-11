"""The worker hands confidential preparation to an on-demand task and adopts only its verified Pod."""

import json
from types import SimpleNamespace

import pytest

from astrolift_clusters import nitro_preparation
from astrolift_dispatch.tests.test_confidential_pod_tasks_2668 import run  # noqa: F401 - fixture

pytestmark = pytest.mark.django_db

CONFIG = {
    "cluster": "arn:aws:ecs:us-west-2:123456789012:cluster/control",
    "task_definition": "control-nitro-preparation",
    "container": "preparation",
    "subnets": ["subnet-a"],
    "security_groups": ["sg-a"],
}


@pytest.fixture
def ecs(run, settings, monkeypatch):  # noqa: F811
    """An ECS whose started task runs the real preparation entry point in this process."""
    settings.NITRO_PREPARATION_TASK = json.dumps(CONFIG)
    monkeypatch.setattr(nitro_preparation, "POLL_SECONDS", 0)
    state = SimpleNamespace(
        launched=[], stopped=[], existing=[], perform=True, last="STOPPED", exit=None, definition_memory=32768
    )

    def run_task(**kwargs):
        state.launched.append(kwargs)
        marker = kwargs["overrides"]["containerOverrides"][0]["environment"][0]
        assert marker["name"] == nitro_preparation.MARKER
        state.overrides = kwargs["overrides"]
        if state.perform:
            monkeypatch.setenv(nitro_preparation.MARKER, marker["value"])
            guid, _, attempt = marker["value"].partition(":")
            state.exit = 0 if nitro_preparation.perform(guid, attempt) else 1
            monkeypatch.delenv(nitro_preparation.MARKER)
        return {"tasks": [{"taskArn": "arn:task/new"}], "failures": []}

    def describe_tasks(**kwargs):
        assert kwargs["cluster"] == CONFIG["cluster"]
        if state.stopped:
            state.last = "STOPPED"
        return {
            "tasks": [
                {
                    "taskArn": kwargs["tasks"][0],
                    "lastStatus": state.last,
                    "stoppedReason": "OutOfMemoryError: container killed",
                    "overrides": getattr(state, "overrides", {}),
                    "containers": [{"name": "preparation", "exitCode": state.exit}],
                }
            ]
        }

    client = SimpleNamespace(
        run_task=run_task,
        describe_tasks=describe_tasks,
        list_tasks=lambda **kwargs: {"taskArns": state.existing},
        stop_task=lambda **kwargs: state.stopped.append(kwargs),
        describe_task_definition=lambda **kwargs: {
            "taskDefinition": {
                "taskDefinitionArn": "arn:definition/7",
                "status": "ACTIVE",
                "cpu": "8192",
                "memory": str(state.definition_memory),
            }
        },
    )
    monkeypatch.setattr(nitro_preparation, "_client", lambda config: client)
    report = run.driver.probe_nitro_readiness

    def probe(slug, namespace, config, preparation=None):
        state.preparation = preparation
        return report(slug, namespace, config)

    run.driver.probe_nitro_readiness = probe
    return state


def test_worker_adopts_the_pod_its_preparation_task_created_without_passing_run_material(run, ecs):  # noqa: F811
    result = run.spawner.spawn(run.task)
    assert result.ok and result.external_id == run.spawner._name(run.task)
    assert run.driver.pod["spec"]["containers"][0]["image"].startswith("registry.example/run@sha256:")
    (launch,) = ecs.launched
    assert launch["startedBy"] == "np-" + run.task.guid.hex and len(launch["startedBy"]) <= 36
    assert launch["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] == "DISABLED"
    sent = json.dumps(launch)
    assert "private" not in sent and str(run.task.guid) in sent
    assert run.driver.seen["PROVIDER_TOKEN"] == "private-provider-secret"


def test_refused_pod_reports_the_task_error_and_retires_its_artifacts_once(run, ecs):  # noqa: F811
    run.driver.fail_apply = True
    result = run.spawner.spawn(run.task)
    assert not result.ok and "synthetic refusal" in result.error
    assert len(run.driver.cleaned) == 1


def test_task_killed_without_a_result_names_its_stop_reason_and_retires_the_checkpoint(run, ecs):  # noqa: F811
    ecs.perform = False
    ecs.exit = 137
    run.task.dispatch_target = {**run.task.dispatch_target, "nitro_confidential": {"task_id": "orphaned"}}
    run.task.save(update_fields=["dispatch_target", "updated_at", "version"])
    result = run.spawner.spawn(run.task)
    assert not result.ok and "OutOfMemoryError" in result.error
    assert run.driver.cleaned == [{"task_id": "orphaned"}]


def test_cancellation_stops_the_preparation_task_and_creates_no_pod(run, ecs):  # noqa: F811
    from django.utils import timezone

    ecs.perform = False
    ecs.last = "RUNNING"
    run.task.cancel_requested_at = timezone.now()
    run.task.save(update_fields=["cancel_requested_at", "updated_at", "version"])
    result = run.spawner.spawn(run.task)
    assert not result.ok and "cancelled during confidential image" in result.error
    assert [row["task"] for row in ecs.stopped] == ["arn:task/new"]
    assert run.driver.pod is None


def test_replacement_worker_waits_on_the_running_task_and_never_retires_its_key(run, ecs):  # noqa: F811
    attempt = "a" * 32
    ecs.existing = ["arn:task/earlier"]
    ecs.overrides = {
        "containerOverrides": [
            {"environment": [{"name": nitro_preparation.MARKER, "value": f"{run.task.guid}:{attempt}"}]}
        ]
    }
    ecs.exit = 1
    run.task.dispatch_target = {
        **run.task.dispatch_target,
        "nitro_confidential": {"task_id": "in-flight"},
        "nitro_preparation": {"run": attempt, "ok": False, "error": "earlier refusal"},
    }
    run.task.save(update_fields=["dispatch_target", "updated_at", "version"])
    cleaned_before_wait = []
    wait = nitro_preparation.prepare

    def prepare(task):
        cleaned_before_wait.extend(run.driver.cleaned)
        return wait(task)

    nitro_preparation.prepare, restore = prepare, wait
    try:
        result = run.spawner.spawn(run.task)
    finally:
        nitro_preparation.prepare = restore
    assert not ecs.launched and not cleaned_before_wait
    assert not result.ok and result.error == "earlier refusal"


def test_stale_outcome_of_an_earlier_attempt_is_not_this_attempts_result(run, ecs):  # noqa: F811
    ecs.perform = False
    ecs.exit = 0
    run.task.dispatch_target = {
        **run.task.dispatch_target,
        "nitro_preparation": {"run": "b" * 32, "ok": True, "error": ""},
    }
    run.task.save(update_fields=["dispatch_target", "updated_at", "version"])
    result = run.spawner.spawn(run.task)
    assert not result.ok and "without a result" in result.error


def test_a_refused_launch_settles_the_run_and_retires_its_checkpoint(run, ecs, monkeypatch):  # noqa: F811
    def denied(config):
        raise PermissionError("AccessDeniedException: arn:aws:iam::123456789012:role/control-task")

    monkeypatch.setattr(nitro_preparation, "_client", denied)
    run.task.dispatch_target = {**run.task.dispatch_target, "nitro_confidential": {"task_id": "orphaned"}}
    run.task.save(update_fields=["dispatch_target", "updated_at", "version"])
    result = run.spawner.spawn(run.task)
    assert not result.ok and "could not be run" in result.error and "arn:aws" not in result.error
    assert run.driver.cleaned == [{"task_id": "orphaned"}]


def test_malformed_preparation_configuration_refuses_before_secrets(run, settings, monkeypatch):  # noqa: F811
    settings.NITRO_PREPARATION_TASK = json.dumps({**CONFIG, "subnets": []})
    monkeypatch.setattr(
        "astrolift_dispatch.agent_secrets.resolve_task_secret_manifest",
        lambda **kwargs: pytest.fail("secrets must follow preparation configuration"),
    )
    result = run.spawner.spawn(run.task)
    assert not result.ok and run.driver.seen is None


def test_readiness_reports_the_task_instead_of_a_local_workspace(run, ecs, monkeypatch):  # noqa: F811
    from astrolift_clusters.nitro import review

    review(run.cluster, namespace="agents")
    assert ecs.preparation["code"] == "preparation_task" and ecs.preparation["state"] == "ready"
    assert ecs.preparation["evidence"] == {
        "task_definition": "arn:definition/7",
        "cpu": 8192,
        "memory_mib": 32768,
    }
    ecs.definition_memory = 8192
    assert nitro_preparation.readiness()["state"] == "not_ready"
    # Inside the preparation task the process checks its own workspace.
    monkeypatch.setenv(nitro_preparation.MARKER, "guid:run")
    assert nitro_preparation.readiness() is None and nitro_preparation.launcher() is None


def test_without_a_preparation_task_the_probe_keeps_its_original_call(run, settings):  # noqa: F811
    settings.NITRO_PREPARATION_TASK = ""
    from astrolift_clusters.nitro import review

    # The fixture's probe accepts exactly three arguments.
    assert review(run.cluster, namespace="agents")["modes"]["confidential"]["state"] == "ready"
    assert run.spawner.spawn(run.task).ok
