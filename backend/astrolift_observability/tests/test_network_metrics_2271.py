"""Pod network I/O over real PostgreSQL, role bindings and the Prometheus HTTP wire (#2271).

The collector double answers ``query_range`` over HTTP the way Prometheus
does, so the strict transport, the series validation and the GraphQL types
all run for real; only the answers are scripted. It cannot evaluate PromQL,
which is the point of the ``foreign incarnation`` case: a series the
selector should have excluded must still be refused by the reader.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
import strawberry
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability.network_metrics import cgroup_pattern
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_operations import prometheus_client
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_cluster, make_user
from providers._sdk.workload_metrics import MetricContainer, MetricMembershipUnavailable

pytestmark = pytest.mark.django_db

QUERY = """query Network($app: String!, $environment: String, $workload: String, $range: Int) {
  astroliftAppNetworkMetrics(appSlug: $app, environmentName: $environment,
    workloadSlug: $workload, rangeSeconds: $range) {
    reason rangeSeconds stepSeconds rateWindowSeconds sourcedAt
    series { direction unit totalBytes promql reason samples { ts value } measurement {
      effectiveScope source identityBasis available unavailableReason membershipObservedAt measurementStart
      target { organizationId appId appSlug environmentId environmentName clusterId namespace workloadId workloadSlug }
      containers { podName podUid containerName containerId }
    } }
  }
}"""

RX, TX = "container_network_receive_bytes_total", "container_network_transmit_bytes_total"
POD_A = ("api-7d9f-a", "6f1c2a64-0b7e-4c1a-9d3e-2b5f8a1c0e11")
POD_B = ("api-7d9f-b", "0c5ad2f1-6a7b-4c41-9b8d-2e8f9d5e1a22")


def _systemd_id(uid):
    return (
        "/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod"
        + uid.replace("-", "_")
        + ".slice/cri-containerd-"
        + "a" * 64
        + ".scope"
    )


def _cgroupfs_id(uid):
    return "/kubepods/burstable/pod" + uid + "/" + "b" * 64


@pytest.fixture
def collector():
    """A Prometheus query_range endpoint answering from ``state.answer``."""
    state = SimpleNamespace(requests=[], answer=lambda request: [], fail_when=lambda query: False)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            request = SimpleNamespace(
                query=form["query"][0],
                start=int(form["start"][0]),
                end=int(form["end"][0]),
                step=int(form["step"][0]),
            )
            state.requests.append(request)
            if state.fail_when(request.query):
                self.send_response(503)
                self.end_headers()
                return
            body = json.dumps(
                {"status": "success", "data": {"resultType": "matrix", "result": state.answer(request)}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    prometheus_client.clear_cache_for_tests()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        prometheus_client.clear_cache_for_tests()


def steps(request):
    return list(range(request.start, request.end + 1, request.step))


def row(labels, request, value):
    return {"metric": labels, "values": [[ts, str(value)] for ts in steps(request)]}


def rate_of(query):
    """Bytes/s the scripted pods move: 2 KiB in, 512 B out per pod."""
    return 2048.0 if RX in query else 512.0


def namespace_answer(request):
    per_second = rate_of(request.query)
    if "increase(" in request.query:
        seconds = int(re.search(r"\[(\d+)s\]", request.query).group(1))
        return [row({}, request, per_second * seconds)]
    return [row({}, request, per_second)]


def pods_answer(namespace, pods, ids=None):
    """One series per pod, as ``sum by (namespace, pod, id)`` returns them."""

    def answer(request):
        per_second = rate_of(request.query)
        rows = []
        for index, (pod, uid) in enumerate(pods):
            labels = {
                "namespace": namespace,
                "pod": pod,
                "id": (ids or {}).get(pod) or (_systemd_id(uid) if index % 2 == 0 else _cgroupfs_id(uid)),
            }
            if "increase(" in request.query:
                seconds = int(re.search(r"\[(\d+)s\]", request.query).group(1))
                rows.append(row(labels, request, per_second * seconds))
            else:
                rows.append(row(labels, request, per_second))
        return rows

    return answer


@pytest.fixture
def world(collector):
    world = ScopeWorld("net2271")
    world.app = world.medops_app
    world.cluster = make_cluster(world, "net2271")
    world.cluster.provider_config = {"prometheus_endpoint": collector.endpoint}
    world.cluster.save(update_fields=["provider_config"])
    world.environment = AppEnvironment.objects.create(
        registered_app=world.app,
        tenant_cluster=world.cluster,
        name="production",
        k8s_namespace="acme-qs-ops-production",
    )
    world.workload = Workload.objects.create(
        registered_app=world.app, slug="api", name="API", kind="deployment"
    )
    world.user = make_user("net2271")
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="APP", scope_id=world.app.pk, slug="net-reader"
    )
    return world


@pytest.fixture
def membership(monkeypatch):
    """The workload's verified containers, as the cluster driver reports them (#2263)."""
    state = SimpleNamespace(calls=[], members=(), error=None, supported=True)

    def metric_containers(**selectors):
        state.calls.append(selectors)
        if state.error is not None:
            raise state.error
        return state.members

    def driver(cluster):
        if not state.supported:
            return SimpleNamespace()
        return SimpleNamespace(metric_containers=metric_containers)

    monkeypatch.setattr("astrolift_observability.network_metrics._driver_for_cluster", driver)
    monkeypatch.setattr("astrolift_observability.network_metrics._auth_for_cluster", lambda cluster: None)
    return state


def container(pod, *, started_at, name="api", runtime="c" * 64):
    return MetricContainer(pod[0], pod[1], name, runtime, started_at)


def read(world, user=None, *, environment="production", workload=None, range_seconds=3600):
    user = user or world.user
    schema = strawberry.Schema(query=GoldenSignalsQuery)
    context = SimpleNamespace(user=user, request=SimpleNamespace(user=user))
    with as_tenant(world, user):
        return schema.execute_sync(
            QUERY,
            variable_values={
                "app": world.app.slug,
                "environment": environment,
                "workload": workload,
                "range": range_seconds,
            },
            context_value=context,
        )


def panel(result):
    assert result.errors is None, result.errors
    data = result.data["astroliftAppNetworkMetrics"]
    return data, {row["direction"]: row for row in data["series"]}


def test_environment_reads_its_own_namespace_rates_totals_and_provenance(world, collector):
    collector.answer = namespace_answer
    before = timezone.now()
    data, series = panel(read(world))
    assert data["reason"] == "OK"
    assert (data["rangeSeconds"], data["stepSeconds"], data["rateWindowSeconds"]) == (3600, 15, 120)
    assert dt.datetime.fromisoformat(data["sourcedAt"]) >= before.replace(microsecond=0)
    assert set(series) == {"RECEIVE", "TRANSMIT"}
    for direction, counter, per_second in (("RECEIVE", RX, 2048.0), ("TRANSMIT", TX, 512.0)):
        row = series[direction]
        assert row["reason"] == "OK"
        assert row["unit"] == "bytes_per_second"
        assert row["promql"] == f'sum(rate({counter}{{namespace="acme-qs-ops-production"}}[2m]))'
        assert len(row["samples"]) == 241
        assert {point["value"] for point in row["samples"]} == {per_second}
        assert row["totalBytes"] == per_second * 3600
        measured = row["measurement"]
        assert measured["available"] is True
        assert measured["unavailableReason"] is None
        assert measured["effectiveScope"] == "APP_ENVIRONMENT"
        assert measured["source"] == "CADVISOR"
        assert measured["identityBasis"] == "NAMESPACE"
        assert measured["containers"] == []
        assert measured["measurementStart"] is None
        assert measured["target"] == {
            "organizationId": str(world.org.guid),
            "appId": str(world.app.guid),
            "appSlug": world.app.slug,
            "environmentId": str(world.environment.guid),
            "environmentName": "production",
            "clusterId": str(world.cluster.guid),
            "namespace": "acme-qs-ops-production",
            "workloadId": None,
            "workloadSlug": None,
        }
    queries = [request.query for request in collector.requests]
    assert len(queries) == 4
    assert all('{namespace="acme-qs-ops-production"}' in query for query in queries)
    assert f'sum(increase({RX}{{namespace="acme-qs-ops-production"}}[3600s]))' in queries


def test_workload_sums_only_its_verified_pods_bound_to_their_uids(world, collector, membership):
    now = time.time()
    membership.members = (
        container(POD_A, started_at=now - 7200),
        container(POD_A, started_at=now - 7200, name="sidecar", runtime="d" * 64),
        container(POD_B, started_at=now - 600),
    )
    collector.answer = pods_answer("acme-qs-ops-production", [POD_A, POD_B])
    data, series = panel(read(world, workload="api"))
    assert data["reason"] == "OK"
    selectors = membership.calls[0]
    assert selectors["namespace"] == "acme-qs-ops-production"
    assert selectors["app_id"] == str(world.app.guid)
    assert selectors["environment_id"] == str(world.environment.guid)
    assert selectors["workload_id"] == str(world.workload.guid)
    assert (selectors["workload_slug"], selectors["workload_kind"]) == ("api", "deployment")
    # The newest pod started ten minutes ago: the measurement begins once it
    # has a full rate window, and the bytes count from the same instant.
    for direction, counter, per_second in (("RECEIVE", RX, 2048.0), ("TRANSMIT", TX, 512.0)):
        row = series[direction]
        measured = row["measurement"]
        assert measured["effectiveScope"] == "WORKLOAD"
        assert measured["identityBasis"] == "VERIFIED_RUNTIME"
        assert measured["available"] is True
        assert measured["target"]["workloadId"] == str(world.workload.guid)
        assert [c["podName"] for c in measured["containers"]] == [POD_A[0], POD_A[0], POD_B[0]]
        assert measured["membershipObservedAt"] is not None
        measured_from = int(dt.datetime.fromisoformat(measured["measurementStart"]).timestamp())
        assert measured_from == int(now - 600) + 1 + 120
        assert {point["value"] for point in row["samples"]} == {2 * per_second}
        assert dt.datetime.fromisoformat(row["samples"][0]["ts"]).timestamp() == measured_from
        rates, totals = (
            next(r for r in collector.requests if counter in r.query and kind in r.query)
            for kind in ("rate(", "increase(")
        )
        assert rates.start == measured_from
        assert f"[{totals.end - measured_from}s]" in totals.query
        assert row["totalBytes"] == 2 * per_second * (totals.end - measured_from)
        assert row["promql"] == rates.query
        for pod, uid in (POD_A, POD_B):
            assert f'pod="{pod}"' in row["promql"]
            assert uid in row["promql"] and uid.replace("-", "_") in row["promql"]
    assert len(collector.requests) == 4
    for request in collector.requests:
        assert request.query.startswith("sum by (namespace, pod, id) (")
        assert request.query.count('{id=~"') == 0 and request.query.count("id=~") == 2
        assert request.query.count('namespace="acme-qs-ops-production",pod="') == 2


@pytest.mark.parametrize(
    "problem,expected",
    [
        ("other-incarnation", "INVALID_DATA"),
        ("other-pod", "INVALID_DATA"),
        ("other-namespace", "INVALID_DATA"),
        ("same-pod-twice", "AMBIGUOUS_SERIES"),
        ("more-series-than-pods", "QUERY_ERROR"),
    ],
)
def test_a_series_outside_the_verified_pods_refuses_the_workload_read(
    world, collector, membership, problem, expected
):
    now = time.time()
    membership.members = (container(POD_A, started_at=now - 7200), container(POD_B, started_at=now - 7200))
    stranger = ("api-7d9f-c", "9e8d7c6b-5a49-4382-9170-6f5e4d3c2b33")
    ids, pods, namespace = {}, [POD_A, POD_B], "acme-qs-ops-production"
    if problem == "other-incarnation":
        # A same-name pod's earlier incarnation: right name, another UID.
        ids = {POD_A[0]: _systemd_id("11111111-2222-4333-8444-555555555555")}
    elif problem == "other-pod":
        pods = [POD_A, stranger]
    elif problem == "other-namespace":
        namespace = "acme-gateway-production"
    elif problem == "same-pod-twice":
        pods = [POD_A, POD_A]
    else:
        pods = [POD_A, POD_B, stranger]
    collector.answer = pods_answer(namespace, pods, ids)
    data, series = panel(read(world, workload="api"))
    assert data["reason"] == "ERROR"
    for row in series.values():
        assert (row["reason"], row["measurement"]["unavailableReason"]) == ("ERROR", expected)
        assert row["samples"] == [] and row["totalBytes"] is None
        assert row["measurement"]["available"] is False


def test_a_pod_without_series_is_partial_not_a_smaller_sum(world, collector, membership):
    now = time.time()
    membership.members = (container(POD_A, started_at=now - 7200), container(POD_B, started_at=now - 7200))
    collector.answer = pods_answer("acme-qs-ops-production", [POD_A])
    data, series = panel(read(world, workload="api"))
    assert data["reason"] == "ERROR"
    for row in series.values():
        assert row["measurement"]["unavailableReason"] == "PARTIAL_DATA"
        assert row["samples"] == [] and row["totalBytes"] is None
    # Never widened to the namespace total.
    assert all("pod=" in request.query for request in collector.requests)


def test_a_step_one_pod_missed_is_partial(world, collector, membership):
    now = time.time()
    membership.members = (container(POD_A, started_at=now - 7200), container(POD_B, started_at=now - 7200))
    complete = pods_answer("acme-qs-ops-production", [POD_A, POD_B])

    def gap(request):
        rows = complete(request)
        if "increase(" not in request.query:
            rows[1]["values"] = rows[1]["values"][:-3]
        return rows

    collector.answer = gap
    data, series = panel(read(world, workload="api"))
    assert data["reason"] == "ERROR"
    assert {row["measurement"]["unavailableReason"] for row in series.values()} == {"PARTIAL_DATA"}


def test_rates_without_a_window_total_are_partial(world, collector):
    def no_totals(request):
        return [] if "increase(" in request.query else namespace_answer(request)

    collector.answer = no_totals
    data, series = panel(read(world))
    assert data["reason"] == "ERROR"
    assert {row["measurement"]["unavailableReason"] for row in series.values()} == {"PARTIAL_DATA"}
    assert all(row["samples"] == [] for row in series.values())


def test_window_total_reads_the_latest_evaluation_the_series_share(world, collector):
    """A backend may align its steps, so the total is not looked up at ``end``."""

    def aligned(request):
        rows = namespace_answer(request)
        if "increase(" in request.query:
            rows[0]["values"] = [[request.end - 7, rows[0]["values"][-1][1]]]
        return rows

    collector.answer = aligned
    data, series = panel(read(world))
    assert data["reason"] == "OK"
    assert series["RECEIVE"]["totalBytes"] == 2048.0 * 3600
    assert series["TRANSMIT"]["totalBytes"] == 512.0 * 3600


def test_window_totals_from_different_evaluations_are_partial(world, collector, membership):
    now = time.time()
    membership.members = (container(POD_A, started_at=now - 7200), container(POD_B, started_at=now - 7200))
    complete = pods_answer("acme-qs-ops-production", [POD_A, POD_B])

    def skewed(request):
        rows = complete(request)
        if "increase(" in request.query:
            rows[1]["values"] = rows[1]["values"][:-1]
        return rows

    collector.answer = skewed
    data, series = panel(read(world, workload="api"))
    assert data["reason"] == "ERROR"
    assert {row["measurement"]["unavailableReason"] for row in series.values()} == {"PARTIAL_DATA"}
    assert all(row["totalBytes"] is None for row in series.values())


def test_one_failed_direction_leaves_the_other_measured(world, collector):
    collector.answer = namespace_answer
    collector.fail_when = lambda query: TX in query
    data, series = panel(read(world))
    assert data["reason"] == "OK"
    assert series["RECEIVE"]["reason"] == "OK" and series["RECEIVE"]["samples"]
    assert series["TRANSMIT"]["reason"] == "ERROR"
    assert series["TRANSMIT"]["measurement"]["unavailableReason"] == "QUERY_ERROR"
    assert series["TRANSMIT"]["samples"] == [] and series["TRANSMIT"]["totalBytes"] is None


@pytest.mark.parametrize(
    "case,panel_reason,unavailable,queried",
    [
        ("no-endpoint", "NOT_CONFIGURED", "NOT_CONFIGURED", False),
        ("environment-not-recorded", "NOT_CONFIGURED", "ENVIRONMENT_NOT_FOUND", False),
        ("workload-not-found", "NOT_CONFIGURED", "WORKLOAD_NOT_FOUND", False),
        ("replaced-workload", "ERROR", "OWNERSHIP_UNVERIFIED", False),
        ("cluster-of-another-org", "NOT_CONFIGURED", "TARGET_NOT_OWNED", False),
        ("cluster-deleted", "NOT_CONFIGURED", "TARGET_NOT_OWNED", False),
        ("provider-without-membership", "NOT_SUPPORTED_BY_PROVIDER", "NOT_SUPPORTED_BY_PROVIDER", False),
        ("provider-not-implemented", "NOT_SUPPORTED_BY_PROVIDER", "NOT_SUPPORTED_BY_PROVIDER", False),
        ("no-pods", "NO_DATA_YET", "NO_PODS", False),
        ("pods-too-new", "NO_DATA_YET", "NO_DATA_YET", False),
        ("membership-denied", "ERROR", "PERMISSION_DENIED", False),
        ("membership-provider-error", "ERROR", "PROVIDER_ERROR", False),
        ("no-samples", "NO_DATA_YET", "NO_DATA_YET", True),
        ("collector-error", "ERROR", "QUERY_ERROR", True),
    ],
)
def test_every_unavailable_state_is_explicit_and_never_zero(
    world, collector, membership, case, panel_reason, unavailable, queried
):
    environment, workload = "production", "api"
    membership.members = (container(POD_A, started_at=time.time() - 7200),)
    collector.answer = lambda request: []
    if case == "no-endpoint":
        world.cluster.provider_config = {}
        world.cluster.save(update_fields=["provider_config"])
    elif case == "environment-not-recorded":
        environment = "staging"
    elif case == "workload-not-found":
        workload = "ghost"
    elif case == "replaced-workload":
        world.workload.deleted_at = timezone.now()
        world.workload.save(update_fields=["deleted_at"])
        Workload.objects.create(registered_app=world.app, slug="api", name="Replacement", kind="deployment")
        membership.error = MetricMembershipUnavailable("OWNERSHIP_UNVERIFIED")
    elif case == "cluster-of-another-org":
        world.cluster.organization = Organization.objects.create(name="Other", slug="other-net2271")
        world.cluster.save(update_fields=["organization"])
    elif case == "cluster-deleted":
        world.cluster.deleted_at = timezone.now()
        world.cluster.save(update_fields=["deleted_at"])
    elif case == "provider-without-membership":
        membership.supported = False
    elif case == "provider-not-implemented":
        membership.error = NotImplementedError()
    elif case == "no-pods":
        membership.error = MetricMembershipUnavailable("NO_PODS")
    elif case == "pods-too-new":
        membership.members = (container(POD_A, started_at=time.time() - 30),)
    elif case == "membership-denied":
        membership.error = MetricMembershipUnavailable("PERMISSION_DENIED")
    elif case == "membership-provider-error":
        membership.error = RuntimeError("kubelet unreachable")
    elif case == "collector-error":
        collector.fail_when = lambda query: True
    if case in ("no-samples", "collector-error", "no-endpoint", "environment-not-recorded"):
        workload = None
    data, series = panel(read(world, environment=environment, workload=workload))
    assert data["reason"] == panel_reason
    assert set(series) == {"RECEIVE", "TRANSMIT"}
    for row in series.values():
        assert row["reason"] == panel_reason
        assert row["measurement"]["available"] is False
        assert row["measurement"]["unavailableReason"] == unavailable
        assert row["samples"] == []
        assert row["totalBytes"] is None
    assert bool(collector.requests) is queried
    if case == "environment-not-recorded":
        assert series["RECEIVE"]["measurement"]["target"]["environmentId"] is None
    if case == "replaced-workload":
        replacement = Workload.objects.get(registered_app=world.app, slug="api", deleted_at__isnull=True)
        assert series["RECEIVE"]["measurement"]["target"]["workloadId"] == str(replacement.guid)
        assert membership.calls[0]["workload_id"] == str(replacement.guid)
        assert series["RECEIVE"]["measurement"]["identityBasis"] == "UNRESOLVED"


@pytest.mark.parametrize("grant", ["own-org", "own-app", "spoofed-organization"])
def test_another_tenant_never_reads_this_apps_network(world, collector, membership, grant):
    """Org B's people asking for org A's app slug: refused, or an empty
    envelope when an org-wide grant clears the gate. Nothing is read."""
    collector.answer = namespace_answer
    membership.members = (container(POD_A, started_at=time.time() - 7200),)
    foreign = ScopeWorld("net2271-foreign")
    outsider = make_user("net2271-outsider")
    if grant == "own-org":
        bind_role(
            outsider, permissions=[Permission.APP_READ], kind="ORG", scope_id=foreign.org.pk, slug="b-org"
        )
    else:
        bind_role(
            outsider,
            permissions=[Permission.APP_READ],
            kind="APP",
            scope_id=foreign.medops_app.pk,
            slug="b-app",
        )
    tenant = world if grant == "spoofed-organization" else foreign
    for workload in (None, "api"):
        with tenant_context(TenantContext(organization_id=tenant.org.pk, actor_user_id=outsider.pk)):
            schema = strawberry.Schema(query=GoldenSignalsQuery)
            context = SimpleNamespace(user=outsider, request=SimpleNamespace(user=outsider))
            result = schema.execute_sync(
                QUERY,
                variable_values={"app": world.app.slug, "environment": "production", "workload": workload},
                context_value=context,
            )
        if grant == "own-org":
            data, series = panel(result)
            assert data["reason"] == "NOT_CONFIGURED"
            assert series == {}
        else:
            assert result.errors and result.data is None
    assert collector.requests == [] and membership.calls == []
    # The owner's own reader still gets the data.
    assert panel(read(world))[0]["reason"] == "OK"


def test_same_slug_apps_in_two_orgs_each_read_only_their_own_namespace(world, collector):
    other = ScopeWorld("net2271-twin")
    twin = RegisteredApp.objects.create(
        organization=other.org,
        team=other.medops,
        project=other.medops_project,
        name="Twin",
        slug=world.app.slug,
        k8s_namespace="twin-qs-ops",
        provisioning_status="ready",
    )
    cluster = make_cluster(other, "net2271-twin")
    cluster.provider_config = {"prometheus_endpoint": collector.endpoint}
    cluster.save(update_fields=["provider_config"])
    AppEnvironment.objects.create(registered_app=twin, tenant_cluster=cluster, name="production")
    reader = make_user("net2271-twin")
    bind_role(reader, permissions=[Permission.APP_READ], kind="APP", scope_id=twin.pk, slug="twin-reader")
    collector.answer = namespace_answer
    data, series = panel(read(SimpleNamespace(org=other.org, app=twin), reader))
    assert data["reason"] == "OK"
    assert series["RECEIVE"]["measurement"]["target"]["organizationId"] == str(other.org.guid)
    assert series["RECEIVE"]["measurement"]["target"]["namespace"] == "twin-qs-ops"
    assert all('{namespace="twin-qs-ops"}' in request.query for request in collector.requests)
    assert not any("acme-qs-ops-production" in request.query for request in collector.requests)


def test_cgroup_pattern_binds_the_pod_uid_under_either_driver():
    uid = POD_A[1]
    pattern = re.compile(cgroup_pattern(uid))
    assert pattern.fullmatch(_systemd_id(uid))
    assert pattern.fullmatch(_cgroupfs_id(uid))
    assert pattern.fullmatch("/kubepods/pod" + uid + "/" + "e" * 64)
    assert pattern.fullmatch("/kubepods.slice/kubepods-pod" + uid.replace("-", "_") + ".slice/crio-1.scope")
    # The pod's own cgroup, another pod, a UID prefix and a bare mention never bind.
    assert not pattern.fullmatch("/kubepods/burstable/pod" + uid)
    assert not pattern.fullmatch("/kubepods.slice/kubepods-burstable-pod" + uid.replace("-", "_") + ".slice")
    assert not pattern.fullmatch(_systemd_id(POD_B[1]))
    assert not pattern.fullmatch("/kubepods/burstable/pod" + uid + "0/" + "b" * 64)
    assert not pattern.fullmatch("/system.slice/" + uid + "/x")
