from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.mssql_cloudsql import (
    _ENGINE_VERSIONS,
    CloudSQLRestClient,
    CloudSQLServerConfig,
    CloudSQLServerDriver,
    CloudSQLServerError,
    CloudSQLServerNotFound,
    _generate_master_password,
    _parse_handle,
)
from gcp.secrets import GCPSecretsBackend, GCPSecretsConfig


@dataclass
class FakeResponse:
    status_code: int
    payload: dict[str, Any] | None = None
    text: str = ""

    @property
    def content(self) -> bytes:
        return b"json" if self.payload is not None else b""

    def json(self) -> dict[str, Any]:
        return dict(self.payload or {})


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class FakeSqlClient:
    def __init__(self) -> None:
        self.instances: dict[str, dict[str, Any]] = {}
        self.databases: dict[tuple[str, str], dict[str, Any]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.next_operation_error = ""
        self.pending_polls = 0
        self.backup_id = 41
        self.minimum_shrink_size_gb = 10

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))

    def _operation(self, *, backup_id: str = "") -> dict[str, Any]:
        name = f"operation-{len(self.operations) + 1}"
        final: dict[str, Any] = {"name": name, "status": "DONE"}
        if backup_id:
            final["backupContext"] = {"backupId": backup_id}
        if self.next_operation_error:
            final["error"] = {"errors": [{"code": "FAILED", "message": self.next_operation_error}]}
            self.next_operation_error = ""
        self.operations[name] = final
        if self.pending_polls:
            return {"name": name, "status": "PENDING"}
        return dict(final)

    def get_operation(self, project: str, operation: str) -> dict[str, Any]:
        self._record("get_operation", project=project, operation=operation)
        if self.pending_polls:
            self.pending_polls -= 1
            return {"name": operation, "status": "RUNNING"}
        return dict(self.operations[operation])

    def get_instance(self, project: str, instance: str) -> dict[str, Any]:
        self._record("get_instance", project=project, instance=instance)
        if instance not in self.instances:
            raise CloudSQLServerNotFound(instance)
        return self.instances[instance]

    def create_instance(self, project: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_instance", project=project, body=body)
        instance = body["name"]
        private = bool((body["settings"].get("ipConfiguration") or {}).get("privateNetwork"))
        self.instances[instance] = {
            **body,
            "state": "RUNNABLE",
            "connectionName": f"{project}:{body['region']}:{instance}",
            "ipAddresses": [
                {"type": "PRIMARY", "ipAddress": "34.1.2.3"},
                *([{"type": "PRIVATE", "ipAddress": "10.1.2.3"}] if private else []),
            ],
        }
        return self._operation()

    def patch_instance(
        self,
        project: str,
        instance: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self._record(
            "patch_instance",
            project=project,
            instance=instance,
            body=body,
            update_mask=update_mask,
        )
        if instance not in self.instances:
            raise CloudSQLServerNotFound(instance)
        settings = self.instances[instance].setdefault("settings", {})
        for key, value in (body.get("settings") or {}).items():
            settings[key] = value
        return self._operation()

    def delete_instance(self, project: str, instance: str) -> dict[str, Any]:
        self._record("delete_instance", project=project, instance=instance)
        if instance not in self.instances:
            raise CloudSQLServerNotFound(instance)
        del self.instances[instance]
        for key in [key for key in self.databases if key[0] == instance]:
            del self.databases[key]
        return self._operation()

    def get_database(self, project: str, instance: str, database: str) -> dict[str, Any]:
        self._record("get_database", project=project, instance=instance, database=database)
        try:
            return self.databases[(instance, database)]
        except KeyError as exc:
            raise CloudSQLServerNotFound(database) from exc

    def create_database(self, project: str, instance: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_database", project=project, instance=instance, body=body)
        self.databases[(instance, body["name"])] = dict(body)
        return self._operation()

    def patch_database(
        self,
        project: str,
        instance: str,
        database: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self._record(
            "patch_database",
            project=project,
            instance=instance,
            database=database,
            body=body,
        )
        if (instance, database) not in self.databases:
            raise CloudSQLServerNotFound(database)
        self.databases[(instance, database)].update(body)
        return self._operation()

    def create_backup(self, project: str, instance: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_backup", project=project, instance=instance, body=body)
        self.backup_id += 1
        return self._operation(backup_id=str(self.backup_id))

    def get_disk_shrink_config(self, project: str, instance: str) -> dict[str, Any]:
        self._record("get_disk_shrink_config", project=project, instance=instance)
        return {"min_target_size_gb": str(self.minimum_shrink_size_gb)}

    def perform_disk_shrink(
        self,
        project: str,
        instance: str,
        target_size_gb: int,
    ) -> dict[str, Any]:
        self._record(
            "perform_disk_shrink",
            project=project,
            instance=instance,
            target_size_gb=target_size_gb,
        )
        self.instances[instance]["settings"]["dataDiskSizeGb"] = target_size_gb
        return self._operation()

    def restore_backup(self, project: str, instance: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("restore_backup", project=project, instance=instance, body=body)
        restored = dict(body["restoreInstanceSettings"])
        restored["state"] = "RUNNABLE"
        restored["connectionName"] = f"{project}:{restored['region']}:{instance}"
        restored["ipAddresses"] = [{"type": "PRIVATE", "ipAddress": "10.9.8.7"}]
        self.instances[instance] = restored
        return self._operation()


class FakeSecretClient:
    def __init__(self) -> None:
        self.secrets: dict[str, list[bytes]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_secret(self, *, request: dict[str, Any]) -> None:
        self.calls.append(("create_secret", request))
        secret_id = request["secret_id"]
        if secret_id in self.secrets:
            raise RuntimeError("AlreadyExists")
        self.secrets[secret_id] = []

    def add_secret_version(self, *, request: dict[str, Any]) -> None:
        self.calls.append(("add_secret_version", request))
        secret_id = request["parent"].split("/secrets/")[-1]
        self.secrets.setdefault(secret_id, []).append(request["payload"]["data"])

    def access_secret_version(self, *, request: dict[str, Any] | None = None, name: str | None = None) -> Any:
        resource = str((request or {}).get("name") or name)
        secret_id = resource.split("/secrets/", 1)[-1].split("/versions/", 1)[0]
        versions = self.secrets.get(secret_id)
        if not versions:
            raise RuntimeError("404 not found")
        return SimpleNamespace(payload=SimpleNamespace(data=versions[-1]))

    def delete_secret(self, *, request: dict[str, Any]) -> None:
        self.calls.append(("delete_secret", request))
        secret_id = request["name"].split("/secrets/")[-1]
        self.secrets.pop(secret_id, None)


@pytest.fixture
def sql() -> FakeSqlClient:
    return FakeSqlClient()


@pytest.fixture
def secrets_client() -> FakeSecretClient:
    return FakeSecretClient()


@pytest.fixture
def driver(sql: FakeSqlClient, secrets_client: FakeSecretClient) -> CloudSQLServerDriver:
    return CloudSQLServerDriver(
        config=CloudSQLServerConfig(
            project_id="acme-prod",
            region="us-central1",
            private_network="projects/acme-prod/global/networks/main",
            poll_interval_seconds=0,
        ),
        client=sql,
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
        "service_handle_hint": "records",
        "size": "small",
        "managed_service_id": "4",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _instance_id(result: Any) -> str:
    return _parse_handle(result.handle)


def test_rest_client_uses_v1_resource_paths_and_surfaces_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": "db"}),
            FakeResponse(404, {"error": {"message": "gone"}}, "gone"),
            FakeResponse(409, {"error": {"message": "conflict"}}, "conflict"),
        ],
    )
    client = CloudSQLRestClient(endpoint="https://sql.example/v1/", session=session)
    assert client.get_instance("p", "i")["name"] == "db"
    assert session.calls[0]["url"] == "https://sql.example/v1/projects/p/instances/i"
    with pytest.raises(CloudSQLServerNotFound):
        client.get_instance("p", "missing")
    with pytest.raises(CloudSQLServerError, match="conflict"):
        client.create_instance("p", {})

    patch_session = FakeSession(
        [
            FakeResponse(200, {"name": "op", "status": "DONE"}),
            FakeResponse(200, {"min_target_size_gb": "25"}),
            FakeResponse(200, {"name": "shrink", "status": "DONE"}),
        ],
    )
    patch_client = CloudSQLRestClient(endpoint="https://sql.example/v1", session=patch_session)
    patch_client.patch_instance("p", "i", {"settings": {"tier": "db-custom-2-8192"}}, update_mask=["settings.tier"])
    assert patch_session.calls[0]["method"] == "PATCH"
    assert patch_session.calls[0]["params"] is None
    assert patch_client.get_disk_shrink_config("p", "i")["min_target_size_gb"] == "25"
    patch_client.perform_disk_shrink("p", "i", 50)
    assert patch_session.calls[1]["url"].endswith("/projects/p/instances/i/getDiskShrinkConfig")
    assert patch_session.calls[2]["url"].endswith("/projects/p/instances/i/performDiskShrink")
    assert patch_session.calls[2]["json"] == {"targetSizeGb": "50"}


def test_provision_creates_current_express_instance_database_and_secret(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
    secrets_client: FakeSecretClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok and result.ready
    instance = sql.instances[_instance_id(result)]
    assert instance["databaseVersion"] == "SQLSERVER_2025_EXPRESS"
    assert instance["rootPassword"]
    assert instance["settings"]["tier"] == "db-custom-1-3840"
    assert instance["settings"]["backupConfiguration"]["pointInTimeRecoveryEnabled"] is True
    assert instance["settings"]["ipConfiguration"]["requireSsl"] is True
    assert (_instance_id(result), "api_prod_records") in sql.databases
    assert f"astrolift-cloudsql-{_instance_id(result)}-master" in secrets_client.secrets


def test_provision_exposes_current_supported_version_matrix(driver: CloudSQLServerDriver) -> None:
    assert "SQLSERVER_2025_EXPRESS" in _ENGINE_VERSIONS
    assert "SQLSERVER_2025_STANDARD" in _ENGINE_VERSIONS
    assert "SQLSERVER_2025_ENTERPRISE" in _ENGINE_VERSIONS
    assert driver.config_schema()["properties"]["engine_version"]["enum"] == list(_ENGINE_VERSIONS)


def test_provision_selects_valid_default_tiers_for_enterprise_editions(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
) -> None:
    enterprise = driver.provision(
        _spec(
            service_handle_hint="enterprise",
            config={"engine_version": "SQLSERVER_2025_ENTERPRISE"},
        ),
    )
    plus = driver.provision(
        _spec(
            service_handle_hint="plus",
            config={
                "engine_version": "SQLSERVER_2025_ENTERPRISE",
                "cloudsql_edition": "ENTERPRISE_PLUS",
            },
        ),
    )
    assert sql.instances[_instance_id(enterprise)]["settings"]["tier"] == "db-custom-2-8192"
    assert sql.instances[_instance_id(plus)]["settings"]["tier"] == "db-perf-optimized-N-2"


def test_provision_reconciles_full_instance_and_database_options(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
) -> None:
    config = {
        "engine_version": "SQLSERVER_2025_ENTERPRISE",
        "cloudsql_edition": "ENTERPRISE_PLUS",
        "tier": "db-perf-optimized-N-4",
        "storage_gb": 512,
        "storage_type": "PD_SSD",
        "storage_auto_resize": False,
        "storage_auto_resize_limit_gb": 1024,
        "public_ipv4_enabled": True,
        "ssl_mode": "ENCRYPTED_ONLY",
        "data_cache_enabled": True,
        "high_availability": True,
        "deletion_protection": False,
        "point_in_time_recovery": True,
        "backup_retention_days": 35,
        "retained_backups_count": 120,
        "retain_backups_on_delete": True,
        "final_backup_enabled": True,
        "final_backup_retention_days": 90,
        "backup_start_time": "03:30",
        "backup_location": "us",
        "kms_key_name": "projects/p/locations/us/keyRings/r/cryptoKeys/k",
        "zone": "us-central1-a",
        "secondary_zone": "us-central1-b",
        "database_name": "Orders",
        "database_compatibility_level": 170,
        "database_recovery_model": "FULL",
        "database_flags": {"cloudsql_server_timezone": "UTC"},
        "maintenance_day": 7,
        "maintenance_hour": 6,
        "maintenance_update_track": "stable",
        "activation_policy": "ALWAYS",
        "query_insights": True,
        "query_string_length": 1048576,
        "record_application_tags": True,
        "record_client_address": True,
    }
    result = driver.provision(_spec(size="xlarge", config=config))
    assert result.ok
    instance = sql.instances[_instance_id(result)]
    settings = instance["settings"]
    assert settings["edition"] == "ENTERPRISE_PLUS"
    assert settings["tier"] == "db-perf-optimized-N-4"
    assert settings["dataDiskSizeGb"] == 512
    assert settings["storageAutoResize"] is False
    assert settings["dataCacheConfig"] == {"dataCacheEnabled": True}
    assert settings["ipConfiguration"]["ipv4Enabled"] is True
    assert settings["ipConfiguration"]["sslMode"] == "ENCRYPTED_ONLY"
    assert settings["availabilityType"] == "REGIONAL"
    assert settings["backupConfiguration"]["transactionLogRetentionDays"] == 35
    assert settings["backupConfiguration"]["backupRetentionSettings"] == {
        "retainedBackups": 120,
        "retentionUnit": "COUNT",
    }
    assert settings["retainBackupsOnDelete"] is True
    assert settings["finalBackupConfig"] == {"enabled": True, "retentionDays": 90}
    assert settings["backupConfiguration"]["startTime"] == "03:30"
    assert settings["locationPreference"]["secondaryZone"] == "us-central1-b"
    assert settings["insightsConfig"]["queryStringLength"] == 1048576
    assert instance["diskEncryptionConfiguration"]["kmsKeyName"].endswith("/k")
    database = sql.databases[(_instance_id(result), "Orders")]
    assert database["sqlserverDatabaseDetails"] == {"compatibilityLevel": 170, "recoveryModel": "FULL"}


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"engine_version": "SQLSERVER_2030_EXPRESS"}, "unsupported"),
        (
            {"engine_version": "SQLSERVER_2025_EXPRESS", "cloudsql_edition": "ENTERPRISE_PLUS"},
            "requires SQL Server 2019, 2022, or 2025 Enterprise",
        ),
        (
            {"engine_version": "SQLSERVER_2017_ENTERPRISE", "cloudsql_edition": "ENTERPRISE_PLUS"},
            "requires SQL Server 2019, 2022, or 2025 Enterprise",
        ),
        ({"backup_retention_days": 8}, "between 1 and 7"),
        ({"query_string_length": 4501}, "between 256 and 4500"),
        (
            {"engine_version": "SQLSERVER_2022_STANDARD", "database_compatibility_level": 170},
            "maximum is 160",
        ),
        ({"retained_backups_count": 366}, "between 1 and 365"),
        ({"unknown": True}, "unknown"),
    ],
)
def test_invalid_configs_fail_closed(driver: CloudSQLServerDriver, config: dict[str, Any], message: str) -> None:
    result = driver.provision(_spec(config=config))
    assert not result.ok
    assert message in result.message


def test_provision_is_idempotent_and_does_not_rotate_password(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
    secrets_client: FakeSecretClient,
) -> None:
    first = driver.provision(_spec())
    secret_id = f"astrolift-cloudsql-{_instance_id(first)}-master"
    password = secrets_client.secrets[secret_id][-1]
    second = driver.provision(_spec())
    assert second.ok and second.handle == first.handle
    assert secrets_client.secrets[secret_id] == [password]
    assert len([call for call in sql.calls if call[0] == "create_instance"]) == 1


def test_existing_instance_without_secret_is_not_claimed_healthy(
    driver: CloudSQLServerDriver,
    secrets_client: FakeSecretClient,
) -> None:
    created = driver.provision(_spec())
    secrets_client.secrets.clear()
    retry = driver.provision(_spec())
    assert not retry.ok
    assert "master secret is missing" in retry.message
    assert retry.handle == created.handle


def test_unowned_existing_instance_is_refused_without_operator_adoption(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
) -> None:
    created = driver.provision(_spec())
    sql.instances[_instance_id(created)]["settings"]["userLabels"] = {}
    denied = driver.provision(_spec())
    assert not denied.ok
    assert "operator-authorized" in denied.message

    # The flag is gone entirely, not just ignored -- adoption is
    # operator-only and no tenant config reopens it (#2021).
    rejected = driver.provision(_spec(config={"adopt_existing": True}))
    assert not rejected.ok
    assert "unknown" in rejected.message
    assert sql.instances[_instance_id(created)]["settings"]["userLabels"] == {}


def test_operation_polling_and_operation_failure_are_real_contracts(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
) -> None:
    sql.pending_polls = 2
    created = driver.provision(_spec())
    assert created.ok
    assert len([call for call in sql.calls if call[0] == "get_operation"]) >= 2

    other_sql = FakeSqlClient()
    other_sql.next_operation_error = "quota exhausted"
    failed = CloudSQLServerDriver(
        config=driver._config,  # type: ignore[attr-defined]
        client=other_sql,
        secrets_client=FakeSecretClient(),
        sleep=lambda _: None,
    ).provision(_spec(service_handle_hint="failed"))
    assert not failed.ok and "quota exhausted" in failed.message


def test_binding_prefers_private_ip_and_emits_portable_mssql_envelope(
    driver: CloudSQLServerDriver,
) -> None:
    created = driver.provision(_spec(config={"database_name": "Orders"}))
    binding = driver.binding(ServiceHandle(created.handle), {"database_name": "Orders"})
    env = binding.env_vars
    assert env["MSSQL_HOST"].literal == "10.1.2.3"
    assert env["MSSQL_PORT"].literal == "1433"
    assert env["MSSQL_DB"].literal == "Orders"
    assert env["MSSQL_USER"].literal == "sqlserver"
    assert env["MSSQL_PASSWORD"].secret_ref
    assert env["MSSQL_ENCRYPT"].literal == "true"
    assert env["DATABASE_URL"].secret_ref
    assert env["GCP_CLOUDSQL_CONNECTION_NAME"].literal == "acme-prod:us-central1:astrolift-acme-api-prod-records"
    assert binding.iam_grants[0].actions == ["roles/cloudsql.client"]


def test_binding_secrets_resolve_through_gcp_backend(
    driver: CloudSQLServerDriver,
) -> None:
    created = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(created.handle))
    backend = GCPSecretsBackend(
        config=GCPSecretsConfig(project_id="acme-prod", client=driver._sm),  # type: ignore[attr-defined]
    )
    password = binding.env_vars["MSSQL_PASSWORD"].secret_ref
    url = binding.env_vars["DATABASE_URL"].secret_ref
    assert password and backend.get(password)
    assert url
    value = next(iter(backend.get(url).values()))
    assert value.startswith("mssql://sqlserver:")
    assert "@10.1.2.3:1433/api_prod_records?encrypt=true" in value


def test_binding_requires_reachable_endpoint(driver: CloudSQLServerDriver, sql: FakeSqlClient) -> None:
    created = driver.provision(_spec())
    sql.instances[_instance_id(created)]["ipAddresses"] = []
    with pytest.raises(CloudSQLServerError, match="no reachable IP"):
        driver.binding(ServiceHandle(created.handle))


@pytest.mark.parametrize(
    ("cloud_state", "protocol_state"),
    [
        ("RUNNABLE", "available"),
        ("PENDING_CREATE", "provisioning"),
        ("MAINTENANCE", "updating"),
        ("FAILED", "error"),
        ("SUSPENDED", "error"),
        ("PENDING_DELETE", "deprovisioning"),
    ],
)
def test_status_maps_cloud_sql_states(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
    cloud_state: str,
    protocol_state: str,
) -> None:
    created = driver.provision(_spec())
    sql.instances[_instance_id(created)]["state"] = cloud_state
    assert driver.status(ServiceHandle(created.handle)).state == protocol_state


def test_status_missing_is_deprovisioned(driver: CloudSQLServerDriver) -> None:
    assert driver.status(ServiceHandle("mssql/missing")).state == "deprovisioned"


def test_update_reconciles_mutable_settings_and_database(driver: CloudSQLServerDriver, sql: FakeSqlClient) -> None:
    created = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=created.handle,
            size="medium",
            config={
                "database_name": "api_prod_records",
                "high_availability": True,
                "deletion_protection": False,
                "backup_retention_days": 5,
                "retained_backups_count": 30,
                "retain_backups_on_delete": False,
                "final_backup_enabled": False,
                "final_backup_retention_days": 45,
                "storage_auto_resize": False,
                "database_flags": {"cloudsql_server_timezone": "UTC"},
                "query_insights": True,
                "database_compatibility_level": 150,
                "database_recovery_model": "SIMPLE",
                "point_in_time_recovery": False,
            },
        ),
    )
    assert result.ok
    settings = sql.instances[_instance_id(created)]["settings"]
    assert settings["tier"] == "db-custom-2-8192"
    assert settings["availabilityType"] == "REGIONAL"
    assert settings["deletionProtectionEnabled"] is False
    assert settings["backupConfiguration"]["transactionLogRetentionDays"] == 5
    assert settings["backupConfiguration"]["pointInTimeRecoveryEnabled"] is False
    assert settings["backupConfiguration"]["backupRetentionSettings"]["retainedBackups"] == 30
    assert settings["retainBackupsOnDelete"] is False
    assert settings["finalBackupConfig"] == {"enabled": False, "retentionDays": 45}
    assert settings["databaseFlags"] == [{"name": "cloudsql_server_timezone", "value": "UTC"}]
    assert settings["insightsConfig"]["queryInsightsEnabled"] is True
    assert sql.databases[(_instance_id(created), "api_prod_records")]["sqlserverDatabaseDetails"] == {
        "compatibilityLevel": 150,
        "recoveryModel": "SIMPLE",
    }


@pytest.mark.parametrize("recovery_model", ["SIMPLE", "BULK_LOGGED"])
def test_pitr_rejects_recovery_models_that_break_the_log_chain(
    driver: CloudSQLServerDriver,
    recovery_model: str,
) -> None:
    result = driver.provision(_spec(config={"database_recovery_model": recovery_model}))
    assert not result.ok
    assert "must be FULL" in result.message


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"data_cache_enabled": True}, "ENTERPRISE_PLUS"),
        ({"authorized_networks": [{"name": "office", "value": "10.0.0.0/8"}]}, "public_ipv4_enabled"),
    ],
)
def test_rejects_incompatible_network_and_cache_options(
    driver: CloudSQLServerDriver,
    config: dict[str, Any],
    message: str,
) -> None:
    result = driver.provision(_spec(config=config))
    assert not result.ok
    assert message in result.message


def test_update_rejects_immutable_engine_and_implicit_storage_shrink(
    driver: CloudSQLServerDriver,
) -> None:
    created = driver.provision(_spec(config={"storage_gb": 100}))
    engine = driver.update(
        UpdateSpec(handle=created.handle, config={"engine_version": "SQLSERVER_2022_EXPRESS"}),
    )
    shrink = driver.update(UpdateSpec(handle=created.handle, config={"storage_gb": 50}))
    assert not engine.ok and "immutable" in engine.message
    assert not shrink.ok and "allow_storage_shrink" in shrink.message


def test_update_and_reprovision_reject_database_name_changes(driver: CloudSQLServerDriver) -> None:
    created = driver.provision(_spec(config={"database_name": "Orders"}))
    updated = driver.update(UpdateSpec(handle=created.handle, config={"database_name": "Customers"}))
    reprovisioned = driver.provision(_spec(config={"database_name": "Customers"}))
    assert not updated.ok and "database_name is immutable" in updated.message
    assert not reprovisioned.ok and "database_name is immutable" in reprovisioned.message


def test_update_allows_explicit_storage_shrink(driver: CloudSQLServerDriver, sql: FakeSqlClient) -> None:
    created = driver.provision(_spec(config={"storage_gb": 100}))
    changed = driver.update(
        UpdateSpec(handle=created.handle, config={"storage_gb": 50, "allow_storage_shrink": True}),
    )
    assert changed.ok
    assert sql.instances[_instance_id(created)]["settings"]["dataDiskSizeGb"] == 50
    calls = [name for name, _ in sql.calls]
    assert calls.index("get_disk_shrink_config") < calls.index("create_backup") < calls.index("perform_disk_shrink")


def test_update_rejects_storage_shrink_below_cloud_preflight_minimum(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
) -> None:
    created = driver.provision(_spec(config={"storage_gb": 100}))
    sql.minimum_shrink_size_gb = 75
    sql.calls.clear()
    changed = driver.update(
        UpdateSpec(handle=created.handle, config={"storage_gb": 50, "allow_storage_shrink": True}),
    )
    assert not changed.ok
    assert "safe minimum of 75" in changed.message
    assert not any(name in {"create_backup", "perform_disk_shrink"} for name, _ in sql.calls)


def test_deprovision_protection_requires_force(driver: CloudSQLServerDriver) -> None:
    created = driver.provision(_spec())
    denied = driver.deprovision(DeprovisionSpec(created.handle))
    assert not denied.ok and denied.errors == ["deletion_protection_enabled"]


def test_safe_deprovision_waits_for_exact_backup_then_deletes(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
) -> None:
    created = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(created.handle), force_destroy=True)
    assert result.ok and "retained backup acme-prod/" in result.message
    operations = [name for name, _ in sql.calls]
    assert operations.index("create_backup") < operations.index("delete_instance")
    assert _instance_id(created) not in sql.instances


def test_safe_deprovision_enables_backup_retention_before_snapshot(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
) -> None:
    created = driver.provision(
        _spec(
            service_handle_hint="retention",
            config={"deletion_protection": False, "retain_backups_on_delete": False},
        ),
    )
    sql.calls.clear()
    result = driver.deprovision(DeprovisionSpec(created.handle))
    assert result.ok
    calls = [name for name, _ in sql.calls]
    assert calls.index("patch_instance") < calls.index("create_backup") < calls.index("delete_instance")
    patch = next(kwargs for name, kwargs in sql.calls if name == "patch_instance")
    assert patch["body"] == {"settings": {"retainBackupsOnDelete": True}}


def test_delete_data_skips_backup_and_removes_secrets(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
    secrets_client: FakeSecretClient,
) -> None:
    created = driver.provision(_spec(config={"deletion_protection": False}))
    driver.binding(ServiceHandle(created.handle))
    sql.calls.clear()
    result = driver.deprovision(DeprovisionSpec(created.handle), delete_data=True)
    assert result.ok
    assert not any(name == "create_backup" for name, _ in sql.calls)
    assert not secrets_client.secrets


def test_adopted_deletion_requires_separate_delete_ack(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
) -> None:
    created = driver.provision(_spec(config={"deletion_protection": False}))
    sql.instances[_instance_id(created)]["settings"]["userLabels"] = {}
    denied = driver.deprovision(DeprovisionSpec(created.handle), delete_data=True)
    allowed = driver.deprovision(
        DeprovisionSpec(created.handle, config={"delete_adopted": True}),
        delete_data=True,
    )
    assert not denied.ok and "delete_adopted" in denied.message
    assert allowed.ok


def test_snapshot_returns_exact_backup_identity(driver: CloudSQLServerDriver) -> None:
    created = driver.provision(_spec())
    snapshot = driver.snapshot(ServiceHandle(created.handle))
    assert snapshot.snapshot_id.startswith(f"acme-prod/{_instance_id(created)}/")
    assert snapshot.snapshot_id.rsplit("/", 1)[-1].isdigit()


def test_restore_uses_exact_source_project_instance_and_backup(
    driver: CloudSQLServerDriver,
    sql: FakeSqlClient,
) -> None:
    source = driver.provision(_spec(config={"database_name": "Orders"}))
    snapshot = driver.snapshot(ServiceHandle(source.handle))
    restored = driver.restore(snapshot, _spec(service_handle_hint="restored"))
    assert restored.ok and restored.ready
    call = next(kwargs for name, kwargs in sql.calls if name == "restore_backup")
    context = call["body"]["restoreBackupContext"]
    assert context == {
        "backupRunId": snapshot.snapshot_id.rsplit("/", 1)[-1],
        "instanceId": _instance_id(source),
        "project": "acme-prod",
    }
    assert _instance_id(restored) in sql.instances
    assert (_instance_id(restored), "Orders") in sql.databases
    binding = driver.binding(ServiceHandle(restored.handle))
    assert binding.env_vars["MSSQL_DB"].literal == "Orders"


def test_restore_rejects_ambiguous_snapshot_and_missing_source_secret(
    driver: CloudSQLServerDriver,
    secrets_client: FakeSecretClient,
) -> None:
    invalid = driver.restore(
        SimpleNamespace(snapshot_id="description-only"),
        _spec(service_handle_hint="bad"),
    )
    assert not invalid.ok and invalid.errors == ["invalid_snapshot"]
    source = driver.provision(_spec())
    snapshot = driver.snapshot(ServiceHandle(source.handle))
    secrets_client.secrets.clear()
    missing = driver.restore(snapshot, _spec(service_handle_hint="missing"))
    assert not missing.ok and missing.errors == ["missing_master_password_secret"]


def test_password_meets_sql_server_complexity() -> None:
    password = _generate_master_password(64)
    assert len(password) == 64
    assert any(char.isupper() for char in password)
    assert any(char.islower() for char in password)
    assert any(char.isdigit() for char in password)
    assert any(char in "-_.!#$%" for char in password)
    assert not set('/@"\\ ').intersection(password)


def test_handle_validation_is_kind_specific() -> None:
    assert _parse_handle("mssql/example") == "example"
    for invalid in ("example", "mysql/example", "mssql/"):
        with pytest.raises(ValueError):
            _parse_handle(invalid)


def test_binding_schema_matches_cloud_neutral_contract(driver: CloudSQLServerDriver) -> None:
    schema = driver.binding_schema()
    for name in (
        "MSSQL_HOST",
        "MSSQL_PORT",
        "MSSQL_DB",
        "MSSQL_USER",
        "MSSQL_PASSWORD",
        "MSSQL_ENCRYPT",
        "DATABASE_URL",
        "GCP_CLOUDSQL_INSTANCE",
        "GCP_CLOUDSQL_CONNECTION_NAME",
    ):
        assert name in schema.env_vars


def test_provision_does_not_adopt_another_services_resource(driver) -> None:
    """Names are slug-joined, so another service can map to this one's name (#1961)."""
    import dataclasses

    first = driver.provision(dataclasses.replace(_spec(), managed_service_id="svc-a"))
    second = driver.provision(dataclasses.replace(_spec(), managed_service_id="svc-b"))

    assert first.ok, first.message
    assert not second.ok and "refusing to adopt" in second.message
