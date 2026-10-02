"""Bindings retain their provisioned namespace when the consumer lives elsewhere."""

from dataclasses import replace

import pytest

from _sdk.cluster import ApplyResult
from _sdk.managed_service import ProvisionSpec, ServiceHandle
from _sdk.secrets import resolve_secret_reference
from k8s_native.managed._handle import unpack
from k8s_native.managed.event_stream_nats import NATSConfig, NATSDriver
from k8s_native.managed.event_stream_strimzi import StrimziKafkaConfig, StrimziKafkaDriver
from k8s_native.managed.mongodb_operator import MongoDBOperatorConfig, MongoDBOperatorDriver
from k8s_native.managed.mysql_operator import MySQLOperatorConfig, MySQLOperatorDriver
from k8s_native.managed.postgres_cnpg import CNPGConfig, CNPGPostgresDriver
from k8s_native.managed.queue_rabbitmq import RabbitMQOperatorConfig, RabbitMQOperatorDriver
from k8s_native.managed.redis_operator import RedisOperatorConfig, RedisOperatorDriver


class RecordingCluster:
    def __init__(self):
        self.applies = []

    def get_object(self, *args):
        return None

    def get_manifest(self, *args):
        return None

    def apply_manifests(self, cluster, namespace, manifests, **kwargs):
        self.applies.append((cluster, namespace, manifests))
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])


CASES = [
    (CNPGPostgresDriver, CNPGConfig, "POSTGRES_HOST", "-rw", "", ""),
    (RedisOperatorDriver, RedisOperatorConfig, "REDIS_HOST", "-redis", "", ""),
    (MySQLOperatorDriver, MySQLOperatorConfig, "MYSQL_HOST", "-haproxy", "", ""),
    (MongoDBOperatorDriver, MongoDBOperatorConfig, "DOCDB_URI", "-rs0", "mongodb+srv://", "/?replicaSet={name}-rs0"),
    (RabbitMQOperatorDriver, RabbitMQOperatorConfig, "RABBITMQ_HOST", "", "", ""),
    (NATSDriver, NATSConfig, "EVENT_STREAM_BROKERS", "", "nats://", ":4222"),
    (StrimziKafkaDriver, StrimziKafkaConfig, "EVENT_STREAM_BROKERS", "-kafka-bootstrap", "", ":9092"),
]


@pytest.mark.parametrize("driver_cls,config_cls,key,suffix,prefix,tail", CASES)
def test_recording_provision_and_binding_use_the_same_namespace(driver_cls, config_cls, key, suffix, prefix, tail):
    cluster = RecordingCluster()
    config = config_cls(cluster_driver=cluster)
    if hasattr(config, "namespace"):
        config = replace(config, namespace="operator-service-namespace")
    spec = ProvisionSpec(
        managed_service_id="8b7e2c6b-0b93-4126-a121-abc123456789",
        organization_id="organization-a",
        organization_slug="acme",
        app_id="app-a",
        app_slug="web",
        environment_id="production",
        environment_name="production",
        tenant_cluster_id="cluster-a",
        service_handle_hint="db",
        size="small",
    )
    result = driver_cls(config=config).provision(spec)
    assert result.ok, result.errors
    parsed = unpack(result.handle)
    assert cluster.applies
    assert all((cluster_id, ns) == (parsed.cluster_id, parsed.namespace) for cluster_id, ns, _ in cluster.applies)
    assert parsed.namespace != "acme-web-preview"
    # A changed operator default must not redirect a previously recorded service.
    if hasattr(config, "namespace"):
        config = replace(config, namespace="different-default")
    binding = driver_cls(config=config).binding(ServiceHandle(result.handle))
    expected = f"{prefix}{parsed.name}{suffix}.{parsed.namespace}.svc{tail.format(name=parsed.name)}"
    assert binding.env_vars[key].literal == expected
    assert binding.env_vars[key].secret_ref is None
    if key == "POSTGRES_HOST":
        assert binding.env_vars["DATABASE_HOST"] == binding.env_vars[key]
        assert binding.env_vars["POSTGRES_PASSWORD"].secret_ref == f"{parsed.name}-app#password"
        assert binding.env_vars["DATABASE_URL"].secret_ref == f"{parsed.name}-app#fqdn-uri"
    if key == "REDIS_HOST":
        assert binding.env_vars["REDIS_URL"].literal == f"redis://{expected}:6379"
        assert binding.env_vars["REDIS_PASSWORD"].secret_ref == f"{parsed.name}-redis#redis-password"


@pytest.mark.parametrize("driver_cls,config_cls,key,suffix,prefix,tail", CASES)
@pytest.mark.parametrize("handle", ["kind/resource", "kind/cluster//resource", "kind//namespace/resource"])
def test_binding_never_guesses_an_unrecorded_locator(driver_cls, config_cls, key, suffix, prefix, tail, handle):
    config = config_cls()
    if hasattr(config, "namespace"):
        config = replace(config, namespace="tempting-default")
    with pytest.raises(ValueError, match="recorded cluster and namespace"):
        driver_cls(config=config).binding(ServiceHandle(handle))


def test_cnpg_url_selects_the_operators_custom_domain_and_preserves_credential_encoding():
    binding = CNPGPostgresDriver().binding(ServiceHandle("postgres/cluster/database-ns/main"))
    uri = "postgresql://app:p%40ss%3Aword@main-rw.database-ns.svc.private.internal:5432/app?sslmode=require"

    class Store:
        def get(self, path):
            assert path == "main-app"
            return {"uri": "postgresql://wrong-short-host/app", "fqdn-uri": uri, "password": "p@ss:word"}

    store = Store()
    assert resolve_secret_reference(store, binding.env_vars["DATABASE_URL"].secret_ref) == uri
    assert resolve_secret_reference(store, binding.env_vars["POSTGRES_PASSWORD"].secret_ref) == "p@ss:word"
