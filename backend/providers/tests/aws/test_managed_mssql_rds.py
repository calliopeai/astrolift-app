from __future__ import annotations

from dataclasses import replace

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.mssql_rds import RDSSqlServerConfig, RDSSqlServerDriver

from .test_managed_aurora import FakeSecrets, NotFound


class FakeRDS:
    def __init__(self):
        self.instances: dict[str, dict] = {}
        self.creates: list[dict] = []
        self.modifies: list[dict] = []
        self.deletes: list[dict] = []
        self.snapshots: list[dict] = []

    def describe_orderable_db_instance_options(self, **kwargs):
        engine = kwargs["Engine"]
        classes = {
            "sqlserver-ex": ["db.t3.micro", "db.t3.medium", "db.m6i.xlarge"],
            "sqlserver-web": ["db.t3.small", "db.m5.large", "db.m6i.xlarge"],
            "sqlserver-se": ["db.t3.small", "db.m5.large", "db.m6i.xlarge"],
            "sqlserver-ee": ["db.t3.xlarge", "db.m6i.xlarge", "db.m6i.2xlarge"],
        }[engine]
        return {"OrderableDBInstanceOptions": [{"DBInstanceClass": item} for item in classes]}

    def describe_db_instances(self, **kwargs):
        instance_id = kwargs["DBInstanceIdentifier"]
        if instance_id not in self.instances:
            raise NotFound("DBInstanceNotFoundFault")
        return {"DBInstances": [self.instances[instance_id]]}

    def create_db_instance(self, **kwargs):
        self.creates.append(kwargs)
        self.instances[kwargs["DBInstanceIdentifier"]] = {
            "DBInstanceIdentifier": kwargs["DBInstanceIdentifier"],
            "DBInstanceStatus": "creating",
            "Endpoint": {"Address": "sql.example", "Port": 1433},
            "MasterUsername": kwargs["MasterUsername"],
            "DeletionProtection": kwargs["DeletionProtection"],
            "TagList": kwargs.get("Tags", []),
        }

    def modify_db_instance(self, **kwargs):
        self.modifies.append(kwargs)
        if "DeletionProtection" in kwargs:
            self.instances[kwargs["DBInstanceIdentifier"]]["DeletionProtection"] = kwargs["DeletionProtection"]

    def delete_db_instance(self, **kwargs):
        self.deletes.append(kwargs)
        self.instances[kwargs["DBInstanceIdentifier"]]["DBInstanceStatus"] = "deleting"

    def create_db_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)

    def restore_db_instance_from_db_snapshot(self, **kwargs):
        self.instances[kwargs["DBInstanceIdentifier"]] = {
            "DBInstanceIdentifier": kwargs["DBInstanceIdentifier"],
            "DBInstanceStatus": "creating",
            "Endpoint": {"Address": "restored-sql.example", "Port": 1433},
            "MasterUsername": "astrolift",
            "DeletionProtection": kwargs["DeletionProtection"],
        }


def spec():
    return ProvisionSpec(
        organization_id="org",
        organization_slug="acme",
        app_id="app",
        app_slug="api",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="orders",
        size="small",
        config={},
        managed_service_id="00000000-0000-4000-8000-000000000001",
    )


def driver(engine="sqlserver-ex"):
    rds = FakeRDS()
    sm = FakeSecrets()
    subject = RDSSqlServerDriver(
        config=RDSSqlServerConfig(
            region="us-west-2",
            db_subnet_group="private-db",
            security_group_ids=["sg-db"],
            engine=engine,
        ),
        rds_client=rds,
        secrets_client=sm,
    )
    return subject, rds, sm


def test_provision_is_private_encrypted_license_included_and_idempotent():
    subject, rds, _ = driver("sqlserver-ex")

    result = subject.provision(spec())
    again = subject.provision(spec())

    assert result.ok and again.ok
    assert result.handle == "mssql/astrolift-00000000000040008000000000000001"
    assert len(rds.creates) == 1
    create = rds.creates[0]
    assert create["Engine"] == "sqlserver-ex"
    assert create["LicenseModel"] == "license-included"
    assert create["PubliclyAccessible"] is False
    assert create["StorageEncrypted"] is True
    assert create["Port"] == 1433


def test_binding_and_update_use_portable_mssql_contract():
    subject, rds, sm = driver("sqlserver-se")
    result = subject.provision(spec())
    instance_id = result.handle.split("/", 1)[1]
    rds.instances[instance_id]["DBInstanceStatus"] = "available"

    binding = subject.binding(ServiceHandle(result.handle))
    updated = subject.update(
        UpdateSpec(
            handle=result.handle,
            size="medium",
            config={"multi_az": True, "apply_immediately": True},
        ),
    )

    assert binding.env_vars["MSSQL_HOST"].literal == "sql.example"
    assert binding.env_vars["MSSQL_ENCRYPT"].literal == "true"
    assert sm.values[binding.env_vars["DATABASE_URL"].secret_ref].startswith("sqlserver://")
    assert updated.ok
    assert rds.modifies[-1]["DBInstanceClass"] == "db.m5.large"
    assert rds.modifies[-1]["MultiAZ"] is True


def test_deprovision_guard_and_final_snapshot():
    subject, rds, _ = driver()
    result = subject.provision(spec())

    blocked = subject.deprovision(DeprovisionSpec(result.handle))
    deleted = subject.deprovision(DeprovisionSpec(result.handle), force_destroy=True)

    assert not blocked.ok
    assert deleted.ok
    assert rds.modifies[-1]["DeletionProtection"] is False
    assert rds.deletes[-1]["SkipFinalSnapshot"] is False
    assert "FinalDBSnapshotIdentifier" in rds.deletes[-1]


def test_all_supported_editions_are_accepted():
    for engine in ("sqlserver-ex", "sqlserver-web", "sqlserver-se", "sqlserver-ee"):
        subject, rds, _ = driver(engine)
        assert subject.provision(spec()).ok
        assert rds.creates[0]["Engine"] == engine


def test_express_rejects_multi_az_before_calling_aws():
    subject, rds, _ = driver("sqlserver-ex")
    request = replace(spec(), config={"multi_az": True})

    result = subject.provision(request)

    assert not result.ok
    assert "does not support Multi-AZ" in result.message
    assert not rds.creates


def test_an_instance_joins_only_an_option_group_the_operator_listed():
    # An option group can carry the IAM role native backup and restore reads
    # S3 with, so naming another tenant's restores from its bucket (#2087).
    subject, rds, sm = driver()
    listed = RDSSqlServerDriver(
        config=replace(subject._config, allowed_option_groups=("Platform-Native-Backup",)),
        rds_client=rds,
        secrets_client=sm,
    )

    refused = subject.provision(replace(spec(), config={"option_group": "other-tenant-backup-restore"}))
    accepted = listed.provision(replace(spec(), config={"option_group": "platform-native-backup"}))
    moved = listed.update(UpdateSpec(handle=accepted.handle, config={"option_group": "other-tenant-backup-restore"}))

    assert not refused.ok and "mssql_allowed_option_groups" in refused.message
    assert accepted.ok
    assert [create["OptionGroupName"] for create in rds.creates] == ["platform-native-backup"]
    assert not moved.ok and "mssql_allowed_option_groups" in moved.message
    assert rds.modifies == []
