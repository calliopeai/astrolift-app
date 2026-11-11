"""Real persistence/GraphQL fences with controlled SDK replies, not native dispatch."""

import copy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_immutable as artifacts
from astrolift_clusters.models import (
    NvkeInstallPlanImageBinding,
    NvkeInstallPlanImagePayload,
)
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_image_bindings import binding_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_workload_images import (
    agent,  # noqa: F401
    chart_fixture,  # noqa: F401
    configured,  # noqa: F401
    context_fixture,  # noqa: F401
    image_fixture,  # noqa: F401
    linked_image_fixture,  # noqa: F401
    prepared_fixture,  # noqa: F401
    review_input,
    tree_fixture,  # noqa: F401
    workload_fixture,  # noqa: F401
    world,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_identity.models import RoleBinding
from core.schema.audit import MutationAuditLog
from core.secrets import EncryptedSecret, decrypt

pytestmark = pytest.mark.django_db


@pytest.fixture(params=[False, True])
def immutable_fixture(binding_fixture, monkeypatch, request):  # noqa: F811
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
    common = {key: f.body[key] for key in ("format", "planId", "planDigest", "treeId", "treeDigest")}
    common.update({"bindingId": str(binding.guid), "bindingDigest": binding.summary["bindingDigest"]})
    calls, responses = [], {}
    plan = binding.tree.charts.plan

    def invoke(kind, tree, job, **kwargs):
        body = kwargs["body"]
        artifact_id = body["artifactId"]
        model = artifacts._model(tree)
        assert model.objects.filter(guid=artifact_id).exists()
        assert job == str(f.original.guid)
        assert kwargs["workload_id"] == f.body["workloadId"]
        assert kwargs["workload_digest"] == f.body["workloadDigest"]
        calls.append((kind, tree, copy.deepcopy(body)))
        if artifact_id not in responses:
            digest = hashlib.sha256(artifact_id.encode()).hexdigest()
            result = {
                "format": 1,
                "artifactId": artifact_id,
                "artifactDigest": digest,
                "sourceDigest": "5" * 64,
                "captureDigest": "6" * 64,
                "observedAt": binding.summary["observedAt"],
                "sourceObservedAt": binding.summary["observedAt"],
                "fileCount": 1,
                "executionState": artifacts.protocol.TREE_STATE if tree else artifacts.protocol.PAYLOAD_STATE,
                "continuationAuthorized": False,
            }
            if tree:
                skipped = plan.summary["plan"]["firstRemainingOrdinal"]
                result.update(
                    {
                        "executionDigest": "7" * 64,
                        "bytes": "128",
                        "phaseCount": plan.summary["itemCount"] - skipped,
                        "skippedPhaseCount": skipped,
                        "importCount": 0,
                    }
                )
            else:
                result.update(
                    {
                        "payloadDigest": "8" * 64,
                        "payloadBytes": "64",
                        "resourceCount": 1,
                        "imageUseCount": 1,
                        "rewriteCount": 1,
                    }
                )
            responses[artifact_id] = result
        return copy.deepcopy(responses[artifact_id])

    for tree, suffix in [(False, "image_payload"), (True, "immutable_execution_tree")]:
        for kind, prefix in [("prepare", "prepare_"), ("read", "")]:

            def call(_client, job, _kind=kind, _tree=tree, **kwargs):
                return invoke(_kind, _tree, job, **kwargs)

            monkeypatch.setattr(OperatorAgentClient, prefix + "install_plan_" + suffix, call)
    body = {**common, "artifactId": str(uuid4()), "maximumBytes": "1048576", "timeoutSeconds": 300}
    if request.param:
        selected = {}
        for number in range(plan.summary["itemCount"]):
            name = "NVKE_Phase_" + str(number)
            payload = {**body, "artifactId": str(uuid4()), "item": name, "maximumResources": 100}
            with context(f.state.world):
                result = artifacts.prepare(body=payload, **review)
            selected[name] = {
                "artifactId": result["id"],
                "artifactDigest": result["metadata"]["artifactDigest"],
            }
        body["payloadBindings"] = selected
    else:
        body.update({"item": "NVKE_Phase_0", "maximumResources": 100})
    calls.clear()
    return SimpleNamespace(
        binding=f,
        row=binding,
        review=review,
        body=body,
        tree=request.param,
        calls=calls,
        responses=responses,
        invoke=invoke,
        model=artifacts._model(request.param),
    )


def test_encrypted_intent_explicit_recovery_and_original_preservation(immutable_fixture):
    f = immutable_fixture
    original = type(f.binding.original).all_objects.filter(pk=f.binding.original.pk).values().get()
    with context(f.binding.state.world):
        result = artifacts.prepare(body=f.body, tree=f.tree, **f.review)
        assert artifacts.read(artifact_id=result["id"], tree=f.tree, **f.review) == result
        assert artifacts.recover_preparation(artifact_id=result["id"], tree=f.tree, **f.review) == result
    row = f.model.objects.get(guid=result["id"])
    assert artifacts.outbox.canonical(f.body) not in bytes(row.input_ciphertext)
    assert decrypt(
        EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))
    ) == artifacts.outbox.canonical(f.body)
    assert [c[0] for c in f.calls] == ["prepare", "read", "prepare"]
    assert result["metadata"]["continuationAuthorized"] is False
    assert "payloadBindings" not in str(result) and "item" not in result
    f.binding.original.refresh_from_db()
    assert type(f.binding.original).all_objects.filter(pk=f.binding.original.pk).values().get() == original


def test_lost_reply_retains_intent_for_explicit_read(immutable_fixture, monkeypatch):
    f = immutable_fixture
    method = (
        "prepare_install_plan_immutable_execution_tree" if f.tree else "prepare_install_plan_image_payload"
    )

    def lost(_client, job, **kw):
        f.invoke("prepare", f.tree, job, **kw)
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost reply", ambiguous=True)

    with context(f.binding.state.world):
        with monkeypatch.context() as patch:
            patch.setattr(OperatorAgentClient, method, lost)
            with pytest.raises(NvkeError):
                artifacts.prepare(body=f.body, tree=f.tree, **f.review)
        row = f.model.objects.get(guid=f.body["artifactId"])
        assert row.status == "preparing" and row.summary == {}
        assert artifacts.read(artifact_id=str(row.guid), tree=f.tree, **f.review)["status"] == "prepared"
    assert [c[0] for c in f.calls] == ["prepare", "read"]


def test_conflicting_intent_and_stale_binding_refuse_before_io(immutable_fixture):
    f = immutable_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, tree=f.tree, **f.review)
        with pytest.raises(NvkeError) as error:
            artifacts.prepare(body={**f.body, "maximumBytes": "1048577"}, tree=f.tree, **f.review)
        assert error.value.code == "CONFLICT"
        with pytest.raises(NvkeError) as error:
            artifacts.prepare(
                body={**f.body, "artifactId": str(uuid4())},
                tree=f.tree,
                **{**f.review, "expected_binding_version": f.row.version + 1},
            )
        assert error.value.code == "SOURCE_CHANGED"
    assert len(f.calls) == 1


def test_new_identity_is_reserved_against_old_preparation(immutable_fixture):
    f = immutable_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, tree=f.tree, **f.review)
        with pytest.raises(NvkeError) as error:
            artifacts.bindings.prepare(
                body={**f.binding.body, "artifactId": f.body["artifactId"]}, **f.binding.review
            )
    assert error.value.code == "CONFLICT"


@pytest.mark.parametrize("change", ["source", "response"])
def test_mid_io_source_change_or_invalid_summary_never_publishes(immutable_fixture, monkeypatch, change):
    f = immutable_fixture
    method = (
        "prepare_install_plan_immutable_execution_tree" if f.tree else "prepare_install_plan_image_payload"
    )

    def changed(_client, job, **kwargs):
        result = f.invoke("prepare", f.tree, job, **kwargs)
        if change == "source":
            NvkeInstallPlanImageBinding.objects.filter(pk=f.row.pk).update(version=f.row.version + 1)
        else:
            result["continuationAuthorized"] = True
        return result

    monkeypatch.setattr(OperatorAgentClient, method, changed)
    with context(f.binding.state.world), pytest.raises(NvkeError):
        artifacts.prepare(body=f.body, tree=f.tree, **f.review)
    row = f.model.objects.get(guid=f.body["artifactId"])
    assert row.status == "preparing" and row.summary == {}
    assert len(f.calls) == 1


@pytest.mark.parametrize("immutable_fixture", [True], indirect=True)
def test_selected_phase_deletion_prevents_execution_publication(immutable_fixture, monkeypatch):
    f = immutable_fixture
    choice = next(iter(f.body["payloadBindings"].values()))

    def deleted(_client, job, **kwargs):
        result = f.invoke("prepare", True, job, **kwargs)
        NvkeInstallPlanImagePayload.objects.get(guid=choice["artifactId"]).soft_delete()
        return result

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_immutable_execution_tree", deleted)
    with context(f.binding.state.world), pytest.raises(NvkeError):
        artifacts.prepare(body=f.body, tree=True, **f.review)
    row = f.model.objects.get(guid=f.body["artifactId"])
    assert row.status == "preparing" and row.summary == {}
    assert len(f.calls) == 1


def graphql_source(f):
    return {
        **review_input(f.binding.image),
        "workloadId": f.binding.body["workloadId"],
        "expectedWorkloadVersion": f.binding.image.workload.version,
        "workloadDigest": f.binding.body["workloadDigest"],
        "bindingId": str(f.row.guid),
        "expectedBindingVersion": f.row.version,
        "bindingDigest": f.row.summary["bindingDigest"],
    }


def test_graphql_controls_are_audited_redacted_and_admin_only(immutable_fixture):
    f = immutable_fixture
    suffix = "ImmutableExecutionTree" if f.tree else "ImagePayload"
    first = None
    for verb in ["prepare", "review", "recover"]:
        action = verb + "NvkeOperatorInstallPlan" + suffix + ("Preparation" if verb == "recover" else "")
        prepare = verb == "prepare"
        input_type = (
            "PrepareNvkeOperatorInstallPlanImmutableInput"
            if prepare
            else "NvkeOperatorInstallPlanImmutableReviewInput"
        )
        fields = {"requestText": json.dumps(f.body)} if prepare else {"artifactId": f.body["artifactId"]}
        response = execute(
            f.binding.state.world,
            "mutation($v:"
            + input_type
            + "!){"
            + action
            + "(input:$v){ok errors{code} data{id version bindingId bindingDigest continuationAuthorized}}}",
            {"v": {**graphql_source(f), **fields}},
        )
        assert response.errors is None
        assert response.data[action]["ok"], response.data
        result = response.data[action]["data"]
        assert result["continuationAuthorized"] is False
        if first is not None:
            assert result == first
        first = result
    operation = (
        "nvke.operator_install_plan_"
        + ("immutable_execution_tree" if f.tree else "image_payload")
        + "_prepare"
    )
    audit = MutationAuditLog.objects.filter(operation=operation).latest("timestamp")
    assert "payloadBindings" not in str(audit.variables) and "NVKE_Phase" not in str(audit.variables)
    RoleBinding.objects.filter(user=f.binding.state.world.user).update(deleted_at=timezone.now())
    response = execute(
        f.binding.state.world,
        "mutation($v:NvkeOperatorInstallPlanImmutableReviewInput!){reviewNvkeOperatorInstallPlan"
        + suffix
        + "(input:$v){ok errors{code}}}",
        {"v": {**graphql_source(f), "artifactId": f.body["artifactId"]}},
    )
    assert response.errors is None and not next(iter(response.data.values()))["ok"]
    assert len(f.calls) == 3


def test_aliased_invalid_request_is_redacted_before_parsing(immutable_fixture):
    f = immutable_fixture
    suffix = "ImmutableExecutionTree" if f.tree else "ImagePayload"
    response = execute(
        f.binding.state.world,
        "mutation($renamed:PrepareNvkeOperatorInstallPlanImmutableInput!){x:prepareNvkeOperatorInstallPlan"
        + suffix
        + "(input:$renamed){ok errors{code}}}",
        {"renamed": {**graphql_source(f), "requestText": "INVALID PRIVATE_IMMUTABLE_INPUT"}},
    )
    assert response.errors is None and not response.data["x"]["ok"]
    operation = (
        "nvke.operator_install_plan_"
        + ("immutable_execution_tree" if f.tree else "image_payload")
        + "_prepare"
    )
    audit = MutationAuditLog.objects.filter(operation=operation).latest("timestamp")
    assert "PRIVATE_IMMUTABLE_INPUT" not in str(audit.variables)
    assert not f.calls and not f.model.objects.filter(guid=f.body["artifactId"]).exists()
