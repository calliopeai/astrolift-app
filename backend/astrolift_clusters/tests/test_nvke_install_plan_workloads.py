import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db.models import F
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_workloads as workloads
from astrolift_clusters.models import NvkeInstallPlan, NvkeInstallPlanTree, NvkeInstallPlanWorkload
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_contexts import (
    agent,  # noqa: F401
    chart_fixture,  # noqa: F401
    configured,  # noqa: F401
    context_fixture,  # noqa: F401
    prepared_fixture,  # noqa: F401
    tree_fixture,  # noqa: F401
    world,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_install_plan_images import image_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_sites import context
from core.secrets import EncryptedSecret, decrypt

pytestmark = pytest.mark.django_db


@pytest.fixture
def workload_fixture(context_fixture, monkeypatch):  # noqa: F811
    f = context_fixture
    with context(f.state.world):
        workloads.contexts.prepare(body=f.body, **f.review)
    body = {
        "format": 1,
        "artifactId": str(uuid4()),
        **{key: f.body[key] for key in ("planId", "planDigest", "treeId", "treeDigest")},
        "contextBindings": {
            f.body["item"]: {"contextId": f.body["artifactId"], "contextDigest": f.result["contextDigest"]}
        },
        "maximumBytes": "67108864",
        "maximumResources": 10000,
        "timeoutSeconds": 300,
    }
    result = {
        "format": 1,
        "artifactId": body["artifactId"],
        "artifactDigest": "1" * 64,
        "sourceDigest": "2" * 64,
        "captureDigest": "3" * 64,
        "workloadDigest": "4" * 64,
        "observedAt": f.result["observedAt"],
        "itemCount": NvkeInstallPlan.objects.get(guid=body["planId"]).summary["itemCount"],
        "resourceCount": 2,
        "imageUseCount": 3,
        "imageReferenceCount": 1,
        "issueCount": 0,
        "executionState": workloads.protocol.STATE,
        "continuationAuthorized": False,
    }
    calls = []

    def invoke(kind, job, *, intent, body, chart_artifact_id, chart_artifact_digest):
        assert job == str(f.original.guid) and intent == f.original.request_digest
        assert chart_artifact_id == f.tree.body["chartArtifactId"]
        assert chart_artifact_digest == f.tree.body["chartArtifactDigest"]
        assert NvkeInstallPlanWorkload.objects.filter(guid=body["artifactId"]).exists()
        calls.append((kind, copy.deepcopy(body)))
        return copy.deepcopy(result)

    monkeypatch.setattr(
        OperatorAgentClient,
        "prepare_install_plan_workload",
        lambda self, job, **kw: invoke("prepare", job, **kw),
    )
    monkeypatch.setattr(
        OperatorAgentClient, "install_plan_workload", lambda self, job, **kw: invoke("read", job, **kw)
    )
    return SimpleNamespace(
        parent=f,
        original=f.original,
        state=f.state,
        body=body,
        result=result,
        invoke=invoke,
        calls=calls,
        review={key: value for key, value in f.review.items() if key not in {"item", "ordinal"}},
    )


def test_durable_encrypted_intent_read_and_explicit_retry_preserve_original_and_tree(workload_fixture):
    f = workload_fixture
    before = list(NvkeInstallPlanTree.all_objects.order_by("pk").values())
    with context(f.state.world):
        result = workloads.prepare(body=f.body, **f.review)
        assert workloads.read(artifact_id=f.body["artifactId"], **f.review) == result
        assert workloads.recover_preparation(artifact_id=f.body["artifactId"], **f.review) == result
    row = NvkeInstallPlanWorkload.objects.get(guid=f.body["artifactId"])
    assert workloads.outbox.canonical(f.body) not in bytes(row.input_ciphertext)
    assert decrypt(
        EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))
    ) == workloads.outbox.canonical(f.body)
    assert result["metadata"] == f.result and row.status == "prepared"
    assert "contextBindings" not in str(result) and "contextId" not in str(result)
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert set(f.calls[1][1]) == workloads.protocol.BASE
    assert list(NvkeInstallPlanTree.all_objects.order_by("pk").values()) == before


def test_lost_prepare_reply_remains_partial_until_explicit_historical_read(workload_fixture, monkeypatch):
    f = workload_fixture

    def lost(_client, job, **kwargs):
        f.invoke("prepare", job, **kwargs)
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost reply", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_workload", lost)
    with context(f.state.world):
        with pytest.raises(NvkeError) as error:
            workloads.prepare(body=f.body, **f.review)
        assert error.value.ambiguous
        assert NvkeInstallPlanWorkload.objects.get(guid=f.body["artifactId"]).status == "preparing"
        assert [kind for kind, _ in f.calls] == ["prepare"]
        assert workloads.read(artifact_id=f.body["artifactId"], **f.review)["status"] == "prepared"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


@pytest.mark.parametrize(
    "change",
    [
        {"timeoutSeconds": 299},
        {"maximumResources": 9999},
        {"maximumBytes": "33554432"},
    ],
)
def test_same_uuid_cannot_change_private_capture_intent(workload_fixture, change):
    f = workload_fixture
    with context(f.state.world):
        workloads.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            workloads.prepare(body={**f.body, **change}, **f.review)
    assert error.value.code == "CONFLICT" and len(f.calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"credentials": "PRIVATE_IMAGE"},
        {"continuationAuthorized": True},
        {"workloadDigest": "invalid"},
        {"resourceCount": 10001},
        {"artifactId": str(uuid4())},
    ],
)
def test_invalid_or_private_peer_evidence_is_not_published(workload_fixture, change):
    f = workload_fixture
    f.result.update(change)
    with context(f.state.world), pytest.raises(NvkeError) as error:
        workloads.prepare(body=f.body, **f.review)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE_IMAGE" not in str(error.value)
    row = NvkeInstallPlanWorkload.objects.get(guid=f.body["artifactId"])
    assert row.status == "preparing" and row.summary == {}


def test_parent_change_during_capture_cannot_publish(workload_fixture, monkeypatch):
    f = workload_fixture

    def drift(_client, job, **kwargs):
        result = f.invoke("prepare", job, **kwargs)
        NvkeInstallPlanTree.objects.filter(guid=f.body["treeId"]).update(version=F("version") + 1)
        return result

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_workload", drift)
    with context(f.state.world), pytest.raises(NvkeError) as error:
        workloads.prepare(body=f.body, **f.review)
    assert error.value.code == "SOURCE_CHANGED"
    assert NvkeInstallPlanWorkload.objects.get(guid=f.body["artifactId"]).status == "preparing"


def test_tampered_ciphertext_refuses_private_retry_before_peer_call(workload_fixture):
    f = workload_fixture
    with context(f.state.world):
        workloads.prepare(body=f.body, **f.review)
        NvkeInstallPlanWorkload.objects.filter(guid=f.body["artifactId"]).update(
            input_ciphertext=b"PRIVATE_TAMPER"
        )
        with pytest.raises(NvkeError) as error:
            workloads.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
    assert error.value.code == "PREPARATION_UNAVAILABLE" and len(f.calls) == 1


def test_image_uuid_reserves_workload_preparation(workload_fixture, image_fixture):  # noqa: F811
    f, image = workload_fixture, image_fixture
    with context(f.state.world):
        workloads.images.prepare(body=image.body, **image.review)
        with pytest.raises(NvkeError) as error:
            workloads.prepare(body={**f.body, "artifactId": image.body["artifactId"]}, **f.review)
    assert error.value.code == "CONFLICT"
    assert not f.calls


def test_workload_uuid_reserves_image_preparation(workload_fixture, image_fixture):  # noqa: F811
    f, image = workload_fixture, image_fixture
    with context(f.state.world):
        workloads.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            workloads.images.prepare(body={**image.body, "artifactId": f.body["artifactId"]}, **image.review)
    assert error.value.code == "CONFLICT"
    assert not image.calls


def test_soft_deleted_context_prevents_workload_collection(workload_fixture):
    f = workload_fixture
    workloads.NvkeInstallPlanContext.objects.filter(guid=f.parent.body["artifactId"]).update(
        deleted_at=timezone.now()
    )
    with context(f.state.world), pytest.raises(NvkeError) as error:
        workloads.prepare(body=f.body, **f.review)
    assert error.value.code == "SOURCE_CHANGED"
    assert not f.calls
