from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.event_stream_managed_kafka import (
    ManagedKafkaConfig,
    ManagedKafkaConflict,
    ManagedKafkaDriver,
    ManagedKafkaError,
    ManagedKafkaNotFound,
    ManagedKafkaRestClient,
)

SPEC = ProvisionSpec(
    organization_id="org-id",
    organization_slug="acme",
    app_id="app-id",
    app_slug="events",
    environment_id="env-id",
    environment_name="production",
    tenant_cluster_id="cluster-id",
    service_handle_hint="stream",
    size="small",
    binding_id="binding-id",
    managed_service_id="managed-id",
)


class FakeManagedKafka:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, Any]] = {}
        self.schema_versions: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.operations: dict[str, dict[str, Any]] = {}
        self._operation = 0

    def get(self, name: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append(("get", name, deepcopy(params)))
        if name not in self.resources:
            raise ManagedKafkaNotFound(name)
        return deepcopy(self.resources[name])

    def list_resources(
        self,
        parent: str,
        collection: str,
        response_key: str,
        *,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        del response_key, params
        self.calls.append(("list", parent, collection))
        prefix = f"{parent}/{collection}/"
        return [deepcopy(value) for name, value in self.resources.items() if name.startswith(prefix)]

    def create(
        self,
        parent: str,
        collection: str,
        resource_id: str,
        id_parameter: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        del id_parameter
        name = f"{parent}/{collection}/{resource_id}"
        self.calls.append(("create", name, deepcopy(body)))
        if name in self.resources:
            raise ManagedKafkaConflict(name)
        resource = {"name": name, **deepcopy(body)}
        if collection == "clusters":
            resource.update(
                {
                    "state": "ACTIVE",
                    "bootstrapAddress": f"{resource_id}.bootstrap.example.test:9092",
                },
            )
        elif collection == "connectClusters":
            resource["state"] = "ACTIVE"
        elif collection == "connectors":
            resource["state"] = "RUNNING"
        elif collection == "acls":
            resource["etag"] = f"etag-{resource_id}"
        self.resources[name] = resource
        if collection in {"clusters", "connectClusters"}:
            return self._done(resource)
        return deepcopy(resource)

    def patch(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self.calls.append(("patch", name, deepcopy(body), list(update_mask)))
        if name not in self.resources:
            raise ManagedKafkaNotFound(name)
        self.resources[name].update(deepcopy(body))
        if "/acls/" in name:
            self.resources[name]["etag"] = f"etag-{self._operation + 1}"
        if name.count("/") == 5 and ("/clusters/" in name or "/connectClusters/" in name):
            return self._done(self.resources[name])
        return deepcopy(self.resources[name])

    def delete(self, name: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        del params
        self.calls.append(("delete", name))
        self.resources.pop(name, None)
        if name.count("/") == 5 and ("/clusters/" in name or "/connectClusters/" in name):
            return self._done({})
        return {}

    def action(self, name: str, action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("action", name, action, deepcopy(body or {})))
        states = {"resume": "RUNNING", "pause": "PAUSED", "stop": "STOPPED", "restart": "RESTARTING"}
        if name not in self.resources:
            raise ManagedKafkaNotFound(name)
        self.resources[name]["state"] = states[action]
        return deepcopy(self.resources[name])

    def create_schema_registry(self, parent: str, registry_id: str) -> dict[str, Any]:
        name = f"{parent}/schemaRegistries/{registry_id}"
        self.calls.append(("create_schema_registry", name))
        if name in self.resources:
            raise ManagedKafkaConflict(name)
        self.resources[name] = {"name": name}
        return deepcopy(self.resources[name])

    def lookup_schema(
        self,
        registry_name: str,
        subject: str,
        body: dict[str, Any],
    ) -> dict[str, Any] | None:
        self.calls.append(("lookup_schema", registry_name, subject, deepcopy(body)))
        for version in self.schema_versions.get((registry_name, subject), []):
            if all(
                version.get(key) == value for key, value in body.items() if key not in {"normalize", "id", "version"}
            ):
                return deepcopy(version)
        return None

    def create_schema_version(
        self,
        registry_name: str,
        subject: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(("create_schema_version", registry_name, subject, deepcopy(body)))
        versions = self.schema_versions.setdefault((registry_name, subject), [])
        version = {**deepcopy(body), "version": int(body.get("version") or len(versions) + 1), "id": len(versions) + 1}
        versions.append(version)
        return {"id": version["id"]}

    def get_schema_version(
        self,
        registry_name: str,
        subject: str,
        version: str = "latest",
    ) -> dict[str, Any]:
        del version
        versions = self.schema_versions.get((registry_name, subject), [])
        if not versions:
            raise ManagedKafkaNotFound(subject)
        return deepcopy(versions[-1])

    def update_schema_setting(
        self,
        registry_name: str,
        setting: str,
        subject: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(("schema_setting", registry_name, setting, subject, deepcopy(body)))
        return deepcopy(body)

    def delete_schema_subject(
        self,
        registry_name: str,
        subject: str,
        *,
        permanent: bool,
    ) -> dict[str, Any]:
        self.calls.append(("delete_schema_subject", registry_name, subject, permanent))
        self.schema_versions.pop((registry_name, subject), None)
        return {}

    def get_operation(self, name: str) -> dict[str, Any]:
        return deepcopy(self.operations[name])

    def _done(self, response: dict[str, Any]) -> dict[str, Any]:
        self._operation += 1
        operation = {
            "name": f"projects/p/locations/l/operations/{self._operation}",
            "done": True,
            "response": deepcopy(response),
        }
        self.operations[operation["name"]] = operation
        return operation


@pytest.fixture
def config() -> ManagedKafkaConfig:
    return ManagedKafkaConfig(
        project_id="project-1",
        location="us-central1",
        subnet_names=("projects/net/regions/us-central1/subnetworks/kafka",),
        operation_timeout_seconds=1,
        poll_interval_seconds=0,
    )


@pytest.fixture
def client() -> FakeManagedKafka:
    return FakeManagedKafka()


@pytest.fixture
def driver(config: ManagedKafkaConfig, client: FakeManagedKafka) -> ManagedKafkaDriver:
    return ManagedKafkaDriver(config=config, client=client, sleep=lambda _: None)


def _full_config() -> dict[str, Any]:
    return {
        "cluster_id": "shared-events",
        "vcpu_count": 6,
        "memory_gib": 24,
        "kms_key": "projects/p/locations/us-central1/keyRings/r/cryptoKeys/k",
        "rebalance_config": {"mode": "AUTO_REBALANCE_ON_SCALE_UP"},
        "tls_config": {
            "trust_config": {
                "cas_configs": [{"ca_pool": "projects/p/locations/us-central1/caPools/clients"}],
            },
            "ssl_principal_mapping_rules": "DEFAULT",
        },
        "topics": [
            {
                "id": "orders.v1",
                "partition_count": 6,
                "replication_factor": 3,
                "configs": {"cleanup.policy": "compact", "retention.ms": "86400000"},
            },
        ],
        "acls": [
            {
                "id": "topic/orders.v1",
                "entries": [
                    {
                        "principal": "User:producer@project-1.iam.gserviceaccount.com",
                        "host": "*",
                        "operation": "WRITE",
                        "permission_type": "ALLOW",
                    },
                ],
            },
        ],
        "schema_registries": [
            {
                "id": "events_registry",
                "mode": "READWRITE",
                "config": {"compatibility": "BACKWARD_TRANSITIVE", "normalize": True},
                "subjects": [
                    {
                        "name": "orders-value",
                        "config": {"compatibility": "FULL"},
                        "versions": [
                            {
                                "schema_type": "AVRO",
                                "schema": '{"type":"record","name":"Order","fields":[]}',
                                "normalize": True,
                            },
                        ],
                    },
                ],
            },
        ],
        "connect_clusters": [
            {
                "id": "events-connect",
                "vcpu_count": 3,
                "memory_gib": 12,
                "network_configs": [
                    {
                        "primary_subnet": "projects/net/regions/us-central1/subnetworks/connect",
                        "dns_domain_names": ["internal.example.test"],
                    },
                ],
                "secret_paths": ["projects/p/secrets/db/versions/7"],
                "connectors": [
                    {
                        "id": "warehouse-sink",
                        "configs": {
                            "connector.class": "com.example.WarehouseSink",
                            "topics": "orders.v1",
                            "tasks.max": "2",
                        },
                        "task_restart_policy": {
                            "minimum_backoff": "60s",
                            "maximum_backoff": "3600s",
                        },
                    },
                ],
            },
        ],
    }


def test_provision_reconciles_cluster_topics_acls_registry_and_connect(
    driver: ManagedKafkaDriver,
    client: FakeManagedKafka,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    assert result.ok and result.ready
    assert result.handle == "event_stream/us-central1/shared-events"
    parent = "projects/project-1/locations/us-central1"
    cluster_name = f"{parent}/clusters/shared-events"
    cluster = client.resources[cluster_name]
    assert cluster["capacityConfig"] == {"vcpuCount": "6", "memoryBytes": str(24 * 1024**3)}
    assert cluster["gcpConfig"]["accessConfig"]["networkConfigs"][0]["subnet"].endswith("/kafka")
    assert cluster["labels"]["astrolift-io-managed-service-id"] == "managed-id"
    topic = client.resources[f"{cluster_name}/topics/orders.v1"]
    assert topic["configs"]["cleanup.policy"] == "compact"
    acl = client.resources[f"{cluster_name}/acls/topic/orders.v1"]
    assert acl["aclEntries"][0]["permissionType"] == "ALLOW"
    registry = f"{parent}/schemaRegistries/events_registry"
    assert (registry, "_astrolift_ownership") in client.schema_versions
    assert (registry, "orders-value") in client.schema_versions
    connect = client.resources[f"{parent}/connectClusters/events-connect"]
    assert connect["kafkaCluster"] == cluster_name
    connector = client.resources[f"{parent}/connectClusters/events-connect/connectors/warehouse-sink"]
    assert connector["configs"]["tasks.max"] == "2"


def test_repeated_provision_is_idempotent(driver: ManagedKafkaDriver, client: FakeManagedKafka) -> None:
    first = driver.provision(replace(SPEC, config=_full_config()))
    create_count = len([call for call in client.calls if call[0] in {"create", "create_schema_version"}])
    second = driver.provision(replace(SPEC, config=_full_config()))
    assert first.ok and second.ok
    assert len([call for call in client.calls if call[0] in {"create", "create_schema_version"}]) == create_count


def test_binding_emits_portable_kafka_and_schema_registry_contract(
    driver: ManagedKafkaDriver,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    binding = driver.binding(
        ServiceHandle(result.handle),
        {
            "schema_registry_id": "events_registry",
            "schema_registry_access": "write",
            "client_certificate_secret_ref": "secret://client-cert",
            "client_key_secret_ref": "secret://client-key",
        },
    )
    assert binding.env_vars["EVENT_STREAM_BROKERS"].literal == "shared-events.bootstrap.example.test:9092"
    assert binding.env_vars["EVENT_STREAM_AUTH_MECHANISM"].literal == "gcp_iam_mtls"
    assert binding.env_vars["EVENT_STREAM_CLIENT_CERT"].secret_ref == "secret://client-cert"
    assert binding.env_vars["SCHEMA_REGISTRY_URL"].literal.endswith("/schemaRegistries/events_registry")
    assert binding.iam_grants[0].resource == "projects/project-1"
    assert binding.iam_grants[0].actions == ["roles/managedkafka.client"]
    assert binding.iam_grants[1].actions == ["roles/managedkafka.schemaRegistryEditor"]


def test_update_scales_cluster_and_increases_topic_partitions(
    driver: ManagedKafkaDriver,
    client: FakeManagedKafka,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    updated = driver.update(
        UpdateSpec(
            result.handle,
            config={
                "vcpu_count": 12,
                "memory_gib": 48,
                "topics": [
                    {
                        "id": "orders.v1",
                        "partition_count": 12,
                        "replication_factor": 3,
                        "configs": {"cleanup.policy": "compact"},
                    },
                ],
            },
        ),
    )
    assert updated.ok
    cluster = client.resources["projects/project-1/locations/us-central1/clusters/shared-events"]
    assert cluster["capacityConfig"]["vcpuCount"] == "12"
    assert client.resources[f"{cluster['name']}/topics/orders.v1"]["partitionCount"] == 12
    cluster_patch = next(
        call
        for call in client.calls
        if call[0] == "patch" and call[1] == cluster["name"] and "capacityConfig" in call[3]
    )
    assert cluster_patch[3] == ["capacityConfig"]


def test_immutable_kms_and_replication_changes_are_rejected(driver: ManagedKafkaDriver) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    kms = driver.update(
        UpdateSpec(
            result.handle,
            config={"kms_key": "projects/p/locations/us-central1/keyRings/r/cryptoKeys/other"},
        ),
    )
    replication = driver.update(
        UpdateSpec(
            result.handle,
            config={
                "topics": [
                    {
                        "id": "orders.v1",
                        "partition_count": 6,
                        "replication_factor": 2,
                    },
                ],
            },
        ),
    )
    assert not kms.ok and "kms_key is immutable" in kms.message
    assert not replication.ok and "replication_factor is immutable" in replication.message


def test_consumer_group_rewind_requires_explicit_consent(
    driver: ManagedKafkaDriver,
    client: FakeManagedKafka,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    cluster = "projects/project-1/locations/us-central1/clusters/shared-events"
    group = f"{cluster}/consumerGroups/orders-reader"
    topic = f"{cluster}/topics/orders.v1"
    client.resources[group] = {
        "name": group,
        "topics": {topic: {"partitions": {"0": {"offset": "100"}}}},
    }
    denied = driver.update(
        UpdateSpec(
            result.handle,
            config={
                "consumer_group_offsets": [
                    {
                        "id": "orders-reader",
                        "topics": {"orders.v1": {"partitions": {"0": {"offset": 90}}}},
                    },
                ],
            },
        ),
    )
    assert not denied.ok and "offset rewind" in denied.message
    accepted = driver.update(
        UpdateSpec(
            result.handle,
            config={
                "consumer_group_offsets": [
                    {
                        "id": "orders-reader",
                        "allow_offset_rewind": True,
                        "topics": {"orders.v1": {"partitions": {"0": {"offset": 90}}}},
                    },
                ],
            },
        ),
    )
    assert accepted.ok
    assert client.resources[group]["topics"][topic]["partitions"]["0"]["offset"] == "90"


def test_connector_state_and_restart_controls_are_explicit(
    driver: ManagedKafkaDriver,
    client: FakeManagedKafka,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    connect = _full_config()["connect_clusters"][0]
    connect["connectors"][0]["desired_state"] = "PAUSED"
    assert driver.update(UpdateSpec(result.handle, config={"connect_clusters": [connect]})).ok
    connector_name = "projects/project-1/locations/us-central1/connectClusters/events-connect/connectors/warehouse-sink"
    assert client.resources[connector_name]["state"] == "PAUSED"
    restart = driver.restart_connector(
        ServiceHandle(result.handle),
        connect_cluster_id="events-connect",
        connector_id="warehouse-sink",
    )
    assert restart.state == "updating"
    action = [call for call in client.calls if call[:3] == ("action", connector_name, "restart")][-1]
    assert action[3] == {}

    with pytest.raises(ManagedKafkaError, match="Connect cluster ID"):
        driver.restart_connector(
            ServiceHandle(result.handle),
            connect_cluster_id="../other",
            connector_id="warehouse-sink",
        )
    with pytest.raises(ManagedKafkaError, match="connector ID"):
        driver.restart_connector(
            ServiceHandle(result.handle),
            connect_cluster_id="events-connect",
            connector_id="../other",
        )


def test_schema_registry_collision_requires_adoption_or_reassignment(
    driver: ManagedKafkaDriver,
    client: FakeManagedKafka,
) -> None:
    parent = "projects/project-1/locations/us-central1"
    registry = f"{parent}/schemaRegistries/events_registry"
    client.resources[registry] = {"name": registry}
    denied = driver.provision(replace(SPEC, config=_full_config()))
    assert not denied.ok and "not Astrolift-owned" in denied.message
    adopted_cfg = _full_config()
    adopted_cfg["schema_registries"][0]["adopt_existing"] = True
    adopted = driver.provision(replace(SPEC, config=adopted_cfg))
    assert adopted.ok
    assert driver._registry_owner(registry) == "managed-id"

    other_spec = replace(SPEC, managed_service_id="other-id")
    adopted_cfg.pop("connect_clusters")
    reassignment_denied = driver.provision(replace(other_spec, config=adopted_cfg))
    assert not reassignment_denied.ok and "another managed service" in reassignment_denied.message
    adopted_cfg["schema_registries"][0]["reassign_existing"] = True
    adopted_cfg["reassign_existing"] = True
    assert driver.provision(replace(other_spec, config=adopted_cfg)).ok
    assert driver._registry_owner(registry) == "other-id"


def test_destructive_child_operations_require_specific_confirmation(driver: ManagedKafkaDriver) -> None:
    for cfg, message in (
        ({"delete_topics": ["orders"]}, "allow_topic_data_delete"),
        ({"delete_acls": ["cluster"]}, "allow_acl_delete"),
        ({"delete_consumer_groups": ["reader"]}, "allow_consumer_group_delete"),
        (
            {
                "schema_registries": [
                    {"id": "registry", "delete_subjects": ["orders"]},
                ],
            },
            "allow_schema_data_delete",
        ),
    ):
        result = driver.provision(replace(SPEC, config=cfg))
        assert not result.ok and message in result.message


def test_deprovision_requires_force_and_data_consent_and_blocks_external_connect(
    driver: ManagedKafkaDriver,
    client: FakeManagedKafka,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    protected = driver.deprovision(DeprovisionSpec(result.handle, _full_config()))
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    retained = driver.deprovision(
        DeprovisionSpec(result.handle, {**_full_config(), "deletion_protection": False}),
        force_destroy=True,
    )
    assert not retained.ok and retained.errors == ["kafka_data_requires_delete_data"]
    parent = "projects/project-1/locations/us-central1"
    external = f"{parent}/connectClusters/customer-connect"
    cluster = f"{parent}/clusters/shared-events"
    client.resources[external] = {
        "name": external,
        "kafkaCluster": cluster,
        "state": "ACTIVE",
        "labels": {"owner": "customer"},
    }
    blocked = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        delete_data=True,
        force_destroy=True,
    )
    assert not blocked.ok and blocked.errors == ["external_dependents_present"]
    deleted = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            {"deletion_protection": False, "delete_external_dependents": True},
        ),
        delete_data=True,
        force_destroy=True,
    )
    assert deleted.ok
    assert cluster not in client.resources and external not in client.resources


def test_status_surfaces_failed_connector(driver: ManagedKafkaDriver, client: FakeManagedKafka) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    connector = next(value for name, value in client.resources.items() if "/connectors/" in name)
    connector["state"] = "FAILED"
    status = driver.status(ServiceHandle(result.handle))
    assert status.state == "error" and "FAILED" in status.message


@pytest.mark.parametrize(
    ("manifest_config", "message"),
    [
        ({"cluster_id": "Bad_Name"}, "cluster_id"),
        ({"subnet_names": []}, "subnet_names"),
        ({"vcpu_count": 2, "memory_gib": 8}, "vcpu_count"),
        ({"vcpu_count": 3, "memory_gib": 27}, "memory ratio"),
        ({"raw_fields": {"labels": {"owner": "attacker"}}}, "protected fields"),
        ({"raw_fields": {"gcpConfig": {"kmsKey": "other"}}}, "protected fields"),
        ({"clear_fields": ["capacityConfig"]}, "protected fields"),
        (
            {"topics": [{"id": "orders", "partition_count": 0, "replication_factor": 3}]},
            "partition_count",
        ),
        (
            {
                "acls": [
                    {
                        "id": "topic/orders",
                        "entries": [
                            {
                                "principal": "User:x@example.test",
                                "host": "10.0.0.1",
                                "operation": "READ",
                                "permission_type": "ALLOW",
                            },
                        ],
                    },
                ],
            },
            "host must be",
        ),
        (
            {
                "connect_clusters": [
                    {
                        "id": "connect",
                        "network_configs": [{"primary_subnet": "subnet"}],
                        "secret_paths": ["projects/p/secrets/s/versions/latest"],
                    },
                ],
            },
            "exact numeric versions",
        ),
        (
            {
                "connect_clusters": [
                    {
                        "id": "connect",
                        "network_configs": [{"primary_subnet": "subnet"}],
                        "raw_fields": {"kafkaCluster": "projects/other/locations/x/clusters/y"},
                    },
                ],
            },
            "protected fields",
        ),
        (
            {
                "connect_clusters": [
                    {
                        "id": "connect",
                        "network_configs": [{"primary_subnet": "subnet"}],
                        "connectors": [
                            {
                                "id": "sink",
                                "configs": {"connector.class": "example.Sink"},
                                "raw_fields": {"state": "RUNNING"},
                            },
                        ],
                    },
                ],
            },
            "protected fields",
        ),
        ({"delete_topics": ["../other"], "allow_topic_data_delete": True}, "delete_topics"),
        ({"delete_acls": ["../other"], "allow_acl_delete": True}, "delete_acls"),
    ],
)
def test_invalid_configs_are_rejected(
    driver: ManagedKafkaDriver,
    manifest_config: dict[str, Any],
    message: str,
) -> None:
    result = driver.provision(replace(SPEC, config=manifest_config))
    assert not result.ok and message in result.message


def test_schema_exposes_native_and_portable_controls(driver: ManagedKafkaDriver) -> None:
    properties = driver.config_schema()["properties"]
    for field in (
        "topics",
        "acls",
        "consumer_group_offsets",
        "schema_registries",
        "connect_clusters",
        "tls_config",
        "raw_fields",
        "delete_external_dependents",
    ):
        assert field in properties
    assert properties["connect_clusters"]["items"]["properties"]["secret_paths"]["maxItems"] == 32
    assert "SCHEMA_REGISTRY_URL" in driver.binding_schema().env_vars


def test_snapshot_contract_is_honest(driver: ManagedKafkaDriver) -> None:
    with pytest.raises(ManagedKafkaError, match="no cluster snapshot API"):
        driver.snapshot(ServiceHandle("event_stream/us-central1/shared-events"))
    with pytest.raises(ManagedKafkaError, match="cannot be restored"):
        driver.restore(SimpleNamespace(), SPEC)  # type: ignore[arg-type]


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any] | list[Any] | None = None,
        *,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"json" if payload is not None else b""

    def json(self) -> dict[str, Any] | list[Any]:
        return deepcopy(self._payload or {})


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_rest_client_uses_schema_registry_and_paginates() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"topics": [{"name": "one"}], "nextPageToken": "next"}),
            FakeResponse(200, {"topics": [{"name": "two"}]}),
            FakeResponse(200, {"id": 1}),
            FakeResponse(200, [1, 2]),
        ],
    )
    client = ManagedKafkaRestClient(session=session)
    rows = client.list_resources("projects/p/locations/l/clusters/c", "topics", "topics")
    assert [row["name"] for row in rows] == ["one", "two"]
    client.create_schema_version("projects/p/locations/l/schemaRegistries/r", "orders/value", {"schema": "{}"})
    raw = client.get("projects/p/locations/l/schemaRegistries/r/subjects")
    assert raw == {"items": [1, 2]}
    assert "%2F" in session.calls[2]["url"]


def test_rest_client_permanent_schema_delete_soft_deletes_first() -> None:
    session = FakeSession([FakeResponse(200, [1]), FakeResponse(200, [1])])
    client = ManagedKafkaRestClient(session=session)
    client.delete_schema_subject(
        "projects/p/locations/l/schemaRegistries/r",
        "orders/value",
        permanent=True,
    )
    assert [call["params"] for call in session.calls] == [
        {"permanent": "false"},
        {"permanent": "true"},
    ]
    assert all("orders%2Fvalue" in call["url"] for call in session.calls)


def test_rest_client_maps_provider_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(404, {"error": {"message": "gone"}}),
            FakeResponse(409, {"error": {"message": "exists"}}),
            FakeResponse(403, {"error": {"message": "denied"}}),
        ],
    )
    client = ManagedKafkaRestClient(session=session)
    with pytest.raises(ManagedKafkaNotFound):
        client.get("missing")
    with pytest.raises(ManagedKafkaConflict):
        client.create("projects/p/locations/l", "clusters", "c", "clusterId", {})
    with pytest.raises(ManagedKafkaError, match="denied"):
        client.get("forbidden")


def test_operation_error_is_not_reported_as_success(
    driver: ManagedKafkaDriver,
    client: FakeManagedKafka,
) -> None:
    client.get_operation = lambda name: {
        "name": name,
        "done": True,
        "error": {"message": "quota exhausted"},
    }
    with pytest.raises(ManagedKafkaError, match="quota exhausted"):
        driver._wait_operation({"name": "operations/wait", "done": False})
