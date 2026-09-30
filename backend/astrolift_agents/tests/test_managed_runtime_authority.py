import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import close_old_connections
from django.test import RequestFactory
from django.utils import timezone

from astrolift_agents.models import AgentBox, ManagedBoxRuntime
from astrolift_agents.services.managed_runtime_authority import (
    RuntimeAuthorityError,
    authenticated_runtime,
    certify_managed_runtime,
    validate_runtime_authority,
)
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_dispatch.managed_runtime_views import validate_managed_runtime
from astrolift_identity.models import Organization
from astrolift_operations.models import AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch, settings):
    settings.PLATFORM_API_URL = "https://platform.example.test/"
    org = Organization.objects.create(name="Managed", slug="managed-runtime")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="K8s", slug="managed-test", capabilities_manifest={}, config_schema={})]
    )
    cluster = TenantCluster.objects.create(
        name="Managed",
        slug="managed-test",
        organization=org,
        provider_plugin=plugin,
        lifecycle="managed",
        auth_method="kubeconfig",
    )
    box = AgentBox.objects.create(
        organization=org,
        name="Managed IDE",
        slug="managed-ide",
        status="running",
        namespace="managed",
        external_id="managed-job",
        image="ide@sha256:" + "a" * 64,
    )
    manifests = {
        "Job": {
            "kind": "Job",
            "metadata": {
                "name": "managed-job",
                "namespace": "managed",
                "uid": "job-uid",
                "labels": {"astrolift.dev/agent-box": str(box.guid)},
            },
        },
        "PersistentVolumeClaim": {
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": "managed-state",
                "namespace": "managed",
                "uid": "claim-uid",
            },
            "status": {"phase": "Bound"},
        },
        "Pod": {
            "kind": "Pod",
            "metadata": {
                "name": "managed-pod",
                "namespace": "managed",
                "uid": "pod-uid",
                "labels": {"astrolift.dev/agent-box": str(box.guid)},
                "ownerReferences": [
                    {"kind": "Job", "name": "managed-job", "uid": "job-uid", "controller": True}
                ],
            },
            "spec": {
                "containers": [
                    {
                        "name": "ide",
                        "image": box.image,
                        "volumeMounts": [
                            {"name": "data", "mountPath": "/state", "subPath": "state"},
                            {"name": "data", "mountPath": "/workspace", "subPath": "workspace"},
                        ],
                    }
                ],
                "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "managed-state"}}],
            },
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {
                        "name": "ide",
                        "image": box.image,
                        "imageID": "docker-pullable://ide@sha256:" + "d" * 64,
                        "state": {"running": {"startedAt": "2026-09-30T00:00:00Z"}},
                    }
                ],
            },
        },
    }
    calls = []

    def get_manifest(cluster_id, namespace, kind, name):
        calls.append((cluster_id, namespace, kind, name))
        return copy.deepcopy(manifests[kind])

    driver = SimpleNamespace(get_manifest=get_manifest)
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda _: driver)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda _: SimpleNamespace(slug=cluster.slug)
    )
    args = {
        "box_id": box.pk,
        "cluster": cluster,
        "workspace_path": "/workspace",
        "job_uid": "job-uid",
        "claim_name": "managed-state",
        "claim_uid": "claim-uid",
        "pod_name": "managed-pod",
        "pod_uid": "pod-uid",
        "container_name": "ide",
    }
    runtime, token = certify_managed_runtime(**args)
    payload = {
        "version": 1,
        "ownerEpoch": str(runtime.owner_epoch),
        "podUid": "pod-uid",
        "runtimeInstanceId": "b" * 32,
    }
    return SimpleNamespace(
        org=org,
        box=box,
        cluster=cluster,
        manifests=manifests,
        driver=driver,
        calls=calls,
        args=args,
        runtime=runtime,
        token=token,
        payload=payload,
    )


def validate(world, payload=None):
    return validate_runtime_authority(
        world.runtime.pk, world.token, world.payload if payload is None else payload
    )


def response(world, *, authorization=None, payload=None):
    request = RequestFactory().post(
        "/api/dispatch/v1/boxes/runtime/validate/",
        data=json.dumps(world.payload if payload is None else payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=authorization if authorization is not None else "Bearer " + world.token,
    )
    return validate_managed_runtime(request, world.box.guid)


def test_certification_and_repeat_validation_are_scoped_and_hashed(world):
    assert world.token not in world.runtime.token_hash
    assert len(world.runtime.token_hash) == 64
    assert authenticated_runtime(world.box.guid, world.token).pk == world.runtime.pk
    assert authenticated_runtime(world.org.guid, world.token) is None
    expected = {
        "version": 1,
        "owner": {
            "apiUrl": "https://platform.example.test",
            "organizationId": str(world.org.guid),
            "boxId": str(world.box.guid),
            "ownerEpoch": str(world.runtime.owner_epoch),
            "workspacePath": "/workspace",
        },
        "incarnation": {
            "namespace": "managed",
            "podName": "managed-pod",
            "podUid": "pod-uid",
            "containerName": "ide",
            "runtimeInstanceId": "b" * 32,
        },
    }
    assert validate(world) == expected
    assert validate(world) == expected
    assert json.loads(response(world).content) == {"ok": True, "errors": [], "data": expected}
    assert len(world.calls) == 12
    assert all(call[0:2] == (world.cluster.slug, "managed") for call in world.calls)
    assert world.token not in str(list(AuditEvent.objects.values("reasoning")))
    with pytest.raises(RuntimeAuthorityError, match="already"):
        certify_managed_runtime(**world.args)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("ownerEpoch", "foreign"),
        ("podUid", "other-pod"),
        ("runtimeInstanceId", "invalid"),
        ("destination", "invented"),
    ],
)
def test_invalid_or_foreign_claim_does_not_bind_runtime(world, field, value):
    payload = {**world.payload, field: value}
    assert response(world, payload=payload).status_code == 409
    world.runtime.refresh_from_db()
    assert world.runtime.runtime_instance_id == ""


@pytest.mark.parametrize("authorization", ["", "raw", "Basic junk", "Bearer invalid"])
def test_authentication_precedes_live_resource_access(world, authorization):
    world.calls.clear()
    header = world.token if authorization == "raw" else authorization
    assert response(world, authorization=header).status_code == 401
    assert world.calls == []


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("Pod", "uid", "replacement"),
        ("Job", "uid", "replacement"),
        ("PersistentVolumeClaim", "uid", "replacement"),
        ("Pod", "deletionTimestamp", "2026-09-30T00:00:00Z"),
        ("Job", "labels", {}),
        ("Pod", "ownerReferences", []),
        ("Pod", "namespace", "foreign"),
    ],
)
def test_replaced_deleted_or_foreign_resources_refuse_and_audit(world, kind, field, value):
    world.manifests[kind]["metadata"][field] = value
    assert response(world).status_code == 409
    assert AuditEvent.objects.filter(decision="DENY", target_id=str(world.box.guid)).exists()
    world.runtime.refresh_from_db()
    assert world.runtime.runtime_instance_id == ""


@pytest.mark.parametrize(
    "case", ["emptyDir", "readonly", "subpath", "overlay", "stopped", "image", "unbound"]
)
def test_unusable_runtime_or_nonpersistent_workspace_refuses(world, case):
    pod = world.manifests["Pod"]
    container = pod["spec"]["containers"][0]
    if case == "emptyDir":
        pod["spec"]["volumes"] = [{"name": "data", "emptyDir": {}}]
    elif case == "readonly":
        container["volumeMounts"][1]["readOnly"] = True
    elif case == "subpath":
        container["volumeMounts"][1]["subPath"] = "state"
    elif case == "overlay":
        container["volumeMounts"].append({"name": "other", "mountPath": "/workspace/project"})
    elif case == "stopped":
        pod["status"]["containerStatuses"][0]["state"] = {"terminated": {"exitCode": 0}}
    elif case == "image":
        container["image"] = "other:latest"
    else:
        world.manifests["PersistentVolumeClaim"]["status"]["phase"] = "Pending"
    assert response(world).status_code == 409


@pytest.mark.parametrize(
    "case", ["stopped", "deleted", "expired", "rotated", "foreign-cluster", "inactive-cluster"]
)
def test_revocation_after_authentication_refuses(world, case):
    assert authenticated_runtime(world.box.guid, world.token)
    if case == "stopped":
        world.box.status = "stopped"
        world.box.save()
    elif case == "deleted":
        world.box.soft_delete()
    elif case == "expired":
        world.runtime.token_expires_at = timezone.now() - timedelta(seconds=1)
        world.runtime.save()
    elif case == "rotated":
        world.runtime.token_hash = "0" * 64
        world.runtime.save()
    elif case == "foreign-cluster":
        world.cluster.organization = Organization.objects.create(name="Other", slug="other")
        world.cluster.save()
    else:
        world.cluster.is_active = False
        world.cluster.save()
    with pytest.raises(RuntimeAuthorityError):
        validate(world)


def test_new_process_cannot_adopt_old_runtime_receipt(world):
    validate(world)
    assert response(world, payload={**world.payload, "runtimeInstanceId": "c" * 32}).status_code == 409
    world.runtime.refresh_from_db()
    assert world.runtime.runtime_instance_id == "b" * 32


@pytest.mark.parametrize(
    "field,value",
    [
        ("image", "ide@sha256:" + "e" * 64),
        ("image", None),
        ("imageID", None),
        ("imageID", "ide:latest"),
        ("imageID", "ide@sha256:" + "e" * 64),
    ],
)
def test_running_image_must_match_certified_observation(world, field, value):
    world.manifests["Pod"]["status"]["containerStatuses"][0][field] = value
    assert response(world).status_code == 409
    world.runtime.refresh_from_db()
    assert world.runtime.runtime_instance_id == ""


def test_image_id_transport_prefix_is_normalized(world):
    assert world.runtime.observed_image_id == "ide@sha256:" + "d" * 64
    world.manifests["Pod"]["status"]["containerStatuses"][0]["imageID"] = world.runtime.observed_image_id
    assert response(world).status_code == 200


def test_provider_failure_is_generic_and_does_not_bind(world):
    def unavailable(*args):
        raise RuntimeError("private provider credential detail")

    world.driver.get_manifest = unavailable
    result = response(world)
    assert result.status_code == 503
    assert b"private" not in result.content
    world.runtime.refresh_from_db()
    assert world.runtime.runtime_instance_id == ""


def test_expiry_during_provider_check_refuses(world, monkeypatch):
    initial = timezone.now()
    world.runtime.token_expires_at = initial + timedelta(seconds=5)
    world.runtime.save()
    original = world.driver.get_manifest

    def expire(*args):
        result = original(*args)
        monkeypatch.setattr(
            "astrolift_agents.services.managed_runtime_authority.timezone.now",
            lambda: initial + timedelta(seconds=10),
        )
        return result

    world.driver.get_manifest = expire
    assert response(world).status_code == 409
    world.runtime.refresh_from_db()
    assert world.runtime.runtime_instance_id == ""


@pytest.mark.parametrize(
    "url", ["http://platform.test", "https://user:pass@platform.test", "https://platform.test/?q=1"]
)
def test_issuer_rejects_noncanonical_configuration(world, settings, url):
    settings.PLATFORM_API_URL = url
    with pytest.raises(RuntimeAuthorityError, match="HTTPS"):
        certify_managed_runtime(**world.args)
    assert ManagedBoxRuntime.objects.count() == 1


def test_real_http_route_requires_box_token(world, client):
    url = f"/api/dispatch/v1/boxes/{world.box.guid}/runtime/validate/"
    assert client.post(url, data=world.payload, content_type="application/json").status_code == 401
    result = client.post(
        url, data=world.payload, content_type="application/json", HTTP_AUTHORIZATION="Bearer " + world.token
    )
    assert result.status_code == 200
    assert result.json()["data"]["owner"]["boxId"] == str(world.box.guid)


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_incarnations_have_only_one_owner(world):
    def attempt(nonce):
        close_old_connections()
        try:
            validate_runtime_authority(
                world.runtime.pk, world.token, {**world.payload, "runtimeInstanceId": nonce}
            )
            return nonce
        except RuntimeAuthorityError:
            return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, ["b" * 32, "c" * 32]))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1
    world.runtime.refresh_from_db()
    assert world.runtime.runtime_instance_id == winners[0]
