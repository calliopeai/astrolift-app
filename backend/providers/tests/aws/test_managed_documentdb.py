from __future__ import annotations

from dataclasses import replace
from typing import Any

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.documentdb import (
    DocumentDBConfig,
    DocumentDBProvisionedDriver,
    DocumentDBServerlessV2Driver,
)


class NotFound(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeDocumentDB:
    def __init__(self) -> None:
        self.clusters: dict[str, dict[str, Any]] = {}
        self.instances: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def describe_db_clusters(self, **kwargs):
        self.calls.append(("DescribeDBClusters", kwargs))
        cluster_id = kwargs["DBClusterIdentifier"]
        if cluster_id not in self.clusters:
            raise NotFound("DBClusterNotFoundFault")
        return {"DBClusters": [self.clusters[cluster_id]]}

    def describe_db_instances(self, **kwargs):
        self.calls.append(("DescribeDBInstances", kwargs))
        cluster_id = kwargs["Filters"][0]["Values"][0]
        rows = [instance for instance in self.instances.values() if instance["DBClusterIdentifier"] == cluster_id]
        return {"DBInstances": rows}

    def create_db_cluster(self, **kwargs):
        self.calls.append(("CreateDBCluster", kwargs))
        self._add_cluster(kwargs, restored=False)

    def restore_db_cluster_from_snapshot(self, **kwargs):
        self.calls.append(("RestoreDBClusterFromSnapshot", kwargs))
        self._add_cluster(kwargs, restored=True)

    def _add_cluster(self, kwargs: dict[str, Any], *, restored: bool) -> None:
        cluster_id = kwargs["DBClusterIdentifier"]
        self.clusters[cluster_id] = {
            **kwargs,
            "DBClusterIdentifier": cluster_id,
            "DBClusterArn": f"arn:aws:rds:us-west-2:123456789012:cluster:{cluster_id}",
            "Status": "creating",
            "Endpoint": f"{cluster_id}.docdb.amazonaws.com",
            "Port": 27017,
            "MasterUsername": "snapshot-user" if restored else kwargs["MasterUsername"],
            "DeletionProtection": kwargs["DeletionProtection"],
        }

    def create_db_instance(self, **kwargs):
        self.calls.append(("CreateDBInstance", kwargs))
        instance_id = kwargs["DBInstanceIdentifier"]
        self.instances[instance_id] = {
            **kwargs,
            "DBInstanceIdentifier": instance_id,
            "DBInstanceStatus": "creating",
        }

    def modify_db_cluster(self, **kwargs):
        self.calls.append(("ModifyDBCluster", kwargs))
        self.clusters[kwargs["DBClusterIdentifier"]].update(kwargs)

    def modify_db_instance(self, **kwargs):
        self.calls.append(("ModifyDBInstance", kwargs))
        self.instances[kwargs["DBInstanceIdentifier"]].update(kwargs)

    def delete_db_instance(self, **kwargs):
        self.calls.append(("DeleteDBInstance", kwargs))
        self.instances[kwargs["DBInstanceIdentifier"]]["DBInstanceStatus"] = "deleting"

    def delete_db_cluster(self, **kwargs):
        self.calls.append(("DeleteDBCluster", kwargs))
        self.clusters[kwargs["DBClusterIdentifier"]]["Status"] = "deleting"

    def create_db_cluster_snapshot(self, **kwargs):
        self.calls.append(("CreateDBClusterSnapshot", kwargs))
        snapshot_id = kwargs["DBClusterSnapshotIdentifier"]
        return {
            "DBClusterSnapshot": {
                "DBClusterSnapshotArn": (f"arn:aws:rds:us-west-2:123456789012:cluster-snapshot:{snapshot_id}"),
            },
        }


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_secret_value(self, **kwargs):
        name = kwargs["SecretId"]
        if name not in self.values:
            raise NotFound("ResourceNotFoundException")
        return {"SecretString": self.values[name]}

    def create_secret(self, **kwargs):
        name = kwargs["Name"]
        if name in self.values:
            raise NotFound("ResourceExistsException")
        self.values[name] = kwargs["SecretString"]
        return {"ARN": f"arn:aws:secretsmanager:us-west-2:123456789012:secret:{name}"}

    def put_secret_value(self, **kwargs):
        self.values[kwargs["SecretId"]] = kwargs["SecretString"]

    def delete_secret(self, **kwargs):
        name = kwargs["SecretId"]
        if name not in self.values:
            raise NotFound("ResourceNotFoundException")
        del self.values[name]


def spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org",
        organization_slug="acme",
        app_id="app",
        app_slug="api",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="documents",
        size="medium",
        config=config,
        isolation="dedicated",
    )


def driver(*, serverless: bool = False):
    docdb = FakeDocumentDB()
    secrets = FakeSecrets()
    cls = DocumentDBServerlessV2Driver if serverless else DocumentDBProvisionedDriver
    subject = cls(
        config=DocumentDBConfig(
            region="us-west-2",
            db_subnet_group="private-documentdb",
            security_group_ids=["sg-documentdb"],
            serverless_v2=serverless,
        ),
        docdb_client=docdb,
        secrets_client=secrets,
    )
    return subject, docdb, secrets


def test_provisioned_cluster_binding_and_idempotence():
    subject, docdb, secrets = driver()
    result = subject.provision(
        spec(
            cloudwatch_log_exports=["audit", "profiler"],
            storage_type="iopt1",
        ),
    )
    again = subject.provision(
        spec(
            cloudwatch_log_exports=["audit", "profiler"],
            storage_type="iopt1",
        ),
    )

    assert result.ok and again.ok
    assert result.handle == "document_db/astrolift-acme-api-prod-documents"
    assert len([call for call in docdb.calls if call[0] == "CreateDBCluster"]) == 1
    assert len([call for call in docdb.calls if call[0] == "CreateDBInstance"]) == 2
    create = next(payload for operation, payload in docdb.calls if operation == "CreateDBCluster")
    assert create["StorageEncrypted"] is True
    assert create["StorageType"] == "iopt1"
    assert create["EnableCloudwatchLogsExports"] == ["audit", "profiler"]
    assert create["DBSubnetGroupName"] == "private-documentdb"
    assert create["VpcSecurityGroupIds"] == ["sg-documentdb"]
    assert all(
        payload["DBInstanceClass"] == "db.r6g.large"
        for operation, payload in docdb.calls
        if operation == "CreateDBInstance"
    )
    assert not [call for call in docdb.calls if call[0] == "ModifyDBInstance"]

    cluster_id = result.handle.split("/", 1)[1]
    docdb.clusters[cluster_id]["Status"] = "available"
    for instance in docdb.instances.values():
        instance["DBInstanceStatus"] = "available"
    assert subject.status(ServiceHandle(result.handle)).state == "available"

    binding = subject.binding(ServiceHandle(result.handle), {"database": "triage"})
    assert binding.env_vars["DOCDB_DB"].literal == "triage"
    assert binding.env_vars["DOCDB_USER"].literal == "astrolift"
    assert binding.env_vars["DOCDB_PASSWORD"].secret_ref.endswith("/password")
    uri = secrets.values[binding.env_vars["DOCDB_URI"].secret_ref]
    assert "tls=true" in uri and "replicaSet=rs0" in uri and "retryWrites=false" in uri


def test_serverless_v2_uses_dcu_scaling_and_serverless_instances():
    subject, docdb, _ = driver(serverless=True)
    result = subject.provision(spec(min_capacity=1.5, max_capacity=8.0))

    assert result.ok
    create = next(payload for operation, payload in docdb.calls if operation == "CreateDBCluster")
    assert create["ServerlessV2ScalingConfiguration"] == {
        "MinCapacity": 1.5,
        "MaxCapacity": 8.0,
    }
    assert all(
        payload["DBInstanceClass"] == "db.serverless"
        for operation, payload in docdb.calls
        if operation == "CreateDBInstance"
    )


def test_validation_rejects_invalid_capacity_and_instance_count():
    subject, docdb, _ = driver(serverless=True)

    backwards = subject.provision(spec(min_capacity=8, max_capacity=2))
    increment = subject.provision(spec(min_capacity=0.7, max_capacity=2))
    count = subject.provision(spec(num_instances=0))

    assert not backwards.ok and not increment.ok and not count.ok
    assert not [call for call in docdb.calls if call[0] == "CreateDBCluster"]


def test_update_is_noop_for_matching_class_and_applies_explicit_changes():
    subject, docdb, _ = driver()
    result = subject.provision(spec())

    no_change = subject.update(UpdateSpec(result.handle, size="medium"))
    changed = subject.update(
        UpdateSpec(
            result.handle,
            size="large",
            config={"backup_retention_days": 14, "apply_immediately": True},
        ),
    )

    assert no_change.ok and no_change.message.startswith("no DocumentDB")
    assert changed.ok
    cluster_update = next(payload for operation, payload in docdb.calls if operation == "ModifyDBCluster")
    assert cluster_update["BackupRetentionPeriod"] == 14
    instance_updates = [payload for operation, payload in docdb.calls if operation == "ModifyDBInstance"]
    assert instance_updates and all(row["DBInstanceClass"] == "db.r8g.large" for row in instance_updates)


def test_deprovision_checks_protection_before_removing_instances_and_converges():
    subject, docdb, secrets = driver()
    result = subject.provision(spec(deletion_protection=True))

    blocked = subject.deprovision(DeprovisionSpec(result.handle))
    assert not blocked.ok and blocked.retryable is False
    assert not [call for call in docdb.calls if call[0] == "DeleteDBInstance"]

    disabling = subject.deprovision(DeprovisionSpec(result.handle), force_destroy=True)
    assert not disabling.ok and disabling.retryable is True
    assert not [call for call in docdb.calls if call[0] == "DeleteDBInstance"]

    deleting_instances = subject.deprovision(DeprovisionSpec(result.handle), force_destroy=True)
    assert not deleting_instances.ok and deleting_instances.retryable is True
    assert len([call for call in docdb.calls if call[0] == "DeleteDBInstance"]) == 2

    docdb.instances.clear()
    deleting_cluster = subject.deprovision(DeprovisionSpec(result.handle), force_destroy=True)
    delete = next(payload for operation, payload in docdb.calls if operation == "DeleteDBCluster")
    assert not deleting_cluster.ok and delete["SkipFinalSnapshot"] is False
    assert "FinalDBSnapshotIdentifier" in delete

    cluster_id = result.handle.split("/", 1)[1]
    docdb.clusters.pop(cluster_id)
    final = subject.deprovision(DeprovisionSpec(result.handle), force_destroy=True)
    assert final.ok and not secrets.values


def test_snapshot_restore_copies_credentials_and_preserves_snapshot_username():
    subject, docdb, secrets = driver()
    source = subject.provision(spec())
    snapshot = subject.snapshot(ServiceHandle(source.handle))
    target = replace(spec(), app_slug="restored", service_handle_hint="copy")

    restored = subject.restore(snapshot, target)
    target_id = restored.handle.split("/", 1)[1]
    docdb.clusters[target_id]["Status"] = "available"
    binding = subject.binding(ServiceHandle(restored.handle))

    assert restored.ok
    restore = next(payload for operation, payload in docdb.calls if operation == "RestoreDBClusterFromSnapshot")
    assert restore["SnapshotIdentifier"] == snapshot.snapshot_id
    source_id = source.handle.split("/", 1)[1]
    assert (
        secrets.values[subject._password_secret_name(target_id)]
        == secrets.values[subject._password_secret_name(source_id)]
    )
    assert binding.env_vars["DOCDB_USER"].literal == "snapshot-user"


def test_current_botocore_accepts_all_documentdb_request_shapes():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    subject, docdb, _ = driver(serverless=True)
    result = subject.provision(spec(min_capacity=1.0, max_capacity=4.0))
    subject.update(
        UpdateSpec(
            result.handle,
            config={"min_capacity": 2.0, "max_capacity": 8.0},
        ),
    )
    subject.snapshot(ServiceHandle(result.handle))
    subject.deprovision(DeprovisionSpec(result.handle), force_destroy=True)

    service = Session().get_service_model("docdb")
    for operation, request in docdb.calls:
        validate_parameters(request, service.operation_model(operation).input_shape)
