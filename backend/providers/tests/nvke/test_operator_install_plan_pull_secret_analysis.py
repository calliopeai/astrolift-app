from __future__ import annotations

import copy
from uuid import uuid4

import httpx
import pytest

from nvke import _operator_install_plan_pull_secret_analysis as protocol
from nvke.client import NvkeError
from tests.nvke import test_operator_install_plan_pull_authority as parent
from tests.nvke.test_operator_install_plan import DATE, INTENT, JOB
from tests.nvke.test_operator_install_plan_immutable import SOURCE

ARTIFACT = str(uuid4())


def body():
    return {
        **{key: value for key, value in parent.body().items() if key in parent.protocol.BASE},
        "artifactId": ARTIFACT,
        "pullAuthorityId": parent.ARTIFACT,
        "pullAuthorityDigest": parent.summary()["authorityDigest"],
        "imageSelections": [
            {"podKey": "owned/pod", "imagePointer": "/spec/containers/0/image", "ensureSecretPulledImages": True}
        ],
        "maximumBytes": "16777216",
        "maximumHelperBytes": "1048576",
        "timeoutSeconds": 300,
    }


def summary():
    return {
        "format": 1,
        "artifactId": ARTIFACT,
        **{key: "a" * 64 for key in ("artifactDigest", "sourceDigest", "captureDigest", "authorityDigest")},
        "analyzedAt": DATE,
        "sourceObservedAt": DATE,
        "imageUseCount": 1,
        "candidateCount": 1,
        "missingReferenceCount": 0,
        "executionState": protocol.STATE,
        "continuationAuthorized": False,
    }


def peer(*, response=None, lost=False, capability=None):
    sdk, calls = parent.peer()
    original = sdk._http._transport
    response = summary() if response is None else response

    def respond(request):
        if request.url.path.endswith("/install-plan-pull-secret-analysis"):
            calls.append((request.method, request.url.path, dict(request.url.params)))
            if lost and request.method == "POST":
                raise httpx.ReadTimeout("PRIVATE", request=request)
            return httpx.Response(200, json=copy.deepcopy(response))
        result = original.handle_request(request)
        if request.url.path == "/v1/identity":
            data = result.json()
            data["installPlanPullSecretAnalysis"] = (
                capability
                if capability is not None
                else {"format": 1, "prepare": True, "readOnly": True, "continuation": False}
            )
            return httpx.Response(200, json=data)
        return result

    sdk._http._transport = httpx.MockTransport(respond)
    return sdk, calls


def call(sdk, *, read_only=False, value=None):
    value = body() if value is None else value
    if read_only:
        value = {key: item for key, item in value.items() if key in protocol.BASE}
    method = sdk.install_plan_pull_secret_analysis if read_only else sdk.prepare_install_plan_pull_secret_analysis
    return method(JOB, intent=INTENT, body=value, **SOURCE)


@pytest.mark.parametrize(
    "change",
    [
        {"maximumBytes": 2**53 + 1},
        {"maximumBytes": "67108865"},
        {"maximumBytes": "01"},
        {"maximumHelperBytes": "16777217"},
        {"maximumHelperBytes": True},
        {"timeoutSeconds": True},
        {"imageSelections": []},
        {
            "imageSelections": [
                {"podKey": "owned/pod", "imagePointer": "/status/image", "ensureSecretPulledImages": True}
            ]
        },
        {
            "imageSelections": [
                {"podKey": "../pod", "imagePointer": "/spec/containers/0/image", "ensureSecretPulledImages": True}
            ]
        },
        {
            "imageSelections": [
                {"podKey": "owned/pod", "imagePointer": "/spec/containers/0/image", "ensureSecretPulledImages": 1}
            ]
        },
        {"pullAuthorityId": ARTIFACT},
        {"pullAuthorityId": SOURCE["workload_id"]},
        {"pullAuthorityDigest": "bad"},
        {"helperProgram": "/PRIVATE"},
        {"credential": "PRIVATE"},
        {"continuationAuthorized": True},
    ],
)
def test_invalid_requests_refuse_before_transport(change):
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), **change})
    assert error.value.code == "INVALID_INPUT" and not calls


@pytest.mark.parametrize(
    "change",
    [
        {"imageUseCount": 2},
        {"imageUseCount": True},
        {"candidateCount": -1},
        {"candidateCount": 100001},
        {"missingReferenceCount": 10001},
        {"continuationAuthorized": True},
        {"executionState": "ready"},
        {"sourceObservedAt": "2020-01-01T00:00:00+00:00"},
        {"analyzedAt": "2020-01-01T00:00:00+00:00"},
        {"credentials": "PRIVATE"},
        {"imageSelections": []},
        {"authorityDigest": "invalid"},
    ],
)
def test_invalid_or_private_response_refuses(change):
    sdk, _ = peer(response={**summary(), **change})
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"


def test_prepare_and_review_bind_both_original_ancestry_and_parent_pull_capture():
    sdk, calls = peer()
    with sdk:
        assert call(sdk) == summary()
        assert call(sdk, read_only=True) == summary()
    assert sum(method == "POST" for method, _, _ in calls) == 1
    query = next(
        query
        for method, path, query in calls
        if method == "GET" and path.endswith("/install-plan-pull-secret-analysis")
    )
    assert set(query) == protocol.BASE - {"format"}


def test_ambiguous_reply_is_not_automatically_retried():
    sdk, calls = peer(lost=True)
    with sdk:
        with pytest.raises(NvkeError) as error:
            call(sdk)
        assert error.value.ambiguous and not error.value.retryable
        assert "PRIVATE" not in str(error.value)
        assert call(sdk, read_only=True) == summary()
    assert sum(method == "POST" for method, _, _ in calls) == 1


def test_cold_review_remains_available_without_preparation_profile():
    sdk, calls = peer(capability={"format": 1, "prepare": False, "readOnly": True, "continuation": False})
    with sdk:
        assert call(sdk, read_only=True) == summary()
        with pytest.raises(NvkeError) as error:
            call(sdk)
    assert error.value.code == "OPERATOR_CONTROL_UNSUPPORTED"
    assert all(method == "GET" for method, _, _ in calls)


def test_changed_parent_digest_refuses_before_interpretation():
    sdk, calls = peer()
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk, value={**body(), "pullAuthorityDigest": "e" * 64})
    assert error.value.code == "SOURCE_CHANGED"
    assert not any(path.endswith("/install-plan-pull-secret-analysis") for _, path, _ in calls)


def test_changed_parent_after_interpretation_refuses_returned_summary():
    sdk, calls = peer()
    original = sdk._http._transport
    interpreted = False

    def respond(request):
        nonlocal interpreted
        result = original.handle_request(request)
        if request.url.path.endswith("/install-plan-pull-secret-analysis"):
            interpreted = True
        elif interpreted and request.url.path.endswith("/install-plan-pull-authority"):
            data = result.json()
            data["authorityDigest"] = "e" * 64
            return httpx.Response(200, json=data)
        return result

    sdk._http._transport = httpx.MockTransport(respond)
    with sdk, pytest.raises(NvkeError) as error:
        call(sdk)
    assert error.value.code == "SOURCE_CHANGED"
    assert sum(method == "POST" for method, _, _ in calls) == 1
