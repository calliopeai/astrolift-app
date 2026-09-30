"""Actual owner/bearer/policy guards and bounded HTTP matrix reads."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from astrolift_identity import abac
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations import prometheus_client as prom
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_operations.topology_traffic import MAX_BODY_BYTES
from astrolift_operations.topology_traffic import TopologyTrafficStatus as Status
from astrolift_registry.models import Workload
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
START = NOW - timedelta(hours=1)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *a: None))
    monkeypatch.setattr("astrolift_operations.topology_traffic.timezone.now", lambda: NOW)
    prom.clear_cache_for_tests()
    w = ScopeWorld("traffic-2177")
    w.user = make_user("traffic-2177")
    w.cluster = make_cluster(w, "traffic-2177")
    w.cluster.provider_config = {"prometheus_endpoint": "http://prom.invalid"}
    w.cluster.save()
    w.app = w.medops_app
    w.env = AppEnvironment.objects.create(registered_app=w.app, tenant_cluster=w.cluster, name="production")
    w.api = Workload.objects.create(registered_app=w.app, name="api", slug="api")
    w.worker = Workload.objects.create(registered_app=w.app, name="worker", slug="worker")
    w.calls = []
    w.payload = matrix(w)

    def open_request(request, timeout):
        w.calls.append((request.full_url, timeout))
        if isinstance(w.payload, Exception):
            raise w.payload
        body = w.payload if isinstance(w.payload, bytes) else json.dumps(w.payload).encode()
        return io.BytesIO(body)

    monkeypatch.setattr("astrolift_operations.prometheus_client.urllib.request.urlopen", open_request)
    return w


def matrix(w, *, namespace=None, source="api", destination="worker"):
    ns = namespace or w.app.k8s_namespace
    rows = []
    for code, grpc, a, b in [
        ("200", "", 10, 20),
        ("503", "", 2, 4),
        ("200", "14", 1, 2),
        ("200", "0", 5, 10),
    ]:
        rows.append(
            {
                "metric": {
                    "source_workload_namespace": ns,
                    "source_workload": source,
                    "destination_workload_namespace": ns,
                    "destination_workload": destination,
                    "response_code": code,
                    "grpc_response_status": grpc,
                },
                "values": [[START.timestamp(), str(a)], [NOW.timestamp(), str(b)]],
            }
        )
    return {"status": "success", "data": {"resultType": "matrix", "result": rows}}


def grant(w, kind="APP"):
    ids = {"APP": w.app.pk, "PROJECT": w.medops_project.pk, "TEAM": w.medops.pk, "ORG": w.org.pk}
    return bind_role(
        w.user,
        permissions=[Permission.APP_READ_METRICS],
        kind=kind,
        scope_id=ids[kind],
        slug=f"metrics-{kind}",
    )


@contextmanager
def caller(w, token=None):
    current = set_current_api_token(token)
    try:
        with (
            tenant_context(
                TenantContext(
                    organization_id=w.org.pk,
                    actor_user_id=w.user.pk,
                    team_id=w.platform.pk,
                    project_id=w.platform_project.pk,
                )
            ),
            abac.request_attributes(abac.RequestAttributes(actor_user_id=w.user.pk)),
        ):
            yield
    finally:
        reset_current_api_token(current)


def read(w, **kwargs):
    with caller(w):
        return OperationsQuery().astrolift_topology_traffic(
            make_info(w.user), app_slug=w.app.slug, start=START, end=NOW, **kwargs
        )


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_real_owner_grants_ignore_selected_sibling_and_aggregate_http_and_grpc(world, kind):
    grant(world, kind)
    result = read(world)
    assert result.status == Status.AVAILABLE
    assert result.environments[0].namespace == world.app.k8s_namespace
    edge = result.environments[0].edges[0]
    assert str(edge.source_workload_id) == str(world.api.guid)
    assert edge.request_rate == 27
    assert edge.error_rate == 4.5
    assert edge.error_ratio == pytest.approx(1 / 6)
    assert [p.request_rate for p in edge.samples] == [18, 36]
    url, timeout = world.calls[0]
    params = parse_qs(urlsplit(url).query)
    query = params["query"][0]
    assert 'reporter="destination"' in query
    assert f'source_workload_namespace="{world.app.k8s_namespace}"' in query
    assert f'destination_workload_namespace="{world.app.k8s_namespace}"' in query
    assert 'source_workload=~"api|worker"' in query
    assert "source_app" not in query
    assert timeout == 5
    assert int(params["step"][0]) >= 30
    read(world)
    assert len(world.calls) == 1


@pytest.mark.parametrize("retired", ["app", "project", "team", "organization"])
def test_retired_owners_fail_before_http_or_cache(world, retired, monkeypatch):
    grant(world)
    row = {
        "app": world.app,
        "project": world.medops_project,
        "team": world.medops,
        "organization": world.org,
    }[retired]
    row.soft_delete()
    monkeypatch.setattr(prom._cache, "get", lambda *args: pytest.fail("cache accessed"))
    with pytest.raises(PermissionDenied):
        read(world)
    assert not world.calls


@pytest.mark.parametrize("scope,home", [([], True), (["admin"], False)])
def test_bearer_scope_and_home_team_are_ceilings(world, scope, home):
    grant(world, "ORG")
    token = SimpleNamespace(
        organization_id=world.org.pk, user_id=world.user.pk, team_id=world.platform.pk, scopes=scope
    )
    with caller(world, token), pytest.raises(PermissionDenied):
        OperationsQuery().astrolift_topology_traffic(
            make_info(world.user), app_slug=world.app.slug, start=START, end=NOW
        )
    assert not world.calls


def test_cached_request_cannot_survive_role_revocation(world, monkeypatch):
    binding = grant(world)
    assert read(world).status == Status.AVAILABLE
    binding.soft_delete()
    monkeypatch.setattr(prom._cache, "get", lambda *args: pytest.fail("revoked cache accessed"))
    with pytest.raises(PermissionDenied):
        read(world)
    assert len(world.calls) == 1


def deny_production(w):
    Policy.objects.create(
        organization=w.org,
        name="deny production",
        slug="deny-production",
        effect="DENY",
        action_pattern=Permission.APP_READ_METRICS.value,
        resource_pattern={"env": ["production"]},
        actor_pattern={},
        conditions=[],
        scope_level="ORG",
        scope_id=w.org.pk,
    )


def test_actual_environment_policy_denies_all_env_query_before_any_cache_read(world, monkeypatch):
    grant(world, "ORG")
    AppEnvironment.objects.create(
        registered_app=world.app, tenant_cluster=world.cluster, name="staging", k8s_namespace="staging"
    )
    deny_production(world)
    original_get = prom._cache.get
    monkeypatch.setattr(prom._cache, "get", lambda *args: pytest.fail("denied cache accessed"))
    for kwargs in ({}, {"environment_name": "production"}):
        with pytest.raises(PermissionDenied):
            read(world, **kwargs)
    assert not world.calls
    monkeypatch.setattr(prom._cache, "get", original_get)
    world.payload = matrix(world, namespace="staging")
    result = read(world, environment_name="staging")
    assert result.status == Status.AVAILABLE
    assert result.environments[0].namespace == "staging"
    assert len(world.calls) == 1


@pytest.mark.parametrize("invalid", ["foreign", "deleted", "inactive"])
def test_provider_target_is_checked_before_cached_reads(world, invalid, monkeypatch):
    grant(world)
    read(world)
    if invalid == "foreign":
        foreign = ScopeWorld("foreign-traffic")
        world.cluster.organization = foreign.org
        world.cluster.save()
    elif invalid == "deleted":
        world.cluster.soft_delete()
    else:
        world.cluster.is_active = False
        world.cluster.save()
    monkeypatch.setattr(prom._cache, "get", lambda *args: pytest.fail("invalid cluster cache accessed"))
    with pytest.raises(PermissionDenied):
        read(world)
    assert len(world.calls) == 1


def test_second_foreign_target_stops_app_all_env_before_first_http(world):
    grant(world, "ORG")
    foreign = ScopeWorld("foreign-traffic")
    cluster = make_cluster(foreign, "foreign-traffic")
    AppEnvironment.objects.create(
        registered_app=world.app, tenant_cluster=cluster, name="staging", k8s_namespace="staging"
    )
    with pytest.raises(PermissionDenied):
        read(world)
    assert not world.calls


def test_shared_same_app_namespaces_are_explicitly_unconfigured(world):
    grant(world)
    AppEnvironment.objects.create(registered_app=world.app, tenant_cluster=world.cluster, name="staging")
    result = read(world)
    assert result.status == Status.UNCONFIGURED
    assert all("share a namespace" in env.reason for env in result.environments)
    assert not world.calls


def test_sibling_controller_name_collision_is_excluded_even_under_org_grant(world):
    grant(world, "ORG")
    AppEnvironment.objects.create(
        registered_app=world.platform_app,
        tenant_cluster=world.cluster,
        name="production",
        k8s_namespace=world.app.k8s_namespace,
    )
    Workload.objects.create(registered_app=world.platform_app, name="api", slug="api")
    result = read(world)
    assert result.status == Status.NO_DATA
    query = parse_qs(urlsplit(world.calls[0][0]).query)["query"][0]
    assert 'source_workload=~"worker"' in query
    assert not result.environments[0].edges


@pytest.mark.parametrize("state", ["unconfigured", "unsupported", "no-data", "unavailable"])
def test_empty_states_are_honest_without_synthetic_points(world, state):
    grant(world)
    if state in {"unconfigured", "unsupported"}:
        world.cluster.provider_config = (
            {}
            if state == "unconfigured"
            else {"prometheus_endpoint": "http://prom.invalid", "observability_kind": "cloudwatch"}
        )
        world.cluster.save()
    elif state == "no-data":
        world.payload["data"]["result"] = []
    else:
        world.payload = URLError("connection refused")
    result = read(world)
    assert (
        result.status
        == {
            "unconfigured": Status.UNCONFIGURED,
            "unsupported": Status.UNCONFIGURED,
            "no-data": Status.NO_DATA,
            "unavailable": Status.UNAVAILABLE,
        }[state]
    )
    assert result.environments[0].edges == []
    assert len(world.calls) == (0 if state in {"unconfigured", "unsupported"} else 1)


@pytest.mark.parametrize(
    "invalid",
    [
        "NaN",
        "Inf",
        "-Inf",
        "garbage",
        "negative",
        "timestamp",
        "matrix",
        "data",
        "labels",
        "values",
        "missing",
        "row",
        "body",
        "duplicate",
        "status",
    ],
)
def test_invalid_backend_data_is_unavailable_and_never_zero_filled(world, invalid):
    grant(world)
    row = world.payload["data"]["result"][0]
    if invalid in {"NaN", "Inf", "-Inf", "garbage"}:
        row["values"][0][1] = invalid
    elif invalid == "negative":
        row["values"][0][1] = "-1"
    elif invalid == "timestamp":
        row["values"][0][0] = START.timestamp() - 1
    elif invalid == "matrix":
        world.payload["data"]["resultType"] = "vector"
    elif invalid == "data":
        world.payload["data"] = []
    elif invalid == "labels":
        row["metric"] = ["invalid"]
    elif invalid == "values":
        row["values"] = "not a list"
    elif invalid == "missing":
        del world.payload["data"]["result"]
    elif invalid == "row":
        world.payload["data"]["result"] = [None]
    elif invalid == "body":
        world.payload = b"x" * (MAX_BODY_BYTES + 1)
    elif invalid == "duplicate":
        row["values"].append(row["values"][-1])
    else:
        row["metric"]["response_code"] = "unknown"
    result = read(world)
    assert result.status == Status.UNAVAILABLE
    assert not result.environments[0].edges


@pytest.mark.parametrize(
    "start,end",
    [
        (NOW, START),
        (NOW - timedelta(days=2), NOW),
        (NOW - timedelta(seconds=30), NOW),
        (START, NOW + timedelta(seconds=1)),
        (START.replace(tzinfo=None), NOW),
    ],
)
def test_invalid_windows_are_rejected_before_http(world, start, end):
    grant(world)
    with caller(world), pytest.raises(ValueError):
        OperationsQuery().astrolift_topology_traffic(
            make_info(world.user), app_slug=world.app.slug, start=start, end=end
        )
    assert not world.calls


def test_named_deleted_environment_does_not_fall_back_to_another(world):
    grant(world)
    world.env.soft_delete()
    with pytest.raises(ValueError, match="environment not found"):
        read(world, environment_name="production")
    assert not world.calls


def test_graphql_contract_executes_the_scoped_query(world):
    from config.schema import schema

    grant(world)
    with caller(world):
        result = schema.execute_sync(
            "query($app: String!, $start: DateTime!, $end: DateTime!) { astroliftTopologyTraffic(appSlug: $app, start: $start, end: $end) { status source edgeLimit sampleLimit environments { namespace status edges { requestRate errorRate errorRatio sourceWorkloadName destinationWorkloadName samples { timestamp requestRate } } } } }",
            variable_values={"app": world.app.slug, "start": START.isoformat(), "end": NOW.isoformat()},
            context_value=make_info(world.user).context,
        )
    assert not result.errors
    assert result.data["astroliftTopologyTraffic"]["status"] == "AVAILABLE"
    assert result.data["astroliftTopologyTraffic"]["environments"][0]["edges"][0]["errorRate"] == 4.5


@pytest.mark.parametrize("limit", ["series", "samples"])
def test_oversized_matrix_reports_unavailable(world, limit):
    grant(world)
    row = world.payload["data"]["result"][0]
    if limit == "series":
        world.payload["data"]["result"] = [row] * 2049
    else:
        row["values"] = [[START.timestamp() + i, "1"] for i in range(121)]
    assert read(world).status == Status.UNAVAILABLE


def test_edge_cap_is_explicit_and_deterministic(world):
    grant(world)
    world.api.soft_delete()
    world.worker.soft_delete()
    names = [f"service-{i:02d}" for i in range(17)]
    for name in names:
        Workload.objects.create(registered_app=world.app, name=name, slug=name)
    rows = []
    for source in names:
        for destination in names:
            row = matrix(world, source=source, destination=destination)["data"]["result"][0]
            row["values"] = [[START.timestamp(), "1"]]
            rows.append(row)
    world.payload["data"]["result"] = rows
    result = read(world)
    assert result.status == Status.PARTIAL
    assert result.truncated
    assert result.edge_limit == 256
    assert len(result.environments[0].edges) == 256
    assert result.environments[0].truncated
    assert all(len(edge.samples) == 1 for edge in result.environments[0].edges)


def test_environment_count_limit_fails_before_http(world):
    grant(world)
    for i in range(8):
        AppEnvironment.objects.create(
            registered_app=world.app, tenant_cluster=world.cluster, name=f"env-{i}", k8s_namespace=f"env-{i}"
        )
    with pytest.raises(ValueError, match="select one environment"):
        read(world)
    assert not world.calls


def test_deleted_workload_and_sibling_namespace_series_never_surface(world):
    grant(world)
    world.api.soft_delete()
    result = read(world)
    assert result.status == Status.NO_DATA
    assert not result.environments[0].edges
    prom.clear_cache_for_tests()
    world.payload = matrix(world, namespace="somebody-elses-namespace", source="worker", destination="worker")
    assert read(world).status == Status.NO_DATA


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionResetError("reset")])
def test_transport_failures_report_unavailable(world, error):
    grant(world)
    world.payload = error
    assert read(world).status == Status.UNAVAILABLE
    assert len(world.calls) == 1


@pytest.mark.parametrize("code", [400, 503])
def test_http_error_body_read_is_bounded(world, code):
    grant(world)
    reads = []

    class ErrorBody(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    world.payload = HTTPError(
        "http://prom.invalid", code, "failed", {}, ErrorBody(b"x" * (MAX_BODY_BYTES + 1))
    )
    result = read(world)
    assert result.status == Status.UNAVAILABLE
    assert reads == [200]
    assert result.environments[0].reason == "traffic backend failed or returned invalid or oversized data"


def test_read_apps_bearer_can_read_its_authorized_home_app(world):
    grant(world, "ORG")
    token = SimpleNamespace(
        organization_id=world.org.pk, user_id=world.user.pk, team_id=world.medops.pk, scopes=["read:apps"]
    )
    with caller(world, token):
        result = OperationsQuery().astrolift_topology_traffic(
            make_info(world.user), app_slug=world.app.slug, start=START, end=NOW
        )
    assert result.status == Status.AVAILABLE


def test_persisted_selector_injection_never_reaches_http(world):
    grant(world)
    world.env.k8s_namespace = 'bad"namespace'
    world.env.save()
    assert read(world).status == Status.UNAVAILABLE
    assert not world.calls


def test_team_grant_cannot_read_sibling_app_before_cache(world, monkeypatch):
    grant(world, "TEAM")
    monkeypatch.setattr(prom._cache, "get", lambda *args: pytest.fail("sibling cache accessed"))
    with caller(world), pytest.raises(PermissionDenied):
        OperationsQuery().astrolift_topology_traffic(
            make_info(world.user), app_slug=world.platform_app.slug, start=START, end=NOW
        )
    assert not world.calls


def test_foreign_app_scope_cannot_grant_local_traffic(world):
    foreign = ScopeWorld("foreign-app-traffic")
    bind_role(
        world.user,
        permissions=[Permission.APP_READ_METRICS],
        kind="APP",
        scope_id=foreign.medops_app.pk,
        slug="foreign-app-metrics",
    )
    with pytest.raises(PermissionDenied):
        read(world)
    assert not world.calls


def test_cached_request_cannot_survive_new_environment_deny(world, monkeypatch):
    grant(world, "ORG")
    assert read(world).status == Status.AVAILABLE
    deny_production(world)
    monkeypatch.setattr(prom._cache, "get", lambda *args: pytest.fail("policy-denied cache accessed"))
    with pytest.raises(PermissionDenied):
        read(world)
    assert len(world.calls) == 1


def test_standing_workload_limit_rejects_before_http(world):
    grant(world)
    for i in range(63):
        Workload.objects.create(registered_app=world.app, name=f"extra-{i}", slug=f"extra-{i}")
    with pytest.raises(ValueError, match="64 standing workloads"):
        read(world)
    assert not world.calls


def test_duplicate_controller_names_are_not_mapped_to_an_arbitrary_workload_id(world):
    grant(world)
    Workload.objects.create(registered_app=world.app, name="api", slug="other-api")
    result = read(world)
    assert result.status == Status.NO_DATA
    assert not result.environments[0].edges
