from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.postgres_alloydb import (
    AlloyDBConfig,
    AlloyDBError,
    AlloyDBNotFound,
    AlloyDBPostgresDriver,
    AlloyDBRestClient,
    _generate_master_password,
    _parse_handle,
)
from gcp.secrets import GCPSecretsBackend, GCPSecretsConfig


class FakeSecretClient:
    def __init__(self) -> None:
        self.secrets: dict[str, list[bytes]] = {}

    def create_secret(self, *, request: dict[str, Any]) -> None:
        secret_id = request["secret_id"]
        if secret_id in self.secrets:
            raise RuntimeError("AlreadyExists")
        self.secrets[secret_id] = []

    def add_secret_version(self, *, request: dict[str, Any]) -> None:
        secret_id = request["parent"].split("/secrets/")[-1]
        self.secrets.setdefault(secret_id, []).append(request["payload"]["data"])

    def access_secret_version(
        self,
        *,
        request: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> SimpleNamespace:
        full_name = (request or {}).get("name") or name or ""
        secret_id = full_name.split("/secrets/", 1)[-1].split("/versions/", 1)[0]
        versions = self.secrets.get(secret_id)
        if not versions:
            raise RuntimeError("404 not found")
        return SimpleNamespace(payload=SimpleNamespace(data=versions[-1]))

    def delete_secret(self, *, request: dict[str, Any]) -> None:
        self.secrets.pop(request["name"].split("/secrets/")[-1], None)


class FakeAlloyDBClient:
    def __init__(self) -> None:
        self.clusters: dict[str, dict[str, Any]] = {}
        self.instances: dict[str, dict[str, Any]] = {}
        self.backups: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _call(self, operation: str, **kwargs: Any) -> None:
        self.calls.append((operation, kwargs))

    def get_cluster(self, name: str) -> dict[str, Any]:
        self._call("get_cluster", name=name)
        if name not in self.clusters:
            raise AlloyDBNotFound(name)
        return self.clusters[name]

    def create_cluster(self, parent: str, cluster_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._call("create_cluster", parent=parent, cluster_id=cluster_id, body=body)
        name = f"{parent}/clusters/{cluster_id}"
        self.clusters[name] = {**body, "name": name, "state": "READY"}
        return {"name": "operations/create-cluster", "done": True}

    def patch_cluster(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self._call("patch_cluster", name=name, body=body, update_mask=update_mask)
        self.clusters[name].update(body)
        return {"name": "operations/patch-cluster", "done": True}

    def delete_cluster(self, name: str, *, force: bool) -> dict[str, Any]:
        self._call("delete_cluster", name=name, force=force)
        self.clusters.pop(name, None)
        for instance_name in list(self.instances):
            if instance_name.startswith(f"{name}/instances/"):
                del self.instances[instance_name]
        return {"name": "operations/delete-cluster", "done": True}

    def restore_cluster(
        self,
        parent: str,
        cluster_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self._call("restore_cluster", parent=parent, cluster_id=cluster_id, body=body)
        name = f"{parent}/clusters/{cluster_id}"
        self.clusters[name] = {**body["cluster"], "name": name, "state": "READY"}
        return {"name": "operations/restore-cluster", "done": True}

    def get_instance(self, name: str) -> dict[str, Any]:
        self._call("get_instance", name=name)
        if name not in self.instances:
            raise AlloyDBNotFound(name)
        return self.instances[name]

    def list_instances(self, parent: str) -> list[dict[str, Any]]:
        self._call("list_instances", parent=parent)
        return [value for name, value in self.instances.items() if name.startswith(f"{parent}/instances/")]

    def create_instance(
        self,
        parent: str,
        instance_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self._call("create_instance", parent=parent, instance_id=instance_id, body=body)
        name = f"{parent}/instances/{instance_id}"
        self.instances[name] = {**body, "name": name, "state": "READY"}
        return {"name": f"operations/create-{instance_id}", "done": True}

    def patch_instance(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self._call("patch_instance", name=name, body=body, update_mask=update_mask)
        self.instances[name].update(body)
        return {"name": "operations/patch-instance", "done": True}

    def delete_instance(self, name: str) -> dict[str, Any]:
        self.instances.pop(name, None)
        return {"done": True}

    def get_connection_info(self, parent: str) -> dict[str, Any]:
        self._call("get_connection_info", parent=parent)
        return {"ipAddress": "10.20.30.40", "publicIpAddress": "34.1.2.3"}

    def create_backup(self, parent: str, backup_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._call("create_backup", parent=parent, backup_id=backup_id, body=body)
        name = f"{parent}/backups/{backup_id}"
        self.backups[name] = {
            **body,
            "name": name,
            "state": "READY",
            "createTime": "2026-08-14T00:00:00Z",
        }
        return {"name": "operations/create-backup", "done": True}

    def get_backup(self, name: str) -> dict[str, Any]:
        if name not in self.backups:
            raise AlloyDBNotFound(name)
        return self.backups[name]

    def get_operation(self, name: str) -> dict[str, Any]:
        self._call("get_operation", name=name)
        return {"name": name, "done": True}


@pytest.fixture
def cloud() -> FakeAlloyDBClient:
    return FakeAlloyDBClient()


@pytest.fixture
def secrets_client() -> FakeSecretClient:
    return FakeSecretClient()


@pytest.fixture
def driver(cloud: FakeAlloyDBClient, secrets_client: FakeSecretClient) -> AlloyDBPostgresDriver:
    return AlloyDBPostgresDriver(
        config=AlloyDBConfig(
            project_id="acme-prod",
            region="us-central1",
            network="projects/123/global/networks/platform",
            operation_poll_interval_seconds=0.001,
        ),
        client=cloud,
        secrets_client=secrets_client,
        sleep=lambda _: None,
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    values: dict[str, Any] = {
        "organization_id": "1",
        "organization_slug": "acme",
        "app_id": "2",
        "app_slug": "api",
        "environment_id": "3",
        "environment_name": "prod",
        "tenant_cluster_id": "gcp-prod",
        "service_handle_hint": "primary-db",
        "size": "small",
        "binding_id": "binding-guid",
        "managed_service_id": "service-guid",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def test_provision_creates_cluster_primary_secret_and_native_options(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
    secrets_client: FakeSecretClient,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "database_version": "POSTGRES_17",
                "cluster": {"annotations": {"owner": "platform"}, "labels": {"custom": "yes"}},
                "primary_instance": {"activationPolicy": "ALWAYS"},
                "database_flags": {"alloydb.enable_pgaudit": "on"},
                "connection_pool": {"enabled": True},
            },
        ),
    )
    assert result.ok and result.ready
    cluster_id = _parse_handle(result.handle)
    cluster = cloud.clusters[f"projects/acme-prod/locations/us-central1/clusters/{cluster_id}"]
    assert cluster["databaseVersion"] == "POSTGRES_17"
    assert cluster["annotations"] == {"owner": "platform"}
    assert cluster["labels"]["custom"] == "yes"
    assert cluster["labels"]["astrolift-managed-service-id"] == "service-guid"
    primary = next(iter(cloud.instances.values()))
    assert primary["activationPolicy"] == "ALWAYS"
    assert primary["machineConfig"]["machineType"] == "n2-highmem-2"
    assert primary["databaseFlags"]["alloydb.enable_pgaudit"] == "on"
    assert primary["connectionPoolConfig"]["enabled"] is True
    assert any(name.endswith(f"alloydb-{cluster_id}-master") for name in secrets_client.secrets)


def test_provision_requires_private_network_or_psc(secrets_client: FakeSecretClient) -> None:
    driver = AlloyDBPostgresDriver(
        config=AlloyDBConfig(project_id="p", region="r"),
        client=FakeAlloyDBClient(),
        secrets_client=secrets_client,
    )
    result = driver.provision(_spec())
    assert not result.ok
    assert "network/private IP or psc_enabled=true" in result.message
    assert not secrets_client.secrets


def test_provision_psc_without_network(driver: AlloyDBPostgresDriver) -> None:
    driver._config = AlloyDBConfig(project_id="acme-prod", region="us-central1")  # type: ignore[misc]
    result = driver.provision(_spec(config={"psc_enabled": True}))
    assert result.ok


def test_provision_is_idempotent(driver: AlloyDBPostgresDriver, cloud: FakeAlloyDBClient) -> None:
    first = driver.provision(_spec())
    second = driver.provision(_spec())
    assert first.handle == second.handle
    assert len([call for call in cloud.calls if call[0] == "create_cluster"]) == 1
    assert len([call for call in cloud.calls if call[0] == "create_instance"]) == 1


def test_primary_instance_id_is_immutable(
    driver: AlloyDBPostgresDriver,
) -> None:
    first = driver.provision(_spec())
    assert first.ok
    changed = driver.provision(_spec(config={"primary_instance_id": "new-primary"}))
    assert changed.errors == ["immutable_primary_instance_id"]


def test_provision_refuses_unowned_cluster(driver: AlloyDBPostgresDriver, cloud: FakeAlloyDBClient) -> None:
    first = driver.provision(_spec())
    cloud.clusters[driver._cluster_name(_parse_handle(first.handle))]["labels"] = {}  # type: ignore[attr-defined]
    result = driver.provision(_spec())
    assert not result.ok
    assert result.errors == ["resource_not_owned"]


def test_existing_cluster_without_secret_is_not_healthy(
    driver: AlloyDBPostgresDriver,
    secrets_client: FakeSecretClient,
) -> None:
    first = driver.provision(_spec())
    secrets_client.secrets.clear()
    result = driver.provision(_spec())
    assert result.handle == first.handle
    assert result.errors == ["missing_master_password_secret"]


def test_provision_creates_read_pools(driver: AlloyDBPostgresDriver, cloud: FakeAlloyDBClient) -> None:
    result = driver.provision(
        _spec(
            config={
                "read_pools": [
                    {"id": "analytics", "node_count": 2, "machine_type": "n2-highmem-4"},
                    {"id": "reporting", "node_count": 1, "cpu_count": 4},
                ],
            },
        ),
    )
    assert result.ok
    assert {item["instanceType"] for item in cloud.instances.values()} == {"PRIMARY", "READ_POOL"}
    analytics = next(value for name, value in cloud.instances.items() if name.endswith("/analytics"))
    assert analytics["readPoolConfig"] == {"nodeCount": 2}


def test_status_aggregates_cluster_and_instance_state(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
) -> None:
    provisioned = driver.provision(_spec())
    assert driver.status(ServiceHandle(provisioned.handle)).state == "available"
    next(iter(cloud.instances.values()))["state"] = "MAINTENANCE"
    assert driver.status(ServiceHandle(provisioned.handle)).state == "updating"
    next(iter(cloud.instances.values()))["state"] = "FAILED"
    assert driver.status(ServiceHandle(provisioned.handle)).state == "error"
    assert driver.status(ServiceHandle("postgres/missing")).state == "deprovisioned"


def test_binding_resolves_portable_postgres_envelope(
    driver: AlloyDBPostgresDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(provisioned.handle))
    expected = {
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_SSL_MODE",
        "POSTGRES_MASTER_SECRET_REF",
        "DATABASE_URL",
        "ALLOYDB_CLUSTER",
        "ALLOYDB_INSTANCE",
        "ALLOYDB_HOST",
        "ALLOYDB_PORT",
    }
    assert expected <= binding.env_vars.keys()
    assert binding.env_vars["POSTGRES_HOST"].literal == "10.20.30.40"
    assert {grant.actions[0] for grant in binding.iam_grants} == {
        "roles/alloydb.client",
        "roles/serviceusage.serviceUsageConsumer",
    }
    backend = GCPSecretsBackend(
        config=GCPSecretsConfig(project_id="acme-prod", client=secrets_client),
    )
    url_ref = binding.env_vars["DATABASE_URL"].secret_ref
    assert url_ref
    value = backend.get(url_ref)
    assert value and "@10.20.30.40:5432/postgres?sslmode=require" in next(iter(value.values()))


def test_binding_can_select_public_or_psc_endpoint(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
) -> None:
    provisioned = driver.provision(_spec())
    primary = next(iter(cloud.instances.values()))
    primary["pscInstanceConfig"] = {"pscDnsName": "primary.alloydb-psc.goog"}
    assert (
        driver.binding(ServiceHandle(provisioned.handle), {"connectivity": "public"}).env_vars["POSTGRES_HOST"].literal
        == "34.1.2.3"
    )
    assert (
        driver.binding(ServiceHandle(provisioned.handle), {"connectivity": "psc"}).env_vars["POSTGRES_HOST"].literal
        == "primary.alloydb-psc.goog"
    )


def test_deprovision_respects_protection(driver: AlloyDBPostgresDriver) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(provisioned.handle))
    assert not result.ok and not result.retryable
    assert result.errors == ["deletion_protection_enabled"]


def test_deprovision_retains_backup_and_secrets(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(provisioned.handle),
        force_destroy=True,
    )
    assert result.ok and "backups/" in result.message
    assert cloud.backups
    assert secrets_client.secrets


def test_deprovision_delete_data_skips_backup_and_deletes_secrets(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    driver.binding(ServiceHandle(provisioned.handle))
    result = driver.deprovision(
        DeprovisionSpec(provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok and "backup=skipped" in result.message
    assert not cloud.backups
    assert not secrets_client.secrets


def test_snapshot_and_restore(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
) -> None:
    provisioned = driver.provision(_spec())
    snapshot = driver.snapshot(ServiceHandle(provisioned.handle))
    assert snapshot.snapshot_id in cloud.backups
    restored = driver.restore(snapshot, _spec(service_handle_hint="restored"))
    assert restored.ok and restored.ready
    restore_call = next(value for name, value in cloud.calls if name == "restore_cluster")
    assert restore_call["body"]["backupSource"]["backupName"] == snapshot.snapshot_id
    assert "initialUser" not in restore_call["body"]["cluster"]


def test_restore_resumes_after_cluster_creation(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
) -> None:
    provisioned = driver.provision(_spec())
    snapshot = driver.snapshot(ServiceHandle(provisioned.handle))
    target = _spec(service_handle_hint="retry-target")
    first = driver.restore(snapshot, target)
    assert first.ok
    target_id = _parse_handle(first.handle)
    primary_name = f"{driver._cluster_name(target_id)}/instances/primary"  # type: ignore[attr-defined]
    del cloud.instances[primary_name]
    retried = driver.restore(snapshot, target)
    assert retried.ok
    assert primary_name in cloud.instances


def test_update_exposes_native_cluster_and_instance_patches(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            provisioned.handle,
            size="large",
            config={
                "cluster_patch": {"maintenanceUpdatePolicy": {"maintenanceWindows": []}},
                "primary_patch": {"activationPolicy": "NEVER"},
                "query_insights": {"queryStringLength": 1024},
            },
        ),
    )
    assert result.ok
    patch = next(value for name, value in cloud.calls if name == "patch_instance")
    assert patch["body"]["machineConfig"]["machineType"] == "n2-highmem-8"
    assert patch["body"]["activationPolicy"] == "NEVER"
    assert "queryInsightsConfig" in patch["update_mask"]


def test_update_creates_patches_and_explicitly_deletes_read_pools(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
) -> None:
    provisioned = driver.provision(_spec())
    created = driver.update(
        UpdateSpec(
            provisioned.handle,
            config={"read_pools": [{"id": "analytics", "node_count": 2}]},
        ),
    )
    assert created.ok
    analytics_name = next(name for name in cloud.instances if name.endswith("/analytics"))
    patched = driver.update(
        UpdateSpec(
            provisioned.handle,
            config={"read_pools": [{"id": "analytics", "machine_type": "n2-highmem-8"}]},
        ),
    )
    assert patched.ok
    assert cloud.instances[analytics_name]["machineConfig"]["machineType"] == "n2-highmem-8"
    deleted = driver.update(
        UpdateSpec(
            provisioned.handle,
            config={"read_pools": [{"id": "analytics", "delete": True}]},
        ),
    )
    assert deleted.ok
    assert analytics_name not in cloud.instances


def test_update_cannot_overwrite_ownership_labels(
    driver: AlloyDBPostgresDriver,
    cloud: FakeAlloyDBClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            provisioned.handle,
            config={
                "cluster_patch": {
                    "labels": {"astrolift-managed-by": "attacker", "custom": "kept"},
                },
            },
        ),
    )
    assert result.ok
    cluster = next(iter(cloud.clusters.values()))
    assert cluster["labels"]["astrolift-managed-by"] == "platform"
    assert cluster["labels"]["custom"] == "kept"


def test_operation_polling_surfaces_provider_error(
    driver: AlloyDBPostgresDriver,
) -> None:
    with pytest.raises(AlloyDBError, match="quota exhausted"):
        driver._wait_operation(  # type: ignore[attr-defined]
            {"name": "operations/fail", "done": True, "error": {"message": "quota exhausted"}},
            driver._deadline(),  # type: ignore[attr-defined]
        )


def test_schema_and_password_contract(driver: AlloyDBPostgresDriver) -> None:
    schema = driver.config_schema()
    for key in (
        "cluster",
        "primary_instance",
        "read_pools",
        "network",
        "psc_enabled",
        "machine_type",
        "connection_pool",
    ):
        assert key in schema["properties"]
    assert set(driver.binding_schema().env_vars) == {
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_SSL_MODE",
        "POSTGRES_MASTER_SECRET_REF",
        "DATABASE_URL",
        "ALLOYDB_CLUSTER",
        "ALLOYDB_INSTANCE",
        "ALLOYDB_HOST",
        "ALLOYDB_PORT",
    }
    password = _generate_master_password(100)
    assert len(password) == 100
    assert not set('/@"\\ ') & set(password)


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
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.response


def test_rest_adapter_uses_documented_request_shapes() -> None:
    session = FakeSession(FakeResponse(200, {"name": "operations/1"}))
    client = AlloyDBRestClient(api_endpoint="https://example.test/v1", session=session)
    client.create_cluster("projects/p/locations/r", "db", {"databaseVersion": "POSTGRES_17"})
    method, url, kwargs = session.calls[-1]
    assert method == "POST"
    assert url == "https://example.test/v1/projects/p/locations/r/clusters"
    assert kwargs["params"] == {"clusterId": "db"}
    assert kwargs["json"]["databaseVersion"] == "POSTGRES_17"


def test_rest_adapter_maps_404_and_provider_errors() -> None:
    client = AlloyDBRestClient(
        session=FakeSession(FakeResponse(404, {"error": {"message": "missing"}}, "missing")),
    )
    with pytest.raises(AlloyDBNotFound):
        client.get_cluster("projects/p/locations/r/clusters/missing")
    client = AlloyDBRestClient(
        session=FakeSession(FakeResponse(403, {"error": {"message": "denied"}}, "denied")),
    )
    with pytest.raises(AlloyDBError, match="denied"):
        client.get_cluster("projects/p/locations/r/clusters/db")
