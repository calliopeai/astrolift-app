from __future__ import annotations

import copy

import httpx
import pytest

from nvke import _operator_install_plan_pull_runtime as protocol
from nvke.client import NvkeError
from tests.nvke import test_operator_install_plan_node_platforms as nodes
from tests.nvke import test_operator_install_plan_pull_secret_analysis as analyses
from tests.nvke.test_operator_install_plan import DATE, INTENT, JOB
from tests.nvke.test_operator_install_plan_immutable import SOURCE


def body():
    return {
        **{key: value for key, value in analyses.body().items() if key in analyses.protocol.BASE},
        "analysisDigest": analyses.summary()["authorityDigest"],
        "nodePlatformId": nodes.ARTIFACT,
        "nodePlatformDigest": nodes.summary()["platformDigest"],
    }


def summary():
    value = body()
    return {
        "format": 1,
        "analysisArtifactId": value["artifactId"],
        "analysisArtifactDigest": value["analysisDigest"],
        "nodePlatformArtifactId": value["nodePlatformId"],
        "nodePlatformArtifactDigest": value["nodePlatformDigest"],
        "correspondenceDigest": "b" * 64,
        "analysisCaptureDigest": "a" * 64,
        "pullCaptureDigest": "a" * 64,
        "nodePlatformCaptureDigest": "a" * 64,
        "imageUseCount": 1,
        "nodeCount": 1,
        "sourceObservedAt": DATE,
        "nodeObservedAt": DATE,
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def peer(*, response=None, capability=None, changed_parent=False, changed_capability=False, lost=False):
    sdk, calls = analyses.peer()
    original = sdk._http._transport
    reads, completed = 0, False

    def respond(request):
        nonlocal reads, completed
        if request.url.path.endswith("/install-plan-pull-runtime-correspondence"):
            calls.append((request.method, request.url.path, dict(request.url.params)))
            completed = True
            if lost:
                raise httpx.ReadTimeout("PRIVATE_RESPONSE", request=request)
            return httpx.Response(200, json=copy.deepcopy(summary() if response is None else response))
        if request.url.path.endswith("/install-plan-node-platforms"):
            calls.append((request.method, request.url.path, dict(request.url.params)))
            reads += 1
            value = nodes.summary()
            if changed_parent and reads > 1:
                value["captureDigest"] = "c" * 64
            return httpx.Response(200, json=value)
        result = original.handle_request(request)
        if request.url.path == "/v1/identity":
            data = result.json()
            data["installPlanNodePlatforms"] = {"format": 1, "prepare": True, "readOnly": True, "continuation": False}
            data["installPlanPullRuntimeCorrespondence"] = (
                capability
                if capability is not None
                else {"format": 1, "readOnly": not (changed_capability and completed), "continuation": False}
            )
            return httpx.Response(200, json=data)
        return result

    sdk._http._transport = httpx.MockTransport(respond)
    return sdk, calls


def call(sdk, value=None):
    return sdk.install_plan_pull_runtime_correspondence(
        JOB, intent=INTENT, body=body() if value is None else value, **SOURCE
    )


def test_read_only_result_binds_each_parent_before_and_after_without_recursive_child_reads():
    sdk, calls = peer()
    with sdk:
        assert call(sdk) == summary()
    assert all(method == "GET" for method, _, _ in calls)
    for suffix in ("pull-authority", "pull-secret-analysis", "node-platforms"):
        assert sum(path.endswith("/install-plan-" + suffix) for _, path, _ in calls) == 2
    selected = [query for _, path, query in calls if path.endswith("/install-plan-pull-runtime-correspondence")]
    assert len(selected) == 1
    assert set(selected[0]) == protocol.BASE - {"format"}


@pytest.mark.parametrize(
    "change",
    [
        {"program": "/private"},
        {"credential": "PRIVATE"},
        {"imageSelections": []},
        {"format": True},
        {"analysisDigest": True},
        {"nodePlatformDigest": "f" * 64 + "\n"},
        *[
            {"nodePlatformId": value}
            for value in (
                JOB,
                SOURCE["chart_artifact_id"],
                SOURCE["workload_id"],
                analyses.ARTIFACT,
                analyses.parent.ARTIFACT,
            )
        ],
    ],
)
def test_invalid_requests_refuse_before_http(change):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, {**body(), **change})
    assert error.value.code == "INVALID_INPUT"
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"credentials": "PRIVATE"},
        {"format": True},
        {"continuationAuthorized": True},
        {"nodeCount": True},
        {"nodeCount": 2},
        {"imageUseCount": 2},
        {"analysisCaptureDigest": "c" * 64},
        {"pullCaptureDigest": "c" * 64},
        {"nodePlatformCaptureDigest": "c" * 64},
        {"nodePlatformArtifactId": analyses.ARTIFACT},
        {"analysisArtifactDigest": "c" * 64},
        {"nodeObservedAt": "2000-01-01T00:00:00+00:00"},
        {"nodeObservedAt": "2100-01-01T00:00:00+00:00"},
        {"sourceObservedAt": "2000-01-01T00:00:00+00:00"},
        {"executionState": "ready"},
    ],
)
def test_private_or_mismatched_summaries_refuse(change):
    sdk, _ = peer(response={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "capability",
    [
        None,
        {},
        {"format": True, "readOnly": True, "continuation": False},
        {"format": 1, "readOnly": False, "continuation": False},
        {"format": 1, "readOnly": True, "continuation": True},
    ],
)
def test_missing_or_unsafe_capability_refuses(capability):
    sdk, calls = peer(capability={} if capability is None else capability)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert not any(path.endswith("/install-plan-pull-runtime-correspondence") for _, path, _ in calls)


@pytest.mark.parametrize("options", [{"changed_parent": True}, {"changed_capability": True}])
def test_parent_and_capability_changes_after_read_invalidate_result(options):
    sdk, _ = peer(**options)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"


def test_lost_read_is_not_automatically_repeated_or_exposed():
    sdk, calls = peer(lost=True)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert "PRIVATE_RESPONSE" not in str(error.value)
    assert sum(path.endswith("/install-plan-pull-runtime-correspondence") for _, path, _ in calls) == 1
