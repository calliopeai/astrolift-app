"""Real owner/bearer/policy gates and controlled Prometheus HTTP observations."""

import io
import json
from contextlib import contextmanager
from datetime import timedelta
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone
from graphql import GraphQLError

from astrolift_clusters.models import TenantCluster
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization, Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations import prometheus_client as prom
from astrolift_services.model_density import INVENTORY_LIMIT
from astrolift_services.model_observations import MAX_BODY_BYTES, MAX_SAMPLES
from astrolift_services.model_observations import ModelObservationState as State
from astrolift_services.models import ManagedService
from astrolift_services.schema.model_reads import ModelReadsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user
from providers._sdk.k8s_naming import (
    app_namespace,
    cluster_model_namespace,
    cluster_model_resource_name,
    dns_label,
)

pytestmark = pytest.mark.django_db


def matrix(rows):
    return {"status": "success", "data": {"resultType": "matrix", "result": rows}}


def series(w, key, value=2, timestamp=None, service=None):
    service = service or w.model
    return {
        "metric": {
            "managed_service": str(service.guid),
            "namespace": namespace(w, service),
            "service": resource_name(service),
            "astrolift_measurement": key,
        },
        "values": [[(timestamp or w.end).timestamp(), str(value)]],
    }


def namespace(w, row):
    if row.organization_id:
        return cluster_model_namespace(
            organization_id=str(row.organization.guid),
            cluster_id=str(w.cluster.guid),
            managed_service_id=str(row.guid),
        )
    owner = row.registered_app or row.project
    return app_namespace(organization_slug=owner.organization.slug, app_slug=owner.slug)


def resource_name(row):
    return (
        cluster_model_resource_name(str(row.guid))
        if row.organization_id
        else dns_label((row.registered_app or row.project).slug, row.effective_environment_name, row.name)
    )


def handle(w, row):
    return f"model_endpoint/{w.cluster.guid}/{namespace(w, row)}/{resource_name(row)}"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *args: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *args: None))
    w = ScopeWorld("observations2214")
    w.user = make_user("observations2214")
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    w.cluster = make_cluster(w, "observations2214")
    w.cluster.lifecycle = TenantCluster.Lifecycle.MANAGED
    w.cluster.provider_config = {"prometheus_endpoint": "http://prom.invalid"}
    w.cluster.save()
    w.model = ManagedService.objects.create(
        organization=w.org,
        tenant_cluster=w.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="observed",
        status="active",
        config={"replicas": 2, "cpu": "500m", "memory": "2Gi", "gpu": 1},
        applied_config={"replicas": 1, "cpu": "250m", "memory": "1Gi", "gpu": 0},
        operation_completed_at=timezone.now(),
    )
    w.model.backend_ref = handle(w, w.model)
    w.model.save()
    w.end = (timezone.now() - timedelta(seconds=1)).replace(microsecond=0)
    w.start = w.end - timedelta(hours=1)
    w.runtime = matrix(
        [
            series(w, "generation_tokens_per_second", 12.5),
            series(w, "requests_waiting", 0),
            series(w, "ttft_p95", 0.2),
            series(w, "kv_cache_usage", 0.1),
        ]
    )
    w.resources = matrix([series(w, "ready_replicas", 1), series(w, "cpu_usage", 0.05)])
    w.calls = []
    prom.clear_cache_for_tests()

    def open_request(request, timeout):
        params = parse_qs(
            request.data.decode() if request.data is not None else urlsplit(request.full_url).query
        )
        query = params["query"][0]
        w.calls.append((query, params, timeout))
        payload = w.runtime if "vllm:" in query else w.resources
        if isinstance(payload, Exception):
            raise payload
        return io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

    monkeypatch.setattr("astrolift_operations.prometheus_client.urllib.request.urlopen", open_request)
    return w


def grant(w, kind="ORG", permission=Permission.CLUSTER_REGISTER):
    ids = {"ORG": w.org.pk, "TEAM": w.medops.pk, "PROJECT": w.medops_project.pk, "APP": w.medops_app.pk}
    return bind_role(w.user, permissions=[permission], kind=kind, scope_id=ids[kind], slug="observations")


@contextmanager
def caller(w, token=None):
    marker = set_current_api_token(token)
    try:
        with tenant_context(
            TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk, team_id=w.medops.pk)
        ):
            yield
    finally:
        reset_current_api_token(marker)


def metrics(w, row=None, **kwargs):
    row = row or w.model
    args = {
        "service_id": str(row.guid),
        "expected_cluster_id": str(w.cluster.guid),
        "expected_provider_id": str(w.cluster.provider_plugin.guid),
        "start": w.start,
        "end": w.end,
    } | kwargs
    return ModelReadsQuery().astrolift_model_deployment_metrics(make_info(w.user), **args)


def density(w, **kwargs):
    args = {
        "cluster_id": str(w.cluster.guid),
        "expected_provider_id": str(w.cluster.provider_plugin.guid),
        "start": w.start,
        "end": w.end,
    } | kwargs
    return ModelReadsQuery().astrolift_cluster_model_density(make_info(w.user), **args)


def values(result):
    return {item.key: item for item in result.metrics}


def test_exact_guid_namespace_resource_and_pod_mapping_in_bounded_queries(world):
    grant(world)
    with caller(world):
        result = metrics(world)
    observed = values(result)
    assert observed["generation_tokens_per_second"].value == 12.5
    assert observed["generation_tokens_per_second"].unit == "tokens/s"
    assert observed["requests_waiting"].state == State.AVAILABLE and observed["requests_waiting"].value == 0
    assert observed["cpu_usage"].value == 0.05 and observed["cpu_usage"].source == "cadvisor"
    assert observed["gpu_utilization"].state == observed["vram_usage"].state == State.UNSUPPORTED
    assert observed["latency_p95"].state == State.NO_DATA and observed["latency_p95"].value is None
    assert result.scope == "deployment_aggregate_not_app_attributed"
    assert result.sample_limit == 120 and len(world.calls) == 2
    for query, params, timeout in world.calls:
        assert (
            str(world.model.guid) in query
            and namespace(world, world.model) in query
            and resource_name(world.model) in query
        )
        assert int(params["step"][0]) >= 30 and timeout == 5
    assert (
        "kube_pod_labels" in world.calls[1][0]
        and "label_astrolift_io_managed_service_id" in world.calls[1][0]
    )
    assert "label_app_kubernetes_io_instance" in world.calls[1][0]


def test_idle_histograms_are_absent_without_hiding_actual_zero_counters(world):
    grant(world)
    # Prometheus filters undefined quantiles; unlike missing rates, a sampled
    # zero remains an observed zero. Malformed HTTP NaN still fails separately.
    world.runtime = matrix([series(world, "successful_requests_per_second", 0)])
    with caller(world):
        result = metrics(world)
    observed = values(result)
    assert observed["successful_requests_per_second"].state == State.AVAILABLE
    assert observed["successful_requests_per_second"].value == 0
    assert observed["ttft_p95"].state == State.NO_DATA
    assert observed["ttft_p95"].value is None
    assert world.calls[0][0].count("histogram_quantile(0.95") == 3
    assert world.calls[0][0].count("[5m]))) >= 0") == 3


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT"])
def test_descendant_grants_cannot_read_owner_aggregate_metrics_or_density(world, kind):
    grant(world, kind)
    with caller(world), pytest.raises(GraphQLError, match="access is denied"):
        metrics(world)
    with caller(world), pytest.raises(GraphQLError, match="access is denied"):
        density(world)
    assert not world.calls


def test_app_subscription_read_metrics_grant_does_not_grant_shared_usage(world):
    grant(world, "APP", Permission.APP_READ_METRICS)
    with caller(world), pytest.raises(GraphQLError, match="access is denied"):
        metrics(world)
    assert not world.calls


@pytest.mark.parametrize("ceiling", ["team", "apps-only", "clusters-read-only", "empty", "foreign"])
def test_owner_role_still_obeys_actual_bearer_org_and_scope_ceilings(world, ceiling):
    grant(world)
    org = world.org
    if ceiling == "foreign":
        org = Organization.objects.create(name="Foreign", slug="foreign-bearer-observations")
        Member.objects.create(user=world.user, scope_kind="ORG", scope_id=org.pk)
    token = ApiToken.objects.create(
        user=world.user,
        organization=org,
        name="bound",
        token_hash=uuid4().hex,
        team=world.medops if ceiling == "team" else None,
        scopes=["read:clusters"]
        if ceiling == "clusters-read-only"
        else ["read:apps"]
        if ceiling == "apps-only"
        else []
        if ceiling == "empty"
        else ["admin"],
    )
    with caller(world, token), pytest.raises(GraphQLError):
        metrics(world)
    with caller(world, token), pytest.raises(GraphQLError):
        density(world)
    assert not world.calls


@pytest.mark.parametrize(
    "change", ["deleted-model", "deleted-org", "deleted-cluster", "deleted-provider", "foreign-cluster"]
)
def test_retired_or_foreign_ancestry_never_reaches_http(world, change):
    grant(world)
    if change == "deleted-model":
        world.model.soft_delete()
    elif change == "deleted-org":
        world.org.soft_delete()
    elif change == "deleted-cluster":
        world.cluster.soft_delete()
    elif change == "deleted-provider":
        world.cluster.provider_plugin.soft_delete()
    else:
        world.cluster.organization = Organization.objects.create(
            name="Foreign", slug="foreign-cluster-observations"
        )
        world.cluster.save()
    with caller(world):
        assert metrics(world) is None
    assert not world.calls


@pytest.mark.parametrize("change", ["inactive", "unmanaged", "disabled-provider"])
def test_unavailable_transport_keeps_inventory_without_reads(world, change):
    grant(world)
    if change == "inactive":
        world.cluster.is_active = False
    elif change == "unmanaged":
        world.cluster.lifecycle = "registered"
    else:
        world.cluster.provider_plugin.is_enabled = False
        world.cluster.provider_plugin.save()
    world.cluster.save()
    with caller(world):
        result = metrics(world)
        fleet = density(world)
    assert values(result)["cpu_usage"].state == State.UNAVAILABLE
    assert fleet.model_count == 1 and fleet.items[0].status == "active"
    assert fleet.items[0].observations[0].state in (State.UNAVAILABLE, State.UNSUPPORTED)
    assert not world.calls


@pytest.mark.parametrize("argument", ["expected_cluster_id", "expected_provider_id"])
def test_changed_immutable_target_refuses_before_http_or_cache(world, argument):
    grant(world)
    with caller(world), pytest.raises(GraphQLError, match="target changed"):
        metrics(world, **{argument: str(uuid4())})
    assert not world.calls


@pytest.mark.parametrize("change", ["namespace", "resource", "cluster", "legacy"])
def test_recorded_handle_must_match_canonical_persisted_owner(world, change):
    grant(world)
    parts = world.model.backend_ref.split("/")
    parts[{"namespace": 2, "resource": 3, "cluster": 1}.get(change, 1)] = "foreign-target"
    world.model.backend_ref = "model_endpoint/legacy" if change == "legacy" else "/".join(parts)
    world.model.save()
    with caller(world):
        assert values(metrics(world))["requests_running"].state == State.UNAVAILABLE
    assert not world.calls


@pytest.mark.parametrize("value", ["NaN", "+Inf", "-Inf", "-1"])
def test_nonfinite_negative_values_fail_group_without_fabricated_zero(world, value):
    grant(world)
    world.runtime = matrix([series(world, "requests_running", value)])
    with caller(world):
        result = values(metrics(world))
    assert result["requests_running"].state == State.UNAVAILABLE and result["requests_running"].value is None
    assert result["cpu_usage"].state == State.AVAILABLE


@pytest.mark.parametrize(
    "bad",
    [
        "foreign-guid",
        "foreign-namespace",
        "foreign-resource",
        "duplicate",
        "fraction",
        "samples",
        "matrix",
        "body",
    ],
)
def test_malformed_or_mismapped_series_refuse_bounded_readings(world, bad):
    grant(world)
    row = series(world, "kv_cache_usage", 0.5)
    if bad in {"foreign-guid", "foreign-namespace", "foreign-resource"}:
        row["metric"][
            ["managed_service", "namespace", "service"][
                ["foreign-guid", "foreign-namespace", "foreign-resource"].index(bad)
            ]
        ] = "foreign"
    elif bad == "fraction":
        row["values"][0][1] = "2"
    elif bad == "samples":
        row["values"] *= MAX_SAMPLES + 1
    world.runtime = (
        b"x" * (MAX_BODY_BYTES + 1)
        if bad == "body"
        else {"status": "success", "data": {"resultType": "vector", "result": []}}
        if bad == "matrix"
        else matrix([row, row] if bad == "duplicate" else [row])
    )
    with caller(world):
        result = values(metrics(world))
    assert result["kv_cache_usage"].state == State.UNAVAILABLE and result["kv_cache_usage"].value is None


def test_stale_and_no_data_are_distinct_from_transport_failure(world):
    grant(world)
    world.runtime = matrix([series(world, "requests_running", 0, world.end - timedelta(minutes=20))])
    world.resources = matrix([])
    with caller(world):
        observed = values(metrics(world))
    assert observed["requests_running"].state == State.STALE and observed["requests_running"].value == 0
    assert observed["cpu_usage"].state == State.NO_DATA and observed["cpu_usage"].value is None
    prom.clear_cache_for_tests()
    world.runtime = URLError("raw credential-like diagnostic")
    with caller(world):
        observed = values(metrics(world))
    assert (
        observed["requests_running"].state == State.UNAVAILABLE and observed["requests_running"].samples == []
    )


def test_unconfigured_endpoint_never_reads_or_invents_zero(world):
    grant(world)
    world.cluster.provider_config = {}
    world.cluster.save()
    with caller(world):
        observed = values(metrics(world))
    assert (
        observed["requests_running"].state == State.UNCONFIGURED
        and observed["requests_running"].value is None
    )
    assert not world.calls


def test_environment_policy_checks_actual_legacy_app_owner_before_http(world):
    grant(world, "APP", Permission.APP_READ_METRICS)
    env = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="production"
    )
    model = ManagedService.objects.create(
        registered_app=world.medops_app,
        app_environment=env,
        name="legacy",
        kind="model_endpoint",
        variant="vllm",
    )
    model.backend_ref = handle(world, model)
    model.save()
    Policy.objects.create(
        organization=world.org,
        name="No production metrics",
        effect="DENY",
        action_pattern="app.read_metrics",
        resource_pattern={"env": "production"},
        conditions=[],
    )
    with caller(world), pytest.raises(GraphQLError, match="access is denied"):
        metrics(world, model)
    assert not world.calls


def test_owner_deny_and_role_revocation_precede_cached_metrics(world):
    role = grant(world)
    with caller(world):
        metrics(world)
    assert len(world.calls) == 2
    role.soft_delete()
    with caller(world), pytest.raises(GraphQLError, match="access is denied"):
        metrics(world)
    assert len(world.calls) == 2


def test_owner_org_policy_denies_density_and_metrics_before_http(world):
    grant(world)
    Policy.objects.create(
        organization=world.org,
        name="No shared telemetry",
        effect="DENY",
        action_pattern="cluster.register",
        resource_pattern={"org": str(world.org.guid)},
        conditions=[],
    )
    with caller(world), pytest.raises(GraphQLError, match="access is denied"):
        metrics(world)
    with caller(world), pytest.raises(GraphQLError, match="access is denied"):
        density(world)
    assert not world.calls


def test_density_separates_desired_applied_observed_and_exact_tenant_inventory(world):
    grant(world)
    world.resources = matrix(
        [
            series(world, "ready_replicas", 1),
            series(world, "running_replicas", 2),
            series(world, "cpu_usage", 0.15),
            series(world, "memory_usage", 4096),
            series(world, "cpu_requests", 0.25),
            series(world, "memory_requests", 1024**3),
        ]
    )
    foreign = Organization.objects.create(name="Foreign", slug="density-foreign")
    world.cluster.organization = None
    world.cluster.save()
    ManagedService.objects.create(
        organization=foreign,
        tenant_cluster=world.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="never-visible",
    )
    ManagedService.objects.create(
        project=world.medops_project,
        tenant_cluster=world.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="legacy-excluded",
    )
    with caller(world):
        result = density(world)
    assert (
        result.scope == "organization_cluster_owned_models"
        and result.model_count == result.returned_count == 1
    )
    row = result.items[0]
    assert row.desired.replicas == 2 and row.applied.replicas == 1
    assert row.desired.total_cpu_cores == 1 and row.applied.total_cpu_cores == 0.25
    assert row.desired.total_memory_bytes == 4 * 1024**3 and row.applied.total_memory_bytes == 1024**3
    ready = next(item for item in row.observations if item.key == "ready_replicas")
    assert ready.value == 1 and ready.state == State.AVAILABLE
    observed = {item.key: item for item in row.observations}
    assert observed["running_replicas"].value == 2
    assert observed["cpu_usage"].value == 0.15
    assert observed["memory_usage"].value == 4096
    assert observed["cpu_requests"].value == 0.25 and observed["memory_requests"].value == 1024**3
    assert result.capacity.state == State.UNSUPPORTED and result.capacity.gpu_devices == []
    assert result.capacity.cpu_cores is result.capacity.memory_bytes is result.capacity.vram_bytes is None
    assert all("never-visible" not in query for query, _, _ in world.calls)


@pytest.mark.parametrize("recorded_observation", [False, True])
def test_density_failed_newer_revision_preserves_applied_observation_provenance(world, recorded_observation):
    from astrolift_workflows.activities.shared_model_reconcile import _failed_sync

    grant(world)
    observed_at = world.end - timedelta(hours=2) if recorded_observation else None
    world.model.model_ready_observed_at = observed_at
    world.model.operation_completed_at = world.end - timedelta(hours=1)
    world.model.subscription_revision = 2
    world.model.applied_subscription_revision = 1
    world.model.save()
    prior_applied = dict(world.model.applied_config)

    _failed_sync(world.model.pk, 2)
    world.model.refresh_from_db()
    assert world.model.status == "failed"
    assert world.model.applied_config == prior_applied
    assert world.model.operation_completed_at > world.end
    with caller(world):
        applied = density(world).items[0].applied
    assert applied.observed_at == observed_at
    assert applied.total_cpu_cores == 0.25 and applied.replicas == 1


def test_density_pending_newer_revision_uses_recorded_rollout_observation(world):
    grant(world)
    observed_at = world.end - timedelta(minutes=1)
    world.model.status = "updating"
    world.model.operation_completed_at = world.end - timedelta(hours=1)
    world.model.model_ready_observed_at = observed_at
    world.model.save()
    with caller(world):
        applied = density(world).items[0].applied
    assert applied.observed_at == observed_at
    assert applied.observed_at != world.model.operation_completed_at


def test_inventory_limit_never_masquerades_as_full_fleet_count(world):
    grant(world)
    ManagedService.objects.bulk_create(
        [
            ManagedService(
                organization=world.org,
                tenant_cluster=world.cluster,
                kind="model_endpoint",
                variant="vllm",
                name=f"row-{n}",
            )
            for n in range(INVENTORY_LIMIT + 4)
        ]
    )
    world.cluster.is_active = False
    world.cluster.save()
    with caller(world):
        result = density(world)
    assert result.model_count == INVENTORY_LIMIT + 5 and result.returned_count == INVENTORY_LIMIT
    assert result.truncated and len(result.items) == INVENTORY_LIMIT
    assert not world.calls


def test_missing_requests_and_replica_count_remain_unknown(world):
    grant(world)
    world.model.config = {"gpu": 0, "memory": "unsupported-unit"}
    world.model.applied_config = None
    world.model.save()
    world.resources = matrix([])
    with caller(world):
        row = density(world).items[0]
    assert row.desired.replicas is None and row.desired.cpu_cores_per_replica is None
    assert row.desired.memory_bytes_per_replica is None and row.desired.total_cpu_cores is None
    assert row.desired.gpu_devices_per_replica == 0 and row.desired.total_gpu_devices is None
    assert row.applied is None


@pytest.mark.parametrize("stale", [False, True])
def test_actual_gpu_capability_capacity_has_timestamp_not_estimated_vram(world, stale):
    grant(world)
    world.cluster.capabilities = {"gpu": {"total": {"nvidia.com/gpu": 4}, "mig_total": {}}}
    world.cluster.capabilities_probed_at = timezone.now() - timedelta(hours=2 if stale else 0)
    world.cluster.save()
    world.resources = matrix([])
    with caller(world):
        capacity = density(world).capacity
    assert capacity.state == (State.STALE if stale else State.AVAILABLE)
    assert (
        capacity.gpu_devices[0].devices == 4 and capacity.observed_at == world.cluster.capabilities_probed_at
    )
    assert capacity.vram_bytes is None and capacity.cpu_cores is None


def test_http_bearer_revocation_blocks_metrics_continuation_and_cached_read(world):
    grant(world)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="metrics-http",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )
    query = "query($id:GUID!,$cluster:GUID!,$provider:GUID!,$start:DateTime!,$end:DateTime!){astroliftModelDeploymentMetrics(serviceId:$id,expectedClusterId:$cluster,expectedProviderId:$provider,start:$start,end:$end){scope metrics{key state value source observedAt}}}"
    payload = {
        "query": query,
        "variables": {
            "id": str(world.model.guid),
            "cluster": str(world.cluster.guid),
            "provider": str(world.cluster.provider_plugin.guid),
            "start": world.start.isoformat(),
            "end": world.end.isoformat(),
        },
    }
    client = Client()
    first = client.post(
        "/app/gql/config/",
        json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {minted.plaintext}",
    )
    assert (
        first.json()["data"]["astroliftModelDeploymentMetrics"]["scope"]
        == "deployment_aggregate_not_app_attributed"
    )
    token.is_revoked = True
    token.save()
    second = client.post(
        "/app/gql/config/",
        json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {minted.plaintext}",
    )
    assert second.status_code in (401, 403) or second.json().get("errors")
    with caller(world, token), pytest.raises(GraphQLError, match="Authentication required"):
        metrics(world)
    assert len(world.calls) == 2


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT", "ORG"])
def test_legacy_app_metrics_use_actual_owner_chain_and_mapping(world, kind):
    grant(world, kind, Permission.APP_READ_METRICS)
    env = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="staging"
    )
    row = ManagedService.objects.create(
        registered_app=world.medops_app,
        app_environment=env,
        name="legacy",
        kind="model_endpoint",
        variant="vllm",
    )
    row.backend_ref = handle(world, row)
    row.save()
    world.runtime = matrix([series(world, "generation_tokens_per_second", 5, service=row)])
    world.resources = matrix([])
    with caller(world):
        observed = values(metrics(world, row))
    assert observed["generation_tokens_per_second"].value == 5
    assert observed["generation_tokens_per_second"].state == State.AVAILABLE
    assert namespace(world, row) in world.calls[0][0]


@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "ORG"])
def test_legacy_project_metrics_keep_project_owner_and_exclude_sibling(world, kind):
    grant(world, kind, Permission.APP_READ_METRICS)
    row = ManagedService.objects.create(
        project=world.medops_project,
        tenant_cluster=world.cluster,
        name="legacy-project",
        kind="model_endpoint",
        variant="vllm",
    )
    row.backend_ref = handle(world, row)
    row.save()
    world.runtime = matrix([series(world, "requests_running", 0, service=row)])
    world.resources = matrix([])
    with caller(world):
        observed = values(metrics(world, row))
    assert observed["requests_running"].value == 0 and observed["requests_running"].state == State.AVAILABLE
    if kind != "ORG":
        sibling = ManagedService.objects.create(
            project=world.platform_project,
            tenant_cluster=world.cluster,
            name="sibling",
            kind="model_endpoint",
            variant="vllm",
        )
        with caller(world), pytest.raises(GraphQLError, match="access is denied"):
            metrics(world, sibling)
    assert len(world.calls) == 2


@pytest.mark.parametrize("window", ["naive", "too-long", "reversed", "future", "too-short"])
def test_invalid_windows_fail_before_any_cache_or_http(world, window):
    grant(world)
    start, end = world.start, world.end
    if window == "naive":
        start = start.replace(tzinfo=None)
    elif window == "too-long":
        start = end - timedelta(days=2)
    elif window == "reversed":
        start, end = end, start
    elif window == "future":
        end = timezone.now() + timedelta(days=1)
    else:
        start = end - timedelta(seconds=20)
    with caller(world), pytest.raises(GraphQLError):
        metrics(world, start=start, end=end)
    with caller(world), pytest.raises(GraphQLError):
        density(world, start=start, end=end)
    assert not world.calls


def test_density_provider_precondition_and_discovery_are_not_authority(world):
    from config.schema_public import schema_public
    from core.capabilities_registry import SHIPPED_CAPABILITIES

    grant(world)
    with caller(world), pytest.raises(GraphQLError, match="target changed"):
        density(world, expected_provider_id=str(uuid4()))
    assert not world.calls
    assert {"models.deployment_observations", "models.cluster_density"} <= set(SHIPPED_CAPABILITIES)
    assert "astroliftModelDeploymentMetrics" not in schema_public.as_str()
    assert "astroliftClusterModelDensity" not in schema_public.as_str()


def test_applied_and_desired_zero_replicas_do_not_become_running_zeros(world):
    grant(world)
    world.model.config = {"replicas": 0, "cpu": "1", "memory": "1Gi", "gpu": 0}
    world.model.applied_config = dict(world.model.config)
    world.model.save()
    world.resources = matrix([])
    with caller(world):
        row = density(world).items[0]
    assert row.desired.total_cpu_cores == row.applied.total_cpu_cores == 0
    assert row.desired.total_gpu_devices == 0 and row.desired.gpu_resource is None
    ready = next(item for item in row.observations if item.key == "ready_replicas")
    assert ready.state == State.NO_DATA and ready.value is None
