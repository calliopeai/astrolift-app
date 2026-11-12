import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db.models import F
from django.utils import timezone
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


def review_input(f):
    from astrolift_clusters.tests.test_nvke_install_plan_contexts import review_input as parent_review

    return {key: value for key, value in parent_review(f.parent).items() if key not in {"item", "ordinal"}}


FIELDS = "ok errors{code} data{id version status sourceKind planId planDigest chartArtifactId chartArtifactDigest treeId treeDigest artifactDigest sourceDigest captureDigest rootDigest bytes blobCount observedAt executionState continuationAuthorized}"


def test_typed_schema_prepare_review_retry_and_private_audit(image_fixture):
    import json

    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    f = image_fixture
    action = "prepareNvkeOperatorInstallPlanImage"
    response = execute(
        f.state.world,
        "mutation($alias:PrepareNvkeOperatorInstallPlanImageInput!){"
        + action
        + "(input:$alias){"
        + FIELDS
        + "}}",
        {"alias": {**review_input(f), "requestText": json.dumps(f.body)}},
    )
    assert response.errors is None and response.data[action]["ok"]
    result = response.data[action]["data"]
    assert result["id"] == f.body["artifactId"]
    assert result["sourceKind"] == f.body.get("sourceKind", "registry")
    assert result["planDigest"] == f.body["planDigest"] and result["treeDigest"] == f.body["treeDigest"]
    assert result["rootDigest"] == f.result["rootDigest"] and not result["continuationAuthorized"]
    for action in ("reviewNvkeOperatorInstallPlanImage", "recoverNvkeOperatorInstallPlanImagePreparation"):
        response = execute(
            f.state.world,
            "mutation($v:NvkeOperatorInstallPlanImageReviewInput!){" + action + "(input:$v){" + FIELDS + "}}",
            {"v": {**review_input(f), "artifactId": f.body["artifactId"]}},
        )
        assert (
            response.errors is None
            and response.data[action]["ok"]
            and response.data[action]["data"] == result
        )
    audit = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_image_prepare").latest(
        "timestamp"
    )
    assert f.body["reference"] not in str(audit.variables) and f.body["maximumBytes"] not in str(
        audit.variables
    )
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]


def test_rejected_body_and_denied_actor_are_redacted_before_service_mutation(image_fixture):
    import json

    from django.utils import timezone

    from astrolift_clusters.tests.test_nvke_schema import execute
    from astrolift_identity.models import RoleBinding
    from core.schema.audit import MutationAuditLog

    f = image_fixture
    action = "prepareNvkeOperatorInstallPlanImage"
    query = (
        "mutation($renamed:PrepareNvkeOperatorInstallPlanImageInput!){"
        + action
        + "(input:$renamed){ok errors{code}}}"
    )
    for text in (json.dumps({**f.body, "credentials": "PRIVATE_IMAGE"}), "invalid PRIVATE_IMAGE"):
        response = execute(f.state.world, query, {"renamed": {**review_input(f), "requestText": text}})
        assert response.errors is None and not response.data[action]["ok"]
        audit = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_image_prepare").latest(
            "timestamp"
        )
        assert "PRIVATE_IMAGE" not in str(audit.variables)
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    response = execute(
        f.state.world, query, {"renamed": {**review_input(f), "requestText": json.dumps(f.body)}}
    )
    assert response.errors is None and not response.data[action]["ok"]
    assert not NvkeInstallPlanImage.objects.exists() and not f.calls


def test_other_tenant_cannot_review_image_evidence(image_fixture):
    from astrolift_clusters.tests.test_nvke_sites import world as world_fixture
    from core.permissions import PermissionDenied

    f = image_fixture
    with context(f.state.world):
        images.prepare(body=f.body, **f.review)
    other = world_fixture.__wrapped__()
    with context(other), pytest.raises((NvkeError, PermissionDenied)):
        images.read(artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": other.user.pk})
    assert [kind for kind, _ in f.calls] == ["prepare"]


def test_another_current_admin_can_read_but_cannot_retry_original_private_intent(image_fixture):
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import Member, RoleBinding
    from core.permissions import Permission
    from core.tests.utils.scope_world import bind_role

    f = image_fixture
    with context(f.state.world):
        images.prepare(body=f.body, **f.review)
    caller = get_user_model().objects.create(username="image-admin-" + uuid4().hex, is_active=True)
    Member.objects.create(user=caller, scope_kind="ORG", scope_id=f.state.world.org.pk)
    bind_role(
        caller,
        permissions=[Permission.NVKE_ADMIN, Permission.NVKE_READ],
        kind="ORG",
        scope_id=f.state.world.org.pk,
        slug="image-admin-" + uuid4().hex,
    )
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    with context(SimpleNamespace(org=f.state.world.org, user=caller)):
        assert (
            images.read(artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": caller.pk})[
                "metadata"
            ]
            == f.result
        )
        with pytest.raises(NvkeError) as error:
            images.recover_preparation(
                artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": caller.pk}
            )
        assert error.value.code == "PRECONDITION"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_new_token_can_read_but_cannot_repeat_original_private_intent(image_fixture):
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    f = image_fixture

    def token():
        issued = mint_token()
        return ApiToken.objects.create(
            user=f.state.world.user,
            organization=f.state.world.org,
            name="chart token",
            token_hash=issued.token_hash,
            token_last_4=issued.last4,
            scopes=["admin", "read"],
        )

    original, replacement = token(), token()
    mark = set_current_api_token(original)
    try:
        with context(f.state.world):
            images.prepare(body=f.body, **f.review)
    finally:
        reset_current_api_token(mark)
    ApiToken.objects.filter(pk=original.pk).update(is_revoked=True)
    mark = set_current_api_token(replacement)
    try:
        with context(f.state.world):
            assert images.read(artifact_id=f.body["artifactId"], **f.review)["metadata"] == f.result
            with pytest.raises(NvkeError) as error:
                images.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
            assert error.value.code == "PRECONDITION"
    finally:
        reset_current_api_token(mark)
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_token_revoked_during_remote_prepare_cannot_publish(image_fixture, monkeypatch):
    from astrolift_clusters.nvke_sites import NvkeSiteError
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken
    from core.permissions import PermissionDenied

    f = image_fixture
    issued = mint_token()
    token = ApiToken.objects.create(
        user=f.state.world.user,
        organization=f.state.world.org,
        name="chart revoked token",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["admin", "read"],
    )

    def revoked(*_a, **_k):
        ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
        return copy.deepcopy(f.result)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_image", revoked)
    mark = set_current_api_token(token)
    try:
        with context(f.state.world), pytest.raises((PermissionDenied, NvkeSiteError)) as error:
            images.prepare(body=f.body, **f.review)
        if isinstance(error.value, NvkeSiteError):
            assert error.value.code == "AUTHORITY_REVOKED"
    finally:
        reset_current_api_token(mark)
    assert NvkeInstallPlanImage.objects.get(guid=f.body["artifactId"]).summary == {}


@pytest.fixture
def local_image_fixture(image_fixture):
    f = image_fixture
    f.body.pop("policy")
    f.body.pop("maximumCalls")
    f.body.update(sourceKind="local", reference="private.example.test/local@" + f.result["rootDigest"])
    f.result["sourceKind"] = "local"
    return f


def test_local_image_typed_api_reports_kind_and_preserves_encrypted_intent(local_image_fixture):
    test_typed_schema_prepare_review_retry_and_private_audit(local_image_fixture)
    f = local_image_fixture
    read_body = next(body for kind, body in f.calls if kind == "read")
    assert read_body["sourceKind"] == "local"
    assert "policy" not in read_body and "maximumCalls" not in read_body
    row = NvkeInstallPlanImage.objects.get(guid=f.body["artifactId"])
    assert row.summary["sourceKind"] == "local"


def test_local_image_cannot_replace_its_kind_after_preparation(local_image_fixture):
    f = local_image_fixture
    with context(f.state.world):
        images.prepare(body=f.body, **f.review)
        changed = {key: value for key, value in f.body.items() if key != "sourceKind"}
        changed.update(policy="reviewed", maximumCalls=64)
        with pytest.raises(NvkeError):
            images.prepare(body=changed, **f.review)
    assert [kind for kind, _ in f.calls] == ["prepare"]
