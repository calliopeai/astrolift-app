"""Settings retain immutable sources and refuse access-policy substitutions."""

import pytest

from astrolift_graphql import GUID
from astrolift_services.model_settings import ModelSharingMode, UpdateClusterModelInput
from astrolift_services.models import HuggingFaceConnection, ManagedServiceAttachment
from astrolift_services.schema.cluster_model_mutations import ClusterModelMutations
from astrolift_services.tests.model_hosting_helpers import promote_host_operator
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_model_hosting_sources import (
    local_request,
)
from astrolift_services.tests.test_model_hosting_sources import (
    queue as queue_fixture,
)
from astrolift_services.tests.test_model_hosting_sources import (
    world as world_fixture,
)
from core.tests.utils.scope_world import make_info

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    return world_fixture.__wrapped__(monkeypatch)


@pytest.fixture
def queue(monkeypatch):
    return queue_fixture.__wrapped__(monkeypatch)


def update(w, **changes):
    return UpdateClusterModelInput(
        **(
            {
                "organization_id": GUID(str(w.org.guid)),
                "id": GUID(str(w.model.guid)),
                "expected_cluster_id": GUID(str(w.cluster.guid)),
                "expected_provider_id": GUID(str(w.cluster.provider_plugin.guid)),
                "if_match_version": w.model.version,
                "allow_subscriptions": True,
                "cpu_request": "2",
                "memory_request": "8Gi",
                "gpu_count": 0,
                "cpu_kv_cache_gi_b": 2,
            }
            | changes
        )
    )


def active(w, *, local=False, hosting=True):
    from astrolift_services.model_admission import request_config
    from astrolift_services.tests.test_cluster_model_queries_2213 import request

    if hosting:
        promote_host_operator(w)
    with subject(w):
        w.model.config = request_config(local_request(w) if local else request(w))
    w.model.status = "active"
    w.model.save()


def test_update_preserves_verified_local_source_and_changes_name(world, queue):
    active(world, local=True)
    before = dict(world.model.config)
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(
            make_info(world.user), input=update(world, name="Renamed local model")
        )
    assert result.ok, result.errors
    world.model.refresh_from_db()
    assert world.model.name == "Renamed local model"
    for key in (
        "model_source",
        "model",
        "model_artifact_id",
        "model_artifact_version",
        "model_artifact_manifest_sha256",
    ):
        assert world.model.config[key] == before[key]
    assert world.model.config["cpu"] == "2" and "model_revision" not in world.model.config
    assert not world.hf and len(queue) == 1


def test_update_preserves_service_owned_private_hf_credential(world, queue):
    active(world)
    from django.utils import timezone

    connection = HuggingFaceConnection.objects.create(
        organization=world.org,
        name="Private hub",
        account_username="test",
        verified_at=timezone.now(),
        secret_backend_kind="encrypted",
        secret_ciphertext=b"encrypted-fixture",
    )
    world.model.model_hf_connection = connection
    world.model.model_hf_connection_version = connection.version
    ref = f"services/{world.org.guid}/{world.model.guid}/huggingface#token"
    world.model.config["hf_token_secret_ref"] = ref
    world.model.save()
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(make_info(world.user), input=update(world))
    assert result.ok, result.errors
    world.model.refresh_from_db()
    assert world.model.config["hf_token_secret_ref"] == ref
    assert world.model.model_hf_connection_id == connection.pk and not world.hf and len(queue) == 1


@pytest.mark.parametrize("change", ["foreign", "stale", "deleted"])
def test_dedicated_app_is_current_and_org_owned(world, queue, change):
    active(world)
    app = world.medops_app
    if change == "foreign":
        app.organization = world.other_org
        app.save()
    elif change == "deleted":
        app.soft_delete()
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(
            make_info(world.user),
            input=update(
                world,
                sharing_mode=ModelSharingMode.DEDICATED,
                dedicated_app_id=GUID(str(app.guid)),
                if_match_dedicated_app_version=app.version - (change == "stale"),
            ),
        )
    assert not result.ok and not queue
    world.model.refresh_from_db()
    assert "dedicated_app_id" not in world.model.config


def test_cannot_dedicate_until_other_app_revocation_is_applied(world, queue):
    active(world)
    ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        desired_enabled=False,
        subscription_status="revoking",
    )
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(
            make_info(world.user),
            input=update(
                world,
                sharing_mode=ModelSharingMode.DEDICATED,
                dedicated_app_id=GUID(str(world.platform_app.guid)),
                if_match_dedicated_app_version=world.platform_app.version,
            ),
        )
    assert not result.ok and not queue


def test_dedicated_mode_is_persisted_without_app_owning_model(world, queue):
    active(world)
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(
            make_info(world.user),
            input=update(
                world,
                sharing_mode=ModelSharingMode.DEDICATED,
                dedicated_app_id=GUID(str(world.medops_app.guid)),
                if_match_dedicated_app_version=world.medops_app.version,
            ),
        )
    assert result.ok, result.errors
    world.model.refresh_from_db()
    assert world.model.config["sharing_mode"] == "dedicated"
    assert world.model.config["dedicated_app_id"] == str(world.medops_app.guid)
    assert world.model.registered_app_id is None and len(queue) == 1


def test_resource_update_preserves_other_reviewed_runtime_settings(world, queue):
    active(world)
    world.model.config.update(dtype="float32", max_model_len=256, max_num_seqs=1, enable_prefix_caching=True)
    world.model.save()
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(make_info(world.user), input=update(world))
    assert result.ok, result.errors
    world.model.refresh_from_db()
    assert {
        key: world.model.config[key]
        for key in ("dtype", "max_model_len", "max_num_seqs", "enable_prefix_caching")
    } == {
        "dtype": "float32",
        "max_model_len": 256,
        "max_num_seqs": 1,
        "enable_prefix_caching": True,
    }


@pytest.mark.parametrize("change", ["version", "manifest", "deleted"])
def test_update_refuses_changed_local_source_without_mutation(world, queue, change):
    active(world, local=True)
    before = dict(world.model.config)
    if change == "manifest":
        world.artifact.manifest_sha256 = "f" * 64
        world.artifact.save()
    elif change == "deleted":
        world.artifact.soft_delete()
    else:
        world.artifact.save()
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(make_info(world.user), input=update(world))
    assert not result.ok and not queue
    world.model.refresh_from_db()
    assert world.model.config == before and world.model.status == "active"


def test_dedicated_to_shared_removes_identity_and_remains_worker_valid(world, queue):
    from k8s_native.managed.model_endpoint_vllm import VLLMConfig, VLLMDriver

    active(world)
    world.model.config.update(sharing_mode="dedicated", dedicated_app_id=str(world.medops_app.guid))
    world.model.save()
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(
            make_info(world.user), input=update(world, sharing_mode=ModelSharingMode.SHARED)
        )
    assert result.ok, result.errors
    world.model.refresh_from_db()
    assert "dedicated_app_id" not in world.model.config
    assert VLLMDriver(config=VLLMConfig())._normalize(world.model.config)["sharing_mode"] == "shared"


@pytest.mark.parametrize("change", ["no_environment", "deleted_environment"])
def test_dedicated_selection_requires_a_live_environment_on_the_model_cluster(world, queue, change):
    active(world)
    if change == "no_environment":
        app = world.platform_app
    else:
        app = world.medops_app
        world.env.soft_delete()
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(
            make_info(world.user),
            input=update(
                world,
                sharing_mode=ModelSharingMode.DEDICATED,
                dedicated_app_id=GUID(str(app.guid)),
                if_match_dedicated_app_version=app.version,
            ),
        )
    assert not result.ok and not queue


@pytest.mark.parametrize("change", ["stale_revision", "missing_observation", "soft_deleted_unconfirmed"])
def test_dedication_requires_complete_other_app_revocation_confirmation(world, queue, change):
    from django.utils import timezone

    from astrolift_lifecycle.models import AppEnvironment

    active(world)
    env = AppEnvironment.objects.create(
        registered_app=world.platform_app, tenant_cluster=world.cluster, name="prod"
    )
    row = ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=env,
        model_subscription=True,
        binding_alias="chat",
        subscription_status="revoked",
        desired_enabled=False,
        desired_revision=3,
        applied_revision=2 if change == "stale_revision" else 3,
        reconciled_at=None if change != "stale_revision" else timezone.now(),
    )
    if change == "soft_deleted_unconfirmed":
        row.soft_delete()
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(
            make_info(world.user),
            input=update(
                world,
                sharing_mode=ModelSharingMode.DEDICATED,
                dedicated_app_id=GUID(str(world.medops_app.guid)),
                if_match_dedicated_app_version=world.medops_app.version,
            ),
        )
    assert not result.ok and not queue


def test_dedicated_app_metadata_and_chooser_obey_actual_app_read_permission(world):
    from astrolift_services.schema.cluster_models import ClusterModelsQuery
    from astrolift_services.schema.model_types import cluster_model_to_type
    from astrolift_services.tests.test_cluster_model_queries_2213 import grant
    from core.permissions import Permission

    active(world, hosting=False)
    world.model.config.update(sharing_mode="dedicated", dedicated_app_id=str(world.medops_app.guid))
    world.model.save()

    def read():
        return ClusterModelsQuery().cluster_model_dedicated_apps_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            cluster_id=GUID(str(world.cluster.guid)),
            expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
        )

    with subject(world):
        dto = cluster_model_to_type(world.model)
        assert dto.sharing_mode == ModelSharingMode.DEDICATED
        assert dto.dedicated_app_name is None and dto.dedicated_app_id is None
        from core.permissions import PermissionDenied

        with pytest.raises(PermissionDenied):
            read()
        grant(world, Permission.APP_READ, "APP", world.medops_app.pk)
        assert cluster_model_to_type(world.model).dedicated_app_id == str(world.medops_app.guid)
        promote_host_operator(world)
        assert read().total_count == 1


def test_update_admission_uses_stored_local_source_and_exact_version(world, queue):
    from astrolift_services.schema.cluster_models import ClusterModelsQuery

    active(world, local=True)
    before = dict(world.model.config)
    with subject(world):
        query = ClusterModelsQuery()
        assert query.cluster_model_update_admission(make_info(world.user), input=update(world)).eligible
        assert not query.cluster_model_update_admission(
            make_info(world.user), input=update(world, if_match_version=world.model.version - 1)
        ).eligible
    world.model.refresh_from_db()
    assert world.model.config == before and not world.hf and not queue


def test_bearer_membership_withdrawn_after_source_lock_refuses_before_write(world, queue, monkeypatch):
    from django.utils import timezone

    from astrolift_identity.models import Member
    from astrolift_services.schema import cluster_model_mutations as mutations

    active(world)
    before = dict(world.model.config)
    original = mutations.validate_updated_source

    def withdraw(*args, **kwargs):
        result = original(*args, **kwargs)
        Member.objects.filter(user=world.user, scope_kind="ORG", scope_id=world.org.pk).update(
            deleted_at=timezone.now()
        )
        return result

    monkeypatch.setattr(mutations, "validate_updated_source", withdraw)
    with subject(world, scopes=["admin"]):
        result = ClusterModelMutations().update_cluster_model(make_info(world.user), input=update(world))
    assert not result.ok and not queue
    world.model.refresh_from_db()
    assert world.model.config == before and world.model.status == "active"


@pytest.mark.parametrize("operation", ["admission", "chooser"])
def test_ordinary_settings_roles_remain_refused_in_both_policy_regions(world, operation):
    from astrolift_identity.models import Policy
    from astrolift_services.schema.cluster_models import ClusterModelsQuery
    from astrolift_services.tests.test_cluster_model_queries_2213 import grant
    from core.permissions import Permission, PermissionDenied

    active(world, hosting=False)
    grant(world, Permission.APP_READ, "APP", world.medops_app.pk)
    world.cluster.region = "us-west-2"
    world.cluster.save()
    Policy.objects.create(
        organization=world.org,
        name="Deny east",
        slug="deny-east-settings",
        scope_level="ORG",
        scope_id=world.org.pk,
        effect="DENY",
        action_pattern="cluster.update",
        resource_pattern={"region": ["us-east-1"]},
    )

    def read():
        query = ClusterModelsQuery()
        if operation == "admission":
            return query.cluster_model_update_admission(make_info(world.user), input=update(world))
        return query.cluster_model_dedicated_apps_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            cluster_id=GUID(str(world.cluster.guid)),
            expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
        )

    with subject(world):
        with pytest.raises(PermissionDenied):
            read()
        world.cluster.region = "us-east-1"
        world.cluster.save()
        with pytest.raises(PermissionDenied):
            read()


@pytest.mark.parametrize("owner", ["organization", "installation_shared", "foreign"])
def test_dedicated_app_selector_preserves_admitted_cluster_ownership(world, owner):
    from astrolift_services.schema.cluster_models import ClusterModelsQuery

    active(world)
    world.cluster.organization = {
        "organization": world.org,
        "installation_shared": None,
        "foreign": world.other_org,
    }[owner]
    world.cluster.save()
    with subject(world):
        result = ClusterModelsQuery().cluster_model_dedicated_apps_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            cluster_id=GUID(str(world.cluster.guid)),
            expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
        )
    assert result.total_count == (0 if owner == "foreign" else 1)
    assert [str(item.id) for item in result.items] == (
        [] if owner == "foreign" else [str(world.medops_app.guid)]
    )
