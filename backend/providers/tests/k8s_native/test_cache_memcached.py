"""In-cluster Memcached driver for kind=cache (#1465).

The driver closes ``cache`` on GCP and Azure at once, so the tests that matter
are the ones that keep it a real substitute for the AWS ElastiCache Memcached
variants: the same binding envelope, a node list an app can actually shard
over, and nothing left behind on teardown. Memcached authenticates nobody, so
the NetworkPolicy is load-bearing and is pinned here too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from _sdk.cluster import ApplyResult, DeleteResult
from _sdk.k8s_naming import agent_namespace, app_namespace
from _sdk.managed_service import (
    UPDATE_NOT_SUPPORTED_IN_PLACE,
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from aws.managed.memcached_elasticache import (
    ElastiCacheMemcachedConfig,
    ElastiCacheMemcachedDriver,
)
from k8s_native.managed.cache_memcached import (
    PORT,
    SIZE_TO_SPEC,
    MemcachedConfig,
    MemcachedDriver,
)

ORGANIZATION = "acme"
APP = "api"
NAMESPACE = app_namespace(organization_slug=ORGANIZATION, app_slug=APP)
CACHE_NAME = "api-prod-sessions"
HANDLE = f"cache/local-k8s/{NAMESPACE}/{CACHE_NAME}"


@dataclass
class _Call:
    cluster: str
    namespace: str
    manifests: list[dict[str, Any]]


class FakeClusterDriver:
    """Records applies/deletes and serves a canned live StatefulSet."""

    def __init__(
        self,
        *,
        statefulset: dict[str, Any] | None = None,
        apply_errors: list[Any] | None = None,
        delete_errors: list[str] | None = None,
    ) -> None:
        self.apply_calls: list[_Call] = []
        self.delete_calls: list[_Call] = []
        self._statefulset = statefulset
        self._apply_errors = apply_errors or []
        self._delete_errors = delete_errors or []

    def apply_manifests(
        self,
        cluster: str,
        namespace: str,
        manifests: list[dict[str, Any]],
        *,
        dry_run: bool = False,
    ) -> ApplyResult:
        del dry_run
        self.apply_calls.append(_Call(cluster, namespace, list(manifests)))
        return ApplyResult(created=[], updated=[], unchanged=[], errors=list(self._apply_errors))

    def delete_manifests(
        self,
        cluster: str,
        namespace: str,
        manifests: list[dict[str, Any]],
    ) -> DeleteResult:
        self.delete_calls.append(_Call(cluster, namespace, list(manifests)))
        return DeleteResult(deleted=[], not_found=[], errors=list(self._delete_errors))

    def get_manifest(
        self,
        cluster: str,
        namespace: str | None,
        kind: str,
        name: str,
    ) -> dict[str, Any] | None:
        del cluster, namespace, kind, name
        return self._statefulset


def _spec(**overrides: Any) -> ProvisionSpec:
    base: dict[str, Any] = {
        "organization_id": "1",
        "organization_slug": ORGANIZATION,
        "app_id": "1",
        "app_slug": APP,
        "environment_id": "1",
        "environment_name": "prod",
        "tenant_cluster_id": "local-k8s",
        "service_handle_hint": "sessions",
        "size": "small",
    }
    base.update(overrides)
    return ProvisionSpec(**base)


def _statefulset(*, replicas: int, ready: int = 0) -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {"name": CACHE_NAME, "namespace": NAMESPACE},
        "spec": {"replicas": replicas},
        "status": {"readyReplicas": ready},
    }


def _by_kind(manifests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {manifest["kind"]: manifest for manifest in manifests}


def _provision(size: str = "small", **config: Any) -> tuple[FakeClusterDriver, list[dict[str, Any]]]:
    cluster_driver = FakeClusterDriver()
    driver = MemcachedDriver(config=MemcachedConfig(cluster_driver=cluster_driver))
    result = driver.provision(_spec(size=size, config=config))
    assert result.ok, result.message
    assert result.handle == HANDLE
    return cluster_driver, cluster_driver.apply_calls[0].manifests


# ---- provisioning ---------------------------------------------------------


def test_provision_lands_in_the_app_namespace_with_the_ring_wired_together() -> None:
    cluster_driver, manifests = _provision()
    call = cluster_driver.apply_calls[0]
    assert (call.cluster, call.namespace) == ("local-k8s", NAMESPACE)

    objects = _by_kind(manifests)
    assert set(objects) == {"StatefulSet", "Service", "NetworkPolicy"}
    statefulset, service = objects["StatefulSet"], objects["Service"]

    # A client shards over per-pod DNS, which only exists when the StatefulSet
    # is governed by its own headless Service.
    assert statefulset["spec"]["serviceName"] == service["metadata"]["name"]
    assert service["spec"]["clusterIP"] == "None"
    assert service["spec"]["selector"] == statefulset["spec"]["selector"]["matchLabels"]
    assert service["spec"]["ports"][0]["port"] == PORT
    selector = statefulset["spec"]["selector"]["matchLabels"]
    assert selector.items() <= statefulset["spec"]["template"]["metadata"]["labels"].items()


@pytest.mark.parametrize("size", sorted(SIZE_TO_SPEC))
def test_size_sets_the_ring_width_and_leaves_headroom_above_the_slab_cap(size: str) -> None:
    """``-m`` caps the slab allocator only. A container limit equal to it gets
    the pod OOM-killed as soon as connection buffers grow."""
    _, manifests = _provision(size)
    container = _by_kind(manifests)["StatefulSet"]["spec"]["template"]["spec"]["containers"][0]
    expected = SIZE_TO_SPEC[size]

    assert _by_kind(manifests)["StatefulSet"]["spec"]["replicas"] == expected["replicas"]
    args = container["args"]
    assert args[args.index("-m") + 1] == str(expected["max_memory_mb"])
    assert _mebibytes(container["resources"]["limits"]["memory"]) > expected["max_memory_mb"]


def _mebibytes(quantity: str) -> int:
    if quantity.endswith("Mi"):
        return int(quantity[:-2])
    if quantity.endswith("Gi"):
        return int(quantity[:-2]) * 1024
    raise AssertionError(f"unhandled memory quantity {quantity!r}")


def test_config_overrides_the_size_defaults() -> None:
    _, manifests = _provision("small", replicas=4, max_memory_mb=2048, max_connections=4096)
    statefulset = _by_kind(manifests)["StatefulSet"]
    container = statefulset["spec"]["template"]["spec"]["containers"][0]

    assert statefulset["spec"]["replicas"] == 4
    assert container["args"] == ["-m", "2048", "-c", "4096"]


@pytest.mark.parametrize(
    "config",
    [
        {"replicas": 0},
        {"replicas": 999},
        {"replicas": True},
        {"max_memory_mb": 8},
        {"max_connections": 1_000_000},
    ],
)
def test_out_of_range_config_is_refused_before_anything_is_applied(config: dict[str, Any]) -> None:
    cluster_driver = FakeClusterDriver()
    driver = MemcachedDriver(config=MemcachedConfig(cluster_driver=cluster_driver))

    result = driver.provision(_spec(config=config))

    assert result.ok is False
    assert result.errors == ["invalid_memcached_config"]
    assert cluster_driver.apply_calls == []


def test_names_and_namespace_survive_slugs_too_long_for_a_dns_label() -> None:
    """Namespaces come from the naming helpers, never an f-string (#1379): an
    organization slug may be 200 characters and a DNS label may be 63."""
    long_org = "o" * 200
    cluster_driver = FakeClusterDriver()
    driver = MemcachedDriver(config=MemcachedConfig(cluster_driver=cluster_driver))

    result = driver.provision(_spec(organization_slug=long_org))

    namespace = app_namespace(organization_slug=long_org, app_slug=APP)
    assert result.handle.split("/")[2] == namespace
    assert cluster_driver.apply_calls[0].namespace == namespace
    assert len(namespace) <= 63
    for manifest in cluster_driver.apply_calls[0].manifests:
        assert len(manifest["metadata"]["name"]) <= 63


def test_apply_failure_returns_no_handle() -> None:
    """A handle for objects that were never created would make deprovision
    chase a resource that does not exist."""
    cluster_driver = FakeClusterDriver(apply_errors=["StatefulSet/api-prod-sessions: forbidden"])
    driver = MemcachedDriver(config=MemcachedConfig(cluster_driver=cluster_driver))

    result = driver.provision(_spec())

    assert result.ok is False
    assert result.handle == ""
    assert result.errors == ["StatefulSet/api-prod-sessions: forbidden"]


# ---- isolation ------------------------------------------------------------


def test_network_policy_admits_only_the_app_namespace_and_its_agents() -> None:
    """Memcached has no authentication, so this policy is the whole access
    control story. An empty or absent peer list exposes every tenant's cache
    to every pod in the cluster."""
    _, manifests = _provision()
    policy = _by_kind(manifests)["NetworkPolicy"]
    statefulset = _by_kind(manifests)["StatefulSet"]

    assert policy["spec"]["podSelector"] == statefulset["spec"]["selector"]
    assert policy["spec"]["policyTypes"] == ["Ingress"]
    rule = policy["spec"]["ingress"][0]
    assert rule["ports"] == [{"protocol": "TCP", "port": PORT}]
    assert rule["from"] == [
        {"podSelector": {}},
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": agent_namespace(ORGANIZATION)},
            },
        },
    ]


def test_pods_run_unprivileged_with_no_service_account_token() -> None:
    _, manifests = _provision()
    pod = _by_kind(manifests)["StatefulSet"]["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}


# ---- binding --------------------------------------------------------------


def _binding(*, replicas: int) -> dict[str, str]:
    driver = MemcachedDriver(
        config=MemcachedConfig(
            cluster_driver=FakeClusterDriver(statefulset=_statefulset(replicas=replicas)),
        ),
    )
    binding = driver.binding(ServiceHandle(handle=HANDLE))
    return {key: ref.literal or "" for key, ref in binding.env_vars.items()}


def test_binding_lists_every_pod_in_the_ring() -> None:
    envs = _binding(replicas=3)
    service_fqdn = f"{CACHE_NAME}.{NAMESPACE}.svc.cluster.local"

    assert envs["CACHE_HOST"] == service_fqdn
    assert envs["CACHE_PORT"] == str(PORT)
    assert envs["CACHE_PROTOCOL"] == "memcached"
    assert envs["CACHE_NODES"].split(",") == [f"{CACHE_NAME}-{ordinal}.{service_fqdn}:{PORT}" for ordinal in range(3)]
    assert envs["CACHE_TLS"] == "0"
    assert envs["CACHE_RESOURCE_ARN"] == f"k8s://local-k8s/{NAMESPACE}/{CACHE_NAME}"


def test_binding_reads_the_live_ring_rather_than_the_size_table() -> None:
    """The node list is what the client hashes over. Reading it from the size
    the service was ordered at would hand out endpoints that do not exist
    after the ring is reprovisioned at another size."""
    assert len(_binding(replicas=1)["CACHE_NODES"].split(",")) == 1
    assert len(_binding(replicas=3)["CACHE_NODES"].split(",")) == 3


def test_binding_matches_the_aws_elasticache_memcached_envelope() -> None:
    """The point of the variant: a manifest moves between AWS and in-cluster
    without the app reading different variables."""
    aws = ElastiCacheMemcachedDriver(
        config=ElastiCacheMemcachedConfig(region="us-east-1", cache_subnet_group="astrolift"),
        elasticache_client=object(),
    )
    in_cluster = MemcachedDriver()

    assert set(_binding(replicas=2)) == set(aws.binding_schema().env_vars)
    assert set(in_cluster.binding_schema().env_vars) == set(aws.binding_schema().env_vars)


def test_binding_refuses_when_the_ring_is_gone() -> None:
    """Falling back to a guessed endpoint would give the app a cache client
    pointed at nothing, which fails as cache misses rather than as an error."""
    driver = MemcachedDriver(config=MemcachedConfig(cluster_driver=FakeClusterDriver(statefulset=None)))

    with pytest.raises(ValueError, match="does not exist"):
        driver.binding(ServiceHandle(handle=HANDLE))


def test_binding_refuses_a_legacy_handle() -> None:
    driver = MemcachedDriver(
        config=MemcachedConfig(cluster_driver=FakeClusterDriver(statefulset=_statefulset(replicas=1))),
    )

    with pytest.raises(ValueError, match="cluster locator"):
        driver.binding(ServiceHandle(handle=f"cache/{CACHE_NAME}"))


# ---- status ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("replicas", "ready", "state"),
    [(3, 0, "provisioning"), (3, 2, "provisioning"), (3, 3, "available"), (1, 1, "available")],
)
def test_status_waits_for_every_node_the_binding_will_advertise(replicas: int, ready: int, state: str) -> None:
    driver = MemcachedDriver(
        config=MemcachedConfig(
            cluster_driver=FakeClusterDriver(statefulset=_statefulset(replicas=replicas, ready=ready)),
        ),
    )

    assert driver.status(ServiceHandle(handle=HANDLE)).state == state


def test_status_reports_a_deleted_ring_as_deprovisioned() -> None:
    driver = MemcachedDriver(config=MemcachedConfig(cluster_driver=FakeClusterDriver(statefulset=None)))

    assert driver.status(ServiceHandle(handle=HANDLE)).state == "deprovisioned"


# ---- teardown + update ----------------------------------------------------


def test_deprovision_deletes_everything_provision_applied() -> None:
    """#366: an orphaned Service or NetworkPolicy survives tenant teardown and
    blocks the next provision under the same name."""
    cluster_driver, applied = _provision()
    driver = MemcachedDriver(config=MemcachedConfig(cluster_driver=cluster_driver))

    result = driver.deprovision(DeprovisionSpec(handle=HANDLE))

    assert result.ok is True
    call = cluster_driver.delete_calls[0]
    assert (call.cluster, call.namespace) == ("local-k8s", NAMESPACE)
    assert {(m["kind"], m["metadata"]["name"]) for m in call.manifests} == {
        (m["kind"], m["metadata"]["name"]) for m in applied
    }
    for manifest in call.manifests:
        assert manifest["metadata"]["namespace"] == NAMESPACE


def test_deprovision_reports_delete_failures() -> None:
    cluster_driver = FakeClusterDriver(delete_errors=["StatefulSet/api-prod-sessions: forbidden"])
    driver = MemcachedDriver(config=MemcachedConfig(cluster_driver=cluster_driver))

    result = driver.deprovision(DeprovisionSpec(handle=HANDLE))

    assert result.ok is False
    assert result.errors == ["StatefulSet/api-prod-sessions: forbidden"]


def test_deprovision_refuses_a_legacy_handle_instead_of_guessing_a_namespace() -> None:
    cluster_driver = FakeClusterDriver()
    driver = MemcachedDriver(config=MemcachedConfig(cluster_driver=cluster_driver))

    result = driver.deprovision(DeprovisionSpec(handle=f"cache/{CACHE_NAME}"))

    assert result.ok is False
    assert result.retryable is False
    assert result.errors == ["legacy_handle_missing_locator"]
    assert cluster_driver.delete_calls == []


def test_update_refuses_permanently_rather_than_reporting_a_change_it_did_not_make() -> None:
    driver = MemcachedDriver()

    result = driver.update(UpdateSpec(handle=HANDLE, config={"replicas": 5}))

    assert result.ok is False
    assert result.retryable is False
    assert result.errors == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    assert driver.editable_fields() == []
