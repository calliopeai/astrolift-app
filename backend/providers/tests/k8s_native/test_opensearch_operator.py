from __future__ import annotations

import base64
import copy
import json

import pytest

from _sdk import UnsupportedOperationError
from _sdk.cluster import ApplyResult, DeleteResult, StorageClassInfo
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed.opensearch_operator import (
    CURRENT_API_VERSION,
    DEFAULT_BOOTSTRAP_IMAGE,
    DEFAULT_IMAGE,
    OpenSearchOperatorConfig,
    OpenSearchSearchDriver,
    OpenSearchVectorDriver,
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
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str | None, str], dict] = {}
        self.applied: list[list[dict]] = []
        self.deleted: list[list[dict]] = []
        self.namespaces: list[tuple[str, str, dict, dict]] = []
        self.classes = [StorageClassInfo("fast-rwo", True, "csi.example.test")]

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
                    field: base64.b64encode(str(value).encode()).decode()
                    for field, value in manifest.pop("stringData").items()
                }
            if old_status is not None and "status" not in manifest:
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


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "acme",
        "app_id": "project-1",
        "app_slug": "payments",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "catalog",
        "size": "small",
        "config": {},
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _driver(
    *,
    vector: bool = False,
    cluster: _Cluster | None = None,
    secrets: _Secrets | None = None,
    **config,
):
    cluster = cluster or _Cluster()
    secrets = secrets or _Secrets()
    driver_type = OpenSearchVectorDriver if vector else OpenSearchSearchDriver
    return (
        driver_type(
            config=OpenSearchOperatorConfig(
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


def _vector_spec(**overrides) -> ProvisionSpec:
    config = {"vector_dimension": 1536}
    config.update(overrides.pop("config", {}))
    return _spec(config=config, **overrides)


def test_search_provision_emits_secure_operator_resources_and_external_credentials() -> None:
    driver, cluster, backend = _driver()

    result = driver.provision(_spec())

    assert result.ok is True
    assert result.ready is False
    assert result.handle == "search/cluster-1/acme-payments/os-payments-prod-catalog"
    assert cluster.namespaces[0][1] == "acme-payments"
    assert {row["kind"] for row in cluster.applied[-1]} == {
        "NetworkPolicy",
        "OpenSearchCluster",
        "OpensearchRole",
        "OpensearchUser",
        "OpensearchUserRoleBinding",
        "Secret",
    }
    search = _object(
        cluster,
        f"{CURRENT_API_VERSION}/OpenSearchCluster",
        "acme-payments",
        "os-payments-prod-catalog",
    )
    assert search["spec"]["general"]["image"] == DEFAULT_IMAGE
    assert search["spec"]["general"]["setVMMaxMapCount"] is False
    assert search["spec"]["general"]["podSecurityContext"]["runAsNonRoot"] is True
    assert search["spec"]["dashboards"] == {"enable": False}
    assert search["spec"]["security"]["config"]["adminCredentialsSecret"] == {"name": "os-payments-prod-catalog-admin"}
    assert search["spec"]["security"]["tls"]["http"]["rotateDaysBeforeExpiry"] == 30
    assert search["spec"]["security"]["tls"]["transport"]["rotateDaysBeforeExpiry"] == 30
    role = _object(
        cluster,
        f"{CURRENT_API_VERSION}/OpensearchRole",
        "acme-payments",
        "os-payments-prod-catalog-client",
    )
    assert role["spec"]["indexPermissions"][0]["indexPatterns"] == ["payments-prod-catalog-*"]
    assert "indices:admin/exists" in role["spec"]["indexPermissions"][0]["allowedActions"]
    assert role["spec"]["clusterPermissions"] == [
        "indices:data/write/bulk*",
        "indices:data/read/mget",
        "indices:data/read/msearch",
        "indices:data/read/mtv",
        "indices:data/read/scroll",
    ]
    user = _object(
        cluster,
        f"{CURRENT_API_VERSION}/OpensearchUser",
        "acme-payments",
        "os-payments-prod-catalog-app",
    )
    assert user["spec"]["passwordFrom"] == {
        "name": "os-payments-prod-catalog-app-user",
        "key": "os-payments-prod-catalog-app",
    }
    non_secret_rows = [row for row in cluster.applied[-1] if row["kind"] != "Secret"]
    path = "managed/opensearch/cluster-1/acme-payments/os-payments-prod-catalog/credentials"
    assert backend.values[path]["password"] not in repr(non_secret_rows)
    assert backend.values[path]["admin_password"] not in repr(non_secret_rows)

    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["SEARCH_PASSWORD"].secret_ref == f"{path}#password"
    assert binding.env_vars["SEARCH_ENDPOINT"].literal == (
        "https://os-payments-prod-catalog.acme-payments.svc.cluster.local:9200"
    )
    assert binding.env_vars["SEARCH_TLS_VERIFY"].literal == "false"


def test_vector_provision_creates_real_index_with_explicit_hnsw_mapping() -> None:
    driver, cluster, backend = _driver(vector=True)

    result = driver.provision(
        _vector_spec(
            config={
                "vector_dimension": 3072,
                "vector_field": "embedding",
                "vector_engine": "faiss",
                "space_type": "innerproduct",
                "vector_m": 24,
                "vector_ef_construction": 128,
                "vector_ef_search": 64,
                "shards": 3,
                "index_replicas": 2,
            }
        )
    )

    assert result.ok is True
    assert result.handle.startswith("vector_index/")
    job = _object(
        cluster,
        "batch/v1/Job",
        "acme-payments",
        "os-payments-prod-catalog-create-index",
    )
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == DEFAULT_BOOTSTRAP_IMAGE
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["runAsUser"] == 65532
    assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    env = {row["name"]: row for row in container["env"]}
    payload = json.loads(env["OS_INDEX_PAYLOAD"]["value"])
    vector = payload["mappings"]["properties"]["embedding"]
    assert vector["dimension"] == 3072
    assert vector["method"] == {
        "name": "hnsw",
        "engine": "faiss",
        "space_type": "innerproduct",
        "parameters": {"ef_construction": 128, "m": 24},
    }
    assert payload["settings"]["index"] == {
        "knn": True,
        "knn.algo_param.ef_search": 64,
        "number_of_shards": 3,
        "number_of_replicas": 2,
    }
    role = _object(
        cluster,
        f"{CURRENT_API_VERSION}/OpensearchRole",
        "acme-payments",
        "os-payments-prod-catalog-client",
    )
    assert role["spec"]["indexPermissions"][0]["indexPatterns"] == ["payments-prod-catalog"]
    assert env["OS_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == ("os-payments-prod-catalog-app")
    assert backend.values[next(iter(backend.values))]["password"] not in repr(job)

    binding = driver.binding(ServiceHandle(result.handle))
    assert "VECTOR_API_KEY" not in binding.env_vars
    assert binding.env_vars["VECTOR_USERNAME"].literal == "os-payments-prod-catalog-app"
    assert binding.env_vars["VECTOR_PASSWORD"].secret_ref.endswith("#password")
    assert binding.env_vars["VECTOR_INDEX_NAME"].literal == "payments-prod-catalog"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"not_a_setting": True}, "unsupported"),
        ({"version": "3.7.0"}, "custom"),
        ({"image": "opensearchproject/opensearch:latest"}, "custom"),
        ({"network_policy_mode": "disabled"}, "NetworkPolicy"),
        ({"replicas": 2}, "odd value"),
        ({"replicas": 1}, "single-node"),
        ({"memory_request": "1Gi"}, "at least 2Gi"),
        ({"additional_config": {"plugins.security.disabled": "true"}}, "reserved"),
        ({"plugins_list": ["repository-s3"]}, "custom"),
    ],
)
def test_invalid_or_unsafe_search_config_fails_before_mutation(config, message) -> None:
    driver, cluster, backend = _driver()

    result = driver.provision(_spec(config=config))

    assert result.ok is False
    assert message in result.message
    assert cluster.applied == []
    assert backend.upserts == []


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "vector_dimension"),
        ({"vector_dimension": 0}, "vector_dimension"),
        ({"vector_dimension": 16001}, "vector_dimension"),
        ({"vector_dimension": 768, "vector_field": "bad field"}, "vector_field"),
        ({"vector_dimension": 768, "vector_engine": "nmslib"}, "vector_engine"),
        ({"vector_dimension": 768, "space_type": "hamming"}, "space_type"),
        ({"vector_dimension": 768, "vector_m": 1}, "vector_m"),
        ({"vector_dimension": 768, "shards": True}, "shards"),
    ],
)
def test_invalid_vector_contract_fails_before_mutation(config, message) -> None:
    driver, cluster, backend = _driver(vector=True)

    result = driver.provision(_spec(config=config))

    assert result.ok is False
    assert message in result.message
    assert cluster.applied == []
    assert backend.upserts == []


def test_single_node_custom_plugins_and_network_disable_require_install_opt_in() -> None:
    driver, cluster, _ = _driver(
        allow_single_node=True,
        allow_custom_plugins=True,
        allow_network_policy_disable=True,
    )

    result = driver.provision(
        _spec(
            config={
                "replicas": 1,
                "plugins_list": ["repository-s3"],
                "network_policy_mode": "disabled",
            }
        )
    )

    assert result.ok is True
    search = _object(
        cluster,
        f"{CURRENT_API_VERSION}/OpenSearchCluster",
        "acme-payments",
        "os-payments-prod-catalog",
    )
    assert search["spec"]["general"]["pluginsList"] == ["repository-s3"]
    assert "NetworkPolicy" not in {row["kind"] for row in cluster.applied[-1]}


def test_network_policy_is_tenant_scoped_and_keeps_transport_cluster_local() -> None:
    driver, cluster, _ = _driver()

    assert driver.provision(_spec()).ok is True

    policy = _object(
        cluster,
        "networking.k8s.io/v1/NetworkPolicy",
        "acme-payments",
        "os-payments-prod-catalog-ingress",
    )
    http_rule, transport_rule = policy["spec"]["ingress"]
    peers = http_rule["from"]
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
    assert http_rule["ports"] == [{"protocol": "TCP", "port": 9200}]
    assert transport_rule == {
        "from": [{"podSelector": {"matchLabels": {"opensearch.org/opensearch-cluster": "os-payments-prod-catalog"}}}],
        "ports": [{"protocol": "TCP", "port": 9300}],
    }


def test_verified_custom_tls_mounts_ca_and_removes_insecure_bootstrap_flag() -> None:
    driver, cluster, backend = _driver(
        vector=True,
        http_tls_secret_name="search-http-tls",
        http_tls_ca_secret_name="search-http-ca",
        http_tls_admin_secret_name="search-http-admin",
        http_tls_admin_dns=("CN=search-admin,OU=Astrolift",),
        http_tls_verify=True,
    )

    result = driver.provision(_vector_spec())

    assert result.ok is True
    search = _object(
        cluster,
        f"{CURRENT_API_VERSION}/OpenSearchCluster",
        "acme-payments",
        "os-payments-prod-catalog",
    )
    assert search["spec"]["security"]["tls"]["http"] == {
        "generate": False,
        "secret": {"name": "search-http-tls"},
        "caSecret": {"name": "search-http-ca"},
        "adminDn": ["CN=search-admin,OU=Astrolift"],
    }
    assert search["spec"]["security"]["config"]["adminSecret"] == {"name": "search-http-admin"}
    job = _object(
        cluster,
        "batch/v1/Job",
        "acme-payments",
        "os-payments-prod-catalog-create-index",
    )
    container = job["spec"]["template"]["spec"]["containers"][0]
    env = {row["name"]: row for row in container["env"]}
    assert env["TLS_ARGS"]["value"] == "--cacert /var/run/astrolift-opensearch-ca/ca.crt"
    assert container["volumeMounts"][0]["readOnly"] is True
    assert backend.values[next(iter(backend.values))]["tls_verify"] == "true"


def test_generated_tls_cannot_be_claimed_as_verifiable() -> None:
    driver, cluster, backend = _driver(http_tls_verify=True)

    result = driver.provision(_spec())

    assert result.ok is False
    assert "operator-provided certificate" in result.message
    assert cluster.applied == []
    assert backend.upserts == []


@pytest.mark.parametrize(
    "config",
    [
        {"http_tls_secret_name": "search-http-tls"},
        {
            "http_tls_secret_name": "search-http-tls",
            "http_tls_admin_secret_name": "search-http-admin",
        },
    ],
)
def test_custom_tls_requires_admin_certificate_and_dn(config) -> None:
    driver, cluster, backend = _driver(**config)

    result = driver.provision(_spec())

    assert result.ok is False
    assert "custom OpenSearch HTTP TLS requires" in result.message
    assert cluster.applied == []
    assert backend.upserts == []


def test_foreign_cluster_is_never_adopted() -> None:
    driver, cluster, backend = _driver()
    cluster.objects[(f"{CURRENT_API_VERSION}/OpenSearchCluster", "acme-payments", "os-payments-prod-catalog")] = {
        "apiVersion": CURRENT_API_VERSION,
        "kind": "OpenSearchCluster",
        "metadata": {"name": "os-payments-prod-catalog", "namespace": "acme-payments"},
    }

    result = driver.provision(_spec())

    assert result.ok is False
    assert "refusing to adopt" in result.message
    assert backend.upserts == []


def test_status_includes_operator_health_and_vector_bootstrap_state() -> None:
    driver, cluster, _ = _driver(vector=True)
    result = driver.provision(_vector_spec())
    cluster_key = (
        f"{CURRENT_API_VERSION}/OpenSearchCluster",
        "acme-payments",
        "os-payments-prod-catalog",
    )
    job_key = ("batch/v1/Job", "acme-payments", "os-payments-prod-catalog-create-index")

    assert driver.status(ServiceHandle(result.handle)).state == "provisioning"
    cluster.objects[cluster_key]["status"] = {
        "phase": "RUNNING",
        "health": "green",
        "initialized": True,
    }
    assert "application access is pending" in driver.status(ServiceHandle(result.handle)).message
    for kind, name in (
        ("OpensearchRole", "os-payments-prod-catalog-client"),
        ("OpensearchUser", "os-payments-prod-catalog-app"),
        ("OpensearchUserRoleBinding", "os-payments-prod-catalog-client-binding"),
    ):
        cluster.objects[(f"{CURRENT_API_VERSION}/{kind}", "acme-payments", name)]["status"] = {"state": "CREATED"}
    assert driver.status(ServiceHandle(result.handle)).message == "vector index bootstrap is pending"
    cluster.objects[job_key]["status"] = {"failed": 1}
    assert driver.status(ServiceHandle(result.handle)).state == "error"
    cluster.objects[job_key]["status"] = {"succeeded": 1}
    assert driver.status(ServiceHandle(result.handle)).state == "available"
    cluster.objects[cluster_key]["status"] = {"phase": "FAILED", "health": "red"}
    assert driver.status(ServiceHandle(result.handle)).state == "error"


def test_status_fails_closed_when_application_access_reconciliation_fails() -> None:
    driver, cluster, _ = _driver()
    result = driver.provision(_spec())
    cluster.objects[(f"{CURRENT_API_VERSION}/OpenSearchCluster", "acme-payments", "os-payments-prod-catalog")][
        "status"
    ] = {"phase": "RUNNING", "health": "green", "initialized": True}
    cluster.objects[
        (
            f"{CURRENT_API_VERSION}/OpensearchUserRoleBinding",
            "acme-payments",
            "os-payments-prod-catalog-client-binding",
        )
    ]["status"] = {"state": "ERROR", "reason": "  role\nwas rejected  "}

    status = driver.status(ServiceHandle(result.handle))

    assert status.state == "error"
    assert status.message.endswith("role was rejected")


def test_reprovision_replaces_failed_bootstrap_job_and_existing_index_is_idempotent() -> None:
    driver, cluster, _ = _driver(vector=True)
    spec = _vector_spec()
    first = driver.provision(spec)
    key = ("batch/v1/Job", "acme-payments", "os-payments-prod-catalog-create-index")
    cluster.objects[key]["status"] = {"failed": 1}

    second = driver.provision(spec)

    assert first.ok is True
    assert second.ok is True
    assert any(row["kind"] == "Job" for row in cluster.deleted[-1])
    script = cluster.objects[key]["spec"]["template"]["spec"]["containers"][0]["args"][0]
    assert "--head" in script
    assert "exit 0" in script


def test_reprovision_rejects_vector_mapping_drift_before_job_mutation() -> None:
    driver, cluster, _ = _driver(vector=True)
    assert driver.provision(_vector_spec()).ok is True
    applied = len(cluster.applied)

    changed = driver.provision(_vector_spec(config={"vector_dimension": 768}))

    assert changed.ok is False
    assert "vector_dimension is immutable" in changed.message
    assert len(cluster.applied) == applied


def test_partial_update_preserves_shape_and_rejects_immutable_or_unsafe_changes() -> None:
    driver, cluster, _ = _driver()
    result = driver.provision(_spec())

    updated = driver.update(
        UpdateSpec(
            result.handle,
            config={
                "cpu_request": "750m",
                "disk_size": "30Gi",
                "node_selector": {"workload": "search"},
            },
        )
    )

    assert updated.ok is True
    search = _object(
        cluster,
        f"{CURRENT_API_VERSION}/OpenSearchCluster",
        "acme-payments",
        "os-payments-prod-catalog",
    )
    pool = search["spec"]["nodePools"][0]
    assert pool["resources"]["requests"]["cpu"] == "750m"
    assert pool["diskSize"] == "30Gi"
    assert pool["nodeSelector"] == {"workload": "search"}

    shrink = driver.update(UpdateSpec(result.handle, config={"disk_size": "10Gi"}))
    topology = driver.update(UpdateSpec(result.handle, config={"topology": "dedicated"}))
    downgrade = driver.update(UpdateSpec(result.handle, config={"version": "3.7.0"}))
    assert shrink.ok is False
    assert "cannot shrink" in shrink.message
    assert topology.ok is False
    assert "immutable" in topology.message
    assert downgrade.ok is False


def test_vector_mapping_is_immutable_after_index_creation() -> None:
    driver, _, _ = _driver(vector=True)
    result = driver.provision(_vector_spec())

    changed = driver.update(UpdateSpec(result.handle, config={"vector_dimension": 768}))

    assert changed.ok is False
    assert "vector_dimension is immutable" in changed.message


def test_default_teardown_retains_data_and_credentials_and_repairs_external_bundle() -> None:
    driver, cluster, backend = _driver(vector=True)
    spec = _vector_spec()
    result = driver.provision(spec)
    path = next(iter(backend.values))
    original = copy.deepcopy(backend.values[path])

    retained = driver.deprovision(DeprovisionSpec(result.handle))

    assert retained.ok is True
    assert "retained" in retained.message
    assert path in backend.values
    assert ("v1/Secret", "acme-payments", "os-payments-prod-catalog-admin") in cluster.objects
    assert ("batch/v1/Job", "acme-payments", "os-payments-prod-catalog-create-index") not in cluster.objects

    backend.values.clear()
    reprovisioned = driver.provision(spec)
    assert reprovisioned.ok is True
    assert backend.values[path] == original


def test_destructive_teardown_requires_force_and_deletes_secret_bundle() -> None:
    driver, cluster, backend = _driver()
    result = driver.provision(_spec())
    path = next(iter(backend.values))

    protected = driver.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    destroyed = driver.deprovision(
        DeprovisionSpec(result.handle),
        delete_data=True,
        force_destroy=True,
    )

    assert protected.ok is False
    assert protected.retryable is False
    assert destroyed.ok is True
    assert path not in backend.values
    assert backend.deletes == [path]
    assert ("v1/Secret", "acme-payments", "os-payments-prod-catalog-admin") not in cluster.objects


def test_snapshots_and_restore_fail_honestly_until_repository_support_lands() -> None:
    driver, _, _ = _driver()

    with pytest.raises(UnsupportedOperationError, match="repository"):
        driver.snapshot(ServiceHandle("search/cluster/ns/name"))
    restored = driver.restore(
        type("Snapshot", (), {"snapshot_id": "not-used"})(),
        _spec(),
    )
    assert restored.ok is False
    assert restored.errors == ["unsupported_operation"]


def test_binding_rejects_tampered_endpoint_and_extra_secret_fields() -> None:
    driver, _, backend = _driver()
    result = driver.provision(_spec())
    path = next(iter(backend.values))
    backend.values[path]["endpoint"] = "https://attacker.invalid"

    with pytest.raises(ValueError, match="owned Service"):
        driver.binding(ServiceHandle(result.handle))

    backend.values[path]["endpoint"] = "https://os-payments-prod-catalog.acme-payments.svc.cluster.local:9200"
    backend.values[path]["unexpected"] = "not-projected"
    with pytest.raises(ValueError, match="unsupported keys"):
        driver.binding(ServiceHandle(result.handle))

    backend.values[path].pop("unexpected")
    backend.values[path]["password"] = 'unsafe"\nnext-option = "value'
    with pytest.raises(ValueError, match="unsupported characters"):
        driver.binding(ServiceHandle(result.handle))


def test_update_reconciles_tls_policy_without_rotating_credentials() -> None:
    cluster = _Cluster()
    backend = _Secrets()
    insecure, _, _ = _driver(cluster=cluster, secrets=backend)
    spec = _spec()
    first = insecure.provision(spec)
    path = next(iter(backend.values))
    original_password = backend.values[path]["password"]
    verified, _, _ = _driver(
        cluster=cluster,
        secrets=backend,
        http_tls_secret_name="search-http-tls",
        http_tls_ca_secret_name="search-http-ca",
        http_tls_admin_secret_name="search-http-admin",
        http_tls_admin_dns=("CN=search-admin,OU=Astrolift",),
        http_tls_verify=True,
    )

    with pytest.raises(ValueError, match="TLS policy is stale"):
        verified.binding(ServiceHandle(first.handle))
    second = verified.update(UpdateSpec(first.handle))

    assert second.ok is True
    assert backend.values[path]["password"] == original_password
    assert backend.values[path]["tls_verify"] == "true"
    assert verified.binding(ServiceHandle(first.handle)).env_vars["SEARCH_TLS_VERIFY"].literal == "true"


def test_driver_contract_and_plugin_registration_are_complete() -> None:
    search, _, _ = _driver()
    vector, _, _ = _driver(vector=True)

    assert PLUGIN.managed_service_drivers[("search", "opensearch_operator")] is OpenSearchSearchDriver
    assert PLUGIN.managed_service_drivers[("vector_index", "opensearch_operator_vector")] is OpenSearchVectorDriver
    assert search.config_schema()["required"] == []
    assert vector.config_schema()["required"] == ["vector_dimension"]
    assert "VECTOR_PASSWORD" in vector.binding_schema().env_vars
    assert "network_policy_mode" not in search.editable_fields()
