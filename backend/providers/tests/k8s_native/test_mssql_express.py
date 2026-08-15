from __future__ import annotations

import base64
import copy

import pytest

from _sdk import UnsupportedOperationError
from _sdk.cluster import ApplyResult, DeleteResult, StorageClassInfo
from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from k8s_native.managed.mssql_express import (
    DEFAULT_IMAGE,
    SQLServerExpressConfig,
    SQLServerExpressDriver,
)
from k8s_native.plugin import PLUGIN


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}
        self.upserts: list[tuple[str, dict[str, str]]] = []
        self.deletes: list[str] = []

    def get(self, path: str):
        value = self.values.get(path)
        return copy.deepcopy(value) if value is not None else None

    def upsert(self, path: str, values: dict[str, str]) -> None:
        self.values[path] = dict(values)
        self.upserts.append((path, dict(values)))

    def delete(self, path: str) -> None:
        self.values.pop(path, None)
        self.deletes.append(path)


class _Cluster:
    def __init__(self, *, expandable: bool = True) -> None:
        self.objects: dict[tuple[str, str | None, str], dict] = {}
        self.applied: list[list[dict]] = []
        self.deleted: list[list[dict]] = []
        self.namespaces: list[tuple[str, str, dict, dict]] = []
        self.classes = [StorageClassInfo("fast-rwo", True, "csi.example.test")]
        self.objects[("storage.k8s.io/v1/StorageClass", None, "fast-rwo")] = {
            "apiVersion": "storage.k8s.io/v1",
            "kind": "StorageClass",
            "metadata": {"name": "fast-rwo"},
            "allowVolumeExpansion": expandable,
        }

    def ensure_namespace(self, cluster, namespace, labels, annotations):
        self.namespaces.append((cluster, namespace, copy.deepcopy(labels), copy.deepcopy(annotations)))

    def list_storage_classes(self, _cluster):
        return list(self.classes)

    def get_manifest(self, _cluster, namespace, kind, name):
        value = self.objects.get((kind, namespace, name))
        return copy.deepcopy(value) if value is not None else None

    def apply_manifests(self, _cluster, namespace, manifests):
        self.applied.append(copy.deepcopy(manifests))
        for raw in manifests:
            manifest = copy.deepcopy(raw)
            metadata = manifest["metadata"]
            resource_namespace = metadata.get("namespace", namespace)
            kind = f"{manifest['apiVersion']}/{manifest['kind']}"
            key = (kind, resource_namespace, metadata["name"])
            old_status = copy.deepcopy((self.objects.get(key) or {}).get("status"))
            if manifest["kind"] == "Secret" and "stringData" in manifest:
                manifest["data"] = {
                    key: base64.b64encode(str(value).encode()).decode()
                    for key, value in manifest.pop("stringData").items()
                }
            if manifest["kind"] == "VolumeSnapshot":
                manifest["status"] = {
                    "readyToUse": True,
                    "creationTime": "2026-08-14T00:00:00Z",
                }
            elif old_status is not None and "status" not in manifest:
                manifest["status"] = old_status
            self.objects[key] = manifest
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])

    def delete_manifests(self, _cluster, namespace, manifests):
        self.deleted.append(copy.deepcopy(manifests))
        for manifest in manifests:
            metadata = manifest["metadata"]
            resource_namespace = metadata.get("namespace", namespace)
            kind = f"{manifest['apiVersion']}/{manifest['kind']}"
            self.objects.pop((kind, resource_namespace, metadata["name"]), None)
        return DeleteResult(deleted=[], not_found=[], errors=[])

    def enable_snapshots(self) -> None:
        self.objects[
            (
                "apiextensions.k8s.io/v1/CustomResourceDefinition",
                None,
                "volumesnapshots.snapshot.storage.k8s.io",
            )
        ] = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "volumesnapshots.snapshot.storage.k8s.io"},
        }
        self.objects[("snapshot.storage.k8s.io/v1/VolumeSnapshotClass", None, "fast-snapshots")] = {
            "apiVersion": "snapshot.storage.k8s.io/v1",
            "kind": "VolumeSnapshotClass",
            "metadata": {"name": "fast-snapshots"},
        }


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "acme",
        "app_id": "project-1",
        "app_slug": "payments",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "ledger",
        "size": "small",
        "config": {},
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _driver(
    *,
    cluster: _Cluster | None = None,
    secrets: _Secrets | None = None,
    **config,
) -> tuple[SQLServerExpressDriver, _Cluster, _Secrets]:
    cluster = cluster or _Cluster()
    secrets = secrets or _Secrets()
    return (
        SQLServerExpressDriver(
            config=SQLServerExpressConfig(
                cluster_driver=cluster,
                secrets_backend=secrets,
                deletion_timeout_seconds=0,
                deletion_poll_seconds=0,
                **config,
            )
        ),
        cluster,
        secrets,
    )


def _object(cluster: _Cluster, kind: str, namespace: str, name: str) -> dict:
    return cluster.objects[(kind, namespace, name)]


def _container(cluster: _Cluster, name: str = "mssql-payments-prod-ledger") -> dict:
    statefulset = _object(cluster, "apps/v1/StatefulSet", "acme-payments", name)
    return statefulset["spec"]["template"]["spec"]["containers"][0]


def test_provision_emits_secure_durable_statefulset_and_external_credentials() -> None:
    driver, cluster, backend = _driver()

    result = driver.provision(_spec())

    assert result.ok is True
    assert result.ready is False
    assert result.handle == "mssql/cluster-1/acme-payments/mssql-payments-prod-ledger"
    assert cluster.namespaces[0][1] == "acme-payments"
    assert {row["kind"] for row in cluster.applied[-1]} == {
        "ConfigMap",
        "NetworkPolicy",
        "PersistentVolumeClaim",
        "Secret",
        "Service",
        "StatefulSet",
    }
    pod = _object(
        cluster,
        "apps/v1/StatefulSet",
        "acme-payments",
        "mssql-payments-prod-ledger",
    )["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["image"] == DEFAULT_IMAGE
    assert "@sha256:" in container["image"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsUser"] == 10001
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["nodeSelector"]["kubernetes.io/arch"] == "amd64"
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }
    env = {row["name"]: row for row in container["env"]}
    assert env["MSSQL_PID"]["value"] == "Express"
    assert "MSSQL_AGENT_ENABLED" not in env
    assert env["MSSQL_SA_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "sa_password"
    pvc = _object(
        cluster,
        "v1/PersistentVolumeClaim",
        "acme-payments",
        "mssql-payments-prod-ledger-data",
    )
    assert pvc["spec"]["resources"]["requests"]["storage"] == "20Gi"
    assert pvc["spec"]["storageClassName"] == "fast-rwo"
    path = "managed/mssql/cluster-1/acme-payments/mssql-payments-prod-ledger/credentials"
    assert set(backend.values[path]) == {
        "sa_password",
        "username",
        "password",
        "database",
        "trust_server_certificate",
        "database_url",
    }

    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["MSSQL_PASSWORD"].secret_ref == f"{path}#password"
    assert binding.env_vars["DATABASE_URL"].secret_ref == f"{path}#database_url"
    assert binding.env_vars["MSSQL_ENCRYPT"].literal == "true"
    assert binding.env_vars["MSSQL_TRUST_SERVER_CERTIFICATE"].literal == "true"
    assert backend.values[path]["password"] not in repr(binding)


def test_provision_is_idempotent_and_repairs_external_bundle_from_retained_secret() -> None:
    driver, cluster, backend = _driver()
    spec = _spec()
    first = driver.provision(spec)
    path = next(iter(backend.values))
    original = copy.deepcopy(backend.values[path])

    assert driver.deprovision(DeprovisionSpec(first.handle)).ok is True
    backend.values.clear()
    second = driver.provision(spec)

    assert second.ok is True
    assert backend.values[path] == original
    assert len(backend.upserts) == 2
    assert _object(
        cluster,
        "v1/PersistentVolumeClaim",
        "acme-payments",
        "mssql-payments-prod-ledger-data",
    )


def test_tls_certificate_disables_trust_server_certificate_in_every_binding() -> None:
    driver, _, backend = _driver()

    result = driver.provision(_spec(config={"tls_secret_name": "mssql-tls"}))

    assert result.ok is True
    path = next(iter(backend.values))
    assert backend.values[path]["trust_server_certificate"] == "false"
    assert "trustServerCertificate=false" in backend.values[path]["database_url"]
    assert driver.binding(ServiceHandle(result.handle)).env_vars["MSSQL_TRUST_SERVER_CERTIFICATE"].literal == "false"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"database": "not-a-valid-name"}, "database"),
        ({"database": "master"}, "system database"),
        ({"username": "sa"}, "sa login"),
        ({"cpu_limit": "5"}, "four CPU"),
        ({"memory_request": "500m"}, "memory_request"),
        ({"memory_limit_mb": 1411}, "Express limit"),
        ({"storage_size": "500m"}, "storage_size"),
        ({"extra_env": {"MSSQL_SA_PASSWORD": "plaintext"}}, "reserved"),
        ({"extra_env": {"NOT A NAME": "value"}}, "invalid"),
        ({"mssql_conf": {"network": {"forceencryption": 0}}}, "reserved"),
        ({"mssql_conf": {"filelocation": {"defaultdatadir": "/data\nforceencryption = 0"}}}, "control"),
        ({"image": "untrusted.example/mssql:latest"}, "custom"),
        ({"image": "mcr.microsoft.com/mssql/server:2025-latest"}, "custom"),
        ({"network_policy_mode": "disabled"}, "NetworkPolicy"),
        ({"service_type": "LoadBalancer", "load_balancer_source_ranges": ["10.0.0.0/8"]}, "requires"),
        ({"not_a_real_setting": True}, "unsupported"),
    ],
)
def test_invalid_or_unsafe_configuration_fails_before_mutation(config, message) -> None:
    driver, cluster, backend = _driver()

    result = driver.provision(_spec(config=config))

    assert result.ok is False
    assert message in result.message
    assert cluster.applied == []
    assert backend.upserts == []


def test_load_balancer_requires_install_opt_in_and_source_ranges() -> None:
    driver, cluster, _ = _driver(allow_load_balancer=True)

    missing = driver.provision(_spec(config={"service_type": "LoadBalancer"}))
    invalid = driver.provision(
        _spec(
            config={
                "service_type": "LoadBalancer",
                "load_balancer_source_ranges": ["not-a-cidr"],
            }
        )
    )
    allowed = driver.provision(
        _spec(
            config={
                "service_type": "LoadBalancer",
                "load_balancer_source_ranges": ["10.20.0.0/16"],
            }
        )
    )

    assert missing.ok is False
    assert invalid.ok is False
    assert "CIDR" in invalid.message
    assert allowed.ok is True
    service = _object(cluster, "v1/Service", "acme-payments", "mssql-payments-prod-ledger")
    assert service["spec"]["type"] == "LoadBalancer"
    assert service["spec"]["loadBalancerSourceRanges"] == ["10.20.0.0/16"]


def test_network_policy_can_only_be_disabled_by_install_policy() -> None:
    driver, cluster, _ = _driver(allow_network_policy_disable=True)

    result = driver.provision(_spec(config={"network_policy_mode": "disabled"}))

    assert result.ok is True
    assert "NetworkPolicy" not in {row["kind"] for row in cluster.applied[-1]}


def test_network_policy_allows_only_same_tenant_managed_and_agent_namespaces() -> None:
    driver, cluster, _ = _driver()

    result = driver.provision(_spec())

    assert result.ok is True
    policy = _object(
        cluster,
        "networking.k8s.io/v1/NetworkPolicy",
        "acme-payments",
        "mssql-payments-prod-ledger-ingress",
    )
    peers = policy["spec"]["ingress"][0]["from"]
    assert {"podSelector": {}} in peers
    assert {
        "namespaceSelector": {
            "matchLabels": {
                "astrolift.io/managed-by": "astrolift",
                "astrolift.io/organization": "acme",
            }
        }
    } in peers
    assert {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "astrolift-agents-acme"}}} in peers
    assert all(
        peer.get("namespaceSelector", {}).get("matchLabels", {}).get("astrolift.io/organization") != "other-tenant"
        for peer in peers
    )


def test_foreign_statefulset_is_never_adopted() -> None:
    driver, cluster, backend = _driver()
    cluster.objects[("apps/v1/StatefulSet", "acme-payments", "mssql-payments-prod-ledger")] = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {"name": "mssql-payments-prod-ledger", "namespace": "acme-payments"},
    }

    result = driver.provision(_spec())

    assert result.ok is False
    assert "refusing to adopt" in result.message
    assert backend.upserts == []


def test_status_reports_starting_ready_failure_and_retained_data() -> None:
    driver, cluster, _ = _driver()
    result = driver.provision(_spec())
    key = ("apps/v1/StatefulSet", "acme-payments", "mssql-payments-prod-ledger")

    assert driver.status(ServiceHandle(result.handle)).state == "provisioning"
    cluster.objects[key]["status"] = {"readyReplicas": 1, "currentReplicas": 1}
    assert driver.status(ServiceHandle(result.handle)).state == "available"
    cluster.objects[key]["status"] = {
        "conditions": [{"type": "ReplicaFailure", "status": "True", "message": "image rejected"}]
    }
    failed = driver.status(ServiceHandle(result.handle))
    assert failed.state == "error"
    assert failed.message == "image rejected"
    cluster.objects.pop(key)
    retained = driver.status(ServiceHandle(result.handle))
    assert retained.state == "deprovisioned"
    assert "PVC retained" in retained.message


def test_partial_update_preserves_current_size_and_applies_runtime_controls() -> None:
    driver, cluster, _ = _driver()
    result = driver.provision(
        _spec(
            config={
                "pod_annotations": {"before": "true"},
                "extra_env": {"CUSTOM_FLAG": "before"},
                "mssql_conf": {"filelocation": {"defaultdatadir": "/var/opt/mssql/data"}},
            }
        )
    )

    updated = driver.update(
        UpdateSpec(
            result.handle,
            config={
                "cpu_request": "750m",
                "storage_size": "30Gi",
                "node_selector": {"workload": "database"},
                "pod_annotations": {"after": "true"},
                "extra_env": {"CUSTOM_FLAG": "after"},
            },
        )
    )

    assert updated.ok is True
    container = _container(cluster)
    assert container["resources"]["requests"] == {"cpu": "750m", "memory": "2Gi"}
    assert {row["name"]: row.get("value") for row in container["env"]}["CUSTOM_FLAG"] == "after"
    statefulset = _object(
        cluster,
        "apps/v1/StatefulSet",
        "acme-payments",
        "mssql-payments-prod-ledger",
    )
    pod = statefulset["spec"]["template"]
    assert pod["metadata"]["annotations"] == {"after": "true"}
    assert pod["spec"]["nodeSelector"] == {
        "kubernetes.io/arch": "amd64",
        "workload": "database",
    }
    pvc = _object(
        cluster,
        "v1/PersistentVolumeClaim",
        "acme-payments",
        "mssql-payments-prod-ledger-data",
    )
    assert pvc["spec"]["resources"]["requests"]["storage"] == "30Gi"
    config_map = _object(
        cluster,
        "v1/ConfigMap",
        "acme-payments",
        "mssql-payments-prod-ledger-config",
    )
    assert "defaultdatadir = /var/opt/mssql/data" in config_map["data"]["mssql.conf"]


def test_update_rejects_immutable_fields_shrink_and_nonexpandable_storage() -> None:
    driver, _, _ = _driver()
    result = driver.provision(_spec())

    immutable = driver.update(UpdateSpec(result.handle, config={"database": "another"}))
    shrink = driver.update(UpdateSpec(result.handle, config={"storage_size": "10Gi"}))

    assert immutable.ok is False
    assert "immutable" in immutable.message
    assert shrink.ok is False
    assert "cannot shrink" in shrink.message

    blocked_driver, _, _ = _driver(cluster=_Cluster(expandable=False))
    blocked_result = blocked_driver.provision(_spec())
    blocked = blocked_driver.update(UpdateSpec(blocked_result.handle, config={"storage_size": "30Gi"}))
    assert blocked.ok is False
    assert "does not allow volume expansion" in blocked.message


def test_deprovision_retains_data_by_default_and_requires_force_for_delete() -> None:
    driver, cluster, backend = _driver()
    result = driver.provision(_spec())
    path = next(iter(backend.values))

    retained = driver.deprovision(DeprovisionSpec(result.handle))

    assert retained.ok is True
    assert "retained" in retained.message
    assert path in backend.values
    assert ("v1/PersistentVolumeClaim", "acme-payments", "mssql-payments-prod-ledger-data") in cluster.objects

    protected = driver.deprovision(
        DeprovisionSpec(result.handle, config={"deletion_protection": True}),
        delete_data=True,
    )
    destroyed = driver.deprovision(
        DeprovisionSpec(result.handle, config={"deletion_protection": True}),
        delete_data=True,
        force_destroy=True,
    )

    assert protected.ok is False
    assert protected.retryable is False
    assert destroyed.ok is True
    assert path not in backend.values
    assert backend.deletes == [path]


def test_snapshot_is_guarded_and_restore_is_same_cluster_namespace_only() -> None:
    disabled, _, _ = _driver()
    disabled_result = disabled.provision(_spec())
    with pytest.raises(UnsupportedOperationError, match="crash-consistent"):
        disabled.snapshot(ServiceHandle(disabled_result.handle))

    cluster = _Cluster()
    cluster.enable_snapshots()
    driver, cluster, backend = _driver(
        cluster=cluster,
        allow_crash_consistent_snapshots=True,
        volume_snapshot_class="fast-snapshots",
    )
    source = driver.provision(_spec())
    snapshot = driver.snapshot(ServiceHandle(source.handle))
    restored = driver.restore(
        snapshot,
        _spec(service_handle_hint="ledger-copy", managed_service_id="service-2"),
    )

    assert snapshot.snapshot_id.startswith("cluster-1/acme-payments/")
    assert restored.ok is True
    assert restored.handle.endswith("mssql-payments-prod-ledger-copy")
    restored_pvc = _object(
        cluster,
        "v1/PersistentVolumeClaim",
        "acme-payments",
        "mssql-payments-prod-ledger-copy-data",
    )
    assert restored_pvc["spec"]["dataSource"]["name"] == snapshot.snapshot_id.rsplit("/", 1)[-1]
    assert len(backend.values) == 2

    cross_cluster = driver.restore(snapshot, _spec(tenant_cluster_id="cluster-2"))
    other_namespace = driver.restore(snapshot, _spec(app_slug="reports"))
    assert cross_cluster.ok is False
    assert "cross-cluster" in cross_cluster.message
    assert other_namespace.ok is False
    assert "source namespace" in other_namespace.message


def test_driver_contract_and_plugin_registration_are_complete() -> None:
    driver, _, _ = _driver()

    assert PLUGIN.managed_service_drivers[("mssql", "sqlserver_express")] is SQLServerExpressDriver
    assert "agent_enabled" not in driver.config_schema()["properties"]
    assert "MSSQL_TRUST_SERVER_CERTIFICATE" in driver.binding_schema().env_vars
    assert "collation" not in driver.editable_fields()
    assert "storage_size" in driver.editable_fields()


def test_binding_rejects_tampered_endpoint_or_extra_secret_fields() -> None:
    driver, _, backend = _driver()
    result = driver.provision(_spec())
    path = next(iter(backend.values))
    backend.values[path]["database_url"] = "sqlserver://attacker.invalid/master"

    with pytest.raises(ValueError, match="owned endpoint"):
        driver.binding(ServiceHandle(result.handle))

    backend.values[path]["database_url"] = driver._database_url(
        "mssql-payments-prod-ledger.acme-payments.svc.cluster.local",
        backend.values[path]["database"],
        backend.values[path]["username"],
        backend.values[path]["password"],
        trust_server_certificate=True,
    )
    backend.values[path]["unexpected"] = "not-projected"
    with pytest.raises(ValueError, match="unsupported keys"):
        driver.binding(ServiceHandle(result.handle))
