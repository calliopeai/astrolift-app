"""Real grants, tenant/provider snapshots and paged shared-model metadata."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.schema.cluster_models import (
    ClusterModelsFilterInput,
    ClusterModelsQuery,
    ProvisionClusterModelInput,
)
from astrolift_services.tests.model_hosting_helpers import promote_host_operator
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_cluster_model_foundation_2213 import world as foundation_world
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import bind_role, make_info

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    return foundation_world.__wrapped__(monkeypatch)


def grant(w, permission, kind="ORG", target=None):
    return bind_role(
        w.user,
        permissions=[permission],
        kind=kind,
        scope_id=target or w.org.pk,
        slug=f"query-{permission}-{kind}",
    )


def runtime(w):
    w.cluster.provider_config = {
        "vllm_shared_runtimes": {
            "cpu": {
                "version": "0.15.1",
                "image": "vllm/vllm-openai-cpu:v0.15.1-x86_64",
                "hardware_certified": True,
                "architecture": "amd64",
                "node_selector": {"astrolift.io/cpu-runtime": "vllm"},
            }
        }
    }
    w.cluster.save()


def request(w, **changes):
    values = {
        "organization_id": GUID(str(w.org.guid)),
        "cluster_id": GUID(str(w.cluster.guid)),
        "expected_provider_id": GUID(str(w.cluster.provider_plugin.guid)),
        "name": "chat",
        "model_repo": "org/model",
        "revision_sha": "a" * 40,
        "compute_mode": "cpu",
        "cpu_request": "4",
        "memory_request": "16Gi",
        "gpu_count": 0,
        "allow_subscriptions": True,
        "cpu_kv_cache_gi_b": 4,
    }
    return ProvisionClusterModelInput(**(values | changes))


def test_runtime_review_requires_actual_owner_permission_and_current_provider(world):
    runtime(world)
    grant(world, Permission.ORG_READ)
    query = ClusterModelsQuery()
    with subject(world), pytest.raises(PermissionDenied):
        query.cluster_model_runtime_admission(make_info(world.user), input=request(world))
    grant(world, Permission.CLUSTER_UPDATE)
    grant(world, Permission.ORG_UPDATE)
    promote_host_operator(world)
    with subject(world):
        assert query.cluster_model_runtime_admission(make_info(world.user), input=request(world)).eligible
        assert not query.cluster_model_runtime_admission(
            make_info(world.user), input=request(world, expected_provider_id=GUID(str(uuid4())))
        ).eligible
        assert not query.cluster_model_runtime_admission(
            make_info(world.user), input=request(world, organization_id=GUID(str(world.other_org.guid)))
        ).eligible
    type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
    with subject(world), pytest.raises(PermissionDenied):
        query.cluster_model_runtime_admission(make_info(world.user), input=request(world))


@pytest.mark.parametrize("scopes", [["org.read"], ["cluster.update"]])
def test_runtime_review_respects_bearer_ceiling(world, scopes):
    runtime(world)
    grant(world, Permission.CLUSTER_UPDATE)
    with subject(world, scopes=scopes, token_team=world.medops.pk), pytest.raises(PermissionDenied):
        ClusterModelsQuery().cluster_model_runtime_admission(make_info(world.user), input=request(world))


def test_inventory_filters_before_count_and_keeps_unavailable_rows_inspectable(world):
    grant(world, Permission.ORG_READ)
    world.model.created_by = world.user
    world.model.config = {"model": "org/model", "compute_mode": "cpu", "allow_subscriptions": True}
    world.model.save()
    ManagedService.objects.create(
        organization=world.org,
        tenant_cluster=world.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="other",
    )
    query = ClusterModelsQuery()
    with subject(world):
        page = query.cluster_model_deployments_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            page_size=1,
            filter=ClusterModelsFilterInput(deployed_by_me=True, compute_mode="cpu"),
        )
        assert page.total_count == 1 and page.items[0].id == str(world.model.guid)
        assert page.items[0].ready is False and page.items[0].readiness_observed_at is None
        assert (
            query.cluster_model_deployments_page(
                make_info(world.user), organization_id=GUID(str(world.other_org.guid))
            ).total_count
            == 0
        )
        world.cluster.is_active = False
        world.cluster.save()
        row = query.cluster_model_deployment(
            make_info(world.user), organization_id=GUID(str(world.org.guid)), id=GUID(str(world.model.guid))
        )
        assert row is not None and row.reason == "Model placement is unavailable."
        assert (
            query.cluster_model_placement_clusters_page(
                make_info(world.user), organization_id=GUID(str(world.org.guid))
            ).total_count
            == 0
        )


def test_active_without_an_observed_rollout_never_claims_ready(world):
    grant(world, Permission.ORG_READ)
    world.model.status = "active"
    world.model.applied_config = {"replicas": 1}
    from astrolift_services.model_admission import canonical_model_handle

    world.model.backend_ref = canonical_model_handle(world.model)
    world.model.save()
    query = ClusterModelsQuery()
    with subject(world):
        assert (
            query.cluster_model_deployment(
                make_info(world.user),
                organization_id=GUID(str(world.org.guid)),
                id=GUID(str(world.model.guid)),
            ).ready
            is False
        )
        assert (
            query.cluster_model_deployments_page(
                make_info(world.user),
                organization_id=GUID(str(world.org.guid)),
                filter=ClusterModelsFilterInput(ready=True),
            ).total_count
            == 0
        )
    world.model.model_ready_observed_at = timezone.now()
    world.model.model_ready_auth_revision = 0
    world.model.model_ready_generation = 4
    world.model.model_ready_provider_guid = world.cluster.provider_plugin.guid
    world.model.model_ready_backend_ref = world.model.backend_ref
    world.model.save()
    with subject(world):
        row = query.cluster_model_deployment(
            make_info(world.user), organization_id=GUID(str(world.org.guid)), id=GUID(str(world.model.guid))
        )
        assert row.ready is True and row.readiness_generation == 4


def test_subscription_read_and_revoke_authority_are_independent_and_search_is_server_side(world):
    grant(world, Permission.ORG_READ)
    grant(world, Permission.APP_READ, "ORG")
    grant(world, Permission.APP_UPDATE, "APP", world.medops_app.pk)
    sibling = AppEnvironment.objects.create(
        registered_app=world.platform_app, tenant_cluster=world.cluster, name="production"
    )
    for env, alias in [(world.env, "chat"), (sibling, "code")]:
        ManagedServiceAttachment.objects.create(
            managed_service=world.model,
            app_environment=env,
            model_subscription=True,
            binding_alias=alias,
            subscription_status="pending",
        )
    query = ClusterModelsQuery()
    with subject(world):
        page = query.cluster_model_subscriptions_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            model_deployment_id=GUID(str(world.model.guid)),
        )
        assert {row.alias: row.can_revoke for row in page.items} == {"chat": True, "code": False}
        filtered = query.cluster_model_subscriptions_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            model_deployment_id=GUID(str(world.model.guid)),
            search="code",
            page_size=1,
        )
        assert filtered.total_count == 1 and filtered.items[0].alias == "code"
        targets = query.cluster_model_subscription_targets_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            model_deployment_id=GUID(str(world.model.guid)),
        )
        assert targets.total_count == 1 and targets.items[0].environment_id == str(world.env.guid)


def test_deployment_projection_has_no_per_row_queries(world):
    grant(world, Permission.ORG_READ)
    query = ClusterModelsQuery()
    with subject(world), CaptureQueriesContext(connection) as small:
        query.cluster_model_deployments_page(make_info(world.user), organization_id=GUID(str(world.org.guid)))
    ManagedService.objects.bulk_create(
        [
            ManagedService(
                organization=world.org,
                tenant_cluster=world.cluster,
                kind="model_endpoint",
                variant="vllm",
                name=f"model-{i}",
            )
            for i in range(20)
        ]
    )
    with subject(world), CaptureQueriesContext(connection) as large:
        page = query.cluster_model_deployments_page(
            make_info(world.user), organization_id=GUID(str(world.org.guid)), page_size=50
        )
    assert page.total_count == 21
    assert len(small) == len(large)
    assert len(large) <= 15


@pytest.mark.parametrize(
    "change",
    [
        "zero_generation",
        "changed_handle",
        "string_replicas",
        "bool_replicas",
        "fractional_replicas",
        "zero_replicas",
        "non_object_config",
    ],
)
def test_ready_filter_matches_confirmed_projection_before_pagination(world, change):
    from astrolift_services.model_admission import canonical_model_handle, with_canonical_model_handle

    grant(world, Permission.ORG_READ)
    model = world.model
    model.status = "active"
    model.applied_config = {"replicas": 1}
    model.backend_ref = canonical_model_handle(model)
    model.model_ready_backend_ref = model.backend_ref
    model.model_ready_observed_at = timezone.now()
    model.model_ready_auth_revision = model.subscription_revision
    model.model_ready_provider_guid = world.cluster.provider_plugin.guid
    model.model_ready_generation = 3
    model.save()
    assert with_canonical_model_handle(ManagedService.objects.filter(pk=model.pk)).values_list(
        "_canonical_model_handle", flat=True
    ).get() == canonical_model_handle(model)
    with subject(world):
        query = ClusterModelsQuery()
        ready = query.cluster_model_deployments_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            page_size=1,
            filter=ClusterModelsFilterInput(ready=True),
        )
        assert ready.total_count == 1 and ready.items[0].ready is True
    if change == "zero_generation":
        model.model_ready_generation = 0
    elif change == "changed_handle":
        model.backend_ref = model.model_ready_backend_ref = "model_endpoint/foreign/namespace/foreign"
    elif change == "non_object_config":
        model.applied_config = []
    else:
        model.applied_config = {
            "replicas": {
                "string_replicas": "1",
                "bool_replicas": True,
                "fractional_replicas": 1.5,
                "zero_replicas": 0,
            }[change]
        }
    model.save()
    with subject(world):
        ready = query.cluster_model_deployments_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            page_size=1,
            filter=ClusterModelsFilterInput(ready=True),
        )
        unready = query.cluster_model_deployments_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            page_size=1,
            filter=ClusterModelsFilterInput(ready=False),
        )
    assert ready.total_count == 0 and ready.items == []
    assert unready.total_count == 1 and unready.items[0].ready is False
