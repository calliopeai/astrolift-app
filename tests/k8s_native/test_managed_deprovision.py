"""Deprovision coverage for in-cluster managed-service drivers (#366).

Each driver's ``deprovision`` must emit the same CR stubs it
originally applied to the cluster, addressed at the right
``(cluster_id, namespace, name)`` tuple. Pre-#366 the bare-metal
drivers returned ``ok=True`` as a no-op (5 STUB drivers) or hit
``delete_manifests`` with hardcoded / blank locator values (2
PARTIAL drivers — redis_operator and mysql_operator), leaving K8s
resources orphaned after a tenant teardown.

The fix encodes the locator into the handle returned by
``provision`` (``<kind>/<cluster_id>/<namespace>/<name>``) so
``deprovision`` can recover the namespace + tenant cluster id from
the handle alone. These tests pin that contract: a real handle
shape goes in, a stub matching the originally-applied CR comes out,
the right cluster + namespace land on the ``delete_manifests``
call, and driver errors propagate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from _sdk.cluster import ApplyResult, DeleteResult
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec
from k8s_native.managed.event_stream_nats import NATSConfig, NATSDriver
from k8s_native.managed.event_stream_strimzi import (
    StrimziKafkaConfig,
    StrimziKafkaDriver,
)
from k8s_native.managed.filesystem_nfs import NFSConfig, NFSDriver
from k8s_native.managed.mongodb_operator import (
    MongoDBOperatorConfig,
    MongoDBOperatorDriver,
)
from k8s_native.managed.mysql_operator import (
    MySQLOperatorConfig,
    MySQLOperatorDriver,
)
from k8s_native.managed.postgres_cnpg import CNPGConfig, CNPGPostgresDriver
from k8s_native.managed.queue_rabbitmq import (
    RabbitMQOperatorConfig,
    RabbitMQOperatorDriver,
)
from k8s_native.managed.redis_operator import (
    RedisOperatorConfig,
    RedisOperatorDriver,
)

# ---- test infrastructure ------------------------------------------


@dataclass
class _DeleteCall:
    """One captured ``delete_manifests`` invocation."""

    cluster: str
    namespace: str
    manifests: list[dict[str, Any]]


class FakeClusterDriver:
    """Records ``apply_manifests`` + ``delete_manifests`` calls.

    The driver returns the canned ``ApplyResult`` / ``DeleteResult``
    on each call so tests can drive both the success path and the
    error path. The deliberately small interface keeps the fake from
    diverging from the real ``ClusterDriver`` protocol.
    """

    def __init__(
        self,
        *,
        delete_errors: list[str] | None = None,
        apply_errors: list[str] | None = None,
    ) -> None:
        self.delete_calls: list[_DeleteCall] = []
        self.apply_calls: list[_DeleteCall] = []
        self._delete_errors = delete_errors or []
        self._apply_errors = apply_errors or []

    def apply_manifests(
        self,
        cluster: str,
        namespace: str,
        manifests: list[dict[str, Any]],
        *,
        dry_run: bool = False,
    ) -> ApplyResult:
        del dry_run
        self.apply_calls.append(
            _DeleteCall(
                cluster=cluster,
                namespace=namespace,
                manifests=list(manifests),
            ),
        )
        return ApplyResult(
            created=[],
            updated=[],
            unchanged=[],
            errors=list(self._apply_errors),
        )

    def delete_manifests(
        self,
        cluster: str,
        namespace: str,
        manifests: list[dict[str, Any]],
    ) -> DeleteResult:
        self.delete_calls.append(
            _DeleteCall(
                cluster=cluster,
                namespace=namespace,
                manifests=list(manifests),
            ),
        )
        return DeleteResult(
            deleted=[],
            not_found=[],
            errors=list(self._delete_errors),
        )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="api",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="tenant-1",
        service_handle_hint="",
        size="medium",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- CNPG Postgres ------------------------------------------------


def test_cnpg_deprovision_deletes_cluster_cr_at_correct_ns() -> None:
    fake = FakeClusterDriver()
    driver = CNPGPostgresDriver(config=CNPGConfig(cluster_driver=fake))
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))

    assert result.ok is True
    assert len(fake.delete_calls) == 1
    call = fake.delete_calls[0]
    assert call.cluster == "tenant-1"
    assert call.namespace == "acme-api"
    assert len(call.manifests) == 1
    cr = call.manifests[0]
    assert cr["apiVersion"] == "postgresql.cnpg.io/v1"
    assert cr["kind"] == "Cluster"
    assert cr["metadata"]["name"] == "api-prod"
    assert cr["metadata"]["namespace"] == "acme-api"


def test_cnpg_deprovision_render_only_returns_ok() -> None:
    """No cluster_driver → caller deletes; driver still returns ok."""
    driver = CNPGPostgresDriver(config=CNPGConfig(cluster_driver=None))
    result = driver.deprovision(DeprovisionSpec(handle="postgres/x/y/z"))
    assert result.ok is True


def test_cnpg_deprovision_error_propagates() -> None:
    fake = FakeClusterDriver(delete_errors=["webhook denied"])
    driver = CNPGPostgresDriver(config=CNPGConfig(cluster_driver=fake))
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok is False
    assert "webhook denied" in result.errors


def test_cnpg_deprovision_rejects_legacy_handle() -> None:
    """A pre-#366 handle has no locator. The driver refuses rather
    than guessing the namespace + cluster."""
    fake = FakeClusterDriver()
    driver = CNPGPostgresDriver(config=CNPGConfig(cluster_driver=fake))
    result = driver.deprovision(DeprovisionSpec(handle="postgres/api-prod"))
    assert result.ok is False
    assert "legacy_handle_missing_locator" in result.errors
    assert fake.delete_calls == []


# ---- Redis Operator ----------------------------------------------


def test_redis_deprovision_emits_both_cr_kinds() -> None:
    """Provision picks ``Redis`` (small) or ``RedisReplication``
    (medium+) based on size; deprovision deletes both stubs so we
    don't have to round-trip size through the handle."""
    fake = FakeClusterDriver()
    driver = RedisOperatorDriver(
        config=RedisOperatorConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec(size="medium"))
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))

    assert result.ok is True
    call = fake.delete_calls[0]
    assert call.cluster == "tenant-1"
    assert call.namespace == "acme-api"
    kinds = {m["kind"] for m in call.manifests}
    assert kinds == {"Redis", "RedisReplication"}
    for stub in call.manifests:
        assert stub["apiVersion"] == "redis.redis.opstreelabs.in/v1beta2"
        assert stub["metadata"]["name"] == "api-prod"
        assert stub["metadata"]["namespace"] == "acme-api"


def test_redis_deprovision_error_propagates() -> None:
    fake = FakeClusterDriver(delete_errors=["finalizer stuck"])
    driver = RedisOperatorDriver(
        config=RedisOperatorConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok is False
    assert "finalizer stuck" in result.errors


def test_redis_deprovision_rejects_legacy_handle() -> None:
    fake = FakeClusterDriver()
    driver = RedisOperatorDriver(
        config=RedisOperatorConfig(cluster_driver=fake),
    )
    result = driver.deprovision(DeprovisionSpec(handle="redis/api-prod"))
    assert result.ok is False
    assert "legacy_handle_missing_locator" in result.errors


# ---- MySQL Operator ----------------------------------------------


def test_mysql_deprovision_targets_namespace_not_blank() -> None:
    """Pre-#366 the driver passed empty strings; now it must route
    delete to the actual app namespace."""
    fake = FakeClusterDriver()
    driver = MySQLOperatorDriver(
        config=MySQLOperatorConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))

    assert result.ok is True
    call = fake.delete_calls[0]
    assert call.cluster == "tenant-1"
    assert call.namespace == "acme-api"
    cr = call.manifests[0]
    assert cr["apiVersion"] == "pxc.percona.com/v1"
    assert cr["kind"] == "PerconaXtraDBCluster"
    assert cr["metadata"]["name"] == "api-prod"
    assert cr["metadata"]["namespace"] == "acme-api"


def test_mysql_deprovision_mariadb_brand() -> None:
    fake = FakeClusterDriver()
    driver = MySQLOperatorDriver(
        config=MySQLOperatorConfig(
            operator_brand="mariadb",
            cluster_driver=fake,
        ),
    )
    provisioned = driver.provision(_spec())
    driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    cr = fake.delete_calls[0].manifests[0]
    assert cr["apiVersion"] == "k8s.mariadb.com/v1alpha1"
    assert cr["kind"] == "MariaDB"


def test_mysql_deprovision_error_propagates() -> None:
    fake = FakeClusterDriver(delete_errors=["RBAC denied"])
    driver = MySQLOperatorDriver(
        config=MySQLOperatorConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok is False
    assert "RBAC denied" in result.errors


def test_mysql_deprovision_rejects_legacy_handle() -> None:
    fake = FakeClusterDriver()
    driver = MySQLOperatorDriver(
        config=MySQLOperatorConfig(cluster_driver=fake),
    )
    result = driver.deprovision(DeprovisionSpec(handle="mysql/api-prod"))
    assert result.ok is False
    assert "legacy_handle_missing_locator" in result.errors


# ---- MongoDB Operator --------------------------------------------


def test_mongodb_deprovision_deletes_psmdb_cr() -> None:
    """Pre-#366 this driver was a hard no-op (ok=True, no delete)."""
    fake = FakeClusterDriver()
    driver = MongoDBOperatorDriver(
        config=MongoDBOperatorConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))

    assert result.ok is True
    assert len(fake.delete_calls) == 1
    call = fake.delete_calls[0]
    assert call.cluster == "tenant-1"
    assert call.namespace == "acme-api"
    cr = call.manifests[0]
    assert cr["apiVersion"] == "psmdb.percona.com/v1"
    assert cr["kind"] == "PerconaServerMongoDB"
    assert cr["metadata"]["name"] == "api-prod"


def test_mongodb_deprovision_error_propagates() -> None:
    fake = FakeClusterDriver(delete_errors=["timeout"])
    driver = MongoDBOperatorDriver(
        config=MongoDBOperatorConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok is False
    assert "timeout" in result.errors


# ---- Strimzi Kafka -----------------------------------------------


def test_strimzi_deprovision_emits_all_four_crs() -> None:
    """Provision emits Kafka + 2x KafkaNodePool + KafkaUser; the
    delete must mirror that set so the operator finalizer can
    cascade cleanly."""
    fake = FakeClusterDriver()
    driver = StrimziKafkaDriver(
        config=StrimziKafkaConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))

    assert result.ok is True
    call = fake.delete_calls[0]
    assert call.cluster == "tenant-1"
    assert call.namespace == "acme-api"
    assert len(call.manifests) == 4
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for stub in call.manifests:
        by_kind.setdefault(stub["kind"], []).append(stub)
    assert {m["metadata"]["name"] for m in by_kind["Kafka"]} == {"api-prod"}
    nodepool_names = {m["metadata"]["name"] for m in by_kind["KafkaNodePool"]}
    assert nodepool_names == {"controllers", "brokers"}
    assert {m["metadata"]["name"] for m in by_kind["KafkaUser"]} == {
        "api-prod-app-user",
    }
    for stub in call.manifests:
        assert stub["apiVersion"] == "kafka.strimzi.io/v1beta2"
        assert stub["metadata"]["namespace"] == "acme-api"


def test_strimzi_deprovision_error_propagates() -> None:
    fake = FakeClusterDriver(delete_errors=["zk lock"])
    driver = StrimziKafkaDriver(
        config=StrimziKafkaConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok is False
    assert "zk lock" in result.errors


# ---- NATS --------------------------------------------------------


def test_nats_deprovision_deletes_statefulset() -> None:
    fake = FakeClusterDriver()
    driver = NATSDriver(config=NATSConfig(cluster_driver=fake))
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))

    assert result.ok is True
    call = fake.delete_calls[0]
    assert call.cluster == "tenant-1"
    assert call.namespace == "acme-api"
    cr = call.manifests[0]
    assert cr["apiVersion"] == "apps/v1"
    assert cr["kind"] == "StatefulSet"
    assert cr["metadata"]["name"] == "api-prod"
    assert cr["metadata"]["namespace"] == "acme-api"


def test_nats_deprovision_error_propagates() -> None:
    fake = FakeClusterDriver(delete_errors=["nope"])
    driver = NATSDriver(config=NATSConfig(cluster_driver=fake))
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok is False
    assert "nope" in result.errors


# ---- RabbitMQ ----------------------------------------------------


def test_rabbitmq_deprovision_deletes_cluster_cr() -> None:
    fake = FakeClusterDriver()
    driver = RabbitMQOperatorDriver(
        config=RabbitMQOperatorConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))

    assert result.ok is True
    call = fake.delete_calls[0]
    assert call.cluster == "tenant-1"
    assert call.namespace == "acme-api"
    cr = call.manifests[0]
    assert cr["apiVersion"] == "rabbitmq.com/v1beta1"
    assert cr["kind"] == "RabbitmqCluster"
    assert cr["metadata"]["name"] == "api-prod"
    assert cr["metadata"]["namespace"] == "acme-api"


def test_rabbitmq_deprovision_error_propagates() -> None:
    fake = FakeClusterDriver(delete_errors=["erlang clustering"])
    driver = RabbitMQOperatorDriver(
        config=RabbitMQOperatorConfig(cluster_driver=fake),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok is False
    assert "erlang clustering" in result.errors


# ---- NFS Filesystem ----------------------------------------------


def test_nfs_deprovision_deletes_pvc() -> None:
    fake = FakeClusterDriver()
    driver = NFSDriver(
        config=NFSConfig(
            storage_class_name="nfs-csi",
            cluster_driver=fake,
        ),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))

    assert result.ok is True
    call = fake.delete_calls[0]
    assert call.cluster == "tenant-1"
    assert call.namespace == "acme-api"
    cr = call.manifests[0]
    assert cr["apiVersion"] == "v1"
    assert cr["kind"] == "PersistentVolumeClaim"
    assert cr["metadata"]["name"] == "nfs-api-prod"
    assert cr["metadata"]["namespace"] == "acme-api"


def test_nfs_deprovision_error_propagates() -> None:
    fake = FakeClusterDriver(delete_errors=["volume in use"])
    driver = NFSDriver(
        config=NFSConfig(
            storage_class_name="nfs-csi",
            cluster_driver=fake,
        ),
    )
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok is False
    assert "volume in use" in result.errors


# ---- Handle encoding ---------------------------------------------


@pytest.mark.parametrize(
    "driver_factory",
    [
        lambda d: CNPGPostgresDriver(config=CNPGConfig(cluster_driver=d)),
        lambda d: RedisOperatorDriver(
            config=RedisOperatorConfig(cluster_driver=d),
        ),
        lambda d: MySQLOperatorDriver(
            config=MySQLOperatorConfig(cluster_driver=d),
        ),
        lambda d: MongoDBOperatorDriver(
            config=MongoDBOperatorConfig(cluster_driver=d),
        ),
        lambda d: StrimziKafkaDriver(
            config=StrimziKafkaConfig(cluster_driver=d),
        ),
        lambda d: NATSDriver(config=NATSConfig(cluster_driver=d)),
        lambda d: RabbitMQOperatorDriver(
            config=RabbitMQOperatorConfig(cluster_driver=d),
        ),
        lambda d: NFSDriver(
            config=NFSConfig(
                storage_class_name="nfs-csi",
                cluster_driver=d,
            ),
        ),
    ],
)
def test_all_drivers_emit_4_segment_handle(driver_factory) -> None:
    """Every fixed driver must round-trip cluster_id + namespace
    into the handle so the workflow layer can hand it back to
    deprovision without re-deriving anything."""
    fake = FakeClusterDriver()
    driver = driver_factory(fake)
    provisioned = driver.provision(_spec())
    assert provisioned.ok is True
    parts = provisioned.handle.split("/")
    assert len(parts) == 4, f"handle must be 4-segment; got {provisioned.handle!r}"
    _, cluster_id, namespace, _ = parts
    assert cluster_id == "tenant-1"
    assert namespace == "acme-api"
