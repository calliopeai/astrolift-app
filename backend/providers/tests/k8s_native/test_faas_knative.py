from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from astrolift_drivers.registry import PluginManifest, plugins

from _sdk import UnsupportedOperationError
from _sdk.cluster_capabilities import ClusterCapabilities
from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from k8s_native.managed.faas_knative import (
    API_VERSION,
    KnativeServiceConfig,
    KnativeServiceDriver,
)
from k8s_native.plugin import PLUGIN
from k8s_native.preflight import preflight

IMAGE = "registry.example.test/functions/hello@sha256:" + "a" * 64
IMAGE_V2 = "registry.example.test/functions/hello@sha256:" + "b" * 64


@pytest.fixture
def _registered_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    drivers = dict(PLUGIN.drivers)
    drivers.update(
        {f"managed:{kind}:{variant}": driver for (kind, variant), driver in PLUGIN.managed_service_drivers.items()}
    )
    monkeypatch.setattr(
        plugins,
        "_plugins",
        {
            "k8s_native": PluginManifest(
                plugin_id="k8s_native",
                display_name=PLUGIN.display_name,
                version="test",
                drivers=drivers,
            )
        },
    )


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
                key = (
                    f"{manifest['apiVersion']}/{manifest['kind']}",
                    namespace,
                    manifest["metadata"]["name"],
                )
                self.objects[key] = manifest
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


def _spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="steady-md",
        app_id="app-1",
        app_slug="triage",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="intake-function",
        size="small",
        config={"image": IMAGE, **config},
        managed_service_id="service-1",
    )


def _driver(cluster: _Cluster | None = None, **policy: Any) -> tuple[KnativeServiceDriver, _Cluster]:
    live = cluster or _Cluster()
    return KnativeServiceDriver(config=KnativeServiceConfig(cluster_driver=live, **policy)), live


def _manifest(cluster: _Cluster) -> dict[str, Any]:
    return cluster.applied[-1][2][0]


def test_provision_renders_owned_private_scale_to_zero_service() -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec())

    assert result.ok is True
    assert result.ready is False
    assert result.handle == "faas/cluster-1/steady-md-triage/triage-prod-intake-function"
    manifest = _manifest(cluster)
    assert manifest["apiVersion"] == API_VERSION
    assert manifest["kind"] == "Service"
    assert manifest["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "astrolift"
    assert manifest["metadata"]["labels"]["astrolift.io/managed-service-id"] == "service-1"
    assert manifest["metadata"]["labels"]["networking.knative.dev/visibility"] == "cluster-local"
    template = manifest["spec"]["template"]
    assert template["metadata"]["annotations"]["autoscaling.knative.dev/min-scale"] == "0"
    assert template["metadata"]["annotations"]["autoscaling.knative.dev/max-scale"] == "10"
    assert template["spec"]["containers"][0]["image"] == IMAGE
    assert manifest["spec"]["traffic"] == [{"latestRevision": True, "percent": 100}]


def test_public_route_requires_install_policy() -> None:
    denied, _ = _driver()
    allowed, cluster = _driver(allow_public=True)

    assert denied.provision(_spec(public=True)).errors == ["invalid_knative_config"]
    assert allowed.provision(_spec(public=True)).ok is True
    assert "networking.knative.dev/visibility" not in _manifest(cluster)["metadata"]["labels"]


def test_tagged_image_requires_install_policy() -> None:
    denied, _ = _driver()
    allowed, _ = _driver(allow_tagged_images=True)

    assert denied.provision(_spec(image="registry.example.test/function:latest")).ok is False
    assert allowed.provision(_spec(image="registry.example.test/function:v1")).ok is True


def test_render_exposes_runtime_scaling_secret_and_scheduling_controls() -> None:
    driver, cluster = _driver()

    result = driver.provision(
        _spec(
            env={"MODE": "triage"},
            secret_env={"TOKEN": {"secret_name": "agent-token", "key": "value"}},
            min_scale=2,
            max_scale=20,
            target_concurrency=25,
            container_concurrency=50,
            timeout_seconds=600,
            service_account_name="triage-function",
            node_selector={"workload": "functions"},
            tolerations=[{"key": "functions", "operator": "Exists"}],
            image_pull_secrets=["registry-auth"],
            traffic=[
                {"revisionName": "triage-00001", "percent": 10, "tag": "stable"},
                {"latestRevision": True, "percent": 90, "tag": "candidate"},
            ],
        ),
    )

    assert result.ok is True
    manifest = _manifest(cluster)
    template = manifest["spec"]["template"]
    assert template["metadata"]["annotations"]["autoscaling.knative.dev/target"] == "25"
    pod = template["spec"]
    assert pod["containerConcurrency"] == 50
    assert pod["timeoutSeconds"] == 600
    assert pod["serviceAccountName"] == "triage-function"
    assert pod["nodeSelector"] == {"workload": "functions"}
    assert pod["imagePullSecrets"] == [{"name": "registry-auth"}]
    env = {row["name"]: row for row in pod["containers"][0]["env"]}
    assert env["MODE"]["value"] == "triage"
    assert env["TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "agent-token"
    assert manifest["spec"]["traffic"][1]["percent"] == 90


@pytest.mark.parametrize(
    "config",
    [
        {"min_scale": 4, "max_scale": 3},
        {"traffic": [{"latestRevision": True, "percent": 99}]},
        {"traffic": [{"latestRevision": True, "percent": -1}, {"revisionName": "v1", "percent": 101}]},
        {"traffic": [{"latestRevision": True, "percent": 100, "unknown": True}]},
        {"traffic": [{"latestRevision": True, "revisionName": "both", "percent": 100}]},
        {"target_concurrency": 0},
        {"env": {"BAD-NAME": "x"}},
        {"env": {"PORT": "9999"}},
        {"secret_env": {"TOKEN": {"secret_name": "missing-key"}}},
        {"labels": {"app.kubernetes.io/managed-by": "someone-else"}},
        {"revision_annotations": {"autoscaling.knative.dev/max-scale": "9999"}},
        {"service_annotations": {"BAD KEY": "value"}},
        {"revision_annotations": {"example.test/value": 1}},
        {"security_context": {"privileged": True}},
        {"volumes": [{"name": "host", "hostPath": {"path": "/"}}]},
    ],
)
def test_invalid_runtime_config_fails_before_cluster_mutation(config: dict[str, Any]) -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec(**config))

    assert result.ok is False
    assert result.errors == ["invalid_knative_config"]
    assert cluster.applied == []


def test_existing_resource_requires_matching_owner_or_explicit_uid_adoption() -> None:
    driver, cluster = _driver()
    key = (
        f"{API_VERSION}/Service",
        "steady-md-triage",
        "triage-prod-intake-function",
    )
    cluster.objects[key] = {
        "metadata": {"uid": "uid-1", "labels": {"app.kubernetes.io/managed-by": "someone-else"}},
    }

    refused = driver.provision(_spec())
    wrong_uid = driver.provision(_spec(adopt_existing=True, expected_existing_uid="wrong"))
    adopted = driver.provision(_spec(adopt_existing=True, expected_existing_uid="uid-1"))

    assert refused.ok is False
    assert wrong_uid.ok is False
    assert adopted.ok is True
    assert _manifest(cluster)["metadata"]["labels"]["astrolift.io/managed-service-id"] == "service-1"


def test_reprovision_reconciles_resource_owned_by_same_managed_service() -> None:
    driver, cluster = _driver()
    assert driver.provision(_spec()).ok is True

    second = driver.provision(_spec(env={"MODE": "updated"}))

    assert second.ok is True
    assert len(cluster.applied) == 2


def test_reprovision_never_adopts_another_astrolift_managed_resource() -> None:
    driver, _ = _driver()
    assert driver.provision(_spec()).ok is True

    result = driver.provision(
        replace(
            _spec(adopt_existing=True, expected_existing_uid=""),
            managed_service_id="service-2",
        ),
    )

    assert result.ok is False
    assert "another Astrolift managed resource" in result.message


def test_update_requires_existing_owned_resource_and_reconciles_full_desired_config() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())

    result = driver.update(UpdateSpec(handle=provisioned.handle, size="medium", config={"image": IMAGE_V2}))

    assert result.ok is True
    manifest = _manifest(cluster)
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == IMAGE_V2
    assert container["resources"]["limits"]["memory"] == "1Gi"


def test_update_missing_resource_is_permanent_failure() -> None:
    driver, _ = _driver()

    result = driver.update(
        UpdateSpec(
            handle="faas/cluster-1/ns/function",
            size="small",
            config={"image": IMAGE},
        ),
    )

    assert result.ok is False
    assert result.retryable is False
    assert result.errors == ["resource_not_found"]


def test_update_requires_nonempty_astrolift_resource_identity() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    current = cluster.objects[(f"{API_VERSION}/Service", "steady-md-triage", "triage-prod-intake-function")]
    current["metadata"]["labels"].pop("astrolift.io/managed-service-id")

    result = driver.update(UpdateSpec(handle=provisioned.handle, size="small", config={"image": IMAGE_V2}))

    assert result.ok is False
    assert result.retryable is False
    assert result.errors == ["invalid_knative_update"]


def test_deprovision_honors_protection_and_ownership() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    protected = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, config={"deletion_protection": True}),
    )
    assert protected.ok is False
    assert protected.retryable is False

    current = cluster.objects[(f"{API_VERSION}/Service", "steady-md-triage", "triage-prod-intake-function")]
    current["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "foreign"
    foreign = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    forced_foreign = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert foreign.errors == ["ownership_mismatch"]
    assert forced_foreign.errors == ["ownership_mismatch"]
    current["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "astrolift"
    forced_owned = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert forced_owned.ok is True


def test_unsafe_pod_fields_require_install_level_policy() -> None:
    denied, denied_cluster = _driver()
    allowed, allowed_cluster = _driver(allow_unsafe_pod_spec=True)
    unsafe = {
        "security_context": {"privileged": True},
        "volumes": [{"name": "host", "hostPath": {"path": "/data"}}],
        "volume_mounts": [{"name": "host", "mountPath": "/data"}],
    }

    assert denied.provision(_spec(**unsafe)).ok is False
    assert denied_cluster.applied == []
    assert allowed.provision(_spec(**unsafe)).ok is True
    container = _manifest(allowed_cluster)["spec"]["template"]["spec"]["containers"][0]
    assert container["securityContext"]["privileged"] is True


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"type": "Ready", "status": "True"}, "available"),
        ({"type": "Ready", "status": "Unknown", "reason": "Deploying"}, "provisioning"),
        ({"type": "Ready", "status": "False", "reason": "RevisionFailed"}, "error"),
    ],
)
def test_status_maps_knative_ready_condition(condition: dict[str, str], expected: str) -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    service = _manifest(cluster)
    service["status"] = {
        "conditions": [condition],
        "url": "https://function.example.test",
    }

    assert driver.status(ServiceHandle(provisioned.handle)).state == expected


def test_status_does_not_report_available_until_route_url_exists() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    _manifest(cluster)["status"] = {
        "conditions": [{"type": "Ready", "status": "True"}],
    }

    assert driver.status(ServiceHandle(provisioned.handle)).state == "provisioning"


def test_binding_exposes_knative_route_and_portable_locator() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    _manifest(cluster)["status"] = {"url": "https://function.example.test"}

    binding = driver.binding(ServiceHandle(provisioned.handle))

    assert binding.env_vars["FUNCTION_NAME"].literal == "triage-prod-intake-function"
    assert binding.env_vars["FUNCTION_URL"].literal == "https://function.example.test"
    assert binding.env_vars["FUNCTION_ARN"].literal == ("k8s://cluster-1/steady-md-triage/triage-prod-intake-function")


def test_binding_fails_when_service_or_ready_route_is_missing() -> None:
    driver, _ = _driver()
    handle = "faas/cluster-1/steady-md-triage/triage-prod-intake-function"
    with pytest.raises(ValueError, match="does not exist"):
        driver.binding(ServiceHandle(handle))

    provisioned = driver.provision(_spec())
    with pytest.raises(ValueError, match="no ready route URL"):
        driver.binding(ServiceHandle(provisioned.handle))


def test_snapshot_and_restore_are_explicitly_unsupported() -> None:
    driver, _ = _driver()
    with pytest.raises(UnsupportedOperationError):
        driver.snapshot(ServiceHandle("faas/cluster/ns/name"))
    with pytest.raises(UnsupportedOperationError):
        driver.restore(object(), _spec())


def test_plugin_catalogue_and_preflight_are_executable_and_fail_closed() -> None:
    assert PLUGIN.managed_service_drivers[("faas", "knative_service")] is KnativeServiceDriver
    missing = preflight(
        kind="faas",
        variant="knative_service",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32",
            installed_crds=frozenset(),
            crd_inventory_probed=True,
        ),
    )
    installed = preflight(
        kind="faas",
        variant="knative_service",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32",
            installed_crds=frozenset({"services.serving.knative.dev"}),
            crd_inventory_probed=True,
        ),
    )
    assert missing.ok is False
    assert missing.failures[0].code == "missing_crd"
    assert "knative-operator" in missing.install_hints[0]
    assert installed.ok is True


def test_catalogue_surfaces_full_schema_and_binding_contract(_registered_plugin) -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    row = next(item for item in list_catalog("k8s_native") if (item.kind, item.variant) == ("faas", "knative_service"))

    assert row.available is True
    assert row.status == "preview"
    assert row.is_default_for_kind is True
    assert row.size_options == ("small", "medium", "large", "xlarge", "custom")
    assert row.config_schema["properties"]["size"]["default"] == "small"
    assert row.config_schema["required"] == ["image"]
    assert {"FUNCTION_NAME", "FUNCTION_URL", "FUNCTION_ARN", "FUNCTION_REGION"} <= set(
        row.binding_envs,
    )
