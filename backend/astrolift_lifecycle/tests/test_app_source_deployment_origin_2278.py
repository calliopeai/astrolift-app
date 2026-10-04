"""Actual committed bootstrap history cannot become an ordinary deployment."""

import json
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from astrolift_lifecycle.deployment_identity_origin import deployment_origin, native_origin_required
from astrolift_lifecycle.models import AppEnvironment, Deployment, DeploymentIdentityOrigin
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import start_public
from astrolift_services.models import (
    GCPAppIdentitySource,
    GCPClusterIdentitySource,
    GCPGKEPreparationJournal,
    GCPWorkloadIdentityJournal,
    ManagedServiceAttachment,
)
from astrolift_services.tests.test_cluster_model_queries_2213 import grant
from astrolift_services.tests.test_gcp_identity_source_2278 import bootstrap, independent_row
from astrolift_services.tests.test_gcp_identity_source_2278 import world as source_world
from astrolift_services.tests.test_model_connection_2270 import http_token
from astrolift_workflows.client import WorkflowHandle
from core.permissions import Permission
from core.tests.utils.scope_world import make_cluster

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch, client, tmp_path):
    return source_world.__wrapped__(monkeypatch, client, tmp_path)


def public_setup(w, monkeypatch):
    w.medops_app.build_mode = "ci_pushed"
    w.medops_app.save()
    grant(w, Permission.APP_DEPLOY, "APP", w.medops_app.pk)
    w.token, w.headers = http_token(w, scopes=("read:apps", "write:apps"))
    w.starts = []

    def start(name, args, *, workflow_id, **kwargs):
        w.starts.append(args[0])
        return WorkflowHandle(workflow_id, "retained-source-origin", True)

    monkeypatch.setattr("astrolift_lifecycle.schema.mutations.helpers.start_workflow", start)
    monkeypatch.setattr("core.pubsub.publish_sync", lambda *a, **k: None)


def committed_empty_graph(w, state="OBSERVED"):
    from gcp.identity_source import IdentitySourceError

    if state == "UNKNOWN":
        w.native.lost = True
        with pytest.raises(IdentitySourceError, match="CREATE_UNKNOWN"):
            bootstrap(w)
        result = GCPAppIdentitySource.objects.get()
        assert independent_row(str(result.guid))[0:2] == ("UNKNOWN", None)
    else:
        result = bootstrap(w)
        assert independent_row(result.source_id)[0] == "OBSERVED"
    assert w.native.writes == 1
    # Remove only disposable fixture consumer links. The project Endpoint and its
    # protected lifecycle history remain; no app desired service graph remains.
    ManagedServiceAttachment.all_objects.filter(app_environment=w.env).delete()
    assert not GCPWorkloadIdentityJournal.objects.exists()
    assert not GCPGKEPreparationJournal.objects.exists()
    assert GCPAppIdentitySource.objects.count() == 1
    assert not ManagedServiceAttachment.all_objects.filter(app_environment=w.env).exists()
    return result


@pytest.mark.parametrize("provider_replaced", [False, True])
@pytest.mark.parametrize("source_state", ["OBSERVED", "UNKNOWN"])
def test_committed_app_source_empty_graph_requires_exact_human_origin(
    world, client, monkeypatch, provider_replaced, source_state
):
    committed_empty_graph(world, source_state)
    assert native_origin_required(world.medops_app, world.env)
    public_setup(world, monkeypatch)
    if provider_replaced:
        world.cluster.provider_plugin.slug = "aws"
        world.cluster.provider_plugin.save()
    result = start_public(world, client)
    if provider_replaced:
        assert not result["ok"] and result["errors"][0]["code"] == "PRECONDITION"
        assert not Deployment.objects.exists() and not world.starts
    else:
        assert result["ok"], result["errors"]
        row = Deployment.objects.get(guid=result["data"]["id"])
        ref = deployment_origin(row)
        assert ref is not None and ref == world.starts[0].identity_authority
        assert ref.actor_user_id == world.user.pk and ref.environment_guid == str(world.env.guid)
    assert world.native.writes == 1


def test_same_family_provider_fk_reassignment_cannot_adopt_original_source(world, client, monkeypatch):
    committed_empty_graph(world)
    original = world.cluster.provider_plugin
    original.slug = "original-gcp-registration"
    original.save()
    other = make_cluster(world, "replacement-provider-" + uuid4().hex)
    replacement = other.provider_plugin
    replacement.slug = "gcp"
    replacement.save()
    world.cluster.provider_plugin = replacement
    world.cluster.save()
    assert GCPAppIdentitySource.objects.get().provider_plugin_id == original.pk != replacement.pk
    assert native_origin_required(world.medops_app, world.env)
    public_setup(world, monkeypatch)
    outcome = start_public(world, client)
    assert not outcome["ok"] and outcome["errors"][0]["code"] == "PRECONDITION"
    assert not Deployment.objects.exists() and not world.starts and world.native.writes == 1


def test_committed_app_source_refuses_nonhuman_before_deployment_or_dispatch(world, client, monkeypatch):
    from astrolift_lifecycle.deploy_tokens import issue_token

    committed_empty_graph(world)
    public_setup(world, monkeypatch)
    _, plaintext = issue_token(app=world.medops_app, name="retained-history-ci", scopes=["app.deploy"])
    result = client.post(
        f"/api/cli/v1/apps/{world.medops_app.slug}/deploy/",
        json.dumps(
            {
                "environment": world.env.name,
                "image_tags": {"web": "v1"},
                "commit_sha": "1" * 40,
                "branch": "main",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + plaintext,
    )
    assert result.status_code == 409 and result.json()["detail"] == "NATIVE_HUMAN_ORIGIN_REQUIRED"
    assert not Deployment.objects.exists() and not DeploymentIdentityOrigin.objects.exists()
    assert not world.starts and world.native.writes == 1


@pytest.mark.parametrize("provider", ["aws", "local"])
def test_original_app_source_does_not_gate_other_physical_cluster(world, client, monkeypatch, provider):
    committed_empty_graph(world)
    cluster = make_cluster(world, "app-source-other-" + uuid4().hex)
    cluster.provider_plugin.slug = provider
    cluster.provider_plugin.save()
    env = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=cluster, name="ordinary-other"
    )
    assert not native_origin_required(world.medops_app, env)
    public_setup(world, monkeypatch)
    result = start_public(world, client, environmentName=env.name)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    assert deployment_origin(row) is None and world.starts[0].identity_authority is None
    assert not DeploymentIdentityOrigin.objects.exists() and world.native.writes == 1


def test_app_source_tombstone_cannot_erase_retained_origin(world):
    committed_empty_graph(world)
    row = GCPAppIdentitySource.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        GCPAppIdentitySource.all_objects.filter(pk=row.pk).update(deleted_at=timezone.now())
    row.refresh_from_db()
    assert row.deleted_at is None and native_origin_required(world.medops_app, world.env)


def test_shared_cluster_pin_alone_does_not_gate_unrelated_app(world, client, monkeypatch):
    from astrolift_services.gcp_identity_source import _declaration, source_mutex

    _, physical = _declaration(world.authority)
    _, native = world.native.port().observe_cluster(current=lambda: None)
    with source_mutex(world.authority) as store:
        store.pin(world.operation, physical, native)
    assert GCPClusterIdentitySource.objects.count() == 1
    assert not GCPAppIdentitySource.objects.exists() and world.native.writes == 0
    world.medops_app = world.platform_app
    world.env = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="unrelated"
    )
    assert not native_origin_required(world.medops_app, world.env)
    public_setup(world, monkeypatch)
    result = start_public(world, client)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    assert deployment_origin(row) is None and world.starts[0].identity_authority is None
    assert not DeploymentIdentityOrigin.objects.exists() and world.native.writes == 0
