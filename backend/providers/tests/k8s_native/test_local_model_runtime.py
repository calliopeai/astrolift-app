"""Private delivery placement, serving argv and independently owned auth snapshots."""

import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from _sdk.local_model_artifact import ModelFile, model_manifest
from _sdk.managed_service import DeprovisionSpec
from k8s_native.managed.model_endpoint_vllm import VLLMDriver

from .._sdk.test_local_model_artifact_http import contents
from .test_shared_model_runtime import object_of, setup


def local_setup(mode="cpu"):
    driver, cluster, secrets, spec = setup(mode)
    artifact_id = str(uuid4())
    manifest, digest = model_manifest(
        [ModelFile(name, len(body), hashlib.sha256(body).hexdigest()) for name, body in contents().items()]
    )
    cfg = {
        **spec.config,
        "model_source": "local_artifact",
        "model": "local-" + artifact_id,
        "model_artifact_id": artifact_id,
        "model_artifact_version": 2,
        "model_artifact_manifest_sha256": digest,
    }
    del cfg["model_revision"]
    plan = {
        "organization_id": spec.organization_id,
        "cluster_id": spec.tenant_cluster_id,
        "managed_service_id": spec.managed_service_id,
        "artifact_id": artifact_id,
        "artifact_version": 2,
        "manifest_sha256": digest,
        "files": [{**file, "url": "https://objects.invalid/private-download-marker"} for file in manifest],
    }
    driver = VLLMDriver(config=replace(driver._config, local_model_delivery=plan))
    return driver, cluster, secrets, replace(spec, config=cfg)


@pytest.mark.parametrize("mode", ["cpu", "gpu"])
def test_local_runtime_fixed_path_readonly_model_and_auth_coexistence(mode):
    driver, cluster, secrets, spec = local_setup(mode)
    result = driver.provision(spec)
    assert result.ok and not result.ready, result.message
    pod = object_of(cluster, "apps/v1/Deployment")["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["args"][:2] == ["--model", "/models/" + spec.config["model_artifact_manifest_sha256"]]
    assert container["args"][container["args"].index("--served-model-name") + 1] == spec.config["model"]
    assert container["args"][container["args"].index("--load-format") + 1] == "safetensors"
    assert "--revision" not in container["args"] and "--trust-remote-code" not in container["args"]
    env = {row["name"]: row.get("value") for row in container["env"]}
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1" and "HF_TOKEN" not in env
    assert container["volumeMounts"][0] == {"name": "cache", "mountPath": "/models", "readOnly": True}
    init = pod["initContainers"][0]
    assert init["image"] == container["image"]
    assert init["volumeMounts"][0] == {"name": "cache", "mountPath": "/models"}
    assert init["args"] == ["/model-delivery/delivery.json", "/models"]
    assert "private-download-marker" not in json.dumps(pod)
    cm = object_of(cluster, "v1/ConfigMap")
    assert "local_model_delivery.py" in cm["data"] and "private-download-marker" not in json.dumps(cm)
    auth = next(
        row for (kind, _, _), row in cluster.objects.items() if kind == "v1/Secret" and "keys.json" in row["stringData"]
    )
    assert len(json.loads(auth["stringData"]["keys.json"])["subscription_keys"]) == 2
    delivery = next(
        row
        for (kind, _, _), row in cluster.objects.items()
        if kind == "v1/Secret" and "delivery.json" in row["stringData"]
    )
    assert delivery["metadata"]["labels"]["astrolift.io/managed-service-id"] == spec.managed_service_id
    assert "private-download-marker" in delivery["stringData"]["delivery.json"]
    assert all("private-download-marker" not in json.dumps(value) for value in secrets.data.values())


@pytest.mark.parametrize(
    "failure",
    [
        "missing_plan",
        "foreign_org",
        "foreign_cluster",
        "foreign_service",
        "artifact_version",
        "manifest",
        "hf_token",
        "revision",
        "unknown_source",
        "foreign_secret",
    ],
)
def test_source_and_private_plan_refuse_before_secret_or_cluster_write(failure):
    driver, cluster, secrets, spec = local_setup()
    cfg = dict(spec.config)
    plan = copy.deepcopy(driver._config.local_model_delivery)
    if failure == "missing_plan":
        plan = {}
    elif failure.startswith("foreign_") and failure != "foreign_secret":
        field = {
            "foreign_org": "organization_id",
            "foreign_cluster": "cluster_id",
            "foreign_service": "managed_service_id",
        }[failure]
        plan[field] = str(uuid4())
    elif failure == "artifact_version":
        plan["artifact_version"] += 1
    elif failure == "manifest":
        plan["files"][0]["sha256"] = "0" * 64
    elif failure == "hf_token":
        cfg["hf_token_secret_ref"] = "foreign#token"
    elif failure == "revision":
        cfg["model_revision"] = "a" * 40
    elif failure == "unknown_source":
        cfg["model_source"] = "arbitrary"
    else:
        namespace, name = driver._namespace(spec), driver._resource_name(spec)
        cluster.objects[("v1/Secret", namespace, name + "-model-delivery")] = {
            "metadata": {
                "labels": {"app.kubernetes.io/managed-by": "astrolift", "astrolift.io/managed-service-id": str(uuid4())}
            }
        }
    driver = VLLMDriver(config=replace(driver._config, local_model_delivery=plan))
    result = driver.provision(replace(spec, config=cfg))
    assert not result.ok and not cluster.writes and not secrets.writes
    assert "private-download-marker" not in result.message


def test_delivery_secret_cleanup_refuses_foreign_and_retains_model_bytes():
    driver, cluster, _, spec = local_setup()
    result = driver.provision(spec)
    assert result.ok
    deleted = []

    def delete(cid, ns, rows):
        deleted.extend(rows)
        return SimpleNamespace(ok=True)

    cluster.delete_manifests = delete
    secret = next(
        row
        for (kind, _, _), row in cluster.objects.items()
        if kind == "v1/Secret" and "delivery.json" in row["stringData"]
    )
    original = secret["metadata"]["labels"]["astrolift.io/managed-service-id"]
    secret["metadata"].update(uid="owned-delivery-uid", resourceVersion="17")
    secret["metadata"]["labels"]["astrolift.io/managed-service-id"] = str(uuid4())
    delete_spec = DeprovisionSpec(result.handle, managed_service_id=spec.managed_service_id)
    assert not driver.deprovision(delete_spec).ok and not deleted
    secret["metadata"]["labels"]["astrolift.io/managed-service-id"] = original
    assert driver.deprovision(delete_spec).ok
    assert any(row["kind"] == "Secret" and row["metadata"]["name"].endswith("-model-delivery") for row in deleted)
    assert not any(row["kind"] == "PersistentVolumeClaim" for row in deleted)
