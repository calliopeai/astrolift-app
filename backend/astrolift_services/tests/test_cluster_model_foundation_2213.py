"""Real owner, credential and database invariants for shared model placement."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.db import DataError, IntegrityError, transaction
from django.utils import timezone

from astrolift_clusters.models import TenantCluster
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Organization
from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.cluster_models import (
    available_model_clusters,
    cluster_model_org_scope,
    live_cluster_model_by_guid,
    live_cluster_models,
    model_binding_prefix,
)
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_user
from providers._sdk.k8s_naming import cluster_model_namespace, cluster_model_resource_name

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    w = ScopeWorld("models2213")
    w.user = make_user("models2213")
    w.cluster = make_cluster(w, "models2213")
    w.cluster.lifecycle = TenantCluster.Lifecycle.MANAGED
    w.cluster.save()
    w.other_org = Organization.objects.create(name="Other", slug="other-models2213")
    w.model = ManagedService.objects.create(
        organization=w.org, tenant_cluster=w.cluster, kind="model_endpoint", variant="vllm", name="shared"
    )
    w.env = AppEnvironment.objects.create(
        registered_app=w.medops_app, tenant_cluster=w.cluster, name="production"
    )
    return w


@contextmanager
def subject(w, *, token_team=None, token_org=None, scopes=None):
    with tenant_context(
        TenantContext(
            organization_id=w.org.pk,
            team_id=w.medops.pk,
            project_id=w.medops_project.pk,
            actor_user_id=w.user.pk,
        )
    ):
        marker = None
        if scopes is not None:
            marker = set_current_api_token(
                ApiToken.objects.create(
                    user=w.user,
                    token_hash=uuid4().hex,
                    name="models2213",
                    organization_id=token_org or w.org.pk,
                    team_id=token_team,
                    scopes=scopes,
                )
            )
        try:
            yield
        finally:
            if marker is not None:
                reset_current_api_token(marker)


def test_third_owner_is_not_an_app_or_project_alias(world):
    with subject(world):
        row = live_cluster_model_by_guid(world.model.guid, organization_id=world.org.pk)
        assert row.pk == world.model.pk
        assert row.owner_scope == "cluster"
        assert row.effective_environment_name == ""
        assert row.effective_cluster.pk == world.cluster.pk
        assert row.registered_app_id is None and row.project_id is None and row.app_environment_id is None
        assert live_cluster_model_by_guid(row.guid, organization_id=world.other_org.pk) is None
    assert live_cluster_model_by_guid(world.model.guid) is None


@pytest.mark.parametrize(
    "mutation", ["foreign_cluster", "deleted_cluster", "deleted_provider", "deleted_org", "deleted_model"]
)
def test_live_owner_lookup_refuses_incoherent_or_unavailable_placement(world, mutation):
    if mutation == "foreign_cluster":
        world.cluster.organization = world.other_org
    elif mutation == "inactive":
        world.cluster.is_active = False
    elif mutation == "unmanaged":
        world.cluster.lifecycle = TenantCluster.Lifecycle.REGISTERED
    elif mutation == "deleted_cluster":
        world.cluster.deleted_at = timezone.now()
    elif mutation == "deleted_provider":
        world.cluster.provider_plugin.soft_delete()
    elif mutation == "deleted_org":
        world.org.soft_delete()
    else:
        world.model.soft_delete()
    world.cluster.save()
    with subject(world):
        assert live_cluster_model_by_guid(world.model.guid) is None
        assert not live_cluster_models(ManagedService.all_objects.all(), world.org.pk).exists()


@pytest.mark.parametrize("mutation", ["inactive", "unmanaged", "disabled_provider"])
def test_existing_deployment_remains_visible_when_transport_is_unavailable(world, mutation):
    if mutation == "inactive":
        world.cluster.is_active = False
        world.cluster.save()
    elif mutation == "unmanaged":
        world.cluster.lifecycle = TenantCluster.Lifecycle.REGISTERED
        world.cluster.save()
    else:
        world.cluster.provider_plugin.is_enabled = False
        world.cluster.provider_plugin.save()
    with subject(world):
        assert live_cluster_model_by_guid(world.model.guid).pk == world.model.pk
        assert not available_model_clusters(TenantCluster.objects.all(), world.org.pk).exists()


def test_install_shared_cluster_still_requires_managed_active_live_placement(world):
    world.cluster.organization = None
    world.cluster.save()
    with subject(world):
        assert live_cluster_model_by_guid(world.model.guid).pk == world.model.pk
        world.cluster.is_active = False
        world.cluster.save()
        assert live_cluster_model_by_guid(world.model.guid).pk == world.model.pk
        assert not available_model_clusters(TenantCluster.objects.all(), world.org.pk).exists()


@pytest.mark.parametrize("value", [None, "", "invalid", str(uuid4())])
def test_target_miss_keeps_explicit_org_scope(world, value):
    with subject(world):
        assert live_cluster_model_by_guid(value) is None
        assert cluster_model_org_scope(Permission.CLUSTER_UPDATE)({"id": value}) == PermissionScope(
            ScopeKind.ORG, world.org.pk
        )


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
@pytest.mark.parametrize(
    "permission", [Permission.ORG_READ, Permission.CLUSTER_UPDATE, Permission.CLUSTER_REGISTER]
)
def test_permissions_use_actual_org_not_selected_team_or_subscriber_scope(world, kind, permission):
    ids = {
        "APP": world.medops_app.pk,
        "PROJECT": world.medops_project.pk,
        "TEAM": world.medops.pk,
        "ORG": world.org.pk,
    }
    bind_role(world.user, permissions=[permission], kind=kind, scope_id=ids[kind], slug="model-owner")
    with subject(world):
        scope = cluster_model_org_scope(permission)({})
        if kind == "ORG":
            check_permission(permission, scope=scope)
        else:
            with pytest.raises(PermissionDenied):
                check_permission(permission, scope=scope)


@pytest.mark.parametrize("ceiling", ["team", "foreign", "read_only", "empty"])
def test_bearer_cannot_widen_model_management_owner(world, ceiling):
    bind_role(
        world.user, permissions=[Permission.CLUSTER_UPDATE], kind="ORG", scope_id=world.org.pk, slug="manager"
    )
    kwargs = {"scopes": ["admin"]}
    if ceiling == "team":
        kwargs["token_team"] = world.medops.pk
    elif ceiling == "foreign":
        kwargs["token_org"] = world.other_org.pk
    else:
        kwargs["scopes"] = ["read:apps"] if ceiling == "read_only" else []
    with subject(world, **kwargs), pytest.raises(PermissionDenied):
        check_permission(
            Permission.CLUSTER_UPDATE, scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE)({})
        )


def test_revoked_role_cannot_manage_shared_model(world):
    grant = bind_role(
        world.user, permissions=[Permission.CLUSTER_UPDATE], kind="ORG", scope_id=world.org.pk, slug="manager"
    )
    grant.soft_delete()
    with subject(world), pytest.raises(PermissionDenied):
        check_permission(
            Permission.CLUSTER_UPDATE, scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE)({})
        )


@pytest.mark.parametrize("kind,variant", [("redis", "default"), ("model_endpoint", "other")])
def test_third_owner_database_branch_is_vllm_only(world, kind, variant):
    with transaction.atomic(), pytest.raises(IntegrityError):
        ManagedService.objects.create(
            organization=world.org, tenant_cluster=world.cluster, kind=kind, variant=variant, name="bad"
        )


def subscription(world, **changes):
    values = {
        "managed_service": world.model,
        "app_environment": world.env,
        "model_subscription": True,
        "binding_alias": "chat",
        "subscription_status": "pending",
        "desired_revision": 1,
    }
    values.update(changes)
    return ManagedServiceAttachment.objects.create(**values)


@pytest.mark.parametrize("alias", ["", "Chat", "a-b", "a.b", "1chat", "a" * 33, "x\n", "a' OR true --"])
def test_named_alias_database_and_binding_validation(world, alias):
    with pytest.raises(ValueError):
        model_binding_prefix(alias)
    with transaction.atomic(), pytest.raises(DataError if len(alias) > 32 else IntegrityError):
        subscription(world, binding_alias=alias)


def test_multiple_named_subscriptions_and_legacy_attachment_preserved(world):
    first = subscription(world)
    second = subscription(world, binding_alias="embedding")
    assert first.subscription_status == second.subscription_status == "pending"
    assert model_binding_prefix("chat") == "MODEL_CHAT_"
    assert model_binding_prefix("embedding") == "MODEL_EMBEDDING_"
    legacy = ManagedServiceAttachment.objects.create(managed_service=world.model, app_environment=world.env)
    assert legacy.subscription_status == "active" and not legacy.model_subscription
    with transaction.atomic(), pytest.raises(IntegrityError):
        subscription(world)


def test_alias_cannot_be_reused_while_revocation_is_pending(world):
    row = subscription(world, desired_enabled=False, subscription_status="revoking", desired_revision=2)
    with transaction.atomic(), pytest.raises(IntegrityError):
        subscription(world)
    row.subscription_status = "revoked"
    row.applied_revision = 2
    row.soft_delete()
    replacement = subscription(world)
    assert replacement.guid != row.guid
    assert replacement.credential_ref == "" and replacement.applied_revision == 0


def test_applied_revision_cannot_outrun_requested_change(world):
    with transaction.atomic(), pytest.raises(IntegrityError):
        subscription(world, applied_revision=2)


def test_collection_owner_resolution_is_batched(world, django_assert_num_queries):
    for index in range(12):
        ManagedService.objects.create(
            organization=world.org,
            tenant_cluster=world.cluster,
            kind="model_endpoint",
            variant="vllm",
            name=f"model-{index}",
        )
    with subject(world), django_assert_num_queries(1):
        rows = list(
            live_cluster_models(ManagedService.objects.all(), world.org.pk).select_related(
                "organization", "tenant_cluster"
            )
        )
        assert len(rows) == 13
        for row in rows:
            assert row.organization.guid == world.org.guid
            assert row.effective_cluster.guid == world.cluster.guid


def test_service_owned_namespace_has_stable_tenant_cluster_identity(world):
    values = {
        "organization_id": str(world.org.guid),
        "cluster_id": str(world.cluster.guid),
        "managed_service_id": str(world.model.guid),
    }
    namespace = cluster_model_namespace(**values)
    assert namespace.startswith("astrolift-model-") and len(namespace) <= 63
    assert namespace == cluster_model_namespace(**values)
    resource_name = cluster_model_resource_name(str(world.model.guid))
    assert resource_name == f"vllm-{world.model.guid}"
    assert cluster_model_resource_name(str(uuid4())) != resource_name
    for field in values:
        assert cluster_model_namespace(**(values | {field: str(uuid4())})) != namespace
    with pytest.raises(ValueError):
        cluster_model_namespace(**(values | {"organization_id": ""}))


def test_lifecycle_spec_and_secret_namespace_keep_the_actual_third_owner(world):
    from astrolift_services.secret_ref_config import (
        owner_secret_namespace,
        service_organization,
        service_owner,
    )
    from astrolift_workflows.activities.managed_service_lifecycle import (
        _connection_secret_path,
        build_provision_spec,
    )

    spec = build_provision_spec(world.model, cluster=world.cluster)
    assert spec.organization_id == str(world.org.guid)
    assert spec.cluster_model.organization_id == str(world.org.guid)
    assert spec.cluster_model.managed_service_id == str(world.model.guid)
    assert spec.app_id == spec.app_slug == spec.environment_id == spec.environment_name == ""
    assert service_owner(world.model).pk == world.model.pk
    assert service_organization(world.model).pk == world.org.pk
    namespace = f"services/{world.org.guid}/{world.model.guid}/"
    assert owner_secret_namespace(service_owner(world.model)) == namespace
    assert _connection_secret_path(world.model) == namespace + "connection"


def test_worker_consumer_snapshot_is_batched_without_request_tenant_and_refuses_sibling_env(world):
    from astrolift_workflows.activities.managed_service_lifecycle import build_provision_spec

    row = subscription(world)
    row.credential_ref = f"services/{world.org.guid}/{world.model.guid}/subscriptions/{row.guid}#api_key"
    row.save()
    spec = build_provision_spec(world.model, cluster=world.cluster)
    assert spec.cluster_model.consumers[0].environment_name == "production"
    assert spec.cluster_model.consumers[0].app_slug == world.medops_app.slug
    other_cluster = make_cluster(world, "models2213-other")
    world.env.tenant_cluster = other_cluster
    world.env.save()
    with pytest.raises(ValueError, match="destination"):
        build_provision_spec(world.model, cluster=world.cluster)


def test_cluster_model_typed_secret_reference_cannot_name_app_or_sibling_credentials(world):
    from astrolift_services.secret_ref_config import assert_config_secret_refs_scoped, service_owner

    own = f"services/{world.org.guid}/{world.model.guid}/huggingface#token"
    assert_config_secret_refs_scoped(
        {"hf_token_secret_ref": own}, owner=service_owner(world.model), cluster=world.cluster
    )
    for owner_id in (world.medops_app.guid, uuid4()):
        with pytest.raises(ValueError):
            assert_config_secret_refs_scoped(
                {"hf_token_secret_ref": f"services/{world.org.guid}/{owner_id}/huggingface#token"},
                owner=service_owner(world.model),
                cluster=world.cluster,
            )
