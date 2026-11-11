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

from astrolift_clusters import nvke_install_plan_pull_secret_analysis as artifacts
from astrolift_clusters.models import NvkeInstallPlanImageBinding, NvkeInstallPlanPullSecretAnalysis
from astrolift_clusters.tests.test_nvke_install_plan_immutable import (
    _no_opensearch_profile_index,  # noqa: F401
    agent,  # noqa: F401
    binding_fixture,  # noqa: F401
    chart_fixture,  # noqa: F401
    configured,  # noqa: F401
    context_fixture,  # noqa: F401
    image_fixture,  # noqa: F401
    linked_image_fixture,  # noqa: F401
    prepared_fixture,  # noqa: F401
    tree_fixture,  # noqa: F401
    workload_fixture,  # noqa: F401
    world,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_install_plan_immutable import (
    graphql_source as base_graphql_source,
)
from astrolift_clusters.tests.test_nvke_install_plan_pull_authority import (
    pull_authority_fixture,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_identity.models import RoleBinding
from core.permissions import PermissionDenied
from core.schema.audit import MutationAuditLog
from core.secrets import EncryptedSecret, decrypt

pytestmark = pytest.mark.django_db


@pytest.fixture
def pull_secret_analysis_fixture(pull_authority_fixture, monkeypatch):  # noqa: F811
    f = pull_authority_fixture
    with context(f.binding.state.world):
        artifacts.parents.prepare(body=f.body, **f.review)
    parent = artifacts.NvkeInstallPlanPullAuthority.objects.get(guid=f.body["artifactId"])
    review = {
        **f.review,
        "pull_authority_id": str(parent.guid),
        "expected_pull_authority_version": parent.version,
        "pull_authority_digest": parent.summary["authorityDigest"],
    }
    body = {
        **{key: value for key, value in f.body.items() if key in artifacts.parents.protocol.BASE},
        "artifactId": str(uuid4()),
        "pullAuthorityId": str(parent.guid),
        "pullAuthorityDigest": parent.summary["authorityDigest"],
        "imageSelections": [
            {
                "podKey": "private-namespace/private-pod",
                "imagePointer": "/spec/containers/0/image",
                "ensureSecretPulledImages": True,
            }
        ],
        "maximumBytes": "16777216",
        "maximumHelperBytes": "1048576",
        "timeoutSeconds": 300,
    }
    calls, responses = [], {}

    def invoke(kind, job, **kwargs):
        value = kwargs["body"]
        artifact_id = value["artifactId"]
        assert NvkeInstallPlanPullSecretAnalysis.objects.filter(guid=artifact_id).exists()
        assert job == str(f.binding.original.guid)
        assert value["pullAuthorityId"] == str(parent.guid)
        calls.append((kind, copy.deepcopy(value)))
        if artifact_id not in responses:
            responses[artifact_id] = {
                "format": 1,
                "artifactId": artifact_id,
                "artifactDigest": hashlib.sha256(artifact_id.encode()).hexdigest(),
                "sourceDigest": "5" * 64,
                "captureDigest": "6" * 64,
                "authorityDigest": "8" * 64,
                "analyzedAt": parent.summary["observedAt"],
                "sourceObservedAt": parent.summary["observedAt"],
                "imageUseCount": 1,
                "candidateCount": 1,
                "missingReferenceCount": 0,
                "executionState": artifacts.protocol.STATE,
                "continuationAuthorized": False,
            }
        return copy.deepcopy(responses[artifact_id])

    for kind, prefix in [("prepare", "prepare_"), ("read", "")]:

        def call(_client, job, _kind=kind, **kwargs):
            return invoke(_kind, job, **kwargs)

        monkeypatch.setattr(OperatorAgentClient, prefix + "install_plan_pull_secret_analysis", call)
    return SimpleNamespace(
        binding=f.binding,
        row=f.row,
        parent=parent,
        parent_fixture=f,
        review=review,
        body=body,
        calls=calls,
        responses=responses,
        invoke=invoke,
    )


def graphql_source(f):
    return {
        **base_graphql_source(f),
        "pullAuthorityId": f.review["pull_authority_id"],
        "expectedPullAuthorityVersion": f.review["expected_pull_authority_version"],
        "pullAuthorityDigest": f.review["pull_authority_digest"],
    }


def test_encrypted_intent_explicit_recovery_and_original_preservation(pull_secret_analysis_fixture):
    f = pull_secret_analysis_fixture
    original = type(f.binding.original).all_objects.filter(pk=f.binding.original.pk).values().get()
    with context(f.binding.state.world):
        result = artifacts.prepare(body=f.body, **f.review)
        assert artifacts.read(artifact_id=result["id"], **f.review) == result
        assert artifacts.recover_preparation(artifact_id=result["id"], **f.review) == result
    row = NvkeInstallPlanPullSecretAnalysis.objects.get(guid=result["id"])
    assert artifacts.outbox.canonical(f.body) not in bytes(row.input_ciphertext)
    assert decrypt(
        EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))
    ) == artifacts.outbox.canonical(f.body)
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert not result["metadata"]["continuationAuthorized"]
    assert "private-namespace/private-pod" not in str(result) and "imageSelections" not in str(result)
    assert type(f.binding.original).all_objects.filter(pk=f.binding.original.pk).values().get() == original


def test_lost_reply_retains_intent_for_explicit_read(pull_secret_analysis_fixture, monkeypatch):
    f = pull_secret_analysis_fixture

    def lost(_client, job, **kwargs):
        f.invoke("prepare", job, **kwargs)
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost reply", ambiguous=True)

    with context(f.binding.state.world):
        with monkeypatch.context() as patch:
            patch.setattr(OperatorAgentClient, "prepare_install_plan_pull_secret_analysis", lost)
            with pytest.raises(NvkeError):
                artifacts.prepare(body=f.body, **f.review)
        row = NvkeInstallPlanPullSecretAnalysis.objects.get(guid=f.body["artifactId"])
        assert row.status == "preparing" and row.summary == {}
        assert artifacts.read(artifact_id=str(row.guid), **f.review)["status"] == "prepared"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_changed_intent_and_stale_binding_refuse_before_io(pull_secret_analysis_fixture):
    f = pull_secret_analysis_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            artifacts.prepare(body={**f.body, "maximumHelperBytes": "1048577"}, **f.review)
        assert error.value.code == "CONFLICT"
        with pytest.raises(NvkeError) as error:
            artifacts.prepare(
                body={**f.body, "artifactId": str(uuid4())},
                **{**f.review, "expected_binding_version": f.row.version + 1},
            )
        assert error.value.code == "SOURCE_CHANGED"
    assert len(f.calls) == 1


@pytest.mark.parametrize(
    "change", ["source", "parent", "parent_delete", "continuation", "private", "count", "role"]
)
def test_mid_io_source_change_or_invalid_result_never_publishes(
    pull_secret_analysis_fixture, monkeypatch, change
):
    f = pull_secret_analysis_fixture

    def changed(_client, job, **kwargs):
        result = f.invoke("prepare", job, **kwargs)
        if change == "source":
            NvkeInstallPlanImageBinding.objects.filter(pk=f.row.pk).update(version=f.row.version + 1)
        elif change == "parent":
            artifacts.NvkeInstallPlanPullAuthority.objects.filter(pk=f.parent.pk).update(
                version=f.parent.version + 1
            )
        elif change == "parent_delete":
            artifacts.NvkeInstallPlanPullAuthority.objects.filter(pk=f.parent.pk).update(
                deleted_at=timezone.now()
            )
        elif change == "role":
            RoleBinding.objects.filter(user=f.binding.state.world.user).update(deleted_at=timezone.now())
        elif change == "continuation":
            result["continuationAuthorized"] = True
        elif change == "private":
            result["pods"] = ["private-namespace/private-pod"]
        else:
            result["imageUseCount"] += 1
        return result

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_pull_secret_analysis", changed)
    with context(f.binding.state.world), pytest.raises((NvkeError, PermissionDenied)):
        artifacts.prepare(body=f.body, **f.review)
    row = NvkeInstallPlanPullSecretAnalysis.objects.get(guid=f.body["artifactId"])
    assert row.status == "preparing" and row.summary == {} and len(f.calls) == 1


def test_pull_identity_stays_reserved_against_other_preparation_after_deletion(pull_secret_analysis_fixture):
    f = pull_secret_analysis_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, **f.review)
        NvkeInstallPlanPullSecretAnalysis.objects.get(guid=f.body["artifactId"]).soft_delete()
        with pytest.raises(NvkeError) as error:
            artifacts.bindings.prepare(
                body={**f.binding.body, "artifactId": f.body["artifactId"]}, **f.binding.review
            )
    assert error.value.code == "CONFLICT" and len(f.calls) == 1


def test_corrupt_private_intent_refuses_before_agent_read(pull_secret_analysis_fixture):
    f = pull_secret_analysis_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, **f.review)
        NvkeInstallPlanPullSecretAnalysis.objects.filter(guid=f.body["artifactId"]).update(
            input_ciphertext=b"corrupt"
        )
        with pytest.raises(NvkeError) as error:
            artifacts.read(artifact_id=f.body["artifactId"], **f.review)
    assert error.value.code == "PREPARATION_UNAVAILABLE" and len(f.calls) == 1


def test_changed_prepared_result_is_never_replaced(pull_secret_analysis_fixture):
    f = pull_secret_analysis_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, **f.review)
        before = NvkeInstallPlanPullSecretAnalysis.objects.get(guid=f.body["artifactId"]).summary
        f.responses[f.body["artifactId"]]["missingReferenceCount"] += 1
        with pytest.raises(NvkeError) as error:
            artifacts.read(artifact_id=f.body["artifactId"], **f.review)
    assert error.value.code == "SOURCE_CHANGED"
    assert NvkeInstallPlanPullSecretAnalysis.objects.get(guid=f.body["artifactId"]).summary == before


def test_graphql_controls_are_redacted_audited_and_current_admin_only(pull_secret_analysis_fixture):
    f = pull_secret_analysis_fixture
    first = None
    for verb in ("prepare", "review", "recover"):
        action = (
            verb + "NvkeOperatorInstallPlanPullSecretAnalysis" + ("Preparation" if verb == "recover" else "")
        )
        input_type = (
            "PrepareNvkeOperatorInstallPlanPullSecretAnalysisInput"
            if verb == "prepare"
            else "NvkeOperatorInstallPlanPullSecretAnalysisReviewInput"
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
            + "(input:$v){ok errors{code} data{id version bindingId authorityDigest imageUseCount candidateCount continuationAuthorized}}}",
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
        operation="nvke.operator_install_plan_pull_secret_analysis_prepare"
    ).latest("timestamp")
    assert "private-namespace/private-pod" not in str(audit.variables) and "imageSelections" not in str(
        audit.variables
    )
    RoleBinding.objects.filter(user=f.binding.state.world.user).update(deleted_at=timezone.now())
    for verb in ("prepare", "review", "recover"):
        action = (
            verb + "NvkeOperatorInstallPlanPullSecretAnalysis" + ("Preparation" if verb == "recover" else "")
        )
        input_type = (
            "PrepareNvkeOperatorInstallPlanPullSecretAnalysisInput"
            if verb == "prepare"
            else "NvkeOperatorInstallPlanPullSecretAnalysisReviewInput"
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


def test_aliased_invalid_private_request_is_redacted_before_parsing(pull_secret_analysis_fixture):
    f = pull_secret_analysis_fixture
    response = execute(
        f.binding.state.world,
        "mutation($renamed:PrepareNvkeOperatorInstallPlanPullSecretAnalysisInput!){x:prepareNvkeOperatorInstallPlanPullSecretAnalysis(input:$renamed){ok errors{code}}}",
        {"renamed": {**graphql_source(f), "requestText": "INVALID PRIVATE_PULL_AUTHORITY_SCOPE"}},
    )
    assert response.errors is None and not response.data["x"]["ok"]
    audit = MutationAuditLog.objects.filter(
        operation="nvke.operator_install_plan_pull_secret_analysis_prepare"
    ).latest("timestamp")
    assert "PRIVATE_PULL_AUTHORITY_SCOPE" not in str(audit.variables)
    assert (
        not f.calls
        and not NvkeInstallPlanPullSecretAnalysis.objects.filter(guid=f.body["artifactId"]).exists()
    )


@pytest.mark.parametrize("change", ["boolean_version", "version", "digest", "deleted", "preparing"])
def test_exact_prepared_pull_parent_is_required_before_io(pull_secret_analysis_fixture, change):
    f = pull_secret_analysis_fixture
    review = dict(f.review)
    if change == "boolean_version":
        review["expected_pull_authority_version"] = True
    elif change == "version":
        review["expected_pull_authority_version"] += 1
    elif change == "digest":
        review["pull_authority_digest"] = "9" * 64
    elif change == "deleted":
        f.parent.soft_delete()
    else:
        artifacts.NvkeInstallPlanPullAuthority.objects.filter(pk=f.parent.pk).update(status="preparing")
    with context(f.binding.state.world), pytest.raises(NvkeError):
        artifacts.prepare(body=f.body, **review)
    assert not f.calls
    assert not NvkeInstallPlanPullSecretAnalysis.objects.filter(guid=f.body["artifactId"]).exists()


def test_parent_foreign_key_is_immutable_and_checked_before_historical_io(pull_secret_analysis_fixture):
    f = pull_secret_analysis_fixture
    with context(f.binding.state.world):
        artifacts.prepare(body=f.body, **f.review)
        other_id = str(uuid4())
        artifacts.parents.prepare(
            body={**f.parent_fixture.body, "artifactId": other_id}, **f.parent_fixture.review
        )
    other = artifacts.NvkeInstallPlanPullAuthority.objects.get(guid=other_id)
    row = NvkeInstallPlanPullSecretAnalysis.objects.get(guid=f.body["artifactId"])
    row.pull_authority = other
    with pytest.raises(ValueError):
        row.save()
    NvkeInstallPlanPullSecretAnalysis.objects.filter(pk=row.pk).update(pull_authority=other)
    with context(f.binding.state.world), pytest.raises(NvkeError) as error:
        artifacts.read(artifact_id=str(row.guid), **f.review)
    assert error.value.code == "PREPARATION_UNAVAILABLE"
    assert len(f.calls) == 1
