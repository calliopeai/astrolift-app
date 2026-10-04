"""The hosted list/detail uses public source identities through real bearer HTTP."""

import json
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member
from astrolift_services.models import LocalModelArtifact, ManagedService
from astrolift_services.tests.test_cluster_model_foundation_2213 import world as foundation_world
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db

FIELDS = "id name organizationId clusterId providerId sourceKind localArtifactId localArtifactVersion localManifestSha256 modelRepo revisionSha computeMode status ready readinessObservedAt subscriptionsEnabled sharingMode dedicatedAppId dedicatedAppVersion dedicatedAppName dedicatedAppSlug desiredResources{cpuRequest memoryRequest gpuCount}"
PAGE = (
    "query($org:GUID!,$page:Int!,$filter:ClusterModelsFilterInput){clusterModelDeploymentsPage(organizationId:$org,page:$page,pageSize:1,filter:$filter){totalCount page items{"
    + FIELDS
    + "}}}"
)
DETAIL = "query($org:GUID!,$id:GUID!){clusterModelDeployment(organizationId:$org,id:$id){" + FIELDS + "}}"


@pytest.fixture
def world(monkeypatch):
    w = foundation_world.__wrapped__(monkeypatch)
    w.member, _ = Member.objects.get_or_create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        w.user,
        permissions=[Permission.ORG_READ],
        kind="ORG",
        scope_id=w.org.pk,
        slug="hosted-inventory-reader",
    )
    minted = mint_token()
    w.token = ApiToken.objects.create(
        user=w.user,
        organization=w.org,
        name="Hosted model reader",
        token_hash=minted.token_hash,
        scopes=["read:apps"],
    )
    w.bearer = minted.plaintext
    w.client = Client()
    w.model.config = {
        "model": "publisher/tiny",
        "model_revision": "a" * 40,
        "compute_mode": "cpu",
        "cpu": "2",
        "memory": "8Gi",
        "gpu": 0,
        "cpu_kv_cache_gib": 2,
        "allow_subscriptions": True,
    }
    w.model.status = "active"
    w.model.save()
    w.artifact = LocalModelArtifact.objects.create(
        organization=w.org,
        name="Imported files",
        state="verified",
        manifest_sha256="b" * 64,
        storage_source="c" * 64,
        verified_at=timezone.now(),
    )
    w.local = ManagedService.objects.create(
        organization=w.org,
        tenant_cluster=w.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="Imported CPU model",
        config={
            "model_source": "local_artifact",
            "model": "local-" + str(w.artifact.guid),
            "model_artifact_id": str(w.artifact.guid),
            "model_artifact_version": w.artifact.version,
            "model_artifact_manifest_sha256": w.artifact.manifest_sha256,
            "compute_mode": "cpu",
            "cpu": "4",
            "memory": "16Gi",
            "gpu": 0,
            "cpu_kv_cache_gib": 4,
            "allow_subscriptions": False,
        },
    )
    return w


def query(w, document, variables):
    response = w.client.post(
        "/app/gql/config/",
        data={"query": document, "variables": variables},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + w.bearer,
        HTTP_X_ASTROLIFT_ORGANIZATION=str(w.org.guid),
    )
    return response.status_code, response.json()


def test_actual_paged_inventory_and_detail_preserve_local_source_without_claiming_ready(world):
    status, first = query(world, PAGE, {"org": str(world.org.guid), "page": 1})
    assert status == 200 and not first.get("errors")
    status, second = query(world, PAGE, {"org": str(world.org.guid), "page": 2})
    page = second["data"]["clusterModelDeploymentsPage"]
    assert status == 200 and page["totalCount"] == 2 and page["page"] == 2
    local = page["items"][0]
    assert local["id"] == str(world.local.guid) and local["sourceKind"] == "local_artifact"
    assert local["localArtifactId"] == str(world.artifact.guid)
    assert local["localArtifactVersion"] == world.artifact.version
    assert local["localManifestSha256"] == world.artifact.manifest_sha256 and local["revisionSha"] is None
    assert local["clusterId"] == str(world.cluster.guid) and local["providerId"] == str(
        world.cluster.provider_plugin.guid
    )
    assert local["desiredResources"] == {"cpuRequest": "4", "memoryRequest": "16Gi", "gpuCount": 0}
    assert local["sharingMode"] == "SHARED"
    assert all(
        local[field] is None
        for field in ("dedicatedAppId", "dedicatedAppVersion", "dedicatedAppName", "dedicatedAppSlug")
    )
    assert local["ready"] is False and local["readinessObservedAt"] is None
    assert first["data"]["clusterModelDeploymentsPage"]["items"][0]["ready"] is False
    status, detail = query(world, DETAIL, {"org": str(world.org.guid), "id": local["id"]})
    assert status == 200 and detail["data"]["clusterModelDeployment"] == local


@pytest.mark.parametrize("change", ["member", "token", "actor", "role"])
def test_actual_read_admission_refuses_withdrawn_viewer(world, change):
    assert query(world, DETAIL, {"org": str(world.org.guid), "id": str(world.local.guid)})[0] == 200
    if change == "member":
        world.member.is_active = False
        world.member.save()
    elif change == "token":
        world.token.is_revoked = True
        world.token.save()
    elif change == "actor":
        world.user.is_active = False
        world.user.save()
    else:
        world.user.role_bindings.all().update(deleted_at=timezone.now())
    status, result = query(world, DETAIL, {"org": str(world.org.guid), "id": str(world.local.guid)})
    assert status == 401 or result.get("errors") or result["data"]["clusterModelDeployment"] is None
    assert "Imported CPU model" not in json.dumps(result)


@pytest.mark.parametrize("change", ["foreign_model", "retired_model", "foreign_cluster", "retired_provider"])
def test_actual_exact_detail_refuses_foreign_or_retired_identity(world, change):
    if change == "foreign_model":
        world.local.organization = world.other_org
        world.local.save()
    elif change == "retired_model":
        world.local.soft_delete()
    elif change == "foreign_cluster":
        world.cluster.organization = world.other_org
        world.cluster.save()
    else:
        world.cluster.provider_plugin.soft_delete()
    status, result = query(world, DETAIL, {"org": str(world.org.guid), "id": str(world.local.guid)})
    assert status == 200 and result["data"]["clusterModelDeployment"] is None
    assert "Imported CPU model" not in json.dumps(result)
    assert (
        query(world, DETAIL, {"org": str(world.org.guid), "id": str(uuid4())})[1]["data"][
            "clusterModelDeployment"
        ]
        is None
    )


def test_cluster_only_credential_does_not_widen_the_org_read_ceiling(world):
    world.token.scopes = ["read:clusters"]
    world.token.save()
    status, result = query(world, DETAIL, {"org": str(world.org.guid), "id": str(world.local.guid)})
    assert status == 200 and result.get("errors")
    assert "Imported CPU model" not in json.dumps(result)
