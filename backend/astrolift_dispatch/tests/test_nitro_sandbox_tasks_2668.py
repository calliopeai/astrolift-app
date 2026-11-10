"""Task isolation is selected from its own app and cannot override operator policy."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest
from django.test import override_settings

from astrolift_dispatch.nitro_sandbox import selected_task_runtime
from astrolift_dispatch.pod_hardening import AgentRuntimeClassError, preflight_agent_runtime
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner, _render_agent_job
from astrolift_dispatch.tests.test_k8s_job_vnc import _FakeTask, _FakeWorkload


def workload(block=None, *, selected=True):
    row = {"name": "worker", "kind": "agent", "run_family": "task"}
    if selected:
        row["nitro_sandbox"] = block if block is not None else {"mode": "isolated"}
    result = _FakeWorkload("registry.example/agent@sha256:abc", 0)
    result.slug = "worker"
    result.registered_app = SimpleNamespace(manifest_normalized={"workloads": [row]})
    return result


@override_settings(AGENT_RUNTIME_CLASS="")
def test_only_the_selected_workload_uses_isolation_and_source_is_unchanged():
    selected = workload()
    before = deepcopy(selected.registered_app.manifest_normalized)
    assert selected_task_runtime(selected) == "isolated-sandbox"
    selected.slug = "neighbor"
    assert selected_task_runtime(selected) == ""
    assert selected.registered_app.manifest_normalized == before


@override_settings(AGENT_RUNTIME_CLASS="gvisor")
def test_explicit_selection_cannot_replace_operator_runtime_policy():
    with pytest.raises(AgentRuntimeClassError, match="operator AGENT_RUNTIME_CLASS"):
        selected_task_runtime(workload())
    assert selected_task_runtime(workload(selected=False)) == ""


@override_settings(AGENT_RUNTIME_CLASS="isolated-sandbox")
def test_matching_operator_runtime_is_accepted():
    assert selected_task_runtime(workload()) == "isolated-sandbox"


@override_settings(AGENT_RUNTIME_CLASS="")
def test_environment_gpu_request_is_refused():
    with pytest.raises(AgentRuntimeClassError, match="GPU"):
        selected_task_runtime(workload(), SimpleNamespace(gpu=1))


@pytest.mark.parametrize(
    "block",
    [
        {"mode": "confidential", "policy": "policy=="},
        {"mode": "isolated", "network_isolated": True},
        {"mode": "isolated", "policy": "policy=="},
        {"mode": "isolated", "network_isolated": "false"},
        {"mode": "isolated", "unexpected": True},
        {"mode": "unknown"},
        [],
        "isolated",
    ],
)
@override_settings(AGENT_RUNTIME_CLASS="")
def test_unsupported_or_malformed_stored_selector_fails_closed(block):
    with pytest.raises(AgentRuntimeClassError):
        selected_task_runtime(workload(block))


@override_settings(AGENT_RUNTIME_CLASS="")
def test_actual_render_keeps_security_baseline_and_ordinary_output():
    w = workload(selected=False)
    t = _FakeTask(guid="task-1", vnc_enabled=False)
    baseline = _render_agent_job(job_name="agent-task-1", workload=w, namespace="team", task=t)
    assert (
        _render_agent_job(job_name="agent-task-1", workload=w, namespace="team", task=t, runtime_class="")
        == baseline
    )
    isolated = _render_agent_job(
        job_name="agent-task-1", workload=w, namespace="team", task=t, runtime_class="isolated-sandbox"
    )
    expected = deepcopy(baseline)
    expected["spec"]["template"]["spec"]["runtimeClassName"] = "isolated-sandbox"
    assert isolated == expected
    assert isolated["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


@pytest.mark.parametrize("handler", ["runc", None])
def test_runtime_read_requires_actual_aws_handler(monkeypatch, handler):
    provider = SimpleNamespace(
        get_manifest=lambda *args: {"metadata": {"name": "isolated-sandbox"}, "handler": handler}
    )
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: provider)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug="target")
    )
    with pytest.raises(AgentRuntimeClassError, match="AWS handler"):
        preflight_agent_runtime(None, {"runtimeClassName": "isolated-sandbox"})


@pytest.mark.parametrize("existing_runtime,ok", [(None, False), ("runc", False), ("isolated-sandbox", True)])
@override_settings(AGENT_RUNTIME_CLASS="")
def test_recovery_cannot_adopt_a_job_with_the_wrong_runtime(monkeypatch, existing_runtime, ok):
    job_name = "agent-task-task1"
    token = "synthetic-callback"
    job = {
        "metadata": {"name": job_name, "labels": {"astrolift.dev/task-id": "task1"}},
        "spec": {
            "template": {
                "spec": {
                    "runtimeClassName": existing_runtime,
                    "containers": [{"env": [{"name": "ASTROLIFT_CLUSTER_KEY", "value": token}]}],
                }
            }
        },
    }
    provider = SimpleNamespace(get_manifest=lambda *args: job)
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: provider)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug="target")
    )
    task = SimpleNamespace(
        guid="task1",
        callback_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        agent_definition=workload(),
        environment_spec=None,
    )
    result = K8sJobSpawner(object(), "team").recover(task)
    assert result.ok is ok
    if not ok:
        assert "selected Nitro Sandbox runtime" in result.error


@override_settings(AGENT_RUNTIME_CLASS="gvisor")
def test_spawn_conflict_refuses_before_any_provider_lookup(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("provider lookup must follow selector conflict validation")

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", forbidden)
    task = _FakeTask(guid="task1", vnc_enabled=False)
    task.agent_definition = workload()
    result = K8sJobSpawner(object(), "team").spawn(task)
    assert not result.ok
    assert "operator AGENT_RUNTIME_CLASS" in result.error


@override_settings(AGENT_RUNTIME_CLASS="confidential-sandbox")
@pytest.mark.parametrize("selected", [True, False])
def test_operator_confidential_runtime_refuses_before_any_credentials_or_provider(monkeypatch, selected):
    def forbidden(*args, **kwargs):
        pytest.fail("confidential refusal must precede provider access")

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", forbidden)
    task = _FakeTask(guid="task1", vnc_enabled=False)
    task.agent_definition = workload(selected=selected)
    result = K8sJobSpawner(object(), "team").spawn(task)
    assert not result.ok
    assert "attested secret delivery" in result.error


def test_confidential_preflight_refuses_even_with_policy_before_runtime_lookup(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a supplied policy does not establish attested credential delivery")

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", forbidden)
    with pytest.raises(AgentRuntimeClassError, match="attested secret delivery"):
        preflight_agent_runtime(None, {"runtimeClassName": "confidential-sandbox"})


@override_settings(AGENT_RUNTIME_CLASS="")
def test_missing_runtime_refuses_before_managed_model_provisioning(monkeypatch):
    provider = SimpleNamespace(get_manifest=lambda *args: None)
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: provider)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug="target")
    )

    def forbidden(**kwargs):
        pytest.fail("managed-model provisioning must follow sandbox preflight")

    monkeypatch.setattr("astrolift_dispatch.agent_model.resolve_managed_model_wiring", forbidden)
    task = _FakeTask(
        guid="task1",
        vnc_enabled=False,
        environment_spec=SimpleNamespace(image_tag="registry.example/agent:1", managed_model=True, gpu=0),
    )
    task.agent_definition = workload()
    result = K8sJobSpawner(object(), "team").spawn(task)
    assert not result.ok
    assert "not available" in result.error


@override_settings(AGENT_RUNTIME_CLASS="gvisor")
def test_imported_agent_package_extensions_are_not_astrolift_workload_selectors():
    from astrolift_agents.services.agent_package import PACKAGE_SCHEMA

    imported = workload()
    imported.registered_app.manifest_normalized["schema"] = PACKAGE_SCHEMA
    assert selected_task_runtime(imported) == ""
    imported.registered_app.manifest_normalized["workloads"] = {"vendor-extension": True}
    assert selected_task_runtime(imported) == ""


@pytest.mark.parametrize("path", [("spec",), ("spec", "template"), ("spec", "template", "spec")])
@pytest.mark.parametrize("malformed", [None, []])
@override_settings(AGENT_RUNTIME_CLASS="")
def test_recovery_refuses_null_or_list_pod_template_fields(monkeypatch, path, malformed):
    token = "synthetic-callback"
    job = {
        "metadata": {"name": "agent-task-task1", "labels": {"astrolift.dev/task-id": "task1"}},
        "spec": {
            "template": {
                "spec": {
                    "runtimeClassName": "isolated-sandbox",
                    "containers": [{"env": [{"name": "ASTROLIFT_CLUSTER_KEY", "value": token}]}],
                }
            }
        },
    }
    parent = job
    for field in path[:-1]:
        parent = parent[field]
    parent[path[-1]] = malformed
    provider = SimpleNamespace(get_manifest=lambda *args: job)
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: provider)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug="target")
    )
    task = SimpleNamespace(
        guid="task1",
        callback_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        agent_definition=workload(),
        environment_spec=None,
    )
    result = K8sJobSpawner(object(), "team").recover(task)
    assert not result.ok
    assert result.external_id == "agent-task-task1"
    assert result.error == "Existing agent Job has a malformed pod template"
