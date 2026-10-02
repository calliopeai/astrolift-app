from __future__ import annotations

from dataclasses import replace

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.aurora import AuroraConfig, AuroraMySQLDriver, AuroraPostgresDriver


class NotFound(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeRDS:
    def __init__(self):
        self.clusters: dict[str, dict] = {}
        self.instances: dict[str, dict] = {}
        self.cluster_creates: list[dict] = []
        self.instance_creates: list[dict] = []
        self.cluster_modifies: list[dict] = []
        self.instance_modifies: list[dict] = []
        self.cluster_deletes: list[dict] = []
        self.instance_deletes: list[dict] = []
        self.snapshots: list[dict] = []

    def describe_db_clusters(self, **kwargs):
        cluster_id = kwargs["DBClusterIdentifier"]
        if cluster_id not in self.clusters:
            raise NotFound("DBClusterNotFoundFault")
        return {"DBClusters": [self.clusters[cluster_id]]}

    def describe_db_instances(self, **kwargs):
        instance_id = kwargs["DBInstanceIdentifier"]
        if instance_id not in self.instances:
            raise NotFound("DBInstanceNotFoundFault")
        return {"DBInstances": [self.instances[instance_id]]}

    def create_db_cluster(self, **kwargs):
        self.cluster_creates.append(kwargs)
        self.clusters[kwargs["DBClusterIdentifier"]] = {
            "DBClusterIdentifier": kwargs["DBClusterIdentifier"],
            "Status": "creating",
            "Engine": kwargs["Engine"],
            "Endpoint": f"{kwargs['DBClusterIdentifier']}.writer.example",
            "ReaderEndpoint": f"{kwargs['DBClusterIdentifier']}.reader.example",
            "Port": kwargs["Port"],
            "DatabaseName": kwargs["DatabaseName"],
            "MasterUsername": kwargs["MasterUsername"],
            "TagList": kwargs.get("Tags", []),
            "DeletionProtection": kwargs["DeletionProtection"],
            "ServerlessV2ScalingConfiguration": kwargs.get("ServerlessV2ScalingConfiguration"),
            "DBClusterMembers": [],
        }

    def create_db_instance(self, **kwargs):
        self.instance_creates.append(kwargs)
        instance_id = kwargs["DBInstanceIdentifier"]
        self.instances[instance_id] = {
            "DBInstanceIdentifier": instance_id,
            "DBClusterIdentifier": kwargs["DBClusterIdentifier"],
            "TagList": kwargs.get("Tags", []),
            "DBInstanceStatus": "creating",
        }
        cluster = self.clusters[kwargs["DBClusterIdentifier"]]
        cluster["DBClusterMembers"].append({"DBInstanceIdentifier": instance_id})

    def modify_db_cluster(self, **kwargs):
        self.cluster_modifies.append(kwargs)
        cluster = self.clusters[kwargs["DBClusterIdentifier"]]
        if "DeletionProtection" in kwargs:
            cluster["DeletionProtection"] = kwargs["DeletionProtection"]
        if "ServerlessV2ScalingConfiguration" in kwargs:
            cluster["ServerlessV2ScalingConfiguration"] = kwargs["ServerlessV2ScalingConfiguration"]

    def modify_db_instance(self, **kwargs):
        self.instance_modifies.append(kwargs)

    def delete_db_instance(self, **kwargs):
        self.instance_deletes.append(kwargs)
        self.instances[kwargs["DBInstanceIdentifier"]]["DBInstanceStatus"] = "deleting"

    def delete_db_cluster(self, **kwargs):
        self.cluster_deletes.append(kwargs)
        self.clusters[kwargs["DBClusterIdentifier"]]["Status"] = "deleting"

    def create_db_cluster_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)

    def restore_db_cluster_from_snapshot(self, **kwargs):
        self.clusters[kwargs["DBClusterIdentifier"]] = {
            "DBClusterIdentifier": kwargs["DBClusterIdentifier"],
            "Status": "creating",
            "Engine": kwargs["Engine"],
            "Endpoint": "restored.writer.example",
            "ReaderEndpoint": "restored.reader.example",
            "Port": 5432 if kwargs["Engine"] == "aurora-postgresql" else 3306,
            "DatabaseName": "restored",
            "MasterUsername": "astrolift",
            "DeletionProtection": kwargs["DeletionProtection"],
            "DBClusterMembers": [],
        }


class FakeSecrets:
    def __init__(self):
        self.values: dict[str, str] = {}

    def create_secret(self, **kwargs):
        name = kwargs["Name"]
        value = kwargs["SecretString"]
        if name in self.values:
            raise Exception("ResourceExistsException")
        self.values[name] = value
        return {"ARN": f"arn:aws:secretsmanager:::secret:{name}"}

    def put_secret_value(self, **kwargs):
        self.values[kwargs["SecretId"]] = kwargs["SecretString"]

    def get_secret_value(self, **kwargs):
        return {"SecretString": self.values[kwargs["SecretId"]]}

    def delete_secret(self, **kwargs):
        self.values.pop(kwargs["SecretId"], None)


def spec(**config):
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="acme",
        app_id="app-id",
        app_slug="api",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="primary",
        size="medium",
        config=config,
        managed_service_id="00000000-0000-4000-8000-000000000001",
    )


def driver(*, engine="aurora-postgresql", serverless=True):
    rds = FakeRDS()
    sm = FakeSecrets()
    config = AuroraConfig(
        region="us-west-2",
        db_subnet_group="private-db",
        security_group_ids=["sg-db"],
        engine=engine,
        serverless_v2=serverless,
    )
    cls = AuroraPostgresDriver if engine == "aurora-postgresql" else AuroraMySQLDriver
    return cls(config=config, rds_client=rds, secrets_client=sm), rds, sm


def test_serverless_provision_reconciles_cluster_and_writer():
    subject, rds, sm = driver()

    result = subject.provision(spec(min_acu=1.5, max_acu=12.0))
    again = subject.provision(spec(min_acu=1.5, max_acu=12.0))

    assert result.ok and result.handle == "postgres/astrolift-00000000000040008000000000000001"
    assert again.ok
    assert len(rds.cluster_creates) == 1
    assert len(rds.instance_creates) == 1
    create = rds.cluster_creates[0]
    assert create["Engine"] == "aurora-postgresql"
    assert create["StorageEncrypted"] is True
    assert create["PubliclyAccessible"] is False
    assert create["ServerlessV2ScalingConfiguration"] == {"MinCapacity": 1.5, "MaxCapacity": 12.0}
    assert rds.instance_creates[0]["DBInstanceClass"] == "db.serverless"
    assert any(name.endswith("/master") for name in sm.values)


def test_provisioned_mysql_uses_sized_writer_and_portable_binding():
    subject, rds, sm = driver(engine="aurora-mysql", serverless=False)
    result = subject.provision(spec())
    cluster_id = result.handle.split("/", 1)[1]
    rds.clusters[cluster_id]["Status"] = "available"
    writer_id = rds.instance_creates[0]["DBInstanceIdentifier"]
    rds.instances[writer_id]["DBInstanceStatus"] = "available"

    status = subject.status(ServiceHandle(result.handle))
    binding = subject.binding(ServiceHandle(result.handle))

    assert rds.instance_creates[0]["DBInstanceClass"] == "db.r7g.large"
    assert status.state == "available"
    assert binding.env_vars["MYSQL_HOST"].literal.endswith(".writer.example")
    assert binding.env_vars["MYSQL_PASSWORD"].secret_ref.endswith("/master")
    assert sm.values[binding.env_vars["DATABASE_URL"].secret_ref].startswith("mysql://")


def test_serverless_scaling_validation_and_update():
    subject, rds, _ = driver()
    invalid = subject.provision(spec(min_acu=8, max_acu=2))
    assert not invalid.ok
    assert not rds.cluster_creates

    result = subject.provision(spec())
    updated = subject.update(UpdateSpec(handle=result.handle, size="large", config={"apply_immediately": True}))
    assert updated.ok
    assert rds.cluster_modifies[-1]["ServerlessV2ScalingConfiguration"] == {
        "MinCapacity": 2.0,
        "MaxCapacity": 32.0,
    }


def test_deprovision_respects_guard_then_deletes_members_before_cluster():
    subject, rds, _ = driver()
    result = subject.provision(spec(deletion_protection=True))
    blocked = subject.deprovision(DeprovisionSpec(result.handle))
    assert not blocked.ok

    first = subject.deprovision(DeprovisionSpec(result.handle), force_destroy=True)
    assert not first.ok
    assert rds.instance_deletes
    cluster_id = result.handle.split("/", 1)[1]
    rds.instances.clear()
    rds.clusters[cluster_id]["DBClusterMembers"] = []
    second = subject.deprovision(DeprovisionSpec(result.handle), force_destroy=True)
    assert second.ok
    assert rds.cluster_deletes[-1]["SkipFinalSnapshot"] is False
    assert "FinalDBSnapshotIdentifier" in rds.cluster_deletes[-1]


def test_snapshot_and_restore_copy_credentials_and_create_writer():
    subject, rds, sm = driver()
    source = subject.provision(spec())
    snap = subject.snapshot(ServiceHandle(source.handle))
    target = replace(
        spec(),
        app_slug="restored",
        service_handle_hint="copy",
        managed_service_id="00000000-0000-4000-8000-000000000002",
    )

    restored = subject.restore(snap, target)

    assert restored.ok
    assert rds.snapshots[0]["DBClusterSnapshotIdentifier"] == snap.snapshot_id
    assert len(rds.instance_creates) == 2
    target_id = restored.handle.split("/", 1)[1]
    assert sm.values[subject._password_secret(target_id)]


def test_catalog_binding_schema_is_variant_specific_without_constructor():
    pg = AuroraPostgresDriver.binding_schema.__wrapped__(object.__new__(AuroraPostgresDriver))
    mysql = AuroraMySQLDriver.binding_schema.__wrapped__(object.__new__(AuroraMySQLDriver))
    assert "POSTGRES_HOST" in pg.env_vars and "MYSQL_HOST" not in pg.env_vars
    assert "MYSQL_HOST" in mysql.env_vars and "POSTGRES_HOST" not in mysql.env_vars


@pytest.mark.parametrize("change", ["cluster", "owner", "missing_tags"])
def test_existing_writer_must_match_recorded_cluster_and_service(change):
    subject, rds, _ = driver()
    first = subject.provision(spec())
    assert first.ok
    cluster_id = first.handle.partition("/")[2]
    writer = rds.instances[subject._writer_id(cluster_id)]
    if change == "cluster":
        writer["DBClusterIdentifier"] = "another-cluster"
    elif change == "owner":
        writer["TagList"] = [
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/managed_service_id", "Value": "00000000-0000-4000-8000-000000000002"},
        ]
    else:
        writer.pop("TagList")
    refused = subject.provision(replace(spec(), recorded_handle=first.handle))
    assert not refused.ok and refused.errors == ["ownership_refused"]
    assert len(rds.instance_creates) == len(rds.cluster_creates) == 1


def test_new_parent_and_writer_keep_entire_identity_even_with_long_prefix():
    subject, rds, _ = driver()
    subject._config = replace(subject._config, cluster_name_prefix="p" * 300)
    identities = ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"]
    results = [subject.provision(replace(spec(), managed_service_id=identity)) for identity in identities]
    assert all(result.ok for result in results)
    assert len(rds.cluster_creates) == len(rds.instance_creates) == 2
    for identity, result in zip(identities, results, strict=True):
        cluster_id = result.handle.partition("/")[2]
        writer_id = subject._writer_id(cluster_id)
        assert cluster_id.endswith(identity.replace("-", ""))
        assert writer_id.endswith(identity.replace("-", "") + "-writer")
        assert len(cluster_id) <= 56 and len(writer_id) <= 63


def test_legacy_truncated_writer_locator_remains_but_cannot_adopt_other_cluster():
    subject, rds, _ = driver()
    first_name = "p" * 56 + "-one"
    second_name = "p" * 56 + "-two"
    first = subject.provision(replace(spec(), recorded_handle="postgres/" + first_name))
    assert first.ok
    assert subject._writer_id(first_name) == subject._writer_id(second_name)
    refused = subject.provision(
        replace(
            spec(), managed_service_id="00000000-0000-4000-8000-000000000002", recorded_handle="postgres/" + second_name
        )
    )
    assert not refused.ok and refused.errors == ["ownership_refused"]
    writer = rds.instances[subject._writer_id(first_name)]
    assert writer["DBClusterIdentifier"] == first_name
    assert len(rds.instance_creates) == 1
