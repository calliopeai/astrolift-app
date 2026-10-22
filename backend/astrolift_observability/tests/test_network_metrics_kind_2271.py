"""Real kubelet cAdvisor, Prometheus and Kubernetes ownership for pod network I/O (#2271).

Opt-in, like the #2219 workload signal tests whose fixtures it shares: it runs
only against the explicit disposable Kind context they demand, and skips
everywhere else.
"""

import time
from types import SimpleNamespace

import pytest
import strawberry
from django.utils import timezone

from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_observability.tests.test_network_metrics_2271 import QUERY
from astrolift_registry.models import Workload
from core.permissions import Permission
from core.tests.test_metric_runtime_identity_2219 import membership
from core.tests.utils.scope_world import as_tenant, bind_role, make_user

pytestmark = pytest.mark.django_db
pytest_plugins = ["core.tests.test_metric_runtime_identity_2219", "core.testing.prometheus_kind"]

PINGS, PAYLOAD = 20, 1200
# An ICMP echo carries the payload plus 8 bytes of ICMP and 20 of IPv4.
PING_BYTES = PINGS * (PAYLOAD + 28)


def read(world, user, workload=None):
    schema = strawberry.Schema(query=GoldenSignalsQuery)
    context = SimpleNamespace(user=user, request=SimpleNamespace(user=user))
    with as_tenant(world, user):
        result = schema.execute_sync(
            QUERY,
            variable_values={
                "app": world.app.slug,
                "environment": world.environment.name,
                "workload": workload,
                "range": 300,
            },
            context_value=context,
        )
    assert result.errors is None, result.errors
    data = result.data["astroliftAppNetworkMetrics"]
    return data, {row["direction"]: row for row in data["series"]}


@pytest.fixture
def reader(runtime):
    user = make_user("network-2271")
    bind_role(
        user, permissions=[Permission.APP_READ], kind="APP", scope_id=runtime.app.pk, slug="network-reader"
    )
    return user


def test_real_cadvisor_network_reads_only_the_workloads_own_pods(native, kind_prometheus, reader):
    from kubernetes.stream import stream

    world, _, _, core, _ = native
    namespace = world.environment.k8s_namespace
    api, worker = membership(native)[0], membership(native, world.other)[0]
    # Bytes count from a full rate window after the newest pod started.
    while time.time() < max(api.started_at, worker.started_at) + 125:
        time.sleep(1)
    worker_ip = core.read_namespaced_pod(worker.pod_name, namespace).status.pod_ip
    output = stream(
        core.connect_get_namespaced_pod_exec,
        api.pod_name,
        namespace,
        container="api",
        command=["ping", "-c", str(PINGS), "-i", "0.2", "-s", str(PAYLOAD), worker_ip],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
    assert f"{PINGS} packets received" in output, output
    # The kubelet refreshes container stats every 10-15 s; Prometheus scrapes each second.
    deadline = time.monotonic() + 90
    while True:
        data, series = read(world, reader, workload="api")
        if series["TRANSMIT"]["totalBytes"] and series["TRANSMIT"]["totalBytes"] >= PING_BYTES:
            break
        assert time.monotonic() < deadline, series
        time.sleep(3)
    assert data["reason"] == "OK"
    for row in series.values():
        measured = row["measurement"]
        assert row["reason"] == "OK" and measured["available"]
        assert measured["effectiveScope"] == "WORKLOAD"
        assert measured["source"] == "CADVISOR"
        assert measured["identityBasis"] == "VERIFIED_RUNTIME"
        assert measured["target"]["workloadId"] == str(world.workload.guid)
        assert measured["target"]["namespace"] == namespace
        assert [c["podUid"] for c in measured["containers"]] == [api.pod_uid]
        assert f'pod="{api.pod_name}"' in row["promql"] and api.pod_uid in row["promql"]
        assert worker.pod_name not in row["promql"]
        assert row["samples"] and all(point["value"] >= 0 for point in row["samples"])
    # Echo requests leave the api pod and replies come back.
    assert series["RECEIVE"]["totalBytes"] >= PING_BYTES

    _, sibling = read(world, reader, workload=world.other.slug)
    assert [c["podUid"] for c in sibling["RECEIVE"]["measurement"]["containers"]] == [worker.pod_uid]
    assert sibling["RECEIVE"]["totalBytes"] >= PING_BYTES
    assert api.pod_name not in sibling["RECEIVE"]["promql"]

    environment, total = read(world, reader)
    assert environment["reason"] == "OK"
    assert total["RECEIVE"]["measurement"]["identityBasis"] == "NAMESPACE"
    assert total["RECEIVE"]["promql"].count(f'namespace="{namespace}"') == 1
    assert total["RECEIVE"]["totalBytes"] >= sibling["RECEIVE"]["totalBytes"]

    # A same-name replacement cannot adopt the running pods, and is never the namespace total.
    world.workload.deleted_at = timezone.now()
    world.workload.save(update_fields=["deleted_at"])
    replacement = Workload.objects.create(
        registered_app=world.app, slug="api", name="Replacement", kind="deployment"
    )
    refused, rows = read(world, reader, workload="api")
    assert refused["reason"] == "ERROR"
    for row in rows.values():
        assert row["measurement"]["target"]["workloadId"] == str(replacement.guid)
        assert row["measurement"]["unavailableReason"] == "OWNERSHIP_UNVERIFIED"
        assert row["samples"] == [] and row["totalBytes"] is None and row["promql"] == ""
