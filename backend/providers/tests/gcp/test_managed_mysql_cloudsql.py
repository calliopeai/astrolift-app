"""Tests for GCP CloudSQL MySQL managed-service driver (#370 GCP).

Mirror of the CloudSQL Postgres tests (#363) — the engine is the
only operator-facing difference. Same fake-client pattern, same
four-corner deprovision matrix, same handle/secret semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
)
from gcp.managed.mysql_cloudsql import (
    KIND,
    CloudSQLMySQLConfig,
    CloudSQLMySQLDriver,
    _generate_master_password,
    _parse_handle,
)
from gcp.secrets import GCPSecretsBackend, GCPSecretsConfig

# ---- fakes -----------------------------------------------------------


@dataclass
class FakeSqlInstance:
    name: str
    state: str = "RUNNABLE"
    settings: dict[str, Any] = field(default_factory=dict)
    ipAddresses: list[dict[str, Any]] = field(default_factory=list)  # noqa: N815 — mirrors the CloudSQL API shape


class FakeSqlClient:
    def __init__(self):
        self.instances: dict[str, FakeSqlInstance] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, op: str, **kwargs):
        self.calls.append((op, kwargs))

    def insert(self, *, project, body):
        self._record("insert", project=project, body=body)
        name = body["name"]
        ip_kind = (
            "PRIVATE"
            if body.get("settings", {})
            .get(
                "ipConfiguration",
                {},
            )
            .get("privateNetwork")
            else "PRIMARY"
        )
        self.instances[name] = FakeSqlInstance(
            name=name,
            state="RUNNABLE",
            settings=body.get("settings", {}),
            ipAddresses=[{"type": ip_kind, "ipAddress": "10.0.0.7"}],
        )

    def get(self, *, project, instance):
        self._record("get", project=project, instance=instance)
        if instance not in self.instances:
            raise RuntimeError("404 not found")
        return self.instances[instance]

    def patch(self, *, project, instance, body):
        self._record(
            "patch",
            project=project,
            instance=instance,
            body=body,
        )
        inst = self.instances.get(instance)
        if inst is None:
            raise RuntimeError("404 not found")
        for k, v in (body.get("settings", {}) or {}).items():
            inst.settings[k] = v

    def delete(self, *, project, instance):
        self._record("delete", project=project, instance=instance)
        if instance not in self.instances:
            raise RuntimeError("404 not found")
        del self.instances[instance]

    def insert_backup_run(self, *, project, instance, body):
        self._record(
            "insert_backup_run",
            project=project,
            instance=instance,
            body=body,
        )

    def clone(self, *, project, instance, body):
        self._record(
            "clone",
            project=project,
            instance=instance,
            body=body,
        )
        target = body["cloneContext"]["destinationInstanceName"]
        self.instances[target] = FakeSqlInstance(
            name=target,
            state="PENDING_CREATE",
        )


class FakeSecretClient:
    def __init__(self):
        self.secrets: dict[str, list[bytes]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_secret(self, *, request):
        self.calls.append(("create_secret", request))
        sid = request["secret_id"]
        if sid in self.secrets:
            raise RuntimeError("AlreadyExists")
        self.secrets[sid] = []

    def add_secret_version(self, *, request):
        self.calls.append(("add_secret_version", request))
        parent = request["parent"]
        sid = parent.split("/secrets/")[-1]
        if sid not in self.secrets:
            self.secrets[sid] = []
        self.secrets[sid].append(request["payload"]["data"])

    def access_secret_version(self, *, request=None, name=None):
        full_name = (request or {}).get("name") or name
        sid = full_name.split("/secrets/", 1)[-1].split("/versions/", 1)[0]
        versions = self.secrets.get(sid)
        if not versions:
            raise RuntimeError("404 not found")
        return SimpleNamespace(payload=SimpleNamespace(data=versions[-1]))

    def delete_secret(self, *, request):
        self.calls.append(("delete_secret", request))
        name = request["name"]
        sid = name.split("/secrets/")[-1]
        self.secrets.pop(sid, None)


@pytest.fixture
def driver():
    return CloudSQLMySQLDriver(
        config=CloudSQLMySQLConfig(
            project_id="acme-prod",
            region="us-west1",
        ),
        sql_client=FakeSqlClient(),
        secrets_client=FakeSecretClient(),
    )


def _spec(**overrides) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="gcp-prod",
        service_handle_hint="my",
        size="small",
        managed_service_id="00000000-0000-4000-8000-000000000001",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_instance(driver):
    result = driver.provision(_spec())
    assert result.ok
    kind, instance_id = result.handle.partition("/")[0::2]
    assert kind == KIND
    assert instance_id in driver._sql.instances  # type: ignore[attr-defined]


def test_provision_default_engine_is_mysql_8(driver):
    driver.provision(_spec())
    body = next(kw["body"] for op, kw in driver._sql.calls if op == "insert")
    assert body["databaseVersion"] == "MYSQL_8_0"


def test_provision_engine_version_overridable(driver):
    driver.provision(_spec(config={"engine_version": "MYSQL_5_7"}))
    body = next(kw["body"] for op, kw in driver._sql.calls if op == "insert")
    assert body["databaseVersion"] == "MYSQL_5_7"


def test_provision_idempotent(driver):
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert "already exists" in b.message


def test_provision_stores_master_password_in_secret_manager(driver):
    result = driver.provision(_spec())
    instance_id = _parse_handle(result.handle)
    secret_id = f"astrolift-cloudsql-{instance_id}-master"
    versions = driver._sm.secrets[secret_id]  # type: ignore[attr-defined]
    assert len(versions) == 1
    assert len(versions[0]) >= 16


def test_provision_size_to_tier(driver):
    result = driver.provision(_spec(size="large"))
    instance_id = _parse_handle(result.handle)
    inst = driver._sql.instances[instance_id]  # type: ignore[attr-defined]
    assert inst.settings["tier"] == "db-custom-4-15360"
    assert inst.settings["dataDiskSizeGb"] == 100


def test_provision_honours_high_availability(driver):
    result = driver.provision(_spec(config={"high_availability": True}))
    instance_id = _parse_handle(result.handle)
    inst = driver._sql.instances[instance_id]  # type: ignore[attr-defined]
    assert inst.settings["availabilityType"] == "REGIONAL"


def test_provision_deletion_protection_default_on(driver):
    result = driver.provision(_spec())
    instance_id = _parse_handle(result.handle)
    inst = driver._sql.instances[instance_id]  # type: ignore[attr-defined]
    assert inst.settings["deletionProtectionEnabled"] is True


def test_provision_enables_binlog_for_pitr(driver):
    """MySQL PITR uses binlogs (vs Postgres WAL); driver must
    enable ``binaryLogEnabled`` so the operator gets the same
    point-in-time recovery semantics as Postgres."""
    driver.provision(_spec())
    body = next(kw["body"] for op, kw in driver._sql.calls if op == "insert")
    assert body["settings"]["backupConfiguration"]["binaryLogEnabled"] is True


def test_provision_rolls_back_secret_on_failure():
    class RaisingSql:
        def get(self, **_):
            raise RuntimeError("404")

        def insert(self, **_):
            raise RuntimeError("synthetic failure")

    sm = FakeSecretClient()
    d = CloudSQLMySQLDriver(
        config=CloudSQLMySQLConfig(project_id="acme", region="us-west1"),
        sql_client=RaisingSql(),
        secrets_client=sm,
    )
    result = d.provision(_spec())
    assert not result.ok
    # Secret should have been cleaned up after the insert failed.
    assert not sm.secrets


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_when_protected(driver):
    provisioned = driver.provision(_spec())  # default protection on
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert not result.ok
    assert "deletionProtection" in result.message


def test_deprovision_delete_data_with_protection_off_skips_backup(driver):
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "backup=skipped" in result.message
    sql = driver._sql  # type: ignore[attr-defined]
    assert not any(op == "insert_backup_run" for op, _ in sql.calls)


def test_deprovision_default_with_protection_off_takes_backup(driver):
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "backup=taken" in result.message
    sql = driver._sql  # type: ignore[attr-defined]
    assert any(op == "insert_backup_run" for op, _ in sql.calls)


def test_deprovision_force_destroy_clears_protection_first(driver):
    provisioned = driver.provision(_spec())  # protection on
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    sql = driver._sql  # type: ignore[attr-defined]
    patch_calls = [k for op, k in sql.calls if op == "patch"]
    delete_calls = [k for op, k in sql.calls if op == "delete"]
    assert any(k["body"]["settings"].get("deletionProtectionEnabled") is False for k in patch_calls)
    assert delete_calls
    op_seq = [op for op, _ in sql.calls]
    assert op_seq.index("patch") < op_seq.index("delete")


def test_deprovision_atomic_both_flags(driver):
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "backup=skipped" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_idempotent_when_already_gone(driver):
    result = driver.deprovision(
        DeprovisionSpec(handle="mysql/does-not-exist"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_delete_data_drops_master_secret(driver):
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    instance_id = _parse_handle(provisioned.handle)
    driver.binding(ServiceHandle(handle=provisioned.handle))
    secret_ids = {
        f"astrolift-cloudsql-{instance_id}-master",
        f"astrolift-cloudsql-{instance_id}-url",
    }
    assert secret_ids <= driver._sm.secrets.keys()  # type: ignore[attr-defined]
    driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert not (secret_ids & driver._sm.secrets.keys())  # type: ignore[attr-defined]


# ---- status / binding ------------------------------------------


def test_status_for_missing_returns_deprovisioned(driver):
    state = driver.status(ServiceHandle(handle="mysql/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_runnable_to_available(driver):
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_pending_create_to_provisioning(driver):
    provisioned = driver.provision(_spec())
    instance_id = _parse_handle(provisioned.handle)
    driver._sql.instances[instance_id].state = "PENDING_CREATE"  # type: ignore[attr-defined]
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "provisioning"


def test_binding_returns_mysql_envelope(driver):
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    for key in (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_DB",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "DATABASE_URL",
    ):
        assert key in env
    assert env["MYSQL_PASSWORD"].secret_ref is not None
    assert env["MYSQL_USER"].literal == "root"
    assert env["MYSQL_PORT"].literal == "3306"


def test_binding_secrets_resolve_through_cluster_backend(driver):
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    backend = GCPSecretsBackend(
        config=GCPSecretsConfig(
            project_id="acme-prod",
            client=driver._sm,  # type: ignore[attr-defined]
        ),
    )
    password_ref = binding.env_vars["MYSQL_PASSWORD"].secret_ref
    url_ref = binding.env_vars["DATABASE_URL"].secret_ref
    assert password_ref and backend.get(password_ref)
    assert url_ref
    url = backend.get(url_ref)
    assert url and next(iter(url.values())).startswith("mysql://root:")
    assert "@10.0.0.7:3306/mysql?ssl-mode=REQUIRED" in next(iter(url.values()))
    assert binding.iam_grants == []


def test_binding_url_secret_is_idempotent(driver):
    provisioned = driver.provision(_spec())
    handle = ServiceHandle(handle=provisioned.handle)
    driver.binding(handle)
    driver.binding(handle)
    instance_id = _parse_handle(provisioned.handle)
    versions = driver._sm.secrets[f"astrolift-cloudsql-{instance_id}-url"]  # type: ignore[attr-defined]
    assert len(versions) == 1


# ---- snapshot + restore ----------------------------------------


def test_snapshot_records_backup_run(driver):
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    instance_id = _parse_handle(provisioned.handle)
    assert snap.snapshot_id.startswith(instance_id)


def test_restore_clones_instance(driver):
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    target_id = _parse_handle(result.handle)
    assert f"astrolift-cloudsql-{target_id}-master" in driver._sm.secrets  # type: ignore[attr-defined]


def test_existing_instance_without_password_is_not_reported_healthy(driver):
    provisioned = driver.provision(_spec())
    instance_id = _parse_handle(provisioned.handle)
    driver._sm.secrets.clear()  # type: ignore[attr-defined]
    retried = driver.provision(_spec())
    assert not retried.ok
    assert retried.errors == ["missing_master_password_secret"]
    assert instance_id in retried.handle


# ---- module helpers --------------------------------------------


def test_password_safe_alphabet():
    pw = _generate_master_password(length=64)
    assert len(pw) == 64
    forbidden = set('/@"\\ ')
    assert not (set(pw) & forbidden)


def test_instance_id_sanitized():
    """Underscores + uppercase collapse; ≤98 chars."""
    d = CloudSQLMySQLDriver(
        config=CloudSQLMySQLConfig(project_id="p", region="r"),
        sql_client=FakeSqlClient(),
        secrets_client=FakeSecretClient(),
    )
    spec = _spec(app_slug="my_app", environment_name="DEV")
    iid = d._instance_id_for(spec=spec)  # type: ignore[attr-defined]
    assert iid == iid.lower()
    assert "_" not in iid
    assert len(iid) <= 98


# ---- schemas -----------------------------------------------------


def test_config_schema_shape(driver):
    schema = driver.config_schema()
    for key in (
        "engine_version",
        "tier",
        "storage_gb",
        "high_availability",
        "deletion_protection",
        "backup_retention_days",
        "kms_key_name",
        "zone",
    ):
        assert key in schema["properties"]


def test_binding_schema_lists_all_env_vars(driver):
    schema = driver.binding_schema()
    for key in (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_DB",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "DATABASE_URL",
    ):
        assert key in schema.env_vars


def test_provision_does_not_adopt_another_services_resource(driver) -> None:
    """An immutable name is a locator, never authority to adopt another owner."""
    import dataclasses

    first = driver.provision(dataclasses.replace(_spec(), managed_service_id="00000000-0000-4000-8000-000000000001"))
    second = driver.provision(
        dataclasses.replace(
            _spec(), managed_service_id="00000000-0000-4000-8000-000000000002", recorded_handle=first.handle
        )
    )

    assert first.ok, first.message
    assert not second.ok and "refusing to adopt" in second.message
