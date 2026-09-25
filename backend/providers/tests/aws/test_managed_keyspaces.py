from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.keyspaces import KeyspacesConfig, KeyspacesDriver


class NotFound(Exception):
    def __init__(self):
        super().__init__("ResourceNotFoundException")
        self.response = {"Error": {"Code": "ResourceNotFoundException"}}


class FakeKeyspaces:
    def __init__(self) -> None:
        self.keyspaces: dict[str, dict[str, Any]] = {}
        self.tables: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_keyspace(self, **kwargs):
        self.calls.append(("GetKeyspace", kwargs))
        name = kwargs["keyspaceName"]
        if name not in self.keyspaces:
            raise NotFound()
        return self.keyspaces[name]

    def list_tags_for_resource(self, **kwargs):
        for row in [*self.keyspaces.values(), *self.tables.values()]:
            if row["resourceArn"] == kwargs["resourceArn"]:
                return {"tags": row.get("tags", [])}
        return {"tags": []}

    def create_keyspace(self, **kwargs):
        self.calls.append(("CreateKeyspace", kwargs))
        name = kwargs["keyspaceName"]
        self.keyspaces[name] = {
            **kwargs,
            "resourceArn": f"arn:aws:cassandra:us-west-2:123456789012:/keyspace/{name}/",
        }

    def delete_keyspace(self, **kwargs):
        self.calls.append(("DeleteKeyspace", kwargs))

    def get_table(self, **kwargs):
        self.calls.append(("GetTable", kwargs))
        key = (kwargs["keyspaceName"], kwargs["tableName"])
        if key not in self.tables:
            raise NotFound()
        return self.tables[key]

    def create_table(self, **kwargs):
        self.calls.append(("CreateTable", kwargs))
        key = (kwargs["keyspaceName"], kwargs["tableName"])
        self.tables[key] = {
            **kwargs,
            "status": "CREATING",
            "resourceArn": (f"arn:aws:cassandra:us-west-2:123456789012:/keyspace/{key[0]}/table/{key[1]}"),
        }

    def update_table(self, **kwargs):
        self.calls.append(("UpdateTable", kwargs))
        key = (kwargs["keyspaceName"], kwargs["tableName"])
        table = self.tables[key]
        for request_key, response_key in (
            ("capacitySpecification", "capacitySpecification"),
            ("encryptionSpecification", "encryptionSpecification"),
            ("pointInTimeRecovery", "pointInTimeRecovery"),
            ("ttl", "ttl"),
            ("defaultTimeToLive", "defaultTimeToLive"),
            ("clientSideTimestamps", "clientSideTimestamps"),
        ):
            if request_key in kwargs:
                table[response_key] = kwargs[request_key]

    def delete_table(self, **kwargs):
        self.calls.append(("DeleteTable", kwargs))
        key = (kwargs["keyspaceName"], kwargs["tableName"])
        self.tables[key]["status"] = "DELETING"

    def restore_table(self, **kwargs):
        self.calls.append(("RestoreTable", kwargs))
        key = (kwargs["targetKeyspaceName"], kwargs["targetTableName"])
        self.tables[key] = {
            "keyspaceName": key[0],
            "tableName": key[1],
            "status": "RESTORING",
            "resourceArn": (f"arn:aws:cassandra:us-west-2:123456789012:/keyspace/{key[0]}/table/{key[1]}"),
            "pointInTimeRecovery": kwargs.get(
                "pointInTimeRecoveryOverride",
                {"status": "ENABLED"},
            ),
        }


def spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org",
        organization_slug="acme",
        app_id="app",
        app_slug="api",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="events",
        size="medium",
        config=config,
    )


def driver():
    client = FakeKeyspaces()
    subject = KeyspacesDriver(
        config=KeyspacesConfig(
            region="us-west-2",
            account_id="123456789012",
        ),
        keyspaces_client=client,
    )
    return subject, client


def test_provision_binding_status_and_idempotence():
    subject, client = driver()
    result = subject.provision(spec())
    again = subject.provision(spec())
    keyspace, table = result.handle.split("/", 1)[1].split("/", 1)
    client.tables[(keyspace, table)]["status"] = "ACTIVE"

    status = subject.status(ServiceHandle(result.handle))
    binding = subject.binding(ServiceHandle(result.handle))

    assert result.ok and again.ok
    assert len([call for call in client.calls if call[0] == "CreateKeyspace"]) == 1
    assert len([call for call in client.calls if call[0] == "CreateTable"]) == 1
    assert not [call for call in client.calls if call[0] == "UpdateTable"]
    create = next(payload for operation, payload in client.calls if operation == "CreateTable")
    assert create["capacitySpecification"] == {"throughputMode": "PAY_PER_REQUEST"}
    assert create["pointInTimeRecovery"] == {"status": "ENABLED"}
    assert create["schemaDefinition"]["partitionKeys"] == [{"name": "partition_key"}]
    assert status.state == "available"
    assert binding.env_vars["WIDE_COLUMN_ENDPOINT"].literal == "cassandra.us-west-2.amazonaws.com"
    assert binding.env_vars["WIDE_COLUMN_TABLE"].literal == "events"
    assert binding.env_vars["WIDE_COLUMN_AUTH_MODE"].literal == "AWS_SIGV4"
    assert binding.iam_grants[0].actions == ["cassandra:Select", "cassandra:Modify"]


def test_provisioned_multi_region_table_exposes_native_options():
    subject, client = driver()
    result = subject.provision(
        spec(
            throughput_mode="PROVISIONED",
            read_capacity=40,
            write_capacity=20,
            replication_regions=["us-west-2", "us-east-1"],
            ttl_enabled=True,
            default_time_to_live=86400,
            kms_key_arn="arn:aws:kms:us-west-2:123456789012:key/example",
            cdc_specification={"status": "ENABLED", "viewType": "NEW_AND_OLD_IMAGES"},
        ),
    )

    assert result.ok
    keyspace_create = next(payload for operation, payload in client.calls if operation == "CreateKeyspace")
    table_create = next(payload for operation, payload in client.calls if operation == "CreateTable")
    assert keyspace_create["replicationSpecification"] == {
        "replicationStrategy": "MULTI_REGION",
        "regionList": ["us-west-2", "us-east-1"],
    }
    assert table_create["capacitySpecification"] == {
        "throughputMode": "PROVISIONED",
        "readCapacityUnits": 40,
        "writeCapacityUnits": 20,
    }
    assert table_create["clientSideTimestamps"] == {"status": "ENABLED"}
    assert table_create["ttl"] == {"status": "ENABLED"}
    assert table_create["defaultTimeToLive"] == 86400
    assert table_create["encryptionSpecification"]["type"] == "CUSTOMER_MANAGED_KMS_KEY"


def test_update_capacity_pitr_and_schema_additions():
    subject, client = driver()
    result = subject.provision(spec())

    updated = subject.update(
        UpdateSpec(
            result.handle,
            size="large",
            config={
                "throughput_mode": "PROVISIONED",
                "point_in_time_recovery": False,
                "add_columns": [{"name": "payload", "type": "text"}],
            },
        ),
    )

    assert updated.ok
    request = next(payload for operation, payload in client.calls if operation == "UpdateTable")
    assert request["capacitySpecification"] == {
        "throughputMode": "PROVISIONED",
        "readCapacityUnits": 100,
        "writeCapacityUnits": 100,
    }
    assert request["pointInTimeRecovery"] == {"status": "DISABLED"}
    assert request["addColumns"] == [{"name": "payload", "type": "text"}]


def test_deprovision_requires_retention_or_ack_and_converges():
    subject, client = driver()
    result = subject.provision(spec(point_in_time_recovery=False))

    blocked = subject.deprovision(DeprovisionSpec(result.handle))
    deleting = subject.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    assert not blocked.ok and blocked.retryable is False
    assert not deleting.ok and deleting.retryable is True

    keyspace, table = result.handle.split("/", 1)[1].split("/", 1)
    client.tables.pop((keyspace, table))
    deleting_keyspace = subject.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    assert not deleting_keyspace.ok and deleting_keyspace.retryable is True
    client.keyspaces.pop(keyspace)
    final = subject.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    assert final.ok


def test_destructive_deprovision_disables_pitr_before_table_delete():
    subject, client = driver()
    result = subject.provision(spec())

    disabling = subject.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    deleting = subject.deprovision(DeprovisionSpec(result.handle), delete_data=True)

    assert not disabling.ok and disabling.errors == ["pitr_disable_in_progress"]
    assert not deleting.ok and deleting.errors == ["table_deletion_in_progress"]
    operations = [operation for operation, _ in client.calls]
    assert operations.index("UpdateTable") < operations.index("DeleteTable")


def test_safe_deprovision_retains_empty_keyspace_for_deleted_table_restore():
    subject, client = driver()
    result = subject.provision(spec())
    deleting = subject.deprovision(DeprovisionSpec(result.handle))
    keyspace, table = result.handle.split("/", 1)[1].split("/", 1)
    client.tables.pop((keyspace, table))

    retained = subject.deprovision(DeprovisionSpec(result.handle))

    assert not deleting.ok and retained.ok
    assert "35-day PITR" in retained.message
    assert keyspace in client.keyspaces
    assert not [call for call in client.calls if call[0] == "DeleteKeyspace"]


def test_snapshot_restore_uses_pitr_timestamp_and_target_coordinates():
    subject, client = driver()
    source = subject.provision(spec())
    snapshot = subject.snapshot(ServiceHandle(source.handle))
    target = replace(spec(), app_slug="restored", service_handle_hint="copy")

    restored = subject.restore(snapshot, target)
    request = next(payload for operation, payload in client.calls if operation == "RestoreTable")

    assert restored.ok
    assert request["sourceTableName"] == "events"
    assert request["targetTableName"] == "copy"
    assert request["restoreTimestamp"].isoformat() == snapshot.snapshot_id


def test_snapshot_requires_pitr_and_handle_validation_is_clear():
    subject, _ = driver()
    result = subject.provision(spec(point_in_time_recovery=False))

    with pytest.raises(ManagedServiceError, match="point-in-time recovery"):
        subject.snapshot(ServiceHandle(result.handle))
    with pytest.raises(ManagedServiceError, match="invalid Amazon Keyspaces handle"):
        subject.binding(ServiceHandle("wide_column/malformed"))


def test_multi_region_validation_requires_home_and_second_region():
    subject, client = driver()

    missing_home = subject.provision(spec(replication_regions=["us-east-1", "eu-west-1"]))
    only_home = subject.provision(spec(replication_regions=["us-west-2"]))

    assert not missing_home.ok and not only_home.ok
    assert not [call for call in client.calls if call[0] == "CreateKeyspace"]


def test_current_botocore_accepts_all_keyspaces_request_shapes():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    subject, client = driver()
    source = subject.provision(
        spec(
            throughput_mode="PROVISIONED",
            replication_regions=["us-west-2", "us-east-1"],
            cdc_specification={"status": "ENABLED", "viewType": "NEW_IMAGE"},
        ),
    )
    subject.update(
        UpdateSpec(
            source.handle,
            config={"point_in_time_recovery": True, "default_time_to_live": 60},
        ),
    )
    snapshot = subject.snapshot(ServiceHandle(source.handle))
    subject.restore(snapshot, replace(spec(), app_slug="copy", service_handle_hint="copy"))
    subject.deprovision(DeprovisionSpec(source.handle))

    service = Session().get_service_model("keyspaces")
    for operation, request in client.calls:
        validate_parameters(request, service.operation_model(operation).input_shape)


def test_provision_does_not_adopt_another_services_resource():
    """Names are slug-joined, so another service can map to this one's name (#1961)."""
    import dataclasses

    subject, client = driver()[:2]
    first = subject.provision(dataclasses.replace(spec(), managed_service_id="svc-a"))
    calls = len(client.calls)

    second = subject.provision(dataclasses.replace(spec(), managed_service_id="svc-b"))

    assert first.ok, first.message
    assert not second.ok and second.handle == "" and "refusing to adopt" in second.message
    assert not any(name.startswith(("Create", "Update", "Modify")) for name, _ in client.calls[calls:])
