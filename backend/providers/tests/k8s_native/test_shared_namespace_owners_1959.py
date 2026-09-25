"""Operator objects in a shared namespace are not overwritten across orgs (#1959).

Their names carry no org (``<app>-<env>-<hint>``), so two orgs' apps with one
slug render the same object; the second apply took over the first's
database and credentials.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from _sdk.managed_service import ProvisionSpec
from k8s_native.managed.event_stream_nats import NATSConfig, NATSDriver
from k8s_native.managed.event_stream_strimzi import StrimziKafkaConfig, StrimziKafkaDriver
from k8s_native.managed.filesystem_nfs import NFSConfig, NFSDriver
from k8s_native.managed.mongodb_operator import MongoDBOperatorConfig, MongoDBOperatorDriver
from k8s_native.managed.mysql_operator import MySQLOperatorConfig, MySQLOperatorDriver
from k8s_native.managed.queue_rabbitmq import RabbitMQOperatorConfig, RabbitMQOperatorDriver


class _ApplyResult:
    ok = True

    def summary(self) -> list[str]:
        return []


class _Cluster:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.applies = 0

    def get_manifest(self, _cluster, namespace, kind, name):
        value = self.objects.get((kind, namespace, name))
        return copy.deepcopy(value) if value is not None else None

    def apply_manifests(self, _cluster, namespace, manifests):
        self.applies += 1
        for manifest in manifests:
            meta = manifest["metadata"]
            key = (f"{manifest['apiVersion']}/{manifest['kind']}", meta.get("namespace", namespace), meta["name"])
            self.objects[key] = copy.deepcopy(manifest)
        return _ApplyResult()


def _spec(org: str) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id=org,
        organization_slug=org,
        app_id="app",
        app_slug="web",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="data",
        size="small",
        config={},
        managed_service_id=f"svc-{org}",
    )


DRIVERS = {
    "mongodb": lambda c, ns: MongoDBOperatorDriver(config=MongoDBOperatorConfig(namespace=ns, cluster_driver=c)),
    "mysql": lambda c, ns: MySQLOperatorDriver(config=MySQLOperatorConfig(namespace=ns, cluster_driver=c)),
    "rabbitmq": lambda c, ns: RabbitMQOperatorDriver(config=RabbitMQOperatorConfig(namespace=ns, cluster_driver=c)),
    "nats": lambda c, ns: NATSDriver(config=NATSConfig(namespace=ns, cluster_driver=c)),
    "strimzi": lambda c, ns: StrimziKafkaDriver(config=StrimziKafkaConfig(namespace=ns, cluster_driver=c)),
    "nfs": lambda c, ns: NFSDriver(
        config=NFSConfig(namespace=ns, cluster_driver=c, server_address="10.0.0.2", storage_class_name="nfs")
    ),
}


@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_a_second_org_cannot_overwrite_the_first_orgs_object_in_a_shared_namespace(name):
    cluster = _Cluster()
    driver = DRIVERS[name](cluster, "shared-data")

    first = driver.provision(_spec("acme"))
    applies = cluster.applies
    again = driver.provision(_spec("acme"))
    other = driver.provision(_spec("globex"))

    assert first.ok and again.ok, (first.message, again.message)
    assert not other.ok and "refusing to overwrite" in other.message
    assert cluster.applies == applies + 1  # only acme's re-provision applied


@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_per_app_namespaces_are_unaffected(name):
    cluster = _Cluster()
    driver = DRIVERS[name](cluster, None)

    assert driver.provision(_spec("acme")).ok
    assert driver.provision(_spec("globex")).ok


def test_a_second_kafka_cannot_rebind_the_first_ones_fixed_name_node_pools():
    """Strimzi node pools are named controllers/brokers per namespace."""
    import dataclasses

    cluster = _Cluster()
    driver = DRIVERS["strimzi"](cluster, "shared-data")
    assert driver.provision(_spec("acme")).ok

    other = driver.provision(dataclasses.replace(_spec("acme"), app_slug="api"))

    assert not other.ok and "KafkaNodePool" in other.message
