"""Tests for the AWS DynamoDB managed-service driver (#371 / #982).

Exercises the ``ManagedServiceDriver`` protocol surface against a
recording fake (moto is not installed in this environment): provision
idempotency, the deprovision four-corner matrix + NotFound tolerance,
the IRSA binding shape (env + iam_grant, no static keys), status
mapping, snapshot/restore, plus the #982 wiring — plugin registration
and ``managed_config_for`` config resolution.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.dynamodb import (
    KIND,
    DynamoDBConfig,
    DynamoDBDriver,
    _final_backup_name,
)


class ResourceNotFoundException(Exception):  # noqa: N818
    """Mirrors the botocore exception class the driver sniffs for by name
    (``type(exc).__name__ == "ResourceNotFoundException"``); the name must
    match verbatim, so the conventional ``Error`` suffix can't apply."""


_ACCOUNT = "123456789012"
_REGION = "us-east-1"


class FakeDDB:
    """Recording fake for the DynamoDB client surface the driver uses.

    Stores created tables in-memory so describe/status/binding read back a
    coherent shape, and records every call so tests can assert the driver
    issued the right API sequence (create-once idempotency, protection
    flips). Deliberately small: only the methods the driver calls."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, Any]] = {}
        self.backups: dict[str, list[str]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, name: str, **kw: Any) -> None:
        self.calls.append((name, kw))

    def count(self, name: str) -> int:
        return sum(1 for c, _ in self.calls if c == name)

    def last(self, name: str) -> dict[str, Any]:
        for c, kw in reversed(self.calls):
            if c == name:
                return kw
        raise AssertionError(f"{name} was never called")

    def describe_table(self, TableName: str) -> dict[str, Any]:  # noqa: N803
        self._record("describe_table", TableName=TableName)
        if TableName not in self.tables:
            raise ResourceNotFoundException(
                f"Requested resource not found: Table: {TableName}",
            )
        return {"Table": self.tables[TableName]}

    def create_table(self, **kwargs: Any) -> dict[str, Any]:
        self._record("create_table", **kwargs)
        name = kwargs["TableName"]
        table = {
            "TableName": name,
            "TableStatus": "ACTIVE",
            "TableArn": f"arn:aws:dynamodb:{_REGION}:{_ACCOUNT}:table/{name}",
            "KeySchema": kwargs.get("KeySchema", []),
            "AttributeDefinitions": kwargs.get("AttributeDefinitions", []),
            "BillingModeSummary": {
                "BillingMode": kwargs.get("BillingMode", "PROVISIONED"),
            },
            "DeletionProtectionEnabled": bool(
                kwargs.get("DeletionProtectionEnabled", False),
            ),
            "Tags": kwargs.get("Tags", []),
        }
        if "ProvisionedThroughput" in kwargs:
            table["ProvisionedThroughput"] = kwargs["ProvisionedThroughput"]
        self.tables[name] = table
        return {"TableDescription": table}

    def update_table(self, **kwargs: Any) -> dict[str, Any]:
        self._record("update_table", **kwargs)
        name = kwargs["TableName"]
        if name not in self.tables:
            raise ResourceNotFoundException(name)
        t = self.tables[name]
        if "DeletionProtectionEnabled" in kwargs:
            t["DeletionProtectionEnabled"] = bool(
                kwargs["DeletionProtectionEnabled"],
            )
        if "BillingMode" in kwargs:
            t["BillingModeSummary"] = {"BillingMode": kwargs["BillingMode"]}
        if "ProvisionedThroughput" in kwargs:
            t["ProvisionedThroughput"] = kwargs["ProvisionedThroughput"]
        return {"TableDescription": t}

    def delete_table(self, TableName: str) -> dict[str, Any]:  # noqa: N803
        self._record("delete_table", TableName=TableName)
        if TableName not in self.tables:
            raise ResourceNotFoundException(TableName)
        return {"TableDescription": self.tables.pop(TableName)}

    def create_backup(
        self,
        TableName: str,  # noqa: N803
        BackupName: str,  # noqa: N803
    ) -> dict[str, Any]:
        self._record(
            "create_backup",
            TableName=TableName,
            BackupName=BackupName,
        )
        if TableName not in self.tables:
            raise ResourceNotFoundException(TableName)
        arn = f"arn:aws:dynamodb:{_REGION}:{_ACCOUNT}:table/{TableName}/backup/{BackupName}"
        self.backups.setdefault(TableName, []).append(arn)
        return {"BackupDetails": {"BackupArn": arn, "BackupName": BackupName}}

    def update_continuous_backups(self, **kwargs: Any) -> dict[str, Any]:
        self._record("update_continuous_backups", **kwargs)
        return {}

    def update_time_to_live(self, **kwargs: Any) -> dict[str, Any]:
        self._record("update_time_to_live", **kwargs)
        return {}

    def restore_table_from_backup(
        self,
        TargetTableName: str,  # noqa: N803
        BackupArn: str,  # noqa: N803
    ) -> dict[str, Any]:
        self._record(
            "restore_table_from_backup",
            TargetTableName=TargetTableName,
            BackupArn=BackupArn,
        )
        if "does-not-exist" in BackupArn:
            raise ResourceNotFoundException("backup not found")
        self.tables[TargetTableName] = {
            "TableName": TargetTableName,
            "TableStatus": "CREATING",
            "TableArn": (f"arn:aws:dynamodb:{_REGION}:{_ACCOUNT}:table/{TargetTableName}"),
            "KeySchema": [],
            "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
            "DeletionProtectionEnabled": False,
        }
        return {"TableDescription": self.tables[TargetTableName]}


@pytest.fixture
def fake() -> FakeDDB:
    return FakeDDB()


@pytest.fixture
def driver(fake: FakeDDB) -> DynamoDBDriver:
    return DynamoDBDriver(
        config=DynamoDBConfig(region=_REGION),
        ddb_client=fake,
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="sessions",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


def _cluster(provider_config: dict | None = None, *, region: str = "us-west-2"):
    return SimpleNamespace(
        slug="aws-prod",
        region=region,
        provider_config=provider_config or {},
        auth_config={},
    )


# ---- provision --------------------------------------------------


def test_provision_creates_table_pay_per_request_protected(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind, _ = parse_handle(result.handle)
    assert kind == KIND
    create = fake.last("create_table")
    assert create["BillingMode"] == "PAY_PER_REQUEST"
    assert create["DeletionProtectionEnabled"] is True
    # Default key schema: single HASH partition key on ``pk``.
    assert create["KeySchema"] == [{"AttributeName": "pk", "KeyType": "HASH"}]
    # PAY_PER_REQUEST tables carry no ProvisionedThroughput.
    assert "ProvisionedThroughput" not in create


def test_provision_idempotent_creates_table_once(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.ok and b.ok
    assert a.handle == b.handle
    assert "already exists" in b.message
    # The second call must DescribeTable -> see it -> skip CreateTable.
    assert fake.count("create_table") == 1


def test_provision_tags_carry_platform_namespace(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    driver.provision(_spec())
    tags = {t["Key"]: t["Value"] for t in fake.last("create_table")["Tags"]}
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/app"] == "api"
    assert tags["astrolift.io/environment"] == "prod"


def test_provision_provisioned_billing_uses_size_capacity(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    driver.provision(_spec(size="large", config={"billing_mode": "PROVISIONED"}))
    pt = fake.last("create_table")["ProvisionedThroughput"]
    assert pt == {"ReadCapacityUnits": 50, "WriteCapacityUnits": 50}


def test_provision_surfaces_create_failure() -> None:
    class Boom:
        def describe_table(self, **_: Any) -> dict:
            raise RuntimeError("ResourceNotFoundException")

        def create_table(self, **_: Any) -> dict:
            raise RuntimeError("synthetic create failure")

    d = DynamoDBDriver(config=DynamoDBConfig(region=_REGION), ddb_client=Boom())
    result = d.provision(_spec())
    assert not result.ok
    assert "create_table" in result.message


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_idempotent_when_already_gone(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="kv_store/does-not-exist"),
    )
    assert result.ok
    assert "already gone" in result.message
    # NotFound tolerance: never attempt a delete on a missing table.
    assert fake.count("delete_table") == 0


def test_deprovision_default_refuses_when_protected(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    provisioned = driver.provision(_spec())  # protection on by default
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert not result.ok
    assert "DeletionProtection" in result.message
    assert fake.count("delete_table") == 0


def test_deprovision_protection_off_takes_backup_then_deletes(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert "backup=taken" in result.message
    assert fake.count("create_backup") == 1
    assert fake.count("delete_table") == 1


def test_deprovision_delete_data_skips_backup(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "backup=skipped" in result.message
    assert fake.count("create_backup") == 0


def test_deprovision_force_destroy_clears_protection(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    provisioned = driver.provision(_spec())  # protected
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    assert "force_destroy=True" in result.message
    # Protection must be flipped off before the delete.
    flip = fake.last("update_table")
    assert flip["DeletionProtectionEnabled"] is False
    assert fake.count("delete_table") == 1


def test_deprovision_atomic_both_flags(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "backup=skipped" in result.message
    assert "force_destroy=True" in result.message
    assert fake.count("create_backup") == 0
    assert fake.count("delete_table") == 1


# ---- update -----------------------------------------------------


def test_update_noop_when_nothing_to_change(driver: DynamoDBDriver) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


def test_update_capacity_change_issues_update_table(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    provisioned = driver.provision(
        _spec(config={"billing_mode": "PROVISIONED"}),
    )
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "billing_mode": "PROVISIONED",
                "read_capacity": 25,
                "write_capacity": 25,
            },
        ),
    )
    assert result.ok
    pt = fake.last("update_table")["ProvisionedThroughput"]
    assert pt == {"ReadCapacityUnits": 25, "WriteCapacityUnits": 25}


# ---- status -----------------------------------------------------


def test_status_missing_is_deprovisioned(driver: DynamoDBDriver) -> None:
    state = driver.status(ServiceHandle(handle="kv_store/missing"))
    assert state.state == "deprovisioned"


def test_status_active_maps_to_available(driver: DynamoDBDriver) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


# ---- binding (IRSA, no static keys) -----------------------------


def test_binding_env_is_irsa_only_no_static_keys(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    assert "DYNAMODB_TABLE_NAME" in env
    assert env["DYNAMODB_TABLE_NAME"].literal is not None
    assert env["DYNAMODB_REGION"].literal == _REGION
    # Regression guard (#982): the binding must NOT inject static creds.
    # The bindings-Secret render hard-fails on any unresolvable
    # secret_ref, and IRSA workloads never get per-table keys minted.
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    for ref in env.values():
        assert ref.secret_ref is None


def test_binding_iam_grant_covers_item_ops(driver: DynamoDBDriver) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    actions = {a for grant in binding.iam_grants for a in grant.actions}
    for required in (
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:Scan",
    ):
        assert required in actions
    # Grants are scoped to the table ARN, never "*".
    for grant in binding.iam_grants:
        assert grant.resource.startswith("arn:aws:dynamodb:")


def test_binding_for_missing_raises(driver: DynamoDBDriver) -> None:
    with pytest.raises(ManagedServiceError):
        driver.binding(ServiceHandle(handle="kv_store/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_creates_backup(driver: DynamoDBDriver, fake: FakeDDB) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    assert snap.snapshot_id
    assert fake.count("create_backup") == 1


def test_snapshot_missing_raises(driver: DynamoDBDriver) -> None:
    with pytest.raises(ManagedServiceError):
        driver.snapshot(ServiceHandle(handle="kv_store/missing"))


def test_restore_creates_target_table(
    driver: DynamoDBDriver,
    fake: FakeDDB,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    result = driver.restore(snap, _spec(service_handle_hint="restored"))
    assert result.ok
    _, target = parse_handle(result.handle)
    assert target in fake.tables


def test_restore_surfaces_error_on_missing_backup(
    driver: DynamoDBDriver,
) -> None:
    bad = SnapshotHandle(
        handle="kv_store/anything",
        snapshot_id=(f"arn:aws:dynamodb:{_REGION}:{_ACCOUNT}:table/x/backup/does-not-exist"),
        created_at="",
    )
    result = driver.restore(bad, _spec(service_handle_hint="failed"))
    assert not result.ok
    assert "restore_table_from_backup" in result.message


# ---- naming + helpers ------------------------------------------


def test_table_name_canonicalization(driver: DynamoDBDriver) -> None:
    name = driver._table_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="users",
        ),
    )
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    for c in name:
        assert c.isalnum() or c in "-_."
    assert len(name) <= 255


def test_final_backup_name_is_bounded() -> None:
    name = _final_backup_name(table_name="x" * 400)
    assert len(name) <= 255


# ---- #982 wiring: config resolution + plugin registration -------


def test_managed_config_for_kv_store_defaults_and_overrides() -> None:
    from core.cluster_observability import managed_config_for

    cfg = managed_config_for("aws", _cluster(), kind="kv_store")
    assert isinstance(cfg, DynamoDBConfig)
    assert cfg.region == "us-west-2"
    assert cfg.table_name_prefix == "astrolift"
    assert cfg.billing_mode_default == "PAY_PER_REQUEST"

    pinned = managed_config_for(
        "aws",
        _cluster(
            {
                "table_name_prefix": "acme",
                "dynamodb_billing_mode": "PROVISIONED",
                "deletion_protection_default": False,
            },
        ),
        kind="kv_store",
    )
    assert pinned.table_name_prefix == "acme"
    assert pinned.billing_mode_default == "PROVISIONED"
    assert pinned.deletion_protection_default is False


def test_managed_service_kind_has_kv_store() -> None:
    from astrolift_services.models import ManagedService

    assert ManagedService.Kind.KV_STORE == "kv_store"
    assert "kv_store" in {c for c, _ in ManagedService.Kind.choices}


def test_plugin_registers_dynamodb_under_kv_store() -> None:
    from aws.plugin import PLUGIN

    assert PLUGIN.managed_service_drivers[("kv_store", "dynamodb")] is (DynamoDBDriver)
