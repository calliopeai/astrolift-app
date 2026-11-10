"""Actual dispatch seals the final Brief/secret environment and persists Pod recovery authority."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from aws.nitro_confidential import POLICY, prepared_pod_hash
from aws.nitro_confidential_image import task_environment

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Brief
from astrolift_agents.services.task_target import freeze_task_target, resolve_task_target
from astrolift_dispatch.spawners.k8s_confidential import ConfidentialPodSpawner
from astrolift_dispatch.spawners.registry import get_spawner
from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_manifest.tests.test_nitro_sandboxes_2668 import source
from astrolift_manifest.tests.test_persist import _add_environment, _scaffold
from astrolift_registry.models import Workload

pytestmark = pytest.mark.django_db


@pytest.fixture
def run(settings, monkeypatch):
    settings.AGENT_RUNTIME_CLASS = ""
    settings.PLATFORM_API_URL = "https://control.example"
    app = _scaffold()
    text = source('nitro_sandbox = {mode = "confidential"}', kind="agent")
    persist_manifest(app, normalize(parse_raw(text)), raw_text=text)
    workload = Workload.objects.select_related("registered_app").get(registered_app=app, slug="worker")
    cluster = _add_environment(app).tenant_cluster
    spec = AgentEnvironmentSpec.objects.create(
        organization=app.organization,
        name="Confidential",
        slug="confidential",
        image_tag="registry.example/agent@sha256:" + "1" * 64,
        run_as_non_root=True,
        allow_install=False,
        secret_refs=[{"env_var": "PROVIDER_TOKEN", "uri": "secretsmanager://provider-token"}],
    )
    brief = Brief.objects.create(
        organization=app.organization,
        registered_app=app,
        content_hash="a" * 64,
        manifest_snapshot={
            "system_prompt": "private system instructions",
            "env_vars": {"BRIEF_VALUE": "private brief value"},
        },
    )
    task = AgentTask.objects.create(
        organization=app.organization,
        agent_definition=workload,
        environment_spec=spec,
        brief=brief,
        status=AgentTask.Status.PROVISIONING,
    )
    backend, _, namespace = freeze_task_target(task, backend="k8s_job", cluster=cluster, namespace="agents")
    assert backend == "k8s_confidential_pod"
    task.refresh_from_db()
    assert resolve_task_target(task)[0] == "k8s_confidential_pod"
    driver = SimpleNamespace(
        pod=None, applied=[], seen=None, cleaned=[], deleted=[], fail_apply=False, cancel_during_prepare=False
    )

    def get_manifest(cluster, namespace, kind, name):
        if kind == "RuntimeClass":
            return {"metadata": {"name": "confidential-sandbox"}, "handler": "confidential-sandbox"}
        assert kind == "Pod"
        return driver.pod

    def prepare(cluster, namespace, **kwargs):
        pod = deepcopy(kwargs["pod"])
        assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
        assert pod["spec"]["containers"][0]["securityContext"]["capabilities"] == {"drop": ["ALL"]}
        driver.seen = task_environment(pod["spec"], kwargs["secret"])
        pod["spec"]["containers"][0].update(
            image="registry.example/run@sha256:" + "2" * 64,
            env=[{"name": "ASTROLIFT_CONFIDENTIAL_TASK_ID", "value": kwargs["task_id"]}],
        )
        pod["metadata"].setdefault("annotations", {})[POLICY] = "synthetic-generated-policy"
        checkpoint = {
            "task_id": kwargs["task_id"],
            "callback_hash": kwargs["callback_hash"],
            "image": pod["spec"]["containers"][0]["image"],
            "policy": "synthetic-generated-policy",
            "pod_sha256": prepared_pod_hash(pod),
            "attestation_verified": False,
        }
        kwargs["save"](checkpoint)
        if driver.cancel_during_prepare:
            from django.utils import timezone

            task.cancel_requested_at = timezone.now()
            task.save(update_fields=["cancel_requested_at", "updated_at", "version"])
        return pod, checkpoint

    def apply(cluster, namespace, manifests, *, create_only=False):
        assert all(row["kind"] != "Secret" for row in manifests)
        for manifest in manifests:
            driver.applied.append(deepcopy(manifest))
            if manifest["kind"] == "Pod" and not driver.fail_apply:
                assert create_only is True
                driver.pod = deepcopy(manifest)
                driver.pod["metadata"]["uid"] = "server-pod-uid"
                driver.pod["spec"]["nodeName"] = "scheduled-node"
                driver.pod["status"] = {"phase": "Running"}
        return SimpleNamespace(ok=not driver.fail_apply, summary=lambda: "synthetic refusal")

    def delete(cluster, namespace, manifests, **kwargs):
        driver.deleted.extend(manifests)
        driver.pod["metadata"]["deletionTimestamp"] = "server-deletion-time"
        return SimpleNamespace(ok=True)

    driver.get_manifest = get_manifest
    driver.validate_confidential_task = lambda *args: None
    driver.ensure_namespace = lambda *args: None
    driver.prepare_confidential_task = prepare
    driver.apply_manifests = apply
    driver.delete_manifests = delete
    driver.cleanup_confidential_task = lambda checkpoint: driver.cleaned.append(deepcopy(checkpoint))
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: driver)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug="qualification")
    )
    monkeypatch.setattr(
        "astrolift_dispatch.spawners.k8s_confidential.confidential_config", lambda cluster: object()
    )
    monkeypatch.setattr(
        "astrolift_dispatch.agent_secrets.resolve_task_secret_manifest",
        lambda **kwargs: {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": kwargs["secret_name"]},
            "stringData": {"PROVIDER_TOKEN": "private-provider-secret"},
        },
    )
    spawner = get_spawner(backend, cluster=cluster, namespace=namespace)
    assert isinstance(spawner, ConfidentialPodSpawner)
    return SimpleNamespace(task=task, cluster=cluster, driver=driver, spawner=spawner)


def test_final_brief_secret_callback_and_hardening_precede_ciphertext_pod_apply(run):
    result = run.spawner.spawn(run.task)
    assert result.ok, result.error
    assert run.driver.seen["AGENT_SYSTEM"] == "private system instructions"
    assert run.driver.seen["BRIEF_VALUE"] == "private brief value"
    assert run.driver.seen["PROVIDER_TOKEN"] == "private-provider-secret"
    assert run.driver.seen["ASTROLIFT_CLUSTER_KEY"].startswith("alft_cb_")
    public = json.dumps(run.driver.applied)
    assert "private-provider-secret" not in public and "private system instructions" not in public
    assert "alft_cb_" not in public and "secretKeyRef" not in public
    assert [row["kind"] for row in run.driver.applied] == ["Pod"]
    run.task.refresh_from_db()
    checkpoint = run.task.dispatch_target["nitro_confidential"]
    assert checkpoint["callback_hash"] == run.task.callback_token_hash
    assert "alft_cb_" not in json.dumps(checkpoint)
    assert run.spawner.recover(run.task).ok


@pytest.mark.parametrize("changed", ["image", "policy", "env", "credential"])
def test_recovery_refuses_changed_run_authority_without_rotating_credentials(run, changed):
    assert run.spawner.spawn(run.task).ok
    run.task.refresh_from_db()
    before = run.task.callback_token_hash
    if changed == "image":
        run.driver.pod["spec"]["containers"][0]["image"] = "another-image"
    elif changed == "policy":
        run.driver.pod["metadata"]["annotations"][POLICY] = "another-policy"
    elif changed == "env":
        run.driver.pod["spec"]["containers"][0]["env"].append({"name": "INJECTED", "value": "value"})
    else:
        run.task.callback_token_hash = "changed-credential-hash"
    assert not run.spawner.recover(run.task).ok
    run.task.refresh_from_db()
    assert run.task.callback_token_hash == before


def test_stop_uses_uid_precondition_and_keeps_cleanup_pending_until_pod_disappears(run):
    assert run.spawner.spawn(run.task).ok
    name = run.spawner._name(run.task)
    run.spawner.stop(name, expected_task_guid=str(run.task.guid))
    assert run.driver.deleted[0]["metadata"]["uid"] == "server-pod-uid"
    assert not run.driver.cleaned
    assert not run.spawner.confirm_stopped(name)
    run.driver.pod = None
    assert run.spawner.confirm_stopped(name)
    run.task.refresh_from_db()
    assert run.task.dispatch_target["nitro_confidential"]["cleanup_complete"] is True
    assert len(run.driver.cleaned) == 1


def test_failed_pod_create_retires_prepared_artifacts_without_any_plaintext_apply(run):
    run.driver.fail_apply = True
    result = run.spawner.spawn(run.task)
    assert not result.ok
    assert run.driver.pod is None
    assert len(run.driver.cleaned) == 1
    assert not run.driver.deleted


def test_stop_refuses_a_replacement_with_another_policy(run):
    assert run.spawner.spawn(run.task).ok
    run.driver.pod["metadata"]["annotations"][POLICY] = "replacement-policy"
    with pytest.raises(RuntimeError, match="image/policy ownership"):
        run.spawner.stop(run.spawner._name(run.task), expected_task_guid=str(run.task.guid))
    assert not run.driver.deleted and not run.driver.cleaned


def test_cancellation_during_image_preparation_never_creates_a_pod(run):
    run.driver.cancel_during_prepare = True
    result = run.spawner.spawn(run.task)
    assert not result.ok
    assert "cancelled during confidential image" in result.error
    assert not run.driver.applied
    assert len(run.driver.cleaned) == 1
