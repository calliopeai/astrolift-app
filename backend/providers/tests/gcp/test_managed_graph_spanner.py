from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.graph_spanner import (
    SpannerGraphAlreadyExists,
    SpannerGraphConfig,
    SpannerGraphDriver,
    SpannerGraphNotFound,
    SpannerRestClient,
    _dynamic_graph_ddl,
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


class FakeSpannerClient:
    def __init__(self) -> None:
        self.instances: dict[str, dict[str, Any]] = {}
        self.databases: dict[str, dict[str, Any]] = {}
        self.ddl: dict[str, list[str]] = {}
        self.backups: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.schema_operation_already_exists = False

    def _record(self, action: str, **kwargs: Any) -> None:
        self.calls.append((action, kwargs))

    def _operation(self, *, response: dict[str, Any] | None = None) -> dict[str, Any]:
        name = f"projects/acme/operations/op-{len(self.operations) + 1}"
        operation = {"name": name, "done": True, "response": response or {}}
        self.operations[name] = operation
        return deepcopy(operation)

    def get_operation(self, name: str) -> dict[str, Any]:
        self._record("get_operation", name=name)
        if name not in self.operations:
            self.operations[name] = {"name": name, "done": True}
        return deepcopy(self.operations[name])

    def get_instance(self, name: str) -> dict[str, Any]:
        self._record("get_instance", name=name)
        if name not in self.instances:
            raise SpannerGraphNotFound(name)
        return self.instances[name]

    def create_instance(self, project_id: str, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_instance", project_id=project_id, instance_id=instance_id, body=body)
        self.instances[body["name"]] = {**deepcopy(body), "state": "READY"}
        return self._operation(response=self.instances[body["name"]])

    def patch_instance(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        self._record("patch_instance", name=name, body=body, update_mask=update_mask)
        self.instances[name].update(deepcopy(body))
        return self._operation(response=self.instances[name])

    def delete_instance(self, name: str) -> dict[str, Any]:
        self._record("delete_instance", name=name)
        self.instances.pop(name, None)
        return self._operation()

    def get_database(self, name: str) -> dict[str, Any]:
        self._record("get_database", name=name)
        if name not in self.databases:
            raise SpannerGraphNotFound(name)
        return self.databases[name]

    def list_databases(self, instance_name: str) -> list[dict[str, Any]]:
        self._record("list_databases", instance_name=instance_name)
        return [row for name, row in self.databases.items() if name.startswith(f"{instance_name}/databases/")]

    def create_database(self, instance_name: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_database", instance_name=instance_name, body=body)
        database_id = body["createStatement"].split("`")[1]
        name = f"{instance_name}/databases/{database_id}"
        self.databases[name] = {
            "name": name,
            "state": "READY",
            "databaseDialect": "GOOGLE_STANDARD_SQL",
            "enableDropProtection": False,
        }
        self.ddl[name] = list(body.get("extraStatements") or [])
        return self._operation(response=self.databases[name])

    def patch_database(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        self._record("patch_database", name=name, body=body, update_mask=update_mask)
        self.databases[name].update(deepcopy(body))
        return self._operation(response=self.databases[name])

    def drop_database(self, name: str) -> dict[str, Any]:
        self._record("drop_database", name=name)
        if name not in self.databases:
            raise SpannerGraphNotFound(name)
        self.databases.pop(name)
        self.ddl.pop(name, None)
        return {}

    def get_ddl(self, database_name: str) -> list[str]:
        self._record("get_ddl", database_name=database_name)
        return list(self.ddl.get(database_name, []))

    def update_ddl(self, database_name: str, statements: list[str], *, operation_id: str) -> dict[str, Any]:
        self._record(
            "update_ddl",
            database_name=database_name,
            statements=statements,
            operation_id=operation_id,
        )
        operation_name = f"{database_name}/operations/{operation_id}"
        if self.schema_operation_already_exists:
            self.operations[operation_name] = {"name": operation_name, "done": True}
            raise SpannerGraphAlreadyExists(operation_name)
        self.ddl.setdefault(database_name, []).extend(statements)
        operation = {"name": operation_name, "done": True}
        self.operations[operation_name] = operation
        return operation

    def create_backup(self, instance_name: str, backup_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_backup", instance_name=instance_name, backup_id=backup_id, body=body)
        name = f"{instance_name}/backups/{backup_id}"
        self.backups[name] = {"name": name, "state": "READY", **deepcopy(body)}
        return self._operation(response=self.backups[name])

    def list_backups(self, instance_name: str) -> list[dict[str, Any]]:
        self._record("list_backups", instance_name=instance_name)
        return [row for name, row in self.backups.items() if name.startswith(f"{instance_name}/backups/")]

    def get_backup(self, name: str) -> dict[str, Any]:
        self._record("get_backup", name=name)
        if name not in self.backups:
            raise SpannerGraphNotFound(name)
        return self.backups[name]

    def restore_database(self, instance_name: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("restore_database", instance_name=instance_name, body=body)
        source = self.backups[body["backup"]]
        name = f"{instance_name}/databases/{body['databaseId']}"
        self.databases[name] = {
            "name": name,
            "state": "READY",
            "databaseDialect": "GOOGLE_STANDARD_SQL",
            "enableDropProtection": False,
        }
        self.ddl[name] = list(self.ddl[source["database"]])
        return self._operation(response=self.databases[name])


@pytest.fixture
def client() -> FakeSpannerClient:
    return FakeSpannerClient()


@pytest.fixture
def driver(client: FakeSpannerClient) -> SpannerGraphDriver:
    return SpannerGraphDriver(
        config=SpannerGraphConfig(project_id="acme", region="us-central1", poll_interval_seconds=0),
        client=client,
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
        "service_handle_hint": "knowledge",
        "size": "small",
        "binding_id": "binding-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def test_rest_client_uses_admin_v1_paths_and_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": "projects/p/instances/i"}),
            FakeResponse(404, {"error": {"message": "missing"}}, "missing"),
            FakeResponse(409, {"error": {"message": "exists"}}, "exists"),
        ],
    )
    client = SpannerRestClient(endpoint="https://spanner.example/v1/", session=session)
    assert client.get_instance("projects/p/instances/i")["name"].endswith("/i")
    assert session.calls[0]["url"] == "https://spanner.example/v1/projects/p/instances/i"
    with pytest.raises(SpannerGraphNotFound):
        client.get_database("projects/p/instances/i/databases/missing")
    with pytest.raises(SpannerGraphAlreadyExists):
        client.create_instance("p", "i", {})


def test_rest_client_uses_current_mutation_request_shapes() -> None:
    session = FakeSession([FakeResponse(200, {"done": True}) for _ in range(5)])
    client = SpannerRestClient(endpoint="https://spanner.example/v1", session=session)
    instance = "projects/p/instances/graph"
    database = f"{instance}/databases/data"

    client.patch_instance(instance, {"processingUnits": 500}, update_mask=["processingUnits"])
    client.patch_database(database, {"enableDropProtection": True}, update_mask=["enableDropProtection"])
    client.update_ddl(database, ["ALTER TABLE T ADD COLUMN c INT64"], operation_id="astrolift-op")
    client.create_backup(instance, "backup-1", {"database": database})
    client.restore_database(instance, {"databaseId": "restored", "backup": f"{instance}/backups/backup-1"})

    assert session.calls[0]["json"]["fieldMask"] == "processingUnits"
    assert session.calls[1]["params"] == {"updateMask": "enableDropProtection"}
    assert session.calls[2]["method"] == "PATCH"
    assert session.calls[2]["url"].endswith("/databases/data/ddl")
    assert session.calls[2]["json"]["operationId"] == "astrolift-op"
    assert session.calls[3]["params"] == {"backupId": "backup-1"}
    assert session.calls[4]["url"].endswith("/instances/graph/databases:restore")


def test_provision_creates_enterprise_google_sql_dynamic_graph(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok and result.ready
    instance_id, database_id = _parse_handle(result.handle)
    instance = client.instances[f"projects/acme/instances/{instance_id}"]
    database_name = f"projects/acme/instances/{instance_id}/databases/{database_id}"
    assert instance["edition"] == "ENTERPRISE"
    assert instance["processingUnits"] == 100
    assert instance["defaultBackupScheduleType"] == "AUTOMATIC"
    assert client.databases[database_name]["databaseDialect"] == "GOOGLE_STANDARD_SQL"
    assert client.databases[database_name]["enableDropProtection"] is True
    assert any("CREATE TABLE AstroliftGraphMetadata" in row for row in client.ddl[database_name])
    assert any("CREATE PROPERTY GRAPH Graph" in row for row in client.ddl[database_name])


def test_shared_instance_hosts_independent_databases(client: FakeSpannerClient) -> None:
    driver = SpannerGraphDriver(
        config=SpannerGraphConfig(
            project_id="acme",
            region="us-central1",
            shared_instance_id="shared-graph",
            poll_interval_seconds=0,
        ),
        client=client,
        sleep=lambda _: None,
    )
    first = driver.provision(_spec(service_handle_hint="one"))
    second = driver.provision(_spec(service_handle_hint="two"))
    assert first.ok and second.ok
    assert _parse_handle(first.handle)[0] == _parse_handle(second.handle)[0] == "shared-graph"
    assert len(client.instances) == 1
    assert len(client.databases) == 2


def test_custom_graph_ddl_and_cmek_are_atomic(driver: SpannerGraphDriver, client: FakeSpannerClient) -> None:
    ddl = [
        "CREATE TABLE Person (id INT64 NOT NULL) PRIMARY KEY (id)",
        "CREATE PROPERTY GRAPH People NODE TABLES (Person)",
    ]
    result = driver.provision(
        _spec(
            config={
                "graph_name": "People",
                "ddl_statements": ddl,
                "kms_key_names": ["projects/p/locations/us/keyRings/r/cryptoKeys/k"],
            },
        ),
    )
    assert result.ok
    create = next(kwargs for name, kwargs in client.calls if name == "create_database")
    assert "CREATE TABLE AstroliftGraphMetadata" in create["body"]["extraStatements"][0]
    assert create["body"]["extraStatements"][1:] == ddl
    assert create["body"]["encryptionConfig"]["kmsKeyNames"][0].endswith("/k")


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"edition": "STANDARD"}, "requires ENTERPRISE"),
        ({"graph_name": "People", "ddl_statements": ["CREATE TABLE Person (id INT64) PRIMARY KEY(id)"]}, "People"),
        ({"schema_update_statements": ["DROP TABLE Person"]}, "permits only"),
        ({"autoscaling_min_processing_units": 1000, "manage_instance_capacity": True}, "requires both"),
        (
            {
                "autoscaling_min_processing_units": 2000,
                "autoscaling_max_processing_units": 1000,
                "manage_instance_capacity": True,
            },
            "greater than or equal",
        ),
        (
            {
                "processing_units": 1000,
                "autoscaling_min_processing_units": 1000,
                "autoscaling_max_processing_units": 2000,
                "manage_instance_capacity": True,
            },
            "mutually exclusive",
        ),
        ({"version_retention_period": "forever"}, "positive duration"),
        ({"instance_id": "invalid-"}, "invalid Spanner Graph instance_id"),
        ({"processing_units": 150}, "multiple of 100"),
        ({"ddl_statements": "CREATE PROPERTY GRAPH Graph"}, "non-empty list"),
        ({"autoscaling_storage_percent": 80}, "require autoscaling capacity"),
    ],
)
def test_invalid_configs_fail_closed(driver: SpannerGraphDriver, config: dict[str, Any], message: str) -> None:
    result = driver.provision(_spec(config=config))
    assert not result.ok
    assert message in result.message


def test_autoscaling_instance_shape(driver: SpannerGraphDriver, client: FakeSpannerClient) -> None:
    result = driver.provision(
        _spec(
            config={
                "autoscaling_min_processing_units": 1000,
                "autoscaling_max_processing_units": 3000,
                "autoscaling_high_priority_cpu_percent": 65,
                "autoscaling_storage_percent": 75,
                "manage_instance_capacity": True,
            },
        ),
    )
    assert result.ok
    instance = client.instances[f"projects/acme/instances/{_parse_handle(result.handle)[0]}"]
    assert "processingUnits" not in instance
    assert instance["autoscalingConfig"]["autoscalingLimits"] == {
        "minProcessingUnits": 1000,
        "maxProcessingUnits": 3000,
    }


def test_existing_unowned_instance_requires_adoption(driver: SpannerGraphDriver, client: FakeSpannerClient) -> None:
    created = driver.provision(_spec())
    instance_id, _ = _parse_handle(created.handle)
    client.instances[f"projects/acme/instances/{instance_id}"]["labels"] = {}
    denied = driver.provision(_spec())
    adopted = driver.provision(_spec(config={"adopt_existing_instance": True}))
    assert not denied.ok and "adopt_existing_instance" in denied.message
    assert adopted.ok


def test_existing_unmarked_database_requires_explicit_adoption(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    created = driver.provision(_spec())
    instance_id, database_id = _parse_handle(created.handle)
    database_name = f"projects/acme/instances/{instance_id}/databases/{database_id}"
    client.ddl[database_name] = [row for row in client.ddl[database_name] if "AstroliftGraphMetadata" not in row]
    denied = driver.provision(_spec())
    adopted = driver.provision(_spec(config={"adopt_existing_database": True}))
    assert not denied.ok and "adopt_existing_database" in denied.message
    assert adopted.ok
    assert any("CREATE TABLE AstroliftGraphMetadata" in row for row in client.ddl[database_name])


def test_update_schema_is_replay_safe(driver: SpannerGraphDriver, client: FakeSpannerClient) -> None:
    created = driver.provision(_spec())
    statement = "ALTER TABLE GraphNode ADD COLUMN created_at TIMESTAMP"
    first = driver.update(UpdateSpec(created.handle, config={"schema_update_statements": [statement]}))
    client.schema_operation_already_exists = True
    second = driver.update(UpdateSpec(created.handle, config={"schema_update_statements": [statement]}))
    assert first.ok and second.ok
    assert any(name == "get_operation" for name, _ in client.calls)


def test_update_reconciles_capacity_and_drop_protection(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    created = driver.provision(_spec())
    changed = driver.update(
        UpdateSpec(
            created.handle,
            config={
                "processing_units": 500,
                "manage_instance_capacity": True,
                "deletion_protection": False,
            },
        ),
    )
    assert changed.ok
    instance_id, database_id = _parse_handle(created.handle)
    assert client.instances[f"projects/acme/instances/{instance_id}"]["processingUnits"] == 500
    assert (
        client.databases[f"projects/acme/instances/{instance_id}/databases/{database_id}"]["enableDropProtection"]
        is False
    )


def test_update_reconciles_version_retention_with_replay_safe_ddl(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    created = driver.provision(_spec())
    updated = driver.update(UpdateSpec(created.handle, config={"version_retention_period": "7d"}))
    assert updated.ok
    update = next(
        kwargs
        for name, kwargs in reversed(client.calls)
        if name == "update_ddl" and "ALTER DATABASE" in kwargs["statements"][0]
    )
    assert "version_retention_period = '7d'" in update["statements"][0]
    assert update["operation_id"].startswith("astrolift")


def test_binding_emits_portable_graph_contract(driver: SpannerGraphDriver) -> None:
    created = driver.provision(_spec(config={"graph_name": "Knowledge"}))
    binding = driver.binding(ServiceHandle(created.handle), {"graph_name": "Knowledge"})
    assert binding.env_vars["GRAPH_DB_URL"].literal.endswith("/graphs/Knowledge")
    assert binding.env_vars["GRAPH_DB_PROTOCOL"].literal == "gql"
    assert binding.env_vars["GRAPH_DB_AUTH_MODE"].literal == "gcp_iam"
    assert binding.env_vars["GCP_SPANNER_DIALECT"].literal == "GOOGLE_STANDARD_SQL"
    assert binding.iam_grants[0].actions == ["roles/spanner.databaseUser"]


@pytest.mark.parametrize(
    ("instance_state", "database_state", "expected"),
    [
        ("READY", "READY", "available"),
        ("CREATING", "READY", "provisioning"),
        ("READY", "CREATING", "provisioning"),
        ("READY", "READY_OPTIMIZING", "updating"),
    ],
)
def test_status_maps_instance_and_database_states(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
    instance_state: str,
    database_state: str,
    expected: str,
) -> None:
    created = driver.provision(_spec())
    instance_id, database_id = _parse_handle(created.handle)
    client.instances[f"projects/acme/instances/{instance_id}"]["state"] = instance_state
    client.databases[f"projects/acme/instances/{instance_id}/databases/{database_id}"]["state"] = database_state
    assert driver.status(ServiceHandle(created.handle)).state == expected


def test_snapshot_and_restore_use_exact_backup_resource(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    source = driver.provision(_spec(config={"graph_name": "Knowledge"}))
    snapshot = driver.snapshot(ServiceHandle(source.handle))
    assert snapshot.snapshot_id in client.backups
    restored = driver.restore(
        snapshot,
        _spec(service_handle_hint="restored", config={"graph_name": "Knowledge"}),
    )
    assert restored.ok and restored.ready
    call = next(kwargs for name, kwargs in client.calls if name == "restore_database")
    assert call["body"]["backup"] == snapshot.snapshot_id


def test_deprovision_requires_force_for_protection_and_retains_backup(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    created = driver.provision(_spec())
    denied = driver.deprovision(DeprovisionSpec(created.handle))
    allowed = driver.deprovision(DeprovisionSpec(created.handle), force_destroy=True)
    assert not denied.ok and denied.errors == ["deletion_protection_enabled"]
    assert allowed.ok and "retained backup" in allowed.message
    assert client.backups
    assert client.instances


def test_destructive_delete_can_remove_empty_owned_instance(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    created = driver.provision(_spec(config={"deletion_protection": False}))
    result = driver.deprovision(
        DeprovisionSpec(created.handle, config={"delete_empty_instance": True}),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert not client.databases
    assert not client.instances
    assert not client.backups


def test_deprovision_of_adopted_database_requires_separate_delete_consent(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    created = driver.provision(_spec(config={"deletion_protection": False}))
    instance_id, _ = _parse_handle(created.handle)
    client.instances[f"projects/acme/instances/{instance_id}"]["labels"] = {}
    denied = driver.deprovision(DeprovisionSpec(created.handle), delete_data=True, force_destroy=True)
    allowed = driver.deprovision(
        DeprovisionSpec(created.handle, config={"delete_adopted_database": True}),
        delete_data=True,
        force_destroy=True,
    )
    assert not denied.ok and denied.errors == ["adopted_database_delete_requires_opt_in"]
    assert allowed.ok
    assert client.instances


def test_dynamic_ddl_is_gql_property_graph_schema() -> None:
    ddl = _dynamic_graph_ddl("Knowledge")
    assert any("DYNAMIC LABEL" in row and "DYNAMIC PROPERTIES" in row for row in ddl)
    assert any("SOURCE KEY" in row and "DESTINATION KEY" in row for row in ddl)


def test_handle_validation_is_kind_specific() -> None:
    assert _parse_handle("graph_db/instance/database") == ("instance", "database")
    for invalid in ("graph_db/instance", "postgres/instance/database", "graph_db//database"):
        with pytest.raises(ValueError):
            _parse_handle(invalid)
