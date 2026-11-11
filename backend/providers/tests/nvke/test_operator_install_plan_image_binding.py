from __future__ import annotations

import copy
from uuid import uuid4

import httpx
import pytest

from nvke import _operator_install_plan_image_binding as protocol
from nvke.client import NvkeError
from tests.nvke.test_operator_install_plan import DATE, DIGEST, INTENT, JOB, PLAN
from tests.nvke.test_operator_install_plan_charts import ARTIFACT
from tests.nvke.test_operator_install_plan_tree import TREE
from tests.nvke.test_operator_install_plan_workload import WORKLOAD, peer
from tests.nvke.test_operator_install_plan_workload import summary as workload_summary

BINDING = str(uuid4())
IMAGE = str(uuid4())


def body():
    return {
        "format": 1,
        "artifactId": BINDING,
        "planId": PLAN,
        "planDigest": DIGEST,
        "treeId": TREE,
        "treeDigest": "d" * 64,
        "workloadId": WORKLOAD,
        "workloadDigest": workload_summary()["workloadDigest"],
        "imageBindings": {
            "example.test/image:v1": {
                "sourceKind": "registry",
                "artifactId": IMAGE,
                "artifactDigest": "a" * 64,
            }
        },
        "maximumImageBytes": "8388608",
        "maximumBytes": "1048576",
        "timeoutSeconds": 300,
    }


def summary():
    return {
        "format": 1,
        "artifactId": BINDING,
        "artifactDigest": "1" * 64,
        "sourceDigest": "2" * 64,
        "captureDigest": "3" * 64,
        "bindingDigest": "4" * 64,
        "observedAt": DATE,
        "imageCount": 1,
        "imageUseCount": 3,
        "importCount": 0,
        "rewriteCount": 1,
        "verifiedImageBytes": "1234",
        "workloadIssueCount": 0,
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def call(sdk, *, read_only=False, value=None):
    value = body() if value is None else value
    if read_only:
        value = {key: item for key, item in value.items() if key in protocol.BASE}
    method = sdk.install_plan_image_binding if read_only else sdk.prepare_install_plan_image_binding
    return method(JOB, intent=INTENT, body=value, chart_artifact_id=ARTIFACT, chart_artifact_digest="c" * 64)


def test_prepare_recovery_and_lost_reply_preserve_explicit_admission():
    sdk, calls = peer(image_binding=summary())
    with sdk:
        assert call(sdk) == summary()
        assert call(sdk, read_only=True) == summary()
    assert [(method, path) for method, path, _ in calls if method != "GET"] == [
        ("POST", f"/v1/jobs/{JOB}/install-plan-image-binding"),
    ]
    query = next(q for method, path, q in calls if method == "GET" and path.endswith("/install-plan-image-binding"))
    assert set(query) == protocol.BASE - {"format"}
    sdk, calls = peer(image_binding=summary(), lost_image_binding_reply=True)
    with sdk:
        with pytest.raises(NvkeError) as error:
            call(sdk)
        assert error.value.ambiguous and not error.value.retryable
        assert "PRIVATE" not in str(error.value)
        assert call(sdk, read_only=True) == summary()
    assert sum(method == "POST" for method, _, _ in calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"format": True},
        {"helperProgram": "/private"},
        {"maximumBytes": "67108865"},
        {"maximumImageBytes": "9223372036854775808"},
        {"maximumImageBytes": "01"},
        {"timeoutSeconds": True},
        {"workloadId": PLAN},
        {"imageBindings": []},
        {"imageBindings": {"https://example.test/image:v1": {}}},
        {
            "imageBindings": {
                "example.test/image:v1": {"sourceKind": "registry", "artifactId": PLAN, "artifactDigest": "a" * 64}
            }
        },
    ],
)
def test_invalid_private_selections_refuse_before_transport(change):
    sdk, calls = peer(image_binding=summary())
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), **change})
    assert error.value.code == "INVALID_INPUT" and not calls


@pytest.mark.parametrize(
    "change",
    [
        {"credentials": "PRIVATE"},
        {"continuationAuthorized": True},
        {"imageCount": 2},
        {"imageUseCount": 2},
        {"workloadIssueCount": 1},
        {"rewriteCount": 2},
        {"importCount": 10001},
        {"verifiedImageBytes": "8388609"},
        {"verifiedImageBytes": "0"},
        {"bindingDigest": "bad"},
        {"imageUseCount": True},
        {"observedAt": "invalid"},
        {"observedAt": "2020-01-01T00:00:00+00:00"},
    ],
)
def test_invalid_observation_is_never_published(change):
    sdk, _ = peer(image_binding={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert "PRIVATE" not in str(error.value)


def test_missing_capability_and_wrong_workload_refuse_before_post():
    for options, code in (
        ({"image_binding_capability": {}}, "OPERATOR_CONTROL_UNSUPPORTED"),
        ({"workload": {**workload_summary(), "workloadDigest": "f" * 64}}, "SOURCE_CHANGED"),
    ):
        sdk, calls = peer(image_binding=summary(), **options)
        with sdk, pytest.raises(NvkeError) as error:
            call(sdk)
        assert error.value.code == code
        assert all(method == "GET" for method, _, _ in calls)


def test_workload_replacement_after_response_is_rejected():
    sdk, _ = peer(image_binding=summary())
    transport = sdk._http._transport
    mutated = False

    def respond(request):
        nonlocal mutated
        response = transport.handle_request(request)
        data = copy.deepcopy(response.json())
        if request.url.path.endswith("/install-plan-image-binding"):
            mutated = True
        elif mutated and request.url.path.endswith("/install-plan-workload-inventory"):
            data["workloadDigest"] = "f" * 64
        return httpx.Response(response.status_code, json=data)

    sdk._http._transport = httpx.MockTransport(respond)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "SOURCE_CHANGED"


def test_workload_parent_reuses_the_validated_tree_source_at_each_boundary():
    sdk, calls = peer(image_binding=summary())
    with sdk:
        assert call(sdk) == summary()
    for suffix in ("workload-inventory", "tree"):
        assert sum(path.endswith("/install-plan-" + suffix) for _, path, _ in calls) == 2


def test_workload_capability_replacement_during_parent_read_refuses_before_binding():
    sdk, calls = peer(image_binding=summary())
    original = sdk._http._transport
    parent_read = False

    def respond(request):
        nonlocal parent_read
        if request.url.path.endswith("/install-plan-workload-inventory"):
            parent_read = True
        response = original.handle_request(request)
        if request.url.path == "/v1/identity" and parent_read:
            data = response.json()
            data["installPlanWorkloadInventory"] = {}
            return httpx.Response(200, json=data)
        return response

    sdk._http._transport = httpx.MockTransport(respond)
    with sdk, pytest.raises(NvkeError):
        call(sdk)
    assert not any(path.endswith("/install-plan-image-binding") for _, path, _ in calls)
