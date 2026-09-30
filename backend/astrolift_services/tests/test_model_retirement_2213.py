"""Actual retirement entries refuse unconfirmed subscriptions under consumer row locks."""

import pytest
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_identity.schema.mutations import IdentityMutation
from astrolift_identity.schema.mutations.types import SoftDeleteByGuidInput
from astrolift_lifecycle.schema.mutations import DeregisterAppInput, LifecycleMutation
from astrolift_registry.schema.mutations import RegistryMutation
from astrolift_registry.schema.mutations.types import SoftDeleteAppInput, TearDownAppInput
from astrolift_services.model_retirement import (
    MODEL_CLEANUP_REQUIRED,
    REVOCATION_REQUIRED,
    ModelSubscriptionRetirementBlocked,
)
from astrolift_services.models import ManagedServiceAttachment
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_cluster_model_mutations_2213 import (
    allowed,
    subscription,
)
from astrolift_services.tests.test_cluster_model_mutations_2213 import (
    queue as mutation_queue,
)
from astrolift_services.tests.test_cluster_model_mutations_2213 import (
    world as mutation_world,
)
from astrolift_services.tests.test_cluster_model_queries_2213 import grant
from astrolift_workflows.activities.app_teardown import (
    _delete_app_namespaces_sync,
    _mark_tearing_down_sync,
    _soft_delete_app_records_sync,
)
from core.permissions import Permission
from core.tests.utils.scope_world import make_info

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    return mutation_world.__wrapped__(monkeypatch)


@pytest.fixture
def queue(monkeypatch):
    return mutation_queue.__wrapped__(monkeypatch)


def consumer(world, *, status="active"):
    return ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        desired_enabled=status != "revoked",
        subscription_status=status,
    )


@pytest.mark.parametrize("operation", ["soft_delete", "tear_down", "deregister", "project", "team"])
@pytest.mark.parametrize("status", ["pending", "active", "revoking", "failed", "revoked"])
def test_retirement_refuses_until_revocation_is_confirmed(world, monkeypatch, operation, status):
    row = consumer(world, status=status)
    if status == "revoked":
        # A label alone without an applied revision and observation is not confirmation.
        row.desired_revision = 1
        row.applied_revision = 0
        row.save()
    calls = []
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.mutations.deregister.start_workflow",
        lambda *a, **kw: calls.append((a, kw)),
    )
    grant(
        world,
        {"project": Permission.PROJECT_DELETE, "team": Permission.TEAM_DELETE}.get(
            operation, Permission.APP_DELETE
        ),
    )
    if operation == "soft_delete":
        fn, input = (
            RegistryMutation().soft_delete_app,
            SoftDeleteAppInput(id=GUID(str(world.medops_app.guid))),
        )
    elif operation == "tear_down":
        fn, input = RegistryMutation().tear_down_app, TearDownAppInput(id=GUID(str(world.medops_app.guid)))
    elif operation == "deregister":
        fn, input = (
            LifecycleMutation().deregister_astrolift_app,
            DeregisterAppInput(app_slug=world.medops_app.slug, confirm_name=world.medops_app.name),
        )
    elif operation == "project":
        fn, input = (
            IdentityMutation().soft_delete_project,
            SoftDeleteByGuidInput(id=GUID(str(world.medops_project.guid))),
        )
    else:
        fn, input = (
            IdentityMutation().soft_delete_team,
            SoftDeleteByGuidInput(id=GUID(str(world.medops.guid))),
        )
    with subject(world):
        result = fn(make_info(world.user), input=input)
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    assert result.errors[0].message == REVOCATION_REQUIRED and calls == []
    for obj in [world.medops_app, world.env, world.medops_project, world.medops]:
        obj.refresh_from_db()
        assert obj.deleted_at is None


def test_org_retirement_requires_shared_model_cleanup_even_without_consumers(world):
    grant(world, Permission.ORG_DELETE)
    assert not ManagedServiceAttachment.objects.exists()
    with subject(world):
        result = IdentityMutation().soft_delete_organization(
            make_info(world.user), input=SoftDeleteByGuidInput(id=GUID(str(world.org.guid)))
        )
    assert not result.ok and result.errors[0].message == MODEL_CLEANUP_REQUIRED
    world.org.refresh_from_db()
    assert world.org.deleted_at is None
    world.model.refresh_from_db()
    assert world.model.registered_app_id is None and world.model.project_id is None


@pytest.mark.parametrize(
    "worker", [_mark_tearing_down_sync, _delete_app_namespaces_sync, _soft_delete_app_records_sync]
)
def test_direct_worker_teardown_refuses_before_transition_or_driver_side_effect(world, worker):
    consumer(world)
    with pytest.raises(ModelSubscriptionRetirementBlocked, match="Revoke every"):
        worker(world.medops_app.pk)
    world.medops_app.refresh_from_db()
    world.env.refresh_from_db()
    assert world.medops_app.provisioning_status != "tearing_down"
    assert world.medops_app.deleted_at is None and world.env.deleted_at is None


def test_confirmed_revocation_allows_retirement_and_closes_new_admission(world, queue):  # noqa: F811
    row = consumer(world, status="revoked")
    row.desired_revision = row.applied_revision = 2
    row.reconciled_at = timezone.now()
    row.save()
    assert _mark_tearing_down_sync(world.medops_app.pk) is False
    allowed(world)
    from astrolift_services.schema.cluster_model_mutations import ClusterModelMutations

    with subject(world):
        refused = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world, alias="other")
        )
    assert not refused.ok and queue == []
    _soft_delete_app_records_sync(world.medops_app.pk)
    world.medops_app.refresh_from_db()
    world.env.refresh_from_db()
    assert world.medops_app.deleted_at is not None and world.env.deleted_at is not None


@pytest.mark.django_db(transaction=True)
def test_actual_concurrent_subscription_and_retirement_cannot_both_commit(world, queue):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    from astrolift_services.schema.cluster_model_mutations import ClusterModelMutations

    allowed(world)
    grant(world, Permission.APP_DELETE)
    barrier = Barrier(2)

    def operation(kind):
        close_old_connections()
        try:
            with subject(world):
                barrier.wait(timeout=10)
                if kind == "subscribe":
                    return ClusterModelMutations().subscribe_cluster_model(
                        make_info(world.user), input=subscription(world)
                    )
                return RegistryMutation().soft_delete_app(
                    make_info(world.user), input=SoftDeleteAppInput(id=GUID(str(world.medops_app.guid)))
                )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(operation, ["subscribe", "retire"]))
    assert sum(result.ok for result in results) == 1
    world.medops_app.refresh_from_db()
    if ManagedServiceAttachment.objects.exists():
        assert world.medops_app.deleted_at is None and len(queue) == 1
    else:
        assert world.medops_app.deleted_at is not None and queue == []


@pytest.mark.django_db(transaction=True)
def test_concurrent_org_retirement_and_first_shared_model_creation_serialize(world, queue, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    from django.db import close_old_connections

    from astrolift_clusters.models import ProviderPlugin
    from astrolift_services.hf_catalogue import CatalogueState, ModelGating
    from astrolift_services.models import ManagedService
    from astrolift_services.schema.cluster_model_mutations import ClusterModelMutations
    from astrolift_services.tests.test_cluster_model_queries_2213 import request

    world.model.soft_delete()
    grant(world, Permission.ORG_DELETE)
    grant(world, Permission.CLUSTER_UPDATE)
    actual = ProviderPlugin.objects.filter(slug="k8s_native").first()
    if actual is not None:
        world.cluster.provider_plugin = actual
    else:
        world.cluster.provider_plugin.slug = "k8s_native"
        world.cluster.provider_plugin.save()
    world.cluster.save()
    monkeypatch.setattr(
        "astrolift_services.hf_catalogue.model_detail",
        lambda repo, revision: SimpleNamespace(
            state=CatalogueState.AVAILABLE,
            model=SimpleNamespace(repo_id=repo, revision_sha=revision, gated=ModelGating.NONE),
        ),
    )
    barrier = Barrier(2)

    def operation(kind):
        close_old_connections()
        try:
            with subject(world):
                barrier.wait(timeout=10)
                if kind == "create":
                    return ClusterModelMutations().provision_cluster_model(
                        make_info(world.user), input=request(world, name="first")
                    )
                return IdentityMutation().soft_delete_organization(
                    make_info(world.user), input=SoftDeleteByGuidInput(id=GUID(str(world.org.guid)))
                )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(operation, ["create", "retire"]))
    assert sum(result.ok for result in results) == 1
    world.org.refresh_from_db()
    if ManagedService.objects.filter(organization=world.org).exists():
        assert world.org.deleted_at is None and len(queue) == 1
    else:
        assert world.org.deleted_at is not None and queue == []


@pytest.mark.parametrize("kind", ["app", "project", "team", "organization"])
def test_retirement_without_unrevoked_shared_resources_keeps_existing_behavior(world, kind):
    world.model.soft_delete()  # Remove the fixture's shared model; no runtime call is made.
    target = {
        "app": world.medops_app,
        "project": world.medops_project,
        "team": world.medops,
        "organization": world.org,
    }[kind]
    grant(
        world,
        {
            "app": Permission.APP_DELETE,
            "project": Permission.PROJECT_DELETE,
            "team": Permission.TEAM_DELETE,
            "organization": Permission.ORG_DELETE,
        }[kind],
    )
    call = {
        "app": RegistryMutation().soft_delete_app,
        "project": IdentityMutation().soft_delete_project,
        "team": IdentityMutation().soft_delete_team,
        "organization": IdentityMutation().soft_delete_organization,
    }[kind]
    input = (
        SoftDeleteAppInput(id=GUID(str(target.guid)))
        if kind == "app"
        else SoftDeleteByGuidInput(id=GUID(str(target.guid)))
    )
    with subject(world):
        result = call(make_info(world.user), input=input)
    assert result.ok
    target.refresh_from_db()
    assert target.deleted_at is not None
