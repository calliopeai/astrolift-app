"""runTask applies the task's Kubernetes Job and follows it (#2330)."""

# ruff: noqa: F811  (fixtures imported from test_task_run are pytest parameters)

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import AppEnvironment, Deployment, TaskRun
from astrolift_lifecycle.schema.mutations import LifecycleMutation, RunTaskInput
from astrolift_lifecycle.services import task_runner
from astrolift_lifecycle.tests.test_task_run import (  # noqa: F401
    _admin_info,
    app,
    cluster,
    env,
    task_workload,
    user,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

RENDERED = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {"name": "db-migrate", "namespace": "acme-task-app-staging", "labels": {"app": "task-app"}},
    "spec": {
        "completions": 1,
        "backoffLimit": 0,
        "template": {
            "metadata": {"labels": {"app": "task-app"}},
            "spec": {
                "restartPolicy": "Never",
                "tolerations": [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}],
                "nodeSelector": {"astrolift.io/gpu-type": "a10g"},
                "containers": [
                    {
                        "name": "migrate",
                        "image": "ghcr.io/acme/task:v1",
                        "command": ["python", "train.py"],
                        "args": ["--epochs", "3"],
                        "resources": {"limits": {"nvidia.com/gpu": "1"}},
                    }
                ],
            },
        },
    },
}


@pytest.fixture
def cluster_wire(monkeypatch):
    applied, outcome = [], {"ok": True}

    class Driver:
        def apply_manifests(self, slug, namespace, manifests):
            applied.append((namespace, manifests))
            return SimpleNamespace(ok=outcome["ok"], errors=() if outcome["ok"] else ("quota exceeded",))

    monkeypatch.setattr(task_runner, "render_resources_for_deployment", lambda d: [RENDERED])
    monkeypatch.setattr(
        task_runner, "driver_for_deployment", lambda d: (Driver(), SimpleNamespace(slug="c"), None)
    )
    return SimpleNamespace(applied=applied, outcome=outcome)


@pytest.fixture
def running(app, env):
    return Deployment.objects.create(
        registered_app=app, app_environment=env, image_tag="v1", status="running"
    )


def _run(org, user, permission_resolver, **fields):
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        return LifecycleMutation().run_task(
            _admin_info(user), input=RunTaskInput(app_slug="task-app", workload_slug="db-migrate", **fields)
        )


def test_run_applies_the_rendered_task_job_with_gpu_scheduling(
    org, user, task_workload, env, running, cluster_wire, permission_resolver
):
    result = _run(org, user, permission_resolver, environment_name="staging", command=["python", "eval.py"])

    assert result.ok, result.errors
    run = TaskRun.objects.get(guid=str(result.data.id))
    [(namespace, [job])] = cluster_wire.applied
    assert namespace == "acme-task-app-staging" == run.namespace
    assert job["metadata"]["name"] == run.k8s_job_name == result.data.k8s_job_name
    assert run.k8s_job_name.startswith("db-migrate-run-")
    assert job["metadata"]["labels"]["astrolift.io/task-run"] == str(run.guid)
    pod = job["spec"]["template"]["spec"]
    assert pod["containers"][0]["command"] == ["python", "eval.py"] and "args" not in pod["containers"][0]
    assert pod["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert pod["tolerations"][0]["key"] == "nvidia.com/gpu"
    assert pod["nodeSelector"] == {"astrolift.io/gpu-type": "a10g"}
    assert job["spec"]["backoffLimit"] == 0
    assert RENDERED["metadata"]["name"] == "db-migrate"  # the rendered template is untouched
    assert run.status == TaskRun.Status.PENDING and result.data.environment_name == "staging"


def test_the_declared_command_runs_when_none_is_given(
    org, user, task_workload, env, running, cluster_wire, permission_resolver
):
    assert _run(org, user, permission_resolver, environment_name="staging").ok
    [(_ns, [job])] = cluster_wire.applied
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "train.py"] and container["args"] == ["--epochs", "3"]


def test_a_retried_request_id_never_launches_a_second_job(
    org, user, task_workload, env, running, cluster_wire, permission_resolver
):
    first = _run(org, user, permission_resolver, environment_name="staging", request_id="req-1")
    second = _run(org, user, permission_resolver, environment_name="staging", request_id="req-1")
    changed = _run(
        org, user, permission_resolver, environment_name="staging", request_id="req-1", command=["x"]
    )

    assert first.ok and second.ok and first.data.id == second.data.id
    assert len(cluster_wire.applied) == 1
    assert TaskRun.objects.filter(workload=task_workload).count() == 1
    assert not changed.ok and changed.errors[0].code == "CONFLICT"


def test_an_apply_refusal_is_a_terminal_failure_with_the_reason(
    org, user, task_workload, env, running, cluster_wire, permission_resolver
):
    cluster_wire.outcome["ok"] = False
    result = _run(org, user, permission_resolver, environment_name="staging")

    assert result.ok and result.data.status == "failed"
    assert result.data.failure_reason == "quota exceeded"
    run = TaskRun.objects.get(guid=str(result.data.id))
    assert run.k8s_job_name == "" and run.ended_at is not None


def test_environment_is_inferred_only_when_unambiguous(
    org, user, app, task_workload, env, cluster, running, cluster_wire, permission_resolver
):
    inferred = _run(org, user, permission_resolver)
    assert inferred.ok and inferred.data.environment_name == "staging"

    AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    ambiguous = _run(org, user, permission_resolver)
    assert not ambiguous.ok and ambiguous.errors[0].field == "environmentName"


def test_an_oversized_command_is_refused_before_any_run(
    org, user, task_workload, env, running, cluster_wire, permission_resolver
):
    result = _run(org, user, permission_resolver, environment_name="staging", command=["x"] * 65)
    assert not result.ok and result.errors[0].field == "command"
    assert not TaskRun.objects.exists() and cluster_wire.applied == []


def test_job_name_is_stable_per_run_and_dns_safe(task_workload):
    run = TaskRun.objects.create(workload=task_workload, command=[])
    task_workload.slug = "a" * 80
    name = task_runner.task_job_name(run)
    assert len(name) <= 63 and name == task_runner.task_job_name(run)
