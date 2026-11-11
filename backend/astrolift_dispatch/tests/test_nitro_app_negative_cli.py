"""Inspect the operator CLI through real App rows; only provider IO is substituted."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from aws.nitro_confidential import POLICY, key_policy, prepared_pod_hash
from django.db import connection
from django.test.utils import CaptureQueriesContext

from astrolift_agents.models import AgentEnvironmentSpec, AgentSecretBindingOverride, AgentTask, Brief
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.nitro import confidential_config
from astrolift_manifest.tests.test_persist import _scaffold
from astrolift_registry.models import Container, Workload

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "nitro_app_negative_cli", SCRIPTS / "qualify_nitro_app_negative.py"
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)
sys.path.remove(str(SCRIPTS))

pytestmark = pytest.mark.django_db


@pytest.fixture
def inspection(settings, tmp_path, monkeypatch, request):
    app = _scaffold()
    org = app.organization
    org.slug = "conflict"
    org.save()
    plugin, _ = ProviderPlugin.objects.get_or_create(slug="aws", defaults={"name": "AWS"})
    cluster = TenantCluster.objects.create(
        guid=probe.CLUSTER_GUID,
        organization=None,
        provider_plugin=plugin,
        slug="conflict-shared",
        name="Conflict",
        region="us-west-2",
        auth_method="exec_plugin",
        endpoint="https://conflict.example.invalid",
    )
    settings.NITRO_CONFIDENTIAL_TASKS_CONFIG = json.dumps(
        {
            probe.CLUSTER_GUID: {
                "registry": "123456789012.dkr.ecr.us-west-2.amazonaws.com/runs",
                "key_admin_arn": "arn:aws:iam::123456789012:role/conflict-admin",
                "runtime_role_arn": "arn:aws:iam::123456789012:role/conflict-runtime",
                "service_account": "confidential-runner",
                "runtime_directory": str(tmp_path),
                "temporary_root": str(tmp_path),
            }
        }
    )
    config = confidential_config(cluster)
    workload = Workload.objects.create(
        registered_app=app, name="Qualification", slug="qualification", kind="agent", run_family="task"
    )
    primary = Container.objects.create(
        workload=workload,
        name="probe",
        is_primary=True,
        image_ref=probe.BASE_IMAGE,
        command=["python3", "-c"],
        args=[probe.PROBE.read_text()],
    )
    environment = AgentEnvironmentSpec.objects.create(
        organization=org,
        project=app.project,
        name="Qualification",
        slug="qualification",
        image_tag=probe.BASE_IMAGE,
        run_as_non_root=True,
        allow_install=False,
    )
    nonce_binding = AgentSecretBindingOverride.objects.create(
        environment_spec=environment,
        env_var="ASTROLIFT_QUALIFICATION_SECRET",
        uri=f"agents/{org.guid}/projects/{app.project.guid}/qualification",
    )
    scenario = getattr(request, "param", None)
    guard = None
    if scenario in {
        "guard_active",
        "guard_removed",
        "guard_deleted",
        "guard_deleted_before",
        "guard_renamed",
    }:
        guard = AgentSecretBindingOverride.objects.create(
            environment_spec=environment,
            env_var="QUALIFICATION_PUBLIC_VARIANT",
            uri=f"agents/{org.guid}/projects/{app.project.guid}/guard",
        )
        if scenario == "guard_deleted_before":
            guard.soft_delete()
    brief = Brief.objects.create(organization=org, registered_app=app, content_hash="b" * 64, status="ready")
    task = AgentTask.objects.create(
        organization=org,
        team=app.team,
        project=app.project,
        agent_definition=workload,
        environment_spec=environment,
        brief=brief,
        status="running",
        callback_token_hash="c" * 64,
    )
    compact = task.guid.hex
    image = config.registry + "@sha256:" + "d" * 64
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "agent-task-" + compact,
            "namespace": probe.NAMESPACE,
            "uid": "positive-pod-uid",
            "labels": {"astrolift.dev/task-id": str(task.guid)},
            "annotations": {POLICY: "prepared-policy"},
        },
        "spec": {
            "runtimeClassName": "confidential-sandbox",
            "serviceAccountName": config.service_account,
            "securityContext": {"runAsUser": 42042, "runAsGroup": 42042, "fsGroup": 42042},
            "containers": [
                {
                    "name": "probe",
                    "image": image,
                    "env": [{"name": "ASTROLIFT_CONFIDENTIAL_TASK_ID", "value": compact}],
                }
            ],
        },
        "status": {"phase": "Running"},
    }
    checkpoint = {
        "task_id": compact,
        "callback_hash": task.callback_token_hash,
        "registry": config.registry,
        "image": image,
        "policy": "prepared-policy",
        "pod_sha256": prepared_pod_hash(pod),
        "pcr17": "a" * 96,
        "key_arn": "arn:aws:kms:us-west-2:123456789012:key/11111111-1111-1111-1111-111111111111",
    }
    task.dispatch_target = {
        "backend": "k8s_confidential_pod",
        "cluster_guid": str(cluster.guid),
        "namespace": probe.NAMESPACE,
        "nitro_confidential": checkpoint,
    }
    task.result = {
        "findings": [
            {
                "sealed_inputs_ok": True,
                "uid": 42042,
                "gid": 42042,
                "brief_id": str(brief.guid),
                "brief_hash": brief.content_hash,
                "qualification": "orm-cli",
            }
        ]
    }
    task.save()
    if scenario == "guard_removed":
        guard.removed = True
        guard.save()
    elif scenario == "guard_deleted":
        guard.soft_delete()
    elif scenario == "guard_renamed":
        guard.env_var = "OTHER_SECRET"
        guard.save()
    elif scenario == "nonce_changed":
        nonce_binding.uri += "-changed"
        nonce_binding.save()
    elif scenario == "nonce_removed":
        nonce_binding.removed = True
        nonce_binding.save()
    elif scenario == "nonce_deleted":
        nonce_binding.soft_delete()
    elif scenario == "nonce_added":
        AgentSecretBindingOverride.objects.create(
            environment_spec=environment,
            env_var="NEW_SECRET",
            uri=nonce_binding.uri,
        )
    driver = Mock()
    driver._config.cluster_name = probe.CLUSTER_NAME
    driver._config.region = "us-west-2"
    driver._eks.describe_cluster.return_value = {
        "cluster": {
            "status": "ACTIVE",
            "version": "1.36",
            "arn": "arn:aws:eks:us-west-2:123456789012:cluster/conflict-astrolift",
        }
    }
    k8s = driver._k8s.return_value
    k8s.get.return_value = pod
    session = Mock()
    kms, ecr = Mock(), Mock()
    session.client.side_effect = {"kms": kms, "ecr": ecr}.__getitem__
    kms.describe_key.return_value = {"KeyMetadata": {"Arn": checkpoint["key_arn"], "KeyState": "Enabled"}}
    kms.list_resource_tags.return_value = {"Tags": [{"TagKey": "astrolift-task-id", "TagValue": compact}]}
    kms.get_key_policy.return_value = {"Policy": json.dumps(key_policy(config, checkpoint["pcr17"]))}
    ecr.describe_images.return_value = {
        "imageDetails": [{"imageTags": [compact], "imageDigest": "sha256:" + "d" * 64}]
    }
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda value: driver)
    monkeypatch.setattr("aws.session.aws_session", lambda **kwargs: session)
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPTS / "qualify_nitro_app_negative.py"),
            "--task",
            str(task.guid),
            "--positive-uid",
            "positive-pod-uid",
            "--checkpoint-sha256",
            probe.digest(checkpoint),
            "--receipt",
            str(receipt),
        ],
    )
    return task, primary, environment, driver, k8s, kms, ecr, receipt


def test_cli_default_inspect_loads_actual_checkpoint_and_relations_without_writes(inspection, capsys):
    task, primary, environment, driver, k8s, kms, ecr, receipt = inspection
    before = task.dispatch_target
    with CaptureQueriesContext(connection) as queries:
        probe.main()
    output = json.loads(capsys.readouterr().out)
    assert output["action"] == "inspect_only"
    assert output["cluster_guid"] == probe.CLUSTER_GUID
    assert output["checkpoint_sha256"] == probe.digest(before["nitro_confidential"])
    assert len(output["original_grant_sha256"]) == 64
    assert any("astrolift_agents_agenttask" in query["sql"] for query in queries)
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries)
    task.refresh_from_db()
    assert task.dispatch_target == before and task.status == "running"
    assert Container.objects.get(pk=primary.pk).args == [probe.PROBE.read_text()]
    assert AgentEnvironmentSpec.objects.get(pk=environment.pk).run_as_non_root
    assert Brief.objects.get(pk=task.brief_id).content_hash == "b" * 64
    assert not receipt.exists()
    driver.validate_confidential_task.assert_called_once()
    k8s.get.assert_called_once_with(kind="Pod", namespace=probe.NAMESPACE, name="agent-task-" + task.guid.hex)
    k8s.admit_pod.assert_not_called()
    k8s._resource_for.assert_not_called()
    kms.put_key_policy.assert_not_called()
    kms.create_key.assert_not_called()
    ecr.put_image.assert_not_called()


@pytest.mark.parametrize("change", ["checkpoint", "probe", "environment", "deleted_task"])
def test_cli_inspect_refuses_changed_persisted_authority(inspection, change):
    task, primary, environment, _, k8s, kms, _, receipt = inspection
    if change == "checkpoint":
        task.dispatch_target["nitro_confidential"]["callback_hash"] = "changed"
        task.save()
    elif change == "probe":
        primary.args = ["print('different command')"]
        primary.save()
    elif change == "environment":
        environment.env_vars = {"QUALIFICATION_PUBLIC_VARIANT": "positive"}
        environment.save()
    else:
        from django.utils import timezone

        task.deleted_at = timezone.now()
        task.save()
    with pytest.raises((ValueError, AgentTask.DoesNotExist)):
        probe.main()
    kms.describe_key.assert_not_called()
    k8s.admit_pod.assert_not_called()
    k8s._resource_for.assert_not_called()
    assert not receipt.exists()


@pytest.mark.parametrize(
    "inspection",
    [
        "guard_active",
        "guard_removed",
        "guard_deleted",
        "guard_deleted_before",
        "guard_renamed",
        "nonce_changed",
        "nonce_removed",
        "nonce_deleted",
        "nonce_added",
    ],
    indirect=True,
)
def test_cli_refuses_effective_historical_or_changed_override_before_pod_or_mutation(inspection):
    task, _, environment, _, k8s, kms, ecr, receipt = inspection
    # Native override CRUD does not bump the parent spec, so its timestamp alone
    # cannot prove that the sealed task's original bindings were guard-free.
    assert environment.updated_at <= task.created_at
    with pytest.raises(ValueError, match="secret binding"):
        probe.main()
    k8s.get.assert_not_called()
    k8s.admit_pod.assert_not_called()
    k8s._resource_for.assert_not_called()
    kms.describe_key.assert_not_called()
    kms.put_key_policy.assert_not_called()
    ecr.describe_images.assert_not_called()
    assert not receipt.exists()
