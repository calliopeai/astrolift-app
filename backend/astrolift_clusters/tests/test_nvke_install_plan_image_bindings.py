import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_image_bindings as bindings
from astrolift_clusters.models import (
    NvkeInstallPlanImage,
    NvkeInstallPlanImageBinding,
    NvkeInstallPlanWorkload,
)
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
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


@pytest.fixture
def binding_fixture(linked_image_fixture, monkeypatch):  # noqa: F811
    f = linked_image_fixture
    with context(f.state.world):
        bindings.images.prepare(body=f.body, **f.review)
    image = NvkeInstallPlanImage.objects.get(guid=f.body["artifactId"])
    body = {
        "format": 1,
        "artifactId": str(uuid4()),
        **{
            key: f.body[key]
            for key in ("planId", "planDigest", "treeId", "treeDigest", "workloadId", "workloadDigest")
        },
        "imageBindings": {
            f.body["reference"]: {
                "sourceKind": "registry",
                "artifactId": str(image.guid),
                "artifactDigest": image.summary["artifactDigest"],
            }
        },
        "maximumImageBytes": "134217728",
        "maximumBytes": "1048576",
        "timeoutSeconds": 300,
    }
    result = {
        "format": 1,
        "artifactId": body["artifactId"],
        "artifactDigest": "1" * 64,
        "sourceDigest": "2" * 64,
        "captureDigest": "3" * 64,
        "bindingDigest": "4" * 64,
        "observedAt": f.result["observedAt"],
        "imageCount": 1,
        "imageUseCount": f.workload.summary["imageUseCount"],
        "importCount": 0,
        "rewriteCount": 1,
        "verifiedImageBytes": f.result["bytes"],
        "workloadIssueCount": f.workload.summary["issueCount"],
        "executionState": bindings.protocol.STATE,
        "continuationAuthorized": False,
    }
    calls = []

    def invoke(kind, job, **kwargs):
        assert job == str(f.original.guid)
        assert kwargs["intent"] == f.original.request_digest
        assert NvkeInstallPlanImageBinding.objects.filter(guid=body["artifactId"]).exists()
        calls.append((kind, copy.deepcopy(kwargs["body"])))
        return copy.deepcopy(result)

    monkeypatch.setattr(
        OperatorAgentClient,
        "prepare_install_plan_image_binding",
        lambda self, job, **kw: invoke("prepare", job, **kw),
    )
    monkeypatch.setattr(
        OperatorAgentClient, "install_plan_image_binding", lambda self, job, **kw: invoke("read", job, **kw)
    )
    return SimpleNamespace(
        image=f,
        state=f.state,
        original=f.original,
        body=body,
        result=result,
        review=f.review,
        calls=calls,
        invoke=invoke,
    )


def test_encrypted_intent_explicit_recovery_and_immutable_public_summary(binding_fixture):
    f = binding_fixture
    before = (
        list(NvkeInstallPlanWorkload.all_objects.values()),
        list(NvkeInstallPlanImage.all_objects.values()),
    )
    with context(f.state.world):
        result = bindings.prepare(body=f.body, **f.review)
        assert bindings.read(artifact_id=f.body["artifactId"], **f.review) == result
        assert bindings.recover_preparation(artifact_id=f.body["artifactId"], **f.review) == result
    row = NvkeInstallPlanImageBinding.objects.get(guid=f.body["artifactId"])
    assert bindings.outbox.canonical(f.body) not in bytes(row.input_ciphertext)
    assert decrypt(
        EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))
    ) == bindings.outbox.canonical(f.body)
    assert result["metadata"] == f.result and result["workload_id"] == f.body["workloadId"]
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert set(f.calls[1][1]) == bindings.protocol.BASE
    assert "imageBindings" not in str(result) and f.image.body["reference"] not in str(result)
    assert before == (
        list(NvkeInstallPlanWorkload.all_objects.values()),
        list(NvkeInstallPlanImage.all_objects.values()),
    )
    row.summary = {**row.summary, "verifiedImageBytes": "9999"}
    with pytest.raises(ValueError):
        row.save()


def test_lost_reply_keeps_private_intent_for_read_without_automatic_replay(binding_fixture, monkeypatch):
    f = binding_fixture

    def lost(_client, job, **kwargs):
        f.invoke("prepare", job, **kwargs)
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost reply", ambiguous=True)

    with context(f.state.world):
        with monkeypatch.context() as patch:
            patch.setattr(OperatorAgentClient, "prepare_install_plan_image_binding", lost)
            with pytest.raises(NvkeError):
                bindings.prepare(body=f.body, **f.review)
        row = NvkeInstallPlanImageBinding.objects.get(guid=f.body["artifactId"])
        assert row.status == "preparing" and row.summary == {}
        assert bindings.read(artifact_id=f.body["artifactId"], **f.review)["status"] == "prepared"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


@pytest.mark.parametrize("change", [{"maximumBytes": "1048577"}, {"maximumImageBytes": "134217729"}])
def test_existing_identity_cannot_change_private_intent(binding_fixture, change):
    f = binding_fixture
    with context(f.state.world):
        bindings.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            bindings.prepare(body={**f.body, **change}, **f.review)
    assert error.value.code == "CONFLICT" and len(f.calls) == 1


@pytest.mark.parametrize(
    "key,value", [("artifactDigest", "9" * 64), ("sourceKind", "local"), ("artifactId", str(uuid4()))]
)
def test_unavailable_or_wrong_image_proof_refuses_before_intent(binding_fixture, key, value):
    f = binding_fixture
    body = copy.deepcopy(f.body)
    next(iter(body["imageBindings"].values()))[key] = value
    with context(f.state.world), pytest.raises(NvkeError):
        bindings.prepare(body=body, **f.review)
    assert not f.calls and not NvkeInstallPlanImageBinding.objects.exists()


def test_workload_drift_and_selected_image_deletion_prevent_publication(binding_fixture, monkeypatch):
    f = binding_fixture

    def drift(_client, job, **kwargs):
        result = f.invoke("prepare", job, **kwargs)
        NvkeInstallPlanImage.all_objects.filter(guid=f.image.body["artifactId"]).update(
            deleted_at=timezone.now()
        )
        return result

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_image_binding", drift)
    with context(f.state.world), pytest.raises(NvkeError):
        bindings.prepare(body=f.body, **f.review)
    assert NvkeInstallPlanImageBinding.objects.get(guid=f.body["artifactId"]).summary == {}


def test_workload_review_is_required_and_bound_to_private_body(binding_fixture):
    f = binding_fixture
    for review, body in (
        (
            {key: value for key, value in f.review.items() if key not in bindings.images.SOURCE_FIELDS[-3:]},
            f.body,
        ),
        ({**f.review, "expected_workload_version": 999}, f.body),
        (f.review, {**f.body, "workloadDigest": "9" * 64}),
    ):
        with context(f.state.world), pytest.raises(NvkeError):
            bindings.prepare(body=body, **review)
    assert not f.calls and not NvkeInstallPlanImageBinding.objects.exists()


def test_typed_controls_keep_private_selection_out_of_audits_and_enforce_permission(binding_fixture):
    f = binding_fixture
    source = {
        **review_input(f.image),
        "workloadId": f.body["workloadId"],
        "expectedWorkloadVersion": f.image.workload.version,
        "workloadDigest": f.body["workloadDigest"],
    }
    first = None
    for action in (
        "prepareNvkeOperatorInstallPlanImageBinding",
        "reviewNvkeOperatorInstallPlanImageBinding",
        "recoverNvkeOperatorInstallPlanImageBindingPreparation",
    ):
        prepare = action.startswith("prepare")
        input_type = (
            "PrepareNvkeOperatorInstallPlanImageBindingInput"
            if prepare
            else "NvkeOperatorInstallPlanImageBindingReviewInput"
        )
        response = execute(
            f.state.world,
            "mutation($v:"
            + input_type
            + "!){"
            + action
            + "(input:$v){ok errors{code} data{id version workloadId bindingDigest imageCount imageUseCount verifiedImageBytes continuationAuthorized}}}",
            {
                "v": {
                    **source,
                    **(
                        {"requestText": json.dumps(f.body)}
                        if prepare
                        else {"artifactId": f.body["artifactId"]}
                    ),
                }
            },
        )
        assert response.errors is None and response.data[action]["ok"]
        result = response.data[action]["data"]
        assert result["imageCount"] == 1 and not result["continuationAuthorized"]
        if first is not None:
            assert result == first
        first = result
    audit = MutationAuditLog.objects.filter(
        operation="nvke.operator_install_plan_image_binding_prepare"
    ).latest("timestamp")
    assert f.image.body["reference"] not in str(audit.variables) and "imageBindings" not in str(
        audit.variables
    )
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    response = execute(
        f.state.world,
        "mutation($v:NvkeOperatorInstallPlanImageBindingReviewInput!){reviewNvkeOperatorInstallPlanImageBinding(input:$v){ok errors{code}}}",
        {"v": {**source, "artifactId": f.body["artifactId"]}},
    )
    assert response.errors is None and not response.data["reviewNvkeOperatorInstallPlanImageBinding"]["ok"]
    assert len(f.calls) == 3


def test_binding_identity_reserves_existing_workload_preparation(binding_fixture, workload_fixture):  # noqa: F811
    f = binding_fixture
    with context(f.state.world):
        bindings.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            bindings.workloads.prepare(
                body={**workload_fixture.body, "artifactId": f.body["artifactId"]}, **workload_fixture.review
            )
    assert error.value.code == "CONFLICT"


def test_rejected_private_request_text_is_redacted_even_when_aliased(binding_fixture):
    f = binding_fixture
    source = {
        **review_input(f.image),
        "workloadId": f.body["workloadId"],
        "expectedWorkloadVersion": f.image.workload.version,
        "workloadDigest": f.body["workloadDigest"],
    }
    response = execute(
        f.state.world,
        "mutation($renamed:PrepareNvkeOperatorInstallPlanImageBindingInput!){x:prepareNvkeOperatorInstallPlanImageBinding(input:$renamed){ok errors{code}}}",
        {"renamed": {**source, "requestText": "INVALID PRIVATE_IMAGE_BINDING"}},
    )
    assert response.errors is None and not response.data["x"]["ok"]
    audit = MutationAuditLog.objects.filter(
        operation="nvke.operator_install_plan_image_binding_prepare"
    ).latest("timestamp")
    assert "PRIVATE_IMAGE_BINDING" not in str(audit.variables)
    assert not f.calls and not NvkeInstallPlanImageBinding.objects.exists()
