import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db.models import F
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_images as images
from astrolift_clusters.models import NvkeInstallPlanImage, NvkeInstallPlanTree
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
from astrolift_clusters.tests.test_nvke_sites import context
from core.secrets import EncryptedSecret, decrypt

pytestmark = pytest.mark.django_db


@pytest.fixture
def image_fixture(context_fixture, monkeypatch):  # noqa: F811
    f = context_fixture
    body = {
        "format": 1,
        "artifactId": str(uuid4()),
        **{key: f.body[key] for key in ("planId", "planDigest", "treeId", "treeDigest")},
        "reference": "private.example.test/owned:v1",
        "policy": "reviewed",
        "maximumBytes": "268435456",
        "maximumCalls": 64,
        "timeoutSeconds": 300,
    }
    result = {
        "format": 1,
        "artifactId": body["artifactId"],
        "artifactDigest": "1" * 64,
        "sourceDigest": "2" * 64,
        "captureDigest": "3" * 64,
        "rootDigest": "sha256:" + "4" * 64,
        "observedAt": f.result["observedAt"],
        "bytes": "67108864",
        "blobCount": 6,
        "executionState": images.protocol.STATE,
        "continuationAuthorized": False,
    }
    calls = []

    def invoke(kind, job, *, intent, body, chart_artifact_id, chart_artifact_digest):
        assert job == str(f.original.guid) and intent == f.original.request_digest
        assert chart_artifact_id == f.tree.body["chartArtifactId"]
        assert chart_artifact_digest == f.tree.body["chartArtifactDigest"]
        assert NvkeInstallPlanImage.objects.filter(guid=body["artifactId"]).exists()
        calls.append((kind, copy.deepcopy(body)))
        return copy.deepcopy(result)

    monkeypatch.setattr(
        OperatorAgentClient,
        "prepare_install_plan_image",
        lambda self, job, **kw: invoke("prepare", job, **kw),
    )
    monkeypatch.setattr(
        OperatorAgentClient, "install_plan_image", lambda self, job, **kw: invoke("read", job, **kw)
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


def test_durable_encrypted_intent_read_and_explicit_retry_preserve_original_and_tree(image_fixture):
    f = image_fixture
    before = list(NvkeInstallPlanTree.all_objects.order_by("pk").values())
    with context(f.state.world):
        result = images.prepare(body=f.body, **f.review)
        assert images.read(artifact_id=f.body["artifactId"], **f.review) == result
        assert images.recover_preparation(artifact_id=f.body["artifactId"], **f.review) == result
    row = NvkeInstallPlanImage.objects.get(guid=f.body["artifactId"])
    assert images.outbox.canonical(f.body) not in bytes(row.input_ciphertext)
    assert decrypt(
        EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))
    ) == images.outbox.canonical(f.body)
    assert result["metadata"] == f.result and row.status == "prepared"
    assert "reference" not in str(result) and "policy" not in str(result)
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert set(f.calls[1][1]) == images.protocol.BASE
    assert list(NvkeInstallPlanTree.all_objects.order_by("pk").values()) == before


def test_lost_prepare_reply_remains_partial_until_explicit_historical_read(image_fixture, monkeypatch):
    f = image_fixture

    def lost(_client, job, **kwargs):
        f.invoke("prepare", job, **kwargs)
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost reply", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_image", lost)
    with context(f.state.world):
        with pytest.raises(NvkeError) as error:
            images.prepare(body=f.body, **f.review)
        assert error.value.ambiguous
        assert NvkeInstallPlanImage.objects.get(guid=f.body["artifactId"]).status == "preparing"
        assert [kind for kind, _ in f.calls] == ["prepare"]
        assert images.read(artifact_id=f.body["artifactId"], **f.review)["status"] == "prepared"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


@pytest.mark.parametrize(
    "change",
    [
        {"maximumCalls": 65},
        {"policy": "other"},
        {"reference": "private.example.test/other:v1"},
    ],
)
def test_same_uuid_cannot_change_private_capture_intent(image_fixture, change):
    f = image_fixture
    with context(f.state.world):
        images.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            images.prepare(body={**f.body, **change}, **f.review)
    assert error.value.code == "CONFLICT" and len(f.calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"credentials": "PRIVATE_IMAGE"},
        {"continuationAuthorized": True},
        {"rootDigest": "invalid"},
        {"bytes": "268435457"},
        {"artifactId": str(uuid4())},
    ],
)
def test_invalid_or_private_peer_evidence_is_not_published(image_fixture, change):
    f = image_fixture
    f.result.update(change)
    with context(f.state.world), pytest.raises(NvkeError) as error:
        images.prepare(body=f.body, **f.review)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE_IMAGE" not in str(error.value)
    row = NvkeInstallPlanImage.objects.get(guid=f.body["artifactId"])
    assert row.status == "preparing" and row.summary == {}


def test_parent_change_during_capture_cannot_publish(image_fixture, monkeypatch):
    f = image_fixture

    def drift(_client, job, **kwargs):
        result = f.invoke("prepare", job, **kwargs)
        NvkeInstallPlanTree.objects.filter(guid=f.body["treeId"]).update(version=F("version") + 1)
        return result

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_image", drift)
    with context(f.state.world), pytest.raises(NvkeError) as error:
        images.prepare(body=f.body, **f.review)
    assert error.value.code == "SOURCE_CHANGED"
    assert NvkeInstallPlanImage.objects.get(guid=f.body["artifactId"]).status == "preparing"


def test_tampered_ciphertext_refuses_private_retry_before_peer_call(image_fixture):
    f = image_fixture
    with context(f.state.world):
        images.prepare(body=f.body, **f.review)
        NvkeInstallPlanImage.objects.filter(guid=f.body["artifactId"]).update(
            input_ciphertext=b"PRIVATE_TAMPER"
        )
        with pytest.raises(NvkeError) as error:
            images.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
    assert error.value.code == "PREPARATION_UNAVAILABLE" and len(f.calls) == 1
