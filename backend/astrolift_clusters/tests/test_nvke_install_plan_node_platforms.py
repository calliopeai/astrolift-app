"""Real PostgreSQL/GraphQL controls with controlled SDK replies; no admission."""

import copy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_node_platforms as artifacts
from astrolift_clusters.models import NvkeInstallPlanImageBinding, NvkeInstallPlanNodePlatforms
from astrolift_clusters.tests.test_nvke_install_plan_immutable import (
    _no_opensearch_profile_index,  # noqa: F401
    agent,  # noqa: F401
    binding_fixture,  # noqa: F401
    chart_fixture,  # noqa: F401
    configured,  # noqa: F401
    context_fixture,  # noqa: F401
    graphql_source,
    image_fixture,  # noqa: F401
    linked_image_fixture,  # noqa: F401
    prepared_fixture,  # noqa: F401
    tree_fixture,  # noqa: F401
    workload_fixture,  # noqa: F401
    world,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_identity.models import RoleBinding
from core.permissions import PermissionDenied
from core.schema.audit import MutationAuditLog
from core.secrets import EncryptedSecret, decrypt

pytestmark = pytest.mark.django_db


@pytest.fixture
def platform_fixture(binding_fixture, monkeypatch):  # noqa: F811
    f = binding_fixture
    with context(f.state.world):
        artifacts.bindings.prepare(body=f.body, **f.review)
    binding = NvkeInstallPlanImageBinding.objects.get(guid=f.body["artifactId"])
    review = {
        **f.review,
        "binding_id": str(binding.guid),
        "expected_binding_version": binding.version,
        "binding_digest": binding.summary["bindingDigest"],
    }
    body = {
        **{k: f.body[k] for k in ("format", "planId", "planDigest", "treeId", "treeDigest")},
        "artifactId": str(uuid4()),
        "bindingId": str(binding.guid),
        "bindingDigest": binding.summary["bindingDigest"],
        "nodePlatforms": {
            "private-node": {
                "uid": "private-uid",
                "runtimeProfile": "containerd/v2.3.0",
                "platform": {"os": "linux", "architecture": "arm64"},
            }
        },
        **{k: str(min(v, 2**53 + 1)) for k, v in artifacts.protocol.BYTE_LIMITS.items()},
        "maximumCalls": 512,
        "maximumPairs": 10000,
        "timeoutSeconds": 300,
    }
    calls, responses = [], {}

    def invoke(kind, job, **kwargs):
        value = kwargs["body"]
        artifact_id = value["artifactId"]
        assert NvkeInstallPlanNodePlatforms.objects.filter(guid=artifact_id).exists()
        assert job == str(f.original.guid)
        assert kwargs["workload_id"] == f.body["workloadId"]
        assert kwargs["workload_digest"] == f.body["workloadDigest"]
        calls.append((kind, copy.deepcopy(value)))
        if artifact_id not in responses:
            references = binding.summary["imageCount"]
            responses[artifact_id] = {
                "format": 1,
                "artifactId": artifact_id,
                "artifactDigest": hashlib.sha256(artifact_id.encode()).hexdigest(),
                "sourceDigest": "5" * 64,
                "captureDigest": "6" * 64,
                "platformDigest": "7" * 64,
                "observedAt": binding.summary["observedAt"],
                "nodeCount": 1,
                "contentCount": references,
                "imageReferenceCount": references,
                "pairCount": references,
                "issueCount": 4,
                "executionState": artifacts.protocol.STATE,
                "continuationAuthorized": False,
            }
        return copy.deepcopy(responses[artifact_id])

    for kind, prefix in [("prepare", "prepare_"), ("read", "")]:

        def call(_client, job, _kind=kind, **kwargs):
            return invoke(_kind, job, **kwargs)

        monkeypatch.setattr(OperatorAgentClient, prefix + "install_plan_node_platforms", call)
    return SimpleNamespace(
        binding=f, row=binding, review=review, body=body, calls=calls, responses=responses, invoke=invoke
    )


@pytest.mark.parametrize("runtime_profile", ["containerd/v2.3.0", "containerd/v2.3.4"])
def test_encrypted_intent_explicit_recovery_and_original_preservation(platform_fixture, runtime_profile):
    f = platform_fixture
    f.body["nodePlatforms"]["private-node"]["runtimeProfile"] = runtime_profile
    original = type(f.binding.original).all_objects.filter(pk=f.binding.original.pk).values().get()
    with context(f.binding.state.world):
        result = artifacts.prepare(body=f.body, **f.review)
        assert artifacts.read(artifact_id=result["id"], **f.review) == result
        assert artifacts.recover_preparation(artifact_id=result["id"], **f.review) == result
    row = NvkeInstallPlanNodePlatforms.objects.get(guid=result["id"])
    assert artifacts.outbox.canonical(f.body) not in bytes(row.input_ciphertext)
    assert decrypt(
        EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))
    ) == artifacts.outbox.canonical(f.body)
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert not result["metadata"]["continuationAuthorized"]
    assert "private-node" not in str(result) and "nodePlatforms" not in str(result)
    assert type(f.binding.original).all_objects.filter(pk=f.binding.original.pk).values().get() == original


def test_lost_reply_retains_intent_for_explicit_read(platform_fixture, monkeypatch):
    f = platform_fixture

    def lost(_client, job, **kwargs):
        f.invoke("prepare", job, **kwargs)
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost reply", ambiguous=True)

    with context(f.binding.state.world):
        with monkeypatch.context() as patch:
            patch.setattr(OperatorAgentClient, "prepare_install_plan_node_platforms", lost)
            with pytest.raises(NvkeError):
                artifacts.prepare(body=f.body, **f.review)
        row = NvkeInstallPlanNodePlatforms.objects.get(guid=f.body["artifactId"])
        assert row.status == "preparing" and row.summary == {}
        assert artifacts.read(artifact_id=str(row.guid), **f.review)["status"] == "prepared"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_changed_intent_and_stale_binding_refuse_before_io(platform_fixture):
    f = platform_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            artifacts.prepare(body={**f.body, "maximumCalls": 513}, **f.review)
        assert error.value.code == "CONFLICT"
        with pytest.raises(NvkeError) as error:
            artifacts.prepare(
                body={**f.body, "artifactId": str(uuid4())},
                **{**f.review, "expected_binding_version": f.row.version + 1},
            )
        assert error.value.code == "SOURCE_CHANGED"
    assert len(f.calls) == 1


@pytest.mark.parametrize("change", ["source", "continuation", "private", "count", "role"])
def test_mid_io_source_change_or_invalid_result_never_publishes(platform_fixture, monkeypatch, change):
    f = platform_fixture

    def changed(_client, job, **kwargs):
        result = f.invoke("prepare", job, **kwargs)
        if change == "source":
            NvkeInstallPlanImageBinding.objects.filter(pk=f.row.pk).update(version=f.row.version + 1)
        elif change == "role":
            RoleBinding.objects.filter(user=f.binding.state.world.user).update(deleted_at=timezone.now())
        elif change == "continuation":
            result["continuationAuthorized"] = True
        elif change == "private":
            result["nodes"] = ["private-node"]
        else:
            result["pairCount"] += 1
        return result

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_node_platforms", changed)
    with context(f.binding.state.world), pytest.raises((NvkeError, PermissionDenied)):
        artifacts.prepare(body=f.body, **f.review)
    row = NvkeInstallPlanNodePlatforms.objects.get(guid=f.body["artifactId"])
    assert row.status == "preparing" and row.summary == {} and len(f.calls) == 1


def test_node_identity_stays_reserved_against_other_preparation_after_deletion(platform_fixture):
    f = platform_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, **f.review)
        NvkeInstallPlanNodePlatforms.objects.get(guid=f.body["artifactId"]).soft_delete()
        with pytest.raises(NvkeError) as error:
            artifacts.bindings.prepare(
                body={**f.binding.body, "artifactId": f.body["artifactId"]}, **f.binding.review
            )
    assert error.value.code == "CONFLICT" and len(f.calls) == 1


def test_corrupt_private_intent_refuses_before_agent_read(platform_fixture):
    f = platform_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, **f.review)
        NvkeInstallPlanNodePlatforms.objects.filter(guid=f.body["artifactId"]).update(
            input_ciphertext=b"corrupt"
        )
        with pytest.raises(NvkeError) as error:
            artifacts.read(artifact_id=f.body["artifactId"], **f.review)
    assert error.value.code == "PREPARATION_UNAVAILABLE" and len(f.calls) == 1


def test_changed_prepared_result_is_never_replaced(platform_fixture):
    f = platform_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, **f.review)
        before = NvkeInstallPlanNodePlatforms.objects.get(guid=f.body["artifactId"]).summary
        f.responses[f.body["artifactId"]]["issueCount"] += 1
        with pytest.raises(NvkeError) as error:
            artifacts.read(artifact_id=f.body["artifactId"], **f.review)
    assert error.value.code == "SOURCE_CHANGED"
    assert NvkeInstallPlanNodePlatforms.objects.get(guid=f.body["artifactId"]).summary == before


def test_graphql_controls_are_redacted_audited_and_current_admin_only(platform_fixture):
    f = platform_fixture
    first = None
    for verb in ("prepare", "review", "recover"):
        action = verb + "NvkeOperatorInstallPlanNodePlatforms" + ("Preparation" if verb == "recover" else "")
        input_type = (
            "PrepareNvkeOperatorInstallPlanNodePlatformsInput"
            if verb == "prepare"
            else "NvkeOperatorInstallPlanNodePlatformsReviewInput"
        )
        fields = (
            {"requestText": json.dumps(f.body)} if verb == "prepare" else {"artifactId": f.body["artifactId"]}
        )
        response = execute(
            f.binding.state.world,
            "mutation($v:"
            + input_type
            + "!){"
            + action
            + "(input:$v){ok errors{code} data{id version bindingId platformDigest nodeCount pairCount continuationAuthorized}}}",
            {"v": {**graphql_source(f), **fields}},
        )
        assert response.errors is None
        assert response.data[action]["ok"], response.data
        result = response.data[action]["data"]
        assert result["continuationAuthorized"] is False
        if first is not None:
            assert result == first
        first = result
    audit = MutationAuditLog.objects.filter(
        operation="nvke.operator_install_plan_node_platforms_prepare"
    ).latest("timestamp")
    assert "private-node" not in str(audit.variables) and "nodePlatforms" not in str(audit.variables)
    RoleBinding.objects.filter(user=f.binding.state.world.user).update(deleted_at=timezone.now())
    for verb in ("prepare", "review", "recover"):
        action = verb + "NvkeOperatorInstallPlanNodePlatforms" + ("Preparation" if verb == "recover" else "")
        input_type = (
            "PrepareNvkeOperatorInstallPlanNodePlatformsInput"
            if verb == "prepare"
            else "NvkeOperatorInstallPlanNodePlatformsReviewInput"
        )
        fields = (
            {"requestText": json.dumps(f.body)} if verb == "prepare" else {"artifactId": f.body["artifactId"]}
        )
        response = execute(
            f.binding.state.world,
            "mutation($v:" + input_type + "!){" + action + "(input:$v){ok errors{code}}}",
            {"v": {**graphql_source(f), **fields}},
        )
        assert response.errors is None and not next(iter(response.data.values()))["ok"]
    assert len(f.calls) == 3


def test_aliased_invalid_private_request_is_redacted_before_parsing(platform_fixture):
    f = platform_fixture
    response = execute(
        f.binding.state.world,
        "mutation($renamed:PrepareNvkeOperatorInstallPlanNodePlatformsInput!){x:prepareNvkeOperatorInstallPlanNodePlatforms(input:$renamed){ok errors{code}}}",
        {"renamed": {**graphql_source(f), "requestText": "INVALID PRIVATE_PLATFORM_SCOPE"}},
    )
    assert response.errors is None and not response.data["x"]["ok"]
    audit = MutationAuditLog.objects.filter(
        operation="nvke.operator_install_plan_node_platforms_prepare"
    ).latest("timestamp")
    assert "PRIVATE_PLATFORM_SCOPE" not in str(audit.variables)
    assert not f.calls and not NvkeInstallPlanNodePlatforms.objects.filter(guid=f.body["artifactId"]).exists()
