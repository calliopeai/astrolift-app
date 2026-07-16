"""Tests for the new k8s_native operator-backed managed services
(#71 MySQL/MongoDB, #72 Kafka/RabbitMQ/NATS, #73 NFS)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from _sdk.managed_service import (
    ProvisionSpec,
    ServiceHandle,
)
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
from k8s_native.managed.queue_rabbitmq import (
    RabbitMQOperatorConfig,
    RabbitMQOperatorDriver,
)


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="api",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="",
        size="medium",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- MySQL -------------------------------------------------------


def test_mysql_provision_emits_percona_crd() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = MySQLOperatorDriver(
        config=MySQLOperatorConfig(cluster_driver=cluster_driver),
    )
    result = driver.provision(_spec())
    assert result.ok
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    assert manifests[0]["apiVersion"] == "pxc.percona.com/v1"
    assert manifests[0]["kind"] == "PerconaXtraDBCluster"
    # Medium size → 3 instances
    assert manifests[0]["spec"]["pxc"]["size"] == 3
    assert manifests[0]["spec"]["haproxy"]["enabled"] is True


def test_mysql_provision_without_cluster_driver_still_renders() -> None:
    """Driver returns the rendered handle even without a cluster
    driver — useful for dry-run / offline workflows."""
    driver = MySQLOperatorDriver(
        config=MySQLOperatorConfig(),
    )
    result = driver.provision(_spec())
    assert result.ok
    assert "mysql/" in result.handle


def test_mysql_binding_envs() -> None:
    driver = MySQLOperatorDriver(
        config=MySQLOperatorConfig(),
    )
    binding = driver.binding(ServiceHandle(handle="mysql/api-prod"))
    assert "MYSQL_HOST" in binding.env_vars
    assert "MYSQL_DB" in binding.env_vars
    assert binding.env_vars["MYSQL_USER"].secret_ref == ("api-prod-app-secret#username")


def test_mysql_mariadb_brand_emits_mariadb_crd() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = MySQLOperatorDriver(
        config=MySQLOperatorConfig(
            operator_brand="mariadb",
            cluster_driver=cluster_driver,
        ),
    )
    driver.provision(_spec())
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    assert manifests[0]["apiVersion"] == "k8s.mariadb.com/v1alpha1"
    assert manifests[0]["kind"] == "MariaDB"


# ---- MongoDB -----------------------------------------------------


def test_mongodb_provision_emits_psmdb_crd() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = MongoDBOperatorDriver(
        config=MongoDBOperatorConfig(cluster_driver=cluster_driver),
    )
    result = driver.provision(_spec())
    assert result.ok
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    assert manifests[0]["apiVersion"] == "psmdb.percona.com/v1"
    assert manifests[0]["kind"] == "PerconaServerMongoDB"
    assert manifests[0]["spec"]["replsets"][0]["name"] == "rs0"


def test_mongodb_binding_uses_replica_set_uri() -> None:
    driver = MongoDBOperatorDriver(
        config=MongoDBOperatorConfig(),
    )
    binding = driver.binding(
        ServiceHandle(handle="document_db/api-prod"),
    )
    assert binding.env_vars["DOCDB_URI"].literal.startswith("mongodb+srv://")
    assert "replicaSet=api-prod-rs0" in binding.env_vars["DOCDB_URI"].literal


# ---- Strimzi Kafka -----------------------------------------------


def test_kafka_provision_emits_kraft_kafka() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = StrimziKafkaDriver(
        config=StrimziKafkaConfig(cluster_driver=cluster_driver),
    )
    result = driver.provision(_spec())
    assert result.ok
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    kinds = {m["kind"] for m in manifests}
    assert "Kafka" in kinds
    assert "KafkaNodePool" in kinds
    assert "KafkaUser" in kinds
    # KRaft annotation present
    kafka = next(m for m in manifests if m["kind"] == "Kafka")
    assert kafka["metadata"]["annotations"]["strimzi.io/kraft"] == "enabled"


def test_kafka_binding_emits_bootstrap() -> None:
    driver = StrimziKafkaDriver(config=StrimziKafkaConfig())
    binding = driver.binding(
        ServiceHandle(handle="event_stream/api-prod"),
    )
    bootstrap = binding.env_vars["EVENT_STREAM_BROKERS"].literal
    assert "api-prod-kafka-bootstrap" in bootstrap


def test_kafka_size_xlarge_has_more_replicas() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = StrimziKafkaDriver(
        config=StrimziKafkaConfig(cluster_driver=cluster_driver),
    )
    driver.provision(_spec(size="xlarge"))
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    brokers = next(m for m in manifests if m["kind"] == "KafkaNodePool" and m["metadata"]["name"] == "brokers")
    assert brokers["spec"]["replicas"] == 7


def test_kafka_snapshot_unsupported() -> None:
    driver = StrimziKafkaDriver(config=StrimziKafkaConfig())
    with pytest.raises(NotImplementedError):
        driver.snapshot(ServiceHandle(handle="event_stream/x"))


# ---- RabbitMQ ----------------------------------------------------


def test_rabbitmq_provision_emits_cluster_crd() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = RabbitMQOperatorDriver(
        config=RabbitMQOperatorConfig(cluster_driver=cluster_driver),
    )
    result = driver.provision(_spec())
    assert result.ok
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    assert manifests[0]["apiVersion"] == "rabbitmq.com/v1beta1"
    assert manifests[0]["kind"] == "RabbitmqCluster"


def test_rabbitmq_binding_uses_default_user_secret() -> None:
    driver = RabbitMQOperatorDriver(config=RabbitMQOperatorConfig())
    binding = driver.binding(
        ServiceHandle(handle="queue/api-prod"),
    )
    assert binding.env_vars["RABBITMQ_USER"].secret_ref == ("api-prod-default-user#username")


def test_rabbitmq_pause_minority_partition_handling() -> None:
    """Default config sets cluster_partition_handling to
    pause_minority — production-recommended for consistency."""
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = RabbitMQOperatorDriver(
        config=RabbitMQOperatorConfig(cluster_driver=cluster_driver),
    )
    driver.provision(_spec())
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    config = manifests[0]["spec"]["rabbitmq"]["additionalConfig"]
    assert "pause_minority" in config


# ---- NATS --------------------------------------------------------


def test_nats_provision_emits_statefulset() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = NATSDriver(config=NATSConfig(cluster_driver=cluster_driver))
    result = driver.provision(_spec())
    assert result.ok
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    assert manifests[0]["kind"] == "StatefulSet"


def test_nats_jetstream_arg_passed_when_enabled() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = NATSDriver(
        config=NATSConfig(
            enable_jetstream=True,
            cluster_driver=cluster_driver,
        ),
    )
    driver.provision(_spec())
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    args_list = manifests[0]["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--jetstream" in args_list


def test_nats_jetstream_omitted_when_disabled() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = NATSDriver(
        config=NATSConfig(
            enable_jetstream=False,
            cluster_driver=cluster_driver,
        ),
    )
    driver.provision(_spec())
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    args_list = manifests[0]["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--jetstream" not in args_list


# ---- NFS ---------------------------------------------------------


def test_nfs_provision_emits_rwx_pvc() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = NFSDriver(
        config=NFSConfig(
            storage_class_name="nfs-csi",
            cluster_driver=cluster_driver,
        )
    )
    result = driver.provision(_spec())
    assert result.ok
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    pvc = manifests[0]
    assert pvc["kind"] == "PersistentVolumeClaim"
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]
    assert pvc["spec"]["storageClassName"] == "nfs-csi"


def test_nfs_size_storage_mapping() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = NFSDriver(
        config=NFSConfig(
            storage_class_name="nfs-csi",
            cluster_driver=cluster_driver,
        )
    )
    driver.provision(_spec(size="large"))
    args, _kwargs = cluster_driver.apply_manifests.call_args
    manifests = args[2]
    storage = manifests[0]["spec"]["resources"]["requests"]["storage"]
    assert storage == "1Ti"


def test_nfs_binding_carries_pvc_name() -> None:
    driver = NFSDriver(config=NFSConfig(storage_class_name="nfs-csi"))
    binding = driver.binding(
        ServiceHandle(handle="filesystem/nfs-api-prod"),
    )
    assert binding.env_vars["FILESYSTEM_HANDLE"].literal == "nfs-api-prod"
    assert binding.env_vars["FILESYSTEM_TLS"].literal == "false"
