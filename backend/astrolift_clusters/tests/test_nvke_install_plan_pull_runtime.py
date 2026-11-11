"""Real PostgreSQL/GraphQL fences with controlled SDK replies; no native dispatch."""

import copy
from types import SimpleNamespace

import pytest
from django.db import connection
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_pull_runtime as artifacts
from astrolift_clusters.models import NvkeInstallPlanNodePlatforms, NvkeInstallPlanPullSecretAnalysis
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
from astrolift_clusters.tests.test_nvke_install_plan_node_platforms import platform_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_pull_authority import (
    pull_authority_fixture,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_install_plan_pull_secret_analysis import (
    graphql_source,
    pull_secret_analysis_fixture,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_identity.models import RoleBinding
from core.schema.audit import MutationAuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def runtime_fixture(pull_secret_analysis_fixture, platform_fixture, monkeypatch):  # noqa: F811
    f, n = pull_secret_analysis_fixture, platform_fixture
    with context(f.binding.state.world):
        analysis = artifacts.analyses.prepare(body=f.body, **f.review)
        node = artifacts.nodes.prepare(body=n.body, **n.review)
    values = {
        **f.review,
        "analysis_id": analysis["id"],
        "expected_analysis_version": analysis["version"],
        "analysis_digest": analysis["metadata"]["authorityDigest"],
        "node_platform_id": node["id"],
        "expected_node_platform_version": node["version"],
        "node_platform_digest": node["metadata"]["platformDigest"],
    }
    calls = []
    response = {
        "format": 1,
        "analysisArtifactId": analysis["id"],
        "analysisArtifactDigest": values["analysis_digest"],
        "nodePlatformArtifactId": node["id"],
        "nodePlatformArtifactDigest": values["node_platform_digest"],
        "correspondenceDigest": "a" * 64,
        "analysisCaptureDigest": analysis["metadata"]["captureDigest"],
        "pullCaptureDigest": f.parent.summary["captureDigest"],
        "nodePlatformCaptureDigest": node["metadata"]["captureDigest"],
        "imageUseCount": analysis["metadata"]["imageUseCount"],
        "nodeCount": 1,
        "sourceObservedAt": analysis["metadata"]["sourceObservedAt"],
        "nodeObservedAt": node["metadata"]["observedAt"],
        "executionState": artifacts.protocol.STATE,
        "continuationAuthorized": False,
    }

    def invoke(_client, job, **kwargs):
        # pytest's outer transaction is retained; the service adds none around RPC.
        assert len(connection.savepoint_ids) == transaction_depth
        assert job == str(f.binding.original.guid)
        assert set(kwargs["body"]) == artifacts.protocol.BASE
        assert kwargs["body"]["analysisDigest"] == values["analysis_digest"]
        assert kwargs["body"]["nodePlatformDigest"] == values["node_platform_digest"]
        calls.append(copy.deepcopy(kwargs))
        return copy.deepcopy(response)

    transaction_depth = len(connection.savepoint_ids)
    monkeypatch.setattr(OperatorAgentClient, "install_plan_pull_runtime_correspondence", invoke)
    return SimpleNamespace(f=f, values=values, calls=calls, response=response, invoke=invoke)


def test_read_only_public_review_preserves_all_rows(runtime_fixture):
    f = runtime_fixture
    models = [NvkeInstallPlanPullSecretAnalysis, NvkeInstallPlanNodePlatforms, type(f.f.binding.original)]
    before = [list(model.all_objects.values()) for model in models]
    with context(f.f.binding.state.world):
        result = artifacts.read(**f.values)
        assert artifacts.read(**f.values) == result
    assert result["metadata"] == f.response
    assert result["analysis"]["id"] == f.values["analysis_id"]
    assert result["node_platforms"]["id"] == f.values["node_platform_id"]
    assert "private-" not in str(result)
    assert [list(model.all_objects.values()) for model in models] == before
    assert len(f.calls) == 2


@pytest.mark.parametrize("target", ["analysis", "node_platform"])
@pytest.mark.parametrize("change", ["version", "digest", "deleted", "preparing", "ciphertext", "summary"])
def test_changed_parent_refuses_before_io(runtime_fixture, target, change):
    f = runtime_fixture
    model = NvkeInstallPlanPullSecretAnalysis if target == "analysis" else NvkeInstallPlanNodePlatforms
    row = model.all_objects.get(guid=f.values[target + "_id"])
    updates = {
        "version": {"version": row.version + 1},
        "digest": {
            "summary": {
                **row.summary,
                ("authorityDigest" if target == "analysis" else "platformDigest"): "0" * 64,
            }
        },
        "deleted": {"deleted_at": timezone.now()},
        "preparing": {"status": "preparing"},
        "ciphertext": {"input_ciphertext": b"invalid"},
        "summary": {"summary": {**row.summary, "privateExtra": "refuse"}},
    }[change]
    model.all_objects.filter(pk=row.pk).update(**updates)
    with context(f.f.binding.state.world), pytest.raises(NvkeError):
        artifacts.read(**f.values)
    assert f.calls == []


@pytest.mark.parametrize("target", ["analysis", "node_platform"])
@pytest.mark.parametrize("change", ["version", "deleted", "summary", "request_digest"])
def test_parent_change_during_rpc_refuses_publication(runtime_fixture, monkeypatch, target, change):
    f = runtime_fixture
    model = NvkeInstallPlanPullSecretAnalysis if target == "analysis" else NvkeInstallPlanNodePlatforms
    row = model.all_objects.get(guid=f.values[target + "_id"])

    def changed(client, job, **kwargs):
        response = f.invoke(client, job, **kwargs)
        updates = {
            "version": {"version": row.version + 1},
            "deleted": {"deleted_at": timezone.now()},
            "summary": {"summary": {**row.summary, "captureDigest": "0" * 64}},
            "request_digest": {"request_digest": "0" * 64},
        }[change]
        model.all_objects.filter(pk=row.pk).update(**updates)
        return response

    monkeypatch.setattr(OperatorAgentClient, "install_plan_pull_runtime_correspondence", changed)
    with context(f.f.binding.state.world), pytest.raises(NvkeError):
        artifacts.read(**f.values)
    assert len(f.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("continuationAuthorized", True),
        ("nodeCount", 2),
        ("imageUseCount", True),
        ("analysisArtifactDigest", "0" * 64),
        ("nodePlatformCaptureDigest", "0" * 64),
        ("nodeObservedAt", "1900-01-01T00:00:00+00:00"),
        ("privateExtra", "refuse"),
    ],
)
def test_invalid_or_private_agent_summary_refuses(runtime_fixture, field, value):
    f = runtime_fixture
    f.response[field] = value
    with context(f.f.binding.state.world), pytest.raises(NvkeError) as error:
        artifacts.read(**f.values)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert len(f.calls) == 1


def test_lost_read_is_never_retried(runtime_fixture, monkeypatch):
    f = runtime_fixture

    def lost(client, job, **kwargs):
        f.invoke(client, job, **kwargs)
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost read", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "install_plan_pull_runtime_correspondence", lost)
    with context(f.f.binding.state.world), pytest.raises(NvkeError):
        artifacts.read(**f.values)
    assert len(f.calls) == 1


def test_graphql_current_admin_and_audit(runtime_fixture):
    f = runtime_fixture
    values = {
        **graphql_source(f.f),
        **{
            "".join([parts[0], *(part.title() for part in parts[1:])]): value
            for key, value in f.values.items()
            if key not in f.f.review
            for parts in [key.split("_")]
        },
    }
    document = (
        "mutation($v:NvkeOperatorInstallPlanPullRuntimeReviewInput!){"
        "reviewNvkeOperatorInstallPlanPullRuntime(input:$v){ok errors{code} data{"
        "correspondenceDigest nodeCount continuationAuthorized analysis{id version bindingId} nodePlatforms{id version bindingId}}}}"
    )
    result = execute(f.f.binding.state.world, document, {"v": values})
    assert result.errors is None
    body = result.data["reviewNvkeOperatorInstallPlanPullRuntime"]
    assert body["ok"], body
    assert body["data"]["analysis"]["bindingId"] == body["data"]["nodePlatforms"]["bindingId"]
    assert body["data"]["continuationAuthorized"] is False
    assert MutationAuditLog.objects.filter(
        operation="nvke.operator_install_plan_pull_runtime_review", success=True
    ).exists()
    RoleBinding.objects.filter(user=f.f.binding.state.world.user).update(deleted_at=timezone.now())
    result = execute(f.f.binding.state.world, document, {"v": values})
    assert result.errors is None and not result.data["reviewNvkeOperatorInstallPlanPullRuntime"]["ok"]
    assert len(f.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_analysis_version", True),
        ("expected_node_platform_version", 0),
        ("analysis_digest", "A" * 64),
        ("node_platform_digest", "bad"),
    ],
)
def test_invalid_expected_parent_before_io(runtime_fixture, field, value):
    f = runtime_fixture
    with context(f.f.binding.state.world), pytest.raises(NvkeError) as error:
        artifacts.read(**{**f.values, field: value})
    assert error.value.code == "INVALID_INPUT" and not f.calls


@pytest.mark.parametrize("target", ["analysis", "node_platform"])
def test_other_source_parent_refuses(runtime_fixture, target):
    f = runtime_fixture
    model = NvkeInstallPlanPullSecretAnalysis if target == "analysis" else NvkeInstallPlanNodePlatforms
    row = model.all_objects.get(guid=f.values[target + "_id"])
    model.all_objects.filter(pk=row.pk).update(
        source_binding={**row.source_binding, "workload_digest": "0" * 64}
    )
    with context(f.f.binding.state.world), pytest.raises(NvkeError) as error:
        artifacts.read(**f.values)
    assert error.value.code == "SOURCE_CHANGED" and not f.calls


def test_permission_revoked_during_rpc_refuses_publication(runtime_fixture, monkeypatch):
    f = runtime_fixture

    def revoked(client, job, **kwargs):
        response = f.invoke(client, job, **kwargs)
        RoleBinding.objects.filter(user=f.f.binding.state.world.user).update(deleted_at=timezone.now())
        return response

    monkeypatch.setattr(OperatorAgentClient, "install_plan_pull_runtime_correspondence", revoked)
    from core.permissions import PermissionDenied

    with context(f.f.binding.state.world), pytest.raises((NvkeError, PermissionDenied)):
        artifacts.read(**f.values)
    assert len(f.calls) == 1


@pytest.mark.parametrize("target", ["analysis", "node_platform"])
def test_public_artifact_tag_cannot_replace_private_index_digest(runtime_fixture, target):
    f = runtime_fixture
    model = NvkeInstallPlanPullSecretAnalysis if target == "analysis" else NvkeInstallPlanNodePlatforms
    row = model.all_objects.get(guid=f.values[target + "_id"])
    assert row.summary["artifactDigest"] != f.values[target + "_digest"]
    with context(f.f.binding.state.world), pytest.raises(NvkeError) as error:
        artifacts.read(**{**f.values, target + "_digest": row.summary["artifactDigest"]})
    assert error.value.code == "SOURCE_CHANGED" and not f.calls
