"""Actual organization/destination grants and no-side-effect stale snapshot refusals."""

from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_identity.models import RoleBinding
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import Workload
from astrolift_services.model_admission import request_config
from astrolift_services.models import ManagedServiceAttachment
from astrolift_services.schema.cluster_model_mutations import (
    ClusterModelMutations,
    RevokeModelSubscriptionInput,
    SubscribeClusterModelInput,
)
from astrolift_services.tests.model_hosting_helpers import promote_host_operator
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_cluster_model_foundation_2213 import world as foundation_world
from astrolift_services.tests.test_cluster_model_queries_2213 import grant, request, runtime
from astrolift_workflows.client import WorkflowHandle
from core.tests.utils.scope_world import make_info

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    w = foundation_world.__wrapped__(monkeypatch)
    w.medops_app.k8s_namespace = ""
    w.medops_app.save()
    w.platform_app.k8s_namespace = ""
    w.platform_app.save()
    Workload.objects.create(registered_app=w.medops_app, name="web", slug="web", kind="deployment")
    Workload.objects.create(registered_app=w.platform_app, name="web", slug="sibling-web", kind="deployment")
    runtime(w)
    w.model.config = request_config(request(w))
    w.model.applied_config = dict(w.model.config)
    w.model.status = "active"
    from astrolift_services.model_admission import canonical_model_handle

    w.model.backend_ref = canonical_model_handle(w.model)
    w.model.model_ready_observed_at = timezone.now()
    w.model.model_ready_generation = 3
    w.model.model_ready_auth_revision = 0
    w.model.model_ready_provider_guid = w.cluster.provider_plugin.guid
    w.model.model_ready_backend_ref = w.model.backend_ref
    w.model.save()
    return w


@pytest.fixture
def queue(monkeypatch):
    calls = []

    def start(name, args, *, workflow_id):
        calls.append((name, args, workflow_id))
        return WorkflowHandle(workflow_id, "run", True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", start)
    return calls


def subscription(w, **changes):
    values = {
        "organization_id": GUID(str(w.org.guid)),
        "model_deployment_id": GUID(str(w.model.guid)),
        "expected_cluster_id": GUID(str(w.cluster.guid)),
        "expected_provider_id": GUID(str(w.cluster.provider_plugin.guid)),
        "app_environment_id": GUID(str(w.env.guid)),
        "alias": "chat",
        "if_match_version": w.model.version,
        "if_match_environment_version": w.env.version,
    }
    return SubscribeClusterModelInput(**(values | changes))


def allowed(w):
    from core.permissions import Permission

    grant(w, Permission.ORG_READ)
    grant(w, Permission.APP_UPDATE, "APP", w.medops_app.pk)


def test_self_service_uses_exact_app_destination_not_cluster_owner(world, queue):
    allowed(world)
    with subject(world):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
    assert result.ok, result.errors
    row = ManagedServiceAttachment.objects.get()
    assert row.binding_alias == "chat" and row.subscription_status == "pending" and row.applied_revision == 0
    assert (
        row.credential_ref == f"services/{world.org.guid}/{world.model.guid}/subscriptions/{row.guid}#api_key"
    )
    assert result.data.restart_required and result.data.deployment.ready is False
    world.model.refresh_from_db()
    assert world.model.status == "updating" and world.model.subscription_revision == 1
    assert world.model.model_operation_provider_guid == world.cluster.provider_plugin.guid
    assert queue[0][0] == "SharedModelReconcileWorkflow" and queue[0][1][0].revision == 1
    assert not RoleBinding.objects.filter(role__permissions__contains=["cluster.update"]).exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "sibling",
        "foreign_org",
        "foreign_provider",
        "foreign_cluster",
        "stale_model",
        "stale_environment",
        "deleted_app",
        "disabled_provider",
        "no_admission",
        "unobserved",
        "custom_namespace",
        "unsupported_workload",
        "prefix_collision",
        "invalid_alias",
    ],
)
def test_refusals_do_not_create_credentials_rows_or_queue(world, queue, mutation):
    allowed(world)
    changes = {}
    if mutation == "sibling":
        env = AppEnvironment.objects.create(
            registered_app=world.platform_app, tenant_cluster=world.cluster, name="production"
        )
        changes.update(app_environment_id=GUID(str(env.guid)), if_match_environment_version=env.version)
    elif mutation == "foreign_org":
        changes["organization_id"] = GUID(str(world.other_org.guid))
    elif mutation == "foreign_provider":
        changes["expected_provider_id"] = GUID(str(uuid4()))
    elif mutation == "foreign_cluster":
        changes["expected_cluster_id"] = GUID(str(uuid4()))
    elif mutation == "stale_model":
        changes["if_match_version"] = world.model.version - 1
    elif mutation == "stale_environment":
        changes["if_match_environment_version"] = world.env.version - 1
    elif mutation == "deleted_app":
        world.medops_app.soft_delete()
    elif mutation == "disabled_provider":
        world.cluster.provider_plugin.is_enabled = False
        world.cluster.provider_plugin.save()
    elif mutation == "no_admission":
        world.model.config["allow_subscriptions"] = False
        world.model.save()
    elif mutation == "unobserved":
        world.model.model_ready_observed_at = None
        world.model.save()
    elif mutation == "custom_namespace":
        world.env.k8s_namespace = "different"
        world.env.save()
    elif mutation == "unsupported_workload":
        Workload.objects.filter(registered_app=world.medops_app).update(kind="cronjob")
    elif mutation == "prefix_collision":
        world.medops_app.manifest_raw = '[env]\nMODEL_CHAT_API_KEY="existing"\n'
        world.medops_app.save()
    else:
        changes["alias"] = "../chat"
    world.model.refresh_from_db()
    world.env.refresh_from_db()
    with subject(world):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world, **changes)
        )
    assert not result.ok
    if mutation in ("custom_namespace", "unsupported_workload", "prefix_collision"):
        assert result.errors[0].code == "VALIDATION"
        assert "alias" not in result.errors[0].message.lower()
    assert not ManagedServiceAttachment.objects.exists() and queue == []
    world.model.refresh_from_db()
    assert world.model.subscription_revision == 0


@pytest.mark.parametrize(
    "scopes,team", [(["org.read"], None), (["app.update"], None), (["org.read", "app.update"], "team")]
)
def test_subscription_bearer_ceilings_are_not_union_authority(world, queue, scopes, team):
    allowed(world)
    with subject(world, scopes=scopes, token_team=world.medops.pk if team else None):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert queue == [] and not ManagedServiceAttachment.objects.exists()


def test_disabled_queue_rolls_back_accepted_intent(world, monkeypatch):
    allowed(world)
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda *args, **kwargs: WorkflowHandle("disabled", "", False),
    )
    with subject(world):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    assert not ManagedServiceAttachment.objects.exists()
    world.model.refresh_from_db()
    assert world.model.status == "active" and world.model.subscription_revision == 0


def test_inflight_change_and_alias_collision_cannot_overwrite_prior_credential(world, queue):
    allowed(world)
    with subject(world):
        first = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
        world.model.refresh_from_db()
        second = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world, alias="other")
        )
    assert first.ok and not second.ok and len(queue) == 1
    row = ManagedServiceAttachment.objects.get()
    original = row.credential_ref
    world.model.status = "active"
    world.model.applied_subscription_revision = 1
    world.model.model_ready_auth_revision = 1
    world.model.save()
    with subject(world):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
    assert not result.ok and result.errors[0].code == "CONFLICT"
    row.refresh_from_db()
    assert row.credential_ref == original


def test_revoke_keeps_credential_pending_until_actual_reconciliation(world, queue):
    allowed(world)
    row = ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        subscription_status="active",
    )
    row.credential_ref = f"services/{world.org.guid}/{world.model.guid}/subscriptions/{row.guid}#api_key"
    row.save()
    input = RevokeModelSubscriptionInput(
        organization_id=GUID(str(world.org.guid)),
        id=GUID(str(row.guid)),
        expected_cluster_id=GUID(str(world.cluster.guid)),
        expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
        if_match_version=row.version,
        if_match_deployment_version=world.model.version,
    )
    with subject(world):
        result = ClusterModelMutations().revoke_model_subscription(make_info(world.user), input=input)
    assert result.ok, result.errors
    row.refresh_from_db()
    assert row.subscription_status == "revoking" and not row.desired_enabled and row.applied_revision == 0
    assert row.credential_ref and result.data.restart_required and len(queue) == 1


def test_provision_checks_actual_owner_provider_and_runtime_before_hf(world, queue, monkeypatch):
    from types import SimpleNamespace

    from astrolift_clusters.models import ProviderPlugin
    from astrolift_services.hf_catalogue import CatalogueState, ModelGating
    from core.permissions import Permission

    actual = ProviderPlugin.objects.filter(slug="k8s_native").first()
    if actual is not None:
        world.cluster.provider_plugin = actual
    else:
        world.cluster.provider_plugin.slug = "k8s_native"
        world.cluster.provider_plugin.save()
    world.cluster.save()
    calls = []
    monkeypatch.setattr("astrolift_services.hf_connection._read", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "astrolift_services.hf_catalogue.model_detail",
        lambda repo, revision: calls.append((repo, revision))
        or SimpleNamespace(
            state=CatalogueState.AVAILABLE,
            model=SimpleNamespace(repo_id=repo, revision_sha=revision, gated=ModelGating.NONE),
        ),
    )
    with subject(world):
        denied = ClusterModelMutations().provision_cluster_model(
            make_info(world.user), input=request(world, name="new-model")
        )
    assert not denied.ok and calls == [] and queue == []
    grant(world, Permission.CLUSTER_UPDATE)
    grant(world, Permission.ORG_UPDATE)
    promote_host_operator(world)
    with subject(world):
        missing = ClusterModelMutations().provision_cluster_model(
            make_info(world.user),
            input=request(world, name="new-model", expected_provider_id=GUID(str(uuid4()))),
        )
        invalid = ClusterModelMutations().provision_cluster_model(
            make_info(world.user), input=request(world, name="new-model", cpu_request="0")
        )
    assert not missing.ok and not invalid.ok and calls == [] and queue == []
    with subject(world):
        accepted = ClusterModelMutations().provision_cluster_model(
            make_info(world.user), input=request(world, name="new-model")
        )
    assert accepted.ok, accepted.errors
    assert calls == [("org/model", "a" * 40)] and len(queue) == 1
    assert accepted.data.ready is False and accepted.data.provider_id == str(
        world.cluster.provider_plugin.guid
    )
    from astrolift_services.models import ManagedService

    row = ManagedService.objects.get(guid=accepted.data.id)
    assert row.registered_app_id is None and row.project_id is None and row.organization_id == world.org.pk
    assert (
        row.config["model_revision"] == "a" * 40
        and row.model_operation_provider_guid == world.cluster.provider_plugin.guid
    )


@pytest.mark.parametrize("change", ["narrowed-token", "revoked-token", "retired-operator", "inactive-actor"])
def test_creation_refreshes_authority_after_hub_observation(world, queue, monkeypatch, change):
    promote_host_operator(world)
    from types import SimpleNamespace

    from astrolift_clusters.models import ProviderPlugin
    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_identity.models import ApiToken, Member
    from astrolift_services.hf_catalogue import CatalogueState, ModelGating
    from astrolift_services.models import ManagedService
    from core.permissions import Permission

    grant(world, Permission.CLUSTER_UPDATE)
    grant(world, Permission.ORG_UPDATE)
    Member.objects.get_or_create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    actual = ProviderPlugin.objects.filter(slug="k8s_native").first()
    if actual is None:
        world.cluster.provider_plugin.slug = "k8s_native"
        world.cluster.provider_plugin.save()
    else:
        world.cluster.provider_plugin = actual
    world.cluster.save()

    def observed(repo, revision):
        if change == "narrowed-token":
            ApiToken.objects.filter(pk=get_current_api_token().pk).update(scopes=["read:clusters"])
        elif change == "revoked-token":
            ApiToken.objects.filter(pk=get_current_api_token().pk).update(is_revoked=True)
        elif change == "retired-operator":
            type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
        else:
            type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
        return SimpleNamespace(
            state=CatalogueState.AVAILABLE,
            model=SimpleNamespace(repo_id=repo, revision_sha=revision, gated=ModelGating.NONE),
        )

    monkeypatch.setattr("astrolift_services.hf_catalogue.model_detail", observed)
    monkeypatch.setattr("astrolift_services.hf_connection._read", lambda *args, **kwargs: None)
    count = ManagedService.objects.count()
    with subject(world, scopes=["admin"]):
        result = ClusterModelMutations().provision_cluster_model(
            make_info(world.user), input=request(world, name="revoked-during-catalogue")
        )
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert ManagedService.objects.count() == count and queue == []


@pytest.mark.django_db(transaction=True)
def test_simultaneous_subscribers_are_serialized_by_actual_postgres_row_lock(world, queue):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    allowed(world)
    original = subscription(world)
    barrier = Barrier(2)

    def subscribe():
        close_old_connections()
        try:
            with subject(world):
                barrier.wait(timeout=10)
                return ClusterModelMutations().subscribe_cluster_model(make_info(world.user), input=original)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: subscribe(), range(2)))
    assert sum(result.ok for result in results) == 1
    assert len(queue) == 1 and ManagedServiceAttachment.objects.count() == 1
    denied = next(result for result in results if not result.ok)
    assert denied.errors[0].code in ("VERSION_MISMATCH", "PRECONDITION")


def test_subscription_http_bearer_envelope_and_revocation_recheck(world, queue, client):
    import json
    from dataclasses import asdict

    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken, Member

    allowed(world)
    Member.objects.get_or_create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="subscriber",
        token_hash=minted.token_hash,
        scopes=["read:apps", "write:apps"],
    )
    query = "mutation($input:SubscribeClusterModelInput!){subscribeClusterModel(input:$input){ok errors{code message currentVersion} data{restartRequired deployment{id version desiredSubscriptionRevision ready} subscription{id alias status}}}}"
    input = asdict(subscription(world))
    wire = {
        "organizationId": input["organization_id"],
        "modelDeploymentId": input["model_deployment_id"],
        "expectedClusterId": input["expected_cluster_id"],
        "expectedProviderId": input["expected_provider_id"],
        "appEnvironmentId": input["app_environment_id"],
        "alias": input["alias"],
        "ifMatchVersion": input["if_match_version"],
        "ifMatchEnvironmentVersion": input["if_match_environment_version"],
    }
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {minted.plaintext}",
        "HTTP_X_ASTROLIFT_ORG": str(world.org.guid),
    }
    reply = client.post(
        "/app/gql/config/",
        json.dumps({"query": query, "variables": {"input": wire}}),
        content_type="application/json",
        **headers,
    )
    assert reply.status_code == 200 and not reply.json().get("errors"), reply.json()
    data = reply.json()["data"]["subscribeClusterModel"]
    assert (
        data["ok"]
        and data["data"]["subscription"]["status"] == "pending"
        and data["data"]["deployment"]["ready"] is False
    )
    token.scopes = ["read:apps"]
    token.save()
    refused_query = query.replace(
        "code message currentVersion",
        "code message field requiresAttestation currentVersion requestedVersion supportedMethods",
    )
    refused = client.post(
        "/app/gql/config/",
        json.dumps({"query": refused_query, "variables": {"input": wire}}),
        content_type="application/json",
        **headers,
    )
    assert refused.status_code == 200 and not refused.json().get("errors"), refused.json()
    refusal = refused.json()["data"]["subscribeClusterModel"]
    assert refusal["ok"] is False and refusal["data"] is None
    assert refusal["errors"][0]["code"] == "PERMISSION_DENIED"
    assert refusal["errors"][0]["currentVersion"] is None
    assert len(queue) == 1 and ManagedServiceAttachment.objects.count() == 1
    token.is_revoked = True
    token.save()
    denied = client.post(
        "/app/gql/config/",
        json.dumps({"query": query, "variables": {"input": wire}}),
        content_type="application/json",
        **headers,
    )
    assert (
        denied.status_code in (401, 403)
        or denied.json().get("errors")
        or denied.json()["data"]["subscribeClusterModel"]["ok"] is False
    )
    assert len(queue) == 1 and ManagedServiceAttachment.objects.count() == 1


def test_subscription_limit_refusal_does_not_archive_a_revoked_alias(world, queue):
    allowed(world)
    prior = ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        desired_enabled=False,
        subscription_status="revoked",
    )
    for index in range(64):
        ManagedServiceAttachment.objects.create(
            managed_service=world.model,
            app_environment=world.env,
            model_subscription=True,
            binding_alias=f"consumer_{index}",
            subscription_status="active",
        )
    with subject(world):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    prior.refresh_from_db()
    assert prior.deleted_at is None and prior.subscription_status == "revoked"
    assert ManagedServiceAttachment.objects.count() == 65 and queue == []
    world.model.refresh_from_db()
    assert world.model.subscription_revision == 0


def test_revoked_alias_reuse_gets_new_identity_and_credential_reference(world, queue):
    allowed(world)
    prior = ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        desired_enabled=False,
        subscription_status="revoked",
        credential_ref="retired",
    )
    with subject(world):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
    assert result.ok
    replacement = ManagedServiceAttachment.objects.get()
    assert replacement.guid != prior.guid and replacement.credential_ref != "retired"
    assert replacement.subscription_status == "pending" and len(queue) == 1
    assert ManagedServiceAttachment.all_objects.get(pk=prior.pk).deleted_at is not None


@pytest.mark.parametrize("action", ["update", "delete"])
@pytest.mark.parametrize(
    "refusal", ["no_owner_grant", "wrong_provider", "stale_version", "unavailable", "queue_disabled"]
)
def test_owner_mutations_refuse_before_persisting_or_queueing(world, monkeypatch, queue, action, refusal):
    from astrolift_services.schema.cluster_model_mutations import (
        DeprovisionClusterModelInput,
        UpdateClusterModelInput,
    )
    from core.permissions import Permission

    if refusal != "no_owner_grant":
        promote_host_operator(world)
        grant(world, Permission.CLUSTER_UPDATE)
    grant(world, Permission.ORG_UPDATE)
    if refusal == "unavailable":
        world.cluster.is_active = False
        world.cluster.save()
    if refusal == "queue_disabled":
        monkeypatch.setattr(
            "astrolift_workflows.client.start_workflow",
            lambda *a, **kw: WorkflowHandle("disabled", "", False),
        )
    kwargs = {
        "organization_id": GUID(str(world.org.guid)),
        "id": GUID(str(world.model.guid)),
        "expected_cluster_id": GUID(str(world.cluster.guid)),
        "expected_provider_id": GUID(
            str(uuid4() if refusal == "wrong_provider" else world.cluster.provider_plugin.guid)
        ),
        "if_match_version": world.model.version - (1 if refusal == "stale_version" else 0),
    }

    if action == "update":
        input = UpdateClusterModelInput(
            **kwargs,
            allow_subscriptions=False,
            cpu_request="6",
            memory_request="20Gi",
            gpu_count=0,
            cpu_kv_cache_gi_b=4,
        )
        call = ClusterModelMutations().update_cluster_model
    else:
        input = DeprovisionClusterModelInput(**kwargs)
        call = ClusterModelMutations().deprovision_cluster_model
    with subject(world):
        result = call(make_info(world.user), input=input)
    assert not result.ok and queue == []
    world.model.refresh_from_db()
    assert world.model.subscription_revision == 0 and world.model.status == "active"
    assert world.model.config["cpu"] == "4" and world.model.deleted_at is None


def test_owner_update_changes_admission_without_revoking_other_keys(world, queue):
    promote_host_operator(world)
    from astrolift_services.schema.cluster_model_mutations import UpdateClusterModelInput
    from core.permissions import Permission

    grant(world, Permission.CLUSTER_UPDATE)
    grant(world, Permission.ORG_UPDATE)
    row = ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        subscription_status="active",
        credential_ref="unchanged",
    )
    input = UpdateClusterModelInput(
        organization_id=GUID(str(world.org.guid)),
        id=GUID(str(world.model.guid)),
        expected_cluster_id=GUID(str(world.cluster.guid)),
        expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
        if_match_version=world.model.version,
        allow_subscriptions=False,
        cpu_request="6",
        memory_request="20Gi",
        gpu_count=0,
        cpu_kv_cache_gi_b=4,
    )
    with subject(world):
        result = ClusterModelMutations().update_cluster_model(make_info(world.user), input=input)
    assert result.ok and result.data.ready is False
    world.model.refresh_from_db()
    row.refresh_from_db()
    assert world.model.config["allow_subscriptions"] is False and world.model.config["cpu"] == "6"
    assert world.model.status == "updating" and row.desired_enabled and row.credential_ref == "unchanged"
    assert len(queue) == 1


def test_delete_requires_actual_revocation_then_is_accepted_pending(world, queue):
    promote_host_operator(world)
    from astrolift_services.schema.cluster_model_mutations import DeprovisionClusterModelInput
    from core.permissions import Permission

    grant(world, Permission.CLUSTER_UPDATE)
    grant(world, Permission.ORG_UPDATE)
    row = ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        desired_enabled=False,
        subscription_status="revoking",
    )
    input = DeprovisionClusterModelInput(
        organization_id=GUID(str(world.org.guid)),
        id=GUID(str(world.model.guid)),
        expected_cluster_id=GUID(str(world.cluster.guid)),
        expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
        if_match_version=world.model.version,
    )
    with subject(world):
        refused = ClusterModelMutations().deprovision_cluster_model(make_info(world.user), input=input)
    assert not refused.ok and queue == []
    row.subscription_status = "revoked"
    row.save()
    with subject(world):
        accepted = ClusterModelMutations().deprovision_cluster_model(make_info(world.user), input=input)
    assert accepted.ok
    world.model.refresh_from_db()
    assert world.model.status == "deprovisioning" and world.model.deleted_at is None
    assert queue[0][1][0].action == "delete"


@pytest.mark.parametrize(
    "observation", ["unavailable", "missing_model", "wrong_repo", "wrong_revision", "gated", "unknown_access"]
)
def test_creation_refuses_unverified_or_gated_hf_source_without_persisting(
    world, queue, monkeypatch, observation
):
    promote_host_operator(world)
    from types import SimpleNamespace

    from astrolift_clusters.models import ProviderPlugin
    from astrolift_services.hf_catalogue import CatalogueState, ModelGating
    from astrolift_services.models import ManagedService
    from core.permissions import Permission

    grant(world, Permission.CLUSTER_UPDATE)
    grant(world, Permission.ORG_UPDATE)
    actual = ProviderPlugin.objects.filter(slug="k8s_native").first()
    if actual is not None:
        world.cluster.provider_plugin = actual
    else:
        world.cluster.provider_plugin.slug = "k8s_native"
        world.cluster.provider_plugin.save()
    world.cluster.save()
    model = SimpleNamespace(
        repo_id="wrong/model" if observation == "wrong_repo" else "org/model",
        revision_sha="b" * 40 if observation == "wrong_revision" else "a" * 40,
        gated=ModelGating.MANUAL
        if observation == "gated"
        else ModelGating.UNKNOWN
        if observation == "unknown_access"
        else ModelGating.NONE,
    )
    monkeypatch.setattr(
        "astrolift_services.hf_catalogue.model_detail",
        lambda *args: SimpleNamespace(
            state=CatalogueState.UNAVAILABLE if observation == "unavailable" else CatalogueState.AVAILABLE,
            model=None if observation == "missing_model" else model,
        ),
    )
    from astrolift_services.hf_connection import HuggingFaceUnavailable

    def denied(*args, **kwargs):
        raise HuggingFaceUnavailable("Hugging Face access could not be verified.")

    monkeypatch.setattr("astrolift_services.hf_connection._read", denied)
    count = ManagedService.objects.count()
    with subject(world):
        result = ClusterModelMutations().provision_cluster_model(
            make_info(world.user), input=request(world, name="unverified")
        )
    assert not result.ok and result.errors[0].code == "VALIDATION"
    assert ManagedService.objects.count() == count and queue == []


@pytest.mark.parametrize("action", ["create", "update", "delete"])
def test_ordinary_owner_is_denied_even_after_locked_region_change(world, queue, monkeypatch, action):
    from astrolift_identity.models import Policy
    from astrolift_services.schema import cluster_model_mutations as module
    from astrolift_services.schema.cluster_model_mutations import (
        DeprovisionClusterModelInput,
        UpdateClusterModelInput,
    )
    from core.permissions import Permission

    grant(world, Permission.CLUSTER_UPDATE)
    grant(world, Permission.ORG_UPDATE)
    world.cluster.region = "us-west-2"
    world.cluster.save()
    Policy.objects.create(
        organization=world.org,
        name="deny-east",
        slug="deny-east",
        scope_level="ORG",
        scope_id=world.org.pk,
        effect="DENY",
        action_pattern="cluster.update",
        resource_pattern={"region": ["us-east-1"]},
    )
    actual = module._locked_cluster

    def region_changes(cluster_id, provider_id):
        type(world.cluster).objects.filter(pk=world.cluster.pk).update(region="us-east-1")
        return actual(cluster_id, provider_id)

    monkeypatch.setattr(module, "_locked_cluster", region_changes)
    if action == "create":
        input = request(world, name="new")
        call = ClusterModelMutations().provision_cluster_model
    else:
        kwargs = {
            "organization_id": GUID(str(world.org.guid)),
            "id": GUID(str(world.model.guid)),
            "expected_cluster_id": GUID(str(world.cluster.guid)),
            "expected_provider_id": GUID(str(world.cluster.provider_plugin.guid)),
            "if_match_version": world.model.version,
        }
        if action == "update":
            input = UpdateClusterModelInput(
                **kwargs,
                allow_subscriptions=False,
                cpu_request="6",
                memory_request="20Gi",
                gpu_count=0,
                cpu_kv_cache_gi_b=4,
            )
            call = ClusterModelMutations().update_cluster_model
        else:
            input = DeprovisionClusterModelInput(**kwargs)
            call = ClusterModelMutations().deprovision_cluster_model
    with subject(world):
        result = call(make_info(world.user), input=input)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED" and queue == []
    world.model.refresh_from_db()
    assert world.model.subscription_revision == 0 and world.model.status == "active"


@pytest.mark.parametrize("change", ["foreign_owner", "revoked_operator", "inactive_actor"])
def test_locked_target_rechecks_canonical_owner_actor_and_grants(world, queue, monkeypatch, change):
    promote_host_operator(world)
    from astrolift_services.schema import cluster_model_mutations as module
    from astrolift_services.schema.cluster_model_mutations import DeprovisionClusterModelInput
    from core.permissions import Permission

    grant(world, Permission.CLUSTER_UPDATE)
    grant(world, Permission.ORG_UPDATE)
    world.cluster.organization = None
    world.cluster.save()
    input = DeprovisionClusterModelInput(
        organization_id=GUID(str(world.org.guid)),
        id=GUID(str(world.model.guid)),
        expected_cluster_id=GUID(str(world.cluster.guid)),
        expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
        if_match_version=world.model.version,
    )
    if change == "foreign_owner":
        original = module.live_cluster_model_by_guid

        def raced_lookup(guid):
            row = original(guid)
            type(world.model).objects.filter(pk=world.model.pk).update(organization=world.other_org)
            return row

        monkeypatch.setattr(module, "live_cluster_model_by_guid", raced_lookup)
    else:
        original = module._locked_model

        def raced_lock(value):
            row = original(value)
            if change == "revoked_operator":
                type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
            else:
                type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
            return row

        monkeypatch.setattr(module, "_locked_model", raced_lock)
    with subject(world):
        result = ClusterModelMutations().deprovision_cluster_model(make_info(world.user), input=input)
    assert not result.ok and queue == []
    world.model.refresh_from_db()
    assert world.model.subscription_revision == 0 and world.model.deleted_at is None


@pytest.mark.parametrize("change", ["revoked_source_grant", "revoked_token", "narrowed_token"])
def test_subscription_rechecks_current_source_and_bearer_after_locks(world, queue, monkeypatch, change):
    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_identity.models import ApiToken, Member
    from astrolift_services.schema import cluster_model_mutations as module

    allowed(world)
    Member.objects.get_or_create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    original = module._locked_environment

    def raced_environment(value):
        env = original(value)
        if change == "revoked_source_grant":
            RoleBinding.objects.filter(role__permissions__contains=["org.read"]).update(
                deleted_at=timezone.now()
            )
        elif change == "revoked_token":
            ApiToken.objects.filter(pk=get_current_api_token().pk).update(is_revoked=True)
        else:
            ApiToken.objects.filter(pk=get_current_api_token().pk).update(scopes=["read:apps"])
        return env

    monkeypatch.setattr(module, "_locked_environment", raced_environment)
    with subject(world, scopes=["read:apps", "write:apps"]):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert queue == [] and not ManagedServiceAttachment.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_source_revocation_while_last_attachment_lock_waits_is_rechecked(world, queue, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import close_old_connections, transaction
    from django.db.models.query import QuerySet

    allowed(world)
    row = ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        subscription_status="active",
    )
    input = RevokeModelSubscriptionInput(
        organization_id=GUID(str(world.org.guid)),
        id=GUID(str(row.guid)),
        expected_cluster_id=GUID(str(world.cluster.guid)),
        expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
        if_match_version=row.version,
        if_match_deployment_version=world.model.version,
    )
    waiting = Event()
    first = QuerySet.first

    def entering_last_lock(qs):
        if qs.model is ManagedServiceAttachment and qs.query.select_for_update:
            waiting.set()
        return first(qs)

    monkeypatch.setattr(QuerySet, "first", entering_last_lock)

    def revoke():
        close_old_connections()
        try:
            with subject(world):
                return ClusterModelMutations().revoke_model_subscription(make_info(world.user), input=input)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction.atomic():
            ManagedServiceAttachment.objects.select_for_update().get(pk=row.pk)
            future = executor.submit(revoke)
            assert waiting.wait(timeout=10)
            RoleBinding.objects.filter(role__permissions__contains=["org.read"]).update(
                deleted_at=timezone.now()
            )
        result = future.result(timeout=15)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED" and queue == []
    row.refresh_from_db()
    world.model.refresh_from_db()
    assert row.desired_enabled and row.subscription_status == "active"
    assert world.model.subscription_revision == 0
