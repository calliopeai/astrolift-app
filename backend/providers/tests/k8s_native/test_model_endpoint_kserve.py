from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.availability import MATRIX
from _sdk.cluster_capabilities import ClusterCapabilities
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed.model_endpoint_kserve import (
    API_VERSION,
    KServeConfig,
    KServeDriver,
)
from k8s_native.plugin import PLUGIN
from k8s_native.preflight import preflight

IMAGE = "registry.example.test/models/transformer@sha256:" + "a" * 64
MODEL_DIGEST = "oci://registry.example.test/models/fraud@sha256:" + "b" * 64


@dataclass
class _Result:
    ok: bool = True
    errors: list[str] = field(default_factory=list)

    def summary(self) -> list[str]:
        return list(self.errors)


class _Cluster:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.applied: list[tuple[str, str, list[dict[str, Any]]]] = []
        self.deleted: list[tuple[str, str, list[dict[str, Any]]]] = []
        self.apply_result = _Result()
        self.delete_result = _Result()

    def get_manifest(self, cluster_id, namespace, kind, name):
        del cluster_id
        return self.objects.get((kind, namespace, name))

    def apply_manifests(self, cluster_id, namespace, manifests):
        self.applied.append((cluster_id, namespace, manifests))
        if self.apply_result.ok:
            for manifest in manifests:
                self.objects[
                    (
                        f"{manifest['apiVersion']}/{manifest['kind']}",
                        namespace,
                        manifest["metadata"]["name"],
                    )
                ] = manifest
        return self.apply_result

    def delete_manifests(self, cluster_id, namespace, manifests):
        self.deleted.append((cluster_id, namespace, manifests))
        if self.delete_result.ok:
            for manifest in manifests:
                self.objects.pop(
                    (
                        f"{manifest['apiVersion']}/{manifest['kind']}",
                        namespace,
                        manifest["metadata"]["name"],
                    ),
                    None,
                )
        return self.delete_result


def _inference_spec(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "predictor": {
            "minReplicas": 1,
            "maxReplicas": 4,
            "model": {
                "modelFormat": {"name": "sklearn", "version": "1"},
                "protocolVersion": "v2",
                "storageUri": "s3://model-bucket/fraud/v1",
                "resources": {
                    "requests": {"cpu": "1", "memory": "2Gi"},
                    "limits": {"cpu": "2", "memory": "4Gi", "nvidia.com/gpu": "1"},
                },
            },
            "nodeSelector": {"accelerator": "nvidia"},
            "tolerations": [{"key": "gpu", "operator": "Exists"}],
        },
    }
    value.update(overrides)
    return value


def _spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="steady-md",
        app_id="app-1",
        app_slug="triage",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="fraud-model",
        size="custom",
        config={"inference_spec": _inference_spec(), **config},
        managed_service_id="service-1",
    )


def _driver(cluster: _Cluster | None = None, **policy: Any) -> tuple[KServeDriver, _Cluster]:
    live = cluster or _Cluster()
    return KServeDriver(config=KServeConfig(cluster_driver=live, **policy)), live


def _resource(cluster: _Cluster, name: str = "triage-prod-fraud-model") -> dict[str, Any]:
    return cluster.objects[(f"{API_VERSION}/InferenceService", "steady-md-triage", name)]


def test_provision_preserves_native_spec_and_applies_safe_defaults() -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec(labels={"team": "ml"}, deletion_protection=True))

    assert result.ok is True
    assert result.ready is False
    assert result.handle == "model_endpoint/cluster-1/steady-md-triage/triage-prod-fraud-model"
    account, service = cluster.applied[-1][2]
    assert account["kind"] == "ServiceAccount"
    assert account["metadata"]["name"] == "kserve-model"
    assert account["automountServiceAccountToken"] is False
    assert service["apiVersion"] == API_VERSION
    assert service["metadata"]["labels"] == {
        "team": "ml",
        "app.kubernetes.io/managed-by": "astrolift",
        "astrolift.io/managed-service-id": "service-1",
        "astrolift.io/organization": "steady-md",
        "astrolift.io/app": "triage",
        "astrolift.io/environment": "prod",
        "app.kubernetes.io/name": "triage-prod-fraud-model",
        "networking.kserve.io/visibility": "cluster-local",
        "networking.knative.dev/visibility": "cluster-local",
    }
    assert service["metadata"]["annotations"] == {
        "serving.kserve.io/deploymentMode": "Standard",
        "storage.kserve.io/readonly": "true",
        "serving.kserve.io/autoscalerClass": "hpa",
        "serving.kserve.io/enable-prometheus-scraping": "true",
        "serving.kserve.io/disable-localmodel": "true",
        "astrolift.io/deletion-protection": "true",
    }
    predictor = service["spec"]["predictor"]
    assert predictor["serviceAccountName"] == "kserve-model"
    assert predictor["automountServiceAccountToken"] is False
    assert predictor["model"]["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert predictor["nodeSelector"] == {"accelerator": "nvidia"}
    assert predictor["tolerations"] == [{"key": "gpu", "operator": "Exists"}]


def test_native_canary_multi_storage_and_component_surface_is_preserved() -> None:
    driver, cluster = _driver(
        allow_custom_containers=True,
        allowed_image_prefixes=("registry.example.test/models",),
    )
    native = _inference_spec(
        canary=[
            {
                "trafficPercent": 15,
                "predictor": {
                    "maxReplicas": 2,
                    "model": {
                        "name": "canary",
                        "modelFormat": {"name": "xgboost"},
                        "storageUri": "s3://models/canary",
                    },
                },
            },
        ],
        predictor={
            "minReplicas": 0,
            "maxReplicas": 8,
            "model": {"modelFormat": {"name": "xgboost"}},
            "storageUris": [
                {"uri": "s3://models/weights", "mountPath": "/mnt/models/weights"},
                {"uri": MODEL_DIGEST, "mountPath": "/mnt/models/tokenizer"},
            ],
            "affinity": {"nodeAffinity": {}},
        },
        transformer={
            "containers": [
                {
                    "name": "transformer",
                    "image": IMAGE,
                    "env": [{"name": "MODE", "value": "normalize"}],
                },
            ],
        },
    )

    assert driver.provision(_spec(inference_spec=native)).ok is True
    rendered = _resource(cluster)["spec"]
    assert rendered["predictor"]["storageUris"] == native["predictor"]["storageUris"]
    assert rendered["predictor"]["affinity"] == {"nodeAffinity": {}}
    assert rendered["transformer"]["containers"] == native["transformer"]["containers"]
    assert rendered["canary"][0]["trafficPercent"] == 15
    assert rendered["canary"][0]["predictor"]["model"] == native["canary"][0]["predictor"]["model"]
    for component in (
        rendered["predictor"],
        rendered["transformer"],
        rendered["canary"][0]["predictor"],
    ):
        assert component["serviceAccountName"] == "kserve-model"
        assert component["automountServiceAccountToken"] is False


@pytest.mark.parametrize(
    ("config", "policy", "message"),
    [
        ({"public": True}, {}, "public KServe endpoints"),
        ({"storage_read_only": False}, {}, "writable KServe model storage"),
        ({"deployment_mode": "Knative"}, {}, "deployment mode 'Knative'"),
        ({"autoscaler_class": "keda"}, {}, "autoscaler class 'keda'"),
        ({"use_local_model_cache": True}, {}, "local model cache"),
        ({"public": "false"}, {}, "public must be a boolean"),
        (
            {"inference_spec": _inference_spec(predictor={"model": {"runtime": "custom"}})},
            {},
            "ServingRuntime 'custom'",
        ),
        (
            {"inference_spec": _inference_spec(predictor={"model": {"modelFormat": {"name": "onnx"}}})},
            {"allowed_model_formats": ("sklearn",)},
            "model format 'onnx'",
        ),
        (
            {"inference_spec": _inference_spec(predictor={"xgboost": {"storageUri": "s3://models/xgb"}})},
            {"allowed_model_formats": ("sklearn",)},
            "model format 'xgboost'",
        ),
        (
            {"inference_spec": _inference_spec(predictor={"maxReplicas": 101, "model": {}})},
            {},
            "maximum 100",
        ),
        (
            {"inference_spec": _inference_spec(canary="legacy")},
            {},
            "canary must be an array",
        ),
        (
            {"inference_spec": _inference_spec(canaryTrafficPercent=10)},
            {},
            "canary revision entries",
        ),
        (
            {
                "inference_spec": _inference_spec(
                    canary=[
                        {
                            "trafficPercent": 60,
                            "predictor": {
                                "model": {"name": "a", "modelFormat": {"name": "sklearn"}},
                            },
                        },
                        {
                            "trafficPercent": 50,
                            "predictor": {
                                "model": {"name": "b", "modelFormat": {"name": "sklearn"}},
                            },
                        },
                    ],
                ),
            },
            {},
            "cannot total more than 100",
        ),
        (
            {
                "inference_spec": _inference_spec(
                    canary=[
                        {
                            "trafficPercent": 10,
                            "predictor": {
                                "model": {"name": "canary", "modelFormat": {"name": "onnx"}},
                            },
                        },
                    ],
                ),
            },
            {"allowed_model_formats": ("sklearn",)},
            "model format 'onnx'",
        ),
    ],
)
def test_operator_policy_gates_are_fail_closed(
    config: dict[str, Any],
    policy: dict[str, Any],
    message: str,
) -> None:
    driver, _ = _driver(**policy)

    result = driver.provision(_spec(**config))

    assert result.ok is False
    assert result.errors == ["invalid_kserve_config"]
    assert message in result.message


def test_explicit_policy_enables_public_knative_keda_cache_and_writable_storage() -> None:
    driver, cluster = _driver(
        allowed_deployment_modes=("Standard", "Knative"),
        allow_public=True,
        allow_writable_storage=True,
        allowed_autoscaler_classes=("hpa", "keda"),
        allow_local_model_cache=True,
    )

    result = driver.provision(
        _spec(
            deployment_mode="Knative",
            public=True,
            storage_read_only=False,
            autoscaler_class="keda",
            use_local_model_cache=True,
        ),
    )

    assert result.ok is True
    metadata = _resource(cluster)["metadata"]
    assert "networking.kserve.io/visibility" not in metadata["labels"]
    assert "networking.knative.dev/visibility" not in metadata["labels"]
    assert metadata["annotations"]["serving.kserve.io/deploymentMode"] == "Knative"
    assert metadata["annotations"]["storage.kserve.io/readonly"] == "false"
    assert metadata["annotations"]["serving.kserve.io/autoscalerClass"] == "keda"
    assert metadata["annotations"]["serving.kserve.io/disable-localmodel"] == "false"


@pytest.mark.parametrize(
    "config",
    [
        {"labels": {"networking.kserve.io/visibility": "public"}},
        {"labels": {"networking.knative.dev/visibility": "public"}},
        {"annotations": {"internal.serving.kserve.io/predictor-host": "attacker.invalid"}},
    ],
)
def test_visibility_and_kserve_internal_metadata_cannot_bypass_policy(
    config: dict[str, Any],
) -> None:
    driver, _ = _driver()

    result = driver.provision(_spec(**config))

    assert result.ok is False
    assert "reserved" in result.message


def test_metadata_key_name_segment_obeys_kubernetes_limit() -> None:
    driver, _ = _driver()

    result = driver.provision(_spec(labels={"example.test/" + "a" * 64: "bad"}))

    assert result.ok is False
    assert "invalid Kubernetes metadata key" in result.message


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda spec: spec["predictor"].update({"hostNetwork": True}), "hostNetwork"),
        (lambda spec: spec["predictor"].update({"hostPID": True}), "hostPID"),
        (
            lambda spec: spec["predictor"].update(
                {"securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": True}},
            ),
            "disabled by cluster policy",
        ),
        (
            lambda spec: spec["predictor"].update(
                {"volumes": [{"name": "host", "hostPath": {"path": "/"}}]},
            ),
            "hostPath",
        ),
        (
            lambda spec: spec["predictor"].update(
                {"securityContext": {"appArmorProfile": {"type": "Unconfined"}}},
            ),
            "appArmorProfile",
        ),
        (
            lambda spec: spec["predictor"].update(
                {
                    "volumes": [
                        {
                            "name": "token",
                            "projected": {
                                "sources": [{"serviceAccountToken": {"path": "token"}}],
                            },
                        },
                    ],
                },
            ),
            "service-account token",
        ),
    ],
)
def test_pod_security_escape_hatches_are_rejected(mutator, message: str) -> None:
    driver, _ = _driver()
    native = _inference_spec()
    mutator(native)

    result = driver.provision(_spec(inference_spec=native))

    assert result.ok is False
    assert message in result.message


def test_sensitive_literal_env_is_rejected_but_secret_reference_is_allowed() -> None:
    denied, _ = _driver(allow_custom_containers=True, allowed_image_prefixes=("registry.example.test/models",))
    literal = _inference_spec(
        predictor={
            "containers": [
                {"name": "model", "image": IMAGE, "env": [{"name": "API_TOKEN", "value": "oops"}]},
            ],
        },
    )
    secret_ref = _inference_spec(
        predictor={
            "containers": [
                {
                    "name": "model",
                    "image": IMAGE,
                    "env": [
                        {
                            "name": "API_TOKEN",
                            "valueFrom": {"secretKeyRef": {"name": "model-auth", "key": "token"}},
                        },
                    ],
                },
            ],
        },
    )

    rejected = denied.provision(_spec(inference_spec=literal))
    accepted = denied.provision(_spec(inference_spec=secret_ref))

    assert rejected.ok is False
    assert "must reference sensitive environment values from a Secret" in rejected.message
    assert accepted.ok is True


def test_image_allowlist_uses_repository_boundary_and_requires_digest() -> None:
    driver, _ = _driver(
        allow_custom_containers=True,
        allowed_image_prefixes=("registry.example.test/team",),
    )

    wrong_repo = driver.provision(
        _spec(
            inference_spec=_inference_spec(
                predictor={"containers": [{"name": "model", "image": IMAGE.replace("models", "team-evil")}]},
            ),
        ),
    )
    tagged = driver.provision(
        _spec(
            inference_spec=_inference_spec(
                predictor={"containers": [{"name": "model", "image": "registry.example.test/team/model:v1"}]},
            ),
        ),
    )

    assert wrong_repo.ok is False
    assert "outside the cluster image allowlist" in wrong_repo.message
    assert tagged.ok is False
    assert "immutable sha256 digest" in tagged.message


def test_storage_and_logger_urls_require_explicit_host_allowlists() -> None:
    denied, _ = _driver()
    allowed, _ = _driver(
        allow_external_storage_urls=True,
        allowed_external_storage_hosts=("models.example.test",),
        allow_external_logger_urls=True,
        allowed_external_logger_hosts=("logs.example.test",),
    )
    native = _inference_spec(
        predictor={
            "model": {
                "modelFormat": {"name": "sklearn"},
                "storageUri": "https://cdn.models.example.test/fraud/v1",
            },
            "logger": {"url": "https://ingest.logs.example.test/v1"},
        },
    )

    assert denied.provision(_spec(inference_spec=native)).ok is False
    assert allowed.provision(_spec(inference_spec=native)).ok is True
    spoofed = _inference_spec(
        predictor={
            "model": {
                "modelFormat": {"name": "sklearn"},
                "storageUri": "https://models.example.test.attacker.invalid/fraud",
            },
        },
    )
    result = allowed.provision(_spec(inference_spec=spoofed))
    assert result.ok is False
    assert "outside the cluster allowlist" in result.message


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("s3://user:pass@models/path", "cannot embed credentials"),
        ("s3://models/path?token=secret", "query strings"),
        ("file:///etc/passwd", "scheme 'file'"),
        ("oci://registry.example.test/models/fraud:latest", "immutable sha256 digest"),
        ("oci+native://registry.example.test/models/fraud:latest", "immutable sha256 digest"),
        ("pvc:///models", "requires a claim name"),
    ],
)
def test_storage_uri_validation_rejects_credential_and_mutability_hazards(
    uri: str,
    message: str,
) -> None:
    driver, _ = _driver()
    native = _inference_spec(
        predictor={"model": {"modelFormat": {"name": "sklearn"}, "storageUri": uri}},
    )

    result = driver.provision(_spec(inference_spec=native))

    assert result.ok is False
    assert message in result.message


def test_multiple_storage_mounts_must_share_a_non_root_directory() -> None:
    driver, _ = _driver()
    native = _inference_spec(
        predictor={
            "model": {
                "modelFormat": {"name": "sklearn"},
                "storageUris": [
                    {"uri": "s3://models/a", "mountPath": "/mnt/models/a"},
                    {"uri": "s3://models/b", "mountPath": "/other/b"},
                ],
            },
        },
    )

    result = driver.provision(_spec(inference_spec=native))

    assert result.ok is False
    assert "common non-root directory" in result.message


def test_custom_service_account_requires_policy_allowlist_and_existing_account() -> None:
    cluster = _Cluster()
    native = _inference_spec()
    native["predictor"]["serviceAccountName"] = "gpu-runner"
    denied, _ = _driver(cluster)
    allowed, _ = _driver(
        cluster,
        allow_service_account_override=True,
        allowed_service_accounts=("gpu-runner",),
    )

    assert denied.provision(_spec(inference_spec=native)).ok is False
    missing = allowed.provision(_spec(inference_spec=native))
    assert missing.ok is False
    assert "does not exist" in missing.message
    cluster.objects[("v1/ServiceAccount", "steady-md-triage", "gpu-runner")] = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": "gpu-runner", "namespace": "steady-md-triage"},
    }
    assert allowed.provision(_spec(inference_spec=native)).ok is True


def test_explicit_component_account_does_not_leave_siblings_on_default_account() -> None:
    cluster = _Cluster()
    cluster.objects[("v1/ServiceAccount", "steady-md-triage", "gpu-runner")] = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": "gpu-runner", "namespace": "steady-md-triage"},
    }
    driver, _ = _driver(
        cluster,
        allow_custom_containers=True,
        allowed_image_prefixes=("registry.example.test/models",),
        allow_service_account_override=True,
        allowed_service_accounts=("gpu-runner",),
    )
    native = _inference_spec(
        transformer={
            "serviceAccountName": "gpu-runner",
            "containers": [{"name": "transformer", "image": IMAGE}],
        },
    )

    assert driver.provision(_spec(inference_spec=native)).ok is True
    rendered = _resource(cluster)["spec"]
    assert rendered["predictor"]["serviceAccountName"] == "kserve-model"
    assert rendered["transformer"]["serviceAccountName"] == "gpu-runner"
    assert ("v1/ServiceAccount", "steady-md-triage", "kserve-model") in cluster.objects


def test_baseline_service_account_cannot_adopt_foreign_or_ambient_identity() -> None:
    cluster = _Cluster()
    cluster.objects[("v1/ServiceAccount", "steady-md-triage", "kserve-model")] = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": "kserve-model", "namespace": "steady-md-triage"},
    }
    driver, _ = _driver(cluster)

    foreign = driver.provision(_spec())

    assert foreign.ok is False
    assert "not owned by this Astrolift project" in foreign.message

    cluster.objects.clear()
    provisioned = driver.provision(_spec())
    account = cluster.objects[("v1/ServiceAccount", "steady-md-triage", "kserve-model")]
    account["metadata"]["annotations"] = {
        "eks.amazonaws.com/role-arn": "arn:aws:iam::123456789012:role/unapproved",
    }

    ambient = driver.status(ServiceHandle(provisioned.handle))

    assert ambient.state == "error"
    assert "ambient workload identity annotation" in ambient.message


def test_existing_service_is_refused_even_with_its_exact_uid_and_other_owner_is_never_adoptable() -> None:
    cluster = _Cluster()
    key = (f"{API_VERSION}/InferenceService", "steady-md-triage", "triage-prod-fraud-model")
    cluster.objects[key] = {
        "apiVersion": API_VERSION,
        "kind": "InferenceService",
        "metadata": {"name": "triage-prod-fraud-model", "uid": "uid-1"},
        "spec": _inference_spec(),
    }
    driver, _ = _driver(cluster)

    refused = driver.provision(_spec())
    assert refused.ok is False
    assert "operator-authorized" in refused.message
    # Knowing the uid proved only that the caller could see the object.
    # Adoption is operator-only (#2021); the flags are rejected, not ignored.
    flagged = driver.provision(_spec(adopt_existing=True, expected_existing_uid="uid-1"))
    assert flagged.ok is False
    assert "unsupported KServe config fields" in flagged.message
    assert "labels" not in cluster.objects[key]["metadata"]

    cluster.objects[key]["metadata"]["labels"] = {
        "app.kubernetes.io/managed-by": "astrolift",
        "astrolift.io/managed-service-id": "another-service",
    }
    result = driver.provision(_spec())
    assert result.ok is False
    assert "another Astrolift resource" in result.message
    assert cluster.applied == []


def test_update_preserves_owner_and_reconciles_native_spec() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    updated_spec = _inference_spec()
    updated_spec["predictor"]["maxReplicas"] = 7

    result = driver.update(UpdateSpec(handle=provisioned.handle, config={"inference_spec": updated_spec}))

    assert result.ok is True
    resource = _resource(cluster)
    assert resource["metadata"]["labels"]["astrolift.io/managed-service-id"] == "service-1"
    assert resource["spec"]["predictor"]["maxReplicas"] == 7


def test_status_and_binding_report_runtime_protocol_and_endpoints() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    resource = _resource(cluster)
    resource["status"] = {
        "url": "http://triage-prod-fraud-model.steady-md-triage.svc",
        "grpcUrl": "grpc://triage-prod-fraud-model.steady-md-triage.svc:8081",
        "servingRuntimeName": "sklearn-runtime",
        "conditions": [{"type": "Ready", "status": "True"}],
        "modelStatus": {
            "transitionStatus": "UpToDate",
            "lastFailureInfo": {"message": "an older revision once failed"},
        },
    }

    status = driver.status(ServiceHandle(provisioned.handle))
    binding = driver.binding(ServiceHandle(provisioned.handle))

    assert status.state == "available"
    assert "sklearn-runtime" in status.message
    literals = {key: value.literal for key, value in binding.env_vars.items()}
    assert literals == {
        "MODEL_ENDPOINT_URL": "http://triage-prod-fraud-model.steady-md-triage.svc",
        "MODEL_DEPLOYMENT_NAME": "triage-prod-fraud-model",
        "MODEL_REGION": "kubernetes",
        "MODEL_API_STYLE": "kserve",
        "MODEL_AUTH_MODE": "none",
        "KSERVE_INFERENCE_SERVICE": "triage-prod-fraud-model",
        "KSERVE_NAMESPACE": "steady-md-triage",
        "MODEL_ENDPOINT_GRPC_URL": "grpc://triage-prod-fraud-model.steady-md-triage.svc:8081",
        "MODEL_PROTOCOL_VERSION": "v2",
        "MODEL_SERVING_RUNTIME": "sklearn-runtime",
        "MODEL_FORMAT": "sklearn",
    }


def test_binding_refuses_stale_url_until_ready() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    _resource(cluster)["status"] = {
        "url": "http://stale.example.test",
        "conditions": [{"type": "Ready", "status": "False"}],
    }

    with pytest.raises(ValueError, match="not ready"):
        driver.binding(ServiceHandle(provisioned.handle))


def test_status_fails_closed_for_model_failure_and_missing_service_account() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    resource = _resource(cluster)
    resource["status"] = {
        "modelStatus": {"lastFailureInfo": {"message": "model checksum mismatch"}},
        "conditions": [{"type": "Ready", "status": "False"}],
    }

    assert driver.status(ServiceHandle(provisioned.handle)).message == "model checksum mismatch"
    cluster.objects.pop(("v1/ServiceAccount", "steady-md-triage", "kserve-model"))
    missing = driver.status(ServiceHandle(provisioned.handle))
    assert missing.state == "error"
    assert "ServiceAccount" in missing.message


def test_deprovision_honors_live_deletion_protection_and_is_idempotent() -> None:
    driver, _ = _driver()
    provisioned = driver.provision(_spec(deletion_protection=True))

    refused = driver.deprovision(DeprovisionSpec(provisioned.handle))
    deleted = driver.deprovision(DeprovisionSpec(provisioned.handle), force_destroy=True)
    repeated = driver.deprovision(DeprovisionSpec(provisioned.handle), force_destroy=True)

    assert refused.ok is False
    assert refused.retryable is False
    assert refused.errors == ["deletion_protection_enabled"]
    assert deleted.ok is True
    assert "model artifacts were retained" in deleted.message
    assert repeated.ok is True


def test_foreign_and_legacy_handles_fail_closed() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    _resource(cluster)["metadata"]["labels"].pop("astrolift.io/managed-service-id")

    status = driver.status(ServiceHandle(provisioned.handle))
    wrong_kind = driver.update(
        UpdateSpec(handle=provisioned.handle.replace("model_endpoint/", "queue/"), config={}),
    )
    legacy = driver.status(ServiceHandle("legacy-model"))

    assert status.state == "error"
    assert "not owned" in status.message
    assert wrong_kind.ok is False
    assert "handle kind" in wrong_kind.message
    assert legacy.state == "error"


def test_snapshot_contract_is_explicitly_unsupported() -> None:
    driver, _ = _driver()

    with pytest.raises(UnsupportedOperationError, match="external immutable storage"):
        driver.snapshot(ServiceHandle("model_endpoint/cluster/ns/name"))


def test_plugin_catalogue_and_contract_are_executable_preview() -> None:
    driver_type = PLUGIN.managed_service_drivers[("model_endpoint", "kserve")]
    assert driver_type is KServeDriver
    driver, _ = _driver()
    assert driver.config_schema()["required"] == ["inference_spec"]
    assert "MODEL_ENDPOINT_URL" in driver.binding_schema().env_vars
    entry = next(
        row
        for row in MATRIX.managed_services
        if (row.plugin_id, row.kind, row.variant) == ("k8s_native", "model_endpoint", "kserve")
    )
    assert entry.status == "preview"
    assert "MODEL_ENDPOINT_GRPC_URL" in entry.binding_envs


def test_preflight_requires_kserve_020_and_kubernetes_132() -> None:
    required_crds = frozenset(
        {
            "inferenceservices.serving.kserve.io",
            "servingruntimes.serving.kserve.io",
            "clusterservingruntimes.serving.kserve.io",
            "clusterstoragecontainers.serving.kserve.io",
        },
    )
    missing = preflight(
        kind="model_endpoint",
        variant="kserve",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.31.9",
            crd_inventory_probed=True,
        ),
    )
    stale = preflight(
        kind="model_endpoint",
        variant="kserve",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32.0",
            installed_crds=required_crds,
            crd_inventory_probed=True,
            operator_versions={"kserve": "v0.19.0"},
        ),
    )
    installed = preflight(
        kind="model_endpoint",
        variant="kserve",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32.2",
            installed_crds=required_crds,
            crd_inventory_probed=True,
            operator_versions={"kserve": "v0.20.0"},
        ),
    )

    assert {failure.code for failure in missing.failures} == {"missing_crd", "k8s_too_old"}
    assert stale.ok is False
    assert stale.failures[0].code == "operator_too_old"
    assert installed.ok is True
