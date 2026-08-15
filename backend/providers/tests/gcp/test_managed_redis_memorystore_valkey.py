from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from gcp.managed.redis_memorystore_valkey import (
    MemorystoreValkeyConfig,
    MemorystoreValkeyDriver,
    MemorystoreValkeyNotFound,
    MemorystoreValkeyRestClient,
    _connection_endpoints,
    _parse_handle,
)


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


class FakeValkeyClient:
    def __init__(self) -> None:
        self.instances: dict[str, dict[str, Any]] = {}
        self.backups: dict[str, dict[str, Any]] = {}
        self.token_users: dict[str, dict[str, Any]] = {}
        self.auth_tokens: dict[str, dict[str, Any]] = {}
        self.auth_token_sequence = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_delete = False

    def _record(self, action: str, **kwargs: Any) -> None:
        self.calls.append((action, kwargs))

    def _operation(self, response: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "name": f"projects/acme/locations/us-central1/operations/{len(self.calls)}",
            "done": True,
            "response": response or {},
        }

    def get_operation(self, name: str) -> dict[str, Any]:
        self._record("get_operation", name=name)
        return {"name": name, "done": True}

    def get_instance(self, name: str) -> dict[str, Any]:
        self._record("get_instance", name=name)
        if name not in self.instances:
            raise MemorystoreValkeyNotFound(name)
        return deepcopy(self.instances[name])

    def create_instance(self, parent: str, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_instance", parent=parent, instance_id=instance_id, body=deepcopy(body))
        name = f"{parent}/instances/{instance_id}"
        materialized = deepcopy(body)
        replication = materialized.get("crossInstanceReplicationConfig") or {}
        if replication.get("instanceRole") == "SECONDARY":
            primary = self.instances[replication["primaryInstance"]["instance"]]
            for field in (
                "authorizationMode",
                "transitEncryptionMode",
                "shardCount",
                "nodeType",
                "engineVersion",
                "engineConfigs",
                "mode",
                "persistenceConfig",
            ):
                if field in primary:
                    materialized[field] = deepcopy(primary[field])
        endpoints = deepcopy(body["endpoints"])
        for index, endpoint in enumerate(endpoints):
            for connection in endpoint["connections"]:
                row = connection.get("pscAutoConnection") or connection.get("pscConnection")
                row.update(
                    {
                        "ipAddress": f"10.0.{index}.10",
                        "port": 6379,
                        "connectionType": "CONNECTION_TYPE_DISCOVERY",
                        "pscConnectionStatus": "ACTIVE",
                    }
                )
        instance = {
            **materialized,
            "name": name,
            "uid": f"uid-{instance_id}",
            "state": "ACTIVE",
            "endpoints": endpoints,
            "backupCollection": f"{parent}/backupCollections/{instance_id}",
            "availableMaintenanceVersions": ["20260801_00_00"],
        }
        self.instances[name] = instance
        if instance.get("authorizationMode") == "TOKEN_AUTH":
            self._create_token_user(name, "default")
        return self._operation(instance)

    def patch_instance(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        self._record("patch_instance", name=name, body=deepcopy(body), update_mask=list(update_mask))
        self.instances[name].update(deepcopy(body))
        return self._operation(self.instances[name])

    def delete_instance(self, name: str) -> dict[str, Any]:
        self._record("delete_instance", name=name)
        if self.fail_delete:
            raise RuntimeError("delete unavailable")
        if name not in self.instances:
            raise MemorystoreValkeyNotFound(name)
        self.instances.pop(name)
        return self._operation()

    def backup_instance(self, name: str, backup_id: str, ttl: str) -> dict[str, Any]:
        self._record("backup_instance", name=name, backup_id=backup_id, ttl=ttl)
        collection = self.instances[name]["backupCollection"]
        backup_name = f"{collection}/backups/{backup_id}"
        backup = {
            "name": backup_name,
            "instance": name,
            "state": "READY",
            "createTime": "2026-08-14T12:00:00Z",
        }
        self.backups[backup_name] = backup
        return self._operation(backup)

    def get_backup(self, name: str) -> dict[str, Any]:
        self._record("get_backup", name=name)
        if name not in self.backups:
            raise MemorystoreValkeyNotFound(name)
        return deepcopy(self.backups[name])

    def list_backups(self, backup_collection: str) -> list[dict[str, Any]]:
        self._record("list_backups", backup_collection=backup_collection)
        return [row for name, row in self.backups.items() if name.startswith(f"{backup_collection}/backups/")]

    def get_certificate_authority(self, name: str) -> dict[str, Any]:
        self._record("get_certificate_authority", name=name)
        return {"managedServerCa": {"caCerts": [{"certificates": ["CERT-LEAF", "CERT-ROOT"]}]}}

    def get_shared_certificate_authority(self, region: str) -> str:
        self._record("get_shared_certificate_authority", region=region)
        return "SHARED-CA-BUNDLE"

    def list_token_auth_users(self, instance: str) -> list[dict[str, Any]]:
        self._record("list_token_auth_users", instance=instance)
        return [deepcopy(row) for name, row in self.token_users.items() if name.startswith(f"{instance}/")]

    def add_token_auth_user(self, instance: str, username: str) -> dict[str, Any]:
        self._record("add_token_auth_user", instance=instance, username=username)
        self._create_token_user(instance, username)
        return self._operation()

    def list_auth_tokens(self, token_auth_user: str) -> list[dict[str, Any]]:
        self._record("list_auth_tokens", token_auth_user=token_auth_user)
        return [
            deepcopy(row) for name, row in self.auth_tokens.items() if name.startswith(f"{token_auth_user}/authTokens/")
        ]

    def get_auth_token(self, name: str) -> dict[str, Any]:
        self._record("get_auth_token", name=name)
        if name not in self.auth_tokens:
            raise MemorystoreValkeyNotFound(name)
        return deepcopy(self.auth_tokens[name])

    def add_auth_token(self, token_auth_user: str) -> dict[str, Any]:
        self._record("add_auth_token", token_auth_user=token_auth_user)
        self._create_auth_token(token_auth_user)
        return self._operation()

    def delete_auth_token(self, name: str) -> dict[str, Any]:
        self._record("delete_auth_token", name=name)
        if name not in self.auth_tokens:
            raise MemorystoreValkeyNotFound(name)
        self.auth_tokens.pop(name)
        return self._operation()

    def _create_token_user(self, instance: str, username: str) -> None:
        name = f"{instance}/tokenAuthUsers/{username}"
        self.token_users[name] = {"name": name, "state": "ACTIVE"}
        self._create_auth_token(name)

    def _create_auth_token(self, token_auth_user: str) -> dict[str, Any]:
        self.auth_token_sequence += 1
        token_id = f"token-{self.auth_token_sequence}"
        name = f"{token_auth_user}/authTokens/{token_id}"
        row = {
            "name": name,
            "token": f"secret-{token_auth_user.rsplit('/', 1)[-1]}-{token_id}",
            "createTime": f"2026-08-14T12:00:{self.auth_token_sequence:02d}Z",
            "state": "ACTIVE",
        }
        self.auth_tokens[name] = row
        return row


class FakeSecretClient:
    def __init__(self) -> None:
        self.secrets: dict[str, list[bytes]] = {}
        self.fail_next_write = False

    def create_secret(self, *, request: dict[str, Any]) -> None:
        secret_id = request["secret_id"]
        if secret_id in self.secrets:
            raise RuntimeError("AlreadyExists")
        self.secrets[secret_id] = []

    def add_secret_version(self, *, request: dict[str, Any]) -> None:
        if self.fail_next_write:
            self.fail_next_write = False
            raise RuntimeError("Secret Manager unavailable")
        secret_id = request["parent"].split("/secrets/", 1)[-1]
        self.secrets.setdefault(secret_id, []).append(request["payload"]["data"])

    def access_secret_version(self, *, request: dict[str, Any]) -> Any:
        secret_id = request["name"].split("/secrets/", 1)[-1].split("/versions/", 1)[0]
        versions = self.secrets.get(secret_id)
        if not versions:
            raise RuntimeError("404 not found")
        return SimpleNamespace(payload=SimpleNamespace(data=versions[-1]))

    def delete_secret(self, *, request: dict[str, Any]) -> None:
        secret_id = request["name"].split("/secrets/", 1)[-1]
        self.secrets.pop(secret_id, None)


@pytest.fixture
def client() -> FakeValkeyClient:
    return FakeValkeyClient()


@pytest.fixture
def secrets_client() -> FakeSecretClient:
    return FakeSecretClient()


@pytest.fixture
def driver(client: FakeValkeyClient, secrets_client: FakeSecretClient) -> MemorystoreValkeyDriver:
    return MemorystoreValkeyDriver(
        config=MemorystoreValkeyConfig(project_id="acme", region="us-central1", poll_interval_seconds=0),
        client=client,
        secrets_client=secrets_client,
        sleep=lambda _: None,
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    values: dict[str, Any] = {
        "organization_id": "1",
        "organization_slug": "steadymd",
        "app_id": "2",
        "app_slug": "triage",
        "environment_id": "3",
        "environment_name": "prod",
        "tenant_cluster_id": "gcp-prod",
        "service_handle_hint": "cache",
        "size": "medium",
        "binding_id": "binding-1",
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def test_rest_client_uses_v1_shapes_and_deterministic_request_ids() -> None:
    session = FakeSession([FakeResponse(200, {"done": True}) for _ in range(4)])
    rest = MemorystoreValkeyRestClient(endpoint="https://memorystore.example/v1/", session=session)
    parent = "projects/p/locations/r"
    name = f"{parent}/instances/i"
    rest.create_instance(parent, "i", {"nodeType": "STANDARD_SMALL"})
    rest.create_instance(parent, "i", {"nodeType": "STANDARD_SMALL"})
    rest.patch_instance(name, {"shardCount": 2}, update_mask=["shardCount"])
    rest.delete_instance(name)

    assert session.calls[0]["url"] == "https://memorystore.example/v1/projects/p/locations/r/instances"
    assert session.calls[0]["params"]["instanceId"] == "i"
    assert session.calls[0]["params"]["requestId"] == session.calls[1]["params"]["requestId"]
    assert session.calls[2]["params"]["updateMask"] == "shardCount"
    assert session.calls[2]["json"] == {"name": name, "shardCount": 2}
    assert "requestId" in session.calls[3]["params"]


def test_rest_client_maps_404_and_backup_paths() -> None:
    session = FakeSession(
        [
            FakeResponse(404, {"error": {"message": "missing"}}),
            FakeResponse(200, {"name": "op"}),
            FakeResponse(200, {"backups": [{"name": "b"}]}),
            FakeResponse(200, {"managedServerCa": {}}),
        ]
    )
    rest = MemorystoreValkeyRestClient(endpoint="https://memorystore.example/v1", session=session)
    name = "projects/p/locations/r/instances/i"
    with pytest.raises(MemorystoreValkeyNotFound):
        rest.get_instance(name)
    rest.backup_instance(name, "backup-1", "86400s")
    assert session.calls[1]["url"].endswith("/instances/i:backup")
    assert session.calls[1]["json"] == {"backupId": "backup-1", "ttl": "86400s"}
    assert rest.list_backups("projects/p/locations/r/backupCollections/c")[0]["name"] == "b"
    rest.get_certificate_authority(name)
    assert session.calls[3]["url"].endswith("/instances/i/certificateAuthority")


def test_rest_client_uses_stable_v1_token_auth_shapes() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"tokenAuthUsers": []}),
            FakeResponse(200, {"name": "operations/user"}),
            FakeResponse(200, {"authTokens": []}),
            FakeResponse(200, {"name": "operations/token"}),
            FakeResponse(200, {"name": "operations/delete"}),
        ]
    )
    rest = MemorystoreValkeyRestClient(endpoint="https://memorystore.example/v1", session=session)
    instance = "projects/p/locations/r/instances/i"
    user = f"{instance}/tokenAuthUsers/default"
    token = f"{user}/authTokens/token-1"

    rest.list_token_auth_users(instance)
    rest.add_token_auth_user(instance, "default")
    rest.list_auth_tokens(user)
    rest.add_auth_token(user)
    rest.delete_auth_token(token)

    assert session.calls[0]["url"].endswith("/instances/i/tokenAuthUsers")
    assert session.calls[1]["url"].endswith("/instances/i:addTokenAuthUser")
    assert session.calls[1]["json"] == {"tokenAuthUser": "default"}
    assert session.calls[2]["url"].endswith("/tokenAuthUsers/default/authTokens")
    assert session.calls[3]["url"].endswith("/tokenAuthUsers/default:addAuthToken")
    assert session.calls[3]["json"] == {"authToken": {}}
    assert session.calls[4]["method"] == "DELETE"


def test_rest_client_downloads_the_documented_shared_ca_bundle() -> None:
    session = FakeSession([FakeResponse(200, text="SHARED-CA")])
    rest = MemorystoreValkeyRestClient(endpoint="https://memorystore.example/v1", session=session)
    assert rest.get_shared_certificate_authority("us-central1") == "SHARED-CA"
    assert session.calls[0]["url"].endswith("/prod/regions/us-central1/ca_bundle.pem")


def test_provision_builds_secure_current_valkey_instance(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok and result.ready
    instance_id = _parse_handle(result.handle)
    call = next(kwargs for action, kwargs in client.calls if action == "create_instance")
    body = call["body"]
    assert body["engineVersion"] == "VALKEY_9_0"
    assert body["authorizationMode"] == "IAM_AUTH"
    assert body["transitEncryptionMode"] == "SERVER_AUTHENTICATION"
    assert body["mode"] == "CLUSTER"
    assert body["nodeType"] == "HIGHMEM_MEDIUM"
    assert body["persistenceConfig"]["mode"] == "RDB"
    assert body["automatedBackupConfig"]["retention"] == "3024000s"
    assert body["deletionProtectionEnabled"] is True
    assert "allowFewerZonesDeployment" not in body
    assert body["labels"]["astrolift-binding"] == "binding-1"
    assert body["endpoints"][0]["connections"][0]["pscAutoConnection"]["network"].endswith("/default")
    assert instance_id in result.handle


def test_provision_is_idempotent_and_reconciles_mutable_fields(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    first = driver.provision(_spec())
    second = driver.provision(_spec(config={"shard_count": 3, "replica_count": 2}))
    assert first.handle == second.handle and second.ok
    patch = next(kwargs for action, kwargs in client.calls if action == "patch_instance")
    assert patch["body"] == {"shardCount": 3, "replicaCount": 2}
    assert patch["update_mask"] == ["shardCount", "replicaCount"]


def test_output_only_psc_and_maintenance_fields_do_not_create_perpetual_drift(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    cfg = {
        "psc_networks": [
            "projects/acme/global/networks/cache-a",
            "projects/acme/global/networks/cache-b",
        ],
        "maintenance_day": "SUNDAY",
        "maintenance_hour_utc": 3,
    }
    first = driver.provision(_spec(config=cfg))
    name = next(iter(client.instances))
    client.instances[name]["endpoints"].reverse()
    client.instances[name]["maintenancePolicy"]["createTime"] = "2026-08-14T12:00:00Z"
    client.calls.clear()
    second = driver.provision(_spec(config=cfg))
    assert first.ok and second.ok
    assert not any(action == "patch_instance" for action, _ in client.calls)


def test_existing_unowned_instance_requires_explicit_adoption(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    first = driver.provision(_spec())
    name = next(iter(client.instances))
    client.instances[name]["labels"] = {}
    refused = driver.provision(_spec())
    adopted = driver.provision(_spec(config={"adopt_existing_instance": True}))
    assert first.ok and not refused.ok and "adopt_existing_instance" in refused.message
    assert adopted.ok


def test_immutable_drift_fails_closed(driver: MemorystoreValkeyDriver) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(provisioned.handle, config={"mode": "CLUSTER_DISABLED"}))
    assert not result.ok and "immutable mode mismatch" in result.message


def test_partial_update_does_not_compare_omitted_immutable_defaults(
    driver: MemorystoreValkeyDriver,
) -> None:
    provisioned = driver.provision(_spec(config={"authorization_mode": "AUTH_DISABLED", "transit_encryption": False}))
    result = driver.update(UpdateSpec(provisioned.handle, config={"shard_count": 2}))
    assert result.ok


def test_partial_nested_updates_preserve_existing_schedule_fields(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    provisioned = driver.provision(
        _spec(
            config={
                "rdb_snapshot_period": "ONE_HOUR",
                "rdb_snapshot_start_time": "2026-08-14T08:00:00Z",
                "backup_start_hour_utc": 7,
                "maintenance_day": "TUESDAY",
                "maintenance_hour_utc": 8,
                "maintenance_minute_utc": 30,
            }
        )
    )
    client.calls.clear()
    assert driver.update(UpdateSpec(provisioned.handle, config={"backup_retention_days": 60})).ok
    backup_patch = next(kwargs for action, kwargs in client.calls if action == "patch_instance")
    start = backup_patch["body"]["automatedBackupConfig"]["fixedFrequencySchedule"]["startTime"]
    assert start == {"hours": 7, "minutes": 0, "seconds": 0, "nanos": 0}

    client.calls.clear()
    assert driver.update(UpdateSpec(provisioned.handle, config={"maintenance_minute_utc": 45})).ok
    maintenance_patch = next(kwargs for action, kwargs in client.calls if action == "patch_instance")
    window = maintenance_patch["body"]["maintenancePolicy"]["weeklyMaintenanceWindow"][0]
    assert window["day"] == "TUESDAY"
    assert window["startTime"]["hours"] == 8
    assert window["startTime"]["minutes"] == 45

    client.calls.clear()
    assert driver.update(
        UpdateSpec(
            provisioned.handle,
            config={
                "persistence_mode": "RDB",
                "rdb_snapshot_start_time": "2026-08-15T09:00:00Z",
            },
        )
    ).ok
    persistence_patch = next(kwargs for action, kwargs in client.calls if action == "patch_instance")
    assert persistence_patch["body"]["persistenceConfig"]["rdbConfig"]["rdbSnapshotPeriod"] == "ONE_HOUR"


def test_customer_ca_acl_and_maintenance_options_are_preserved(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    config = {
        "allow_preview_features": True,
        "server_ca_mode": "CUSTOMER_MANAGED_CAS_CA",
        "server_ca_pool": "projects/security/locations/us-central1/caPools/valkey",
        "acl_policy": "projects/acme/locations/us-central1/aclPolicies/readers",
        "maintenance_version": "20260801_00_00",
    }
    provisioned = driver.provision(_spec(config=config))
    assert provisioned.ok
    body = next(kwargs for action, kwargs in client.calls if action == "create_instance")["body"]
    assert body["serverCaMode"] == "CUSTOMER_MANAGED_CAS_CA"
    assert body["serverCaPool"].endswith("/caPools/valkey")
    assert body["aclPolicy"].endswith("/aclPolicies/readers")
    assert "maintenanceVersion" not in body
    maintenance_patch = next(
        kwargs
        for action, kwargs in client.calls
        if action == "patch_instance" and "maintenanceVersion" in kwargs["body"]
    )
    assert maintenance_patch["body"]["maintenanceVersion"] == "20260801_00_00"
    binding = driver.binding(ServiceHandle(provisioned.handle))
    assert binding.env_vars["GCP_MEMORYSTORE_SERVER_CA_MODE"].literal == "CUSTOMER_MANAGED_CAS_CA"
    assert "REDIS_CA_CERT" not in binding.env_vars

    client.calls.clear()
    assert driver.update(UpdateSpec(provisioned.handle, config={"clear_acl_policy": True})).ok
    patch = next(kwargs for action, kwargs in client.calls if action == "patch_instance")
    assert patch["body"]["aclPolicy"] is None


def test_shared_ca_binding_uses_regional_bundle(
    driver: MemorystoreValkeyDriver,
) -> None:
    provisioned = driver.provision(_spec(config={"server_ca_mode": "GOOGLE_MANAGED_SHARED_CA"}))
    assert provisioned.ok
    binding = driver.binding(ServiceHandle(provisioned.handle))
    assert binding.env_vars["REDIS_CA_CERT"].literal == "SHARED-CA-BUNDLE"


def test_delete_failure_restores_deletion_protection(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    provisioned = driver.provision(_spec())
    client.fail_delete = True
    result = driver.deprovision(DeprovisionSpec(provisioned.handle), delete_data=True, force_destroy=True)
    assert not result.ok and result.retryable
    patches = [kwargs for action, kwargs in client.calls if action == "patch_instance"]
    assert patches[-1]["body"] == {"deletionProtectionEnabled": True}


def test_update_supports_scaling_persistence_maintenance_and_replication(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            provisioned.handle,
            config={
                "node_type": "HIGHMEM_XLARGE",
                "shard_count": 4,
                "replica_count": 2,
                "persistence_mode": "AOF",
                "aof_append_fsync": "ALWAYS",
                "maintenance_day": "SATURDAY",
                "maintenance_hour_utc": 5,
                "automated_backup": True,
                "backup_retention_days": 60,
                "cross_instance_role": "PRIMARY",
                "secondary_instances": ["projects/acme/locations/europe-west1/instances/secondary"],
            },
        )
    )
    assert result.ok
    patch = [kwargs for action, kwargs in client.calls if action == "patch_instance"][-1]
    assert patch["body"]["nodeType"] == "HIGHMEM_XLARGE"
    assert patch["body"]["persistenceConfig"] == {"mode": "AOF", "aofConfig": {"appendFsync": "ALWAYS"}}
    assert patch["body"]["maintenancePolicy"]["weeklyMaintenanceWindow"][0]["day"] == "SATURDAY"
    assert patch["body"]["automatedBackupConfig"]["retention"] == "5184000s"
    assert patch["body"]["labels"]["astrolift-backup-days"] == "60"
    assert patch["body"]["crossInstanceReplicationConfig"]["instanceRole"] == "PRIMARY"


def test_binding_emits_psc_iam_tls_and_ca_without_static_password(
    driver: MemorystoreValkeyDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(provisioned.handle))
    env = binding.env_vars
    assert env["REDIS_HOST"].literal == "10.0.0.10"
    assert env["REDIS_URL"].literal == "rediss://10.0.0.10:6379"
    assert env["REDIS_USER"].literal == "default"
    assert env["REDIS_AUTH_MODE"].literal == "gcp_iam"
    assert env["REDIS_CA_CERT"].literal == "CERT-LEAF\nCERT-ROOT"
    assert "REDIS_PASSWORD" not in env
    assert binding.iam_grants[0].actions == ["roles/memorystore.dbConnectionUser"]
    assert "short-lived" in binding.notes


def test_token_auth_uses_managed_user_and_secret_backed_binding(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec(config={"authorization_mode": "TOKEN_AUTH", "allow_preview_features": True}))
    assert provisioned.ok
    instance_name = next(iter(client.instances))
    user_name = f"{instance_name}/tokenAuthUsers/default"
    assert user_name in client.token_users
    assert list(client.token_users) == [user_name]
    binding = driver.binding(ServiceHandle(provisioned.handle))
    assert binding.env_vars["REDIS_AUTH_MODE"].literal == "token"
    assert binding.env_vars["REDIS_USER"].literal == "default"
    assert binding.env_vars["REDIS_PASSWORD"].secret_ref
    assert binding.env_vars["REDIS_URL"].secret_ref
    password_id = driver._secret_store.secret_id(binding.env_vars["REDIS_PASSWORD"].secret_ref or "")
    url_id = driver._secret_store.secret_id(binding.env_vars["REDIS_URL"].secret_ref or "")
    assert secrets_client.secrets[password_id][-1].startswith(b"secret-default-")
    assert b"rediss://default:secret-default-" in secrets_client.secrets[url_id][-1]
    assert not binding.iam_grants


def test_token_auth_reconcile_is_idempotent_and_rotation_is_two_phase(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
    secrets_client: FakeSecretClient,
) -> None:
    config = {"authorization_mode": "TOKEN_AUTH", "allow_preview_features": True}
    provisioned = driver.provision(_spec(config=config))
    assert provisioned.ok
    user_name = next(name for name in client.token_users if name.endswith("/default"))
    assert len(client.list_auth_tokens(user_name)) == 1
    instance_id = _parse_handle(provisioned.handle)
    url_id = driver._secret_store.secret_id(driver._token_url_secret(instance_id, "default"))
    original_url = secrets_client.secrets[url_id][-1]

    client.calls.clear()
    assert driver.provision(_spec(config=config)).ok
    assert not any(action in {"add_token_auth_user", "add_auth_token"} for action, _ in client.calls)

    rotated = driver.update(UpdateSpec(provisioned.handle, config={"token_auth_rotation_generation": 2}))
    assert rotated.ok
    assert len(client.list_auth_tokens(user_name)) == 2
    assert secrets_client.secrets[url_id][-1] != original_url
    assert not any(action == "delete_auth_token" for action, _ in client.calls)

    retired = driver.update(UpdateSpec(provisioned.handle, config={"token_auth_retire_generation": 1}))
    assert retired.ok
    assert len(client.list_auth_tokens(user_name)) == 1
    assert any(action == "delete_auth_token" for action, _ in client.calls)


def test_token_auth_refuses_next_rotation_until_previous_generation_is_retired(
    driver: MemorystoreValkeyDriver,
) -> None:
    provisioned = driver.provision(_spec(config={"authorization_mode": "TOKEN_AUTH", "allow_preview_features": True}))
    assert provisioned.ok
    assert driver.update(UpdateSpec(provisioned.handle, config={"token_auth_rotation_generation": 2})).ok
    blocked = driver.update(UpdateSpec(provisioned.handle, config={"token_auth_rotation_generation": 3}))
    assert not blocked.ok
    assert "still retained" in blocked.message


def test_token_auth_rotation_keeps_old_token_when_secret_publication_fails(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec(config={"authorization_mode": "TOKEN_AUTH", "allow_preview_features": True}))
    assert provisioned.ok
    user_name = next(name for name in client.token_users if name.endswith("/default"))
    auth_path = driver._token_secret(_parse_handle(provisioned.handle), "default")
    auth_id = driver._secret_store.secret_id(auth_path)
    original = secrets_client.secrets[auth_id][-1]

    secrets_client.fail_next_write = True
    failed = driver.update(UpdateSpec(provisioned.handle, config={"token_auth_rotation_generation": 2}))
    assert not failed.ok
    assert len(client.list_auth_tokens(user_name)) == 2
    assert secrets_client.secrets[auth_id][-1] == original

    retried = driver.update(UpdateSpec(provisioned.handle, config={"token_auth_rotation_generation": 2}))
    assert retried.ok
    assert secrets_client.secrets[auth_id][-1] != original


def test_token_auth_deprovision_removes_connection_secrets(
    driver: MemorystoreValkeyDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(
        _spec(
            config={
                "authorization_mode": "TOKEN_AUTH",
                "allow_preview_features": True,
                "deletion_protection": False,
            }
        )
    )
    assert provisioned.ok
    driver.binding(ServiceHandle(provisioned.handle))
    assert secrets_client.secrets
    removed = driver.deprovision(DeprovisionSpec(provisioned.handle), delete_data=True)
    assert removed.ok
    assert not secrets_client.secrets


def test_connection_parser_prefers_primary_and_exposes_reader() -> None:
    instance = {
        "endpoints": [
            {
                "connections": [
                    {
                        "pscAutoConnection": {
                            "ipAddress": "10.1.0.1",
                            "port": 6380,
                            "connectionType": "CONNECTION_TYPE_READER",
                        }
                    },
                    {
                        "pscAutoConnection": {
                            "ipAddress": "10.1.0.2",
                            "port": 6379,
                            "connectionType": "CONNECTION_TYPE_PRIMARY",
                        }
                    },
                ]
            }
        ]
    }
    endpoint, reader, records = _connection_endpoints(instance)
    assert endpoint == ("10.1.0.2", 6379, "CONNECTION_TYPE_PRIMARY")
    assert reader == ("10.1.0.1", 6380, "CONNECTION_TYPE_READER")
    assert len(records) == 2


def test_snapshot_and_restore_use_exact_managed_backup(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    provisioned = driver.provision(_spec())
    snapshot = driver.snapshot(ServiceHandle(provisioned.handle))
    assert snapshot.snapshot_id in client.backups
    assert next(kwargs for action, kwargs in client.calls if action == "backup_instance")["ttl"] == "3024000s"
    restored = driver.restore(snapshot, _spec(app_slug="triage-restored"))
    assert restored.ok
    create_calls = [kwargs for action, kwargs in client.calls if action == "create_instance"]
    assert create_calls[-1]["body"]["managedBackupSource"]["backup"] == snapshot.snapshot_id
    assert "astrolift-restore-source" in create_calls[-1]["body"]["labels"]


def test_restore_accepts_gcs_export_source(driver: MemorystoreValkeyDriver, client: FakeValkeyClient) -> None:
    snapshot = SnapshotHandle("redis/old", "gs://backup-bucket/valkey.rdb", "2026-08-14T12:00:00Z")
    result = driver.restore(snapshot, _spec(app_slug="imported"))
    assert result.ok
    body = next(kwargs for action, kwargs in client.calls if action == "create_instance")["body"]
    assert body["gcsSource"] == {"uris": ["gs://backup-bucket/valkey.rdb"]}


def test_deprovision_matrix_respects_protection_and_backup(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    protected = driver.provision(_spec())
    refused = driver.deprovision(DeprovisionSpec(protected.handle))
    retained = driver.deprovision(DeprovisionSpec(protected.handle), force_destroy=True)
    assert not refused.ok and not refused.retryable
    assert retained.ok and "retained backup" in retained.message
    assert any(action == "backup_instance" for action, _ in client.calls)
    assert any(action == "delete_instance" for action, _ in client.calls)


def test_delete_data_skips_backup(driver: MemorystoreValkeyDriver, client: FakeValkeyClient) -> None:
    provisioned = driver.provision(_spec(config={"deletion_protection": False}))
    result = driver.deprovision(DeprovisionSpec(provisioned.handle), delete_data=True)
    assert result.ok
    assert not any(action == "backup_instance" for action, _ in client.calls)


def test_disabled_persistence_refuses_safe_delete(driver: MemorystoreValkeyDriver) -> None:
    provisioned = driver.provision(
        _spec(config={"persistence_mode": "DISABLED", "automated_backup": False, "deletion_protection": False})
    )
    result = driver.deprovision(DeprovisionSpec(provisioned.handle))
    assert not result.ok and not result.retryable
    assert "persistence is disabled" in result.message


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"engine_version": "VALKEY_9_1"}, "Preview"),
        ({"mode": "CLUSTER_DISABLED", "shard_count": 2}, "shard_count=1"),
        ({"persistence_mode": "DISABLED"}, "automated backups require persistence"),
        ({"persistence_mode": "AOF", "rdb_snapshot_period": "ONE_HOUR"}, "RDB settings"),
        ({"cross_instance_role": "SECONDARY"}, "primary_instance"),
        ({"zone_distribution_mode": "SINGLE_ZONE"}, "requires zone"),
        ({"psc_networks": ["net"], "endpoints": [{"connections": []}]}, "mutually exclusive"),
        ({"authorization_mode": "TOKEN_AUTH"}, "Preview"),
        (
            {
                "authorization_mode": "TOKEN_AUTH",
                "allow_preview_features": True,
                "token_auth_user": "not/a/user",
            },
            "token_auth_user",
        ),
        (
            {
                "authorization_mode": "TOKEN_AUTH",
                "allow_preview_features": True,
                "token_auth_rotation_generation": 2,
                "token_auth_retire_generation": 2,
            },
            "must be lower",
        ),
        (
            {"authorization_mode": "IAM_AUTH", "transit_encryption": False},
            "requires transit_encryption=true",
        ),
        ({"engine_configs": []}, "map strings"),
        ({"psc_networks": "cache"}, "list of network names"),
        ({"endpoints": []}, "PSC auto"),
        ({"transit_encryption": "false"}, "must be a boolean"),
        ({"backup_start_minute_utc": 30}, "must be 0"),
        (
            {
                "allow_preview_features": True,
                "server_ca_mode": "CUSTOMER_MANAGED_CAS_CA",
                "server_ca_pool": "projects/p/locations/europe-west1/caPools/c",
            },
            "same-region",
        ),
        ({"acl_policy": "projects/p/locations/europe-west1/aclPolicies/a"}, "same-region"),
        ({"acl_policy": "projects/p/locations/us-central1/aclPolicies/a", "clear_acl_policy": True}, "mutually"),
        ({"zone": "us-central1-a"}, "only with SINGLE_ZONE"),
        ({"rdb_snapshot_period": "DAILY"}, "unsupported rdb_snapshot_period"),
        ({"unknown": True}, "unknown"),
    ],
)
def test_invalid_or_preview_config_fails_closed(
    driver: MemorystoreValkeyDriver,
    config: dict[str, Any],
    message: str,
) -> None:
    result = driver.provision(_spec(config=config))
    assert not result.ok and message in result.message


def test_preview_engine_requires_explicit_opt_in(driver: MemorystoreValkeyDriver, client: FakeValkeyClient) -> None:
    result = driver.provision(_spec(config={"engine_version": "VALKEY_9_1", "allow_preview_features": True}))
    assert result.ok
    body = next(kwargs for action, kwargs in client.calls if action == "create_instance")["body"]
    assert body["engineVersion"] == "VALKEY_9_1"


def test_custom_psc_and_single_zone_native_options_are_preserved(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    endpoints = [
        {
            "connections": [
                {
                    "pscConnection": {
                        "pscConnectionId": "123",
                        "ipAddress": "10.2.0.10",
                        "forwardingRule": "projects/p/regions/r/forwardingRules/f",
                        "network": "projects/p/global/networks/data",
                        "serviceAttachment": "projects/p/regions/r/serviceAttachments/s",
                    }
                }
            ]
        }
    ]
    result = driver.provision(
        _spec(
            config={
                "endpoints": endpoints,
                "zone_distribution_mode": "SINGLE_ZONE",
                "zone": "us-central1-a",
                "kms_key": "projects/p/locations/r/keyRings/k/cryptoKeys/v",
                "engine_configs": {"maxmemory-policy": "allkeys-lru"},
            }
        )
    )
    assert result.ok
    body = next(kwargs for action, kwargs in client.calls if action == "create_instance")["body"]
    assert body["zoneDistributionConfig"] == {"mode": "SINGLE_ZONE", "zone": "us-central1-a"}
    assert body["kmsKey"].endswith("/cryptoKeys/v")
    assert body["engineConfigs"] == {"maxmemory-policy": "allkeys-lru"}
    assert body["endpoints"][0]["connections"][0]["pscConnection"]["pscConnectionId"] == "123"


def test_deprecated_fewer_zones_flag_is_only_sent_when_explicit(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    result = driver.provision(_spec(config={"allow_fewer_zones_deployment": True}))
    assert result.ok
    body = next(kwargs for action, kwargs in client.calls if action == "create_instance")["body"]
    assert body["allowFewerZonesDeployment"] is True


def test_secondary_copies_primary_settings_and_only_sends_local_overrides(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    primary = driver.provision(
        _spec(
            app_slug="primary",
            config={
                "instance_id": "primary-cache",
                "engine_version": "VALKEY_8_0",
                "node_type": "STANDARD_SMALL",
                "shard_count": 3,
                "engine_configs": {"maxmemory-policy": "volatile-lru"},
                "persistence_mode": "AOF",
            },
        )
    )
    assert primary.ok
    primary_name = "projects/acme/locations/us-central1/instances/primary-cache"
    client.calls.clear()
    secondary = driver.provision(
        _spec(
            app_slug="secondary",
            config={
                "instance_id": "secondary-cache",
                "cross_instance_role": "SECONDARY",
                "primary_instance": primary_name,
                "replica_count": 2,
                "maintenance_day": "MONDAY",
            },
        )
    )
    assert secondary.ok
    body = next(kwargs for action, kwargs in client.calls if action == "create_instance")["body"]
    assert body["crossInstanceReplicationConfig"] == {
        "instanceRole": "SECONDARY",
        "primaryInstance": {"instance": primary_name},
    }
    assert body["replicaCount"] == 2
    assert body["maintenancePolicy"]["weeklyMaintenanceWindow"][0]["day"] == "MONDAY"
    for copied in (
        "authorizationMode",
        "transitEncryptionMode",
        "shardCount",
        "nodeType",
        "engineVersion",
        "engineConfigs",
        "mode",
        "persistenceConfig",
    ):
        assert copied not in body


def test_token_auth_secondary_requires_preview_opt_in_and_persists_local_user(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    primary = driver.provision(
        _spec(
            app_slug="primary",
            config={
                "instance_id": "primary-token-cache",
                "authorization_mode": "TOKEN_AUTH",
                "allow_preview_features": True,
            },
        )
    )
    assert primary.ok
    primary_name = "projects/acme/locations/us-central1/instances/primary-token-cache"
    refused = driver.provision(
        _spec(
            app_slug="secondary-refused",
            config={
                "instance_id": "secondary-token-refused",
                "cross_instance_role": "SECONDARY",
                "primary_instance": primary_name,
            },
        )
    )
    assert not refused.ok and "allow_preview_features=true" in refused.message

    secondary = driver.provision(
        _spec(
            app_slug="secondary",
            config={
                "instance_id": "secondary-token-cache",
                "cross_instance_role": "SECONDARY",
                "primary_instance": primary_name,
                "authorization_mode": "TOKEN_AUTH",
                "allow_preview_features": True,
                "token_auth_user": "secondary_app",
            },
        )
    )
    assert secondary.ok
    create = [kwargs for action, kwargs in client.calls if action == "create_instance"][-1]
    assert "authorizationMode" not in create["body"]
    assert create["body"]["labels"]["astrolift-token-user"] == "secondary_app"
    binding = driver.binding(ServiceHandle(secondary.handle))
    assert binding.env_vars["REDIS_USER"].literal == "secondary_app"
    assert binding.env_vars["REDIS_PASSWORD"].secret_ref


def test_secondary_rejects_conflicting_copied_settings_and_primary_only_updates(
    driver: MemorystoreValkeyDriver,
) -> None:
    primary = driver.provision(_spec(app_slug="primary", config={"instance_id": "primary-cache"}))
    assert primary.ok
    primary_name = "projects/acme/locations/us-central1/instances/primary-cache"
    conflict = driver.provision(
        _spec(
            app_slug="conflict",
            config={
                "instance_id": "secondary-conflict",
                "cross_instance_role": "SECONDARY",
                "primary_instance": primary_name,
                "engine_version": "VALKEY_8_0",
            },
        )
    )
    assert not conflict.ok and "copied from the primary" in conflict.message

    secondary = driver.provision(
        _spec(
            app_slug="secondary",
            config={
                "instance_id": "secondary-cache",
                "cross_instance_role": "SECONDARY",
                "primary_instance": primary_name,
            },
        )
    )
    assert secondary.ok
    update = driver.update(UpdateSpec(secondary.handle, config={"shard_count": 4}))
    assert not update.ok and "update these fields on the cross-region primary" in update.message


def test_secondary_can_detach_to_independent_instance(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    primary = driver.provision(_spec(app_slug="primary", config={"instance_id": "primary-cache"}))
    assert primary.ok
    secondary = driver.provision(
        _spec(
            app_slug="secondary",
            config={
                "instance_id": "secondary-cache",
                "cross_instance_role": "SECONDARY",
                "primary_instance": "projects/acme/locations/us-central1/instances/primary-cache",
            },
        )
    )
    assert secondary.ok
    client.calls.clear()
    detached = driver.update(UpdateSpec(secondary.handle, config={"cross_instance_role": "NONE"}))
    assert detached.ok
    patch = next(kwargs for action, kwargs in client.calls if action == "patch_instance")
    assert patch["body"]["crossInstanceReplicationConfig"] == {"instanceRole": "NONE"}


def test_partial_primary_replication_update_preserves_role(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    primary = driver.provision(
        _spec(
            config={
                "cross_instance_role": "PRIMARY",
                "secondary_instances": ["projects/acme/locations/europe-west1/instances/secondary-a"],
            }
        )
    )
    assert primary.ok
    client.calls.clear()
    result = driver.update(
        UpdateSpec(
            primary.handle,
            config={"secondary_instances": ["projects/acme/locations/europe-west1/instances/secondary-b"]},
        )
    )
    assert result.ok
    patch = next(kwargs for action, kwargs in client.calls if action == "patch_instance")
    assert patch["body"]["crossInstanceReplicationConfig"] == {
        "instanceRole": "PRIMARY",
        "secondaryInstances": [{"instance": "projects/acme/locations/europe-west1/instances/secondary-b"}],
    }


def test_unavailable_maintenance_version_fails_closed(
    driver: MemorystoreValkeyDriver,
    client: FakeValkeyClient,
) -> None:
    provisioned = driver.provision(_spec())
    assert provisioned.ok
    name = next(iter(client.instances))
    client.instances[name]["availableMaintenanceVersions"] = ["20260801_00_00"]
    result = driver.update(UpdateSpec(provisioned.handle, config={"maintenance_version": "20260901_00_00"}))
    assert not result.ok and "not currently available" in result.message


def test_status_maps_provider_states_and_missing(driver: MemorystoreValkeyDriver, client: FakeValkeyClient) -> None:
    provisioned = driver.provision(_spec())
    name = next(iter(client.instances))
    client.instances[name]["state"] = "UPDATING"
    assert driver.status(ServiceHandle(provisioned.handle)).state == "updating"
    client.instances[name]["state"] = "MIGRATING"
    assert driver.status(ServiceHandle(provisioned.handle)).state == "updating"
    client.instances.pop(name)
    assert driver.status(ServiceHandle(provisioned.handle)).state == "deprovisioned"


def test_schema_and_editable_fields_expose_native_controls(driver: MemorystoreValkeyDriver) -> None:
    schema = driver.config_schema()
    assert schema["additionalProperties"] is False
    assert "VALKEY_9_1" in schema["properties"]["engine_version"]["enum"]
    assert {
        "CUSTOM_PICO",
        "CUSTOM_MICRO",
        "CUSTOM_MINI",
        "STANDARD_LARGE",
        "HIGHCPU_MEDIUM",
        "HIGHMEM_2XLARGE",
    }.issubset(schema["properties"]["node_type"]["enum"])
    assert {"shard_count", "persistence_mode", "cross_instance_role"}.issubset(driver.editable_fields())
    assert "authorization_mode" not in driver.editable_fields()
    assert schema["properties"]["allow_fewer_zones_deployment"]["deprecated"] is True
    assert "GCP_MEMORYSTORE_ENDPOINTS" in driver.binding_schema().env_vars


def test_availability_catalog_is_executable_and_exposes_token_auth_binding() -> None:
    from _sdk.availability import MATRIX

    entry = next(
        row
        for row in MATRIX.managed_services
        if (row.plugin_id, row.kind, row.variant) == ("gcp", "redis", "memorystore_valkey")
    )
    assert entry.status == "ga"
    assert not entry.issue_url
    assert {"REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "GCP_MEMORYSTORE_ENDPOINTS"}.issubset(entry.binding_envs)
