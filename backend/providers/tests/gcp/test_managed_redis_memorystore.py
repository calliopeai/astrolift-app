"""Tests for GCP Memorystore Redis managed-service driver (#363).

Same fake-client pattern as the CloudSQL tests — no GCP emulator
exists, so we drive the SDK via call-recording fakes that return
canned responses. Test surface mirrors AWS ElastiCache (#352).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
)
from gcp.managed.redis_memorystore import (
    KIND,
    MemorystoreConfig,
    MemorystoreRedisDriver,
    _generate_auth_token,
    _parse_handle,
)

# ---- fakes -----------------------------------------------------------


@dataclass
class FakeRedisInstance:
    name: str
    host: str = "10.0.0.10"
    port: int = 6379
    state: str = "READY"
    transit_encryption_mode: str = "SERVER_AUTHENTICATION"
    auth_enabled: bool = True


class FakeRedisClient:
    def __init__(self):
        self.instances: dict[str, FakeRedisInstance] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, op, **kwargs):
        self.calls.append((op, kwargs))

    def create_instance(self, *, request):
        self._record("create_instance", request=request)
        full_name = (
            request["parent"]
            + "/instances/"
            + request["instance_id"]
        )
        inst_body = request["instance"]
        self.instances[request["instance_id"]] = FakeRedisInstance(
            name=full_name,
            host="10.0.0.10",
            transit_encryption_mode=inst_body.get(
                "transit_encryption_mode", "DISABLED",
            ),
            auth_enabled=bool(inst_body.get("auth_enabled", False)),
        )

    def get_instance(self, *, request):
        self._record("get_instance", request=request)
        instance_id = request["name"].rsplit("/", 1)[-1]
        if instance_id not in self.instances:
            raise RuntimeError("404 NotFound")
        return self.instances[instance_id]

    def update_instance(self, *, request):
        self._record("update_instance", request=request)
        instance_id = request["instance"]["name"].rsplit("/", 1)[-1]
        if instance_id not in self.instances:
            raise RuntimeError("404 NotFound")

    def delete_instance(self, *, request):
        self._record("delete_instance", request=request)
        instance_id = request["name"].rsplit("/", 1)[-1]
        if instance_id not in self.instances:
            raise RuntimeError("404 NotFound")
        del self.instances[instance_id]

    def export_instance(self, *, request):
        self._record("export_instance", request=request)

    def import_instance(self, *, request):
        self._record("import_instance", request=request)


class FakeSecretClient:
    def __init__(self):
        self.secrets: dict[str, list[bytes]] = {}

    def create_secret(self, *, request):
        sid = request["secret_id"]
        if sid in self.secrets:
            raise RuntimeError("AlreadyExists")
        self.secrets[sid] = []

    def add_secret_version(self, *, request):
        sid = request["parent"].split("/secrets/")[-1]
        self.secrets.setdefault(sid, []).append(request["payload"]["data"])

    def delete_secret(self, *, request):
        sid = request["name"].split("/secrets/")[-1]
        self.secrets.pop(sid, None)


@pytest.fixture
def driver():
    return MemorystoreRedisDriver(
        config=MemorystoreConfig(
            project_id="acme-prod",
            region="us-west1",
        ),
        redis_client=FakeRedisClient(),
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
        service_handle_hint="rd",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_instance(driver):
    result = driver.provision(_spec())
    assert result.ok
    kind, instance_id = result.handle.partition("/")[0::2]
    assert kind == KIND
    assert instance_id in driver._redis.instances  # type: ignore[attr-defined]


def test_provision_idempotent(driver):
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert "already exists" in b.message


def test_provision_stores_auth_token_in_secret_manager(driver):
    result = driver.provision(_spec())
    instance_id = _parse_handle(result.handle)
    sid = (
        f"astrolift/memorystore/{instance_id}/auth".replace("/", "_")
    )
    versions = driver._sm.secrets[sid]  # type: ignore[attr-defined]
    assert versions and len(versions[0]) >= 16


def test_provision_no_auth_token_when_auth_disabled(driver):
    result = driver.provision(_spec(config={"auth_enabled": False}))
    instance_id = _parse_handle(result.handle)
    sid = (
        f"astrolift/memorystore/{instance_id}/auth".replace("/", "_")
    )
    assert sid not in driver._sm.secrets  # type: ignore[attr-defined]


def test_provision_size_to_memory(driver):
    result = driver.provision(_spec(size="large"))
    redis_client = driver._redis  # type: ignore[attr-defined]
    create_call = next(
        k for op, k in redis_client.calls if op == "create_instance"
    )
    assert create_call["request"]["instance"]["memory_size_gb"] == 16


def test_provision_high_availability_bumps_tier(driver):
    result = driver.provision(_spec(config={"high_availability": True}))
    redis_client = driver._redis  # type: ignore[attr-defined]
    create_call = next(
        k for op, k in redis_client.calls if op == "create_instance"
    )
    assert create_call["request"]["instance"]["tier"] == "STANDARD_HA"


def test_provision_rolls_back_secret_on_failure():
    class RaisingRedis:
        def get_instance(self, **_):
            raise RuntimeError("404 NotFound")

        def create_instance(self, **_):
            raise RuntimeError("synthetic failure")

    sm = FakeSecretClient()
    d = MemorystoreRedisDriver(
        config=MemorystoreConfig(project_id="acme", region="us-west1"),
        redis_client=RaisingRedis(),
        secrets_client=sm,
    )
    result = d.provision(_spec())
    assert not result.ok
    assert not sm.secrets


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_takes_export(driver):
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "export=taken" in result.message
    redis_client = driver._redis  # type: ignore[attr-defined]
    assert any(op == "export_instance" for op, _ in redis_client.calls)


def test_deprovision_delete_data_skips_export(driver):
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "export=skipped" in result.message


def test_deprovision_atomic_both_flags(driver):
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "force_destroy=True" in result.message


def test_deprovision_idempotent_when_already_gone(driver):
    result = driver.deprovision(
        DeprovisionSpec(handle="redis/missing"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_delete_data_drops_auth_secret(driver):
    provisioned = driver.provision(_spec())
    instance_id = _parse_handle(provisioned.handle)
    sid = (
        f"astrolift/memorystore/{instance_id}/auth".replace("/", "_")
    )
    assert sid in driver._sm.secrets  # type: ignore[attr-defined]
    driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert sid not in driver._sm.secrets  # type: ignore[attr-defined]


def test_deprovision_force_destroy_retries_on_failed_precondition():
    class RaisingRedis:
        def get_instance(self, **_):
            return FakeRedisInstance(name="x", state="READY")

        def delete_instance(self, **_):
            raise RuntimeError(
                "FAILED_PRECONDITION: instance is being modified",
            )

        def export_instance(self, **_):
            pass

    class _SM:
        secrets: dict = {}

        def delete_secret(self, **_):
            pass

    d = MemorystoreRedisDriver(
        config=MemorystoreConfig(project_id="p", region="r"),
        redis_client=RaisingRedis(),
        secrets_client=_SM(),
    )
    soft = d.deprovision(
        DeprovisionSpec(handle="redis/x"),
        force_destroy=False,
    )
    assert not soft.ok
    assert "mid-modify" in soft.message

    hard = d.deprovision(
        DeprovisionSpec(handle="redis/x"),
        force_destroy=True,
    )
    assert not hard.ok
    assert "delete_instance" in hard.message


# ---- status / binding ------------------------------------------


def test_status_for_missing_returns_deprovisioned(driver):
    state = driver.status(ServiceHandle(handle="redis/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_ready_to_available(driver):
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_binding_with_auth_uses_secret_refs(driver):
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    assert env["REDIS_TLS"].literal == "1"
    assert env["REDIS_AUTH_TOKEN"].secret_ref is not None
    assert env["REDIS_URL"].secret_ref is not None
    assert len(binding.iam_grants) == 1


def test_binding_without_auth_uses_literal_url(driver):
    provisioned = driver.provision(
        _spec(config={"auth_enabled": False, "transit_encryption": False}),
    )
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    assert env["REDIS_TLS"].literal == "0"
    assert "REDIS_AUTH_TOKEN" not in env
    assert env["REDIS_URL"].literal is not None
    assert env["REDIS_URL"].literal.startswith("redis://")


# ---- snapshot + restore ----------------------------------------


def test_snapshot_exports_to_gcs(driver):
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    assert snap.snapshot_id.startswith("gs://astrolift-snap-redis")


def test_restore_imports_from_gcs(driver):
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok


# ---- module helpers --------------------------------------------


def test_auth_token_length_and_charset():
    t = _generate_auth_token(length=64)
    assert len(t) == 64
    assert all(c.isalnum() for c in t)


def test_instance_id_starts_with_letter_and_under_limit(driver):
    spec = _spec(app_slug="my-app", environment_name="DEV")
    iid = driver._instance_id_for(spec=spec)  # type: ignore[attr-defined]
    assert iid[0].isalpha()
    assert iid == iid.lower()
    assert len(iid) <= 40


# ---- schemas -----------------------------------------------------


def test_config_schema_shape(driver):
    schema = driver.config_schema()
    for key in (
        "memory_gb", "redis_version", "tier", "high_availability",
        "transit_encryption", "auth_enabled", "kms_key_name",
    ):
        assert key in schema["properties"]


def test_binding_schema_lists_all_env_vars(driver):
    schema = driver.binding_schema()
    for key in (
        "REDIS_HOST", "REDIS_PORT", "REDIS_TLS",
        "REDIS_AUTH_TOKEN", "REDIS_URL",
    ):
        assert key in schema.env_vars
