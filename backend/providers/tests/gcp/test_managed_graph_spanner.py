from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.graph_spanner import (
    SpannerGraphAlreadyExists,
    SpannerGraphConfig,
    SpannerGraphDriver,
    SpannerGraphError,
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
        schema = self.ddl.setdefault(database_name, [])
        for statement in statements:
            # get_ddl returns the live schema, not a log: a DROP removes the table.
            dropped = re.fullmatch(r"DROP TABLE (\w+)", statement)
            if dropped:
                schema[:] = [row for row in schema if not row.startswith(f"CREATE TABLE {dropped.group(1)} ")]
            else:
                schema.append(statement)
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


ORG = "01996b1a-1111-7e8f-9a0b-111111111111"
OTHER_ORG = "01996b1a-9999-7e8f-9a0b-999999999999"

MSID = "01996b1a-3c4d-7e8f-9a0b-1c2d3e4f5a6b"
OTHER_MSID = "01996b1a-ffff-7e8f-9a0b-aaaaaaaaaaaa"


def _spec(**overrides: Any) -> ProvisionSpec:
    values: dict[str, Any] = {
        "organization_id": ORG,
        "organization_slug": "steadymd",
        "app_id": "2",
        "app_slug": "triage",
        "environment_id": "3",
        "environment_name": "prod",
        "tenant_cluster_id": "gcp-prod",
        "service_handle_hint": "knowledge",
        "size": "small",
        "binding_id": "binding-1",
        "managed_service_id": MSID,
        "recorded_container_exclusive": True,
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _owner_table(managed_service_id: str) -> str:
    return "AstroliftGraphOwner_" + managed_service_id.replace("-", "")


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
    assert f"CREATE TABLE {_owner_table(MSID)} " in create["body"]["extraStatements"][1]
    assert create["body"]["extraStatements"][2:] == ddl
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


def test_existing_unowned_instance_is_refused_without_operator_adoption(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    created = driver.provision(_spec())
    instance_id, _ = _parse_handle(created.handle)
    name = f"projects/acme/instances/{instance_id}"
    client.instances[name]["labels"] = {}
    denied = driver.provision(_spec())
    assert not denied.ok
    assert "operator-authorized" in denied.message
    refused_update = driver.update(
        UpdateSpec(
            created.handle, managed_service_id=MSID, config={}, organization_id=ORG, recorded_container_exclusive=True
        )
    )
    assert not refused_update.ok
    assert "operator-authorized" in refused_update.message

    # The flag is gone entirely, not just ignored: adoption is operator-only
    # and no tenant config reopens it (#2021).
    rejected = driver.provision(_spec(config={"adopt_existing_instance": True}))
    assert not rejected.ok
    assert "unknown" in rejected.message
    assert client.instances[name]["labels"] == {}


def test_unlabeled_operator_shared_instance_needs_the_operator_marker(client: FakeSpannerClient) -> None:
    """An operator naming a pre-existing shared instance adopts it by labeling it, not by a switch."""
    name = "projects/acme/instances/shared-graph"
    client.instances[name] = {
        "name": name,
        "config": "projects/acme/instanceConfigs/regional-us-central1",
        "edition": "ENTERPRISE",
        "state": "READY",
        "labels": {},
    }
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

    denied = driver.provision(_spec())
    assert not denied.ok
    assert "operator-authorized" in denied.message
    assert not client.databases

    client.instances[name]["labels"] = {"astrolift-managed-by": "platform"}
    assert driver.provision(_spec()).ok


def test_existing_unmarked_database_is_refused_without_operator_adoption(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    created = driver.provision(_spec())
    instance_id, database_id = _parse_handle(created.handle)
    database_name = f"projects/acme/instances/{instance_id}/databases/{database_id}"
    client.ddl[database_name] = [row for row in client.ddl[database_name] if "AstroliftGraph" not in row]
    denied = driver.provision(_spec())
    assert not denied.ok
    assert "operator-authorized" in denied.message
    refused_update = driver.update(
        UpdateSpec(
            created.handle, managed_service_id=MSID, config={}, organization_id=ORG, recorded_container_exclusive=True
        )
    )
    assert not refused_update.ok
    assert "operator-authorized" in refused_update.message

    rejected = driver.provision(_spec(config={"adopt_existing_database": True}))
    assert not rejected.ok
    assert "unknown" in rejected.message
    assert not any("AstroliftGraphMetadata" in row for row in client.ddl[database_name])


def test_update_schema_is_replay_safe(driver: SpannerGraphDriver, client: FakeSpannerClient) -> None:
    created = driver.provision(_spec())
    statement = "ALTER TABLE GraphNode ADD COLUMN created_at TIMESTAMP"
    first = driver.update(
        UpdateSpec(
            created.handle,
            managed_service_id=MSID,
            config={"schema_update_statements": [statement]},
            organization_id=ORG,
            recorded_container_exclusive=True,
        )
    )
    client.schema_operation_already_exists = True
    second = driver.update(
        UpdateSpec(
            created.handle,
            managed_service_id=MSID,
            config={"schema_update_statements": [statement]},
            organization_id=ORG,
            recorded_container_exclusive=True,
        )
    )
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
            managed_service_id=MSID,
            config={
                "processing_units": 500,
                "manage_instance_capacity": True,
                "deletion_protection": False,
            },
            organization_id=ORG,
            recorded_container_exclusive=True,
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
    updated = driver.update(
        UpdateSpec(
            created.handle,
            managed_service_id=MSID,
            config={"version_retention_period": "7d"},
            organization_id=ORG,
            recorded_container_exclusive=True,
        )
    )
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
    binding = driver.binding(
        ServiceHandle(created.handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True),
        {"graph_name": "Knowledge"},
    )
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
    assert (
        driver.status(
            ServiceHandle(
                created.handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True
            )
        ).state
        == expected
    )


def test_snapshot_and_restore_use_exact_backup_resource(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    source = driver.provision(_spec(config={"graph_name": "Knowledge"}))
    snapshot = driver.snapshot(
        ServiceHandle(source.handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True)
    )
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
    denied = driver.deprovision(
        DeprovisionSpec(created.handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True)
    )
    allowed = driver.deprovision(
        DeprovisionSpec(
            created.handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True
        ),
        force_destroy=True,
    )
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
        DeprovisionSpec(
            created.handle,
            managed_service_id=MSID,
            config={"delete_empty_instance": True},
            organization_id=ORG,
            recorded_container_exclusive=True,
        ),
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
    denied = driver.deprovision(
        DeprovisionSpec(
            created.handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True
        ),
        delete_data=True,
        force_destroy=True,
    )
    allowed = driver.deprovision(
        DeprovisionSpec(
            created.handle,
            managed_service_id=MSID,
            config={"delete_adopted_database": True},
            organization_id=ORG,
            recorded_container_exclusive=True,
        ),
        delete_data=True,
        force_destroy=True,
    )
    assert not denied.ok and "ownership marker" in denied.message
    assert not allowed.ok and "ownership marker" in allowed.message
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


# ---- #2086: ownership is the managed-service id, not a tenant-settable name ----

_MUTATIONS = {"patch_database", "patch_instance", "update_ddl", "drop_database", "create_backup"}


def _shared_driver(client: FakeSpannerClient) -> SpannerGraphDriver:
    return SpannerGraphDriver(
        config=SpannerGraphConfig(
            project_id="acme",
            region="us-central1",
            shared_instance_id="shared-graph",
            poll_interval_seconds=0,
        ),
        client=client,
        sleep=lambda _: None,
    )


def _other_org(**overrides: Any) -> ProvisionSpec:
    """A second org whose app slug, environment and hint all match the first's."""
    return _spec(
        **{"organization_id": OTHER_ORG, "organization_slug": "globex", "managed_service_id": OTHER_MSID, **overrides}
    )


def _database_name(handle: str) -> str:
    instance_id, database_id = _parse_handle(handle)
    return f"projects/acme/instances/{instance_id}/databases/{database_id}"


def _owner_rows(client: FakeSpannerClient, name: str) -> list[str]:
    return [row for row in client.ddl[name] if "AstroliftGraphOwner_" in row]


def _legacy_database(client: FakeSpannerClient, driver: SpannerGraphDriver, spec: ProvisionSpec) -> tuple[str, str]:
    """A database provisioned before #2086: the Metadata marker and no owner table."""
    created = driver.provision(spec)
    assert created.ok
    name = _database_name(created.handle)
    client.ddl[name] = [row for row in client.ddl[name] if "AstroliftGraphOwner_" not in row]
    return created.handle, name


def test_two_orgs_with_the_same_app_env_and_hint_get_separate_databases_on_a_shared_instance(
    client: FakeSpannerClient,
) -> None:
    driver = _shared_driver(client)
    first = driver.provision(_spec())
    second = driver.provision(_other_org())

    assert first.ok and second.ok
    assert first.handle != second.handle
    assert _owner_rows(client, _database_name(first.handle)) == [
        f"CREATE TABLE {_owner_table(MSID)} (marker STRING(1) NOT NULL) PRIMARY KEY (marker)",
    ]
    assert _owner_rows(client, _database_name(second.handle)) == [
        f"CREATE TABLE {_owner_table(OTHER_MSID)} (marker STRING(1) NOT NULL) PRIMARY KEY (marker)",
    ]


def test_a_tenant_naming_another_orgs_database_is_refused_and_it_is_left_untouched(
    client: FakeSpannerClient,
) -> None:
    driver = _shared_driver(client)
    victim = driver.provision(_spec())
    name = _database_name(victim.handle)
    ddl_before = list(client.ddl[name])
    client.calls.clear()

    denied = driver.provision(
        _other_org(
            config={
                "database_id": _parse_handle(victim.handle)[1],
                "deletion_protection": False,
                "version_retention_period": "7d",
                "schema_update_statements": ["ALTER TABLE GraphNode ADD COLUMN taken STRING(MAX)"],
                "manage_instance_capacity": True,
                "processing_units": 500,
            },
        ),
    )

    assert not denied.ok
    assert "not tenant-manageable" in denied.message
    assert client.ddl[name] == ddl_before
    assert client.databases[name]["enableDropProtection"] is True
    assert client.instances["projects/acme/instances/shared-graph"]["processingUnits"] == 100
    assert not [call for call, _ in client.calls if call in _MUTATIONS]


def test_another_services_handle_is_refused_by_update_binding_snapshot_and_deprovision(
    client: FakeSpannerClient,
) -> None:
    driver = _shared_driver(client)
    victim = driver.provision(_spec(config={"deletion_protection": False}))
    name = _database_name(victim.handle)
    client.calls.clear()
    # Even a record claiming exclusivity loses to the marker once there is one.
    stranger: dict[str, Any] = {"managed_service_id": OTHER_MSID, "recorded_handle_exclusive": True}

    updated = driver.update(
        UpdateSpec(
            victim.handle,
            config={"deletion_protection": True},
            **stranger,
            organization_id=ORG,
            recorded_container_exclusive=True,
        )
    )
    assert not updated.ok and "another managed service" in updated.message
    with pytest.raises(SpannerGraphError, match="another managed service"):
        driver.binding(ServiceHandle(victim.handle, **stranger, organization_id=ORG, recorded_container_exclusive=True))
    with pytest.raises(SpannerGraphError, match="another managed service"):
        driver.snapshot(
            ServiceHandle(victim.handle, **stranger, organization_id=ORG, recorded_container_exclusive=True)
        )
    deleted = driver.deprovision(
        DeprovisionSpec(victim.handle, **stranger, organization_id=ORG, recorded_container_exclusive=True),
        delete_data=True,
        force_destroy=True,
    )
    assert not deleted.ok
    assert deleted.errors == ["resource_not_owned"]
    assert deleted.retryable is False

    assert name in client.databases
    assert client.databases[name]["enableDropProtection"] is False
    assert not [call for call, _ in client.calls if call in _MUTATIONS]


def test_a_database_from_before_2086_is_refused_without_an_exclusive_record(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    handle, name = _legacy_database(client, driver, _spec())

    refused = [
        driver.provision(_spec()),
        driver.provision(_spec(recorded_handle=handle)),
        driver.update(
            UpdateSpec(
                handle, managed_service_id=MSID, config={}, organization_id=ORG, recorded_container_exclusive=True
            )
        ),
    ]
    for result in refused:
        assert not result.ok
        assert "exclusive platform record" in result.message
        assert _owner_table(MSID) in result.message
    with pytest.raises(SpannerGraphError, match="exclusive platform record"):
        driver.binding(
            ServiceHandle(handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True)
        )
    deleted = driver.deprovision(
        DeprovisionSpec(handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True),
        delete_data=True,
        force_destroy=True,
    )
    assert not deleted.ok and deleted.errors == ["resource_not_owned"]

    assert _owner_rows(client, name) == []
    assert name in client.databases


def test_a_database_from_before_2086_is_backfilled_once_on_an_exclusive_record(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    handle, name = _legacy_database(client, driver, _spec())

    assert driver.update(
        UpdateSpec(
            handle,
            managed_service_id=MSID,
            recorded_handle_exclusive=True,
            config={},
            organization_id=ORG,
            recorded_container_exclusive=True,
        )
    ).ok
    assert _owner_rows(client, name) == [
        f"CREATE TABLE {_owner_table(MSID)} (marker STRING(1) NOT NULL) PRIMARY KEY (marker)",
    ]

    # From here the marker decides: this service needs no record, another is refused with one.
    assert driver.update(
        UpdateSpec(handle, managed_service_id=MSID, config={}, organization_id=ORG, recorded_container_exclusive=True)
    ).ok
    taken = driver.update(
        UpdateSpec(
            handle,
            managed_service_id=OTHER_MSID,
            recorded_handle_exclusive=True,
            config={},
            organization_id=ORG,
            recorded_container_exclusive=True,
        ),
    )
    assert not taken.ok and "another managed service" in taken.message
    assert len(_owner_rows(client, name)) == 1


def test_a_reprovision_backfills_a_database_its_exclusive_record_names(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    handle, name = _legacy_database(client, driver, _spec())

    result = driver.provision(_spec(recorded_handle=handle, recorded_handle_exclusive=True))

    assert result.ok and result.handle == handle
    assert _owner_rows(client, name) == [
        f"CREATE TABLE {_owner_table(MSID)} (marker STRING(1) NOT NULL) PRIMARY KEY (marker)",
    ]


def test_a_service_named_before_2086_keeps_its_recorded_database(client: FakeSpannerClient) -> None:
    driver = _shared_driver(client)
    legacy_handle = "graph_db/shared-graph/triage-prod-knowledge"
    seeded, name = _legacy_database(client, driver, _spec(config={"database_id": "triage-prod-knowledge"}))
    assert seeded == legacy_handle
    databases_before = set(client.databases)

    kept = driver.provision(_spec(recorded_handle=legacy_handle, recorded_handle_exclusive=True))
    assert kept.ok and kept.handle == legacy_handle
    assert set(client.databases) == databases_before
    assert _owner_rows(client, name)

    # Another org with the same app, environment and hint now derives its own
    # database, and naming the old one outright is refused.
    other = driver.provision(_other_org())
    assert other.ok and other.handle != legacy_handle
    named = driver.provision(_other_org(config={"database_id": "triage-prod-knowledge"}))
    assert not named.ok and "another managed service" in named.message


@pytest.mark.parametrize("key", ["ddl_statements", "schema_update_statements"])
@pytest.mark.parametrize(
    "statement",
    [
        f"CREATE TABLE {_owner_table(OTHER_MSID)} (marker STRING(1) NOT NULL) PRIMARY KEY (marker)",
        "CREATE TABLE astroliftgraphowner_01 (marker STRING(1) NOT NULL) PRIMARY KEY (marker)",
        "ALTER TABLE AstroliftGraphMetadata ADD COLUMN owner STRING(36)",
        "RENAME TABLE GraphNode TO AstroliftGraphOwner_x",
    ],
)
def test_tenant_ddl_cannot_name_the_ownership_tables(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
    key: str,
    statement: str,
) -> None:
    statements = [statement]
    if key == "ddl_statements":
        statements.append("CREATE PROPERTY GRAPH Graph NODE TABLES (GraphNode)")

    result = driver.provision(_spec(config={key: statements}))

    assert not result.ok
    assert "ownership tables" in result.message
    assert not client.databases


def test_restore_marks_the_restored_database_for_the_target_service(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    source = driver.provision(_spec(config={"graph_name": "Knowledge"}))
    snapshot = driver.snapshot(
        ServiceHandle(source.handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True)
    )

    restored = driver.restore(
        snapshot,
        _spec(service_handle_hint="restored", managed_service_id=OTHER_MSID, config={"graph_name": "Knowledge"}),
    )

    assert restored.ok
    assert _owner_rows(client, _database_name(restored.handle)) == [
        f"CREATE TABLE {_owner_table(OTHER_MSID)} (marker STRING(1) NOT NULL) PRIMARY KEY (marker)",
    ]
    config = {"graph_name": "Knowledge"}
    assert driver.update(
        UpdateSpec(
            restored.handle,
            managed_service_id=OTHER_MSID,
            config=config,
            organization_id=ORG,
            recorded_container_exclusive=True,
        )
    ).ok
    assert not driver.update(
        UpdateSpec(
            restored.handle,
            managed_service_id=MSID,
            config=config,
            organization_id=ORG,
            recorded_container_exclusive=True,
        )
    ).ok


def test_provision_without_a_managed_service_id_fails_closed(
    driver: SpannerGraphDriver,
    client: FakeSpannerClient,
) -> None:
    result = driver.provision(_spec(managed_service_id=""))

    assert not result.ok
    assert "managed-service id" in result.message
    assert not client.instances and not client.databases


_MUTATIONS = {
    "create_instance",
    "patch_instance",
    "delete_instance",
    "create_database",
    "patch_database",
    "drop_database",
    "update_ddl",
    "create_backup",
    "restore_database",
}


def _assert_no_mutations(client: FakeSpannerClient) -> None:
    assert not [action for action, _ in client.calls if action in _MUTATIONS]


def test_new_instance_name_uses_immutable_service_id_and_retains_recorded_names(driver, client) -> None:
    from dataclasses import replace

    spec = _spec(organization_slug="a-b", app_slug="c", environment_name="d")
    other = _other_org(organization_slug="a", app_slug="b-c", environment_name="d")
    first = driver.provision(spec)
    second = driver.provision(other)
    assert first.ok and second.ok
    assert _parse_handle(first.handle)[0] != _parse_handle(second.handle)[0]
    assert driver._instance_id(spec, {}) == driver._instance_id(
        replace(spec, organization_slug="renamed", app_slug="renamed"), {}
    )
    kept = driver.provision(
        replace(
            spec,
            organization_slug="renamed",
            app_slug="renamed",
            recorded_handle=first.handle,
            recorded_handle_exclusive=True,
        )
    )
    assert kept.ok and kept.handle == first.handle
    assert len(client.instances) == 2 and len(client.databases) == 2
    assert next(iter(client.instances.values()))["labels"]["astrolift-organization-id"] == ORG


def test_foreign_instance_org_refuses_all_paths_even_for_the_correct_database_id(driver, client) -> None:
    created = driver.provision(_spec(config={"deletion_protection": False}))
    instance_id, database_id = _parse_handle(created.handle)
    client.calls.clear()
    denied = driver.provision(_other_org(config={"instance_id": instance_id, "database_id": database_id}))
    assert not denied.ok and "another organization" in denied.message
    update = driver.update(
        UpdateSpec(
            created.handle,
            managed_service_id=MSID,
            organization_id=OTHER_ORG,
            recorded_container_exclusive=True,
            config={"manage_instance_capacity": True, "processing_units": 500},
        )
    )
    teardown = driver.deprovision(
        DeprovisionSpec(
            created.handle,
            managed_service_id=MSID,
            organization_id=OTHER_ORG,
            recorded_container_exclusive=True,
            config={"delete_empty_instance": True, "delete_adopted_database": True},
        ),
        delete_data=True,
        force_destroy=True,
    )
    assert not update.ok and "another organization" in update.message
    assert not teardown.ok and "another organization" in teardown.message
    for method in (driver.binding, driver.snapshot):
        with pytest.raises(SpannerGraphError, match="another organization"):
            method(
                ServiceHandle(
                    created.handle,
                    managed_service_id=MSID,
                    organization_id=OTHER_ORG,
                    recorded_container_exclusive=True,
                )
            )
    _assert_no_mutations(client)
    assert _database_name(created.handle) in client.databases


@pytest.mark.parametrize("method", ["provision", "update", "deprovision"])
def test_operator_shared_capacity_and_instance_delete_never_reach_mutating_calls(client, method: str) -> None:
    driver = _shared_driver(client)
    created = driver.provision(_spec(config={"deletion_protection": False}))
    client.calls.clear()
    if method == "provision":
        result = driver.provision(_spec(config={"manage_instance_capacity": True, "processing_units": 500}))
    elif method == "update":
        result = driver.update(
            UpdateSpec(
                created.handle,
                managed_service_id=MSID,
                organization_id=ORG,
                recorded_container_exclusive=True,
                config={"manage_instance_capacity": True, "processing_units": 500},
            )
        )
    else:
        result = driver.deprovision(
            DeprovisionSpec(
                created.handle,
                managed_service_id=MSID,
                organization_id=ORG,
                recorded_container_exclusive=True,
                config={"delete_empty_instance": True},
            ),
            delete_data=True,
            force_destroy=True,
        )
    assert not result.ok and "not tenant-manageable" in result.message
    _assert_no_mutations(client)


@pytest.mark.parametrize("proof", [False, True])
def test_container_resize_and_delete_require_complete_cloud_database_set(driver, client, proof: bool) -> None:
    created = driver.provision(_spec(config={"deletion_protection": False}))
    instance_id, _ = _parse_handle(created.handle)
    foreign = f"projects/acme/instances/{instance_id}/databases/foreign"
    client.databases[foreign] = {"name": foreign}
    client.ddl[foreign] = []
    client.calls.clear()
    changed = driver.update(
        UpdateSpec(
            created.handle,
            managed_service_id=MSID,
            organization_id=ORG,
            recorded_handle_exclusive=True,
            recorded_container_exclusive=proof,
            config={
                "processing_units": 500,
                "manage_instance_capacity": True,
                "schema_update_statements": ["ALTER TABLE GraphNode ADD COLUMN taken STRING(MAX)"],
            },
        )
    )
    teardown = driver.deprovision(
        DeprovisionSpec(
            created.handle,
            managed_service_id=MSID,
            organization_id=ORG,
            recorded_handle_exclusive=True,
            recorded_container_exclusive=proof,
            config={"delete_empty_instance": True},
        ),
        delete_data=True,
        force_destroy=True,
    )
    assert not changed.ok and not teardown.ok
    _assert_no_mutations(client)
    assert _database_name(created.handle) in client.databases and foreign in client.databases


def test_uncontested_legacy_instance_backfills_actual_org_only_with_both_proofs(driver, client) -> None:
    created = driver.provision(_spec(config={"deletion_protection": False}))
    instance_id, _ = _parse_handle(created.handle)
    instance = client.instances[f"projects/acme/instances/{instance_id}"]
    del instance["labels"]["astrolift-organization-id"]
    client.calls.clear()
    denied = driver.update(
        UpdateSpec(
            created.handle,
            managed_service_id=MSID,
            organization_id=ORG,
            recorded_handle_exclusive=True,
            recorded_container_exclusive=False,
        )
    )
    assert not denied.ok and "exclusive recorded-container proof" in denied.message
    _assert_no_mutations(client)
    allowed = driver.update(
        UpdateSpec(
            created.handle,
            managed_service_id=MSID,
            organization_id=ORG,
            recorded_handle_exclusive=True,
            recorded_container_exclusive=True,
        )
    )
    assert allowed.ok and instance["labels"]["astrolift-organization-id"] == ORG
    patch = [args for action, args in client.calls if action == "patch_instance"]
    assert len(patch) == 1 and patch[0]["update_mask"] == ["labels"]
    assert driver.update(UpdateSpec(created.handle, managed_service_id=MSID, organization_id=ORG)).ok


@pytest.mark.parametrize("organization_id", ["", "tenant-slug", OTHER_ORG])
def test_invalid_missing_or_foreign_org_cannot_be_bypassed_by_force_or_record(
    driver, client, organization_id: str
) -> None:
    created = driver.provision(_spec(config={"deletion_protection": False}))
    client.calls.clear()
    result = driver.deprovision(
        DeprovisionSpec(
            created.handle,
            managed_service_id=MSID,
            organization_id=organization_id,
            recorded_handle_exclusive=True,
            recorded_container_exclusive=True,
            config={"delete_empty_instance": True, "delete_adopted_database": True},
        ),
        delete_data=True,
        force_destroy=True,
    )
    assert not result.ok
    _assert_no_mutations(client)


def test_repeated_or_malformed_inventory_cannot_prove_container_ownership() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"databases": [], "nextPageToken": "repeat"}),
            FakeResponse(200, {"databases": [], "nextPageToken": "repeat"}),
        ]
    )
    with pytest.raises(SpannerGraphError, match="repeated"):
        SpannerRestClient(session=session).list_databases("projects/acme/instances/owned")
    assert len(session.calls) == 2
    session = FakeSession([FakeResponse(200, {"databases": ["not a database"]})])
    with pytest.raises(SpannerGraphError, match="invalid Spanner resource inventory"):
        SpannerRestClient(session=session).list_databases("projects/acme/instances/owned")


@pytest.mark.parametrize("resource", ["instance", "database"])
def test_provider_response_cannot_redirect_the_recorded_target(driver, client, resource: str) -> None:
    created = driver.provision(_spec(config={"deletion_protection": False}))
    instance_id, _ = _parse_handle(created.handle)
    if resource == "instance":
        client.instances[f"projects/acme/instances/{instance_id}"]["name"] = "projects/acme/instances/replaced"
    else:
        client.databases[_database_name(created.handle)]["name"] = (
            f"projects/acme/instances/{instance_id}/databases/replaced"
        )
    client.calls.clear()
    changed = driver.update(
        UpdateSpec(
            created.handle,
            managed_service_id=MSID,
            organization_id=ORG,
            recorded_container_exclusive=True,
            config={"deletion_protection": True},
        )
    )
    deleted = driver.deprovision(
        DeprovisionSpec(
            created.handle, managed_service_id=MSID, organization_id=ORG, recorded_container_exclusive=True
        ),
        delete_data=True,
        force_destroy=True,
    )
    assert not changed.ok and "identity" in changed.message
    assert not deleted.ok and "identity" in deleted.message and not deleted.retryable
    _assert_no_mutations(client)


def test_tenant_config_cannot_forge_internal_capacity_proof(driver, client) -> None:
    created = driver.provision(_spec())
    client.calls.clear()
    result = driver.update(
        UpdateSpec(
            created.handle,
            managed_service_id=MSID,
            organization_id=ORG,
            config={
                "manage_instance_capacity": True,
                "processing_units": 500,
                "recorded_container_exclusive": True,
                "organization_id": ORG,
            },
        )
    )
    assert not result.ok and "unknown Spanner Graph config keys" in result.message
    _assert_no_mutations(client)


def test_shared_initial_capacity_comes_from_operator_config(client) -> None:
    driver = _shared_driver(client)
    result = driver.provision(_spec(config={"processing_units": 500, "automatic_backup_schedule": False}))
    assert result.ok
    instance = next(iter(client.instances.values()))
    assert instance["processingUnits"] == 100 and instance["defaultBackupScheduleType"] == "AUTOMATIC"
