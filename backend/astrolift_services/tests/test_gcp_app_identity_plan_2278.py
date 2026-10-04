"""Actual HTTP caller + committed lifecycle/consumer rows + generated native source reads."""

import json
from dataclasses import asdict
from uuid import uuid4

import grpc
import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from gcp.identity_owned import NativeGCPIdentity, NativeIdentityContext, NativeIdentityError
from gcp.identity_wi import service_account_id_for
from gcp.managed.model_endpoint_vertex import VertexAIEndpointConfig
from gcp.vertex_catalogue import VertexCatalogue, VertexCatalogueConfig
from google.auth.credentials import AnonymousCredentials
from google.cloud import aiplatform_v1 as vertex
from google.cloud import aiplatform_v1beta1 as vertex_beta
from google.cloud import iam_admin_v1 as iam
from google.cloud import resourcemanager_v3 as manager
from google.cloud.aiplatform_v1.services.endpoint_service.transports.grpc import EndpointServiceGrpcTransport
from google.cloud.aiplatform_v1.services.model_service.transports.grpc import ModelServiceGrpcTransport
from google.cloud.aiplatform_v1beta1.services.endpoint_service.transports.grpc import (
    EndpointServiceGrpcTransport as BetaTransport,
)
from google.cloud.iam_admin_v1.services.iam.transports.grpc import IAMGrpcTransport
from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

from astrolift_identity.models import Policy, RoleBinding
from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.gcp_app_identity_plan import (
    EndpointAppPlanError,
    NativeEndpointObserver,
    endpoint_app_checkpoint,
    endpoint_app_snapshot,
    produce_endpoint_app_plan,
    refresh_endpoint_app_sources,
)
from astrolift_services.models import ManagedService, ManagedServiceAttachment, ManagedServiceBinding
from astrolift_services.native_identity_authority import current_app_identity_authority
from astrolift_services.tests.test_model_connection_2270 import world as original_world
from astrolift_services.tests.test_native_identity_authority_2278 import capture
from astrolift_workflows.activities.managed_service_lifecycle import _finalize_provision_sync
from astrolift_workflows.managed_service_review import capture_reviewed_binding
from astrolift_workflows.tests.test_vertex_operations_2277 import complete
from astrolift_workflows.vertex_managed_service import JOURNAL_KEY
from core.app_deploy import workload_identity_role_name
from core.cluster_credentials import credential_for_cluster
from core.permissions import PermissionDenied
from providers.tests.gcp.test_vertex_resource_operations_2277 import PARENT, VertexWire

pytestmark = pytest.mark.django_db(transaction=True)
NUMBER = "415104041262"
ROLE = "projects/fixture-project/roles/EndpointPredict"


class SourceWire(grpc.Channel):
    """Trusted channel returns native bytes; every allowed operation is a bounded read."""

    def __init__(self, identity, endpoint):
        self.identity, self.endpoint = identity, vertex.Endpoint(endpoint)
        self.endpoint.deployed_models[0].model_version_id = "7"
        self.calls, self.after = [], None
        self.project = manager.Project(name=f"projects/{NUMBER}", project_id="fixture-project", state=1)
        self.account = iam.ServiceAccount(
            name=f"projects/fixture-project/serviceAccounts/{identity.email}",
            project_id="fixture-project",
            email=identity.email,
            unique_id=identity.service_account_unique_id,
            description=identity.owner_description,
        )
        self.role = iam.Role(name=ROLE, stage=2, included_permissions=["aiplatform.endpoints.predict"])

    def subscribe(self, *args, **kwargs):
        raise AssertionError("no network")

    unsubscribe = unary_stream = stream_unary = stream_stream = subscribe

    def close(self):
        pass

    def unary_unary(self, method, request_serializer=None, response_deserializer=None, *args, **kwargs):
        name = (method.decode() if isinstance(method, bytes) else method).rsplit("/", 1)[-1]
        types = {
            "GetProject": manager.GetProjectRequest,
            "GetServiceAccount": iam.GetServiceAccountRequest,
            "GetRole": iam.GetRoleRequest,
            "GetEndpoint": vertex.GetEndpointRequest,
        }

        def call(request, **options):
            assert name in types, "No IAM policy, paid allocation, binding, listing, invocation or write"
            assert 0 < options["timeout"] <= 10 and not connection.in_atomic_block
            typed = types[name].deserialize(request_serializer(request))
            self.calls.append((name, typed.name))
            if name == "GetProject":
                assert typed.name in {"projects/fixture-project", f"projects/{NUMBER}"}
                result = self.project
            elif name == "GetServiceAccount":
                assert typed.name == self.identity.service_account_resource
                result = self.account
            elif name == "GetRole":
                assert typed.name == ROLE
                result = self.role
            else:
                assert typed.name == self.endpoint.name
                result = self.endpoint
            wire = type(result).serialize(result)
            if self.after:
                self.after(name)
            return response_deserializer(wire)

        class Completed:
            def trailing_metadata(self):
                return ()

        call.with_call = lambda request, **options: (call(request, **options), Completed())
        return call

    def observer(self, identity, *, checkpoint):
        assert identity == self.identity
        credentials = AnonymousCredentials()
        project = manager.ProjectsClient(
            transport=ProjectsGrpcTransport(
                channel=lambda *a, **k: self,
                host="cloudresourcemanager.googleapis.com",
                credentials=credentials,
            )
        )
        role_client = iam.IAMClient(
            transport=IAMGrpcTransport(
                channel=lambda *a, **k: self, host="iam.googleapis.com", credentials=credentials
            )
        )
        beta = vertex_beta.EndpointServiceClient(
            transport=BetaTransport(
                channel=lambda *a, **k: self,
                host="us-central1-aiplatform.googleapis.com",
                credentials=credentials,
            )
        )
        endpoint = vertex.EndpointServiceClient(
            transport=EndpointServiceGrpcTransport(
                channel=lambda *a, **k: self,
                host="us-central1-aiplatform.googleapis.com",
                credentials=credentials,
            )
        )
        model = vertex.ModelServiceClient(
            transport=ModelServiceGrpcTransport(
                channel=lambda *a, **k: self,
                host="us-central1-aiplatform.googleapis.com",
                credentials=credentials,
            )
        )
        # Only transport construction is injected: production observe()/facade,
        # generated request serializers and native response parsing remain actual.
        observer = object.__new__(NativeEndpointObserver)
        observer.identity, observer.checkpoint = identity, checkpoint
        observer.roles = NativeGCPIdentity(identity, clients=(project, role_client, beta))
        observer.catalogue = VertexCatalogue(
            VertexCatalogueConfig(identity.project_id, identity.region, identity.credential),
            clients=(project, endpoint, model),
            checkpoint=checkpoint,
        )
        return observer


@pytest.fixture
def world(monkeypatch, client):
    w = original_world.__wrapped__(monkeypatch)
    w.env.name = "development"
    w.env.save()
    w.authority = capture(w, client, monkeypatch)
    # Only this fixture's independently created local subscription is retired.
    for row in ManagedServiceAttachment.objects.filter(managed_service=w.model, app_environment=w.env):
        row.soft_delete()
    w.cluster.provider_plugin.slug = "gcp"
    w.cluster.provider_plugin.save()
    w.cluster.provider_config = {
        "project_id": "fixture-project",
        "region": "us-central1",
        "endpoint_prediction_role": ROLE,
    }
    w.cluster.auth_config = {}
    w.cluster.region = "us-central1"
    w.cluster.save()
    w.service = ManagedService.objects.create(
        project=w.medops_project,
        tenant_cluster=w.cluster,
        kind="model_endpoint",
        variant="vertex_ai",
        name="owned-endpoint",
        config={"model_artifact": f"{PARENT}/models/617492583"},
        operation_kind="provision",
        operation_workflow_id="ProvisionManagedServiceWorkflow-test",
        operation_started_at=timezone.now(),
    )
    w.wire = VertexWire()
    w.config = VertexAIEndpointConfig(project_id="fixture-project", region="us-central1")
    monkeypatch.setattr(vertex, "EndpointServiceClient", lambda **kwargs: w.wire)
    monkeypatch.setattr(vertex, "ModelServiceClient", lambda **kwargs: object())
    monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *a, **k: w.config)
    w.binding = capture_reviewed_binding(w.service.pk)
    result = complete(w)
    _finalize_provision_sync(w.service.pk, "model_endpoint/" + result["state"]["endpoint"])
    w.service.refresh_from_db()
    w.attachment = ManagedServiceAttachment.objects.create(managed_service=w.service, app_environment=w.env)
    w.identity = NativeIdentityContext(
        str(w.org.guid),
        str(w.medops_app.guid),
        str(w.cluster.guid),
        "fixture-project",
        NUMBER,
        "us-central1",
        service_account_id_for(workload_identity_role_name(w.medops_app)),
        "110012345678901234567",
        credential_for_cluster(w.cluster),
    )
    # Lifecycle monkeypatches are removed before actual generated source clients.
    monkeypatch.undo()
    w.source = SourceWire(w.identity, w.wire.endpoint)
    return w


def plan(w):
    return produce_endpoint_app_plan(w.authority, w.identity, observer_factory=w.source.observer)


def test_actual_http_and_completed_lifecycle_produce_exact_scoped_native_union_without_writes(world):
    p = plan(world)
    assert world.service.lifecycle_policy[JOURNAL_KEY]["complete"] is True
    assert "model_version_id" not in world.service.lifecycle_policy[JOURNAL_KEY]
    assert p.observations[0].deployments[0][2] == "7"
    assert p.template.permissions[0].role == ROLE
    assert p.template.permissions[0].resource.startswith(
        f"projects/{NUMBER}/locations/us-central1/endpoints/"
    )
    assert p.template.subjects[0].environment_ids == (str(world.env.guid),)
    assert "PRIVATE" not in json.dumps(asdict(p)) and world.private_key not in json.dumps(asdict(p))
    with transaction.atomic():
        endpoint_app_checkpoint(p)()
        with pytest.raises(EndpointAppPlanError, match="COMMITTED_DATABASE"):
            refresh_endpoint_app_sources(p, observer_factory=world.source.observer)
    refresh_endpoint_app_sources(p, observer_factory=world.source.observer)
    assert {name for name, _ in world.source.calls} == {
        "GetProject",
        "GetServiceAccount",
        "GetRole",
        "GetEndpoint",
    }


@pytest.mark.parametrize(
    "kind,variant", [("postgres", "cloudsql"), ("object_store", "gcs"), ("topic", "pubsub")]
)
def test_mixed_app_services_refuse_complete_union_before_any_native_read(world, kind, variant):
    ManagedService.objects.create(
        registered_app=world.medops_app, app_environment=world.env, kind=kind, variant=variant
    )
    with pytest.raises(EndpointAppPlanError, match="UNSUPPORTED_OR_INCOHERENT"):
        plan(world)
    assert world.source.calls == []


@pytest.mark.parametrize(
    "change",
    [
        "token",
        "scopes",
        "grants",
        "provider",
        "credential",
        "role",
        "binding",
        "attachment",
        "env_namespace",
        "env_config",
        "app_manifest",
        "lifecycle",
        "retirement",
    ],
)
def test_current_db_only_checkpoint_refuses_committed_same_version_drift_without_native_calls(world, change):
    p = plan(world)
    world.source.calls.clear()
    if change == "token":
        type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
    elif change == "scopes":
        type(world.token).objects.filter(pk=world.token.pk).update(scopes=["read:apps"])
    elif change == "grants":
        RoleBinding.objects.filter(user=world.user).update(expires_at=timezone.now())
    elif change == "provider":
        type(world.cluster.provider_plugin).objects.filter(pk=world.cluster.provider_plugin.pk).update(
            is_enabled=False
        )
    elif change in {"credential", "role"}:
        config = dict(world.cluster.provider_config)
        config["project_id" if change == "credential" else "endpoint_prediction_role"] = "foreign-project"
        type(world.cluster).objects.filter(pk=world.cluster.pk).update(provider_config=config)
    elif change == "binding":
        row = ManagedServiceBinding.objects.filter(managed_service=world.service).first()
        ManagedServiceBinding.objects.filter(pk=row.pk).update(env_value_ref="private-drift-marker")
    elif change == "attachment":
        ManagedServiceAttachment.objects.filter(pk=world.attachment.pk).update(desired_enabled=False)
    elif change == "env_namespace":
        AppEnvironment.objects.filter(pk=world.env.pk).update(k8s_namespace="changed-target")
    elif change == "env_config":
        AppEnvironment.objects.filter(pk=world.env.pk).update(deploy_config={"private-source": "changed"})
    elif change == "app_manifest":
        type(world.medops_app).objects.filter(pk=world.medops_app.pk).update(
            manifest_normalized={"source": "changed"}
        )
    elif change == "lifecycle":
        state = dict(world.service.lifecycle_policy)
        state[JOURNAL_KEY]["deployed_model_id"] = "987654321"
        ManagedService.objects.filter(pk=world.service.pk).update(lifecycle_policy=state)
    else:
        ManagedService.objects.filter(pk=world.service.pk).update(deleted_at=timezone.now())
    with transaction.atomic(), pytest.raises((EndpointAppPlanError, PermissionDenied)):
        endpoint_app_checkpoint(p)()
    assert world.source.calls == []


@pytest.mark.parametrize(
    "change", ["version_missing", "version_changed", "sibling", "machine", "traffic", "owner", "gsa", "role"]
)
def test_actual_generated_current_native_drift_refuses_retained_plan_with_no_effects(world, change):
    p = plan(world)
    model = world.source.endpoint.deployed_models[0]
    if change.startswith("version"):
        model.model_version_id = "" if change == "version_missing" else "8"
    elif change == "sibling":
        other = vertex.DeployedModel(model)
        other.id = "9191919"
        world.source.endpoint.deployed_models.append(other)
    elif change == "machine":
        model.dedicated_resources.machine_spec.machine_type = "n1-standard-8"
    elif change == "traffic":
        world.source.endpoint.traffic_split.clear()
    elif change == "owner":
        world.source.endpoint.labels.clear()
        world.source.endpoint.labels["astrolift-managed-service-id"] = str(uuid4())
    elif change == "gsa":
        world.source.account.unique_id = "110012345678901234568"
    else:
        world.source.role.included_permissions.append("aiplatform.endpoints.update")
    with pytest.raises((EndpointAppPlanError, NativeIdentityError)):
        refresh_endpoint_app_sources(p, observer_factory=world.source.observer)


def test_shared_physical_ksa_keeps_all_environment_aliases_and_endpoint_on_single_alias_detach(world):
    second = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="production"
    )
    ManagedServiceAttachment.objects.create(managed_service=world.service, app_environment=second)
    p = plan(world)
    assert len(p.template.subjects) == 1 and p.template.subjects[0].environment_ids == tuple(
        sorted([str(second.guid), str(world.env.guid)])
    )
    assert len(p.template.permissions) == 1
    ManagedServiceAttachment.objects.filter(pk=world.attachment.pk).update(desired_enabled=False)
    with pytest.raises(EndpointAppPlanError):
        endpoint_app_checkpoint(p)()
    changed = plan(world)
    assert (
        changed.template.permissions == p.template.permissions
        and changed.template.sha256 != p.template.sha256
    )
    ManagedServiceAttachment.objects.filter(managed_service=world.service).update(desired_enabled=False)
    empty = plan(world)
    assert not empty.template.permissions and empty.template.subjects == p.template.subjects


def test_all_aliases_need_actual_current_app_abac_admission_before_any_native_reads(world):
    for index in range(23):
        AppEnvironment.objects.create(
            registered_app=world.medops_app, tenant_cluster=world.cluster, name=f"alias-{index}"
        )
    AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="production"
    )
    Policy.objects.create(
        organization=world.org,
        name="protect-prod",
        slug="protect-prod",
        scope_level="ORG",
        effect="DENY",
        action_pattern="app.update",
        resource_pattern={"env": "production"},
        conditions=[],
    )
    with current_app_identity_authority(world.authority):
        pass
    with pytest.raises(PermissionDenied):
        plan(world)
    assert not world.source.calls


def test_source_withdrawal_during_native_sdk_boundary_prevents_following_reads(world):
    def withdraw(name):
        if name == "GetEndpoint":
            ManagedServiceAttachment.objects.filter(pk=world.attachment.pk).update(desired_enabled=False)

    world.source.after = withdraw
    with pytest.raises((EndpointAppPlanError, NativeIdentityError)):
        plan(world)
    assert world.source.calls[-1][0] == "GetEndpoint"


@pytest.mark.parametrize("declared_role", [None, "roles/aiplatform.user"])
def test_empty_detach_requires_no_role_or_endpoint_observation_even_with_withdrawn_role(world, declared_role):
    ManagedServiceAttachment.objects.filter(pk=world.attachment.pk).update(desired_enabled=False)
    config = dict(world.cluster.provider_config)
    config["endpoint_prediction_role"] = declared_role
    type(world.cluster).objects.filter(pk=world.cluster.pk).update(provider_config=config)
    world.source.role.deleted = True

    def refuse_construction(*args, **kwargs):
        pytest.fail("Empty desired union constructed ADC/source/role clients")

    p = produce_endpoint_app_plan(world.authority, world.identity, observer_factory=refuse_construction)
    assert p.template.permissions == () and p.observations == ()
    refresh_endpoint_app_sources(p, observer_factory=refuse_construction)
    assert world.source.calls == []


def test_available_replica_observation_does_not_change_accepted_routing_identity(world):
    p = plan(world)
    world.source.endpoint.deployed_models[0].status.available_replica_count = 0
    fresh = plan(world)
    assert fresh.observations != p.observations
    assert fresh.template == p.template
    refresh_endpoint_app_sources(p, observer_factory=world.source.observer)


def test_manual_outer_transaction_refuses_native_construction_before_any_read(world):
    p = plan(world)
    world.source.calls.clear()
    transaction.set_autocommit(False)
    try:
        for operation in (
            lambda: plan(world),
            lambda: refresh_endpoint_app_sources(p, observer_factory=world.source.observer),
        ):
            with pytest.raises(EndpointAppPlanError, match="COMMITTED_DATABASE"):
                operation()
        assert world.source.calls == []
    finally:
        transaction.rollback()
        transaction.set_autocommit(True)


def test_observer_constructor_withdrawal_closes_already_created_private_role_clients(world, monkeypatch):
    closed = []

    class Roles:
        def __init__(self, identity):
            assert identity == world.identity

        def close(self):
            closed.append(True)

    def withdrawn(*args, checkpoint, **kwargs):
        type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
        checkpoint()
        pytest.fail("Withdrawn source continued catalogue construction")

    monkeypatch.setattr("gcp.identity_owned.NativeGCPIdentity", Roles)
    monkeypatch.setattr("gcp.vertex_catalogue.VertexCatalogue", withdrawn)
    with pytest.raises(PermissionDenied):
        produce_endpoint_app_plan(world.authority, world.identity)
    assert closed == [True] and world.source.calls == []


def test_app_slug_rename_never_rederives_original_gsa_identity(world):
    p = plan(world)
    type(world.medops_app).objects.filter(pk=world.medops_app.pk).update(slug="renamed-app")
    with pytest.raises(EndpointAppPlanError):
        endpoint_app_checkpoint(p)()
    renamed = plan(world)
    assert renamed.identity == p.identity
    assert renamed.template.sha256 != p.template.sha256
    assert world.identity.service_account_resource in {
        value for name, value in world.source.calls if name == "GetServiceAccount"
    }


def test_current_complete_alias_source_queries_are_bounded_one_vs_twenty_five(world, record_property):
    def counted():
        with CaptureQueriesContext(connection) as queries:
            snapshot = endpoint_app_snapshot(world.authority, world.identity)
        return len(queries), snapshot

    one, initial = counted()
    for index in range(24):
        env = AppEnvironment.objects.create(
            registered_app=world.medops_app, tenant_cluster=world.cluster, name=f"alias-{index}"
        )
        ManagedServiceAttachment.objects.create(managed_service=world.service, app_environment=env)
    many, complete = counted()
    record_property("single_alias_query_count", one)
    record_property("twenty_five_alias_query_count", many)
    assert one == many
    assert len(initial.environments) == 1 and len(complete.environments) == 25
    assert len(complete.subjects) == 1 and len(complete.subjects[0].environment_ids) == 25
    assert len(complete.attachments) == 25 and len(complete.endpoints) == 1
