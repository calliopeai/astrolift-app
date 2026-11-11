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
        run.task.refresh_from_db()
        launch = run.task.dispatch_target["nitro_preparation"]
        assert launch["request"] == kwargs and launch["run"] == kwargs["clientToken"]
        assert kwargs["tags"] == [{"key": nitro_preparation.OWNER_TAG, "value": CONFIG["task_definition"]}]
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
    launch = nitro_preparation._launch(run.task, CONFIG, nitro_preparation._client(CONFIG))
    attempt = launch["run"]
    nitro_preparation._record(
        run.task, attempt, task_arn="arn:task/earlier", ok=False, error="earlier refusal"
    )
    ecs.exit = 1
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


def test_unknown_launcher_state_is_retryable_without_retiring_artifacts(run, ecs, monkeypatch):  # noqa: F811
    def denied(config):
        raise PermissionError("PRIVATE-DIAGNOSTIC")

    monkeypatch.setattr(nitro_preparation, "_client", denied)
    run.task.dispatch_target = {**run.task.dispatch_target, "nitro_confidential": {"task_id": "in-flight"}}
    run.task.save(update_fields=["dispatch_target", "updated_at", "version"])
    with pytest.raises(nitro_preparation.PreparationUncertain, match="state could not be verified"):
        run.spawner.spawn(run.task)
    assert run.driver.cleaned == []


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
        "workspace_verified": False,
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


def test_lost_run_task_response_reuses_durable_request_without_duplicate_producer(run, ecs, monkeypatch):  # noqa: F811
    client = nitro_preparation._client(CONFIG)
    submit = client.run_task
    requests = []

    def lost_response(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            # AWS accepted the request; ListTasks has not observed it yet.
            raise TimeoutError("response lost")
        return submit(**kwargs)

    client.run_task = lost_response
    with pytest.raises(nitro_preparation.PreparationUncertain, match="launch is not confirmed"):
        run.spawner.spawn(run.task)
    run.task.refresh_from_db()
    first = run.task.dispatch_target["nitro_preparation"]
    assert first["request"]["clientToken"] == first["run"]
    assert run.driver.cleaned == [] and not ecs.stopped
    assert run.spawner.spawn(run.task).ok
    assert requests[0] == requests[1]
    assert len(ecs.launched) == 1


def test_transient_observation_failure_preserves_live_preparer_and_checkpoint(run, ecs, monkeypatch):  # noqa: F811
    client = nitro_preparation._client(CONFIG)
    describe = client.describe_tasks
    ecs.perform = False
    ecs.last = "RUNNING"
    run.task.dispatch_target = {**run.task.dispatch_target, "nitro_confidential": {"task_id": "in-flight"}}
    run.task.save(update_fields=["dispatch_target", "updated_at", "version"])
    client.describe_tasks = lambda **kwargs: {"tasks": [], "failures": []}
    with pytest.raises(nitro_preparation.PreparationUncertain, match="state is not confirmed"):
        run.spawner.spawn(run.task)
    assert len(ecs.launched) == 1 and run.driver.cleaned == [] and not ecs.stopped
    client.describe_tasks = describe
    run.task.refresh_from_db()
    run_id = run.task.dispatch_target["nitro_preparation"]["run"]
    nitro_preparation._record(run.task, run_id, ok=False, error="finished refusal")
    ecs.last, ecs.exit = "STOPPED", 1
    assert not run.spawner.spawn(run.task).ok
    assert len(ecs.launched) == 1 and len(run.driver.cleaned) == 1


def test_stop_with_unknown_launch_keeps_artifacts_and_uses_only_started_by_filter(run, ecs):  # noqa: F811
    client = nitro_preparation._client(CONFIG)
    nitro_preparation._launch(run.task, CONFIG, client)
    calls = []

    def empty(**kwargs):
        calls.append(kwargs)
        return {"taskArns": []}

    client.list_tasks = empty
    with pytest.raises(nitro_preparation.PreparationUncertain, match="identity is not confirmed"):
        run.spawner.stop(run.spawner._name(run.task))
    assert calls == [{"cluster": CONFIG["cluster"], "startedBy": "np-" + run.task.guid.hex}]
    assert not ecs.launched and run.driver.cleaned == []


def test_cancelled_live_preparer_must_stop_before_key_cleanup(run, ecs):  # noqa: F811
    client = nitro_preparation._client(CONFIG)
    launch = nitro_preparation._launch(run.task, CONFIG, client)
    nitro_preparation._record(run.task, launch["run"], task_arn="arn:task/live")
    ecs.last = "RUNNING"
    run.task.refresh_from_db()
    run.task.dispatch_target = {**run.task.dispatch_target, "nitro_confidential": {"task_id": "in-flight"}}
    run.task.save(update_fields=["dispatch_target", "updated_at", "version"])
    cleanup = run.driver.cleanup_confidential_task

    def after_stop(checkpoint):
        assert ecs.stopped and ecs.last == "STOPPED"
        return cleanup(checkpoint)

    run.driver.cleanup_confidential_task = after_stop
    run.spawner.stop(run.spawner._name(run.task))
    assert len(run.driver.cleaned) == 1


def test_stale_child_cannot_write_another_launch_outcome(run, ecs):  # noqa: F811
    launch = nitro_preparation._launch(run.task, CONFIG, nitro_preparation._client(CONFIG))
    with pytest.raises(nitro_preparation.PreparationUncertain, match="authority differs"):
        nitro_preparation.perform(str(run.task.guid), "f" * 32)
    run.task.refresh_from_db()
    assert run.task.dispatch_target["nitro_preparation"] == launch
    assert run.driver.seen is None


def test_uncertain_preparation_does_not_settle_dispatch_as_failed(run, ecs, monkeypatch):  # noqa: F811
    from astrolift_workflows.activities.agent_stage import _spawn_agent_task_sync

    client = nitro_preparation._client(CONFIG)
    client.describe_tasks = lambda **kwargs: {"tasks": [], "failures": []}
    ecs.perform = False
    with pytest.raises(nitro_preparation.PreparationUncertain):
        _spawn_agent_task_sync(run.task.pk)
    run.task.refresh_from_db()
    assert run.task.status == run.task.Status.PROVISIONING
    assert not run.driver.cleaned


def test_preparer_checks_its_own_workspace_before_resolving_secrets(run, ecs, monkeypatch):  # noqa: F811
    config = nitro_preparation._launch(run.task, CONFIG, nitro_preparation._client(CONFIG))
    monkeypatch.setenv(nitro_preparation.MARKER, f"{run.task.guid}:{config['run']}")
    report = run.driver.probe_nitro_readiness

    def small_workspace(slug, namespace, config, preparation=None):
        assert preparation is None
        result = report(slug, namespace, config)
        result["modes"]["confidential"] = {
            "state": "not_ready",
            "checks": [{"code": "workspace", "state": "not_ready"}],
        }
        return result

    run.driver.probe_nitro_readiness = small_workspace
    monkeypatch.setattr(
        "astrolift_dispatch.agent_secrets.resolve_task_secret_manifest",
        lambda **kwargs: pytest.fail("secrets must follow the actual preparer workspace check"),
    )
    assert not nitro_preparation.perform(str(run.task.guid), config["run"])
    assert run.driver.seen is None


def test_checkpoint_save_preserves_parent_stop_fence_and_launch_arn(run, ecs):  # noqa: F811
    prepare = run.driver.prepare_confidential_task

    def stop_during_prepare(*args, **kwargs):
        run.task.refresh_from_db()
        launch = run.task.dispatch_target["nitro_preparation"]
        nitro_preparation._record(run.task, launch["run"], task_arn="arn:task/new", stop_requested=True)
        return prepare(*args, **kwargs)

    run.driver.prepare_confidential_task = stop_during_prepare
    assert not run.spawner.spawn(run.task).ok
    run.task.refresh_from_db()
    launch = run.task.dispatch_target["nitro_preparation"]
    assert launch["task_arn"] == "arn:task/new" and launch["stop_requested"] is True
    assert run.driver.pod is None


def test_stop_wait_is_bounded_and_never_cleans_unconfirmed_producer(run, ecs, monkeypatch):  # noqa: F811
    client = nitro_preparation._client(CONFIG)
    launch = nitro_preparation._launch(run.task, CONFIG, client)
    nitro_preparation._record(run.task, launch["run"], task_arn="arn:task/live")
    client.describe_tasks = lambda **kwargs: {
        "tasks": [{"taskArn": kwargs["tasks"][0], "lastStatus": "RUNNING"}]
    }
    monkeypatch.setattr(nitro_preparation, "STOP_TIMEOUT_SECONDS", 0)
    with pytest.raises(nitro_preparation.PreparationUncertain, match="still stopping"):
        run.spawner.stop(run.spawner._name(run.task))
    assert ecs.stopped and run.driver.cleaned == []


def test_recovery_freezes_revision_network_and_owner_tag_across_configuration_roll(run, ecs, settings):  # noqa: F811
    client = nitro_preparation._client(CONFIG)
    launch = nitro_preparation._launch(run.task, CONFIG, client)
    settings.NITRO_PREPARATION_TASK = json.dumps(
        {
            **CONFIG,
            "task_definition": "next-family",
            "subnets": ["next-subnet"],
        }
    )
    # A replacement worker still submits the originally committed request.
    assert run.spawner.spawn(run.task).ok
    assert ecs.launched == [launch["request"]]
    assert ecs.launched[0]["taskDefinition"] == "arn:definition/7"
    assert ecs.launched[0]["tags"] == [
        {"key": nitro_preparation.OWNER_TAG, "value": CONFIG["task_definition"]}
    ]


def test_expired_unknown_launch_never_replays_its_client_token(run, ecs):  # noqa: F811
    launch = nitro_preparation._launch(run.task, CONFIG, nitro_preparation._client(CONFIG))
    nitro_preparation._record(run.task, launch["run"], deadline=0)
    with pytest.raises(nitro_preparation.PreparationUncertain, match="identity is not confirmed"):
        run.spawner.spawn(run.task)
    assert not ecs.launched and not ecs.stopped and not run.driver.cleaned


def test_unknown_arn_stop_discovers_only_the_frozen_revision_and_nonce(run, ecs):  # noqa: F811
    client = nitro_preparation._client(CONFIG)
    launch = nitro_preparation._launch(run.task, CONFIG, client)
    client.list_tasks = lambda **kwargs: {"taskArns": ["arn:task/other", "arn:task/owned"]}

    def describe(**kwargs):
        arn = kwargs["tasks"][0]
        marker = f"{run.task.guid}:{launch['run']}" if arn.endswith("owned") else "another-run"
        return {
            "tasks": [
                {
                    "taskArn": arn,
                    "taskDefinitionArn": launch["request"]["taskDefinition"],
                    "lastStatus": "STOPPED" if ecs.stopped else "RUNNING",
                    "overrides": {
                        "containerOverrides": [
                            {
                                "name": CONFIG["container"],
                                "environment": [{"name": nitro_preparation.MARKER, "value": marker}],
                            }
                        ]
                    },
                }
            ]
        }

    client.describe_tasks = describe
    nitro_preparation.stop_preparation(run.task)
    run.task.refresh_from_db()
    assert [item["task"] for item in ecs.stopped] == ["arn:task/owned"]
    assert run.task.dispatch_target["nitro_preparation"]["stopped"] is True
    assert not ecs.launched


def test_explicit_single_task_rejection_settles_without_discovering_a_nonexistent_producer(run, ecs):  # noqa: F811
    client = nitro_preparation._client(CONFIG)
    submissions = []

    def reject(**kwargs):
        submissions.append(kwargs)
        return {"tasks": [], "failures": [{"reason": "synthetic rejection with private diagnostics"}]}

    client.run_task = reject
    client.list_tasks = lambda **kwargs: pytest.fail("a rejected launch needs no discovery")
    for _ in range(2):
        result = run.spawner.spawn(run.task)
        assert not result.ok and result.error == "The confidential preparation task launch was rejected"
    run.task.refresh_from_db()
    launch = run.task.dispatch_target["nitro_preparation"]
    assert launch["stopped"] is True and launch["rejected"] is True
    assert len(submissions) == 1 and not ecs.stopped
    assert "private diagnostics" not in json.dumps(launch)


def test_preparer_cleanup_preserves_parent_launch_updates(run, ecs, monkeypatch):  # noqa: F811
    launch = nitro_preparation._launch(run.task, CONFIG, nitro_preparation._client(CONFIG))
    monkeypatch.setenv(nitro_preparation.MARKER, f"{run.task.guid}:{launch['run']}")
    run.task.dispatch_target = {**run.task.dispatch_target, "nitro_confidential": {"task_id": "in-flight"}}
    run.task.save(update_fields=["dispatch_target", "updated_at", "version"])
    cleanup = run.driver.cleanup_confidential_task

    def parent_updates_during_cleanup(checkpoint):
        nitro_preparation._record(run.task, launch["run"], task_arn="arn:task/new", stop_requested=True)
        return cleanup(checkpoint)

    run.driver.cleanup_confidential_task = parent_updates_during_cleanup
    run.spawner._cleanup_run(run.spawner._name(run.task))
    run.task.refresh_from_db()
    state = run.task.dispatch_target
    assert state["nitro_preparation"]["task_arn"] == "arn:task/new"
    assert state["nitro_preparation"]["stop_requested"] is True
    assert state["nitro_confidential"]["cleanup_complete"] is True
