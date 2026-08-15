from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from gcp.managed.encryption_cloud_kms import (
    CloudKMSConfig,
    CloudKMSDriver,
    CloudKMSError,
    CloudKMSNotFound,
    CloudKMSRestClient,
    _parse_handle,
)
from gcp.managed.event_bus_eventarc import EventarcConfig


class FakeCloudKMSClient:
    def __init__(self) -> None:
        self.key_rings: set[str] = set()
        self.keys: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_key_ring(self, name: str) -> dict[str, Any]:
        self.calls.append(("get_key_ring", {"name": name}))
        if name not in self.key_rings:
            raise CloudKMSNotFound(name)
        return {"name": name}

    def create_key_ring(self, parent: str, key_ring_id: str) -> dict[str, Any]:
        name = f"{parent}/keyRings/{key_ring_id}"
        self.calls.append(
            ("create_key_ring", {"parent": parent, "key_ring_id": key_ring_id}),
        )
        self.key_rings.add(name)
        return {"name": name}

    def get_crypto_key(self, name: str) -> dict[str, Any]:
        self.calls.append(("get_crypto_key", {"name": name}))
        if name not in self.keys:
            raise CloudKMSNotFound(name)
        return deepcopy(self.keys[name])

    def create_crypto_key(
        self,
        parent: str,
        crypto_key_id: str,
        crypto_key: dict[str, Any],
        *,
        skip_initial_version_creation: bool,
    ) -> dict[str, Any]:
        name = f"{parent}/cryptoKeys/{crypto_key_id}"
        self.calls.append(
            (
                "create_crypto_key",
                {
                    "parent": parent,
                    "crypto_key_id": crypto_key_id,
                    "crypto_key": deepcopy(crypto_key),
                    "skip_initial_version_creation": skip_initial_version_creation,
                },
            ),
        )
        key = {"name": name, **deepcopy(crypto_key)}
        self.keys[name] = key
        self.versions[name] = []
        if not skip_initial_version_creation:
            version = {
                "name": f"{name}/cryptoKeyVersions/1",
                "state": "ENABLED",
                "algorithm": key["versionTemplate"]["algorithm"],
                "protectionLevel": key["versionTemplate"]["protectionLevel"],
            }
            self.versions[name].append(version)
            self.keys[name]["primary"] = deepcopy(version)
        return deepcopy(self.keys[name])

    def patch_crypto_key(
        self,
        name: str,
        crypto_key: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "patch_crypto_key",
                {
                    "name": name,
                    "crypto_key": deepcopy(crypto_key),
                    "update_mask": list(update_mask),
                },
            ),
        )
        for field in update_mask:
            if crypto_key.get(field) is None:
                self.keys[name].pop(field, None)
            else:
                self.keys[name][field] = deepcopy(crypto_key[field])
        return deepcopy(self.keys[name])

    def list_crypto_key_versions(self, parent: str) -> list[dict[str, Any]]:
        self.calls.append(("list_crypto_key_versions", {"parent": parent}))
        return deepcopy(self.versions.get(parent, []))

    def patch_crypto_key_version(self, name: str, *, state: str) -> dict[str, Any]:
        self.calls.append(("patch_crypto_key_version", {"name": name, "state": state}))
        version = self._version(name)
        version["state"] = state
        return deepcopy(version)

    def destroy_crypto_key_version(self, name: str) -> dict[str, Any]:
        self.calls.append(("destroy_crypto_key_version", {"name": name}))
        version = self._version(name)
        version["state"] = "DESTROY_SCHEDULED"
        return deepcopy(version)

    def restore_crypto_key_version(self, name: str) -> dict[str, Any]:
        self.calls.append(("restore_crypto_key_version", {"name": name}))
        version = self._version(name)
        version["state"] = "DISABLED"
        return deepcopy(version)

    def _version(self, name: str) -> dict[str, Any]:
        parent = name.rsplit("/cryptoKeyVersions/", 1)[0]
        return next(item for item in self.versions[parent] if item["name"] == name)


@pytest.fixture
def client() -> FakeCloudKMSClient:
    return FakeCloudKMSClient()


@pytest.fixture
def driver(client: FakeCloudKMSClient) -> CloudKMSDriver:
    return CloudKMSDriver(
        config=CloudKMSConfig(project_id="acme-prod", location="us-central1"),
        client=client,
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    values: dict[str, Any] = {
        "organization_id": "org-1",
        "organization_slug": "acme",
        "app_id": "app-1",
        "app_slug": "payments",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "gcp-prod",
        "service_handle_hint": "data-key",
        "size": "small",
        "binding_id": "binding-1",
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _provisioned(
    driver: CloudKMSDriver,
    *,
    config: dict[str, Any] | None = None,
) -> str:
    result = driver.provision(_spec(config=config or {}))
    assert result.ok, result
    return result.handle


def test_provision_creates_owned_key_ring_key_and_version(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
) -> None:
    result = driver.provision(_spec(tags={"cost-center": "Platform R&D"}))

    assert result.ok and result.ready
    assert result.handle.startswith(
        "encryption_key/projects/acme-prod/locations/us-central1/keyRings/",
    )
    create = next(payload for name, payload in client.calls if name == "create_crypto_key")
    body = create["crypto_key"]
    assert body["purpose"] == "ENCRYPT_DECRYPT"
    assert body["versionTemplate"] == {
        "algorithm": "GOOGLE_SYMMETRIC_ENCRYPTION",
        "protectionLevel": "SOFTWARE",
    }
    assert body["rotationPeriod"] == "7776000s"
    assert body["destroyScheduledDuration"] == "2592000s"
    assert body["labels"]["astrolift_io_binding"] == "binding-1"
    assert body["labels"]["astrolift_extra_cost-center"] == "platform_r_d"


def test_provision_is_idempotent(driver: CloudKMSDriver, client: FakeCloudKMSClient) -> None:
    first = driver.provision(_spec())
    second = driver.provision(_spec())

    assert first.ok and second.ok and first.handle == second.handle
    assert [name for name, _ in client.calls].count("create_key_ring") == 1
    assert [name for name, _ in client.calls].count("create_crypto_key") == 1


def test_provision_refuses_foreign_key(driver: CloudKMSDriver, client: FakeCloudKMSClient) -> None:
    created = _provisioned(driver)
    key_name = _parse_handle(created)
    client.keys[key_name]["labels"] = {"owner": "somebody-else"}

    result = driver.provision(_spec())

    assert not result.ok
    assert result.errors == ["resource_not_owned"]


def test_native_crypto_key_fields_are_preserved(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "crypto_key": {
                    "keyAccessJustificationsPolicy": {
                        "allowedAccessReasons": ["CUSTOMER_INITIATED_ACCESS"],
                    },
                },
                "protection_level": "HSM",
            },
        ),
    )

    assert result.ok
    body = next(payload for name, payload in client.calls if name == "create_crypto_key")["crypto_key"]
    assert body["keyAccessJustificationsPolicy"]["allowedAccessReasons"]
    assert body["versionTemplate"]["protectionLevel"] == "HSM"


@pytest.mark.parametrize("field", ["name", "labels"])
def test_native_config_cannot_override_owned_fields(driver: CloudKMSDriver, field: str) -> None:
    result = driver.provision(_spec(config={"crypto_key": {field: "mine"}}))
    assert not result.ok
    assert "Astrolift-owned" in result.message


def test_asymmetric_key_requires_algorithm_and_disables_rotation(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
) -> None:
    missing = driver.provision(
        _spec(config={"purpose": "ASYMMETRIC_SIGN", "rotation_enabled": False}),
    )
    assert not missing.ok and "algorithm is required" in missing.message

    created = driver.provision(
        _spec(
            service_handle_hint="signing",
            config={
                "purpose": "ASYMMETRIC_SIGN",
                "algorithm": "EC_SIGN_P256_SHA256",
                "rotation_enabled": False,
            },
        ),
    )
    assert created.ok
    body = [payload for name, payload in client.calls if name == "create_crypto_key"][-1]["crypto_key"]
    assert "rotationPeriod" not in body


def test_asymmetric_rotation_is_rejected(driver: CloudKMSDriver) -> None:
    result = driver.provision(
        _spec(
            config={
                "purpose": "ASYMMETRIC_SIGN",
                "algorithm": "EC_SIGN_P256_SHA256",
                "rotation_enabled": True,
            },
        ),
    )
    assert not result.ok and "only for ENCRYPT_DECRYPT" in result.message


def test_import_only_skips_initial_version_and_rotation(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
) -> None:
    result = driver.provision(_spec(config={"import_only": True}))

    assert result.ok and not result.ready
    create = next(payload for name, payload in client.calls if name == "create_crypto_key")
    assert create["skip_initial_version_creation"] is True
    assert "rotationPeriod" not in create["crypto_key"]


def test_immutable_drift_is_rejected(driver: CloudKMSDriver) -> None:
    handle = _provisioned(driver)
    result = driver.update(UpdateSpec(handle, config={"protection_level": "HSM"}))
    assert not result.ok and "immutable" in result.message


def test_update_disables_then_enables_versions(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
) -> None:
    handle = _provisioned(driver)
    key_name = _parse_handle(handle)

    disabled = driver.update(UpdateSpec(handle, config={"enabled": False}))
    assert disabled.ok and client.versions[key_name][0]["state"] == "DISABLED"
    enabled = driver.update(UpdateSpec(handle, config={"enabled": True}))
    assert enabled.ok and client.versions[key_name][0]["state"] == "ENABLED"


def test_update_restores_scheduled_versions(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
) -> None:
    handle = _provisioned(driver)
    key_name = _parse_handle(handle)
    client.versions[key_name][0]["state"] = "DESTROY_SCHEDULED"

    result = driver.update(
        UpdateSpec(handle, config={"restore_scheduled_versions": True, "enabled": True}),
    )

    assert result.ok
    assert client.versions[key_name][0]["state"] == "ENABLED"
    assert any(name == "restore_crypto_key_version" for name, _ in client.calls)


def test_deprovision_respects_deletion_protection(driver: CloudKMSDriver) -> None:
    handle = _provisioned(driver)
    result = driver.deprovision(DeprovisionSpec(handle))
    assert not result.ok and not result.retryable
    assert result.errors == ["deletion_protection_enabled"]


def test_safe_deprovision_disables_but_retains_material(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
) -> None:
    handle = _provisioned(driver)
    key_name = _parse_handle(handle)

    result = driver.deprovision(
        DeprovisionSpec(handle, {"deletion_protection": False}),
    )

    assert result.ok and "retained" in result.message
    assert client.versions[key_name][0]["state"] == "DISABLED"
    assert not any(name == "destroy_crypto_key_version" for name, _ in client.calls)


def test_delete_data_disables_then_schedules_destruction(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
) -> None:
    handle = _provisioned(driver)
    key_name = _parse_handle(handle)

    result = driver.deprovision(
        DeprovisionSpec(handle, {"deletion_protection": False}),
        delete_data=True,
    )

    assert result.ok and "scheduled destruction" in result.message
    assert client.versions[key_name][0]["state"] == "DESTROY_SCHEDULED"
    operations = [name for name, _ in client.calls]
    assert operations.index("patch_crypto_key_version") < operations.index(
        "destroy_crypto_key_version",
    )


def test_delete_data_waits_for_pending_versions(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
) -> None:
    handle = _provisioned(driver)
    key_name = _parse_handle(handle)
    client.versions[key_name][0]["state"] = "PENDING_GENERATION"

    result = driver.deprovision(
        DeprovisionSpec(handle, {"deletion_protection": False}),
        delete_data=True,
    )

    assert not result.ok and result.retryable
    assert result.errors == ["key_versions_not_destroyable"]


@pytest.mark.parametrize(
    ("version_states", "expected"),
    [
        (["ENABLED"], "available"),
        (["DISABLED"], "available"),
        (["PENDING_GENERATION"], "provisioning"),
        (["DESTROY_SCHEDULED"], "deprovisioning"),
        (["DESTROYED"], "deprovisioned"),
        ([], "provisioning"),
    ],
)
def test_status_maps_version_lifecycle(
    driver: CloudKMSDriver,
    client: FakeCloudKMSClient,
    version_states: list[str],
    expected: str,
) -> None:
    handle = _provisioned(driver)
    key_name = _parse_handle(handle)
    client.versions[key_name] = [
        {"name": f"{key_name}/cryptoKeyVersions/{index}", "state": state}
        for index, state in enumerate(version_states, 1)
    ]
    assert driver.status(ServiceHandle(handle)).state == expected


@pytest.mark.parametrize(
    ("config", "role"),
    [
        ({}, "roles/cloudkms.cryptoKeyEncrypterDecrypter"),
        ({"access_mode": "encrypt"}, "roles/cloudkms.cryptoKeyEncrypter"),
        ({"access_mode": "decrypt"}, "roles/cloudkms.cryptoKeyDecrypter"),
        ({"access_mode": "crypto"}, "roles/cloudkms.cryptoOperator"),
    ],
)
def test_binding_emits_portable_and_gcp_values_with_least_privilege_roles(
    driver: CloudKMSDriver,
    config: dict[str, Any],
    role: str,
) -> None:
    handle = _provisioned(driver)
    binding = driver.binding(ServiceHandle(handle), config)

    assert binding.env_vars["ENCRYPTION_KEY_ID"].literal == _parse_handle(handle)
    assert binding.env_vars["ENCRYPTION_KEY_ARN"].literal == _parse_handle(handle)
    assert binding.env_vars["GCP_KMS_LOCATION"].literal == "us-central1"
    assert binding.iam_grants[0].actions == [role]


def test_asymmetric_binding_uses_signer_verifier_role(driver: CloudKMSDriver) -> None:
    handle = _provisioned(
        driver,
        config={
            "purpose": "ASYMMETRIC_SIGN",
            "algorithm": "EC_SIGN_P256_SHA256",
            "rotation_enabled": False,
        },
    )
    binding = driver.binding(ServiceHandle(handle))
    assert binding.iam_grants[0].actions == ["roles/cloudkms.signerVerifier"]


def test_snapshot_and_restore_are_honestly_unsupported(driver: CloudKMSDriver) -> None:
    handle = _provisioned(driver)
    with pytest.raises(CloudKMSError, match="non-exportable"):
        driver.snapshot(ServiceHandle(handle))
    result = driver.restore(
        SnapshotHandle(handle=handle, snapshot_id="none", created_at="now"),
        _spec(),
    )
    assert not result.ok and result.errors == ["not_implemented"]


@pytest.mark.parametrize("value", ["bad", "encryption_key/not-a-resource"])
def test_parse_handle_rejects_invalid_shapes(value: str) -> None:
    with pytest.raises(CloudKMSError):
        _parse_handle(value)


def test_registration_catalog_cost_and_runtime_config_are_wired() -> None:
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from gcp.cost import SERVICE_ID_BY_VARIANT
    from gcp.plugin import PLUGIN

    assert PLUGIN.managed_service_drivers[("encryption_key", "cloud_kms")] is CloudKMSDriver
    assert SERVICE_ID_BY_VARIANT[("encryption_key", "cloud_kms")] == "EE2F-D110-890C"
    entry = next(
        item
        for item in MATRIX.managed_services
        if item.plugin_id == "gcp" and item.kind == "encryption_key" and item.variant == "cloud_kms"
    )
    assert entry.status == "preview"
    assert "GCP_KMS_KEY_NAME" in entry.binding_envs

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-west1",
        provider_config={
            "project_id": "acme-prod",
            "cloud_kms_location": "us",
            "cloud_kms_key_ring_name_prefix": "platform",
            "cloud_kms_key_name_prefix": "workload",
            "cloud_kms_deletion_protection_default": False,
            "cloud_kms_rotation_period_default": "86400s",
            "cloud_kms_destroy_scheduled_duration_default": "604800s",
            "cloud_kms_api_endpoint": "https://kms.example.test/v1",
        },
        auth_config={},
    )
    config = managed_config_for(
        "gcp",
        cluster,
        kind="encryption_key",
        variant="cloud_kms",
    )
    assert config == CloudKMSConfig(
        project_id="acme-prod",
        location="us",
        key_ring_name_prefix="platform",
        key_name_prefix="workload",
        deletion_protection_default=False,
        rotation_period_default="86400s",
        destroy_scheduled_duration_default="604800s",
        api_endpoint="https://kms.example.test/v1",
    )


@pytest.mark.parametrize(
    ("kind", "variant", "class_name"),
    [
        ("object_store", "gcs", "GCSConfig"),
        ("queue", "pubsub", "PubSubConfig"),
        ("postgres", "cloudsql", "CloudSQLConfig"),
        ("postgres", "alloydb", "AlloyDBConfig"),
        ("mysql", "cloudsql", "CloudSQLMySQLConfig"),
        ("redis", "memorystore", "MemorystoreConfig"),
        ("kv_store", "bigtable", "BigtableConfig"),
        ("vector_index", "vertex_matching_engine", "VertexMatchingEngineConfig"),
        ("time_series", "gcp_managed_prometheus", "GCPManagedPrometheusConfig"),
        ("model_endpoint", "vertex_ai", "VertexAIEndpointConfig"),
        ("encryption_key", "cloud_kms", "CloudKMSConfig"),
        ("faas", "cloud_functions_gen2", "CloudFunctionsConfig"),
        ("filesystem", "filestore", "FilestoreConfig"),
        ("filesystem", "filestore", "FilestoreConfig"),
        ("api_gateway", "api_gateway", "APIGatewayConfig"),
        ("event_stream", "managed_kafka", "ManagedKafkaConfig"),
        ("event_bus", "eventarc", "EventarcConfig"),
    ],
)
def test_every_executable_gcp_driver_has_a_runtime_config(
    kind: str,
    variant: str,
    class_name: str,
) -> None:
    from core.cluster_observability import managed_config_for

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-central1",
        provider_config={"project_id": "acme-prod"},
        auth_config={},
    )
    config = managed_config_for("gcp", cluster, kind=kind, variant=variant)
    assert type(config).__name__ == class_name
    assert config.project_id == "acme-prod"


def test_managed_kafka_runtime_config_preserves_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    from gcp.managed.event_stream_managed_kafka import ManagedKafkaConfig

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-central1",
        provider_config={
            "project_id": "acme-prod",
            "managed_kafka_location": "us-east1",
            "managed_kafka_cluster_id_prefix": "events",
            "managed_kafka_subnet_names": ["subnet-a", "subnet-b"],
            "managed_kafka_deletion_protection_default": False,
            "managed_kafka_api_endpoint": "https://kafka.example.test/v1",
            "managed_kafka_operation_timeout_seconds": 321,
            "managed_kafka_operation_poll_interval_seconds": 0.25,
        },
        auth_config={},
    )

    assert managed_config_for("gcp", cluster, kind="event_stream", variant="managed_kafka") == ManagedKafkaConfig(
        project_id="acme-prod",
        location="us-east1",
        cluster_id_prefix="events",
        subnet_names=("subnet-a", "subnet-b"),
        api_endpoint="https://kafka.example.test/v1",
        deletion_protection_default=False,
        operation_timeout_seconds=321,
        poll_interval_seconds=0.25,
    )


def test_eventarc_runtime_config_preserves_all_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-central1",
        provider_config={
            "project_id": "acme-prod",
            "eventarc_location": "us-east1",
            "eventarc_message_bus_id": "shared-events",
            "eventarc_deletion_protection_default": False,
            "eventarc_api_endpoint": "https://control.example.test/v1",
            "eventarc_publishing_endpoint": "https://publish.example.test/v1",
            "eventarc_operation_timeout_seconds": 321,
            "eventarc_operation_poll_interval_seconds": 0.25,
        },
        auth_config={},
    )

    assert managed_config_for("gcp", cluster, kind="event_bus", variant="eventarc") == EventarcConfig(
        project_id="acme-prod",
        location="us-east1",
        message_bus_id="shared-events",
        deletion_protection_default=False,
        api_endpoint="https://control.example.test/v1",
        publishing_endpoint="https://publish.example.test/v1",
        operation_timeout_seconds=321,
        poll_interval_seconds=0.25,
    )


@pytest.mark.parametrize(
    ("kind", "variant"),
    [("search", "gcp_elastic_cloud"), ("email", "gcp_thirdparty")],
)
def test_placeholder_gcp_drivers_remain_unprovisionable(kind: str, variant: str) -> None:
    from core.cluster_observability import ClusterObservabilityError, managed_config_for

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-central1",
        provider_config={"project_id": "acme-prod"},
        auth_config={},
    )
    with pytest.raises(ClusterObservabilityError, match="planned placeholder"):
        managed_config_for("gcp", cluster, kind=kind, variant=variant)


def test_gcp_config_requires_project_id() -> None:
    from core.cluster_observability import ClusterObservabilityError, managed_config_for

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-central1",
        provider_config={},
        auth_config={},
    )
    with pytest.raises(ClusterObservabilityError, match="project_id"):
        managed_config_for("gcp", cluster, kind="object_store", variant="gcs")


def test_cloud_functions_runtime_config_preserves_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    from gcp.managed.faas_cloud_functions import CloudFunctionsConfig

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-central1",
        provider_config={
            "project_id": "acme-prod",
            "cloud_functions_region": "us-east1",
            "cloud_functions_name_prefix": "functions",
            "cloud_functions_deletion_protection_default": False,
            "cloud_functions_api_endpoint": "https://functions.example.test/v2",
            "cloud_functions_operation_timeout_seconds": 120,
            "cloud_functions_operation_poll_interval_seconds": 0.25,
        },
        auth_config={},
    )
    config = managed_config_for(
        "gcp",
        cluster,
        kind="faas",
        variant="cloud_functions_gen2",
    )
    assert config == CloudFunctionsConfig(
        project_id="acme-prod",
        region="us-east1",
        function_name_prefix="functions",
        api_endpoint="https://functions.example.test/v2",
        deletion_protection_default=False,
        operation_timeout_seconds=120,
        poll_interval_seconds=0.25,
    )


def test_api_gateway_runtime_config_preserves_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    from gcp.managed.api_gateway import APIGatewayConfig

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-central1",
        provider_config={
            "project_id": "acme-prod",
            "api_gateway_region": "us-east1",
            "api_gateway_api_id_prefix": "apis",
            "api_gateway_gateway_id_prefix": "gateways",
            "api_gateway_config_id_prefix": "revisions",
            "api_gateway_deletion_protection_default": False,
            "api_gateway_api_endpoint": "https://gateway.example.test/v1",
            "api_gateway_operation_timeout_seconds": 120,
            "api_gateway_operation_poll_interval_seconds": 0.25,
        },
        auth_config={},
    )
    config = managed_config_for(
        "gcp",
        cluster,
        kind="api_gateway",
        variant="api_gateway",
    )
    assert config == APIGatewayConfig(
        project_id="acme-prod",
        region="us-east1",
        api_id_prefix="apis",
        gateway_id_prefix="gateways",
        config_id_prefix="revisions",
        api_endpoint="https://gateway.example.test/v1",
        deletion_protection_default=False,
        operation_timeout_seconds=120,
        poll_interval_seconds=0.25,
    )


@dataclass
class FakeResponse:
    status_code: int
    payload: dict[str, Any]
    text: str = ""

    @property
    def content(self) -> bytes:
        return b"json" if self.payload else b""

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_rest_client_emits_documented_create_requests() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": "projects/p/locations/l/keyRings/r"}),
            FakeResponse(200, {"name": "projects/p/locations/l/keyRings/r/cryptoKeys/k"}),
        ],
    )
    client = CloudKMSRestClient(session=session)

    client.create_key_ring("projects/p/locations/l", "r")
    client.create_crypto_key(
        "projects/p/locations/l/keyRings/r",
        "k",
        {"purpose": "ENCRYPT_DECRYPT"},
        skip_initial_version_creation=True,
    )

    assert session.calls[0]["url"].endswith("/projects/p/locations/l/keyRings")
    assert session.calls[0]["params"] == {"keyRingId": "r"}
    assert session.calls[1]["params"] == {
        "cryptoKeyId": "k",
        "skipInitialVersionCreation": "true",
    }


def test_rest_client_paginates_versions() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "cryptoKeyVersions": [{"name": "v1"}],
                    "nextPageToken": "next",
                },
            ),
            FakeResponse(200, {"cryptoKeyVersions": [{"name": "v2"}]}),
        ],
    )
    client = CloudKMSRestClient(session=session)
    assert [item["name"] for item in client.list_crypto_key_versions("key")] == ["v1", "v2"]
    assert session.calls[1]["params"]["pageToken"] == "next"


def test_rest_client_distinguishes_not_found_and_provider_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(404, {"error": {"message": "gone"}}),
            FakeResponse(403, {"error": {"message": "denied"}}),
        ],
    )
    client = CloudKMSRestClient(session=session)
    with pytest.raises(CloudKMSNotFound):
        client.get_crypto_key("gone")
    with pytest.raises(CloudKMSError, match="denied"):
        client.get_crypto_key("forbidden")
